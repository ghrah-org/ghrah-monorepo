# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目业务数据源命令入口；只由显式 sync 写入，不做事件投影写回。"""

from __future__ import annotations

import logging
from typing import Any

from ghrah.protocol.payloads.material import (
    MaterialStateGetPayload,
    MaterialStateRegisterPayload,
    MaterialStateResultPayload,
    MaterialStateSyncPayload,
)
from ghrah.protocol.routing import MATERIAL_STATE_COMMANDS
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.errors import ProjectArchivedError
from ghrah.subject.project.scoped_stores import ProjectScopedMaterialStateStore
from ghrah.subject.runtime.service_keys import PROJECT_MANAGER, ROOM_MANAGER, ROOM_STORE
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta

logger = logging.getLogger(__name__)


class MaterialStateUnit(SubjectUnit):
    """持有当前值存储，校验项目和 Room 作用域并管理库连接生命周期。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._manager: Any = None
        self._rooms: Any = None
        self._room_manager: Any = None
        self._stores: ProjectScopedMaterialStateStore | None = None
        self._meta = UnitMeta(
            name="material_state",
            requires=frozenset({PROJECT_MANAGER, ROOM_MANAGER, ROOM_STORE}),
            routes=RouteSpec(commands=MATERIAL_STATE_COMMANDS),
        )

    @property
    def meta(self) -> UnitMeta:
        """返回命令及服务依赖声明。"""
        return self._meta

    async def init(self, ctx: Any) -> None:
        """登记项目资源，确保归档与删除关闭数据库连接。"""
        self._manager = ctx.get(PROJECT_MANAGER.name)
        self._rooms = ctx.get(ROOM_STORE.name)
        self._room_manager = ctx.get(ROOM_MANAGER.name)

        async def roots() -> dict[str, str]:
            result = await self._manager.handle_command("project_list", {"archived": None})
            return {
                p["project_id"]: p["project_root_locator"]
                for p in result["data"]["projects"]
                if p.get("project_root_locator")
            }

        self._stores = ProjectScopedMaterialStateStore(roots)
        self._manager.register_scoped_resource(self._stores)

    async def stop(self) -> None:
        """退出 Subject 时关闭所有业务数据连接。"""
        if self._stores is not None:
            await self._stores.stop()

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: CommandContext
    ) -> dict[str, Any]:
        """通过单一命令面完成登记、读取和校验后执行。"""
        del cmd_ctx
        models = {
            "material_state_register": MaterialStateRegisterPayload,
            "material_state_get": MaterialStateGetPayload,
            "material_state_sync": MaterialStateSyncPayload,
        }
        try:
            # Observer transport injects these envelope fields into a copy of
            # the payload. They are not material data; all other extra keys
            # remain forbidden by the wire contract.
            business_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"request_id", "session_id"}
            }
            request = models[command].model_validate(business_payload)
            async with self._manager.lifecycle_lock(request.project_id):
                result = await self._manager.handle_command(
                    "project_get", {"project_id": request.project_id}
                )
                if not result.get("success"):
                    raise ValueError("project_not_found")
                if result["data"]["project"].get("archived_at") is not None:
                    raise ProjectArchivedError(request.project_id)
                assert self._stores is not None
                store = await self._stores.store(request.project_id)
                if isinstance(request, MaterialStateRegisterPayload):
                    rooms = await self._rooms.list(project_id=request.project_id)
                    data = await store.register(
                        request, room_cursors={room.room_id: room.seq_watermark for room in rooms}
                    )
                elif isinstance(request, MaterialStateSyncPayload):
                    room = await self._rooms.get(request.room_id)
                    if room is None or room.project_id != request.project_id:
                        raise ValueError("material_state_room_mismatch")
                    legacy = []
                    for log_id in await store.legacy_log_ids():
                        entry = await self._rooms.get_log_by_id(request.room_id, log_id)
                        if entry is not None:
                            legacy.append(entry)
                    await store.bind_legacy_room(request.room_id, legacy)
                    entries = await self._room_manager.get_log_page(
                        request.room_id,
                        since_seq=await store.cursor(request.room_id),
                        limit=request.limit,
                    )
                    data = await store.sync(request.room_id, entries)
                    data["has_more"] = bool(
                        entries
                        and len(entries) == request.limit
                        and entries[-1].seq < room.seq_watermark
                    )
                else:
                    data = await store.get()
            return {
                "success": True,
                "data": MaterialStateResultPayload.model_validate(data).model_dump(mode="json"),
                "error": None,
            }
        except ProjectArchivedError:
            return {"success": False, "data": None, "error": "project_archived"}
        except (ValueError, ValidationError, SchemaError, SchemaValidationError) as exc:
            return {"success": False, "data": None, "error": str(exc)}
        except Exception:
            logger.exception("material state command failed: %s", command)
            return {"success": False, "data": None, "error": "material_state_unavailable"}
