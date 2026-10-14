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

from collections.abc import Callable
from typing import Any

from ghrah.protocol.types import ProjectStatus

from ghrah.subject.config import SubjectConfig
from ghrah.subject.core_cluster.registry import (
    CoreClusterRegistry,
    default_core_unit_factory,
)
from ghrah.subject.errors import StableError
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    MANIFEST_STORE,
    SANDBOX_EXECUTOR,
    WORKSPACE_SERVICE,
)
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta

__all__ = ["CoreClusterRegistryUnit"]

_PROJECT_AGENT_COMMANDS = frozenset(
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

_TARGET_FIELDS = {
    "terminate_agent": "name",
    "send_message": "target",
    "register_ability": "agent_name",
    "unregister_ability": "agent_name",
    "get_agent_info": "name",
    "agent_compact_context": "agent_name",
    # agent_reset 以 project_id + agent_id 稳定寻址（AgentResetPayload 无 name
    # 字段）；目标字段置空串 = 仅按 agent_id 解析，不校验可重名 name。
    "agent_reset": "",
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
        "register_ability",
        "unregister_ability",
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
_NO_MOUNT_COMMANDS = frozenset({"get_agent_info", "session_list", "terminate_agent"})


def _is_version_conflict(project_id: str, error: str) -> bool:
    """识别 project_add_agent 回执中的乐观锁冲突文案（store 抛错的透传）。"""
    return (
        f"Project {project_id} modified" in error
        or "concurrent modification during update" in error
    )


class CoreClusterRegistryUnit(SubjectUnit):
    """Provides the CORE_CLUSTER_REGISTRY service (cluster = CoreUnit 实例)."""

    def __init__(
        self,
        config: SubjectConfig,
        *,
        unit_factory: Callable[[str, str, str], Any] | None = None,
        llm_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._config = config
        # 测试可注入假工厂；生产默认工厂在 init 时构造（需 ctx 取 MANIFEST_STORE
        # 注入 CoreUnitConfig.manifest_store，对齐 Core 独立库 runner 范式）。
        self._unit_factory = unit_factory
        # 可选 per-cluster LLM 工厂透传（None = Core 内建默认 agentconf 解析）；
        # 显式 unit_factory 优先，本参数被忽略（工厂自持全部 CoreUnitConfig）。
        self._llm_factory = llm_factory
        self._registry: CoreClusterRegistry | None = None
        self._ctx: Any | None = None
        self._meta = UnitMeta(
            name="core_cluster_registry",
            requires=frozenset({MANIFEST_STORE, WORKSPACE_SERVICE, SANDBOX_EXECUTOR}),
            provides=frozenset({CORE_CLUSTER_REGISTRY}),
            routes=RouteSpec(commands=_PROJECT_AGENT_COMMANDS),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def service(self) -> CoreClusterRegistry:
        if self._registry is None:
            raise RuntimeError("CoreClusterRegistryUnit has not been initialized.")
        return self._registry

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        store = ctx.get(MANIFEST_STORE.name)
        if self._unit_factory is None:
            # 生产默认工厂：注入 manifest_store（从 ctx 取），
            # 供 CoreUnit 内部解析 manifest_ref spawn。
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
                    if not isinstance(result, dict) or not result.get("success"):
                        raise RuntimeError(str(result))

            self._unit_factory = default_core_unit_factory(
                self._config,
                manifest_store=store,
                llm_factory=self._llm_factory,
                room_projector=project_room,
            )
        registry = CoreClusterRegistry(
            unit_factory=self._unit_factory,
        )
        registry.bind(ctx)
        self._registry = registry
        ctx.provide(CORE_CLUSTER_REGISTRY.name, registry)

    async def start(self) -> None:
        # CoreUnit 实例按需懒挂载（ensure_cluster），无启动期动作。
        pass

    async def stop(self) -> None:
        if self._registry is not None:
            await self._registry.stop()

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        if command == "send_message" and getattr(cmd_ctx, "request_id", None):
            payload = {
                **payload,
                "metadata": {**(payload.get("metadata") or {}), "request_id": cmd_ctx.request_id},
            }
        if command not in _PROJECT_AGENT_COMMANDS:
            return {
                "success": False,
                "data": None,
                "error": f"unknown project agent command: {command}",
            }
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
            cluster_id = str(agent["cluster_id"])
            if command in _NO_MOUNT_COMMANDS and not self.service.has_cluster(cluster_id):
                return self._no_mount_result(command, project, agent)
            handle = await self.service.ensure_cluster(
                cluster_id,
                project_id=str(project["project_id"]),
                project_root_locator=str(project["project_root_locator"]),
            )
            core_payload = dict(payload)
            core_payload["project_id"] = project["project_id"]
            core_payload["agent_id"] = agent["agent_id"]
            target_field = _TARGET_FIELDS[command]
            if target_field:
                core_payload[target_field] = agent["name"]
            result = await handle.dispatch(command, core_payload)
            if result.get("success") and isinstance(result.get("data"), dict):
                result["data"].update(
                    {
                        "project_id": project["project_id"],
                        "agent_id": agent["agent_id"],
                        "agent_name": agent["name"],
                        "cluster_id": agent["cluster_id"],
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
    def _no_mount_result(
        command: str, project: dict[str, Any], agent: dict[str, Any]
    ) -> dict[str, Any]:
        """cluster 未挂载时的免挂载回执（读定义态 / 收敛性终止）。

        stopped/archived 后 cluster 已 dispose：无 runtime 可终止、无活跃
        session；get_agent_info 从 Project AgentSpec 返回定义态。
        """
        identity = {
            "project_id": project["project_id"],
            "agent_id": agent["agent_id"],
            "agent_name": agent["name"],
            "cluster_id": agent["cluster_id"],
        }
        if command == "terminate_agent":
            return {"success": True, "data": {**identity, "terminated": True}}
        if command == "session_list":
            return {"success": True, "data": {"sessions": [], **identity}}
        return {
            "success": True,
            "data": {
                "name": agent["name"],
                "system_prompt": agent.get("system_prompt") or "",
                "abilities": list(agent.get("abilities") or []),
                "runtime_state": "stopped",
                "context_usage": None,
                **identity,
            },
        }

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
        # ACTIVE 校验已在 _project() 的 _RUNTIME_REVIVING_COMMANDS 守卫统一完成。
        config = payload.get("config") or {}
        name = str(config.get("name") or "")
        if not name:
            raise ValueError("config.name required")
        # 乐观锁冲突（并发写同 Project）时重取最新快照重试，最多 3 次：
        # 合法的不同 name 并发 spawn 不应因陈旧 version 快照被误拒；
        # 重试中同名重复由 add_agent 的 name 唯一性校验正常拦截。
        for attempt in range(3):
            cluster_id = str(payload.get("cluster_id") or "")
            cluster_ids = [str(value) for value in project.get("cluster_ids") or []]
            if not cluster_id:
                if len(cluster_ids) != 1:
                    raise ValueError("cluster_id required")
                cluster_id = cluster_ids[0]
            if cluster_id not in cluster_ids:
                raise ValueError("cluster_project_mismatch")
            result = await manager.handle_command(
                "project_add_agent",
                {
                    "project_id": project["project_id"],
                    "expected_version": project["version"],
                    "agent": {
                        "agent_id": str(config.get("agent_id") or ""),
                        "name": name,
                        "cluster_id": cluster_id,
                        "manifest_ref": str(payload.get("manifest_ref") or ""),
                        "system_prompt": str(config.get("system_prompt") or ""),
                        "abilities": [
                            str(item.get("ability_type") or "")
                            for item in payload.get("abilities") or []
                            if item.get("ability_type")
                        ]
                        or None,
                    },
                },
            )
            if result.get("success"):
                break
            error_text = str(result.get("error") or "")
            if attempt < 2 and _is_version_conflict(project["project_id"], error_text):
                # 重取快照（同时重验 ACTIVE/归档状态，防并发暂停后继续 spawn）
                project, manager = await self._project(payload, "spawn_agent")
                continue
            if _is_version_conflict(project["project_id"], error_text):
                return {
                    "success": False,
                    "data": None,
                    "error": "project_version_conflict",
                }
            return dict(result)
        data = result.get("data") or {}
        return {
            "success": True,
            "data": {
                "name": data.get("agent_name", name),
                "agent_id": data.get("agent_id", ""),
                "project_id": project["project_id"],
                "cluster_id": cluster_id,
                "runtime_pending": bool(data.get("runtime_pending")),
                "runtime_error": data.get("runtime_error"),
            },
            "error": None,
        }

    async def _list(self, project: dict[str, Any]) -> dict[str, Any]:
        running: dict[str, dict[str, Any]] = {}
        for cluster_id in project.get("cluster_ids") or []:
            try:
                handle = self.service.get_handle(str(cluster_id))
            except KeyError:
                # has_cluster→get_handle 之间 cluster 可能被并发 shutdown。
                continue
            for item in await handle.list_agents(str(project["project_id"])):
                identity = str(item.get("agent_id") or "")
                if identity:
                    running[identity] = item
        agents = []
        for spec in project.get("agents") or []:
            item = dict(spec)
            runtime = running.get(str(spec.get("agent_id") or ""))
            item["runtime_state"] = (
                runtime.get("state", "running") if runtime is not None else "stopped"
            )
            if runtime is not None:
                item.update(runtime)
            item.update(
                {
                    "project_id": project["project_id"],
                    "cluster_id": spec.get("cluster_id", ""),
                    "agent_id": spec.get("agent_id", ""),
                    "name": spec.get("name", ""),
                }
            )
            agents.append(item)
        return {"success": True, "data": {"agents": agents}, "error": None}

    async def _broadcast(self, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        responses: dict[str, Any] = {}
        cluster_ids = {str(agent.get("cluster_id") or "") for agent in project.get("agents") or []}
        for cluster_id in sorted(cluster_ids - {""}):
            handle = await self.service.ensure_cluster(
                cluster_id,
                project_id=str(project["project_id"]),
                project_root_locator=str(project["project_root_locator"]),
            )
            result = await handle.dispatch(
                "broadcast_message",
                {**payload, "project_id": project["project_id"]},
            )
            responses[cluster_id] = result
        return {"success": True, "data": {"clusters": responses}, "error": None}

    async def _delegate(self, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        from_id = str(payload.get("from_agent_id") or "")
        to_id = str(payload.get("to_agent_id") or "")
        agents = project.get("agents") or []
        source = next((agent for agent in agents if agent.get("agent_id") == from_id), None)
        target = next((agent for agent in agents if agent.get("agent_id") == to_id), None)
        if source is None or target is None:
            raise ValueError("agent_project_mismatch")
        if source.get("cluster_id") != target.get("cluster_id"):
            raise ValueError("cross_cluster_delegate_unsupported")
        handle = await self.service.ensure_cluster(
            str(source["cluster_id"]),
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
