# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ContextManager：上下文管理器门面类。

整合 SessionRuntime + StateManager + MessageStore，
为 ActorAgent 提供统一的上下文管理接口。

核心职责：
- 迭代生命周期：begin_iteration → commit_iteration / rollback_iteration
- 消息+状态协调：commit 时自动计算 delta、定期存储 snapshot、管理 state 事务
- 上下文构建：build_execution_context 供 Ability/Hook 使用
- 子 Agent 继承：fork_for_sub_agent 创建独立但继承的上下文
- Session 管理：create_session / activate_session / list_sessions
- Branch 管理：create_branch / activate_branch / list_branches
- 持久化：persist/restore 异步保存和恢复完整链式状态
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from ghrah.context.action_session import ActionSession
from ghrah.context.branch import ActionBranch
from ghrah.context.chain import ActionChain
from ghrah.context.iteration_commit import (
    IterationCommitter,
    discard_iteration_effects,
    get_iteration_committer,
)
from ghrah.context.message_store import MessageStore
from ghrah.context.node import ContextNode
from ghrah.context.persistence import ContextChanges, PersistenceBackend
from ghrah.context.session_runtime import SessionRuntime
from ghrah.context.state import StateManager
from ghrah.context.token_estimator import DEFAULT_TOKEN_ESTIMATOR, TokenEstimator
from ghrah.context.window import WindowManager
from ghrah.core.window_protocol import MessageFactory
from ghrah.scheme.loading.skill_context import digest_skill_catalog, render_skill_catalog

if TYPE_CHECKING:
    from ghrah.core.message import AgentMessage
    from ghrah.scheme.execution.context import AbilityExecutionContext
    from ghrah.types.config_types import AgentConfig

logger = logging.getLogger(__name__)

__all__ = ["ContextManager"]


class DeliverySessionUnavailableError(ValueError):
    """目标执行会话缺失或已关闭，禁止改投当前会话。"""


