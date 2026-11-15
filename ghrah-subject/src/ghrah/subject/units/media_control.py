# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目级附件（媒体）的观察端 REST：原始字节上传 + Range 读取。

端点::

    POST /api/v1/projects/{project_id}/attachments
      Content-Type: <MIME>；X-Filename: <URL 编码文件名>（可选）
      → 201 {attachment_id, size, mime_type, filename}

    GET /api/v1/projects/{project_id}/attachments/{attachment_id}
      → 200 字节流（Content-Type / Content-Disposition / Content-Length），
        支持 Range: bytes=start-end（206）；越界 416

鉴权与错误形状沿用能力管理面（``ability_control``）：回环请求直通，非回环
请求必须带 ``Authorization: Bearer <token>``（token 来自
``GHRAH_SUBJECT_OBSERVER_HTTP_API_TOKEN``）；错误统一为
``{"error": {"code", "message", "detail"}}``。

安全边界：

- ``attachment_id`` 只接受 sha256 hex，且文件路径由 id 派生——用户输入永不
  参与路径拼接（防穿越）。
- 附件是项目级资源：先解析 ``project_id`` → Project Root，只读写该 Root 的
  ``attachments/``；不做跨项目引用。
- 上传先按 ``Content-Length`` 早拒，再按实际字节数复核（防谎报长度）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.attachments import (
    is_valid_attachment_id,
    project_attachment_store,
)
from ghrah.subject.runtime.service_keys import OBSERVER_ENDPOINT, PROJECT_MANAGER
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units._http_bytes import (
    content_disposition,
    iter_file_range,
    parse_byte_range,
    read_body_limited,
)
from ghrah.subject.units.ability_control import AbilityApiError, _authorized

__all__ = [
    "MediaControlUnit",
    "attachment_content_disposition",
    "build_media_control_router",
    "parse_byte_range",
]

logger = logging.getLogger(__name__)

ProjectRootLookup = Callable[[str], Awaitable[tuple[str, bool] | None]]
"""按项目返回根定位符和归档状态，项目不存在时返回 ``None``。"""

_FALLBACK_MIME_TYPE = "application/octet-stream"
_MAX_FILENAME_CHARS = 255


def _media_type(content_type: str) -> str:
    """从 ``Content-Type`` 头取裸 MIME（去掉 ``; charset=...`` 等参数）。"""
    return content_type.split(";", 1)[0].strip().lower()


def _mime_allowed(mime_type: str, prefixes: tuple[str, ...]) -> bool:
    """空白名单表示不限制；否则必须是任一前缀的扩展。"""
    if not prefixes:
        return True
    return any(mime_type.startswith(prefix) for prefix in prefixes)


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


attachment_content_disposition = content_disposition
"""兼容别名：``Content-Disposition`` 生成器（实现已抽到 ``_http_bytes``）。"""


