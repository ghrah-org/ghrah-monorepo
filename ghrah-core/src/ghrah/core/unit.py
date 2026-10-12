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

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, cast

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
    RegisterAbilityPayload,
    SendMessagePayload,
    SessionActivatePayload,
    SessionArchivePayload,
    SessionCreatePayload,
    SessionDeletePayload,
    SessionListPayload,
    SpawnAgentPayload,
    TerminateAgentPayload,
    UnregisterAbilityPayload,
    generate_request_id,
)

from ghrah.abilities.context import AbilityExecutionContext
from ghrah.communication.errors import RegistryError
from ghrah.communication.supervisor import SupervisorActor
from ghrah.core.ability_protocol import AbilityProtocol
from ghrah.core.config.builders import (
    build_context_from_dict,
    build_model_overrides_from_dict,
    build_window_from_dict,
)
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
        persistence_factory: per-agent 持久化后端工厂
            （``(AgentConfig) -> PersistenceBackend | None``；None = Core
            内建 sqlite）。新 spawn 生效，存量 agent 不受影响。
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
        default_abilities: spawn 未显式传 abilities 时注入的能力名列表
            （透传 SupervisorActor.default_abilities——部署方显式声明，
            非隐式兜底；None = 未配置，spawn(None) raise）。
        max_cluster_members: 集群成员上限（spawn 时超限 raise；
            透传 SupervisorActor.max_cluster_members，<=0 表示不限制）。
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
    persistence_factory: Callable[[Any], Any] | None = None
    # per-cluster LLM 工厂注入（(AgentConfig) -> LLMProtocol；None = Core 内建
    # 默认，即 agentconf 解析）。与 persistence_factory 同范式：部署方显式
    # 声明 LLM 出口（网关/凭据/计量包装），注入即全责（不再套用
    # _default_llm_factory 的 model_overrides 逻辑，由注入方自理）。
    llm_factory: Callable[[Any], Any] | None = None
    # duck-typed ManifestStoreProtocol；None = standalone（manifest_ref spawn 报错）
    manifest_store: Any = None
    default_abilities: tuple[str, ...] | None = None
    max_cluster_members: int = 20


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


# ----------------------------------------------------------------
# Ability 实例化（移植自旧 WS 服务器形态的 router::_create_ability_from_def）
# ----------------------------------------------------------------


