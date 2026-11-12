# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Core Unit 挂载形态 — 将 ghrah-core 包装为可挂载的 Unit。

ghrah-core 正从「独立 WS 服务器进程」重构为「可挂载的 Unit 形态」。
挂载宿主是 subject 侧的 Ouroboros 运行时，但本模块 **不 import
宿主运行时与 subject 包**：``CoreUnit`` 是纯 duck-typed 对象，
形状与 subject 侧桥 ``mount_unit`` 的契约严格兼容：

- ``unit.meta``：``.name`` / ``.requires`` / ``.provides`` / ``.routes``
  （routes 含 ``commands`` / ``long_running_commands`` / ``events`` 三个
  frozenset[str]，requires/provides 元素含 ``.name`` 属性）
- 生命周期：``await unit.init(ctx)`` → 路由注册 → ``await unit.start()``；
  卸载时 ``await unit.stop()``
- 命令：``await unit.handle_command(command, payload, cmd_ctx) -> dict``
- 事件：``await unit.handle_event(event_type, payload)``（core unit 不消费事件）

ctx 视为 duck-typed ``Any``，仅用到 ``ctx.emit(name, payload)``
（fire-and-forget）。Supervisor 等内部状态由实例自持，**不向宿主
provide**——Ouroboros 同名服务全局唯一（无 isolate 时），每实例状态
注册为全局服务会导致多集群（多 CoreUnit 实例）挂载冲突。

事件命名约定：core 域事件经 ``ctx.emit(f"core:{event_type}", payload_dict)``
发送，``core:`` 前缀防止与 subject/observer 域事件冲突。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from ghrah.protocol.types import (
    AGENT_SCOPED_EVENT_TYPES,
    AbilityResultPayload,
    AgentCompactContextPayload,
    AgentResetPayload,
    BranchActivatePayload,
    BranchArchivePayload,
    BranchCreatePayload,
    BranchDeletePayload,
    BranchListPayload,
    BroadcastMessagePayload,
    CommandType,
    DelegatePayload,
    EventType,
    ExecuteAbilityPayload,
    GetAgentInfoPayload,
    HITLResponsePayload,
    ListAgentsPayload,
    SendMessagePayload,
    SessionActivatePayload,
    SessionArchivePayload,
    SessionCreatePayload,
    SessionDeletePayload,
    SessionListPayload,
    SpawnAgentPayload,
    TerminateAgentPayload,
    generate_request_id,
)

from ghrah.ability.assembly import (
    AbilityAssembly,
    AbilitySourceMode,
)
from ghrah.ability.surface.assembly import SurfaceThresholds
from ghrah.ability.surface.config import (
    DEFAULT_DEFINITION_TOKEN_RATIO,
    DEFAULT_HARD_CAP,
    DEFAULT_LOADED_LIMIT,
    DEFAULT_MAX_STATIC,
    DEFAULT_SEARCH_LIMIT,
)
from ghrah.communication.errors import RegistryError
from ghrah.communication.supervisor import SupervisorActor
from ghrah.core.ability_protocol import AbilityProtocol
from ghrah.core.event_publisher import EventPublisher
from ghrah.core.events import (
    ActionChainUpdatedEvent,
    AgentErrorEvent,
    AgentResponseEvent,
    BranchActivatedEvent,
    BranchArchivedEvent,
    BranchCreatedEvent,
    BranchDeletedEvent,
    ContextUsageUpdatedEvent,
    CoreEvent,
    CoreEventType,
    HITLRequestEvent,
    HITLResolvedEvent,
    SessionActivatedEvent,
    SessionArchivedEvent,
    SessionCreatedEvent,
    SessionDeletedEvent,
)
from ghrah.core.hitl import HITLPromiseRegistry
from ghrah.core.host_command_port import HostCommandPort
from ghrah.core.room_protocol import SerialRoomBridge
from ghrah.core.subject_bridge import SubjectCommandBridge
from ghrah.types.config_types import AgentConfig
from ghrah.types.results import ActionOutcome

logger = logging.getLogger(__name__)

__all__ = [
    "CoreUnitConfig",
    "UnitEventPublisher",
    "CoreUnit",
    "create_core_unit",
]


# ----------------------------------------------------------------
# 配置
# ----------------------------------------------------------------


@dataclass(frozen=True)
class CoreUnitConfig:
    """CoreUnit 配置。

    Attributes:
        cluster_id: SupervisorActor 的集群标识
        default_timeout: Agent 间通信默认超时（秒）
        hitl_timeout: HITL 审批等待超时（秒）。
            AgentBuilder 不透传 hitl_timeout 到 LocalAbilityExecutor，
            CoreUnit 在 spawn 后对 executor 做 best-effort 调整。
        workspace_root: 工作区根目录默认值；AgentConfig 未显式指定时应用
            （None 表示不限制）。
        auto_approve_abilities: HITL 运行时覆盖层——管理员强制放行的能力名
            白名单（覆盖 manifest 的 require_hitl=True；语义平移自 subject
            侧旧 HITLPolicy 的同名覆盖层）。
        require_approval_by_default: 对 manifest 未声明审批要求的能力的兜底
            策略（语义平移自旧 HITLPolicy）。
        safe_extra_commands: 部署方追加的基础命令 safe 白名单（如 pnpm、
            make）。与内置 DEFAULT_SAFE_COMMANDS 并集合并，只增不减。
        safe_extra_sub_commands: 部署方追加的子命令 safe 白名单
            （{"pnpm": ("test", "lint")} 形状），与内置表并集合并。
        hitl_consecutive_timeout_limit: HITL 连续超时熔断阈值——连续 N 次
            审批超时后，后续 HITL 等待降级为 hitl_degraded_timeout 并在
            错误中声明审批通道疑似离线（引导 agent 收敛/end_task）；任何
            approve/reject 即复位。0 = 禁用（默认，零隐式）。
        hitl_degraded_timeout: 熔断降级后的 HITL 等待秒数。
        environment_injection: 环境信息注入开关——True 时 spawn 流程组装
            环境快照（部署形态/授权根/HITL 参数）注入 AgentConfig，经
            AgentBuilder 渲染 [Environment] prompt 段。默认 False（零隐式）。
        command_runner: execute_command 能力的命令执行器注入（per-cluster
            构造期注入，duck-typed ``run_command``；None = standalone 直跑
            subprocess）。Subject 挂载模式下传 SandboxExecutor。
        shell_cwd_provider: execute_command 省略 working_dir 时读取的 shell
            初始目录。每次调用重新读取；None 或空字符串表示不覆盖进程 cwd。
        persistence_factory: per-agent 持久化后端工厂
            （``(AgentConfig) -> PersistenceBackend | None``；None = Core
            内建 sqlite）。新 spawn 生效，存量 agent 不受影响。
        message_manager: 显式注入已打开的项目 MessageManager；开启固定会话路由，
            由装配方管理生命周期。未接入时 agent 间持久发送明确失败。
        llm_factory: per-cluster LLM 工厂注入
            （``(AgentConfig) -> LLMProtocol``；None = Core 内建默认，即
            agentconf 解析 + model_overrides 应用）。部署方显式声明 LLM
            出口（网关路由/凭据轮换/成本计量包装）的唯一接缝；注入即全责，
            model_overrides 等默认逻辑由注入方自理。仅进程内部署形态可用
            （Callable 不可序列化，与 command_runner 同边界）。
        manifest_store: ManifestStoreProtocol 实现（duck-typed），
            供 manifest_ref spawn 解析 agent manifest。None = standalone
            模式（manifest_ref spawn 明确报错，对齐 ROOM_BRIDGE_UNAVAILABLE
            模式）。Subject 挂载模式下注入其 ManifestStore。
        max_cluster_members: 集群成员上限（spawn 时超限 raise；
            透传 SupervisorActor.max_cluster_members，<=0 表示不限制）。
        surface_definition_token_ratio: 定义 token 占模型上下文窗口比例的
            默认上限，表面装配的阈值之一（本层只承载配置）。
        surface_max_static: 静态工具数默认上限。
        surface_hard_cap: auto 模式下切渐进的总成员数默认上限。
        surface_search_limit: 单次搜索结果默认上限（硬上限 20，不可配置）。
        surface_loaded_limit: 已加载集默认上限。
    """

    cluster_id: str = "default"
    # 空 = 未归属：Agent 作用域事件将按契约跳过发布（绝不猜测归属）。
    # Subject 挂载模式由 registry 工厂显式注入；standalone 显式传入。
    project_id: str = ""
    default_timeout: float = 300.0
    hitl_timeout: float = 300.0
    workspace_root: str | None = None
    auto_approve_abilities: tuple[str, ...] = ()
    require_approval_by_default: bool = True
    safe_extra_commands: tuple[str, ...] = ()
    safe_extra_sub_commands: tuple[tuple[str, tuple[str, ...]], ...] = ()
    hitl_consecutive_timeout_limit: int = 0
    hitl_degraded_timeout: float = 30.0
    environment_injection: bool = False
    command_runner: Any = None
    shell_cwd_provider: Callable[[], Awaitable[str | None]] | None = None
    persistence_factory: Callable[[Any], Any] | None = None
    # per-cluster LLM 工厂注入（(AgentConfig) -> LLMProtocol；None = Core 内建
    # 默认，即 agentconf 解析）。与 persistence_factory 同范式：部署方显式
    # 声明 LLM 出口（网关/凭据/计量包装），注入即全责（不再套用
    # _default_llm_factory 的 model_overrides 逻辑，由注入方自理）。
    message_manager: Any = None  # 已打开的项目 MessageManager，由装配方管理生命周期
    llm_factory: Callable[[Any], Any] | None = None
    # duck-typed ManifestStoreProtocol；None = standalone（manifest_ref spawn 报错）
    manifest_store: Any = None
    ability_source: str = "builtin"
    # 本地包 Provider 的显式安装根；None 不读取安装存储。
    ability_install_root: str | None = None
    ability_filesystem_roots: tuple[str, ...] = ()
    max_cluster_members: int = 20
    # 工具表面阈值：本层只承载配置，表面装配读取；默认值单源在
    # ghrah.ability.surface.config。显式 static 的绝对上限固定 64，不开放配置。
    surface_definition_token_ratio: float = DEFAULT_DEFINITION_TOKEN_RATIO
    surface_max_static: int = DEFAULT_MAX_STATIC
    surface_hard_cap: int = DEFAULT_HARD_CAP
    surface_search_limit: int = DEFAULT_SEARCH_LIMIT
    surface_loaded_limit: int = DEFAULT_LOADED_LIMIT
    # 启动授权关系库（装配方打开/关闭，duck-typed AbilityAccessStore）。
    # None = 未接线：builtin 模式 spawn 直接失败，不再回退到按模板实例化；
    # 注入后启动工具表只认授权事实（模板只决定申请哪些成员）。
    ability_store: Any = None
    # 启动授权策略（SubjectConfig.ability_grant_policy 透传）。None = 不授予。
    ability_grant_policy: Any = None
    # 受信 MCP 装配配置；None = 关闭，不读取 agentconf、不发现端点。
    mcp: Any = None
    # 宿主命令授权表：key = instance_id 或 f"{instance_id}/{member}"；
    # value = 允许派发的不透明命令名。空表 = 任何成员都不注入 HostCommandPort。
    # 领域命令名与包身份由装配方（Subject）写入，Core 不解读。
    host_command_grants: Mapping[str, frozenset[str]] = field(default_factory=dict)