def build_media_control_router(
    *,
    config: SubjectConfig,
    project_root: ProjectRootLookup,
    lifecycle_lock: Callable[[str], AbstractAsyncContextManager[None]] | None = None,
) -> APIRouter:
    """构造附件路由；鉴权/错误映射与能力管理面一致。"""
    router = APIRouter(prefix="/api/v1")
    media = config.media

    def _ensure_enabled() -> None:
        if not config.observer_http_api_enabled:
            raise AbilityApiError(
                "capability_unavailable",
                "观测端 HTTP API 未启用（observer_http_api_enabled=false）",
            )
        if not media.enabled:
            raise AbilityApiError(
                "media_disabled",
                "项目级附件端点未启用（GHRAH_SUBJECT_MEDIA_ENABLED=false）",
                status=503,
            )

    def _ensure_authorized(request: Request) -> None:
        _ensure_enabled()
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")

    async def _project_root(project_id: str, *, writing: bool = False) -> str:
        project = await project_root(project_id)
        if project is None:
            raise AbilityApiError("not_found", f"项目不存在：{project_id}")
        root_locator, archived = project
        if writing and archived:
            raise AbilityApiError("project_archived", "项目已归档，附件只读", status=409)
        return root_locator

    async def _upload(project_id: str, request: Request) -> JSONResponse:
        _ensure_authorized(request)
        await _project_root(project_id, writing=True)

        declared = _content_length(request)
        if (
            media.max_upload_bytes > 0
            and declared is not None
            and declared > media.max_upload_bytes
        ):
            raise AbilityApiError(
                "attachment_too_large",
                f"上传超过上限：{declared} > {media.max_upload_bytes} 字节",
                status=413,
            )
        data = await read_body_limited(request, media.max_upload_bytes)
        if not data:
            raise AbilityApiError("invalid_request", "上传内容为空")

        mime_type = _media_type(request.headers.get("content-type", "")) or _FALLBACK_MIME_TYPE
        if not _mime_allowed(mime_type, media.allowed_mime_prefixes):
            raise AbilityApiError(
                "unsupported_attachment_type",
                f"不接受的附件类型：{mime_type}",
                status=415,
            )
        filename = unquote(request.headers.get("x-filename", ""))[:_MAX_FILENAME_CHARS]

        async with lifecycle_lock(project_id) if lifecycle_lock else nullcontext():
            root_locator = await _project_root(project_id, writing=True)
            store = project_attachment_store(root_locator)
            record = await store.save(data, mime_type=mime_type, filename=filename)
        logger.info(
            "attachment stored: project=%s id=%s size=%d mime=%s",
            project_id,
            record.attachment_id,
            record.size,
            record.mime_type,
        )
        return JSONResponse(
            status_code=201,
            content=record.to_wire(),
            headers={
                "Location": f"/api/v1/projects/{project_id}/attachments/{record.attachment_id}"
            },
        )

    router.add_api_route(
        "/projects/{project_id}/attachments",
        _upload,
        methods=["POST"],
    )

    async def _download(project_id: str, attachment_id: str, request: Request) -> StreamingResponse:
        _ensure_authorized(request)
        root_locator = await _project_root(project_id)
        if not is_valid_attachment_id(attachment_id):
            raise AbilityApiError(
                "invalid_attachment_id",
                "attachment_id 必须是 sha256 hex",
                status=400,
            )
        store = project_attachment_store(root_locator)
        record = await store.load(attachment_id)
        if record is None:
            raise AbilityApiError(
                "attachment_not_found",
                f"附件不存在：{attachment_id}",
                status=404,
            )
        path = store.path_for(attachment_id)
        byte_range = None
        if media.range_requests:
            header = request.headers.get("range")
            if header:
                byte_range = parse_byte_range(header, record.size)
        headers = {
            "Content-Disposition": content_disposition(record.filename),
            "Accept-Ranges": "bytes" if media.range_requests else "none",
            "X-Content-Type-Options": "nosniff",
        }
        if byte_range is None:
            return StreamingResponse(
                iter_file_range(path, 0, record.size),
                status_code=200,
                media_type=record.mime_type,
                headers={**headers, "Content-Length": str(record.size)},
            )
        start, end = byte_range
        length = end - start + 1
        return StreamingResponse(
            iter_file_range(path, start, length),
            status_code=206,
            media_type=record.mime_type,
            headers={
                **headers,
                "Content-Length": str(length),
                "Content-Range": f"bytes {start}-{end}/{record.size}",
            },
        )

    router.add_api_route(
        "/projects/{project_id}/attachments/{attachment_id}",
        _download,
        methods=["GET"],
    )
    return router


class MediaControlUnit(SubjectUnit):
    """项目级附件库（上传/读取）REST unit；挂在观测端 HTTP 应用上。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._router: APIRouter | None = None
        self._meta = UnitMeta(
            name="media_control",
            requires=frozenset({OBSERVER_ENDPOINT, PROJECT_MANAGER}),
            routes=RouteSpec(),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def router(self) -> APIRouter:
        if self._router is None:
            raise RuntimeError("MediaControlUnit has not been initialized.")
        return self._router

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        project_manager = ctx.get(PROJECT_MANAGER.name)

        async def project_root(project_id: str) -> tuple[str, bool] | None:
            result = await project_manager.handle_command("project_get", {"project_id": project_id})
            if not result.get("success"):
                return None
            project = (result.get("data") or {}).get("project") or {}
            locator = str(project.get("project_root_locator") or "")
            return (locator, bool(project.get("archived_at"))) if locator else None

        router = build_media_control_router(
            config=self._config,
            project_root=project_root,
            lifecycle_lock=project_manager.lifecycle_lock,
        )
        endpoint.app.include_router(router)
        self._router = router
        logger.info(
            "media control REST mounted (enabled=%s, max_upload_bytes=%s, range=%s)",
            self._config.media.enabled,
            self._config.media.max_upload_bytes,
            self._config.media.range_requests,
        )

    async def stop(self) -> None:
        self._ctx = None
