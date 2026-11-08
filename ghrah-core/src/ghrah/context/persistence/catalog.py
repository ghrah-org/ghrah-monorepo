# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ContextChainMeta：不携带节点载荷的链目录视图。"""

from __future__ import annotations

from dataclasses import dataclass

from ghrah.context.action_session import ActionSession
from ghrah.context.branch import ActionBranch

__all__ = ["ContextChainMeta"]


@dataclass(frozen=True)
class ContextChainMeta:
    """Agent 的 Session/Branch 目录；不包含任何链节点。

    与 :class:`~ghrah.context.persistence.checkpoint.ContextCheckpoint` 的区别：
    - 没有 ``nodes``，因此不需要（也不能）做链拓扑校验；
    - 只用于「查目录 / 判断存在性 / 取 active 指针」这类轻量读取，
      不会因历史节点数量而付出加载成本。
    """

    agent_name: str
    active_session_id: str
    sessions: tuple[ActionSession, ...]
    branches: tuple[ActionBranch, ...]

    def __post_init__(self) -> None:
        """校验 Agent 归属、ID 唯一性与 active 指针闭合。"""
        if not self.agent_name:
            raise ValueError("agent_name must not be empty")
        session_by_id = {session.session_id: session for session in self.sessions}
        if len(session_by_id) != len(self.sessions):
            raise ValueError("chain meta contains duplicate session IDs")
        if self.active_session_id not in session_by_id:
            raise ValueError("active session does not belong to the chain meta")
        if any(session.agent_name != self.agent_name for session in self.sessions):
            raise ValueError("chain meta contains a session owned by another agent")
        if len({branch.branch_id for branch in self.branches}) != len(self.branches):
            raise ValueError("chain meta contains duplicate branch IDs")
        if any(branch.session_id not in session_by_id for branch in self.branches):
            raise ValueError("chain meta contains an orphan branch")