# ----------------------------------------------------------------
# 桥契约形状（本地轻量复刻，不 import subject）
# ----------------------------------------------------------------


@dataclass(frozen=True)
class _ServiceKey:
    """服务键 — 仅需 ``.name`` 属性（bridge 做 ``[key.name for key in ...]``）。"""

    name: str


@dataclass(frozen=True)
class _RouteSpec:
    """路由声明 — 复刻 subject 侧 RouteSpec 三字段形状。"""

    commands: frozenset[str] = frozenset()
    long_running_commands: frozenset[str] = frozenset()
    events: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _UnitMeta:
    """Unit 元数据 — duck-typed 对齐 subject 侧 mount_unit 桥契约。"""

    name: str
    requires: frozenset[_ServiceKey]
    provides: frozenset[_ServiceKey]
    routes: _RouteSpec


# Core 管理命令：CommandType 的 .value 字符串
_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.SPAWN_AGENT.value,
        CommandType.TERMINATE_AGENT.value,
        CommandType.SEND_MESSAGE.value,
        CommandType.BROADCAST_MESSAGE.value,
        CommandType.REGISTER_ABILITY.value,
        CommandType.UNREGISTER_ABILITY.value,
        CommandType.LIST_AGENTS.value,
        CommandType.HEALTH_CHECK.value,
        CommandType.DELEGATE.value,
        CommandType.GET_AGENT_INFO.value,
        CommandType.EXECUTE_ABILITY.value,
        CommandType.AGENT_COMPACT_CONTEXT.value,
        CommandType.AGENT_RESET.value,
        CommandType.HITL_RESPONSE.value,
        CommandType.SESSION_CREATE.value,
        CommandType.SESSION_ACTIVATE.value,
        CommandType.SESSION_LIST.value,
        CommandType.SESSION_ARCHIVE.value,
        CommandType.SESSION_DELETE.value,
        CommandType.BRANCH_CREATE.value,
        CommandType.BRANCH_ACTIVATE.value,
        CommandType.BRANCH_LIST.value,
        CommandType.BRANCH_ARCHIVE.value,
        CommandType.BRANCH_DELETE.value,
    }
)


# ----------------------------------------------------------------
# 事件发布器：Core → ctx.emit
# ----------------------------------------------------------------


def _core_event_to_dict(event: CoreEvent) -> tuple[str, dict[str, Any]]:
    """将 CoreEvent 转换为 (event_type_str, payload_dict)。

    序列化逻辑对齐原 WS 服务器形态事件消息的 payload
    组装部分，但输出纯 dict，不包 WS Message/Envelope。
    """
    event_type_map: dict[str, str] = {
        CoreEventType.HITL_REQUEST.value: "hitl_request",
        CoreEventType.HITL_RESOLVED.value: "hitl_resolved",
        CoreEventType.ACTION_CHAIN_UPDATED.value: "action_chain_updated",
        CoreEventType.AGENT_ERROR.value: "agent_error",
        CoreEventType.AGENT_RESPONSE.value: "agent_response",
        CoreEventType.CONTEXT_USAGE_UPDATED.value: "context_usage_updated",
        CoreEventType.SESSION_CREATED.value: "session_created",
        CoreEventType.SESSION_ACTIVATED.value: "session_activated",
        CoreEventType.SESSION_ARCHIVED.value: "session_archived",
        CoreEventType.SESSION_DELETED.value: "session_deleted",
        CoreEventType.BRANCH_CREATED.value: "branch_created",
        CoreEventType.BRANCH_ACTIVATED.value: "branch_activated",
        CoreEventType.BRANCH_ARCHIVED.value: "branch_archived",
        CoreEventType.BRANCH_DELETED.value: "branch_deleted",
    }

    payload: dict[str, Any] = {
        "agent_name": event.agent_name,
        "event_type": event_type_map.get(event.event_type.value, event.event_type.value),
    }

    if isinstance(event, HITLRequestEvent):
        payload.update(
            {
                "ability_name": event.ability_name,
                # 协议 HITLRequestPayload 的键名为 tool_args（历史键 tool_call 与
                # schema 错位导致 Observer 命令摘要无数据）
                "tool_args": event.tool_call,
                "context": event.context,
                "promise_id": event.promise_id,
                "reason": event.reason,
            }
        )
    elif isinstance(event, HITLResolvedEvent):
        payload.update(
            {
                "promise_id": event.promise_id,
                "ability_name": event.ability_name,
                "tool_call_id": event.tool_call_id,
                "status": event.status,
            }
        )
    elif isinstance(event, ActionChainUpdatedEvent):
        payload.update({"node": event.node, "branch": event.branch})
    elif isinstance(event, AgentErrorEvent):
        payload.update({"error": event.error})
    elif isinstance(event, AgentResponseEvent):
        payload_update: dict[str, Any] = {
            "content": event.content,
            "message_type": event.message_type,
            "metadata": event.metadata,
        }
        if event.content_blocks is not None:
            payload_update["content_blocks"] = event.content_blocks
        payload.update(payload_update)
    elif isinstance(event, ContextUsageUpdatedEvent):
        payload.update(
            {
                "phase": event.phase,
                "occupied_tokens": event.occupied_tokens,
                "basis": event.basis,
                "budget_tokens": event.budget_tokens,
                "budget_source": event.budget_source,
                "compact_threshold": event.compact_threshold,
                "real_input_tokens": event.real_input_tokens,
                "real_output_tokens": event.real_output_tokens,
                "real_cache_read_tokens": event.real_cache_read_tokens,
                "real_cache_write_tokens": event.real_cache_write_tokens,
                "compaction": event.compaction,
                "iteration": event.iteration,
            }
        )
    elif isinstance(event, SessionCreatedEvent):
        payload.update({"session": event.session})
    elif isinstance(event, SessionActivatedEvent):
        payload.update({"session": event.session})
    elif isinstance(event, SessionArchivedEvent):
        payload.update({"session_id": event.session_id})
    elif isinstance(event, SessionDeletedEvent):
        payload.update({"session_id": event.session_id})
    elif isinstance(event, (BranchCreatedEvent, BranchActivatedEvent)):
        payload.update({"branch": event.branch})
    elif isinstance(event, (BranchArchivedEvent, BranchDeletedEvent)):
        payload.update({"session_id": event.session_id, "branch_id": event.branch_id})

    event_type_str = event_type_map.get(event.event_type.value, event.event_type.value)
    return event_type_str, payload


