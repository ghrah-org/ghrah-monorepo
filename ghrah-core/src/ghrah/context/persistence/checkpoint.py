# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ContextCheckpoint：Agent 多 Session 持久化快照。"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Literal

from ghrah.context.action_session import ActionSession
from ghrah.context.branch import ActionBranch
from ghrah.context.node import ContextNode
from ghrah.context.session_runtime import SessionRuntime

__all__ = ["ContextCheckpoint", "SnapshotPayloads", "project_snapshot_payloads"]

SnapshotPayloads = Literal["all", "boundaries", "none"]


@dataclass(frozen=True)
class ContextCheckpoint:
    """Agent 的完整拓扑快照；运行 state/messages 由 Head 派生。"""

    agent_name: str
    active_session_id: str
    sessions: tuple[ActionSession, ...]
    branches: tuple[ActionBranch, ...]
    nodes: tuple[ContextNode, ...]

    def __post_init__(self) -> None:
        """校验全局 ID 唯一性与每个 Session 的拓扑闭包。"""
        if not self.agent_name:
            raise ValueError("agent_name must not be empty")
        session_by_id = {session.session_id: session for session in self.sessions}
        if len(session_by_id) != len(self.sessions):
            raise ValueError("checkpoint contains duplicate session IDs")
        if self.active_session_id not in session_by_id:
            raise ValueError("active session does not belong to the checkpoint")
        if any(session.agent_name != self.agent_name for session in self.sessions):
            raise ValueError("checkpoint contains a session owned by another agent")
        if len({branch.branch_id for branch in self.branches}) != len(self.branches):
            raise ValueError("checkpoint contains duplicate branch IDs")
        if len({node.id for node in self.nodes}) != len(self.nodes):
            raise ValueError("checkpoint contains duplicate node IDs")

        for session in self.sessions:
            session_branches = [
                branch for branch in self.branches if branch.session_id == session.session_id
            ]
            session_nodes = [node for node in self.nodes if node.session_id == session.session_id]
            SessionRuntime.restore(
                session=session,
                branches=session_branches,
                nodes=session_nodes,
            )

        known_session_ids = set(session_by_id)
        if any(branch.session_id not in known_session_ids for branch in self.branches):
            raise ValueError("checkpoint contains an orphan branch")
        if any(node.session_id not in known_session_ids for node in self.nodes):
            raise ValueError("checkpoint contains an orphan node")


def _is_snapshot_boundary(node: ContextNode) -> bool:
    """判断节点是否为可独立回放的语义边界（root 或 compact）。"""
    return node.parent_id is None or node.metadata.get("node_kind") == "compact"


def project_snapshot_payloads(
    checkpoint: ContextCheckpoint, policy: SnapshotPayloads
) -> ContextCheckpoint:
    """按策略剥离快照载荷，返回新的 checkpoint（不修改入参）。

    仅用于内存型后端（``memory`` / ``json_file``）；SQLite 后端在 SQL
    层完成同样的投影，避免反序列化整列数据。
    """
    if policy == "all":
        return checkpoint

    def _project(node: ContextNode) -> ContextNode:
        if node.messages_snapshot is None:
            return node
        keep = policy == "boundaries" and _is_snapshot_boundary(node)
        return dataclasses.replace(node, messages_snapshot=node.messages_snapshot if keep else None)

    return dataclasses.replace(
        checkpoint,
        nodes=tuple(_project(node) for node in checkpoint.nodes),
    )