class ContextManager:
    """上下文管理器门面类 — 管理多个单 Root Session。

    为 ActorAgent 的驱动循环提供统一的上下文管理，支持：
    - 原子性迭代（事务性状态变更 + 消息管理）
    - 链式历史（类似 Git 的不可变节点链）
    - Session 管理（独立 Root + 元数据）
    - Branch 管理（稳定 Head 引用）
    - 回滚即分支（rollback 自动创建新 Branch）
    - 子 Agent 继承（fork_for_sub_agent 创建独立上下文）
    - 异步持久化（persist/restore）

    迭代生命周期：
        begin_iteration() → add_messages() / apply_state_changes()
        → commit_iteration() 或 rollback_iteration()

    Args:
        agent_name: 所属 Agent 名称
        initial_state: 初始 Agent 状态
        snapshot_interval: 消息快照间隔，默认 5
        system_prompt: 系统提示词
        window_manager: 窗口管理器（可选）
        persistence: 持久化后端（可选，None 则不持久化）
        auto_persist: 是否在每次 commit/rollback 后自动持久化节点，默认 False
        initial_messages: Root 初始消息（system_prompt 会显式插入最前）
        origin_session_id: Root 来源 Session ID，必须与 origin_node_id 同时提供
        origin_node_id: Root 来源节点 ID，只记录 provenance，不建立跨 Session 父边
        session_metadata: 初始 Session 元数据
        root_metadata: 初始 Root 节点元数据
        compact_threshold: 链上 compact 触发阈值（0~1 相对预算比率）；None 禁用
        compact_keep_recent: compact 保留窗节点数
        compact_method: compact 综合方法名（当前唯一合法值 "ghrah.builtin"）
        estimator: token 估算器（WindowManager 侧无实例时的 CM 级口径）；
            None 时用模块级默认实例 DEFAULT_TOKEN_ESTIMATOR
        skill_provider: SkillAbilityProvider（duck-typed，装配期显式注入）；
            None = 不接线——不向上下文注入技能目录摘要（零隐式）
    """

    def __init__(
        self,
        agent_name: str,
        initial_state: dict[str, Any] | None = None,
        snapshot_interval: int = 5,
        system_prompt: str = "",
        window_manager: WindowManager | None = None,
        persistence: PersistenceBackend | None = None,
        auto_persist: bool = False,
        message_factory: MessageFactory | None = None,
        initial_messages: list[Any] | None = None,
        origin_session_id: str | None = None,
        origin_node_id: str | None = None,
        origin_agent_name: str | None = None,
        session_metadata: dict[str, Any] | None = None,
        root_metadata: dict[str, Any] | None = None,
        compact_threshold: float | None = None,
        compact_keep_recent: int = 2,
        compact_method: str = "ghrah.builtin",
        estimator: TokenEstimator | None = None,
        skill_provider: Any | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._state_manager = StateManager(initial_state)
        self._message_store = MessageStore(snapshot_interval=snapshot_interval)
        self._system_prompt = system_prompt
        self._window_manager = window_manager
        self._persistence = persistence
        self._auto_persist = auto_persist
        self._message_factory = message_factory
        self._estimator: TokenEstimator = estimator or DEFAULT_TOKEN_ESTIMATOR
        # 技能目录摘要：provider 装配期显式注入；digest 仅用于文案口径与变化日志
        self._skill_provider = skill_provider
        self._skill_catalog_digest: str | None = None
        self._pending_messages: list[Any] = []
        self._in_iteration: bool = False
        self._pending_compaction: list[dict[str, Any]] = []
        self._persist_tasks: set[asyncio.Task[Any]] = set()
        self._persist_lock = asyncio.Lock()

        # 链上 compact 配置（threshold=None 即禁用，行为与现状管道等价）
        self._compact_threshold = compact_threshold
        self._compact_keep_recent = compact_keep_recent
        self._compact_method = compact_method
        # 最近一次主循环真实 input_tokens 锚点；None 表示尚无可靠锚点
        self._window_occupied: int | None = None
        self._last_real_input_tokens: int | None = None
        # 最近一次调用完整用量留存（get_agent_info 恢复通道数据源）
        self._last_real_output_tokens: int | None = None
        self._last_real_cache_read_tokens: int | None = None
        self._last_real_cache_write_tokens: int | None = None
        # 累计真实用量（成本口径：compact 不清零，reset 随 ContextManager 重建归零）
        self._cumulative_input_tokens: int = 0
        self._cumulative_output_tokens: int = 0
        self._cumulative_cache_read_tokens: int = 0
        # 手动 compact 请求标志（B/H 节接线消费）
        self._compact_requested: bool = False
        # 空窗跳过标志（振荡防护第二形态）：阈值触发的 compact 回合发现
        # 窗外为空时置位——此时锚点仍 >= trigger，若不抑制会每轮重进
        # 回合再退出死循环。下一次真实锚点记录（record_window_occupied）
        # 清除并重新武装；手动请求不受此标志否决。
        self._compact_skip_until_reanchor: bool = False

        # 初始化默认 Session：一次性创建 Root 与 main Branch。
        effective_messages = list(initial_messages or [])
        if system_prompt:
            if message_factory is None:
                raise ValueError(
                    "message_factory is required when system_prompt is provided. "
                    "Pass a MessageFactory instance (e.g. ChatMessageFactory())."
                )
            msg = message_factory.create_message(role="system", text=system_prompt)
            effective_messages.insert(0, msg)
        self._message_store.extend(effective_messages)

        root_runtime = SessionRuntime.create(
            agent_name=agent_name,
            system_prompt=system_prompt,
            agent_state=initial_state or {},
            messages=effective_messages,
            origin_agent_name=origin_agent_name,
            origin_session_id=origin_session_id,
            origin_node_id=origin_node_id,
            metadata=session_metadata,
            root_metadata=root_metadata,
        )
        self._session_runtimes: dict[str, SessionRuntime] = {
            root_runtime.session.session_id: root_runtime
        }
        self._active_session_id = root_runtime.session.session_id

        # 建立初始快照基准
        self._message_store.take_snapshot(0)
        if (
            self._auto_persist
            and self._persistence is not None
            and getattr(self._persistence, "connected", True)
        ):
            self._schedule_changes(
                ContextChanges(
                    agent_name=self._agent_name,
                    sessions=(root_runtime.session,),
                    branches=tuple(root_runtime.branches.values()),
                    nodes=(root_runtime.active_head,),
                    active_session_id=self._active_session_id,
                ),
                label=f"initialize session {root_runtime.session.session_id}",
            )

    # ----------------------------------------------------------------
    # 属性
    # ----------------------------------------------------------------

    @property
    def agent_name(self) -> str:
        """所属 Agent 名称。"""
        return self._agent_name

    @property
    def chain(self) -> ActionChain:
        """当前激活 Session 的单 Root ActionChain。"""
        return self._active_runtime.chain

    @property
    def active_head(self) -> ContextNode:
        """由 active_session_id 与 active_branch_id 推导的唯一运行 Head。"""
        return self._active_runtime.active_head

    @property
    def message_store(self) -> MessageStore:
        """底层消息存储（只读访问）。"""
        return self._message_store

    @property
    def in_iteration(self) -> bool:
        """是否在迭代中。"""
        return self._in_iteration

    @property
    def window_manager(self) -> WindowManager | None:
        """窗口管理器（只读访问）。"""
        return self._window_manager

    @property
    def token_estimator(self) -> TokenEstimator:
        """token 估算器 — WindowManager 实例优先，CM 级参数兜底。

        compact 回合与 recall 等消费方经本出口取统一口径，避免各自
        持有独立实例造成漂移。
        """
        if self._window_manager is not None:
            return self._window_manager.estimator
        return self._estimator

    @property
    def compact_threshold(self) -> float | None:
        """链上 compact 触发阈值（None 禁用）。"""
        return self._compact_threshold

    @property
    def compact_keep_recent(self) -> int:
        """compact 保留窗节点数。"""
        return self._compact_keep_recent

    @property
    def compact_method(self) -> str:
        """compact 综合方法名。"""
        return self._compact_method

    @property
    def persistence(self) -> PersistenceBackend | None:
        """持久化后端（只读访问）。"""
        return self._persistence

    @property
    def auto_persist(self) -> bool:
        """是否自动持久化。"""
        return self._auto_persist

    @property
    def message_factory(self) -> MessageFactory | None:
        """消息工厂（只读访问）。"""
        return self._message_factory

    @property
    def message_count(self) -> int:
        """当前 MessageStore 中的消息数量。"""
        return self._message_store.count

    @property
    def active_session_id(self) -> str:
        """当前激活 Session 的稳定 ID。"""
        return self._active_session_id

    @property
    def _active_runtime(self) -> SessionRuntime:
        """当前激活 Session 运行时。"""
        return self._session_runtimes[self._active_session_id]

    # ----------------------------------------------------------------
    # Session 管理
    # ----------------------------------------------------------------

    def create_session(
        self,
        *,
        name: str | None = None,
        origin_session_id: str | None = None,
        origin_node_id: str | None = None,
        system_prompt: str | None = None,
        initial_state: dict[str, Any] | None = None,
        initial_messages: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActionSession:
        """创建拥有独立 Root 的 Session，不隐式激活。

        传入 origin_session_id + origin_node_id 时，新 Root 复制来源节点的
        state/messages，但 parent_id 仍为 None。name 省略时按 Agent 内
        既有 Session 数量分配确定性的 ``Session N`` 展示名。
        """
        self._ensure_not_in_iteration("create a session")
        if (origin_session_id is None) != (origin_node_id is None):
            raise ValueError(
                "origin_session_id and origin_node_id must either both be set or both be None"
            )
        if origin_session_id is not None and (
            initial_state is not None or initial_messages is not None
        ):
            raise ValueError("origin context and explicit initial context are mutually exclusive")

        effective_name = self._next_session_name() if name is None else name.strip()
        if not effective_name:
            raise ValueError("session name must be a non-empty trimmed string")
        effective_prompt = self._system_prompt if system_prompt is None else system_prompt
        effective_state = initial_state or {}
        effective_messages = list(initial_messages or [])
        origin_agent_name: str | None = None
        if origin_session_id is not None and origin_node_id is not None:
            source = self._get_runtime(origin_session_id)
            source_node = source.chain.checkout(origin_node_id)
            if source_node is None:
                raise ValueError(
                    f"Node '{origin_node_id}' not found in session '{origin_session_id}'."
                )
            origin_agent_name = source.session.agent_name
            effective_state = source_node.agent_state
            effective_messages = self._messages_from_history(
                source.chain.get_history_from(origin_node_id)
            )
            effective_messages = [
                message
                for message in effective_messages
                if getattr(message, "role", None) != "system"
            ]

        if effective_prompt:
            if self._message_factory is None:
                raise ValueError("message_factory is required when system_prompt is provided")
            system_message = self._message_factory.create_message(
                role="system", text=effective_prompt
            )
            effective_messages.insert(0, system_message)

        runtime = SessionRuntime.create(
            agent_name=self._agent_name,
            name=effective_name,
            system_prompt=effective_prompt,
            agent_state=effective_state,
            messages=effective_messages,
            origin_agent_name=origin_agent_name,
            origin_session_id=origin_session_id,
            origin_node_id=origin_node_id,
            metadata=metadata,
        )
        self._session_runtimes[runtime.session.session_id] = runtime
        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(
                    agent_name=self._agent_name,
                    sessions=(runtime.session,),
                    branches=tuple(runtime.branches.values()),
                    nodes=(runtime.active_head,),
                    active_session_id=self._active_session_id,
                ),
                label=f"create session {runtime.session.session_id}",
            )
        return runtime.session

    def _next_session_name(self) -> str:
        """为 Agent 内新 Session 分配确定性的 ``Session N`` 展示名。

        N 从 1 递增取首个未被占用的编号（显式命名的 ``Session N`` 会
        跳过该编号），保证自动名在 Agent 内不与既有名撞车。
        """
        taken = {runtime.session.name for runtime in self._session_runtimes.values()}
        index = 1
        while f"Session {index}" in taken:
            index += 1
        return f"Session {index}"

    def list_sessions(self, *, include_deleted: bool = False) -> list[ActionSession]:
        """列出独立 Root Session；默认隐藏删除墓碑。"""
        sessions = [runtime.session for runtime in self._session_runtimes.values()]
        if include_deleted:
            return sessions
        return [session for session in sessions if session.lifecycle != "deleted"]

    def get_active_session(self) -> ActionSession:
        """获取当前激活 Session。"""
        return self._active_runtime.session

    def get_active_session_branch(self) -> ActionBranch:
        """获取当前激活 Session 的 active Branch（commit 后的权威 Head 引用）。"""
        return self._active_runtime.active_branch

    def activate_session(self, session_id: str) -> None:
        """显式激活 Session 并完整恢复其记忆的 Branch Head 上下文。"""
        self._ensure_not_in_iteration("activate a session")
        runtime = self._get_runtime(session_id)
        if runtime.session.lifecycle == "deleted":
            raise ValueError("Cannot activate a deleted session.")
        self._active_session_id = session_id
        self._restore_active_context()
        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(
                    agent_name=self._agent_name,
                    active_session_id=self._active_session_id,
                ),
                label=f"activate session {session_id}",
            )

    @contextmanager
    def use_session(self, session_id: str) -> Iterator[None]:
        """临时在指定开放 Session 执行，结束恢复原选择，不修改持久活跃指针。"""
        self._ensure_not_in_iteration("select a delivery session")
        try:
            runtime = self._get_runtime(session_id)
        except ValueError as exc:
            raise DeliverySessionUnavailableError("Delivery session is missing") from exc
        if runtime.session.lifecycle != "open":
            raise DeliverySessionUnavailableError("Delivery session is not open")
        previous = self._active_session_id
        if previous == session_id:
            yield
            return
        self._active_session_id = session_id
        self._restore_active_context()
        try:
            yield
        finally:
            self.ensure_iteration_closed()
            self._active_session_id = previous
            self._restore_active_context()

    async def reset(self) -> ActionSession:
        """重置运行上下文：新起默认 Session/main Branch 并激活（就地，不换后端）。

        旧 Session 全部保留（lifecycle 不动、不归档、不删除）；同一次
        增量变更集内只写新 Session/Branch/Root 与 active 指针（既有实体
        零冗余写），并返回前等待后台持久化完成——返回即已落库。

        Returns:
            新激活的默认 Session（展示名按 Agent 内序号顺延，如 "Session 2"）。

        Raises:
            RuntimeError: 迭代进行中（禁止在迭代事务中切换运行 Head）。
        """
        self._ensure_not_in_iteration("reset the agent")
        effective_messages: list[Any] = []
        if self._system_prompt:
            if self._message_factory is None:
                raise ValueError("message_factory is required when system_prompt is provided")
            effective_messages.append(
                self._message_factory.create_message(role="system", text=self._system_prompt)
            )
        runtime = SessionRuntime.create(
            agent_name=self._agent_name,
            name=self._next_session_name(),
            system_prompt=self._system_prompt,
            agent_state={},
            messages=effective_messages,
        )
        self._session_runtimes[runtime.session.session_id] = runtime
        self._active_session_id = runtime.session.session_id
        self._restore_active_context()
        self._pending_compaction = []
        self._compact_requested = False
        self._compact_skip_until_reanchor = False
        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(
                    agent_name=self._agent_name,
                    sessions=(runtime.session,),
                    branches=tuple(runtime.branches.values()),
                    nodes=(runtime.active_head,),
                    active_session_id=self._active_session_id,
                ),
                label=f"reset session {runtime.session.session_id}",
            )
            await self.wait_for_persist()
        return runtime.session

    def get_session(self, session_id: str) -> ActionSession:
        """获取指定 ID 的 session。

        Args:
            session_id: session ID

        Returns:
            对应的 Session 实例

        Raises:
            KeyError: session 不存在
        """
        if session_id not in self._session_runtimes:
            raise KeyError(f"Session '{session_id}' not found.")
        return self._session_runtimes[session_id].session

    def create_branch(
        self,
        *,
        session_id: str,
        name: str,
        from_node_id: str | None = None,
        parent_branch_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActionBranch:
        """在同一 Session 内从指定节点创建 Branch，不隐式激活。"""
        self._ensure_not_in_iteration("create a branch")
        runtime = self._get_runtime(session_id)
        if runtime.session.lifecycle == "deleted":
            raise ValueError("Cannot create a branch in a deleted session.")
        branch = runtime.create_branch(
            name=name,
            parent_branch_id=parent_branch_id,
            fork_point_node_id=from_node_id,
            metadata=metadata,
        )
        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(
                    agent_name=self._agent_name,
                    branches=(branch,),
                ),
                label=f"create branch {branch.branch_id}",
            )
        return branch

    def list_branches(
        self, session_id: str, *, include_deleted: bool = False
    ) -> list[ActionBranch]:
        """列出 Session 内的 Branch；默认隐藏删除墓碑。"""
        branches = list(self._get_runtime(session_id).branches.values())
        if include_deleted:
            return branches
        return [branch for branch in branches if branch.lifecycle != "deleted"]

    def activate_branch(self, session_id: str, branch_id: str) -> None:
        """显式激活当前 Session 内的 Branch 并完整恢复上下文。"""
        self._ensure_not_in_iteration("activate a branch")
        if session_id != self._active_session_id:
            raise ValueError("activate the session before activating one of its branches")
        runtime = self._get_runtime(session_id)
        if runtime.get_branch(branch_id).lifecycle == "deleted":
            raise ValueError("Cannot activate a deleted branch.")
        runtime.activate_branch(branch_id)
        self._restore_active_context()
        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(
                    agent_name=self._agent_name,
                    sessions=(runtime.session,),
                    active_session_id=self._active_session_id,
                ),
                label=f"activate branch {branch_id}",
            )

    def get_branch_head(self, session_id: str, branch_id: str) -> ContextNode:
        """获取指定 Session/Branch 的 Head。"""
        runtime = self._get_runtime(session_id)
        branch = runtime.get_branch(branch_id)
        node = runtime.chain.checkout(branch.head_node_id)
        if node is None:  # pragma: no cover - SessionRuntime 已校验
            raise RuntimeError("branch head is missing")
        return node

    def archive_session(self, session_id: str) -> ActionSession:
        """归档指定 session（lifecycle → archived）。

        Args:
            session_id: 要归档的 session ID

        Returns:
            归档后的 Session 实例（新对象，dataclasses.replace 创建）

        Raises:
            KeyError: session 不存在
        """
        self._ensure_not_in_iteration("archive a session")
        runtime = self._get_runtime(session_id, error_type=KeyError)
        if session_id == self._active_session_id:
            raise ValueError("Cannot archive the active session.")
        session = runtime.session
        archived = dataclasses.replace(session, lifecycle="archived")
        runtime.session = archived
        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(agent_name=self._agent_name, sessions=(archived,)),
                label=f"archive session {session_id}",
            )
        return archived

    def delete_session(self, session_id: str) -> ActionSession:
        """删除指定 session（lifecycle → deleted 墓碑，历史节点保留）。

        不能删除当前活跃的 session。

        Args:
            session_id: 要删除的 session ID

        Returns:
            被删除的 Session 实例

        Raises:
            KeyError: session 不存在
            ValueError: 试图删除当前活跃 session
        """
        if session_id not in self._session_runtimes:
            raise KeyError(f"Session '{session_id}' not found.")
        if session_id == self._active_session_id:
            raise ValueError("Cannot delete the active session.")
        self._ensure_not_in_iteration("delete a session")
        runtime = self._session_runtimes[session_id]
        deleted = dataclasses.replace(runtime.session, lifecycle="deleted")
        runtime.session = deleted
        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(
                    agent_name=self._agent_name,
                    sessions=(deleted,),
                ),
                label=f"delete session {session_id}",
            )
        return deleted

    def archive_branch(self, session_id: str, branch_id: str) -> ActionBranch:
        """归档非运行 Branch（lifecycle → archived），保留不可变节点。"""
        self._ensure_not_in_iteration("archive a branch")
        runtime = self._get_runtime(session_id)
        if session_id == self._active_session_id and branch_id == runtime.session.active_branch_id:
            raise ValueError("Cannot archive the active branch.")
        branch = runtime.get_branch(branch_id)
        archived = dataclasses.replace(branch, lifecycle="archived")
        runtime.branches[branch_id] = archived
        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(agent_name=self._agent_name, branches=(archived,)),
                label=f"archive branch {branch_id}",
            )
        return archived

    def delete_branch(self, session_id: str, branch_id: str) -> ActionBranch:
        """删除非运行 Branch（lifecycle → deleted 墓碑），不破坏节点 provenance 与拓扑。"""
        self._ensure_not_in_iteration("delete a branch")
        runtime = self._get_runtime(session_id)
        if session_id == self._active_session_id and branch_id == runtime.session.active_branch_id:
            raise ValueError("Cannot delete the active branch.")
        branch = runtime.get_branch(branch_id)
        deleted = dataclasses.replace(branch, lifecycle="deleted")
        runtime.branches[branch_id] = deleted
        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(agent_name=self._agent_name, branches=(deleted,)),
                label=f"delete branch {branch_id}",
            )
        return deleted

    def _get_runtime(
        self, session_id: str, *, error_type: type[Exception] = ValueError
    ) -> SessionRuntime:
        """获取 SessionRuntime，并统一产生领域错误。"""
        try:
            return self._session_runtimes[session_id]
        except KeyError as exc:
            raise error_type(f"Session '{session_id}' not found.") from exc

    def _ensure_not_in_iteration(self, operation: str) -> None:
        """禁止在迭代事务中改变运行 Head。"""
        if self._in_iteration:
            raise RuntimeError(f"Cannot {operation} during an iteration")

    @staticmethod
    def _messages_from_history(history: list[ContextNode]) -> list[Any]:
        """从最近快照与后续 delta 重建 Head 消息。"""
        snapshot_index = next(
            (
                index
                for index in range(len(history) - 1, -1, -1)
                if history[index].is_snapshot and history[index].messages_snapshot is not None
            ),
            None,
        )
        if snapshot_index is None:
            return [message for node in history for message in node.messages_delta]
        messages = list(history[snapshot_index].messages_snapshot or [])
        for node in history[snapshot_index + 1 :]:
            messages.extend(node.messages_delta)
        return messages

    def _restore_active_context(self) -> None:
        """从当前 Session/Branch Head 恢复 state 与 MessageStore。

        Head 变更（activate_branch/activate_session/restore）会使既有真实
        占用锚点失效——store 已按新 Head 重建，旧锚点不再计量当前视图，
        因此置 None，待下一次真实 LLM 调用重建。
        """
        runtime = self._active_runtime
        head = runtime.active_head
        messages = self._messages_from_history(runtime.get_history())
        self._state_manager.reset(head.agent_state)
        self._message_store = MessageStore(snapshot_interval=self._message_store.snapshot_interval)
        self._message_store.extend(messages)
        self._message_store.take_snapshot(head.iteration)
        self._system_prompt = runtime.session.system_prompt
        self._pending_messages = []
        self._window_occupied = None
        # Head 变更后历史视图已重建：目录摘要按"首次"文案重新注入
        self._skill_catalog_digest = None

    # ----------------------------------------------------------------
    # 迭代生命周期
    # ----------------------------------------------------------------

    def begin_iteration(self) -> None:
        """开始新迭代。

        开启状态事务，清空 pending_messages 缓冲区。

        Raises:
            RuntimeError: 已有迭代在进行中
        """
        if self._in_iteration:
            raise RuntimeError("Iteration already in progress")
        self._state_manager.begin_transaction()
        self._pending_messages = []
        self._in_iteration = True

    def apply_state_changes(self, changes: dict[str, Any]) -> dict[str, Any]:
        """在当前迭代的事务中应用状态变更。

        变更累积到 pending 区，commit 时才生效。

        Args:
            changes: 要应用的变更（支持嵌套 dict 递归合并）

        Returns:
            合并后的预期状态（深拷贝）

        Raises:
            RuntimeError: 没有活跃的迭代
        """
        if not self._in_iteration:
            raise RuntimeError("No iteration in progress")
        return self._state_manager.apply_changes(changes)

    def add_messages(self, messages: list[Any]) -> None:
        """收集本轮新消息到缓冲区。

        消息在 commit_iteration 时才写入 MessageStore，
        rollback_iteration 时会被丢弃。

        Args:
            messages: ChatMessage 列表
        """
        if self._in_iteration:
            self._pending_messages.extend(messages)
        else:
            self._message_store.extend(messages)

    def commit_iteration(
        self,
        ability_names: list[str] | None = None,
        action_results: list[dict[str, Any]] | None = None,
        state_changes: dict[str, Any] | None = None,
        llm_metadata: dict[str, Any] | None = None,
    ) -> ContextNode:
        """提交当前迭代。

        流程：
        1. 如果有额外的 state_changes，先应用
        2. 提交状态事务
        3. 将 pending_messages 写入 MessageStore
        4. 计算 delta / 判断是否需要 snapshot
        5. 创建链式节点

        Args:
            ability_names: 本轮执行的 ability 名称列表
            action_results: 本轮执行结果列表
            state_changes: 额外的状态变更（可选）
            llm_metadata: LLM 响应元数据

        Returns:
            新创建的 ContextNode

        Raises:
            RuntimeError: 没有活跃的迭代
        """
        if not self._in_iteration:
            raise RuntimeError("No iteration in progress")

        effective_names: list[str]
        effective_result: Any | None

        if ability_names is not None:
            effective_names = ability_names
            effective_result = action_results
        else:
            effective_names = ["unknown"]
            effective_result = None

        # 1. 如果有额外的 state_changes，先应用
        if state_changes:
            self._state_manager.apply_changes(state_changes)

        # 2. 提交状态事务
        new_state = self._state_manager.commit()

        # 3. 将 pending_messages 写入 MessageStore
        self._message_store.extend(self._pending_messages)

        # 4. 计算迭代号（基于当前 Branch Head）
        iteration = self._active_runtime.active_head.iteration + 1

        # 5. delta 为当前轮次新增的消息
        delta = list(self._pending_messages)

        # 6. 判断是否需要快照
        is_snapshot = self._message_store.should_snapshot(iteration)
        if is_snapshot:
            snapshot = self._message_store.take_snapshot(iteration)
        else:
            snapshot = None

        # 7. 合并 llm_metadata 到节点 metadata
        node_metadata: dict[str, Any] = {}
        if llm_metadata:
            node_metadata.update(llm_metadata)

        # 7.1 合并窗口压缩事件记录（内部权威，来源为 get_llm_messages 的 stash）
        if self._pending_compaction:
            node_metadata["compaction"] = list(self._pending_compaction)
            self._pending_compaction = []

        # 8. 创建链式节点并前移当前 Branch Head
        node = self._active_runtime.commit_node(
            ability_names=effective_names,
            agent_state=new_state,
            messages_delta=delta,
            messages_snapshot=snapshot,
            is_snapshot=is_snapshot,
            action_results=effective_result or [],
            metadata=node_metadata or None,
        )

        # 10. 清理迭代状态
        self._pending_messages = []
        self._in_iteration = False

        # 11. 自动持久化（如果启用）
        if self._auto_persist and self._persistence is not None:
            self._schedule_persist_node(node)

        return node

    async def commit_iteration_async(
        self,
        *,
        ability_names: list[str] | None = None,
        action_results: list[dict[str, Any]] | None = None,
        state_changes: dict[str, Any] | None = None,
        llm_metadata: dict[str, Any] | None = None,
    ) -> ContextNode:
        """原子作用域内先持久提交再推进内存；旧调用保留原提交方式。"""
        committer = get_iteration_committer(self)
        options = {
            "ability_names": ability_names,
            "action_results": action_results,
            "state_changes": state_changes,
            "llm_metadata": llm_metadata,
        }
        if committer is None:
            return self.commit_iteration(**options)
        if not self._in_iteration:
            raise RuntimeError("No iteration in progress")
        # 旧后台变更必须先完成，防止较早的 branch head 在持久提交后覆盖新 head。
        await self.wait_for_persist()
        candidate = self._commit_candidate()
        node = candidate.commit_iteration(**options)
        return await self._commit_durable_candidate(candidate, node, committer)

    def _commit_candidate(self) -> ContextManager:
        """复制本次提交会修改的内存聚合，暂不推进权威 Head。"""
        candidate = copy.copy(self)
        candidate._session_runtimes = dict(self._session_runtimes)
        candidate._session_runtimes[self._active_session_id] = copy.deepcopy(self._active_runtime)
        candidate._state_manager = copy.deepcopy(self._state_manager)
        candidate._message_store = copy.deepcopy(self._message_store)
        candidate._pending_messages = list(self._pending_messages)
        candidate._pending_compaction = copy.deepcopy(self._pending_compaction)
        candidate._auto_persist = False
        return candidate

    async def _commit_durable_candidate(
        self, candidate: ContextManager, node: ContextNode, committer: IterationCommitter
    ) -> ContextNode:
        """等待同事务持久化完成，再安装候选内存视图。"""
        changes = ContextChanges(
            agent_name=self._agent_name,
            sessions=(candidate.get_active_session(),),
            nodes=(node,),
            branches=(candidate.get_active_session_branch(),),
        )
        # COMMIT 期间取消也要等待明确结果；成功后必须推进内存，不能假装回滚。
        task = asyncio.create_task(committer(changes))
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                cancelled = True
                if task.cancelled():
                    raise
        self._session_runtimes = candidate._session_runtimes
        self._state_manager = candidate._state_manager
        self._message_store = candidate._message_store
        self._pending_messages = candidate._pending_messages
        self._pending_compaction = candidate._pending_compaction
        self._in_iteration = candidate._in_iteration
        self._window_occupied = candidate._window_occupied
        self._system_prompt = candidate._system_prompt
        if node.metadata.get("node_kind") == "compact":
            self._compact_requested = False
        if cancelled:
            raise asyncio.CancelledError
        return node

    # ----------------------------------------------------------------
    # 回滚即分支
    # ----------------------------------------------------------------

    def ensure_iteration_closed(self) -> None:
        """兜底关闭未收尾的迭代事务（幂等）。

        正常路径 commit_iteration / rollback_iteration 已复位迭代状态，
        本方法为 no-op。调用场景是 BaseException（如 asyncio.CancelledError、
        KeyboardInterrupt）逃逸驱动循环的 except Exception 捕获面：迭代标志
        与状态事务可能残留开启，导致下一次 begin_iteration 抛
        RuntimeError("Iteration already in progress")，agent 对后续消息
        永久失效。

        处理与 rollback_iteration 的差异：不创建重试 Branch（取消不是失败，
        链上无对应节点，无处落错误元数据）、不落任何持久化——仅丢弃本轮
        pending（从未进入 store，store 本就正确）并复位事务。窗口锚点若
        计量的是已废弃的发送视图，同样失效。

        调用方应置于 finally 兜底；非预期残留以 warning 计数，不静默。
        """
        if not self._in_iteration:
            return
        discard_iteration_effects(self)
        logger.warning(
            "ContextManager[%s]: iteration left open (likely cancelled mid-flight) "
            "— force-closing pending buffers and state transaction",
            self._agent_name,
        )
        if self._state_manager.in_transaction:
            self._state_manager.rollback()
        self._pending_messages = []
        self._pending_compaction = []
        self._in_iteration = False
        self._window_occupied = None

    def rollback_iteration(self, error: Exception) -> ActionBranch:
        """回滚未提交迭代，从当前 Head 创建并激活重试 Branch。

        未提交迭代不会产生 ContextNode，因此重试 Branch 的 fork point 就是
        迭代开始时的 Head。错误信息存在 Branch metadata 中。

        Args:
            error: 导致回滚的异常

        Returns:
            新创建并已激活的重试 Branch

        Raises:
            RuntimeError: 没有活跃的迭代
        """
        if not self._in_iteration:
            raise RuntimeError("No iteration in progress")
        discard_iteration_effects(self)

        # 1. 回滚状态事务
        self._state_manager.rollback()

        runtime = self._active_runtime
        current_branch = runtime.active_branch
        current_head = runtime.active_head
        retry_number = len(runtime.branches)
        branch = runtime.create_branch(
            name=f"retry-{retry_number}",
            parent_branch_id=current_branch.branch_id,
            fork_point_node_id=current_head.id,
            metadata={
                "is_rollback": True,
                "rollback_from_branch_id": current_branch.branch_id,
                "rollback_from_node_id": current_head.id,
                "rollback_to_node_id": current_head.id,
                "error": str(error),
                "error_type": type(error).__name__,
            },
        )
        runtime.activate_branch(branch.branch_id)

        # 8. 清理迭代状态
        self._pending_messages = []
        self._pending_compaction = []
        self._in_iteration = False
        # pending 消息从未进入 store，store 本就正确（不重建）；但本轮迭代
        # 若有 LLM 调用，其锚点计量的是已回滚的发送视图，须失效
        self._window_occupied = None

        if self._auto_persist and self._persistence is not None:
            self._schedule_changes(
                ContextChanges(
                    agent_name=self._agent_name,
                    sessions=(runtime.session,),
                    branches=(branch,),
                    active_session_id=self._active_session_id,
                ),
                label=f"rollback to branch {branch.branch_id}",
            )
        return branch

    # ----------------------------------------------------------------
    # 上下文构建
    # ----------------------------------------------------------------

    def build_execution_context(
        self,
        message: AgentMessage | None = None,
        config: AgentConfig | None = None,
        system_prompt: str | None = None,
        context_filter: Callable | None = None,
    ) -> AbilityExecutionContext:
        """构建 AbilityExecutionContext。

        Args:
            message: 输入消息（可选，保留向后兼容）
            config: Agent 配置（可选）
            system_prompt: 覆盖默认 system_prompt（可选）
            context_filter: 上下文过滤函数（可选）

        Returns:
            初始化后的 AbilityExecutionContext
        """
        from ghrah.scheme.execution.context import AbilityExecutionContext

        return AbilityExecutionContext(
            current_ability_name="",
            agent_state=self._state_manager.current,
            context_manager=self,
        )

    async def get_llm_messages(
        self,
        max_tokens: int | None = None,
        filter_fn: Callable | None = None,
    ) -> list[Any]:
        """获取 LLM 消息列表。

        返回经过窗口管理策略处理后的消息列表。
        如果配置了 WindowManager，会自动应用压缩策略确保消息在 token 预算内。

        Args:
            max_tokens: 最大 token 数（可选，覆盖 WindowManager 的默认值）
            filter_fn: 消息过滤函数（可选）

        Returns:
            ChatMessage 列表
        """
        messages = self._message_store.current_messages

        # 合并 pending_messages（迭代中尚未 commit 的消息）
        if self._in_iteration and self._pending_messages:
            messages = messages + list(self._pending_messages)
            logger.debug(
                "get_llm_messages: merged %d pending messages into LLM context (total=%d)",
                len(self._pending_messages),
                len(messages),
            )

        if filter_fn is not None:
            messages = [m for m in messages if filter_fn(m)]

        # 应用窗口管理策略
        if self._window_manager is not None:
            budget = max_tokens or self._window_manager.max_tokens
            messages = await self._window_manager.apply(messages, max_tokens=budget)
            records = self._window_manager.drain_compaction_records()
            if records:
                self._pending_compaction.extend(records)

        # 技能目录摘要：窗口管理之后追加——不被压缩裁掉，且始终位于队列末尾
        catalog = self._skill_catalog_message()
        if catalog is not None:
            messages = [*messages, catalog]

        return messages

    def _skill_catalog_message(self) -> Any | None:
        """构造技能目录摘要消息；未接线或构造失败 → None（不阻断本轮问答）。

        注入物**不写入消息链**（读侧派生），所以不能像 DSH 那样"注入过就跳过"
        ——每轮都拼在队列末尾；``digest`` 只决定文案口径（首次 / 已变化）与
        变化日志。
        """
        provider = self._skill_provider
        if provider is None:
            return None
        if self._message_factory is None:
            logger.warning(
                "skill_provider is wired but message_factory is missing "
                "— skill catalog injection skipped"
            )
            return None
        try:
            scan = provider.scan()
        except Exception:
            logger.warning("skill catalog scan failed — injection skipped", exc_info=True)
            return None
        if not scan.skills and self._skill_catalog_digest is None:
            # 从未发布过且清单为空：无事可报（对齐 DSH），也不占位
            return None

        digest = digest_skill_catalog(scan.skills)
        first = self._skill_catalog_digest is None
        changed = not first and digest != self._skill_catalog_digest
        if scan.issues and (first or changed):
            logger.warning(
                "skill catalog: %d skill(s) failed to scan and are omitted",
                len(scan.issues),
            )
        if changed:
            logger.info("skill catalog changed — injecting the replacement catalog")
        self._skill_catalog_digest = digest
        return self._message_factory.create_message(
            role="user",
            text=render_skill_catalog(scan.skills, changed=changed),
            source="skill:catalog",
        )

    @property
    def skill_provider(self) -> Any | None:
        """SkillAbilityProvider（装配期显式接线；None = 未接线）。"""
        return self._skill_provider

    def get_cumulative_token_usage(self) -> dict[str, int]:
        """从链历史中累计计算总 token 用量。

        遍历所有链节点的 metadata.token_usage 字段，累加得到总和。
        旧节点缺 cache 键时按 0 计（.get 兼容）。

        Returns:
            包含 input_tokens、output_tokens、total_tokens、
            cache_read_tokens、cache_write_tokens 的 dict
        """
        total: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        for node in self._active_runtime.get_history():
            usage = node.metadata.get("token_usage", {})
            if isinstance(usage, dict):
                total["input_tokens"] += usage.get("input_tokens", 0)
                total["output_tokens"] += usage.get("output_tokens", 0)
                total["total_tokens"] += usage.get("total_tokens", 0)
                total["cache_read_tokens"] += usage.get("cache_read_tokens", 0)
                total["cache_write_tokens"] += usage.get("cache_write_tokens", 0)
        return total

    # ----------------------------------------------------------------
    # 窗口占用锚点与链上 compact 决策
    # ----------------------------------------------------------------

    def record_window_occupied(self, real_input_tokens: int) -> None:
        """记录最近一次主循环真实 LLM 调用的 input_tokens 锚点。

        该锚点是链上 compact 决策的唯一计量权威：决策与触发均以
        真实用量为准，估算值不参与。锚点仅在两处失效：
        ``_restore_active_context``（head 变更）与 ``rollback_iteration``。

        Args:
            real_input_tokens: 首响应真实 input_tokens（多响应取首响应）
        """
        self._window_occupied = real_input_tokens
        self._last_real_input_tokens = real_input_tokens
        # 新真实锚点重新武装阈值触发（清除空窗跳过标志）
        self._compact_skip_until_reanchor = False

    def record_real_usage(self, usage: Any) -> None:
        """记录最近一次主循环真实 LLM 调用的完整用量。

        在锚点语义（record_window_occupied）之上扩展：留存 output/cache
        拆分并累计三值（成本口径）。锚点失效（回滚/head 变更）只清
        occupied 锚点，本方法的留存与累计不受影响。

        Args:
            usage: 首响应的 TokenUsage（多响应取首响应）
        """
        self.record_window_occupied(usage.input_tokens)
        self._last_real_output_tokens = usage.output_tokens
        self._last_real_cache_read_tokens = usage.cache_read_tokens
        self._last_real_cache_write_tokens = usage.cache_write_tokens
        self._cumulative_input_tokens += usage.input_tokens
        self._cumulative_output_tokens += usage.output_tokens
        self._cumulative_cache_read_tokens += usage.cache_read_tokens

    def evaluate_compaction_decision(self) -> dict[str, Any] | None:
        """评估当前是否需要链上 compact，产出决策记录。

        仅在配置了 compact_threshold 且存在 WindowManager 时参与决策；
        否则返回 None（不写节点 metadata，与现状行为 bit 级一致）。
        无锚点（从未记录或已失效）时 needs_compaction 恒为 False，
        交由发送侧安全阀兜底。

        Returns:
            决策 dict：{occupied, threshold, needs_compaction}；不参与决策
            时返回 None
        """
        if self._compact_threshold is None or self._window_manager is None:
            return None
        occupied = self._window_occupied
        trigger = int(self._window_manager.max_tokens * self._compact_threshold)
        needs = occupied is not None and occupied >= trigger
        return {
            "occupied": occupied,
            "threshold": self._compact_threshold,
            "needs_compaction": needs,
        }

    def check_compact_trigger(self) -> bool:
        """触发门判定（锚点活体复评）。

        供驱动循环在每轮迭代开头、普通 action 之前调用：
        (a) 手动标志（显式意图，不受锚点状态否决）；
        (b) 活体锚点达到阈值（空窗跳过标志置位时抑制——振荡防护）。
        节点 metadata 的 ``needs_compaction`` 仅作审计记录，不作为触发依据。

        Returns:
            是否应进入 compact 回合
        """
        if self._compact_requested:
            return True
        if self._compact_threshold is None or self._window_manager is None:
            return False
        if self._window_occupied is None or self._compact_skip_until_reanchor:
            return False
        trigger = int(self._window_manager.max_tokens * self._compact_threshold)
        return self._window_occupied >= trigger

    def skip_compact_until_reanchor(self) -> None:
        """置空窗跳过标志（阈值触发的 compact 回合发现窗外为空时调用）。

        抑制锚点仍超阈值时的反复空转；下一次 record_window_occupied
        （新真实锚点，通常对应 compact 后变短的发送视图）清除并重新评估。
        """
        self._compact_skip_until_reanchor = True

    def request_compact(self) -> None:
        """置手动 compact 请求标志（幂等）。

        标志由 compact 回合的窗外为空守卫与消费点清除（B/H 节）。
        """
        self._compact_requested = True

    @property
    def compact_requested(self) -> bool:
        """手动 compact 请求标志是否置位（触发源判定用）。"""
        return self._compact_requested

    def commit_compact_node(
        self, snapshot_messages: list[Any], metadata: dict[str, Any] | None = None
    ) -> ContextNode:
        """提交链上 compact 节点。

        与 commit_iteration 的关键差异：
        - 跳过 ``should_snapshot``/活 store 自拍——compact 时活 store 仍是
          压缩前的旧全量，naive 快照会把旧全量拍进节点，压缩归零；
          本路径直接注入压缩视图 deepcopy 为 messages_snapshot；
        - messages_delta 恒空、ability_names=["compact"]；
        - 提交后按 ``_restore_active_context`` 模式重建 store（compact
          节点 is_snapshot=True，天然成为回放新基准）并置锚点 None；
        - 不计入 snapshot_interval 节奏（后续常规快照照常）。

        Args:
            snapshot_messages: 压缩视图消息（调用方组装，本方法深拷贝注入）
            metadata: 节点 metadata（node_kind/method/trigger_source 等，
                本方法强制合并 node_kind="compact"）

        Returns:
            新创建的 compact ContextNode

        Raises:
            RuntimeError: 迭代进行中（compact 回合必须不在迭代事务内）
        """
        if self._in_iteration:
            raise RuntimeError("Cannot commit compact node during an iteration")

        node_metadata = dict(metadata or {})
        node_metadata["node_kind"] = "compact"

        node = self._active_runtime.commit_node(
            ability_names=["compact"],
            agent_state=self._active_runtime.active_head.agent_state,
            messages_delta=[],
            messages_snapshot=copy.deepcopy(snapshot_messages),
            is_snapshot=True,
            action_results=[],
            metadata=node_metadata,
        )

        # store 按新 head（compact 节点快照）重建，锚点随之失效
        self._restore_active_context()

        if self._auto_persist and self._persistence is not None:
            self._schedule_persist_node(node)

        return node

    async def commit_compact_node_async(
        self, snapshot_messages: list[Any], metadata: dict[str, Any] | None = None
    ) -> ContextNode:
        """原子接收内持久提交压缩节点，压缩本身不消费新的输入消息。"""
        committer = get_iteration_committer(self)
        if committer is None:
            return self.commit_compact_node(snapshot_messages, metadata)
        if self._in_iteration:
            raise RuntimeError("Cannot commit compact node during an iteration")
        await self.wait_for_persist()
        candidate = self._commit_candidate()
        node = candidate.commit_compact_node(snapshot_messages, metadata)
        return await self._commit_durable_candidate(candidate, node, committer)

    def consume_compact_request(self) -> bool:
        """消费手动 compact 请求标志。

        Returns:
            标志是否原本为 True
        """
        was_requested = self._compact_requested
        self._compact_requested = False
        return was_requested

    def get_window_status(self) -> dict[str, Any]:
        """导出窗口占用状态（观测出口，C.2）。

        Returns:
            状态 dict：occupied_tokens 为锚点值（可能为 None）；
            basis 恒为 "anchor"（同步快照即锚点口径；"real" 保留给
            post_call 事件相位）；无 WindowManager 时 budget_tokens 为 0、
            budget_source 为 None、compact_threshold 为 None
        """
        return {
            "occupied_tokens": self._window_occupied,
            "basis": "anchor",
            "budget_tokens": (
                self._window_manager.max_tokens if self._window_manager is not None else 0
            ),
            "budget_source": (
                self._window_manager.max_tokens_source if self._window_manager is not None else None
            ),
            "compact_threshold": (
                self._compact_threshold if self._window_manager is not None else None
            ),
            "compact_keep_recent": self._compact_keep_recent,
            "compact_method": self._compact_method,
            "last_real_input_tokens": self._last_real_input_tokens,
            "last_real_output_tokens": self._last_real_output_tokens,
            "last_real_cache_read_tokens": self._last_real_cache_read_tokens,
            "last_real_cache_write_tokens": self._last_real_cache_write_tokens,
            "cumulative_input_tokens": self._cumulative_input_tokens,
            "cumulative_output_tokens": self._cumulative_output_tokens,
            "cumulative_cache_read_tokens": self._cumulative_cache_read_tokens,
        }

    def peek_pending_compaction(self) -> list[dict[str, Any]]:
        """只读窥视待排水压缩记录（不排水，C.3）。

        供事件组装读取 ``get_llm_messages`` 产生的压缩审计记录；
        排水仍由 ``commit_iteration`` 的既有合并逻辑负责。

        Returns:
            当前待排水记录的浅拷贝列表
        """
        return list(self._pending_compaction)

    # ----------------------------------------------------------------
    # 子 Agent 支持
    # ----------------------------------------------------------------

    def fork_for_sub_agent(
        self,
        agent_name: str,
        system_prompt: str | None = None,
        state_filter: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        snapshot_interval: int | None = None,
        window_manager: WindowManager | None = None,
    ) -> ContextManager:
        """为子 Agent fork 出独立的上下文。

        向后兼容的子 Agent fork 方法。内部调用 create_rebased_context()。

        注意：如果 system_prompt=None，旧行为是继承父 Agent 的 system_prompt。
        这与 rebase 的默认语义（不继承 system_prompt）不同。
        为保持向后兼容，当 system_prompt=None 时使用父 Agent 的 system_prompt。

        Args:
            agent_name: 子 Agent 名称
            system_prompt: 覆盖 system_prompt（可选，None 继承父 Agent 的）
            state_filter: 状态过滤函数，选择性继承状态（可选）
            snapshot_interval: 快照间隔（可选，默认使用父 CM 的设置）
            window_manager: 子 Agent 的窗口管理器（可选，None 不配置）。
                必须传入独立实例——共享会使摘要策略状态与压缩排水记录
                跨 Agent 互串

        Returns:
            子 Agent 的 ContextManager
        """
        from ghrah.context.rebase import create_rebased_context

        effective_prompt = system_prompt or self._system_prompt
        return create_rebased_context(
            source_cm=self,
            agent_name=agent_name,
            system_prompt=effective_prompt,
            inherit_messages=True,
            inherit_state=True,
            state_filter=state_filter,
            snapshot_interval=snapshot_interval,
            window_manager=window_manager,
        )

    # ----------------------------------------------------------------
    # 查询
    # ----------------------------------------------------------------

    def get_history(
        self,
        limit: int = -1,
        *,
        session_id: str | None = None,
        branch_id: str | None = None,
    ) -> list[ContextNode]:
        """获取链历史。

        Args:
            limit: 限制返回数量。-1 表示全部，>0 返回最近 N 个。

        Returns:
            历史节点列表（根在前）
        """
        runtime = self._get_runtime(session_id or self._active_session_id)
        return runtime.get_history(branch_id=branch_id, limit=limit)

    def get_messages(
        self,
        *,
        session_id: str | None = None,
        branch_id: str | None = None,
    ) -> list[Any]:
        """从指定 Session/Branch Head 重建完整消息上下文。"""
        history = self.get_history(session_id=session_id, branch_id=branch_id)
        return self._messages_from_history(history)

    def get_current_state(self) -> dict[str, Any]:
        """获取当前状态的深拷贝。

        Returns:
            当前状态快照
        """
        return self._state_manager.get_snapshot()

    def set_state(self, key: str, value: Any) -> None:
        """设置单个状态键值（迭代外 API）。

        直接操作 StateManager，不经过事务。仅限迭代外调用；
        迭代中应使用 apply_state_changes() 写入事务 pending 区。

        Args:
            key: 状态键
            value: 状态值

        Raises:
            RuntimeError: 在迭代中调用（应改用 apply_state_changes()）
        """
        if self._in_iteration:
            raise RuntimeError(
                "Cannot call set_state() during iteration. Use apply_state_changes() instead."
            )
        current = self._state_manager.current
        current[key] = value
        self._state_manager.reset(new_state=current)

    def remove_state(self, key: str) -> None:
        """删除单个状态键（迭代外 API）。

        直接操作 StateManager，不经过事务。与 set_state 对称，
        键缺席时幂等静默。仅限迭代外调用；迭代中应使用
        apply_state_changes() 写入事务 pending 区。

        Args:
            key: 状态键

        Raises:
            RuntimeError: 在迭代中调用（应改用 apply_state_changes()）
        """
        if self._in_iteration:
            raise RuntimeError(
                "Cannot call remove_state() during iteration. Use apply_state_changes() instead."
            )
        current = self._state_manager.current
        current.pop(key, None)
        self._state_manager.reset(new_state=current)

    def update_state(self, partial: dict[str, Any]) -> None:
        """合并更新多个状态键（迭代外 API）。

        直接操作 StateManager，不经过事务。仅限迭代外调用；
        迭代中应使用 apply_state_changes() 写入事务 pending 区。

        Args:
            partial: 要合并的状态变更

        Raises:
            RuntimeError: 在迭代中调用（应改用 apply_state_changes()）
        """
        if self._in_iteration:
            raise RuntimeError(
                "Cannot call update_state() during iteration. Use apply_state_changes() instead."
            )
        current = self._state_manager.current
        current.update(partial)
        self._state_manager.reset(new_state=current)

    def get_branch_heads(self, session_id: str | None = None) -> dict[str, ContextNode]:
        """获取指定 Session 所有 Branch 的 Head。

        Returns:
            branch_id → Head ContextNode 的映射
        """
        runtime = self._get_runtime(session_id or self._active_session_id)
        result: dict[str, ContextNode] = {}
        for branch_id, branch in runtime.branches.items():
            node = runtime.chain.checkout(branch.head_node_id)
            if node is not None:
                result[branch_id] = node
        return result

    def get_chain_node(self, node_id: str, session_id: str | None = None) -> ContextNode | None:
        """获取指定 ID 的链节点。

        Args:
            node_id: 节点 ID

        Returns:
            对应的 ContextNode，不存在则返回 None
        """
        runtime = self._get_runtime(session_id or self._active_session_id)
        return runtime.chain.checkout(node_id)

    # ----------------------------------------------------------------
    # 持久化
    # ----------------------------------------------------------------

    async def persist(self) -> None:
        """显式持久化当前完整状态到后端。

        保存内容：
        1. 链的所有节点（遍历 ActionChain 内部节点）
        2. 链元信息（分支映射 + 当前状态 + 活跃 session）
        3. 当前完整消息列表
        4. 所有 session

        Raises:
            RuntimeError: 未配置持久化后端
        """
        if self._persistence is None:
            logger.warning("persist() called but no persistence backend configured")
            return

        # 等待所有后台 auto-persist 任务完成，避免竞态
        await self.wait_for_persist()
        await self._persist_complete_checkpoint()

    async def _persist_complete_checkpoint(self) -> None:
        """写完整 checkpoint；调用方负责避免把当前 task 纳入 wait。"""
        if self._persistence is None:
            return

        async with self._persist_lock:
            runtimes = list(self._session_runtimes.values())
            changes = ContextChanges(
                agent_name=self._agent_name,
                active_session_id=self._active_session_id,
                sessions=tuple(runtime.session for runtime in runtimes),
                branches=tuple(
                    branch for runtime in runtimes for branch in runtime.branches.values()
                ),
                nodes=tuple(node for runtime in runtimes for node in runtime.chain.nodes),
            )
            await self._persistence.apply_changes(changes)
            logger.debug(
                "Upserted context for agent '%s': %d sessions, %d branches, %d nodes",
                self._agent_name,
                len(changes.sessions),
                len(changes.branches),
                len(changes.nodes),
            )

    async def restore(self, agent_name: str) -> None:
        """从完整 checkpoint 恢复多 SessionRuntime 和当前运行上下文。

        Args:
            agent_name: 要恢复的 Agent 名称

        Raises:
            RuntimeError: 未配置持久化后端
            ValueError: 后端中无该 agent 的数据
        """
        if self._persistence is None:
            raise RuntimeError("No persistence backend configured")

        checkpoint = await self._persistence.load_checkpoint(agent_name)
        if checkpoint is None:
            raise ValueError(f"No persisted data found for agent '{agent_name}'")

        branches_by_session: dict[str, list[ActionBranch]] = {}
        nodes_by_session: dict[str, list[ContextNode]] = {}
        for branch in checkpoint.branches:
            branches_by_session.setdefault(branch.session_id, []).append(branch)
        for node in checkpoint.nodes:
            nodes_by_session.setdefault(node.session_id, []).append(node)

        runtimes = {
            session.session_id: SessionRuntime.restore(
                session=session,
                branches=branches_by_session.get(session.session_id, []),
                nodes=nodes_by_session.get(session.session_id, []),
            )
            for session in checkpoint.sessions
        }
        self._agent_name = agent_name
        self._session_runtimes = runtimes
        self._active_session_id = checkpoint.active_session_id
        self._restore_active_context()
        self._in_iteration = False

        logger.debug(
            "Restored context for agent '%s': %d nodes, %d sessions, %d messages",
            agent_name,
            sum(runtime.chain.node_count for runtime in runtimes.values()),
            len(runtimes),
            self._message_store.count,
        )

    async def wait_for_persist(self) -> None:
        """等待所有待处理的后台持久化任务完成。

        在调用 persist() 前自动调用，也可手动调用以确保所有
        auto-persist 调度的节点保存操作已完成。
        """
        if self._persist_tasks:
            await asyncio.gather(*self._persist_tasks, return_exceptions=True)
            self._persist_tasks.clear()

    def persist_node(self, node: ContextNode) -> None:
        """公共方法：后台异步保存单个节点（不阻塞主循环）。

        包装内部 _schedule_persist_node，供 rebase 等外部场景使用，
        消除对私有方法的直接访问。

        Args:
            node: 要保存的 ContextNode
        """
        self._schedule_persist_node(node)

    # ----------------------------------------------------------------
    # LLM 注入支持
    # ----------------------------------------------------------------

    def inject_llm_into_summary(self, llm: Any) -> None:
        """将 LLM 注入到 WindowManager 的 LLMSummaryStrategy 中。

        遍历 window_manager.strategies（使用只读 property），
        对尚未设置 LLM 的 LLMSummaryStrategy 注入。

        Args:
            llm: 实现 LLMProtocol 的 LLM 实例
        """
        if self._window_manager is None:
            return
        from ghrah.context.strategies.llm_summary import LLMSummaryStrategy

        for strategy in self._window_manager.strategies:
            if isinstance(strategy, LLMSummaryStrategy) and strategy.llm is None:
                strategy.set_llm(llm)

    def _schedule_persist_node(self, node: ContextNode) -> None:
        """后台异步保存单节点与对应 Branch Head 变更。

        Args:
            node: 要保存的 ContextNode
        """
        runtime = self._get_runtime(node.session_id)
        branch = runtime.get_branch(node.created_on_branch_id)
        self._schedule_changes(
            ContextChanges(
                agent_name=self._agent_name,
                branches=(branch,),
                nodes=(node,),
            ),
            label=f"commit node {node.id}",
        )

    def _schedule_changes(self, changes: ContextChanges, *, label: str) -> None:
        """按调度顺序在后台原子应用增量变更集。"""
        if self._persistence is None:
            return
        tasks = self._persist_tasks
        predecessors = tuple(tasks)
        backend = self._persistence

        async def _tracked_save() -> None:
            try:
                if predecessors:
                    await asyncio.gather(*predecessors, return_exceptions=True)
                async with self._persist_lock:
                    await backend.apply_changes(changes)
            except Exception:
                logger.warning(
                    "Failed to persist %s for agent '%s'",
                    label,
                    changes.agent_name,
                    exc_info=True,
                )
            finally:
                tasks.discard(task)

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_tracked_save())
            tasks.add(task)
        except RuntimeError:
            logger.debug("No running event loop, skipping auto-persist for %s", label)