def _agent_config_to_wire(config: AgentConfig) -> dict[str, Any]:
    """AgentConfig → wire 事件 dict（对齐 AgentConfigPayload 字段，供 AGENT_SPAWNED 事件）。"""
    import dataclasses

    return {
        "agent_id": config.effective_agent_id,
        "name": config.name,
        "agent_config_name": config.agent_config_name,
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


def _create_ability_from_def(
    ability_def: Any,
    *,
    command_runner: Any = None,
    auto_approve_abilities: tuple[str, ...] = (),
    require_approval_by_default: bool = True,
    safe_extra_commands: tuple[str, ...] = (),
    safe_extra_sub_commands: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> AbilityProtocol:
    """将 AbilityDefinitionPayload 转换为 Ability 实例。

    与原 server/router.py 的 ``_create_ability_from_def`` 共享同一套常量与
    AbilityRegistry.create 工厂：
    1. 文件系统类（FS_ABILITY_TYPES）：将 require_hitl/allowed_paths/
       denied_paths/workspace_root 原始字段转换为 FSPermissionChecker。
    2. execute_command：将 require_approval 标记转换为
       CommandSafetyChecker + CommandApprovalHook 注入构造函数。
    3. end_task：统一 mode="toolcall"。

    HITL 运行时覆盖层（语义平移自 subject 侧旧 HITLPolicy）：
    - ``auto_approve_abilities`` 白名单 → 强制免审批（覆盖 manifest
      require_hitl=True）；
    - manifest 未声明审批字段时 → ``require_approval_by_default`` 兜底。
    决策优先级：auto_approve > manifest 显式声明 > 兜底默认。

    ``command_runner`` 非 None 时注入 execute_command（Subject 挂载模式的
    SandboxExecutor；None = standalone 直跑 subprocess）。
    """
    from ghrah.abilities import (
        FS_ABILITY_TYPES,
        AbilityRegistry,
        AccessApprovalHook,
        CommandApprovalHook,
        CommandSafetyChecker,
        FSPermissionChecker,
    )

    params = dict(ability_def.params) if ability_def.params else {}
    ability_type = ability_def.ability_type
    auto_approved = ability_type in auto_approve_abilities

    _fs_permission_keys = {"require_hitl", "allowed_paths", "denied_paths", "workspace_root"}

    if ability_type in FS_ABILITY_TYPES and _fs_permission_keys & set(params.keys()):
        fs_params = {k: params.pop(k) for k in _fs_permission_keys if k in params}
        allowed_paths = fs_params.get("allowed_paths")
        denied_paths = fs_params.get("denied_paths")
        workspace_root = fs_params.get("workspace_root")
        # HITL 覆盖层：auto_approve > manifest require_hitl > 兜底默认
        if auto_approved:
            require_approval = False
        elif "require_hitl" in fs_params and isinstance(fs_params["require_hitl"], bool):
            require_approval = fs_params["require_hitl"]
        else:
            require_approval = require_approval_by_default
        checker = FSPermissionChecker(
            allowed_paths=allowed_paths,
            workspace_root=workspace_root,
            denied_paths=denied_paths,
            require_approval=require_approval,
        )
        params["permission_checker"] = checker
        # require_hitl 声明必须经 PRE_EXECUTE Hook 生效：FS ability 内联检查
        # 只区分允许/拒绝，(True, "pending") 需由此 Hook 转为 HITL 等待，
        # 否则白名单外访问会被静默放行（审批断链）。require_approval=False
        # 时不挂——白名单外维持直接拒绝，不产生审批卡。
        if require_approval:
            params["hooks"] = [AccessApprovalHook(checker)]

    if ability_type == "execute_command":
        # HITL 覆盖层：auto_approve > manifest require_approval > 兜底默认
        manifest_requirement = params.pop("require_approval", None)
        if auto_approved:
            require_approval = False
        elif manifest_requirement is not None:
            require_approval = bool(manifest_requirement)
        else:
            require_approval = require_approval_by_default
        command_checker = CommandSafetyChecker(require_approval=require_approval)
        # 部署方 safe 白名单扩展（只增不减，与内置并集）
        if safe_extra_commands or safe_extra_sub_commands:
            sub_map = dict(safe_extra_sub_commands) if safe_extra_sub_commands else None
            command_checker.extend_safe_lists(
                extra_commands=safe_extra_commands,
                extra_sub_commands=sub_map,
            )
        params["command_checker"] = command_checker
        params["hooks"] = [CommandApprovalHook(command_checker)]
        if command_runner is not None:
            params["command_runner"] = command_runner

    if ability_type == "end_task":
        params["mode"] = "toolcall"

    return AbilityRegistry.create(ability_type, **params)


def _materialize_command_proxy(
    params: dict[str, Any],
    subject_bridge: SubjectCommandBridge | None,
    *,
    auto_approve_abilities: tuple[str, ...] = (),
) -> AbilityProtocol:
    """command_proxy descriptor → Ability 实例（Core 侧物化）。

    过渡形态（REGISTER_ABILITY params 通道）的物化入口，三段职责：

    1. descriptor 校验：``CommandProxyDescriptor`` pydantic 模型
       （``extra="forbid"``，未知字段拒绝，hooks 类字段无从混入）；
    2. 强制闸门：HITL 默认开启，仅 ``auto_approve_abilities`` 白名单
       可关闭；descriptor 的 ``require_hitl`` 声明不具自批效力
       （挂载面声明不得削弱部署方信任闸）；
    3. 非白名单包装：``HITLRequiredAbility``（内核固定实现，复用自
       materialize 物化器的通用 HITL 接线）。

    Args:
        params: REGISTER_ABILITY 命令的 ``ability.params``（descriptor dict）
        subject_bridge: 宿主命令面桥（standalone 时为 None——工具执行
            时明确报错，注册不拒绝）
        auto_approve_abilities: 部署方强制放行的能力名白名单

    Returns:
        已物化的 Ability 实例（非白名单时含 HITL 包装）

    Raises:
        pydantic.ValidationError: descriptor 不合法（未知字段/缺必填/
            非法值）
    """
    from ghrah.abilities.builtin.command_proxy import CommandProxyDescriptor
    from ghrah.abilities.hitl import HITLRequiredAbility
    from ghrah.abilities.registry import AbilityRegistry

    descriptor = CommandProxyDescriptor.model_validate(params)
    ability = AbilityRegistry.create(
        "command_proxy",
        tool_name=descriptor.tool_name,
        description=descriptor.description,
        parameters=descriptor.parameters,
        bridge=subject_bridge,
        command=descriptor.command,
        timeout=descriptor.timeout,
    )
    if descriptor.tool_name in auto_approve_abilities:
        return ability
    return HITLRequiredAbility(
        ability,
        ability_name=descriptor.tool_name,
        reason=(
            f"command_proxy '{descriptor.command}' requires HITL approval (plugin-provided ability)"
        ),
    )


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

    # ----------------------------------------------------------------
    # 生命周期
    # ----------------------------------------------------------------

    async def init(self, ctx: Any) -> None:
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

        # Subject command bridge（command_proxy 工具回路）：同一 serial 桥接
        # 协议命令面（standalone 场景无 serial → bridge 为 None，工具执行
        # 时明确报错，注册不拒绝——对齐 send/ROOM_BRIDGE_UNAVAILABLE 模式）
        self._subject_bridge: SubjectCommandBridge | None = (
            SubjectCommandBridge(serial) if callable(serial) else None
        )

        supervisor = SupervisorActor(
            default_timeout=self._config.default_timeout,
            cluster_id=self._config.cluster_id,
            event_publisher=self._publisher,
            room_bridge=room_bridge,
            default_abilities=(
                list(self._config.default_abilities)
                if self._config.default_abilities is not None
                else None
            ),
            max_cluster_members=self._config.max_cluster_members,
            manifest_store=self._config.manifest_store,
            llm_factory=self._config.llm_factory,
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

        await supervisor.shutdown()
        logger.info("CoreUnit stopped")

    # ----------------------------------------------------------------
    # 命令/事件分发（bridge 契约）
    # ----------------------------------------------------------------

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        """分发命令到对应 handler。

        Args:
            command: 命令名（CommandType 的 .value 字符串）
            payload: 命令载荷
            cmd_ctx: 命令上下文（subject 侧占位对象，视为 Any）

        Returns:
            回执字典 ``{"success": bool, "data": ..., "error": ...}``
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

        SessionInfoAbility 经 AbilityRegistry 裸实例化（无参），静态部署
        快照由本方法在 spawn 装配期回填——快照单一真源在
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

    async def _handle_spawn_agent(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        """spawn_agent — 接收 SpawnAgentPayload。

        manifest_ref 路径：CoreUnit 内解析 manifest 并直接构造 Ability
        （对齐独立库 ``examples/agents_manifest/runner`` 范式，不经 wire
        DTO 往返）；无 manifest_ref 时消费 Observer 直传的
        ``AbilityDefinitionPayload`` 列表（真跨 WS 路径，wire DTO 合理存在）。
        """
        from ghrah.manifest.materialize import instantiate_resolved_abilities
        from ghrah.manifest.resolver import ManifestResolver

        supervisor = self._require_supervisor()
        sp = SpawnAgentPayload.model_validate(payload)
        self._validate_project(sp.project_id)
        if sp.cluster_id and sp.cluster_id != self._config.cluster_id:
            raise ValueError("cluster_id_mismatch")
        logger.info(f"CoreUnit.spawn_agent: name={sp.config.name}")

        # ── manifest_ref 路径：CoreUnit 内解析 + 直接构造 Ability（不经 wire DTO） ──
        if sp.manifest_ref:
            if self._config.manifest_store is None:
                return self._err("manifest_store not available (standalone)")
            try:
                manifest = self._config.manifest_store.load_agent(sp.manifest_ref)
                resolved = ManifestResolver(self._config.manifest_store).resolve(
                    manifest, runtime_name=sp.config.name or None
                )
            except Exception as e:
                logger.exception("CoreUnit.spawn_agent: manifest resolve failed: %s", e)
                return self._err(f"manifest resolve failed for '{sp.manifest_ref}': {e}")
            ability_instances: list[AbilityProtocol] | None = instantiate_resolved_abilities(
                resolved.abilities,
                workspace_root=self._config.workspace_root,
                auto_approve_abilities=self._config.auto_approve_abilities,
                require_approval_by_default=self._config.require_approval_by_default,
                command_runner=self._config.command_runner,
                safe_extra_commands=self._config.safe_extra_commands,
                safe_extra_sub_commands=self._config.safe_extra_sub_commands,
            )
            if not ability_instances:
                # fail-closed：manifest 全 non-builtin/解析出 0 能力时明确报错，
                # 不落入 default_abilities 或任何隐式注入（零隐式）。
                return self._err(
                    f"manifest '{sp.manifest_ref}' resolved 0 abilities for agent "
                    f"'{sp.config.name}' — fix the manifest or spawn with explicit "
                    f"abilities; refusing implicit defaults"
                )
            try:
                resolved_config = replace(
                    resolved.config,
                    agent_id=sp.config.agent_id,
                )
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

        # ── 直传 abilities 路径（无 manifest_ref，Observer 直传 wire DTO） ──
        # None → default_abilities（CoreUnitConfig 显式配置）；未配置 → raise。
        # 空 abilities 列表在 wire 上不可能出现（AbilityDefinitionPayload
        # 非 None 即非空），防御性映射为明确报错而非隐式注入。
        core_config = AgentConfig(
            name=sp.config.name,
            agent_id=sp.config.agent_id,
            agent_config_name=sp.config.agent_config_name,
            description=sp.config.description,
            system_prompt=sp.config.system_prompt,
            max_iterations=sp.config.max_iterations,
            communication_timeout=sp.config.communication_timeout,
            window=(build_window_from_dict(sp.config.window) if sp.config.window else None),
            context=(build_context_from_dict(sp.config.context) if sp.config.context else None),
            model_overrides=(
                build_model_overrides_from_dict(sp.config.model_overrides)
                if sp.config.model_overrides
                else None
            ),
            workspace_root=self._config.workspace_root,
        )
        # 环境快照在 abilities 实例化后组装（需从 FS checker 收集授权根）；
        # 注入开关关闭时 session_info 工具同样拿不到快照——快照仅此一处组装。

        ability_instances = None
        if sp.abilities:
            ability_instances = []
            for ability_def in sp.abilities:
                try:
                    ability = _create_ability_from_def(
                        ability_def,
                        command_runner=self._config.command_runner,
                        auto_approve_abilities=self._config.auto_approve_abilities,
                        require_approval_by_default=(self._config.require_approval_by_default),
                        safe_extra_commands=self._config.safe_extra_commands,
                        safe_extra_sub_commands=self._config.safe_extra_sub_commands,
                    )
                    ability_instances.append(ability)
                except KeyError as e:
                    return self._err(
                        f"Unknown ability type '{ability_def.ability_type}' "
                        f"for agent '{sp.config.name}': {e}"
                    )
                except (TypeError, ValueError) as e:
                    return self._err(
                        f"Invalid params for ability '{ability_def.ability_type}' "
                        f"for agent '{sp.config.name}': {e}"
                    )
        # 直传路径的快照组装晚于 abilities 实例化（checker 授权根可收集），
        # 与 manifest_ref 路径共用 _inject_session_info_snapshot。
        if self._config.environment_injection and ability_instances is not None:
            core_config = replace(
                core_config,
                environment_context_injection=True,
                environment_info=self._environment_snapshot(ability_instances),
            )
            self._inject_session_info_snapshot(ability_instances, core_config)

        try:
            agent_name = await supervisor.spawn_agent(
                core_config,
                abilities=ability_instances,
                persistence_factory=self._config.persistence_factory,
                llm_factory=self._config.llm_factory,
            )
        except RegistryError as e:
            return self._err(str(e))

        recovery_mode = supervisor.recovery_mode(agent_name)
        incarnation_id = supervisor.incarnation_id(agent_name)
        self._apply_hitl_timeout(agent_name)

        config_dict = sp.config.model_dump()
        self._emit_event(
            EventType.AGENT_SPAWNED.value,
            {
                "name": agent_name,
                "agent_id": core_config.effective_agent_id,
                "config": config_dict,
                "recovery_mode": recovery_mode,
                "incarnation_id": incarnation_id,
            },
        )
        return self._ok(
            {
                "name": agent_name,
                "agent_id": core_config.effective_agent_id,
                "recovery_mode": recovery_mode,
                "incarnation_id": incarnation_id,
            }
        )

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
        result = await supervisor.send(
            target=sp.target,
            content=sp.content,
            sender=sp.sender,
            timeout=sp.timeout,
            metadata=sp.metadata,
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
        supervisor = self._require_supervisor()
        rp = RegisterAbilityPayload.model_validate(payload)
        self._validate_agent(rp.project_id, rp.agent_id, rp.agent_name)
        if rp.ability.ability_type == "command_proxy":
            # 插件能力桥：descriptor 校验 + 强制闸门在物化器内完成，
            # 实例经专用通道注册（不经 params 工厂）
            ability = _materialize_command_proxy(
                rp.ability.params,
                self._subject_bridge,
                auto_approve_abilities=self._config.auto_approve_abilities,
            )
            result = await supervisor.register_ability_instance_for_agent(rp.agent_name, ability)
        else:
            result = await supervisor.register_ability_for_agent(
                agent_name=rp.agent_name,
                ability_type=rp.ability.ability_type,
                ability_params=rp.ability.params,
            )
        return self._ok({"agent_name": rp.agent_name, "ability": result, "registered": True})

    async def _handle_unregister_ability(
        self, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        supervisor = self._require_supervisor()
        up = UnregisterAbilityPayload.model_validate(payload)
        self._validate_agent(up.project_id, up.agent_id, up.agent_name)
        agent_handle = await supervisor.get_agent_handle(up.agent_name)
        await agent_handle.unregister_ability(up.ability_name)
        return self._ok(
            {
                "agent_name": up.agent_name,
                "ability": up.ability_name,
                "unregistered": True,
            }
        )

    # ----------------------------------------------------------------
    # 查询命令
    # ----------------------------------------------------------------

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
            context = AbilityExecutionContext(
                current_ability_name=ep.ability_name,
                tool_args=ep.tool_args,
                context_manager=getattr(agent_handle, "_context_manager", None),
                supervisor=cast(Any, supervisor),
                agent_name=ep.agent_name,
                manifest_store=getattr(supervisor, "manifest_store", None),
            )
            action_result = await executor.execute_ability(ability, context)
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

    async def _handle_hitl_response(self, payload: dict[str, Any], cmd_ctx: Any) -> dict[str, Any]:
        """hitl_response — 审批响应解析（promise 优先，三元组回退）。

        promise 路径：凭 promise_id 从 unit 级注册表翻译回
        (agent_name, ability_name, tool_call_id) 再 resolve——真实链路
        Observer 只回传 promise_id。三元组路径：直接按单体字段集定位
        （存量调用方兼容）。两路皆 miss 时显式失败（不 resolve 任何
        future，不伪装成功）；拒绝时把 UI 收集的 reason 并入
        HITLResult.result 透传给 LLM。resolve 成功后发布
        hitl_resolved 事件（Observer 据此清除审批卡片）。
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
                return self._err("HITL promise not found or expired")
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
