# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ActionChain：一个 ActionSession 内的不可变节点索引。"""

from __future__ import annotations

from typing import Any

from ghrah.context.node import ContextNode

__all__ = ["ActionChain"]


class ActionChain:
    """管理单一 Session Root 下的不可变 ContextNode DAG。

    Branch Head 不属于本对象；它由 :class:`SessionRuntime` 中稳定的
    ``ActionBranch`` 引用维护，避免节点索引和 Branch 表形成双重真相。
    """

    def __init__(self, agent_name: str, session_id: str) -> None:
        if not agent_name:
            raise ValueError("agent_name must not be empty")
        if not session_id:
            raise ValueError("session_id must not be empty")
        self._agent_name = agent_name
        self._session_id = session_id
        self._nodes: dict[str, ContextNode] = {}

    @property
    def agent_name(self) -> str:
        """所属 Agent 名称。"""
        return self._agent_name

    @property
    def session_id(self) -> str:
        """所属 ActionSession 的稳定 ID。"""
        return self._session_id

    @property
    def nodes(self) -> list[ContextNode]:
        """返回链内全部节点。"""
        return list(self._nodes.values())

    @property
    def node_count(self) -> int:
        """节点总数。"""
        return len(self._nodes)

    def init_chain(
        self,
        agent_state: dict[str, Any] | None = None,
        messages: list[Any] | None = None,
        *,
        branch_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ContextNode:
        """创建唯一 Root。"""
        if self._nodes:
            raise ValueError("Chain already initialized.")
        root = ContextNode.create_root(
            agent_name=self._agent_name,
            agent_state=agent_state,
            messages=messages,
            session_id=self._session_id,
            created_on_branch_id=branch_id,
            metadata=metadata,
        )
        self._nodes[root.id] = root
        return root

    def append_node(
        self,
        *,
        parent_id: str,
        branch_id: str,
        ability_names: list[str] | None = None,
        agent_state: dict[str, Any] | None = None,
        messages_delta: list[Any] | None = None,
        messages_snapshot: list[Any] | None = None,
        is_snapshot: bool = False,
        action_results: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        turn_id: str = "",
    ) -> ContextNode:
        """在指定父节点后追加节点，不修改任何 Branch Head。"""
        if not branch_id:
            raise ValueError("branch_id must not be empty")
        parent = self._nodes.get(parent_id)
        if parent is None:
            raise ValueError(f"Node '{parent_id}' not found.")
        if parent.session_id != self._session_id:
            raise ValueError("parent node belongs to another session")
        node = ContextNode(
            parent_id=parent.id,
            agent_name=self._agent_name,
            iteration=parent.iteration + 1,
            ability_names=ability_names or [],
            agent_state=agent_state or {},
            messages_delta=messages_delta or [],
            messages_snapshot=messages_snapshot,
            is_snapshot=is_snapshot,
            action_results=action_results or [],
            metadata=metadata or {},
            session_id=self._session_id,
            created_on_branch_id=branch_id,
            turn_id=turn_id,
        )
        self._nodes[node.id] = node
        return node

    def get_history_from(self, head_node_id: str, limit: int = -1) -> list[ContextNode]:
        """从指定 Head 回溯到本 Session 的 Root。"""
        head = self._nodes.get(head_node_id)
        if head is None:
            raise ValueError(f"Node '{head_node_id}' not found.")
        history: list[ContextNode] = []
        current: ContextNode | None = head
        visited: set[str] = set()
        while current is not None:
            if current.id in visited:
                raise ValueError("cycle detected in ActionChain")
            visited.add(current.id)
            if current.session_id != self._session_id:
                raise ValueError("node belongs to another session")
            history.append(current)
            current = self._nodes.get(current.parent_id) if current.parent_id else None
        history.reverse()
        return history[-limit:] if limit > 0 else history

    def checkout(self, node_id: str) -> ContextNode | None:
        """按稳定 ID 获取节点。"""
        return self._nodes.get(node_id)

    def ingest_node(self, node: ContextNode) -> None:
        """恢复时接纳已构造节点，并验证所有权。"""
        if node.agent_name != self._agent_name or node.session_id != self._session_id:
            raise ValueError("node belongs to another session or agent")
        if node.id in self._nodes:
            raise ValueError(f"Node '{node.id}' already exists.")
        self._nodes[node.id] = node
