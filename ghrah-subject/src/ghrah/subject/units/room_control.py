# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""观察端 Room 历史与增量 REST 读面。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from ghrah.protocol.views.observer_room import RoomTranscriptView

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.errors import ProjectArchivedError
from ghrah.subject.room.store import RoomArchivedError
from ghrah.subject.runtime.service_keys import OBSERVER_ENDPOINT, PROJECT_MANAGER, ROOM_MANAGER
from ghrah.subject.unit.base import SubjectUnit, UnitMeta
from ghrah.subject.units.ability_control import AbilityApiError, _authorized
from ghrah.subject.units.turn_control import _read


def build_room_control_router(*, config: SubjectConfig, ctx: Any) -> APIRouter:
    """构造只读的 Room transcript 路由。"""
    projects = ctx.get(PROJECT_MANAGER.name)
    rooms = ctx.get(ROOM_MANAGER.name)
    router = APIRouter(prefix="/api/v1/projects/{project_id}/rooms/{room_id}")

    @router.get("/transcript")
    async def transcript(
        request: Request,
        project_id: str,
        room_id: str,
        before_seq: Annotated[int | None, Query(ge=0, le=2**63 - 1)] = None,
        since_seq: Annotated[int | None, Query(ge=0, le=2**63 - 1)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> RoomTranscriptView:
        if not config.observer_http_api_enabled:
            raise AbilityApiError("capability_unavailable", "观测端 HTTP API 未启用")
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")
        if before_seq is not None and since_seq is not None:
            raise AbilityApiError("invalid_request", "before_seq 与 since_seq 只能提供一个")
        result = await projects.handle_command("project_get", {"project_id": project_id})
        project = (result.get("data") or {}).get("project") if result.get("success") else None
        if not project or project.get("project_id") != project_id:
            raise AbilityApiError("not_found", f"项目不存在：{project_id}")
        if project.get("archived_at") or project.get("deleted_at"):
            raise AbilityApiError("project_archived", "项目已归档", status=409)
        try:
            page = await _read(
                rooms.transcript(
                    project_id=project_id,
                    project=project,
                    room_id=room_id,
                    before_seq=before_seq,
                    since_seq=since_seq,
                    limit=limit,
                )
            )
        except RoomArchivedError as error:
            raise AbilityApiError("resource_archived", "Room 已归档", status=409) from error
        except ProjectArchivedError as error:
            raise AbilityApiError("project_archived", "项目已归档", status=409) from error
        if page is None:
            raise AbilityApiError("not_found", f"Room 不存在：{room_id}")
        return page

    return router


class RoomControlUnit(SubjectUnit):
    """将 Room transcript 挂到 full profile 的 Observer HTTP 应用。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config

    @property
    def meta(self) -> UnitMeta:
        return UnitMeta(
            name="room_control",
            requires=frozenset({OBSERVER_ENDPOINT, PROJECT_MANAGER, ROOM_MANAGER}),
        )

    async def init(self, ctx: Any) -> None:
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        endpoint.app.include_router(build_room_control_router(config=self._config, ctx=ctx))
