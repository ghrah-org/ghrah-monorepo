# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ghrah-subject: 控制面/执行层（effect host + Agent 外围管理）。

Subject 运行时为 Ouroboros 装配形态：``assemble_subject``（mount_builtin_
units + 第三方 allowlist + reconcile 末尾触发）；cluster = CoreUnit 实例
（CoreClusterRegistry 运行时挂载）；Agent 运行期基建（Ability 执行/权限/
HITL/ActionChain 存储）单源归 Core（聚合裁决 1787900000000）。

公共导出全部惰性绑定（Python 3.15 ``lazy from``）：导入本包只建立名字，不加载
装配栈、units 或 Ouroboros。最小宿主入口只依赖 ``server.*`` 等轻量子模块，
不应因为 ``import ghrah.subject`` 的父包初始化被旧运行时拖进来。
"""

lazy from ghrah.subject.config import (
    CoreConnectionConfig,
    CoreTransportConfig,
    HITLPolicyConfig,
    ManifestConfig,
    PersistenceConfig,
    SandboxUnitConfig,
    SubjectConfig,
    TransportKindConfig,
)
lazy from ghrah.subject.ledger import (
    ActionChainLedger,
    ChainMeta,
    DAGEntry,
    LedgerNode,
)
lazy from ghrah.subject.manifest_store import ManifestStore
lazy from ghrah.subject.runtime.assembly import assemble_subject
lazy from ghrah.subject.runtime.service_keys import SubjectServiceKey
lazy from ghrah.subject.sandbox import (
    AgentWorkspace,
    CommandResult,
    SandboxExecutor,
    SandboxExecutorConfig,
    SnapshotError,
    WorkspaceManager,
    WorkspaceStatus,
)
lazy from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta

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
    "ActionChainLedger",
    "AgentWorkspace",
    "ChainMeta",
    "CommandResult",
    "DAGEntry",
    "LedgerNode",
    "ManifestStore",
    "SandboxExecutor",
    "SandboxExecutorConfig",
    "SnapshotError",
    "WorkspaceManager",
    "WorkspaceStatus",
]
