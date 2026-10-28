# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Subject runtime（Ouroboros 装配形态）。

装配入口 ``assemble_subject``；服务键表 ``service_keys``；挂载桥
``ouroboros_bridge``；第三方发现 ``third_party``。旧基建
（SubjectEngine/SubjectContext/SubjectServices/MessageDispatcher/
topological_sort/CapabilityRegistry）已随聚合裁决删除。
"""

from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import (
    bridge_command,
    mount_unit,
    wait_active,
)
from ghrah.subject.runtime.service_keys import (
    COMMAND_RUNNER,
    CORE_CLUSTER_REGISTRY,
    DESIRED_STATE_STORE,
    EVENT_BRIDGE,
    LEDGER,
    MANIFEST_STORE,
    MCP_CLIENT_REGISTRY,
    OBSERVER_ENDPOINT,
    OBSERVER_EVENT_BUS,
    PROJECT_MANAGER,
    RECONCILIATION_SERVICE,
    SANDBOX_EXECUTOR,
    SESSION_REGISTRY,
    TASK_MANAGER,
    TASK_STORE,
    WORKSPACE_MANAGER,
    WORKSPACE_SERVICE,
    ClusterHandle,
    CommandRunner,
    CoreClusterRegistryService,
    ObserverEventBus,
    ProjectManagerService,
    SubjectServiceKey,
    TaskManagerService,
    WorkspaceService,
)
from ghrah.subject.runtime.third_party import (
    discover,
    mount_third_party_units,
    resolve_discovered,
)

__all__ = [
    "COMMAND_RUNNER",
    "CORE_CLUSTER_REGISTRY",
    "DESIRED_STATE_STORE",
    "EVENT_BRIDGE",
    "LEDGER",
    "MANIFEST_STORE",
    "MCP_CLIENT_REGISTRY",
    "OBSERVER_ENDPOINT",
    "OBSERVER_EVENT_BUS",
    "PROJECT_MANAGER",
    "RECONCILIATION_SERVICE",
    "SANDBOX_EXECUTOR",
    "SESSION_REGISTRY",
    "TASK_MANAGER",
    "TASK_STORE",
    "WORKSPACE_MANAGER",
    "WORKSPACE_SERVICE",
    "ClusterHandle",
    "CommandRunner",
    "CoreClusterRegistryService",
    "ObserverEventBus",
    "ProjectManagerService",
    "SubjectServiceKey",
    "TaskManagerService",
    "WorkspaceService",
    "assemble_subject",
    "bridge_command",
    "discover",
    "mount_third_party_units",
    "mount_unit",
    "resolve_discovered",
    "wait_active",
]