class UnitEventPublisher(EventPublisher):
    """Core → ctx.emit 事件发布器（Unit 挂载模式）。

    将 CoreEvent 序列化为纯 dict，经宿主 ctx.emit 以
    ``core:{event_type}`` 名称发送（fire-and-forget）。
    emit 为 None 时退化为 Null 行为（仅日志），用于 standalone 场景。
    """

    def __init__(
        self,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
        *,
        project_id: str = "",
        cluster_id: str = "",
        agent_id_resolver: Callable[[str], str] | None = None,
    ) -> None:
        """初始化 UnitEventPublisher。

        Args:
            emit: 宿主事件发射回调（通常为 ``ctx.emit``），None 表示 Null 模式
        """
        self._emit = emit
        self._project_id = project_id
        self._cluster_id = cluster_id
        self._agent_id_resolver = agent_id_resolver

    async def publish(self, event: CoreEvent) -> None:
        """发布事件到宿主 ctx。"""
        event_type, payload = _core_event_to_dict(event)
        payload["project_id"] = self._project_id
        payload["cluster_id"] = self._cluster_id
        payload["agent_id"] = (
            self._agent_id_resolver(event.agent_name) if self._agent_id_resolver is not None else ""
        )
        # Agent 作用域事件缺失归属（project_id/agent_id 任一为空）时跳过发布：
        # 不以空值污染下游 Project 分桶，也不猜测归属（如 shutdown 后解析失败）。
        if event_type in AGENT_SCOPED_EVENT_TYPES and (
            not payload["project_id"] or not payload["agent_id"]
        ):
            logger.warning(
                "Dropped unattributed core event '%s' for agent '%s' (missing project_id/agent_id)",
                event_type,
                event.agent_name,
            )
            return
        if self._emit is None:
            logger.debug(f"Event published (unit-null): {event_type} for agent {event.agent_name}")
            return
        try:
            self._emit(f"core:{event_type}", payload)
            logger.debug(f"Event published (unit): core:{event_type} for agent {event.agent_name}")
        except Exception:
            # fire-and-forget：emit 失败不阻断驱动循环
            logger.exception(f"Failed to emit core:{event_type} for agent {event.agent_name}")


def _agent_config_to_wire(config: AgentConfig) -> dict[str, Any]:
    """AgentConfig → wire 事件 dict（对齐 AgentConfigPayload 字段，供 AGENT_SPAWNED 事件）。"""
    import dataclasses

    return {
        "agent_id": config.effective_agent_id,
        "name": config.name,
        "endpoint_name": config.endpoint_name,
        "description": config.description,
        "system_prompt": config.system_prompt,
        "max_iterations": config.max_iterations,
        "communication_timeout": config.communication_timeout,
        "window": dataclasses.asdict(config.window) if config.window else None,
        "context": dataclasses.asdict(config.context) if config.context else None,
        "model_overrides": (
            dataclasses.asdict(config.model_overrides) if config.model_overrides else None
        ),
    }


# ----------------------------------------------------------------
# CoreUnit
# ----------------------------------------------------------------

_CommandHandler = Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]


