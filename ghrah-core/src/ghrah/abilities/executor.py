# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Ability 执行器：将 Ability 执行从 Agent 循环中解耦。

核心设计：
- AbilityExecutor 是 Ability 执行的抽象接口
- LocalAbilityExecutor 在 Core 端本地执行 Ability（单体模式）

单体模式下，HITL 在 Core 端处理：
    Hook → HITLFutureStore.create_future() → EventPublisher.publish(HITLRequestEvent)
    → Subject → Observer → 审批 → receive_hitl_response() → resolve_future()
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ghrah.abilities.context import AbilityExecutionContext
from ghrah.abilities.hook_context import HookContext
from ghrah.abilities.hook_runner import HookRunner
from ghrah.abilities.hook_store import HookStore
from ghrah.abilities.hooks import HookPoint, HookResult
from ghrah.abilities.invocation import AbilityInvocation
from ghrah.abilities.paths import ABILITY_PATH_SPECS
from ghrah.chat.content import ToolCallBlock
from ghrah.core.ability_protocol import AbilityProtocol
from ghrah.core.event_publisher import EventPublisher
from ghrah.core.events import HITLRequestEvent, HITLResolvedEvent
from ghrah.core.hitl import HITLFutureStore, HITLPromiseRegistry, HITLResult
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.abilities.hooks import Hook
    from ghrah.context.manager import ContextManager

logger = logging.getLogger(__name__)

__all__ = [
    "AbilityExecutor",
    "HitlOutcome",
    "HITLWaitResult",
    "LocalAbilityExecutor",
]

# HITL 审批终态：批准 / 人工拒绝 / 等待超时
HitlOutcome = Literal["approved", "rejected", "timeout"]


@dataclass(frozen=True)
class HITLWaitResult:
    """HITL 等待终态回执（携带归因字段）。

    agent 侧错误自足的最小信息面：outcome 之外附 promise_id / tool_call_id
    （并行工具调用各自超时时可归因到具体调用），rejected 时携带审批人
    拒绝理由（HITLResult.result 中 unit 组装的 {"reason": ...}）。
    timeout 且熔断降级生效时 degraded=True（等待已缩短，错误信息引导
    agent 收敛）。
    """

    outcome: HitlOutcome
    promise_id: str = ""
    tool_call_id: str = ""
    reason: Any = None
    degraded: bool = False
    degraded_timeout: float = 0.0


