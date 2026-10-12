# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Typed service keys and runtime service contracts for Subject units.

聚合裁决（1787900000000）后收敛：Agent 运行期基建（Ability 执行/权限/
HITL/ActionChain 存储）单源归 Core；Subject 服务面 = 外围管理
（workspace/sandbox/task/project/manifest/recovery/observer/ledger 读侧/
cluster 调度）。历史 key（HITL_*/PERMISSION/PERSISTENCE/ABILITY_EXECUTOR/
CAPABILITY_REGISTRY/CLUSTER_TRANSPORT_MANAGER）随旧实现剔除。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Protocol, Required, TypedDict, TypeVar

from ghrah.subject.manifest_store.store import ManifestStore
from ghrah.subject.sandbox.executor import SandboxExecutor
from ghrah.subject.sandbox.workspace import WorkspaceManager
from ghrah.subject.task.store import TaskStore
from ghrah.subject.workspace.models import WorkspaceRecord

if TYPE_CHECKING:
    from ghrah.subject.project.models import AgentSpec, ProjectRecord

__all__ = [
    "ABILITY_BRIDGE",
    "ABILITY_BRIDGE_UNIT",
    "COMMAND_BRIDGE",
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
    "PROJECT_STORE",
    "RECONCILIATION_SERVICE",
    "ROOM_MANAGER",
    "ROOM_STORE",
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
    "RoomManagerService",
    "SubjectServiceKey",
    "TaskManagerService",
    "AbilityBridgeService",
    "AbilityDescriptor",
    "WorkspaceService",
]

T = TypeVar("T")


@dataclass(frozen=True)
class SubjectServiceKey(Generic[T]):
    """Typed key for values stored in the Ouroboros context (name carrier)."""

    name: str
    service_type: type[Any] | tuple[type[Any], ...] = object


class WorkspaceService(Protocol):
    """Workspace contract required by runtime units."""

    @property
    def root_path(self) -> str:
        """Return the workspace root path."""

    async def create_workspace(self, agent_name: str) -> Any:
        """Create or return a workspace for an agent."""

    def resolve_agent_path(self, agent_name: str) -> str | None:
        """Resolve an agent name to its workspace path, if present."""

    def resolve_agent_default_path(self, agent_name: str) -> str | None:
        """Resolve an agent name to its default workspace path."""

    def get_workspace_record(self, workspace_id: str) -> WorkspaceRecord | None:
        """Return the WorkspaceRecord for a workspace_id, if registered."""


class CommandRunner(Protocol):
    """Command execution contract used by sandbox-facing units."""

    async def run_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Run a command and return an implementation-defined result."""


class TaskManagerService(Protocol):
    """Task command orchestration contract exposed to runtime dispatch."""

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a task command. Returns a dispatcher-shape result dict."""


