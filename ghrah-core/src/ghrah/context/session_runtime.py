# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SessionRuntime：单 Root ActionSession 的内存聚合。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Self

from ghrah.context.action_session import ActionSession
from ghrah.context.branch import ActionBranch
from ghrah.context.chain import ActionChain
from ghrah.context.node import ContextNode
from ghrah.context.topology import validate_session_topology

__all__ = ["SessionRuntime"]


@dataclass
class SessionRuntime:
    """聚合一个 Session 的元数据、单 Root ActionChain 与 Branch 表。"""

    session: ActionSession
    chain: ActionChain
    branches: dict[str, ActionBranch]

    def __post_init__(self) -> None:
        """在聚合边界校验 Session 拓扑。"""
        self.branches = dict(self.branches)
        self.validate()

    @classmethod
    def create(
        cls,
        *,
        agent_name: str,
        name: str = "Session 1",
        system_prompt: str = "",
        agent_state: dict[str, Any] | None = None,
        messages: list[Any] | None = None,
        session_id: str | None = None,
        branch_id: str | None = None,
        branch_name: str = "main",
        origin_agent_name: str | None = None,
        origin_session_id: str | None = None,
        origin_node_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        root_metadata: dict[str, Any] | None = None,
    ) -> Self:
        """创建独立 Session，同时产生 Root 和默认 Branch。"""
        effective_session_id = session_id or ActionSession.new_id()
        effective_branch_id = branch_id or ActionBranch.new_id()
        chain = ActionChain(agent_name=agent_name, session_id=effective_session_id)
        root = chain.init_chain(
            agent_state=agent_state,
            messages=messages,
            branch_id=effective_branch_id,
            metadata=root_metadata,
        )
        branch = ActionBranch.create_root(
            session_id=effective_session_id,
            head_node_id=root.id,
            name=branch_name,
            branch_id=effective_branch_id,
        )
        session = ActionSession.create(
            agent_name=agent_name,
            name=name,
            root_node_id=root.id,
            active_branch_id=effective_branch_id,
            session_id=effective_session_id,
            system_prompt=system_prompt,
            origin_agent_name=origin_agent_name,
            origin_session_id=origin_session_id,
            origin_node_id=origin_node_id,
            metadata=metadata,
        )
        return cls(session=session, chain=chain, branches={branch.branch_id: branch})

    @classmethod
    def restore(
        cls,
        *,
        session: ActionSession,
        branches: list[ActionBranch],
        nodes: list[ContextNode],
    ) -> Self:
        """从持久化数据恢复单 Session 运行时。"""
        chain = ActionChain(agent_name=session.agent_name, session_id=session.session_id)
        for node in nodes:
            chain.ingest_node(node)
        return cls(
            session=session,
            chain=chain,
            branches={branch.branch_id: branch for branch in branches},
        )

    @property
    def active_branch(self) -> ActionBranch:
        """当前运行 Branch。"""
        return self.branches[self.session.active_branch_id]

    @property
    def active_head(self) -> ContextNode:
        """当前 Branch 的 Head 节点。"""
        node = self.chain.checkout(self.active_branch.head_node_id)
        if node is None:  # pragma: no cover - validate() 已保证该不变量
            raise RuntimeError("active branch head is missing")
        return node

    def get_branch(self, branch_id: str) -> ActionBranch:
        """按稳定 ID 获取 Branch。"""
        try:
            return self.branches[branch_id]
        except KeyError as exc:
            raise ValueError(f"Branch '{branch_id}' does not exist.") from exc

    def create_branch(
        self,
        *,
        name: str,
        parent_branch_id: str | None = None,
        fork_point_node_id: str | None = None,
        branch_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActionBranch:
        """创建指向已有节点的 Branch，不隐式激活。"""
        effective_parent_id = parent_branch_id or self.session.active_branch_id
        parent_branch = self.get_branch(effective_parent_id)
        effective_fork_point = fork_point_node_id or parent_branch.head_node_id
        if self.chain.checkout(effective_fork_point) is None:
            raise ValueError(f"Node '{effective_fork_point}' not found.")

        branch = ActionBranch.create_fork(
            session_id=self.session.session_id,
            name=name,
            head_node_id=effective_fork_point,
            parent_branch_id=parent_branch.branch_id,
            fork_point_node_id=effective_fork_point,
            branch_id=branch_id,
            metadata=metadata,
        )
        if branch.branch_id in self.branches:
            raise ValueError(f"Branch '{branch.branch_id}' already exists.")
        self.branches[branch.branch_id] = branch
        self.validate()
        return branch

    def activate_branch(self, branch_id: str) -> None:
        """显式激活 Session 内的 Branch。"""
        self.get_branch(branch_id)
        self.session = replace(self.session, active_branch_id=branch_id)

    def commit_node(
        self,
        *,
        branch_id: str | None = None,
        ability_names: list[str] | None = None,
        agent_state: dict[str, Any] | None = None,
        messages_delta: list[Any] | None = None,
        messages_snapshot: list[Any] | None = None,
        is_snapshot: bool = False,
        action_results: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        turn_id: str = "",
    ) -> ContextNode:
        """向指定 Branch 追加节点并原子前移其 Head。"""
        effective_branch_id = branch_id or self.session.active_branch_id
        branch = self.get_branch(effective_branch_id)
        node = self.chain.append_node(
            parent_id=branch.head_node_id,
            branch_id=branch.branch_id,
            ability_names=ability_names,
            agent_state=agent_state,
            messages_delta=messages_delta,
            messages_snapshot=messages_snapshot,
            is_snapshot=is_snapshot,
            action_results=action_results,
            metadata=metadata,
            turn_id=turn_id,
        )
        self.branches[branch.branch_id] = replace(branch, head_node_id=node.id)
        self.validate()
        return node

    def get_history(self, branch_id: str | None = None, limit: int = -1) -> list[ContextNode]:
        """返回指定 Branch Head 的祖先历史。"""
        branch = self.get_branch(branch_id or self.session.active_branch_id)
        return self.chain.get_history_from(branch.head_node_id, limit=limit)

    def validate(self) -> None:
        """校验聚合的单 Root 与 Session 所有权不变量。"""
        if self.chain.session_id != self.session.session_id:
            raise ValueError("ActionChain belongs to another session")
        if self.chain.agent_name != self.session.agent_name:
            raise ValueError("ActionChain belongs to another agent")
        validate_session_topology(self.session, self.branches.values(), self.chain.nodes)
