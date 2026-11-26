# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Room built-in Subject unit（范式对齐 TaskUnit）。"""

from __future__ import annotations

import json
from typing import Any, Literal

from ghrah.protocol.enums import RoomSubjectType
from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import ProblemCode
from ghrah.protocol.types import ChatActionSendPayload, RoomSendPayload, SendMessagePayload
from ghrah.protocol.views.core import DeliveryAcceptanceView
from ghrah.protocol.views.observer_room import RoomFactsQuery, TranscriptFactsView
from pydantic import ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.scoped_stores import (
    ProjectScopedChatActionStore,
    ProjectScopedRoomStore,
)
from ghrah.subject.room.content_blocks import resolve_content_attachments
from ghrah.subject.room.manager import RoomManager
from ghrah.subject.room.models import RoomRecord
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    CORE_PORT_SERVICE,
    PROJECT_MANAGER,
    ROOM_MANAGER,
    ROOM_STORE,
)
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units._commands import ROOM_COMMANDS

__all__ = ["RoomUnit"]


class RoomUnit(SubjectUnit):
    """Owns RoomStore + RoomManager and room command/event routes.

    requires ``PROJECT_MANAGER`` 与 ``CORE_CLUSTER_REGISTRY``。Room 投递先由
    room.project_id 将稳定 agent_id 解析到 cluster，再直达该 CoreUnitHandle；
    不使用宿主全局 ``command/send_message``，因此同名 agent 不会跨 cluster 串线。
    """

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._store: ProjectScopedRoomStore | None = None
        self._chat_actions: ProjectScopedChatActionStore | None = None
        self._manager: RoomManager | None = None
        self._project_manager: Any | None = None
        self._meta = UnitMeta(
            name="room",
            requires=frozenset({PROJECT_MANAGER, CORE_CLUSTER_REGISTRY, CORE_PORT_SERVICE}),
            provides=frozenset({ROOM_MANAGER, ROOM_STORE}),
            routes=RouteSpec(commands=ROOM_COMMANDS),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def service(self) -> RoomManager:
        if self._manager is None:
            raise RuntimeError("RoomUnit has not been initialized.")
        return self._manager

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        project_manager = ctx.get(PROJECT_MANAGER.name)
        self._project_manager = project_manager
        cluster_registry = ctx.get(CORE_CLUSTER_REGISTRY.name)
        ports = ctx.get(CORE_PORT_SERVICE.name)

        async def read_facts(
            project_id: str,
            locator: str,
            query: RoomFactsQuery,
        ) -> TranscriptFactsView:
            ports.remember_locator(project_id, locator)
            return await ports.for_project(project_id).transcript.facts(query)

        async def project_roots() -> dict[str, str]:
            result = await project_manager.handle_command("project_list", {"archived": None})
            projects = (result.get("data") or {}).get("projects", [])
            return {
                p["project_id"]: p["project_root_locator"]
                for p in projects
                if p.get("project_root_locator")
            }

        self._store = ProjectScopedRoomStore(self._config.persistence.db_path, project_roots)
        self._chat_actions = ProjectScopedChatActionStore(project_roots)
        register_resource = getattr(project_manager, "register_scoped_resource", None)
        if register_resource is not None:
            register_resource(self._store, room_store=True)
            register_resource(self._chat_actions)

        async def project_get(project_id: str) -> dict[str, Any] | None:
            result = await project_manager.handle_command("project_get", {"project_id": project_id})
            if not result.get("success"):
                return None
            return (result.get("data") or {}).get("project")

        async def deliver(
            target: str,
            sender: str,
            content: str,
            room_id: str,
            blocks: list[dict[str, Any]] | None = None,
            delivery_policy: Literal["inject", "queue"] = "queue",
        ) -> dict[str, Any]:
            """按 room.project_id + agent_id 找 cluster，并直达对应 CoreUnit。"""
            if self._store is None:
                return {"success": False, "data": None, "error": "RoomUnit not initialized"}
            room = await self._store.get(room_id)
            if room is None:
                return {"success": False, "data": None, "error": f"room not found: {room_id}"}
            project_result = await project_manager.handle_command(
                "project_get", {"project_id": room.project_id}
            )
            if not project_result.get("success"):
                return {
                    "success": False,
                    "data": None,
                    "error": f"project not found for room: {room.project_id}",
                }
            project = (project_result.get("data") or {}).get("project") or {}
            agents = project.get("agents") or []
            matches = [a for a in agents if a.get("agent_id") == target]
            if not matches:
                # 旧 Room 成员仍可能保存 name；只在 project 内唯一时兼容。
                matches = [a for a in agents if a.get("name") == target]
            resolved: list[tuple[dict[str, Any], Any]] = []
            for agent in matches:
                # 单元按项目唯一：运行键就是 project_id。
                handle = await cluster_registry.ensure_cluster(
                    room.project_id,
                    project_id=room.project_id,
                    project_root_locator=str(project.get("project_root_locator") or ""),
                )
                resolved.append((agent, handle))

            if len(resolved) != 1:
                reason = "ambiguous" if resolved else "not found"
                return {
                    "success": False,
                    "data": None,
                    "error": f"agent {reason} in room project: {target}",
                }
            agent, handle = resolved[0]
            return await handle.send_message(
                SendMessagePayload(
                    project_id=room.project_id,
                    agent_id=str(agent.get("agent_id") or target),
                    target=str(agent["name"]),
                    content=content,
                    sender=sender,
                    content_blocks=blocks,
                    delivery_policy=delivery_policy,
                    metadata={
                        "room_id": room_id,
                        "project_id": room.project_id,
                        "agent_id": str(agent.get("agent_id") or target),
                    },
                )
            )

        async def publish(room: RoomRecord, payload: RoomSendPayload) -> dict[str, Any]:
            """先持久受理 Room 人类消息，再由 MM 唯一投影入口写引用日志。"""
            from uuid import uuid4

            project = await project_get(room.project_id)
            if not project:
                return {"success": False, "error": "Room project unavailable", "data": None}
            ports.remember_locator(room.project_id, project["project_root_locator"])
            raw_blocks = payload.data.get("content_blocks")
            blocks: list[dict[str, Any]] | None = (
                raw_blocks if isinstance(raw_blocks, list) and raw_blocks else None
            )
            if blocks is not None:
                blocks = await resolve_content_attachments(blocks, project["project_root_locator"])
            try:
                manager = cluster_registry.get_message_manager(room.project_id)
            except KeyError:
                # 单元按项目唯一：直接用 project_id 作为运行键。
                await cluster_registry.ensure_cluster(
                    room.project_id,
                    project_id=room.project_id,
                    project_root_locator=project["project_root_locator"],
                )
                manager = cluster_registry.get_message_manager(room.project_id)
            from ghrah.communication import MessageKind

            meta: dict[str, Any] = {
                "room_ids": [room.room_id],
                "room_id": room.room_id,
                "room_projection": True,
            }
            key = f"room:{room.room_id}:{payload.data.get('request_id') or uuid4().hex}"
            content = str(payload.data.get("message") or "")
            has_message = bool(content.strip())
            meta["room_data"] = {
                k: v for k, v in payload.data.items() if k != "message" or not has_message
            }
            meta["room_has_message"] = has_message
            if not has_message:
                if blocks is None:
                    # 既有语义：非消息类 room 事件把 data 序列化为正文。
                    content = json.dumps(payload.data, ensure_ascii=False)
                else:
                    # 纯块消息：正文留空，不把 data（含 base64）序列化进正文。
                    content = ""
            deliveries: list[DeliveryAcceptanceView] = []
            if payload.author_type == RoomSubjectType.HUMAN:
                # 人类消息块由 Core 存在消息权威中，历史读通时再投影到日志。
                meta["room_data"].pop("content_blocks", None)
                targets = (
                    RoomManager._resolve_delivery_targets(room, payload)
                    if has_message or blocks is not None
                    else []
                )
                try:
                    message = await manager.send_external(
                        sender=payload.author,
                        recipient_ids=targets,
                        content=content,
                        idempotency_key=key,
                        metadata=meta,
                        content_blocks=blocks,
                        delivery_policy=payload.delivery_policy,
                        await_reply=payload.delivery_policy == "queue",
                    )
                except ApiError as error:
                    if error.problem.code != ProblemCode.CONFLICT:
                        raise
                    return {"success": False, "data": None, "error": error.problem.detail}
                deliveries = message.deliveries
            else:
                # 外部 agent 日志发布保留原语义；工具的实际投递在 action 中准备。
                if blocks is not None:
                    meta["room_data"]["content_blocks"] = blocks
                message = await manager.send(
                    sender_id=payload.author,
                    recipient_ids=(),
                    content=content,
                    kind=MessageKind.NOTIFICATION,
                    idempotency_key=key,
                    metadata=meta,
                )
            await manager.recover_room_projections()
            entry = await self._store.get_log_by_id(
                room.room_id, f"mm:{message.message_id}:{room.room_id}"
            )
            if entry is None:
                return {
                    "success": False,
                    "data": {"message_id": message.message_id},
                    "error": "Message accepted; Room projection pending",
                }
            assert self._manager is not None
            return {
                "success": True,
                "error": None,
                "data": {
                    "entry": (
                        await self._manager.hydrate_log_entry(room, project, entry)
                    ).to_wire(),
                    "deliveries": [delivery.model_dump(mode="json") for delivery in deliveries],
                },
            }

        self._manager = RoomManager(
            self._store,
            on_event=self._emit_event,
            project_get=project_get,
            read_facts=read_facts,
            deliver=deliver,
            publish=publish
            if callable(getattr(cluster_registry, "get_message_manager", None))
            else None,
        )
        ctx.provide(ROOM_MANAGER.name, self._manager)
        ctx.provide(ROOM_STORE.name, self._store)

    async def start(self) -> None:
        if self._store is not None:
            await self._store.start()
            if self._project_manager is not None:
                result = await self._project_manager.handle_command(
                    "project_list", {"archived": True}
                )
                for project in (result.get("data") or {}).get("projects", []):
                    if project.get("archived_at") or project.get("deleted_at"):
                        self._store.register_project_root(
                            str(project["project_id"]),
                            str(project.get("project_root_locator") or ""),
                        )
                        await self._store.close_project(str(project["project_id"]))
            await self._store.migrate_all()
            if self._chat_actions is not None:
                await self._chat_actions.start()

    async def stop(self) -> None:
        if self._store is not None:
            await self._store.stop()
            await self._chat_actions.stop()

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        if (
            command == "room_send"
            and getattr(cmd_ctx, "request_id", None)
            and not (payload.get("data") or {}).get("mm_message_id")
        ):
            payload = {
                **payload,
                "data": {**(payload.get("data") or {}), "request_id": cmd_ctx.request_id},
            }
        normalized = await self._normalize_agent_identity(command, payload)
        if isinstance(normalized, dict) and normalized.get("success") is False:
            return normalized
        if command == "chat_action_list":
            from ghrah.protocol.types import ChatActionListPayload

            try:
                request = ChatActionListPayload.model_validate(normalized)
            except ValidationError as error:
                return {"success": False, "data": None, "error": f"invalid payload: {error}"}
            room = await self._store.get(request.room_id)
            if room is None:
                return {"success": False, "data": None, "error": "room_not_found"}
            store = await self._chat_actions.store(room.project_id)
            records = await store.list(request.room_id, limit=request.limit)
            return {
                "success": True,
                "data": {
                    "actions": [record.to_wire() for record in records],
                    "count": len(records),
                },
                "error": None,
            }
        if command == "room_send" and self._store is not None:
            send_data = normalized.get("data")
            if isinstance(send_data, dict) and "chat_action" in send_data:
                try:
                    action_payload = ChatActionSendPayload.model_validate(
                        send_data.get("chat_action")
                    )
                except ValidationError as error:
                    return {"success": False, "data": None, "error": f"invalid payload: {error}"}
                chat_action = action_payload.action.strip()
                if not chat_action:
                    return {
                        "success": False,
                        "data": None,
                        "error": "invalid payload: action must not be blank",
                    }
                room = await self._store.get(str(normalized.get("room_id") or ""))
                if room is None:
                    return {"success": False, "data": None, "error": "room_not_found"}
                store = await self._chat_actions.store(room.project_id)
                record = await store.append(
                    project_id=room.project_id,
                    room_id=room.room_id,
                    action=chat_action,
                    payload=action_payload.payload,
                )
                try:
                    result = await self.service.handle_command(command, normalized)
                except Exception as error:
                    await store.mark_failed(record.action_id, str(error))
                    raise
                if result.get("success"):
                    entry = (result.get("data") or {}).get("entry") or {}
                    await store.mark_sent(record.action_id, str(entry.get("id") or ""))
                else:
                    await store.mark_failed(record.action_id, str(result.get("error") or ""))
                return result
        return await self.service.handle_command(command, normalized)

    async def _normalize_agent_identity(
        self, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """把 Room 边界的 Agent 显示名归一为 project-scoped agent_id。"""
        if self._store is None or self._project_manager is None:
            return payload
        room_id = str(payload.get("room_id") or "")
        if not room_id or command not in {"room_join", "room_leave", "room_send"}:
            return payload
        room = await self._store.get(room_id)
        if room is None:
            return payload

        def member_identity(value: str) -> tuple[str, str] | None:
            matches = [
                member
                for member in room.members
                if member.subject == value or member.subject_name == value
            ]
            if len(matches) == 1:
                member = matches[0]
                return member.subject, member.subject_name
            return None

        normalized = dict(payload)
        if command == "room_join" and str(payload.get("subject_type")) == "agent":
            project_result = await self._project_manager.handle_command(
                "project_get", {"project_id": room.project_id}
            )
            project = (project_result.get("data") or {}).get("project") or {}
            value = str(payload.get("subject") or "")
            matches = [
                agent
                for agent in project.get("agents") or []
                if agent.get("agent_id") == value or agent.get("name") == value
            ]
            if len(matches) > 1:
                return {
                    "success": False,
                    "data": None,
                    "error": f"ambiguous agent in room project: {value}",
                }
            if len(matches) == 1 and matches[0].get("agent_id"):
                normalized["subject"] = matches[0]["agent_id"]
                normalized["subject_name"] = matches[0]["name"]
            return normalized

        if command == "room_leave":
            resolved = member_identity(str(payload.get("subject") or ""))
            if resolved is not None:
                normalized["subject"] = resolved[0]
            return normalized

        if command == "room_send" and str(payload.get("author_type")) == "agent":
            resolved = member_identity(str(payload.get("author") or ""))
            if resolved is not None:
                normalized["author"] = resolved[0]

        if command == "room_send":
            data = dict(payload.get("data") or {})
            targets = data.get("targets")
            if isinstance(targets, list):
                normalized_targets: list[str] = []
                for target in targets:
                    resolved = member_identity(str(target))
                    normalized_targets.append(resolved[0] if resolved else str(target))
                data["targets"] = normalized_targets
                normalized["data"] = data
        return normalized

    async def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """RoomManager 的 ``on_event`` 回调：保留 async 签名，体内同步 ctx.emit。"""

        ctx = self._ctx
        if ctx is None:
            return
        ctx.emit(f"event/{event_type}", payload)