class ProjectManagerService(Protocol):
    """Project command orchestration contract exposed to runtime dispatch."""

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a project command. Returns a dispatcher-shape result dict."""

    async def bootstrap_default_project(self) -> ProjectRecord:
        """建 default project 并 ProjectStore.upsert，返回记录。"""

    async def adopt_existing_agents(self, project_id: str, cluster_id: str) -> list[AgentSpec]:
        """经 registry.list_agents 取现有 agent，构造 AgentSpec 并加入
        project.agents desired-state，返回列表。"""

    async def mark_agent_runtime(
        self, project_id: str, agent_id: str, runtime_error: str | None
    ) -> None:
        """持久化 agent 运行诊断（running / error；best-effort，允许陈旧）。"""


class RoomManagerService(Protocol):
    """Room command orchestration contract exposed to runtime dispatch.

    send 收敛点（双调用方分流）：Core send ability 经
    ``ctx.serial("command/room_send", ...)``（handle_command）或本服务直调
    ``append_log`` 落账，两路径同语义。
    """

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a room command. Returns a dispatcher-shape result dict."""

    async def append_log(
        self,
        room_id: str,
        *,
        author: str,
        author_type: Any,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """分配 seq → 落库 → 广播 ROOM_LOG_APPENDED。"""

    async def resolve_recipients(
        self, room_ids: list[str], extra_targets: list[str] | None = None
    ) -> dict[str, Any]:
        """recipients = Σ room agent 成员 ∪ extra_targets 去重（send ability 用）。"""


class ClusterHandle(Protocol):
    """单 cluster 的 Core 接入门面契约（spawn/terminate/list_agents）。"""

    @property
    def is_connected(self) -> bool:
        """该 cluster 的 CoreUnit 实例是否在运行。"""

    async def spawn_agent(self, payload: Any) -> dict[str, Any]:
        """发 spawn_agent，返回 command_result dict。"""

    async def list_agents(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """发 list_agents，返回 agent 信息列表。"""

    async def terminate_agent(self, agent_id: str, agent_name: str) -> dict[str, Any]:
        """发 terminate_agent，返回 command_result dict。"""

    async def send_message(self, payload: Any) -> dict[str, Any]:
        """向本 cluster 内指定 agent 发消息。"""


class CoreClusterRegistryService(Protocol):
    """cluster = CoreUnit 实例注册表契约（进程内运行时挂载/卸载）。"""

    async def ensure_cluster(
        self,
        cluster_id: str,
        *,
        project_id: str,
        project_root_locator: str,
    ) -> ClusterHandle:
        """幂等挂载/取某 cluster 的 CoreUnit 实例 handle。"""

    def get_handle(self, cluster_id: str) -> ClusterHandle:
        """取已挂载 handle；不存在 raise KeyError。"""

    def has_cluster(self, cluster_id: str) -> bool:
        """某 cluster 实例是否已挂载。"""

    async def shutdown_cluster(self, cluster_id: str) -> None:
        """dispose 某 cluster 的 fiber（handle 失效）。"""

    async def stop(self) -> None:
        """dispose 全部 cluster（unit.stop 时调用）。"""


class ObserverEventBus(Protocol):
    """Observer-facing event bridge contract."""

    async def publish(self, event_type: str, payload: dict[str, Any]) -> int:
        """Publish an event to observer sessions."""

    def subscribe(self, *args: Any, **kwargs: Any) -> Any:
        """Subscribe an observer session."""


class _ReservedService(Protocol):
    """Placeholder protocol for services planned for future stages."""


WORKSPACE_SERVICE = SubjectServiceKey[WorkspaceService]("workspace_service")
WORKSPACE_MANAGER = SubjectServiceKey[WorkspaceManager]("workspace_manager", WorkspaceManager)
COMMAND_RUNNER = SubjectServiceKey[CommandRunner]("command_runner")
LEDGER = SubjectServiceKey[Any]("ledger")
SANDBOX_EXECUTOR = SubjectServiceKey[SandboxExecutor]("sandbox_executor", SandboxExecutor)
MANIFEST_STORE = SubjectServiceKey[ManifestStore]("manifest_store", ManifestStore)
OBSERVER_ENDPOINT = SubjectServiceKey[Any]("observer_endpoint")
OBSERVER_EVENT_BUS = SubjectServiceKey[ObserverEventBus]("observer_event_bus")
TASK_MANAGER = SubjectServiceKey[TaskManagerService]("task_manager")
TASK_STORE = SubjectServiceKey[TaskStore]("task_store", TaskStore)
# 任务归因内核（S3）：facade 与 checker 注册表两个服务键（不改 legacy TASK_STORE 语义）
TASKSTORE_SERVICE = SubjectServiceKey[Any]("taskstore_service")
TASKSTORE_CHECKERS = SubjectServiceKey[Any]("taskstore_checkers")
CORE_CLUSTER_REGISTRY = SubjectServiceKey[CoreClusterRegistryService]("core_cluster_registry")
PROJECT_MANAGER = SubjectServiceKey[ProjectManagerService]("project_manager")
PROJECT_STORE = SubjectServiceKey["ProjectStore"]("project_store")
ROOM_MANAGER = SubjectServiceKey[RoomManagerService]("room_manager")
ROOM_STORE = SubjectServiceKey["RoomStore"]("room_store")


class AbilityDescriptor(TypedDict):
    """capability → 工具面描述符（进程内过渡形态）。

    字段面与 Core 侧 ``CommandProxyDescriptor``
    （ghrah-core ``abilities/builtin/command_proxy.py``，``extra="forbid"``）
    精确对齐：多出的键在 Core 物化时被拒。D2 落地时随 descriptor 升格
    ghrah-protocol 一并冻结形状。
    """

    tool_name: Required[str]
    description: str
    parameters: dict[str, Any] | None
    command: Required[str]
    require_hitl: bool
    timeout: float | None


class AbilityBridgeService(Protocol):
    """能力包装服务契约（场景 B：包装插件经服务键提供映射）。

    声明与实质分离：插件在 ``PluginSpec.provides.capabilities`` 声明供
    协商/校验，实质「capability → 工具描述符」映射经本服务提供——
    服务挂提供方 fiber 生命周期，插件卸载/崩溃即失活。
    """

    def capabilities(self) -> dict[str, AbilityDescriptor]:
        """返回当前 capability → descriptor 映射（纯数据快照）。"""

    async def register(self, capability: str, descriptor: AbilityDescriptor) -> None:
        """登记/覆盖一个 capability 映射。"""

    async def remove(self, capability: str) -> None:
        """移除一个 capability 映射（缺席幂等）。"""


# 能力包装映射（单提供者：ouroboros 同名服务全局唯一，冲突即拒挂）
ABILITY_BRIDGE = SubjectServiceKey[AbilityBridgeService]("ability_bridge")
# 桥接 unit 自身门面（plugin_mount 挂载/崩溃路径经它触发 reconcile）
ABILITY_BRIDGE_UNIT = SubjectServiceKey["AbilityBridgeUnit"]("ability_bridge_unit")

# Reserved for future integrations. The keys are intentionally real so
# dependency declarations can be written before the concrete services exist.
MCP_CLIENT_REGISTRY = SubjectServiceKey[_ReservedService]("mcp_client_registry")
SESSION_REGISTRY = SubjectServiceKey[_ReservedService]("session_registry")
COMMAND_BRIDGE = SubjectServiceKey[Any]("command_bridge")
EVENT_BRIDGE = SubjectServiceKey[Any]("event_bridge")


# recovery 服务键置于文件末尾：DesiredStateStore / ReconciliationService 为
# 具体类，置于末尾导入避免循环（recovery 模块本身不 import 本模块，
# 经 unit 注入依赖）。
from ghrah.subject.recovery.desired_state import (  # noqa: E402
    DesiredStateStore,
)
from ghrah.subject.recovery.reconciler import (  # noqa: E402
    ReconciliationService,
)

DESIRED_STATE_STORE = SubjectServiceKey[DesiredStateStore](
    "desired_state_store",
    DesiredStateStore,
)
RECONCILIATION_SERVICE = SubjectServiceKey[ReconciliationService](
    "reconciliation_service",
    ReconciliationService,
)
