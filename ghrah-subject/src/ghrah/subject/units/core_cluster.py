# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Core cluster registry built-in Subject unit.

持有 :class:`CoreClusterRegistry`（cluster = CoreUnit 实例注册表，进程内
运行时挂载），提供 ``CORE_CLUSTER_REGISTRY`` 服务。``project`` /
``recovery`` 经其 ensure/get handle 消费 spawn/list/terminate/shutdown
语义（对齐旧 CLUSTER_TRANSPORT_MANAGER 消费面，零改动平移）。

``init`` 经 ctx 取 ``MANIFEST_STORE`` + ``WORKSPACE_SERVICE`` 构造 spawn
物化器（manifest_ref 展开 + 权限物化），随 registry 注入 handle。
无命令路由（cluster 命令 = registry 操作，经 project/recovery 消费）。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any

from ghrah.protocol.types import CommandType, ProjectStatus

from ghrah.subject.config import SubjectConfig
from ghrah.subject.core_cluster.registry import default_core_unit_factory
from ghrah.subject.errors import StableError
from ghrah.subject.room.content_blocks import (
    CONTENT_TEXT_REQUIRED,
    resolve_content_attachments,
    validate_content_blocks,
)
from ghrah.subject.runtime.service_keys import (
    CORE_CATALOG_SERVICE,
    CORE_CLUSTER_REGISTRY,
    CORE_PORT_SERVICE,
    PROJECT_STORE,
    SANDBOX_EXECUTOR,
    WORKSPACE_SERVICE,
)
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta

__all__ = ["CoreClusterRegistryUnit"]

logger = logging.getLogger(__name__)

_TURN_COMMANDS = frozenset({CommandType.TURN_ABORT.value, CommandType.TURN_ROLLBACK.value})

_PROJECT_AGENT_COMMANDS = _TURN_COMMANDS | frozenset(
    {
        "spawn_agent",
        "terminate_agent",
        "send_message",
        "broadcast_message",
        "register_ability",
        "unregister_ability",
        "list_agents",
        "delegate",
        "get_agent_info",
        "agent_compact_context",
        "agent_reset",
        "session_create",
        "session_activate",
        "session_list",
        "session_archive",
        "session_delete",
        "branch_create",
        "branch_activate",
        "branch_list",
        "branch_archive",
        "branch_delete",
    }
)

# HITL 审批回程：命令不带 project_id（Observer 只凭 promise_id 回发），
# 经端口实例扇出到所有已启动单元，由持有 promise 的单元认领。
_HITL_RESPONSE_COMMAND = "hitl_response"


def _room_missing(error: str) -> bool:
    """房间不存在属永久条件，不重试（删除是 Subject 的权威决定）。"""
    text = error.strip().lower()
    return text.startswith("room not found") or "room_not_found" in text


_TARGET_FIELDS = {
    "terminate_agent": "name",
    "send_message": "target",
    "get_agent_info": "name",
    "agent_compact_context": "agent_name",
    # agent_reset 以 project_id + agent_id 稳定寻址（AgentResetPayload 无 name
    # 字段）；目标字段置空串 = 仅按 agent_id 解析，不校验可重名 name。
    "agent_reset": "",
    CommandType.TURN_ABORT.value: "agent_name",
    CommandType.TURN_ROLLBACK.value: "agent_name",
    "session_create": "agent_name",
    "session_activate": "agent_name",
    "session_list": "agent_name",
    "session_archive": "agent_name",
    "session_delete": "agent_name",
    "branch_create": "agent_name",
    "branch_activate": "agent_name",
    "branch_list": "agent_name",
    "branch_archive": "agent_name",
    "branch_delete": "agent_name",
}

# 会挂载/复活 cluster 或向运行时投递消息的命令：Project 非 ACTIVE 时拒绝，
# 防止 stopped/paused Project 被一条消息惰性复活（spawn 的 ACTIVE 校验同源）。
_RUNTIME_REVIVING_COMMANDS = frozenset(
    {
        "spawn_agent",
        "send_message",
        "broadcast_message",
        "delegate",
        "agent_compact_context",
        "agent_reset",
        "session_create",
        "session_activate",
        "session_archive",
        "session_delete",
        "branch_create",
        "branch_activate",
        "branch_archive",
        "branch_delete",
    }
)

