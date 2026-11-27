# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""工作台动态页面命令入口：观察端读 page_get，Agent 工具经宿主命令写 page_set/page_patch。"""

from __future__ import annotations

import json
import logging
from typing import Any

from ghrah.protocol.payloads.page import PageChangedPayload, PageGetPayload, PagePayload
from ghrah.protocol.routing import PAGE_COMMANDS
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.page.store import PageStateStore
from ghrah.subject.project.errors import ProjectArchivedError
from ghrah.subject.project.scoped_stores import ProjectScopedPageStore
from ghrah.subject.runtime.service_keys import PROJECT_MANAGER
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta

logger = logging.getLogger(__name__)

MAX_HTML_BYTES = 256 * 1024
MAX_DATA_BYTES = 64 * 1024

HOST_COMMANDS = frozenset({"page_set", "page_patch"})


class PageSetPayload(BaseModel):
    """宿主命令：整页替换（Agent 工具调用）。"""

    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)
    page_id: str = "main"
    title: str = ""
    html: str = Field(max_length=MAX_HTML_BYTES)
    data: dict[str, Any] = Field(default_factory=dict)
    updated_by: str = ""


class PagePatchPayload(BaseModel):
    """宿主命令：合并页面 data。"""

    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)
    page_id: str = "main"
    data: dict[str, Any]
    updated_by: str = ""


class PageUnit(SubjectUnit):
    """持有项目页面当前值，写侧只此一处。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any = None
        self._manager: Any = None
        self._stores: ProjectScopedPageStore | None = None
        self._meta = UnitMeta(
            name="page",
            requires=frozenset({PROJECT_MANAGER}),
            routes=RouteSpec(commands=frozenset({*PAGE_COMMANDS, *HOST_COMMANDS})),
        )

    @property
    def meta(self) -> UnitMeta:
        """返回命令与服务依赖声明。"""
        return self._meta

    async def init(self, ctx: Any) -> None:
        """登记项目资源，确保归档/删除时关闭页面库连接。"""
        self._ctx = ctx
        self._manager = ctx.get(PROJECT_MANAGER.name)

        async def roots() -> dict[str, str]:
            result = await self._manager.handle_command("project_list", {"archived": None})
            return {
                p["project_id"]: p["project_root_locator"]
                for p in result["data"]["projects"]
                if p.get("project_root_locator")
            }

        self._stores = ProjectScopedPageStore(roots)
        self._manager.register_scoped_resource(self._stores)

    async def stop(self) -> None:
        """退出 Subject 时关闭页面库连接。"""
        if self._stores is not None:
            await self._stores.stop()

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: CommandContext
    ) -> dict[str, Any]:
        """读命令返回页面快照；宿主命令写入并广播 page_changed。"""
        del cmd_ctx
        try:
            business = {
                key: value
                for key, value in payload.items()
                if key not in {"request_id", "session_id"}
            }
            if command == "page_get":
                request = PageGetPayload.model_validate(business)
                page = await self._get_page(request.project_id)
                return {"success": True, "data": {"page": page}, "error": None}
            if command == "page_set":
                return await self._set_page(PageSetPayload.model_validate(business))
            if command == "page_patch":
                return await self._patch_page(PagePatchPayload.model_validate(business))
            return {"success": False, "data": None, "error": f"Unknown command: {command}"}
        except ProjectArchivedError:
            return {"success": False, "data": None, "error": "project_archived"}
        except (ValueError, ValidationError) as exc:
            return {"success": False, "data": None, "error": str(exc)}
        except Exception:
            logger.exception("page command failed: %s", command)
            return {"success": False, "data": None, "error": "page_unavailable"}

    async def _require_project(self, project_id: str) -> str:
        async with self._manager.lifecycle_lock(project_id):
            result = await self._manager.handle_command("project_get", {"project_id": project_id})
            if not result.get("success"):
                raise ValueError("project_not_found")
            project = result["data"]["project"]
            if project.get("archived_at") is not None:
                raise ProjectArchivedError(project_id)
            return str(project.get("project_root_locator") or "")

    async def _store(self, project_id: str) -> PageStateStore:
        assert self._stores is not None
        return await self._stores.store(project_id)

    async def _get_page(self, project_id: str) -> dict[str, Any] | None:
        await self._require_project(project_id)
        store = await self._store(project_id)
        return await store.get("main")

    async def _set_page(self, request: PageSetPayload) -> dict[str, Any]:
        await self._require_project(request.project_id)
        if len(request.html.encode("utf-8")) > MAX_HTML_BYTES:
            raise ValueError(f"page html exceeds {MAX_HTML_BYTES} bytes")
        if len(json.dumps(request.data, ensure_ascii=False).encode("utf-8")) > MAX_DATA_BYTES:
            raise ValueError(f"page data exceeds {MAX_DATA_BYTES} bytes")
        store = await self._store(request.project_id)
        page = await store.set_page(
            page_id=request.page_id,
            title=request.title or "页面",
            html=request.html,
            data=request.data,
            updated_by=request.updated_by,
        )
        self._emit_changed(request.project_id, request.page_id, int(page["revision"]))
        return {
            "success": True,
            "data": {
                "page_id": request.page_id,
                "revision": int(page["revision"]),
                "bytes": len(request.html.encode("utf-8")),
                "page": PagePayload.model_validate(page).model_dump(mode="json"),
            },
            "error": None,
        }

    async def _patch_page(self, request: PagePatchPayload) -> dict[str, Any]:
        await self._require_project(request.project_id)
        if len(json.dumps(request.data, ensure_ascii=False).encode("utf-8")) > MAX_DATA_BYTES:
            raise ValueError(f"page data exceeds {MAX_DATA_BYTES} bytes")
        store = await self._store(request.project_id)
        result = await store.patch_data(
            page_id=request.page_id,
            data=request.data,
            updated_by=request.updated_by,
        )
        self._emit_changed(request.project_id, request.page_id, int(result["revision"]))
        return {
            "success": True,
            "data": {
                "page_id": request.page_id,
                "revision": int(result["revision"]),
                "page": await self._get_page(request.project_id),
            },
            "error": None,
        }

    def _emit_changed(self, project_id: str, page_id: str, revision: int) -> None:
        ctx = self._ctx
        if ctx is None or not callable(getattr(ctx, "emit", None)):
            return
        payload = PageChangedPayload(
            project_id=project_id, page_id=page_id, revision=revision
        ).model_dump(mode="json")
        ctx.emit("event/page_changed", payload)