class AbilityExecutor(ABC):
    """Ability 执行器接口 — 将执行与 Agent 循环解耦。

    默认实现为 LocalAbilityExecutor（在 Core 端直接执行 Ability + HITL）。

    子类必须实现：
    - execute_ability(): 执行单个 Ability
    - execute_tool_calls(): 执行一组 tool_calls
    - handle_hitl_hook_result(): 处理 HITL Hook 结果
    - update_hooks(): 同步 Hook 列表
    - update_event_publisher(): 同步事件发布器
    - receive_hitl_response(): 接收 HITL 审批结果
    """

    @abstractmethod
    async def execute_ability(
        self,
        ability: AbilityProtocol,
        context: AbilityExecutionContext,
    ) -> ActionResult:
        """执行单个 Ability。

        Args:
            ability: 要执行的 Ability 实例
            context: 执行上下文

        Returns:
            执行结果
        """
        ...

    @abstractmethod
    async def execute_tool_calls(
        self,
        tool_calls: list[ToolCallBlock],
        abilities: dict[str, AbilityProtocol],
        accumulated_data: dict[str, Any],
        context_manager: ContextManager,
        last_action_result: ActionResult | None = None,
    ) -> list[dict]:
        """执行一组 tool_calls，返回结果列表。

        每个 tool_call 映射到对应的 Ability 执行。

        Args:
            tool_calls: LLM 返回的 ToolCallBlock 列表
            abilities: 已注册的 Ability 字典
            accumulated_data: 累积数据
            context_manager: 上下文管理器
            last_action_result: 上一次 action 的结果（由 ActorAgent 从 IterationState 显式传入）

        Returns:
            结果列表，每个 dict 包含 "ability_name", "action_result", "tool_call_id"
        """
        ...

    @abstractmethod
    async def handle_hitl_hook_result(
        self,
        hook_result: HookResult,
        context: AbilityExecutionContext,
    ) -> HITLWaitResult:
        """处理 HITL Hook 结果。

        当 PRE_EXECUTE Hook 返回 should_continue=False 且 requires_hitl=True 时，
        执行器需要决定如何处理：单体模式创建 Future 等待审批，分布式模式委托给 Subject。

        Args:
            hook_result: Hook 返回的结果
            context: 当前执行上下文

        Returns:
            审批终态回执：outcome（"approved" / "rejected" / "timeout"）
            附 promise_id / tool_call_id / 拒绝理由（归因字段）
        """
        ...

    @abstractmethod
    def update_hooks(self, hooks: list[Hook]) -> None:
        """同步更新 Hook 列表。

        当 Agent 注册/反注册 Ability 时，需要同步更新执行器的 Hook 列表。

        Args:
            hooks: 最新的 Hook 列表
        """
        ...

    @abstractmethod
    def update_event_publisher(self, publisher: EventPublisher) -> None:
        """同步更新事件发布器。

        当 Agent 的事件发布器变更时，需要同步更新执行器的事件发布器。

        Args:
            publisher: 新的事件发布器
        """
        ...

    @abstractmethod
    def receive_hitl_response(
        self,
        ability_name: str,
        tool_call_id: str,
        approved: bool,
        result: Any = None,
    ) -> bool:
        """接收 HITL 审批结果。

        当 Observer 审批 HITL 请求后，通过此方法 resolve 对应的 Future。
        由 LocalAbilityExecutor 处理。

        Args:
            ability_name: Ability 名称
            tool_call_id: 工具调用 ID
            approved: 是否批准
            result: 审批附加结果

        Returns:
            是否成功解析 Future
        """
        ...


