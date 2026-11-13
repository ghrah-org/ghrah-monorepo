# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Session/Branch 域载荷模型（命令、事件与链历史查询）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ─── Session 命令载荷模型 ───


class SessionCreatePayload(BaseModel):
    """session_create 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    name: str | None = None
    origin_session_id: str | None = None
    origin_node_id: str | None = None
    system_prompt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionActivatePayload(BaseModel):
    """session_activate 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    session_id: str


class SessionListPayload(BaseModel):
    """session_list 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str


class SessionArchivePayload(BaseModel):
    """session_archive 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    session_id: str


class SessionDeletePayload(BaseModel):
    """session_delete 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    session_id: str


class BranchCreatePayload(BaseModel):
    """branch_create 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    session_id: str
    name: str
    from_node_id: str | None = None
    parent_branch_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BranchActivatePayload(BaseModel):
    """branch_activate 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    session_id: str
    branch_id: str


class BranchListPayload(BaseModel):
    """branch_list 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    session_id: str


class BranchArchivePayload(BranchActivatePayload):
    """branch_archive 命令载荷。"""


class BranchDeletePayload(BranchActivatePayload):
    """branch_delete 命令载荷。"""


class GetChainHistoryPayload(BaseModel):
    """get_chain_history 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    session_id: str
    branch_id: str
    limit: int = -1


class ChainHistoryResultPayload(BaseModel):
    """get_chain_history 命令的响应载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    session_id: str
    branch_id: str
    active_session_id: str = ""
    nodes: list[dict[str, Any]] = Field(default_factory=list)


# ─── Session 事件载荷模型 ───


class SessionInfoPayload(BaseModel):
    """Session 信息载荷，用于 session 事件和查询结果。"""

    project_id: str = ""
    agent_id: str = ""
    session_id: str
    agent_name: str
    name: str = "Session 1"
    root_node_id: str
    active_branch_id: str
    lifecycle: str = "open"
    system_prompt: str = ""
    origin_agent_name: str | None = None
    origin_session_id: str | None = None
    origin_node_id: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    message_count: int = 0
    iteration_count: int = 0


class SessionCreatedPayload(BaseModel):
    """session_created 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    session: SessionInfoPayload


class SessionActivatedPayload(BaseModel):
    """session_activated 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    session: SessionInfoPayload


class SessionArchivedPayload(BaseModel):
    """session_archived 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    session_id: str


class SessionDeletedPayload(BaseModel):
    """session_deleted 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    session_id: str


class SessionListResultPayload(BaseModel):
    """session_list_result 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    active_session_id: str = ""
    sessions: list[SessionInfoPayload]


class BranchInfoPayload(BaseModel):
    """Branch 信息载荷。"""

    branch_id: str
    session_id: str
    name: str
    head_node_id: str
    lifecycle: str = "open"
    parent_branch_id: str | None = None
    fork_point_node_id: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BranchEventPayload(BaseModel):
    """Branch 创建/激活事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    branch: BranchInfoPayload


class BranchLifecyclePayload(BaseModel):
    """Branch 归档/删除事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    session_id: str
    branch_id: str


class BranchListResultPayload(BaseModel):
    """branch_list_result 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    session_id: str
    active_branch_id: str = ""
    branches: list[BranchInfoPayload]
