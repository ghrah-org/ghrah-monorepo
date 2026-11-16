# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Workspace 域载荷模型（管理命令 + 事件）。"""

from __future__ import annotations

from pydantic import BaseModel

# ─── Workspace 管理载荷模型 ───


class CreateWorkspacePayload(BaseModel):
    """create_workspace 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str


class DestroyWorkspacePayload(BaseModel):
    """destroy_workspace 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str


class WorkspaceSnapshotPayload(BaseModel):
    """workspace_snapshot 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    message: str = ""


class WorkspaceRollbackPayload(BaseModel):
    """workspace_rollback 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    snapshot_id: str


class WorkspaceDiffPayload(BaseModel):
    """workspace_diff 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str
    snapshot_id: str | None = None


class WorkspaceStatusPayload(BaseModel):
    """workspace_status 命令载荷。"""

    project_id: str
    agent_id: str
    agent_name: str


class WorkspaceRegisterPayload(BaseModel):
    """workspace_register 命令载荷（把已有目录登记为 workspace）。

    provider_type 为 None 时由 Subject 按 registry.detect 探测规则分派
    （有 .git → git；否则 → plain）。locator 为 file:// URI（MVP）。
    """

    locator: str
    name: str = ""
    provider_type: str | None = None


class WorkspaceGetPayload(BaseModel):
    """workspace_get 命令载荷（workspace_id 键控）。"""

    workspace_id: str


class WorkspaceListPayload(BaseModel):
    """workspace_list 命令载荷（可选 provider_type 过滤）。"""

    provider_type: str | None = None  # ─── Workspace 事件载荷模型 ───


class WorkspaceCreatedPayload(BaseModel):
    """workspace_created 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    path: str


class WorkspaceDestroyedPayload(BaseModel):
    """workspace_destroyed 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str


class WorkspaceSnapshotCreatedPayload(BaseModel):
    """workspace_snapshot_created 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    snapshot_id: str
    message: str = ""


class WorkspaceRolledBackPayload(BaseModel):
    """workspace_rolled_back 事件载荷。"""

    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    snapshot_id: str