class LocalAbilityExecutor(AbilityExecutor):
    """本地执行器 — 在 Core 端直接执行 Ability。

    保留当前所有行为：
    - PRE_EXECUTE / POST_EXECUTE Hook
    - HITL 审批（通过 HITLFutureStore）
    - 本地 I/O 操作

    用于单体模式（ghrah-core 独立使用）。
    """

    def __init__(
        self,
        agent_name: str,
        hooks: list[Hook] | None = None,
        hook_runner: HookRunner | None = None,
        event_publisher: EventPublisher | None = None,
        hitl_timeout: float = 300.0,
        workspace_root: str | None = None,
        hitl_promise_registry: HITLPromiseRegistry | None = None,
        supervisor: Any = None,
        hitl_consecutive_timeout_limit: int = 0,
        hitl_degraded_timeout: float = 30.0,
    ) -> None:
        """初始化本地执行器。

        Args:
            agent_name: Agent 名称（用于 HITL Future 的 key）
            hooks: 已注册的 Hook 列表
            hook_runner: 可选共享 HookRunner（由 ActorAgent 注入）
            event_publisher: 事件发布器（默认 NullEventPublisher）
            hitl_timeout: HITL 等待超时时间（秒）
            workspace_root: 工作区根目录，用于将相对路径解析到沙盒内（None 表示不解析）
            hitl_promise_registry: HITL promise 注册表（CoreUnit 注入，unit 命令面
                按 promise_id 翻译回三元组；None 时 standalone 模式跳过注册）
            supervisor: Supervisor 引用（tool-call 路径注入集群上下文用；
                None 时集群类 ability 在 LLM 工具调用下按 standalone 报错指路）
            hitl_consecutive_timeout_limit: 连续超时熔断阈值（连续 N 次超时后
                后续等待降级为 hitl_degraded_timeout；0 = 禁用）
            hitl_degraded_timeout: 熔断降级后的 HITL 等待秒数
        """
        self._agent_name = agent_name
        self._deferred_contexts: dict[str, AbilityExecutionContext] = {}
        self._hook_store = HookStore()
        if hooks:
            self._hook_store.add_hooks(HookStore.AGENT_OWNER, hooks)
        self._hook_runner = hook_runner or HookRunner(self._hook_store)
        if event_publisher is not None:
            self._event_publisher: EventPublisher = event_publisher
        else:
            from ghrah.core.event_publisher import NullEventPublisher

            self._event_publisher = NullEventPublisher()
        self._hitl_store = HITLFutureStore()
        self._hitl_timeout = hitl_timeout
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._hitl_promise_registry = hitl_promise_registry
        self._supervisor = supervisor
        # 连续超时熔断（per-agent，零隐式默认禁用）：审批人不在场时避免
        # 每次 300s 空等；任何 approve/reject 即复位（不硬快速失败，
        # 避免"离线误判 + 无法解除"的解除信号死锁）
        self._hitl_timeout_limit = hitl_consecutive_timeout_limit
        self._hitl_degraded_timeout = hitl_degraded_timeout
        self._consecutive_hitl_timeouts = 0

    @property
    def hitl_store(self) -> HITLFutureStore:
        """获取 HITL Future 存储（供外部 resolve 使用）。"""
        return self._hitl_store

    @property
    def hitl_promise_registry(self) -> HITLPromiseRegistry | None:
        """获取 HITL promise 注册表（无注入时为 None，standalone 兼容）。"""
        return self._hitl_promise_registry

    def update_hitl_promise_registry(self, registry: HITLPromiseRegistry) -> None:
        """同步更新 HITL promise 注册表（CoreUnit spawn 后 best-effort 注入）。"""
        self._hitl_promise_registry = registry

    def update_hooks(self, hooks: list[Hook]) -> None:
        """兼容旧接口：替换本地私有 HookStore 中的 hooks。

        Args:
            hooks: 最新的 Hook 列表
        """
        self._hook_store.remove_owner(HookStore.AGENT_OWNER)
        self._hook_store.add_hooks(HookStore.AGENT_OWNER, hooks)

    def update_hook_runner(self, hook_runner: HookRunner) -> None:
        """Use the agent-level HookRunner after ActorAgent wires shared storage."""

        self._hook_runner = hook_runner

    def update_event_publisher(self, publisher: EventPublisher) -> None:
        """同步更新事件发布器。

        Args:
            publisher: 新的事件发布器
        """
        self._event_publisher = publisher

    async def execute_ability(
        self,
        ability: AbilityProtocol,
        context: AbilityExecutionContext,
    ) -> ActionResult:
        """执行单个 Ability，包含 PRE_EXECUTE 和 POST_EXECUTE Hook。

        如果 PRE_EXECUTE Hook 返回 should_continue=False 且包含 HITL 请求，
        会创建 Future 等待审批结果。

        Args:
            ability: 要执行的 Ability 实例
            context: 执行上下文

        Returns:
            执行结果

        Raises:
            HookError: Hook 执行出错时
        """
        # PRE_EXECUTE hook（ability 级）
        hook_result = await self._hook_runner.run(
            HookContext.from_ability_context(HookPoint.PRE_EXECUTE, context)
        )

        # 处理 Hook 结果
        if hook_result is not None:
            if not hook_result.should_continue:
                if hook_result.requires_hitl:
                    # 需要 HITL 审批：创建 Future 等待人工审批
                    hitl_wait = await self.handle_hitl_hook_result(hook_result, context)
                    if hitl_wait.outcome != "approved":
                        # 人工拒绝或等待超时——错误信息区分两者，超时不得
                        # 折算成"用户拒绝"信号进 LLM 上下文
                        attribution = self._hitl_attribution(hitl_wait, context)
                        if hitl_wait.outcome == "timeout":
                            if hitl_wait.degraded:
                                error_msg = (
                                    f"HITL approval timed out after "
                                    f"{hitl_wait.degraded_timeout}s (degraded wait; approval "
                                    f"channel likely offline after {self._hitl_timeout_limit}+ "
                                    f"consecutive timeouts); {attribution}; consider wrapping "
                                    f"up and calling end_task instead of requesting more "
                                    f"approvals"
                                )
                            else:
                                error_msg = (
                                    f"HITL approval timed out after {self._hitl_timeout}s "
                                    f"with no response delivered; {attribution}"
                                )
                            return ActionResult(
                                outcome=ActionOutcome.FAILURE,
                                data={
                                    "error": error_msg,
                                    "hitl_timeout": True,
                                    "hitl_degraded": hitl_wait.degraded,
                                    "promise_id": hitl_wait.promise_id,
                                    "tool_call_id": hitl_wait.tool_call_id,
                                },
                            )
                        # 人工拒绝：优先透传审批人填写的理由（unit 组装进
                        # HITLResult.result），无理由时退回 hook 拦截原因
                        reason = self._extract_reject_reason(hitl_wait.reason)
                        error_msg = (
                            f"HITL request rejected by human: {reason}"
                            if reason
                            else (hook_result.message or "Execution blocked by HITL")
                        )
                        return ActionResult(
                            outcome=ActionOutcome.FAILURE,
                            data={
                                "error": error_msg,
                                "hitl_rejected": True,
                                "promise_id": hitl_wait.promise_id,
                                "tool_call_id": hitl_wait.tool_call_id,
                            },
                        )
                    # HITL 批准，继续执行
                else:
                    # 直接拦截，不进入 HITL 等待流程
                    return ActionResult(
                        outcome=ActionOutcome.FAILURE,
                        data={
                            "error": hook_result.message or "Execution blocked by hook",
                        },
                    )
            elif hook_result.route_to:
                # 路由到其他 ability
                return ActionResult(
                    outcome=ActionOutcome.DELEGATE,
                    data={"route_to": hook_result.route_to},
                    next_action_hint=hook_result.route_to,
                )
            elif hook_result.modified_context:
                # 合并修改的上下文
                context.accumulated_data.update(hook_result.modified_context)

        # 执行 Ability
        action_result = await ability.execute(context)

        pending = action_result.data.get("await_reply")
        if pending:
            pending["tool_args"] = dict(context.tool_args)
            self._deferred_contexts[pending["message_id"]] = context
            return action_result

        # POST_EXECUTE hook（ability 级）
        await self._hook_runner.run(
            HookContext.from_ability_context(HookPoint.POST_EXECUTE, context, action_result)
        )

        return action_result

    async def finish_deferred(
        self, message_id: str, context: AbilityExecutionContext, result: ActionResult
    ) -> None:
        """持久同步回复确定后执行原 POST_EXECUTE，恢复时不重跑 PRE/工具。"""
        original = self._deferred_contexts.pop(message_id, context)
        original.agent_state = context.agent_state
        await self._hook_runner.run(
            HookContext.from_ability_context(HookPoint.POST_EXECUTE, original, result)
        )

    def clear_deferred(self) -> None:
        """接收作用域结束时释放未完成 Hook 上下文，持久检查点仍可恢复。"""
        self._deferred_contexts.clear()

    async def execute_tool_calls(
        self,
        tool_calls: list[ToolCallBlock],
        abilities: dict[str, AbilityProtocol],
        accumulated_data: dict[str, Any],
        context_manager: ContextManager,
        last_action_result: ActionResult | None = None,
    ) -> list[dict]:
        """执行一组 tool_calls，返回结果列表。

        多个 tool_calls 使用 asyncio.gather 并行执行。
        子任务的取消信号（CancelledError 等 BaseException）原样传播，
        不折算为结果或普通错误。

        Args:
            tool_calls: LLM 返回的 ToolCallBlock 列表
            abilities: 已注册的 Ability 字典
            accumulated_data: 累积数据
            context_manager: 上下文管理器
            last_action_result: 上一次 action 的结果（由 ActorAgent 从 IterationState 显式传入）

        Returns:
            结果列表，每个 dict 包含 "ability_name", "action_result", "tool_call_id"
        """
        results: list[dict] = []
        tool_call_tasks: list[tuple[AbilityProtocol, dict[str, Any], str]] = []
        task_ability_names: list[str] = []

        for tc in tool_calls:
            ability_name = tc.name
            ability_args = tc.arguments
            tool_call_id = tc.id

            ability = abilities.get(ability_name)
            if ability is None:
                logger.debug(
                    "AbilityExecutor: unknown ability '%s' for tool_call %s → recorded as failure",
                    ability_name,
                    tool_call_id,
                )
                # 未知 ability，直接记录失败结果
                results.append(
                    {
                        "ability_name": ability_name,
                        "action_result": ActionResult(
                            outcome=ActionOutcome.FAILURE,
                            data={"error": f"Unknown ability: {ability_name}"},
                        ),
                        "tool_call_id": tool_call_id,
                    }
                )
                continue

            tool_call_tasks.append((ability, ability_args, tool_call_id))
            task_ability_names.append(ability_name)

        if tool_call_tasks:
            logger.debug(
                "AbilityExecutor: executing %d tool_call(s) in parallel: %s",
                len(tool_call_tasks),
                ", ".join(f"{a.name}({str(args)[:80]})" for a, args, _ in tool_call_tasks),
            )
            # 为每个 ability 创建独立的 AbilityExecutionContext
            raw_results = await asyncio.gather(
                *[
                    self._execute_single_with_context(
                        ability,
                        args,
                        accumulated_data,
                        context_manager,
                        last_action_result,
                        tool_call_id=_tc_id,
                    )
                    for ability, args, _tc_id in tool_call_tasks
                ],
                return_exceptions=True,
            )
            for i, r in enumerate(raw_results):
                if isinstance(r, BaseException) and not isinstance(r, Exception):
                    # 取消等控制流信号不是动作结果：原样重抛，交上层取消
                    # 路径处理——否则下方 r["tool_call_id"] 将抛 TypeError，
                    # 取消以错误形状崩溃
                    raise r
                if isinstance(r, Exception):
                    results.append(
                        {
                            "ability_name": task_ability_names[i],
                            "action_result": ActionResult(
                                outcome=ActionOutcome.FAILURE,
                                data={"error": str(r)},
                            ),
                            "tool_call_id": tool_call_tasks[i][2],
                        }
                    )
                else:
                    r["tool_call_id"] = tool_call_tasks[i][2]
                    results.append(r)

        for entry in results:
            result = entry.get("action_result")
            logger.debug(
                "AbilityExecutor: '%s' -> %s",
                entry.get("ability_name", "?"),
                getattr(getattr(result, "outcome", None), "value", "?"),
            )
        return results

    async def _execute_single_with_context(
        self,
        ability: AbilityProtocol,
        tool_args: dict[str, Any],
        accumulated_data: dict[str, Any],
        context_manager: ContextManager,
        last_action_result: ActionResult | None = None,
        *,
        tool_call_id: str = "",
    ) -> dict:
        """执行单个 ability（用于并行执行），创建独立的 AbilityExecutionContext。

        Args:
            ability: 要执行的 ability 实例
            tool_args: 工具调用参数
            accumulated_data: 累积数据
            context_manager: 上下文管理器
            last_action_result: 上一次 action 的结果（由 ActorAgent 从 IterationState 显式传入）

        Returns:
            dict 包含 "ability_name" 和 "action_result"
        """
        resolved_args = self._resolve_paths(tool_args, ability.name)

        # 集群服务注入：与 ActorAgent._build_ability_context 对齐——tool-call
        # 路径此前不注入 supervisor/manifest_store，LLM 工具调用集群类 ability
        # （query_agents 等）拿到的 supervisor 恒为 None，误报 standalone。
        supervisor = getattr(self, "_supervisor", None)
        manifest_store = getattr(supervisor, "manifest_store", None)
        skill_provider = getattr(supervisor, "skill_provider", None)

        per_ability_context = AbilityExecutionContext(
            invocation=AbilityInvocation(tool_call_id=tool_call_id),
            current_ability_name=ability.name,
            tool_args=resolved_args,
            agent_state=context_manager.get_current_state(),
            context_manager=context_manager,
            supervisor=supervisor,
            manifest_store=manifest_store,
            skill_provider=skill_provider,
            accumulated_data={
                **copy.deepcopy(accumulated_data),
                "tool_args": resolved_args,
            },
            last_action_result=last_action_result,
            agent_name=self._agent_name,
        )

        action_result = await self.execute_ability(ability, per_ability_context)

        return {"ability_name": ability.name, "action_result": action_result}

    def _resolve_paths(self, tool_args: dict[str, Any], ability_name: str) -> dict[str, Any]:
        """将 tool_args 中的相对路径解析到 workspace_root 下。

        如果 workspace_root 未设置，直接返回原 tool_args。
        仅对 ABILITY_PATH_SPECS 中已知的路径参数进行解析，
        绝对路径保持不变。

        Args:
            tool_args: LLM 工具调用参数
            ability_name: Ability 名称

        Returns:
            解析后的 tool_args（副本）
        """
        if self._workspace_root is None:
            return tool_args

        spec = ABILITY_PATH_SPECS.get(ability_name)
        if spec is None:
            return tool_args

        resolved = dict(tool_args)
        for key in spec.path_keys:
            value = resolved.get(key)
            if value and isinstance(value, str) and not Path(value).is_absolute():
                resolved[key] = str((Path(self._workspace_root) / value).resolve())
        for key in spec.working_dir_keys:
            value = resolved.get(key)
            if value and isinstance(value, str) and not Path(value).is_absolute():
                resolved[key] = str((Path(self._workspace_root) / value).resolve())

        return resolved

    @staticmethod
    def _hitl_attribution(hitl_wait: HITLWaitResult, context: AbilityExecutionContext) -> str:
        """组装超时错误的归因片段（ability + 调用参数摘要 + promise/tool_call id）。

        并行工具调用各自超时时，LLM 凭参数摘要与 id 将报错归因到具体调用。
        摘要取 tool_args 中的 command / file_path / dir_path，无则退 ability 名。
        """
        args = context.tool_args or {}
        summary = (
            args.get("command") or args.get("file_path") or args.get("dir_path") or args.get("path")
        )
        if summary:
            target = f"{context.current_ability_name}({summary})"
        else:
            target = context.current_ability_name
        return (
            f"ability={target}, promise_id={hitl_wait.promise_id}, "
            f"tool_call_id={hitl_wait.tool_call_id}"
        )

    @staticmethod
    def _extract_reject_reason(reason: Any) -> str:
        """从 HITLResult.result 提取审批人拒绝理由（unit 组装的 {"reason": ...}）。"""
        if isinstance(reason, dict):
            value = reason.get("reason")
            return value.strip() if isinstance(value, str) else ""
        return reason.strip() if isinstance(reason, str) and reason.strip() else ""

    async def handle_hitl_hook_result(
        self,
        hook_result: HookResult,
        context: AbilityExecutionContext,
    ) -> HITLWaitResult:
        """处理 HITL Hook 结果。

        在单体模式下，创建 asyncio.Future 等待 HITL 审批：
        1. 生成 promise_id，发布 HITLRequestEvent（携带 promise_id）到 EventPublisher
        2. 创建 HITL Future（promise 登记到注册表）并等待
        3. 收到审批结果后继续或终止

        Args:
            hook_result: Hook 返回的结果
            context: 当前执行上下文

        Returns:
            审批终态回执：outcome 附 promise_id / tool_call_id / 拒绝理由（归因字段）
        """
        # 生成 tool_call_id 与 promise_id
        tool_call_id = context.tool_args.get("call_id", "") or uuid.uuid4().hex[:12]
        promise_id = uuid.uuid4().hex

        # 发布 HITL 请求事件（promise_id 随载荷下发，Observer 凭此回发审批；
        # reason 为触发审批的 Hook 拦截原因，审批人可见"为何要审"）
        await self._event_publisher.publish(
            HITLRequestEvent(
                agent_name=self._agent_name,
                ability_name=context.current_ability_name,
                tool_call=context.tool_args,
                context={"tool_call_id": tool_call_id},
                promise_id=promise_id,
                reason=hook_result.message or "",
            )
        )

        # 创建 Future 并等待
        future = self._hitl_store.create_future(
            agent_name=self._agent_name,
            ability_name=context.current_ability_name,
            tool_call_id=tool_call_id,
        )
        if self._hitl_promise_registry is not None:
            self._hitl_promise_registry.register(
                promise_id=promise_id,
                agent_name=self._agent_name,
                ability_name=context.current_ability_name,
                tool_call_id=tool_call_id,
            )

        # 熔断降级：连续超时达阈值后改用短等待（审批通道疑似离线）
        degraded = (
            self._hitl_timeout_limit > 0
            and self._consecutive_hitl_timeouts >= self._hitl_timeout_limit
        )
        wait_timeout = self._hitl_degraded_timeout if degraded else self._hitl_timeout

        # 等待 HITL 结果（带超时）
        try:
            hitl_result: HITLResult = await asyncio.wait_for(future, timeout=wait_timeout)
            outcome: HitlOutcome = "approved" if hitl_result.approved else "rejected"
            # 任何人工响应都证明审批通道在线——熔断计数全量复位
            self._consecutive_hitl_timeouts = 0
            if self._hitl_promise_registry is not None:
                self._hitl_promise_registry.remove(promise_id)
            logger.info(
                f"LocalAbilityExecutor[{self._agent_name}] HITL {outcome}: "
                f"ability={context.current_ability_name}, tool_call_id={tool_call_id}"
            )
            await self._event_publisher.publish(
                HITLResolvedEvent(
                    agent_name=self._agent_name,
                    promise_id=promise_id,
                    ability_name=context.current_ability_name,
                    tool_call_id=tool_call_id,
                    status=outcome,
                )
            )
            return HITLWaitResult(
                outcome=outcome,
                promise_id=promise_id,
                tool_call_id=tool_call_id,
                reason=hitl_result.result,
            )
        except TimeoutError:
            self._consecutive_hitl_timeouts += 1
            logger.warning(
                f"LocalAbilityExecutor[{self._agent_name}] HITL request timed out: "
                f"ability={context.current_ability_name}, tool_call_id={tool_call_id}, "
                f"consecutive={self._consecutive_hitl_timeouts}"
            )
            if self._hitl_promise_registry is not None:
                self._hitl_promise_registry.remove_triplet(
                    agent_name=self._agent_name,
                    ability_name=context.current_ability_name,
                    tool_call_id=tool_call_id,
                )
            await self._event_publisher.publish(
                HITLResolvedEvent(
                    agent_name=self._agent_name,
                    promise_id=promise_id,
                    ability_name=context.current_ability_name,
                    tool_call_id=tool_call_id,
                    status="timeout",
                )
            )
            return HITLWaitResult(
                outcome="timeout",
                promise_id=promise_id,
                tool_call_id=tool_call_id,
                degraded=degraded,
                degraded_timeout=self._hitl_degraded_timeout,
            )

    def receive_hitl_response(
        self,
        ability_name: str,
        tool_call_id: str,
        approved: bool,
        result: Any = None,
    ) -> bool:
        """接收 HITL 审批结果（由外部调用）。

        当 Observer 审批 HITL 请求后，通过此方法 resolve 对应的 Future。

        Args:
            ability_name: Ability 名称
            tool_call_id: 工具调用 ID
            approved: 是否批准
            result: 审批附加结果

        Returns:
            是否成功解析 Future
        """
        hitl_result = HITLResult(approved=approved, result=result)
        resolved = self._hitl_store.resolve_future(
            agent_name=self._agent_name,
            ability_name=ability_name,
            tool_call_id=tool_call_id,
            result=hitl_result,
        )
        if resolved:
            logger.info(
                f"LocalAbilityExecutor[{self._agent_name}] HITL response received: "
                f"ability={ability_name}, tool_call_id={tool_call_id}, approved={approved}"
            )
        else:
            logger.warning(
                f"LocalAbilityExecutor[{self._agent_name}] HITL response ignored "
                f"(no pending future): ability={ability_name}, tool_call_id={tool_call_id}"
            )
        return resolved