# cluster 未挂载时无需（也不得）挂载即可完成的命令：读定义态或收敛性终止。
# stopped/archived 后 cluster 已 dispose，这些命令从 Project AgentSpec 兜底。
# 注意：cluster 已登记不等于有在线实例（spawn 失败前就 ensure 过 cluster），
# 因此 get_agent_info 在挂载状态下还会先探测 runtime，离线同样走定义态。
_NO_MOUNT_COMMANDS = _TURN_COMMANDS | frozenset(
    {"get_agent_info", "session_list", "terminate_agent"}
)


def _is_version_conflict(project_id: str, error: str) -> bool:
    """识别 project_add_agent 回执中的乐观锁冲突文案（store 抛错的透传）。"""
    return (
        f"Project {project_id} modified" in error
        or "concurrent modification during update" in error
    )


def _agent_offline_error(error: Any) -> bool:
    """识别端口 ``agents.get`` 的「不在线」失败（``ApiError`` NOT_FOUND 文案）。"""
    text = str(error or "")
    return "NOT_FOUND" in text or "not_found" in text or "not online" in text


class CoreClusterRegistryUnit(SubjectUnit):
    """装配 Core 端口和 Subject 命令适配器。"""

    def __init__(
        self,
        config: SubjectConfig,
        *,
        unit_factory: Callable[[str, str, str], Any] | None = None,
        llm_factory: Callable[[Any], Any] | None = None,
        storage_factory: Callable[[str, str], Any] | None = None,
        ability_db_path: Callable[[str, str], str | None] | None = None,
    ) -> None:
        self._config = config
        # 测试可注入假工厂；生产默认工厂在 init 时构造（需 ctx 取 MANIFEST_STORE
        # 注入 CoreUnitConfig.manifest_store，对齐 Core 独立库 runner 范式）。
        self._unit_factory = unit_factory
        # 可选 per-cluster LLM 工厂透传（None = Core 内建默认 agentconf 解析）；
        # 显式 unit_factory 优先，本参数被忽略（工厂自持全部 CoreUnitConfig）。
        self._llm_factory = llm_factory
        self._storage_factory = storage_factory
        self._ability_db_path = ability_db_path
        self._transport: Any | None = None
        self._ports: Any | None = None
        self._catalog: Any | None = None
        self._ctx: Any | None = None
        self._meta = UnitMeta(
            name="core_cluster_registry",
            requires=frozenset({WORKSPACE_SERVICE, SANDBOX_EXECUTOR}),
            provides=frozenset({CORE_CATALOG_SERVICE, CORE_CLUSTER_REGISTRY, CORE_PORT_SERVICE}),
            routes=RouteSpec(commands=_PROJECT_AGENT_COMMANDS | {_HITL_RESPONSE_COMMAND}),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def service(self) -> Any:
        """当前主干传输层：命令路由走 Core 能力端口。"""
        if self._transport is None:
            raise RuntimeError("CoreClusterRegistryUnit has not been initialized.")
        return self._transport

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        if self._unit_factory is None:
            # 生产默认工厂：项目级装配（持久化 / 房间投影 / shell 兜底）。
            async def project_room(message: Any) -> None:
                rooms = message.metadata.get("room_ids") or [message.metadata.get("room_id")]
                for room_id in rooms:
                    if not room_id:
                        continue
                    result = await ctx.serial(
                        "command/room_send",
                        {
                            "room_id": room_id,
                            "author": message.sender_id.removeprefix("human:")
                            if message.external_sender
                            else message.sender_id,
                            "author_type": "human" if message.external_sender else "agent",
                            "data": {"mm_message_id": message.message_id},
                        },
                    )
                    if isinstance(result, dict) and result.get("success"):
                        continue
                    error = (
                        str((result or {}).get("error") or "")
                        if isinstance(result, dict)
                        else str(result)
                    )
                    if _room_missing(error):
                        # 房间删除是 Subject 的权威决定；历史消息的 room_ids 是不可变
                        # 事实，重放投影不得复活已删除的房间，也不该当作可重试错误。
                        logger.info(
                            "room projection skipped (room deleted): %s -> %s",
                            message.message_id,
                            room_id,
                        )
                        continue
                    raise RuntimeError(error or str(result))

            async def shell_cwd_lookup(project_id: str) -> str | None:
                # Project unit 在本 unit 之后才 provide store；读取发生在命令执行时。
                try:
                    project_store = ctx.get(PROJECT_STORE.name)
                except Exception:
                    return None
                if project_store is None:
                    return None
                try:
                    record = await project_store.get(project_id)
                except Exception:
                    logger.warning("shell cwd lookup failed for project %s", project_id)
                    return None
                if record is None:
                    return None
                path = str(getattr(record, "shell_cwd", "") or "").strip()
                return path or None

            async def shell_allow_lookup(project_id: str) -> list[dict[str, str]]:
                """每次现读当前项目记忆，不经 HTTP，也不复制到启动配置。"""
                project_store = ctx.get(PROJECT_STORE.name)
                try:
                    record = await project_store.get(project_id)
                except sqlite3.Error, OSError:
                    logger.warning(
                        "shell allow lookup failed for project %s", project_id, exc_info=True
                    )
                    return []
                if record is None:
                    return []
                return [entry.model_dump(mode="json") for entry in record.shell_allow]

            self._unit_factory = default_core_unit_factory(
                self._config,
                llm_factory=self._llm_factory,
                room_projector=project_room,
                shell_cwd_lookup=shell_cwd_lookup,
                shell_allow_lookup=shell_allow_lookup,
            )
        from ghrah.core.wiring import build_core_catalog, build_core_port
        from ghrah.protocol.ports.keys import CORE_CATALOG, CORE_PORT

        from ghrah.subject.project.paths import ProjectPaths
        from ghrah.subject.runtime.core_ports import CorePorts

        def _locator_for(project_id: str) -> str | None:
            """取已登记的项目根定位符（由 cluster 登记提供）。"""
            if self._transport is not None:
                return self._transport.locator_for(project_id)
            return None

        def _build_config(project_id: str, locator: str) -> Any:
            """Core 单元配置：复用 legacy 工厂的项目级装配（含 manifest_store）。"""
            build = getattr(self._unit_factory, "build_config", None)
            if build is None:
                raise ValueError("core unit factory 未提供 build_config")
            return build(project_id, locator)

        def _make_port(project_id: str) -> Any:
            """按项目构造作用域端口实例（数据根 + 不透明作用域 token）。"""
            locator = _locator_for(project_id)
            if not locator:
                raise ValueError(f"project locator unavailable: {project_id}")
            paths = ProjectPaths.from_locator(locator)
            adopt_legacy_chain = (
                self._storage_factory is None and paths.permits_legacy_chain_adoption(project_id)
            )
            return build_core_port(
                scope_id=project_id,
                db_path=str(paths.action_chain_db_path),
                storage_factory=(
                    (lambda scope_id: self._storage_factory(scope_id, locator))
                    if self._storage_factory
                    else None
                ),
                adopt_legacy_chain=adopt_legacy_chain,
                ability_db_path=(
                    self._ability_db_path(project_id, locator) if self._ability_db_path else None
                ),
                config_factory=lambda: _build_config(project_id, locator),
                install_root=self._config.ability.install_root,
                agentconf_db_path=self._config.agentconf_db_path,
                grant_policy=self._config.ability_grant_policy,
                emit=ctx.emit,
                serial=ctx.serial,
            )

        # 按项目懒建/缓存作用域端口；目录端口无作用域，零项目时也可用。
        self._ports = CorePorts(_make_port)
        self._catalog = build_core_catalog(
            install_root=self._config.ability.install_root,
            agentconf_db_path=self._config.agentconf_db_path,
            grant_policy=self._config.ability_grant_policy,
        )
        ctx.provide(CORE_PORT, self._ports)
        ctx.provide(CORE_CATALOG, self._catalog)
        from ghrah.subject.runtime.port_transport import PortClusterTransport

        self._transport = PortClusterTransport(self._ports)
        ctx.provide(CORE_CLUSTER_REGISTRY.name, self._transport)

    async def stop(self) -> None:
        if self._transport is not None:
            await self._transport.stop()
        if self._catalog is not None:
            await self._catalog.aclose()
            self._catalog = None
        self._ports = None

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        if command in {"register_ability", "unregister_ability"}:
            return {
                "success": False,
                "data": None,
                "error": "legacy_ability_registration_disabled",
                "error_detail": "Manage grants and tool selection in the ability access store",
            }
        if command == "send_message":
            metadata = dict(payload.get("metadata") or {})
            # 公开命令只接受已校验的顶层内容块，禁止借用旧数据的 metadata 入口。
            metadata.pop("content_blocks", None)
            if getattr(cmd_ctx, "request_id", None):
                metadata["request_id"] = cmd_ctx.request_id
            payload = {**payload, "metadata": metadata}
        if command == _HITL_RESPONSE_COMMAND:
            return await self._hitl_response(payload)
        if command not in _PROJECT_AGENT_COMMANDS:
            return {
                "success": False,
                "data": None,
                "error": f"unknown project agent command: {command}",
            }
        if command == CommandType.TURN_ROLLBACK.value and "dry_run" in payload:
            return {"success": False, "data": None, "error": "invalid_request"}
        try:
            project, manager = await self._project(payload, command)
            if command == "spawn_agent":
                return await self._spawn(manager, project, payload)
            if command == "list_agents":
                return await self._list(project)
            if command == "broadcast_message":
                return await self._broadcast(project, payload)
            if command == "delegate":
                return await self._delegate(project, payload)
            agent = self._resolve_agent(project, payload, _TARGET_FIELDS[command])
            # 直聊入站内容块：与 Room 通路同一套策略校验（稳定错误码），
            # 且在 ensure_cluster（可能挂载 Core）之前拒绝，避免为坏消息挂载。
            send_blocks: list[dict[str, Any]] | None = None
            if command == "send_message":
                send_blocks = validate_content_blocks(
                    payload.get("content_blocks"), visual_only=True
                )
                if send_blocks is not None:
                    send_blocks = await resolve_content_attachments(
                        send_blocks, str(project["project_root_locator"])
                    )
                if send_blocks is None and not str(payload.get("content") or "").strip():
                    raise StableError(
                        CONTENT_TEXT_REQUIRED,
                        "content is required when content_blocks is empty",
                    )
            cluster_id = str(project["project_id"])
            # 先登记定位符：Core 的库路径由项目定位符推导，未挂载单元也要能解析。
            remember = getattr(self.service, "remember_locator", None)
            if remember is not None:
                remember(
                    str(project["project_id"]),
                    str(project["project_root_locator"]),
                )
            if command in _NO_MOUNT_COMMANDS:
                try:
                    handle = self.service.get_handle(cluster_id)
                except KeyError:
                    return self._definition_result(command, project, agent)
            else:
                handle = await self.service.ensure_cluster(
                    cluster_id,
                    project_id=str(project["project_id"]),
                    project_root_locator=str(project["project_root_locator"]),
                )
            core_payload = dict(payload)
            if send_blocks is not None:
                core_payload["content_blocks"] = send_blocks
            core_payload["project_id"] = project["project_id"]
            core_payload["agent_id"] = agent["agent_id"]
            target_field = _TARGET_FIELDS[command]
            if target_field:
                core_payload[target_field] = agent["name"]
            if command == CommandType.TURN_ROLLBACK.value:
                core_payload["dry_run"] = False
            if command == "get_agent_info" or command in _TURN_COMMANDS:
                # cluster 已登记 ≠ 有在线实例：spawn 失败前 _ensure_agent_spawned
                # 已 ensure_cluster。端口 agents.get 只认识在线 Agent，离线会
                # NOT_FOUND；读侧命令应先探测 runtime，离线回定义态。
                availability = await self._runtime_availability(handle, agent["agent_id"])
                if availability == "offline":
                    return self._definition_result(command, project, agent)
            result = await handle.dispatch(command, core_payload)
            if command in _TURN_COMMANDS:
                return result
            if (
                command == "get_agent_info"
                and not result.get("success")
                and _agent_offline_error(result.get("error"))
            ):
                # 探测与取视图之间 Agent 被终止的竞态：仍回定义态而非失败。
                return self._definition_result(command, project, agent)
            if result.get("success") and isinstance(result.get("data"), dict):
                if command == "get_agent_info":
                    self._fill_agent_info_view(result["data"])
                result["data"].update(
                    {
                        "project_id": project["project_id"],
                        "agent_id": agent["agent_id"],
                        "agent_name": agent["name"],
                    }
                )
            return result
        except StableError as exc:
            return {
                "success": False,
                "data": None,
                "error": exc.code,
                "error_detail": exc.detail,
            }
        except ValueError as exc:
            return {"success": False, "data": None, "error": str(exc)}

    async def _project(
        self, payload: dict[str, Any], command: str = ""
    ) -> tuple[dict[str, Any], Any]:
        project_id = str(payload.get("project_id") or "")
        if not project_id:
            raise ValueError("project_id required")
        if self._ctx is None:
            raise ValueError("project_manager unavailable")
        try:
            manager = self._ctx.get("project_manager")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("project_manager unavailable") from exc
        result = await manager.handle_command("project_get", {"project_id": project_id})
        project = (result.get("data") or {}).get("project") if result.get("success") else None
        if not project:
            raise ValueError(result.get("error") or f"project not found: {project_id}")
        if project.get("archived_at") or project.get("deleted_at"):
            raise ValueError("resource_archived")
        if (
            command in _RUNTIME_REVIVING_COMMANDS
            and project.get("status") != ProjectStatus.ACTIVE.value
        ):
            raise ValueError("project_not_active")
        return project, manager

    @staticmethod
    def _definition_result(
        command: str, project: dict[str, Any], agent: dict[str, Any]
    ) -> dict[str, Any]:
        """定义态回执（读定义态 / 收敛性终止），不需要在线实例。

        两条路径共用：cluster 未挂载（stopped/archived 后已 dispose），以及
        cluster 已登记但 runtime 没有该 Agent 的在线实例（典型：spawn 失败，
        定义已落 Project 名册）。此时无 runtime 可终止、无活跃 session；
        get_agent_info 以 Project AgentSpec 为骨架作答。
        """
        identity = {
            "project_id": project["project_id"],
            "agent_id": agent["agent_id"],
            "agent_name": agent["name"],
        }
        if command in _TURN_COMMANDS:
            return {"success": False, "data": None, "error": "agent_offline"}
        if command == "terminate_agent":
            return {"success": True, "data": {**identity, "terminated": True}}
        if command == "session_list":
            return {"success": True, "data": {"sessions": [], **identity}}
        return {
            "success": True,
            "data": {
                "name": agent["name"],
                # 配置不再存在记录里：端点与提示词的权威在 Core 实例快照，
                # 这里只回身份与编排态，避免 Subject 复制一份配置。
                "runtime_state": "stopped",
                "context_usage": None,
                **identity,
            },
        }

    @staticmethod
    async def _runtime_availability(handle: Any, agent_id: str) -> str:
        """探测 Agent 在线态；探测失败按 ``unknown`` 处理（不误判离线）。"""
        try:
            runtime = await handle.get_entity_runtime(agent_id)
        except Exception:  # noqa: BLE001 - 读侧探测失败不阻断命令
            logger.warning(
                "get_agent_info: runtime probe failed for agent %s",
                agent_id,
                exc_info=True,
            )
            return "unknown"
        return str((runtime or {}).get("availability") or "unknown")

    @staticmethod
    def _fill_agent_info_view(data: dict[str, Any]) -> None:
        """补齐 ``get_agent_info`` 回执里观察端依赖的缺省字段。

        C3 之前回执直接来自 CoreUnit ``_handle_get_agent_info``，含
        ``available_ability_types`` / ``context_usage``；端口视图 ``AgentView``
        现已承载这两个字段（由 Core handler 透传），这里只兜底：老版本端口、
        或某单元确实没有对应数据时，仍给出观察端可消费的缺省值。

        - ``available_ability_types``：无类型目录时为空列表；
        - ``context_usage``：None = 暂无可恢复的用量快照，观察端按既有分支跳过；
        - ``runtime_state``：能走到在线分支即 running（离线分支走定义态回执）。
        """
        data.setdefault("available_ability_types", [])
        data.setdefault("context_usage", None)
        data.setdefault("runtime_state", "running")

    @staticmethod
    def _resolve_agent(
        project: dict[str, Any], payload: dict[str, Any], name_field: str
    ) -> dict[str, Any]:
        agent_id = str(payload.get("agent_id") or "")
        if not agent_id:
            raise ValueError("agent_id required")
        name = str(payload.get(name_field) or "")
        matches = [
            agent for agent in project.get("agents") or [] if agent.get("agent_id") == agent_id
        ]
        if len(matches) != 1:
            raise ValueError(f"agent not found in project: {agent_id}")
        agent = dict(matches[0])
        if name and agent.get("name") != name:
            raise ValueError("agent_identity_mismatch")
        return agent

    async def _spawn(
        self, manager: Any, project: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """旧 manifest 装配入口已退役。

        v3 的创建/恢复统一走 ``agents.create_assembly``：模板在创建时填充一次，
        之后配置的权威在 Core 的实例快照。``spawn_agent`` 只保留这条明确的拒绝，
        避免调用方以为「按 manifest 起一个」还能用。
        """
        del manager, project, payload
        raise StableError(
            "agent_manifest_retired",
            "按 manifest 装配已退役；请用 Agent 模板创建（agents.create_assembly）",
        )

    async def _list(self, project: dict[str, Any]) -> dict[str, Any]:
        running: dict[str, dict[str, Any]] = {}
        for cluster_id in (str(project["project_id"]),):
            try:
                handle = self.service.get_handle(cluster_id)
            except KeyError:
                # has_cluster→get_handle 之间单元可能被并发 shutdown。
                continue
            for item in await handle.list_agents(str(project["project_id"])):
                identity = str(item.get("agent_id") or "")
                if identity:
                    running[identity] = item
        agents = []
        for spec in project.get("agents") or []:
            item = dict(spec)
            runtime = running.get(str(spec.get("agent_id") or ""))
            item["runtime_state"] = "running" if runtime is not None else "stopped"
            if runtime is not None:
                item.update(runtime)
            item.update(
                {
                    "project_id": project["project_id"],
                    "agent_id": spec.get("agent_id", ""),
                    "name": spec.get("name", ""),
                }
            )
            agents.append(item)
        return {"success": True, "data": {"agents": agents}, "error": None}

    async def _broadcast(self, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(project["project_id"])
        handle = await self.service.ensure_cluster(
            project_id,
            project_id=project_id,
            project_root_locator=str(project["project_root_locator"]),
        )
        result = await handle.dispatch("broadcast_message", {**payload, "project_id": project_id})
        return {
            "success": result["success"],
            "data": {"clusters": {project_id: result}},
            "error": result.get("error"),
        }

    async def _delegate(self, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        from_id = str(payload.get("from_agent_id") or "")
        to_id = str(payload.get("to_agent_id") or "")
        agents = project.get("agents") or []
        source = next((agent for agent in agents if agent.get("agent_id") == from_id), None)
        target = next((agent for agent in agents if agent.get("agent_id") == to_id), None)
        if source is None or target is None:
            raise ValueError("agent_project_mismatch")
        handle = await self.service.ensure_cluster(
            str(project["project_id"]),
            project_id=str(project["project_id"]),
            project_root_locator=str(project["project_root_locator"]),
        )
        result = await handle.dispatch(
            "delegate",
            {
                **payload,
                "project_id": project["project_id"],
                "from_agent": source["name"],
                "to_agent": target["name"],
            },
        )
        return dict(result)

    async def _hitl_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        """HITL 审批回程：扇出到所有已启动单元，由持有 promise 的单元认领。

        命令不带 ``project_id``（Observer 只凭 ``promise_id`` 回发，归
        属项目未知），因此不能走 ``_project()`` 的单项目解析。单元按
        「返回 None = 不是我的」穿透约定逐个尝试；全部未认领时返回显式
        失败（对齐 CoreUnit 的既有文案），不伪装成功。
        """
        started = self.service.started_project_ids()
        if not started:
            return {
                "success": False,
                "data": None,
                "error": "HITL promise not found or expired: no active project runtime",
            }
        errors: list[str] = []
        for project_id in started:
            port = self._ports.for_project(project_id)
            try:
                result = await port.dispatch_command(_HITL_RESPONSE_COMMAND, payload)
            except Exception as exc:  # noqa: BLE001 - 单个项目失败不阻断扇出
                errors.append(f"{project_id}: {exc}")
                continue
            if result is None:
                # 单元未启动或穿透（promise 不在此单元）——继续下一个项目。
                continue
            return result
        if errors:
            return {
                "success": False,
                "data": None,
                "error": f"HITL response dispatch failed: {'; '.join(errors)}",
            }
        return {
            "success": False,
            "data": None,
            "error": "HITL promise not found or expired: no unit claimed the response",
        }
