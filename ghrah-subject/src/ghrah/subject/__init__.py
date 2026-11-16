# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ghrah-subject: 控制面/执行层（effect host + Agent 外围管理）。

Subject 运行时为 Ouroboros 装配形态：``assemble_subject``（mount_builtin_
units + 第三方 allowlist + reconcile 末尾触发）；cluster = CoreUnit 实例
（CoreClusterRegistry 运行时挂载）；Agent 运行期基建（Ability 执行/权限/
HITL/ActionChain 存储）单源归 Core（聚合裁决 1787900000000）。
"""

from ghrah.subject.config import (
    CoreConnectionConfig,
    CoreTransportConfig,
    HITLPolicyConfig,
    ManifestConfig,
    PersistenceConfig,
    SandboxUnitConfig,
    SubjectConfig,
    TransportKindConfig,
)
from ghrah.subject.manifest_store import ManifestStore
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.service_keys import SubjectServiceKey
from ghrah.subject.sandbox import (
    AgentWorkspace,
    CommandResult,
    SandboxExecutor,
    SandboxExecutorConfig,
    SnapshotError,
    WorkspaceManager,
    WorkspaceStatus,
)
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta

__all__ = [
    # 装配
    "assemble_subject",
    "SubjectUnit",
    "UnitMeta",
    "RouteSpec",
    "SubjectServiceKey",
    # config
    "SubjectConfig",
    "CoreConnectionConfig",
    "CoreTransportConfig",
    "HITLPolicyConfig",
    "ManifestConfig",
    "PersistenceConfig",
    "SandboxUnitConfig",
    "TransportKindConfig",
    # 子系统（单测用）
    "AgentWorkspace",
    "CommandResult",
    "ManifestStore",
    "SandboxExecutor",
    "SandboxExecutorConfig",
    "SnapshotError",
    "WorkspaceManager",
    "WorkspaceStatus",
]
