# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""rebase：跨 Agent 上下文继承工厂函数。

在 spawn agent 时使用，从源 Agent 的 chain 节点继承上下文，
创建新的独立 ContextManager。

设计要点：
  - 默认继承 messages（对话历史），不继承 state（工作记忆）
  - 可选继承 state（inherit_state=True）
  - 不继承 system_prompt（必须显式指定）
  - 返回的 CM 与源 CM 共享同一 persistence backend
  - 根节点和根 session 记录 rebase 来源
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ghrah.context.manager import ContextManager
from ghrah.context.window import WindowManager


def create_rebased_context(
    source_cm: ContextManager,
    source_node_id: str | None = None,
    agent_name: str = "",
    system_prompt: str = "",
    inherit_messages: bool = True,
    inherit_state: bool = False,
    state_filter: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    snapshot_interval: int | None = None,
    window_manager: WindowManager | None = None,
) -> ContextManager:
    """从源 Agent 的 chain 节点创建 rebased ContextManager。

    这是 spawn agent 时的初始化函数。新 CM 包含源 Agent 在目标节点的上下文快照，
    默认继承对话历史但不继承内部状态。

    Args:
        source_cm: 源 Agent 的 ContextManager
        source_node_id: 源 chain 上的目标节点 ID（None 表示源 chain 的活跃 head）
        agent_name: 新 Agent 的名称
        system_prompt: 新 Agent 的系统提示词（必须显式指定，不继承源 Agent 的）
        inherit_messages: 是否继承源 Agent 的对话消息（默认 True）
        inherit_state: 是否继承源 Agent 的状态（默认 False）
        state_filter: 状态过滤函数，选择性继承状态（仅 inherit_state=True 时生效）
        snapshot_interval: 快照间隔（None 使用源 CM 的设置）
        window_manager: 新 Agent 的窗口管理器（可选，默认 None 不配置）。
            必须传入独立实例——直接共享源 CM 的实例会使摘要策略状态
            与压缩排水记录跨 Agent 互串

    Returns:
        新 Agent 的 ContextManager，与源 CM 共享同一 persistence backend，
        根节点和根 session 记录 rebase 来源信息

    Raises:
        ValueError: 源 chain 为空或指定节点不存在
    """
    # 1. 确定源节点
    if source_node_id is None:
        source_node = source_cm.active_head
        source_node_id = source_node.id
    else:
        source_node = source_cm.chain.checkout(source_node_id)
        if source_node is None:
            raise ValueError(f"Source node '{source_node_id}' not found.")

    # 2. 准备初始状态
    #    继承源 CM 的当前运行时状态（而非节点的快照状态），
    #    因为运行时状态可能包含未提交的改动。
    if inherit_state:
        initial_state = source_cm.get_current_state()
        if state_filter is not None:
            initial_state = state_filter(initial_state)
    else:
        initial_state = {}

    # 3. 准备初始消息
    #    inherit_messages=True 时继承源 Agent 的当前消息，但过滤掉 system message
    #    inherit_messages=False 时不继承消息（新 CM 只有自己的 system_prompt）
    source_messages: list[Any] = []
    if inherit_messages:
        source_messages = [
            m for m in source_cm.message_store.current_messages if not _is_system_message(m)
        ]

    # 4. 创建新 CM（与源 CM 共享同一 persistence backend）
    child_cm = ContextManager(
        agent_name=agent_name,
        initial_state=initial_state,
        snapshot_interval=snapshot_interval or source_cm.message_store.snapshot_interval,
        system_prompt=system_prompt,
        window_manager=window_manager,
        persistence=source_cm.persistence,
        auto_persist=source_cm.auto_persist,
        message_factory=source_cm.message_factory,
        initial_messages=source_messages,
        origin_session_id=source_cm.get_active_session().session_id,
        origin_node_id=source_node_id,
        origin_agent_name=source_cm.agent_name,
        root_metadata={
            "origin_agent_name": source_cm.agent_name,
            "origin_node_id": source_node_id,
        },
        compact_threshold=source_cm.compact_threshold,
        compact_keep_recent=source_cm.compact_keep_recent,
        compact_method=source_cm.compact_method,
        skill_provider=source_cm.skill_provider,
    )

    return child_cm


def _is_system_message(msg: Any) -> bool:
    """判断消息是否为 system message。"""
    role = getattr(msg, "role", None)
    return role == "system"
