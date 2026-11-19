# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 隔离设施。

- ``models``：隔离原语数据模型（ProjectRecord / IsolationSpec / AgentSpec 等）
- ``store``：ProjectStore（aiosqlite + 乐观锁 + 软删）
- ``isolation``：IsolationContext + workspace root 解析 + path_grants 校验
"""

from __future__ import annotations

from ghrah.subject.project.isolation import (
    IsolationContext,
    apply_isolation,
    resolve_project_roots,
    validate_workspace_locators_non_nested,
)
from ghrah.subject.project.manager import ProjectManager
from ghrah.subject.project.models import (
    PROJECT_TRANSITIONS,
    AgentOrigin,
    AgentPresentation,
    AgentRecord,
    IsolationSpec,
    PathGrant,
    ProjectRecord,
    ProjectStatus,
    RecoveryAction,
    RecoverySpec,
    WorkspaceMount,
    WritableWorkspaceSpec,
    can_transition,
    make_project_record,
    normalize_status,
)
from ghrah.subject.project.store import (
    ConcurrentModificationError,
    ProjectNotFoundError,
    ProjectStore,
)

__all__ = [
    "AGENT_TRANSITIONS_NOTE",
    "PROJECT_TRANSITIONS",
    "AgentOrigin",
    "AgentPresentation",
    "AgentRecord",
    "ConcurrentModificationError",
    "IsolationContext",
    "IsolationSpec",
    "PathGrant",
    "ProjectManager",
    "ProjectNotFoundError",
    "ProjectRecord",
    "ProjectStatus",
    "ProjectStore",
    "RecoveryAction",
    "RecoverySpec",
    "WorkspaceMount",
    "WritableWorkspaceSpec",
    "apply_isolation",
    "can_transition",
    "make_project_record",
    "normalize_status",
    "resolve_project_roots",
    "validate_workspace_locators_non_nested",
]