class CoreUnit:
    """ghrah-core 的 Unit 挂载形态。

    纯 duck-typed 对象：不 import 宿主运行时/subject 包，形状与 subject 侧
    ``mount_unit`` 桥契约严格兼容。内部持有 SupervisorActor 与
    UnitEventPublisher，将 17 个 core 命令分发到 Supervisor/Agent 层。

    用法:
        unit = create_core_unit(CoreUnitConfig())
        await unit.init(ctx)   # ctx.provide("supervisor"/"core_registry")
        await unit.start()
        result = await unit.handle_command("list_agents", {}, cmd_ctx)
        await unit.stop()
    """

    def __init__(self, config: CoreUnitConfig) -> None:
        """初始化 CoreUnit（不触碰宿主，init(ctx) 时才接管）。

        Args:
            config: Unit 配置
        """
        self._config = config
        self._meta = _UnitMeta(
            name="core",
            requires=frozenset(),
            provides=frozenset(),
            routes=_RouteSpec(commands=_COMMANDS),
        )
        self._supervisor: SupervisorActor | None = None
        self._publisher: UnitEventPublisher | None = None
        self._emit: Callable[[str, dict[str, Any]], None] | None = None
        self._stopped = False
        self._hitl_promises = HITLPromiseRegistry()
        # 装配模式在 init(ctx) 时确定（需要 manifest_store 等装配期输入）。
        self._assembly: AbilityAssembly | None = None
        # 授权装配边界（builtin 模式唯一入口）；None = 未接线。
        self._startup_materializer: Any = None
        self._mcp_deployment: Any = None

        self._handlers: dict[str, _CommandHandler] = {
            CommandType.SPAWN_AGENT.value: self._handle_spawn_agent,
            CommandType.TERMINATE_AGENT.value: self._handle_terminate_agent,
            CommandType.SEND_MESSAGE.value: self._handle_send_message,
            CommandType.BROADCAST_MESSAGE.value: self._handle_broadcast_message,
            CommandType.REGISTER_ABILITY.value: self._handle_register_ability,
            CommandType.UNREGISTER_ABILITY.value: self._handle_unregister_ability,
            CommandType.LIST_AGENTS.value: self._handle_list_agents,
            CommandType.HEALTH_CHECK.value: self._handle_health_check,
            CommandType.DELEGATE.value: self._handle_delegate,
            CommandType.GET_AGENT_INFO.value: self._handle_get_agent_info,
            CommandType.AGENT_COMPACT_CONTEXT.value: self._handle_agent_compact_context,
            CommandType.AGENT_RESET.value: self._handle_agent_reset,
            CommandType.EXECUTE_ABILITY.value: self._handle_execute_ability,
            CommandType.HITL_RESPONSE.value: self._handle_hitl_response,
            CommandType.SESSION_CREATE.value: self._handle_session_create,
            CommandType.SESSION_ACTIVATE.value: self._handle_session_activate,
            CommandType.SESSION_LIST.value: self._handle_session_list,
            CommandType.SESSION_ARCHIVE.value: self._handle_session_archive,
            CommandType.SESSION_DELETE.value: self._handle_session_delete,
            CommandType.BRANCH_CREATE.value: self._handle_branch_create,
            CommandType.BRANCH_ACTIVATE.value: self._handle_branch_activate,
            CommandType.BRANCH_LIST.value: self._handle_branch_list,
            CommandType.BRANCH_ARCHIVE.value: self._handle_branch_archive,
            CommandType.BRANCH_DELETE.value: self._handle_branch_delete,
        }

    @property
    def meta(self) -> _UnitMeta:
        """Unit 元数据（bridge 契约）。"""
        return self._meta

    @property
    def supervisor(self) -> SupervisorActor | None:
        """内部 SupervisorActor（init 前为 None）。"""
        return self._supervisor

    @property
    def ability_source(self) -> str:
        """返回已配置的 v1 能力来源。"""
        return AbilitySourceMode.parse(self._config.ability_source).value

    def _require_assembly(self) -> AbilityAssembly:
        """取已确定的装配模式，未 init 时抛错。"""
        if self._assembly is None:
            raise RuntimeError("CoreUnit not initialized (init(ctx) first)")
        return self._assembly

    # ----------------------------------------------------------------
    # 生命周期
    # ----------------------------------------------------------------

    async def init(self, ctx: Any) -> None:
        """初始化失败时释放本 Unit 已创建的 MCP 资源，不关闭借用的关系库。"""
        try:
            await self._init(ctx)
        except BaseException:
            if self._mcp_deployment is not None:
                await self._mcp_deployment.close()
            raise

    async def _init(self, ctx: Any) -> None:
        """初始化 Unit：构造 Supervisor（实例自持，不向宿主注册服务）。

        Args:
            ctx: 宿主上下文（duck-typed），用到 ``ctx.emit``
        """
        emit = getattr(ctx, "emit", None)
        self._emit = emit if callable(emit) else None
        self._publisher = UnitEventPublisher(
            emit=self._emit,
            project_id=self._config.project_id,
            cluster_id=self._config.cluster_id,
            agent_id_resolver=self._agent_id_for_name,
        )

        # Room bridge（send 工具回路）：宿主 ctx 有 serial 时桥接 Subject RoomUnit
        # （duck-typed；standalone 场景无 serial → bridge 为 None，send 工具明确报错）
        serial = getattr(ctx, "serial", None)
        room_bridge = SerialRoomBridge(serial) if callable(serial) else None
        self._host_command_port = (
            HostCommandPort(SubjectCommandBridge(serial)) if callable(serial) else None
        )

        if (
            self._config.message_manager is not None
            and self._config.message_manager.project_id != self._config.project_id
        ):
            raise ValueError("MessageManager project does not match CoreUnit")
        # 装配模式：一次选择同时决定能力目录、工具实例与 Skill 读取服务。
        # 只属于旧模式的可写入口由 builtin 模式显式拒绝。
        assembly = AbilityAssembly.select(
            self._config.ability_source,
            agent_store=self._config.manifest_store,
            install_root=self._config.ability_install_root,
        )
        self._assembly = assembly
        if self._config.mcp is not None:
            from ghrah.ability.mcp_wiring import McpDeployment, McpDeploymentConfig

            if not isinstance(self._config.mcp, McpDeploymentConfig):
                raise TypeError("mcp 必须是 McpDeploymentConfig 或 None")
            if self._config.ability_store is None:
                raise ValueError("MCP 接线需要 builtin 来源和显式授权关系库")
            self._mcp_deployment = McpDeployment(
                self._config.mcp, self._config.ability_store, self._config.cluster_id
            )
        # 授权装配边界：builtin 模式在装配期构造唯一一个启动核定器，顶层 spawn
        # 与子 Agent spawn 共用；未注入关系库时保持 None，spawn 期显式失败
        # （不再回退按模板实例化）。
        startup_materializer: Any = None
        if self._config.ability_store is not None:
            from ghrah.ability.access import build_startup_materializer

            startup_materializer = build_startup_materializer(
                store=self._config.ability_store,
                source=assembly.source,
                project_id=self._config.project_id,
                policy=self._config.ability_grant_policy,
                workspace_root=self._config.workspace_root,
                auto_approve_abilities=self._config.auto_approve_abilities,
                filesystem_roots=self._config.ability_filesystem_roots,
                require_approval_by_default=self._config.require_approval_by_default,
                command_runner=self._config.command_runner,
                shell_cwd_provider=self._config.shell_cwd_provider,
                safe_extra_commands=self._config.safe_extra_commands,
                safe_extra_sub_commands=self._config.safe_extra_sub_commands,
                mcp_deployment=self._mcp_deployment,
                surface_thresholds=SurfaceThresholds.from_core_config(self._config),
                surface_search_limit=self._config.surface_search_limit,
                surface_loaded_limit=self._config.surface_loaded_limit,
            )
        self._startup_materializer = startup_materializer

        supervisor = SupervisorActor(
            default_timeout=self._config.default_timeout,
            cluster_id=self._config.cluster_id,
            event_publisher=self._publisher,
            room_bridge=room_bridge,
            max_cluster_members=self._config.max_cluster_members,
            manifest_store=self._config.manifest_store,
            llm_factory=self._config.llm_factory,
            message_manager=self._config.message_manager,
            builtin_abilities=True,
            ability_materializer=startup_materializer,
            host_command_port=self._host_command_port,
            host_command_grants=self._config.host_command_grants,
        )
        self._supervisor = supervisor

        logger.info(f"CoreUnit initialized (cluster_id={self._config.cluster_id})")

    async def start(self) -> None:
        """启动 Unit（幂等空实现 — 路由注册由 bridge 完成）。"""
        logger.debug("CoreUnit started")

    async def stop(self) -> None:
        """停止 Unit：清理 pending HITL（futures + promises）并终止所有 Agent（幂等）。"""
        if self._stopped:
            return
        self._stopped = True

        self._hitl_promises.clear()

        supervisor = self._supervisor
        if supervisor is None:
            if self._mcp_deployment is not None:
                await self._mcp_deployment.close()
            return

        # 先清理 executor 层 pending HITL futures，再终止 agents
        try:
            for agent_info in await supervisor.list_agents():
                name = agent_info.get("name", "")
                if not name:
                    continue
                try:
                    handle = await supervisor.get_agent_handle(name)
                    executor = getattr(handle, "_ability_executor", None)
                    hitl_store = getattr(executor, "hitl_store", None)
                    if hitl_store is not None:
                        hitl_store.cancel_all()
                except Exception:
                    logger.warning(f"CoreUnit.stop: 清理 agent '{name}' HITL futures 失败")
        except Exception:
            logger.warning("CoreUnit.stop: 枚举 agents 失败")

        try:
            await supervisor.shutdown()
        finally:
            if self._mcp_deployment is not None:
                await self._mcp_deployment.close()
        logger.info("CoreUnit stopped")

    # ----------------------------------------------------------------
    # 命令/事件分发（bridge 契约）
    # ----------------------------------------------------------------

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any] | None:
        """分发命令到对应 handler。

        Args:
            command: 命令名（CommandType 的 .value 字符串）
            payload: 命令载荷
            cmd_ctx: 命令上下文（subject 侧占位对象，视为 Any）

        Returns:
            回执字典 ``{"success": bool, "data": ..., "error": ...}``；
            handler 显式返回 ``None`` 时原样穿透（宿主 serial「返回
            None = 不是我的」约定，当前仅 hitl_response 的多 cluster
            穿透使用）。
        """
        handler = self._handlers.get(command)
        if handler is None:
            return {"success": False, "error": f"Unknown command: {command}"}
        try:
            return await handler(payload, cmd_ctx)
        except Exception as e:
            logger.exception(f"CoreUnit: error handling {command}: {e}")
            return {"success": False, "error": str(e)}

    async def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """消费宿主事件（core unit 不消费事件，空实现）。"""
        logger.debug(f"CoreUnit.handle_event ignored: {event_type}")

    # ----------------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------------

    def _require_supervisor(self) -> SupervisorActor:
        """取 Supervisor，未 init 时抛错（由 handle_command 统一捕获）。"""
        if self._supervisor is None:
            raise RuntimeError("CoreUnit not initialized (init(ctx) first)")
        return self._supervisor

    def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """经宿主 emit 发送 core 域事件（``core:{event_type}``，fire-and-forget）。"""
        if self._emit is None:
            logger.debug(f"Event emitted (unit-null): core:{event_type}")
            return
        try:
            enriched = dict(payload)
            # 注意不能用 setdefault：wire payload 的 model_dump 自带空串字段
            # （协议默认 project_id=""），已存在的空键不会被 setdefault 覆盖。
            if not enriched.get("project_id"):
                enriched["project_id"] = self._config.project_id
            if not enriched.get("cluster_id"):
                enriched["cluster_id"] = self._config.cluster_id
            agent_name = str(enriched.get("agent_name") or enriched.get("name") or "")
            if agent_name and not enriched.get("agent_id"):
                enriched["agent_id"] = self._agent_id_for_name(agent_name)
            # Agent 作用域事件缺失归属时跳过（与 UnitEventPublisher 同一契约）。
            if event_type in AGENT_SCOPED_EVENT_TYPES and (
                not enriched.get("project_id") or not enriched.get("agent_id")
            ):
                logger.warning(
                    "Dropped unattributed core event '%s' for agent '%s' "
                    "(missing project_id/agent_id)",
                    event_type,
                    agent_name,
                )
                return
            session = enriched.get("session")
            if isinstance(session, dict):
                if not session.get("project_id"):
                    session["project_id"] = self._config.project_id
                if not session.get("cluster_id"):
                    session["cluster_id"] = self._config.cluster_id
                if not session.get("agent_id"):
                    session["agent_id"] = enriched.get("agent_id", "")
            self._emit(f"core:{event_type}", enriched)
        except Exception:
            logger.exception(f"Failed to emit core:{event_type}")

    def _agent_id_for_name(self, agent_name: str) -> str:
        supervisor = self._require_supervisor()
        try:
            return str(supervisor._registry.get_info(agent_name).config.effective_agent_id)
        except Exception:
            return ""

    def _validate_project(self, project_id: str) -> None:
        if not project_id:
            raise ValueError("project_id required")
        if project_id != self._config.project_id:
            raise ValueError("cluster_project_mismatch")

    def _validate_agent(self, project_id: str, agent_id: str, agent_name: str) -> None:
        self._validate_project(project_id)
        if not agent_id:
            raise ValueError("agent_id required")
        if self._agent_id_for_name(agent_name) != agent_id:
            raise ValueError("agent_identity_mismatch")

    def _environment_snapshot(self, abilities: list[AbilityProtocol] | None) -> dict[str, Any]:
        """组装环境快照（[Environment] 注入与 session_info 的静态部分）。

        数据源均为 CoreUnitConfig / 已实例化 ability 的现成事实，本方法
        只聚合不判定。allowed_roots 从各 FS ability 的 permission_checker
        property 收集去重。

        Args:
            abilities: spawn 时实例化的 ability 列表（可为 None）。

        Returns:
            环境快照 dict（deployment/workspace_root/allowed_roots/hitl 参数等）。
        """
        roots: list[str] = []
        if self._config.workspace_root:
            roots.append(self._config.workspace_root)
        for ability in abilities or []:
            checker = getattr(ability, "_checker", None)
            if checker is None:
                continue
            for prop in ("allowed_paths", "workspace_root"):
                try:
                    value = getattr(checker, prop, None)
                except Exception:
                    value = None
                if not value:
                    continue
                values = value if isinstance(value, list) else [value]
                for item in values:
                    item = str(item)
                    if item not in roots:
                        roots.append(item)
        deployment = "subject-mounted" if self._config.manifest_store is not None else "standalone"
        return {
            "deployment": deployment,
            "project_id": self._config.project_id,
            "cluster_id": self._config.cluster_id,
            "workspace_root": self._config.workspace_root,
            "default_working_dir": self._config.workspace_root,
            "allowed_roots": roots,
            "hitl_timeout": self._config.hitl_timeout,
            "hitl_consecutive_timeout_limit": self._config.hitl_consecutive_timeout_limit,
            "hitl_degraded_timeout": self._config.hitl_degraded_timeout,
        }

    @staticmethod
    def _inject_session_info_snapshot(
        abilities: list[AbilityProtocol] | None, config: AgentConfig
    ) -> None:
        """将环境快照写入已实例化的 SessionInfoAbility（duck-typed best-effort）。

        SessionInfoAbility 在能力包物化后接收部署快照。快照的来源是
        ``_environment_snapshot``，此处只做单向派生注入。

        Args:
            abilities: spawn 时实例化的 ability 列表（可为 None）。
            config: 已带 environment_info 的 AgentConfig（快照来源）。
        """
        if not config.environment_info:
            return
        for ability in abilities or []:
            if getattr(ability, "name", None) == "session_info" and hasattr(
                ability, "set_environment_info"
            ):
                ability.set_environment_info(config.environment_info)

    @staticmethod
    def _ok(data: Any = None) -> dict[str, Any]:
        return {"success": True, "data": data}

    @staticmethod
    def _err(error: str) -> dict[str, Any]:
        return {"success": False, "error": error}

    # ----------------------------------------------------------------
    # Agent 生命周期命令
    # ----------------------------------------------------------------

    def _validate_model_config(self, name: str | None) -> None:
        """启动前检查默认工厂的配置链；自定义工厂管理自己的模型来源。"""
        if self._config.llm_factory is not None:
            return
        from agentconf import AgentsConfig

        if not name or not name.strip():
            raise ValueError(
                "model_configuration_invalid: endpoint_name is required; "
                "migrate legacy Agent configuration"
            )
        try:
            with AgentsConfig() as config:
                config.resolve_model_endpoint(name)
        except Exception as exc:
            raise ValueError(f"model_configuration_invalid: {name}") from exc

    async def _handle_spawn_agent(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        """在授权核定之前固定稳定主体，并预留本次注册。"""
        from uuid import uuid4

        sp = SpawnAgentPayload.model_validate(payload)
        if not sp.config.agent_id:
            config = sp.config.model_copy(update={"agent_id": uuid4().hex})
            sp = sp.model_copy(update={"config": config})
        from ghrah.ability.access.identity import validate_agent_id

        validate_agent_id(sp.config.agent_id)
        # window/context 是 manifest context 段的语义。本命令用 resolver 从
        # manifest 构建配置，wire 侧这两个字段历来不参与装配——此前静默丢弃，
        # 调用方传了会以为生效（context.window 尤其危险：它决定预算载体能否
        # 落定，传了不生效正是「配置链在源头断开」的成因之一）。改为 fail-closed
        # 显式拒绝，与 direct-abilities spawn 同一姿态：不再静默吞掉。
        # 注意 endpoint_name/model_overrides/system_prompt 不在此列——生产路径
        # （subject project manager）确实会传，且 manifest 是它们唯一的权威来源，
        # 拒绝会打断恢复；它们的「传了不生效」由 manifest 权威语义解释。
        if sp.config.window is not None or sp.config.context is not None:
            raise ValueError(
                "wire_context_unsupported: spawn_agent builds config from manifest_ref; "
                "declare context.window / context.persistence in the Agent manifest"
            )
        config = AgentConfig(name=sp.config.name, agent_id=sp.config.agent_id)
        supervisor = self._require_supervisor()
        try:
            async with supervisor.spawn_reservation(config):
                return await self._handle_reserved_spawn(sp)
        except RegistryError as error:
            return self._err(str(error))

    async def _handle_reserved_spawn(self, sp: SpawnAgentPayload) -> dict[str, Any]:
        """在持有预留时解析模板并核定启动授权。"""
        source = self._require_assembly().source
        supervisor = self._require_supervisor()
        self._validate_project(sp.project_id)
        if sp.cluster_id and sp.cluster_id != self._config.cluster_id:
            raise ValueError("cluster_id_mismatch")
        if sp.abilities:
            AbilityAssembly.refuse(
                "direct-abilities spawn",
                "wire ability definitions used the legacy registry; use manifest_ref",
            )
        if not sp.manifest_ref:
            AbilityAssembly.refuse(
                "default-abilities spawn", "use manifest_ref instead of a default ability set"
            )
        if self._config.manifest_store is None:
            return self._err("manifest_store not available (standalone)")
        try:
            manifest = source.load_agent(sp.manifest_ref)
            resolved = source.resolver().resolve(manifest, runtime_name=sp.config.name or None)
        except Exception as e:
            logger.exception("CoreUnit.spawn_agent: manifest resolve failed: %s", e)
            return self._err(f"manifest resolve failed for '{sp.manifest_ref}': {e}")
        if sp.validate_model:
            self._validate_model_config(resolved.config.endpoint_name)
        resolved.config = replace(resolved.config, agent_id=sp.config.agent_id)
        supervisor.check_spawn(resolved.config)
        materialization = await self._materialize_builtin_abilities(
            resolved.config.effective_agent_id, resolved
        )
        ability_instances = materialization.abilities
        tool_table_projector = self._startup_materializer.tool_table_projector(
            resolved.config.effective_agent_id, materialization
        )
        try:
            resolved_config = resolved.config
            if self._config.environment_injection:
                resolved_config = replace(
                    resolved_config,
                    environment_context_injection=True,
                    environment_info=self._environment_snapshot(ability_instances),
                )
                self._inject_session_info_snapshot(ability_instances, resolved_config)
            agent_name = await supervisor.spawn_agent(
                resolved_config,
                abilities=ability_instances,
                persistence_factory=self._config.persistence_factory,
                llm_factory=self._config.llm_factory,
                tags=list(manifest.metadata.tags),
                tool_table_projector=tool_table_projector,
            )
        except RegistryError as e:
            return self._err(str(e))
        recovery_mode = supervisor.recovery_mode(agent_name)
        incarnation_id = supervisor.incarnation_id(agent_name)
        self._apply_hitl_timeout(agent_name)
        self._emit_event(
            EventType.AGENT_SPAWNED.value,
            {
                "name": agent_name,
                "agent_id": resolved_config.effective_agent_id,
                "config": _agent_config_to_wire(resolved_config),
                "recovery_mode": recovery_mode,
                "incarnation_id": incarnation_id,
            },
        )
        return self._ok(
            {
                "name": agent_name,
                "agent_id": resolved_config.effective_agent_id,
                "recovery_mode": recovery_mode,
                "incarnation_id": incarnation_id,
            }
        )

    async def _materialize_builtin_abilities(self, agent: str, resolved: Any) -> Any:
        """经授权装配边界物化工具表。"""
        from ghrah.ability.access import StartupGrantError

        materializer = self._startup_materializer
        if materializer is None:
            raise StartupGrantError(
                "builtin spawn 需要注入启动授权关系库（CoreUnitConfig.ability_store）；"
                "已移除按模板实例化的过渡退路——关系库是授权唯一权威，"
                "模板只表达申请，不能自行生效"
            )
        materialized = await materializer.materialize(agent=agent, resolved=resolved)
        logger.info(
            "builtin spawn 授权核定完成：agent=%s tools=%s granted=%s revoked=%s",
            agent,
            [binding.tool_name for binding in materialized.tools],
            list(materialized.granted),
            list(materialized.revoked),
        )
        return materialized

    def _apply_hitl_timeout(self, agent_name: str) -> None:
        """best-effort 将 config.hitl_timeout 与 promise 注册表应用到 agent 的本地 executor。

        AgentBuilder 不透传 hitl_timeout 到 LocalAbilityExecutor，
        故在 spawn 后直接调整（防御式：找不到 executor 时仅记日志）。
        promise 注册表为 unit 级单例——所有 agent 的 executor 共用同一张
        promise_id → 三元组表，hitl_response 命令面据此翻译。
        """
        if self._supervisor is None:
            return
        try:
            info = self._supervisor._registry.get_info(agent_name)
            executor = getattr(info.actor_handle, "_ability_executor", None)
            if executor is not None and hasattr(executor, "_hitl_timeout"):
                executor._hitl_timeout = self._config.hitl_timeout
            if executor is not None and hasattr(executor, "_hitl_timeout_limit"):
                executor._hitl_timeout_limit = self._config.hitl_consecutive_timeout_limit
                executor._hitl_degraded_timeout = self._config.hitl_degraded_timeout
            if executor is not None and hasattr(executor, "update_hitl_promise_registry"):
                executor.update_hitl_promise_registry(self._hitl_promises)
        except Exception:
            logger.warning(f"CoreUnit: 应用 hitl_timeout 到 agent '{agent_name}' 失败")

    async def _handle_terminate_agent(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        tp = TerminateAgentPayload.model_validate(payload)
        self._validate_agent(tp.project_id, tp.agent_id, tp.name)
        info = supervisor._registry.get_info(tp.name)
        agent_id = info.config.effective_agent_id
        incarnation_id = info.incarnation_id
        await supervisor.terminate_agent(tp.name)
        self._emit_event(
            EventType.AGENT_TERMINATED.value,
            {
                "name": tp.name,
                "project_id": tp.project_id,
                "agent_id": agent_id,
                "incarnation_id": incarnation_id,
            },
        )
        return self._ok(
            {
                "name": tp.name,
                "agent_id": agent_id,
                "incarnation_id": incarnation_id,
                "terminated": True,
            }
        )

    # ----------------------------------------------------------------
    # 消息路由命令
    # ----------------------------------------------------------------

    async def _handle_send_message(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        sp = SendMessagePayload.model_validate(payload)
        self._validate_agent(sp.project_id, sp.agent_id, sp.target)
        invocation = (sp.metadata or {}).get("execute_ability")
        if invocation is not None:
            agent = await supervisor.get_agent_handle(sp.target)
            name = invocation.get("name") if isinstance(invocation, dict) else None
            if not isinstance(name, str) or name not in agent.get_abilities():
                # 在入 MM 队列前拒绝配置错误，避免失败输入阻塞后续同会话消息。
                return {
                    "success": False,
                    "error": "source_ability_missing",
                    "error_detail": f"Agent '{sp.target}' has no registered ability '{name}'",
                    "data": {"agent_name": sp.target, "ability_name": name},
                }
        content_blocks = (
            [block.model_dump(mode="json", exclude_none=True) for block in sp.content_blocks]
            if sp.content_blocks
            else None
        )
        result = await supervisor.send(
            target=sp.target,
            content=sp.content,
            sender=sp.sender,
            timeout=sp.timeout,
            metadata=sp.metadata,
            content_blocks=content_blocks,
        )
        return self._ok({"content": result})

    async def _handle_broadcast_message(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        bp = BroadcastMessagePayload.model_validate(payload)
        self._validate_project(bp.project_id)
        results = await supervisor.broadcast(content=bp.content, sender=bp.sender)
        return self._ok({"responses": results})

    async def _handle_delegate(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        dp = DelegatePayload.model_validate(payload)
        self._validate_agent(dp.project_id, dp.from_agent_id, dp.from_agent)
        self._validate_agent(dp.project_id, dp.to_agent_id, dp.to_agent)
        result = await supervisor.delegate(
            from_agent=dp.from_agent,
            to_agent=dp.to_agent,
            content=dp.content,
            timeout=dp.timeout,
        )
        return self._ok({"content": result})

    # ----------------------------------------------------------------
    # Ability 注册命令
    # ----------------------------------------------------------------

    async def _handle_register_ability(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        return self._err(
            "register_ability is retired; configure tool selection in the ability access store"
        )

    async def _handle_unregister_ability(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        return self._err(
            "unregister_ability is retired; revoke or unbind in the ability access store"
        )

    # ----------------------------------------------------------------
    # 查询命令
    # ----------------------------------------------------------------

    async def tool_snapshot(self, agent_id: str) -> list[str] | None:
        """按稳定 Agent ID 读取当前模型工具面工具名（只读；不在运行返回 None）。"""
        supervisor = self._supervisor
        if supervisor is None:
            return None
        handle = await supervisor.get_agent_handle_by_id(agent_id)
        if handle is None:
            return None
        getter = getattr(handle, "visible_tool_names", None)
        if not callable(getter):
            return None
        return list(getter())

    async def _handle_list_agents(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        request = ListAgentsPayload.model_validate(payload)
        self._validate_project(request.project_id)
        result = await supervisor.list_agents()
        for agent in result:
            name = str(agent.get("name") or "")
            agent.update(
                {
                    "project_id": request.project_id,
                    "cluster_id": self._config.cluster_id,
                    "agent_id": self._agent_id_for_name(name),
                }
            )
        return self._ok({"agents": result})

    async def _handle_health_check(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        result = await supervisor.health_check()
        return self._ok(result)

    def get_entity_runtime(self, project_id: str, agent_id: str) -> dict[str, Any]:
        """按实体身份读取当前执行实例，不返回可写 Actor 引用。"""
        from ghrah.communication.errors import AgentNotFoundError

        self._validate_project(project_id)
        try:
            info = self._require_supervisor()._registry.get_info_by_id(agent_id)
        except AgentNotFoundError:
            return {
                "availability": "offline",
                "incarnation_id": None,
                "busy": None,
                "loaded_abilities": None,
            }
        return {
            "availability": "online",
            "incarnation_id": info.incarnation_id,
            "busy": info.actor_handle.busy,
            "loaded_abilities": info.actor_handle.get_abilities(),
        }

    async def entity_context(
        self, project_id: str, agent_id: str, operation: dict[str, Any]
    ) -> dict[str, Any]:
        """执行身份限定的 Context 操作，不通过 Subject 访问 Actor 私有成员。"""
        from ghrah.communication.errors import AgentNotFoundError
        from ghrah.context.persistence.serialization import serialize_action_session

        self._validate_project(project_id)
        try:
            info = self._require_supervisor()._registry.get_info_by_id(agent_id)
        except AgentNotFoundError as exc:
            raise ValueError("agent_offline") from exc
        actor = info.actor_handle
        kind = operation["type"]
        if kind == "context.list_sessions":
            return actor.get_entity_context()
        if kind == "context.get_history":
            return actor.get_entity_history(
                operation["session_id"], operation["branch_id"], operation["limit"]
            )
        if actor.busy:
            raise ValueError("agent_busy")
        if kind == "context.create_session":
            session = await actor.create_session(
                name=operation.get("name"), system_prompt=operation.get("system_prompt")
            )
            return {"session": serialize_action_session(session)}
        if kind == "context.activate_session":
            return {"session": await actor.activate_session(operation["session_id"])}
        raise ValueError("unsupported_entity_context_operation")

    async def _handle_get_agent_info(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        gp = GetAgentInfoPayload.model_validate(payload)
        self._validate_agent(gp.project_id, gp.agent_id, gp.name)
        agent_handle = await supervisor.get_agent_handle(gp.name)
        state = agent_handle.get_state()
        abilities = agent_handle.get_abilities()
        return self._ok(
            {
                "name": gp.name,
                "state": state,
                "abilities": abilities,
                "available_ability_types": [],
                "context_usage": agent_handle.get_context_usage_snapshot(),
            }
        )

    async def _handle_agent_compact_context(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        """agent_compact_context — 手动触发链上 compact 回合。

        驱动循环活跃时置标志（下一轮触发门消费，回 scheduled）；
        空闲时直接执行一轮 compact（同步等待摘要 LLM 完成，回执含
        node_id）。连续多次请求合并（幂等）。
        """
        supervisor = self._require_supervisor()
        cp = AgentCompactContextPayload.model_validate(payload)
        self._validate_agent(cp.project_id, cp.agent_id, cp.agent_name)
        agent_handle = await supervisor.get_agent_handle(cp.agent_name)
        result = await agent_handle.request_compact()
        return self._ok(result)

    async def _handle_agent_reset(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        """agent_reset — 重置 Agent：新起默认 Session/main Branch 并激活。

        以稳定 ``project_id + agent_id`` 寻址（registry.get_info_by_id），
        不经可重名的局部 name。语义：

        - 就地重置：旧 Session 保留、lifecycle 不动、历史可读；
        - 立即持久化：reset 返回时新 Session/Branch/Root 与 active 指针
          已增量写入 sqlite（不依赖下一次迭代 commit）；
        - 驱动循环进行中显式拒绝（回执 error，不碰运气）；
        - 实体广播复用既有 session_created/session_activated 事件，
          回执 data 仅携带即时反馈（session_id/branch_id/root_node_id）。
        """
        supervisor = self._require_supervisor()
        rp = AgentResetPayload.model_validate(payload)
        self._validate_project(rp.project_id)
        if not rp.agent_id:
            raise ValueError("agent_id required")
        info = supervisor._registry.get_info_by_id(rp.agent_id)
        data = await supervisor.reset_agent(info.name)
        return self._ok(
            {
                "session_id": data["session_id"],
                "branch_id": data["active_branch_id"],
                "root_node_id": data["root_node_id"],
            }
        )

    # ----------------------------------------------------------------
    # execute_ability（本地执行 + ability_result 事件）
    # ----------------------------------------------------------------

    async def _handle_execute_ability(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        """execute_ability — 单体模式本地执行（移植自 router:555 + :393-460）。

        WS 模式下 router 将 execute_ability 转发到 Subject 执行；Unit 形态下
        core 与 subject 同进程，直接经 agent 的 LocalAbilityExecutor 本地执行，
        再把结果转译为 AbilityResultPayload 经 ``core:ability_result`` 事件上抛。

        request_id 关联：优先 ExecuteAbilityPayload.request_id，
        兜底 cmd_ctx.request_id，再兜底自动生成。
        """
        supervisor = self._require_supervisor()
        ep = ExecuteAbilityPayload.model_validate(payload)
        request_id = ep.request_id or getattr(cmd_ctx, "request_id", None) or generate_request_id()
        logger.info(
            "CoreUnit.execute_ability: agent=%s ability=%s request_id=%s",
            ep.agent_name,
            ep.ability_name,
            request_id,
        )

        agent_handle = await supervisor.get_agent_handle(ep.agent_name)
        abilities = getattr(agent_handle, "_abilities", {})
        ability = abilities.get(ep.ability_name)
        if ability is None:
            return self._err(f"Ability '{ep.ability_name}' not found on agent '{ep.agent_name}'")
        executor = getattr(agent_handle, "_ability_executor", None)
        if executor is None:
            return self._err(f"Agent '{ep.agent_name}' has no ability executor")
        if executor.hitl_promise_registry is not self._hitl_promises:
            # 直挂 spawn 旁路了 _apply_hitl_timeout 注入；HITL 请求发出前
            # 补接线，保证 promise 注册进 unit 级表（幂等）
            executor.update_hitl_promise_registry(self._hitl_promises)

        try:
            if supervisor.message_manager is not None:
                from ghrah.context.persistence.serialization import deserialize_action_result

                manager = supervisor.message_manager
                request = await manager.send_external(
                    sender="command",
                    recipient_ids=(agent_handle.config.effective_agent_id,),
                    content=f"Execute {ep.ability_name}",
                    idempotency_key=f"ability:{request_id}",
                    metadata={
                        "execute_ability": {
                            "name": ep.ability_name,
                            "args": ep.tool_args,
                            "call_id": request_id,
                        }
                    },
                )
                replies = await manager.wait_replies(
                    request.message_id, timeout=supervisor._resolve_timeout(ep.agent_name, None)
                )
                action_result = deserialize_action_result(json.loads(replies[0].content))
                await manager.recover_room_projections()
                if ep.ability_name == "send" and action_result.outcome == ActionOutcome.SUCCESS:
                    action_result.data["status"] = "sent"
            else:
                build_call = getattr(agent_handle, "_build_ability_call", None)
                if build_call is None:
                    return self._err(
                        f"Agent '{ep.agent_name}' cannot build an operation call "
                        "(no assembly-bound invocation builder)"
                    )
                call, services = build_call(ability, ep.tool_args, {}, tool_call_id=request_id)
                action_result = await executor.execute_ability(ability, call, services)
            success = action_result.outcome == ActionOutcome.SUCCESS
            error = None if success else str(action_result.data.get("error", "unknown error"))
            result_payload = AbilityResultPayload(
                request_id=request_id,
                agent_name=ep.agent_name,
                ability_name=ep.ability_name,
                success=success,
                result=action_result.data,
                error=error,
            )
        except Exception as e:
            # 对齐 router:457-460 语义：执行异常仅记日志，转译为失败回执
            logger.exception(
                "CoreUnit.execute_ability failed: agent=%s ability=%s request_id=%s",
                ep.agent_name,
                ep.ability_name,
                request_id,
            )
            result_payload = AbilityResultPayload(
                request_id=request_id,
                agent_name=ep.agent_name,
                ability_name=ep.ability_name,
                success=False,
                result=None,
                error=str(e),
            )

        self._emit_event(EventType.ABILITY_RESULT.value, result_payload.model_dump())
        return self._ok(result_payload.model_dump())

    # ----------------------------------------------------------------
    # HITL 命令
    # ----------------------------------------------------------------

    async def _handle_hitl_response(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any] | None:
        """hitl_response — 审批响应解析（promise 优先，三元组回退）。

        promise 路径：凭 promise_id 从 unit 级注册表翻译回
        (agent_name, ability_name, tool_call_id) 再 resolve——真实链路
        Observer 只回传 promise_id。三元组路径：直接按单体字段集定位
        （存量调用方兼容）。两路皆 miss 时显式失败（不 resolve 任何
        future，不伪装成功）；拒绝时把 UI 收集的 reason 并入
        HITLResult.result 透传给 LLM。resolve 成功后发布
        hitl_resolved 事件（Observer 据此清除审批卡片）。

        多 cluster 穿透：promise 注册表是 unit 级单例，promise 归属唯一
        持有它的 CoreUnit；本 unit 未持有且无三元组回退时返回 ``None``
        穿透给下一个 CoreUnit（宿主 serial「返回 None = 不是我的」约定），
        而非以错误回执短路命令链——否则先挂载的 cluster 会拦截其它
        cluster 的审批响应。全部 miss 时由宿主兜底 Unknown command。
        """
        supervisor = self._require_supervisor()
        hp = HITLResponsePayload.model_validate(payload)

        # promise 优先：注册表翻译回三元组（payload 三元组字段缺省时兜底）。
        agent_name = hp.agent_name
        ability_name = hp.ability_name
        tool_call_id = hp.tool_call_id
        promise_id = hp.promise_id
        if promise_id:
            triplet = self._hitl_promises.resolve(promise_id)
            if triplet is None and not (agent_name and ability_name and tool_call_id):
                logger.debug(
                    "hitl_response: promise '%s' not held by cluster '%s'; "
                    "passing through to next CoreUnit",
                    promise_id,
                    self._config.cluster_id,
                )
                return None
            if triplet is not None:
                agent_name, ability_name, tool_call_id = triplet

        agent_handle = await supervisor.get_agent_handle(agent_name)

        # 拒绝理由（UI 收集）并入审批附加结果透传给 LLM
        result: Any = hp.result
        if not hp.approved and hp.reason and result is None:
            result = {"reason": hp.reason}

        executor = getattr(agent_handle, "_ability_executor", None)
        if executor is not None and hasattr(executor, "receive_hitl_response"):
            resolved = executor.receive_hitl_response(
                ability_name=ability_name,
                tool_call_id=tool_call_id,
                approved=hp.approved,
                result=result,
            )
        else:
            await agent_handle.receive_hitl_response(
                ability_name=ability_name,
                tool_call_id=tool_call_id,
                approved=hp.approved,
                result=result,
            )
            resolved = True

        if resolved:
            self._emit_event(
                EventType.HITL_RESOLVED.value,
                {
                    "agent_name": agent_name,
                    "promise_id": promise_id,
                    "ability_name": ability_name,
                    "tool_call_id": tool_call_id,
                    "status": "approved" if hp.approved else "rejected",
                },
            )

        return self._ok({"resolved": bool(resolved)})

    # ----------------------------------------------------------------
    # Session 命令
    # ----------------------------------------------------------------

    async def _handle_session_create(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        sp = SessionCreatePayload.model_validate(payload)
        self._validate_agent(sp.project_id, sp.agent_id, sp.agent_name)
        data = await supervisor.create_session(
            agent_name=sp.agent_name,
            name=sp.name,
            origin_session_id=sp.origin_session_id,
            origin_node_id=sp.origin_node_id,
            system_prompt=sp.system_prompt,
            metadata=sp.metadata,
        )
        return self._ok(data)

    async def _handle_session_activate(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        sp = SessionActivatePayload.model_validate(payload)
        self._validate_agent(sp.project_id, sp.agent_id, sp.agent_name)
        data = await supervisor.activate_session(agent_name=sp.agent_name, session_id=sp.session_id)
        return self._ok(data)

    async def _handle_session_list(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        sp = SessionListPayload.model_validate(payload)
        self._validate_agent(sp.project_id, sp.agent_id, sp.agent_name)
        data = await supervisor.list_sessions(sp.agent_name)
        return self._ok(data)

    async def _handle_session_archive(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        sp = SessionArchivePayload.model_validate(payload)
        self._validate_agent(sp.project_id, sp.agent_id, sp.agent_name)
        await supervisor.archive_session(agent_name=sp.agent_name, session_id=sp.session_id)
        return self._ok({"session_id": sp.session_id, "archived": True})

    async def _handle_session_delete(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        sp = SessionDeletePayload.model_validate(payload)
        self._validate_agent(sp.project_id, sp.agent_id, sp.agent_name)
        await supervisor.delete_session(agent_name=sp.agent_name, session_id=sp.session_id)
        return self._ok({"session_id": sp.session_id, "deleted": True})

    async def _handle_branch_create(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        bp = BranchCreatePayload.model_validate(payload)
        self._validate_agent(bp.project_id, bp.agent_id, bp.agent_name)
        data = await supervisor.create_branch(
            agent_name=bp.agent_name,
            session_id=bp.session_id,
            name=bp.name,
            from_node_id=bp.from_node_id,
            parent_branch_id=bp.parent_branch_id,
            metadata=bp.metadata,
        )
        return self._ok(data)

    async def _handle_branch_activate(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        bp = BranchActivatePayload.model_validate(payload)
        self._validate_agent(bp.project_id, bp.agent_id, bp.agent_name)
        data = await supervisor.activate_branch(bp.agent_name, bp.session_id, bp.branch_id)
        return self._ok(data)

    async def _handle_branch_list(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        bp = BranchListPayload.model_validate(payload)
        self._validate_agent(bp.project_id, bp.agent_id, bp.agent_name)
        data = await supervisor.list_branches(bp.agent_name, bp.session_id)
        return self._ok(data)

    async def _handle_branch_archive(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        bp = BranchArchivePayload.model_validate(payload)
        self._validate_agent(bp.project_id, bp.agent_id, bp.agent_name)
        await supervisor.archive_branch(bp.agent_name, bp.session_id, bp.branch_id)
        return self._ok({"session_id": bp.session_id, "branch_id": bp.branch_id, "archived": True})

    async def _handle_branch_delete(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        bp = BranchDeletePayload.model_validate(payload)
        self._validate_agent(bp.project_id, bp.agent_id, bp.agent_name)
        await supervisor.delete_branch(bp.agent_name, bp.session_id, bp.branch_id)
        return self._ok({"session_id": bp.session_id, "branch_id": bp.branch_id, "deleted": True})


def create_core_unit(config: CoreUnitConfig) -> CoreUnit:
    """创建 CoreUnit 实例（工厂函数）。

    Args:
        config: Unit 配置

    Returns:
        CoreUnit 实例（尚未 init）
    """
    return CoreUnit(config)
