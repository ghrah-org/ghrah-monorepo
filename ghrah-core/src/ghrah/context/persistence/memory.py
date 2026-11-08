# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""纯内存增量 checkpoint 后端。"""

from __future__ import annotations

import copy

from ghrah.context.action_session import ActionSession
from ghrah.context.branch import ActionBranch
from ghrah.context.node import ContextNode
from ghrah.context.persistence.backend import PersistenceBackend
from ghrah.context.persistence.changes import ContextChanges
from ghrah.context.persistence.checkpoint import (
    ContextCheckpoint,
    SnapshotPayloads,
    project_snapshot_payloads,
)

__all__ = ["InMemoryBackend"]


class InMemoryBackend(PersistenceBackend):
    """以独立实体索引保存 checkpoint，适用于测试和临时运行。"""

    def __init__(self) -> None:
        self._sessions: dict[str, ActionSession] = {}
        self._branches: dict[str, ActionBranch] = {}
        self._nodes: dict[str, ContextNode] = {}
        self._active_sessions: dict[str, str] = {}

    async def apply_changes(self, changes: ContextChanges) -> None:
        """在临时副本中应用增量变更后原子替换索引。"""
        sessions = dict(self._sessions)
        branches = dict(self._branches)
        nodes = dict(self._nodes)
        active_sessions = dict(self._active_sessions)

        for branch_id in changes.delete_branch_ids:
            branches.pop(branch_id, None)
        for session_id in changes.delete_session_ids:
            sessions.pop(session_id, None)
            branches = {
                key: branch for key, branch in branches.items() if branch.session_id != session_id
            }
            nodes = {key: node for key, node in nodes.items() if node.session_id != session_id}
        sessions.update({session.session_id: session for session in changes.sessions})
        branches.update({branch.branch_id: branch for branch in changes.branches})
        nodes.update({node.id: node for node in changes.nodes})
        if changes.active_session_id is not None:
            active_sessions[changes.agent_name] = changes.active_session_id

        self._sessions = sessions
        self._branches = branches
        self._nodes = nodes
        self._active_sessions = active_sessions

    async def load_checkpoint(
        self,
        agent_name: str,
        *,
        snapshot_payloads: SnapshotPayloads = "all",
    ) -> ContextCheckpoint | None:
        """从内存加载 Agent checkpoint，并返回深拷贝。"""
        active_session_id = self._active_sessions.get(agent_name)
        if active_session_id is None:
            return None
        sessions = tuple(
            session for session in self._sessions.values() if session.agent_name == agent_name
        )
        session_ids = {session.session_id for session in sessions}
        checkpoint = ContextCheckpoint(
            agent_name=agent_name,
            active_session_id=active_session_id,
            sessions=sessions,
            branches=tuple(
                branch for branch in self._branches.values() if branch.session_id in session_ids
            ),
            nodes=tuple(node for node in self._nodes.values() if node.session_id in session_ids),
        )
        return copy.deepcopy(project_snapshot_payloads(checkpoint, snapshot_payloads))

    async def delete_checkpoint(self, agent_name: str) -> None:
        """删除 Agent 的全部 Session、Branch、Node 和 active 指针。"""
        session_ids = {
            session.session_id
            for session in self._sessions.values()
            if session.agent_name == agent_name
        }
        self._sessions = {
            key: session
            for key, session in self._sessions.items()
            if session.session_id not in session_ids
        }
        self._branches = {
            key: branch
            for key, branch in self._branches.items()
            if branch.session_id not in session_ids
        }
        self._nodes = {
            key: node for key, node in self._nodes.items() if node.session_id not in session_ids
        }
        self._active_sessions.pop(agent_name, None)

    async def list_agents(self) -> list[str]:
        """列出所有存在 active checkpoint 的 Agent。"""
        return sorted(self._active_sessions)
