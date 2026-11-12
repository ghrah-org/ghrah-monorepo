# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目工作区浏览与产物（Artifact）只读面 REST。

端点（wire 契约冻结在 M3 任务书 §2.2）::

    GET  /api/v1/projects/{pid}/workspaces/{wid}/files?path=
         → {entries:[{name,path,type,size?,mtime?}], path}
    GET  /api/v1/projects/{pid}/workspaces/{wid}/file?path=
         → 文件字节（Range 支持；用于发布前预览）
    POST /api/v1/projects/{pid}/artifacts
         body {workspace_id, path, mime_type?, title?} → 201 产物记录
    GET  /api/v1/projects/{pid}/artifacts
         → {artifacts:[...]}（published_at 倒序，最多 200）
    GET  /api/v1/projects/{pid}/artifacts/{artifact_id}/content
         → 产物字节（Range 206；Content-Disposition/Content-Type）

鉴权与错误形状沿用能力/媒体管理面：回环直通，非回环必须
``Authorization: Bearer <token>``；错误统一 ``{"error": {code, message, detail}}``。

安全边界（本模块最重要的一条）：

- **路径只做「工作区相对路径」**：先拒绝绝对路径与任何 ``..`` 段，再用
  ``Path.resolve()`` 解析（跟随符号链接）并校验仍在工作区根之下——因此
  ``..`` 拼接与符号链接逃逸都会被 ``path_outside_workspace`` 拒绝。
- 工作区根来自 Project 的挂载关系 + WorkspaceRecord.locator，不信任 query。
- 产物是**发布副本**（``<project_root>/artifacts/``），id 是内容 sha256，
  不参与寻址；跨项目读不到彼此产物。
- 一切字节响应带 ``X-Content-Type-Options: nosniff``，且 ``text/html`` /
  ``application/xhtml+xml`` 既不接受发布（415）也不会被当作 HTML 输出
  （读取时降级为 ``text/plain``）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.artifacts import (
    MAX_ARTIFACTS_LISTED,
    ArtifactRecord,
    is_valid_artifact_id,
    project_artifact_store,
)
from ghrah.subject.runtime.service_keys import (
    OBSERVER_ENDPOINT,
    PROJECT_MANAGER,
    WORKSPACE_MANAGER,
)
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units._http_bytes import (
    content_disposition,
    iter_file_range,
    parse_byte_range,
    read_body_limited,
)
from ghrah.subject.units.ability_control import AbilityApiError, _authorized
from ghrah.subject.workspace import locator_to_path

__all__ = [
    "MAX_ARTIFACT_BYTES",
    "ArtifactControlUnit",
    "WorkspaceContext",
    "build_artifact_control_router",
    "resolve_workspace_path",
]

logger = logging.getLogger(__name__)

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
"""发布单文件上限（字节）；产物是发布副本，超过视为异常产物。"""

_FALLBACK_MIME_TYPE = "application/octet-stream"
_MAX_JSON_BODY_BYTES = 1024 * 1024
_MAX_TITLE_CHARS = 200
_MAX_FILENAME_CHARS = 255
_BLOCKED_HTML_MIME_TYPES = frozenset({"text/html", "application/xhtml+xml"})

ResolveProject = Callable[[str], Awaitable[str | None]]
"""``project_id -> project_root_locator``；项目不存在返回 ``None``。"""


@dataclass(frozen=True)
class WorkspaceContext:
    """一次工作区请求解析出的受信上下文（全部来自服务端事实）。"""

    project_id: str
    project_root_locator: str
    workspace_id: str
    root: Path
    archived: bool = False


ResolveWorkspace = Callable[[str, str], Awaitable[WorkspaceContext | None]]
"""``(project_id, workspace_id) -> 工作区上下文``；解析失败返回 ``None``。"""


def guess_mime_type(filename: str) -> str:
    """按文件名后缀猜 MIME；猜不出用 ``application/octet-stream``。"""
    guessed, _ = mimetypes.guess_type(filename)
    return (guessed or _FALLBACK_MIME_TYPE).lower()


def _output_mime_type(mime_type: str) -> str:
    """输出 MIME 兜底：HTML 类一律降级为 ``text/plain``（防同源脚本执行）。"""
    normalized = (mime_type or "").strip().lower() or _FALLBACK_MIME_TYPE
    if normalized in _BLOCKED_HTML_MIME_TYPES:
        return "text/plain"
    return normalized


def _relative_posix(root: Path, target: Path) -> str:
    """工作区相对路径（POSIX 形态）；根自身返回空串。"""
    try:
        relative = target.relative_to(root)
    except ValueError:
        return ""
    return relative.as_posix() if str(relative) != "." else ""


def resolve_workspace_path(root: Path, raw: str) -> Path:
    """把工作区相对路径解析为绝对路径，拒绝各种逃逸。

    Args:
        root: 工作区根（调用方须已 ``resolve()``）。
        raw: 请求里的相对路径（``""``/``"."`` 表示根）。

    Returns:
        解析后的绝对路径（可能不存在；存在性由调用方判断）。

    Raises:
        AbilityApiError: ``path_outside_workspace``（绝对路径 / ``..`` 段 /
            符号链接逃逸 / 含 NUL）。
    """
    value = (raw or "").strip().replace("\\", "/")
    if "\x00" in value:
        raise AbilityApiError("path_outside_workspace", "路径包含非法字符", status=400)
    if value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        raise AbilityApiError("path_outside_workspace", "只接受工作区相对路径", status=400)
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise AbilityApiError("path_outside_workspace", "路径不允许包含 ..", status=400)
    resolved_root = root.resolve()
    target = (resolved_root / Path(*parts)).resolve() if parts else resolved_root
    if target != resolved_root and resolved_root not in target.parents:
        raise AbilityApiError("path_outside_workspace", "路径逃出工作区根", status=400)
    return target


def _stream_file(
    request: Request,
    path: Path,
    *,
    mime_type: str,
    filename: str,
) -> StreamingResponse:
    """按 Range（若有）流式返回文件；统一 nosniff + attachment 处置。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise AbilityApiError("artifact_not_found", f"文件不可读：{filename}", status=404) from exc
    headers = {
        "Content-Disposition": content_disposition(filename),
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
    }
    output_type = _output_mime_type(mime_type)
    byte_range: tuple[int, int] | None = None
    header = request.headers.get("range")
    if header:
        byte_range = parse_byte_range(header, size)
    if byte_range is None:
        return StreamingResponse(
            iter_file_range(path, 0, size),
            status_code=200,
            media_type=output_type,
            headers={**headers, "Content-Length": str(size)},
        )
    start, end = byte_range
    length = end - start + 1
    return StreamingResponse(
        iter_file_range(path, start, length),
        status_code=206,
        media_type=output_type,
        headers={
            **headers,
            "Content-Length": str(length),
            "Content-Range": f"bytes {start}-{end}/{size}",
        },
    )


def _list_entries(root: Path, directory: Path) -> list[dict[str, Any]]:
    """列目录：名称排序、只读元数据；单个条目 stat 失败则跳过。"""
    entries: list[dict[str, Any]] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        try:
            stat = entry.stat()
        except OSError:
            continue
        is_dir = entry.is_dir()
        item: dict[str, Any] = {
            "name": entry.name,
            "path": _relative_posix(root, entry),
            "type": "dir" if is_dir else "file",
        }
        if not is_dir:
            item["size"] = stat.st_size
        item["mtime"] = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        entries.append(item)
    return entries


def _parse_publish_body(body: bytes) -> tuple[str, str, str | None, str | None]:
    """解析发布请求体；返回 ``(workspace_id, path, mime_type?, title?)``。"""
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise AbilityApiError("invalid_request", "请求体必须是 JSON") from exc
    if not isinstance(payload, dict):
        raise AbilityApiError("invalid_request", "请求体必须是 JSON 对象")
    workspace_id = payload.get("workspace_id")
    path = payload.get("path")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise AbilityApiError("invalid_request", "workspace_id required")
    if not isinstance(path, str) or not path:
        raise AbilityApiError("invalid_request", "path required")
    mime_type = payload.get("mime_type")
    title = payload.get("title")
    if mime_type is not None and not isinstance(mime_type, str):
        raise AbilityApiError("invalid_request", "mime_type 必须是字符串")
    if title is not None and not isinstance(title, str):
        raise AbilityApiError("invalid_request", "title 必须是字符串")
    return workspace_id, path, mime_type, title


def build_artifact_control_router(
    *,
    config: SubjectConfig,
    resolve_project: ResolveProject,
    resolve_workspace: ResolveWorkspace,
) -> APIRouter:
    """构造工作区浏览 + 产物只读面路由；鉴权/错误形状与能力管理面一致。"""
    router = APIRouter(prefix="/api/v1")

    def _ensure_enabled() -> None:
        if not config.observer_http_api_enabled:
            raise AbilityApiError(
                "capability_unavailable",
                "观测端 HTTP API 未启用（observer_http_api_enabled=false）",
            )

    def _authorize(request: Request) -> None:
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")

    async def _project_locator(request: Request, project_id: str) -> str:
        _authorize(request)
        locator = await resolve_project(project_id)
        if not locator:
            raise AbilityApiError("not_found", f"项目不存在：{project_id}", status=404)
        return locator

    async def _workspace(request: Request, project_id: str, workspace_id: str) -> WorkspaceContext:
        _authorize(request)
        context = await resolve_workspace(project_id, workspace_id)
        if context is None:
            raise AbilityApiError(
                "workspace_not_found",
                f"工作区不存在或不属于该项目：{workspace_id}",
                status=404,
            )
        return context

    async def _list_files(
        project_id: str, workspace_id: str, request: Request, path: str = ""
    ) -> dict[str, Any]:
        _ensure_enabled()
        context = await _workspace(request, project_id, workspace_id)
        target = resolve_workspace_path(context.root, path)
        if not target.exists():
            raise AbilityApiError("artifact_not_found", f"路径不存在：{path}", status=404)
        if not target.is_dir():
            raise AbilityApiError("invalid_request", f"不是目录：{path}")
        entries = await asyncio.to_thread(_list_entries, context.root, target)
        return {"entries": entries, "path": _relative_posix(context.root, target)}

    router.add_api_route(
        "/projects/{project_id}/workspaces/{workspace_id}/files",
        _list_files,
        methods=["GET"],
    )

    async def _get_workspace_file(
        project_id: str, workspace_id: str, request: Request, path: str = ""
    ) -> StreamingResponse:
        _ensure_enabled()
        context = await _workspace(request, project_id, workspace_id)
        target = resolve_workspace_path(context.root, path)
        if not target.exists():
            raise AbilityApiError("artifact_not_found", f"路径不存在：{path}", status=404)
        if not target.is_file():
            raise AbilityApiError("invalid_request", f"不是文件：{path}")
        return _stream_file(
            request,
            target,
            mime_type=guess_mime_type(target.name),
            filename=target.name,
        )

    router.add_api_route(
        "/projects/{project_id}/workspaces/{workspace_id}/file",
        _get_workspace_file,
        methods=["GET"],
    )

    async def _publish(project_id: str, request: Request) -> JSONResponse:
        _ensure_enabled()
        raw = await read_body_limited(
            request,
            _MAX_JSON_BODY_BYTES,
            too_large_code="invalid_request",
            too_large_status=400,
        )
        workspace_id, path, mime_type, title = _parse_publish_body(raw)
        context = await _workspace(request, project_id, workspace_id)
        if context.archived:
            raise AbilityApiError("project_archived", "项目已归档，产物面只读", status=409)
        target = resolve_workspace_path(context.root, path)
        if not target.exists() or not target.is_file():
            raise AbilityApiError("artifact_not_found", f"源文件不存在：{path}", status=404)
        size = target.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            raise AbilityApiError(
                "artifact_too_large",
                f"产物超过上限：{size} > {MAX_ARTIFACT_BYTES} 字节",
                status=413,
            )
        data = await asyncio.to_thread(target.read_bytes)
        resolved_mime = (mime_type or guess_mime_type(target.name)).strip().lower()
        if not resolved_mime or resolved_mime in _BLOCKED_HTML_MIME_TYPES:
            raise AbilityApiError(
                "unsupported_artifact_type",
                f"不接受的产物类型：{resolved_mime or '(empty)'}",
                status=415,
            )
        filename = target.name[:_MAX_FILENAME_CHARS]
        store = project_artifact_store(context.project_root_locator)
        record = await store.publish(
            data,
            filename=filename,
            mime_type=resolved_mime,
            title=(title or "").strip()[:_MAX_TITLE_CHARS],
            source={
                "workspace_id": context.workspace_id,
                "path": _relative_posix(context.root, target),
            },
        )
        logger.info(
            "artifact published: project=%s id=%s size=%d mime=%s",
            project_id,
            record.artifact_id,
            record.size,
            record.mime_type,
        )
        return JSONResponse(
            status_code=201,
            content=record.to_wire(),
            headers={"Location": (f"/api/v1/projects/{project_id}/artifacts/{record.artifact_id}")},
        )

    router.add_api_route(
        "/projects/{project_id}/artifacts",
        _publish,
        methods=["POST"],
    )

    async def _list_artifacts(project_id: str, request: Request) -> dict[str, Any]:
        _ensure_enabled()
        locator = await _project_locator(request, project_id)
        store = project_artifact_store(locator)
        records: list[ArtifactRecord] = await store.list(MAX_ARTIFACTS_LISTED)
        return {"artifacts": [record.to_wire() for record in records]}

    router.add_api_route(
        "/projects/{project_id}/artifacts",
        _list_artifacts,
        methods=["GET"],
    )

    async def _artifact_content(
        project_id: str, artifact_id: str, request: Request
    ) -> StreamingResponse:
        _ensure_enabled()
        locator = await _project_locator(request, project_id)
        if not is_valid_artifact_id(artifact_id):
            raise AbilityApiError(
                "invalid_request",
                "artifact_id 必须是 sha256 hex",
            )
        store = project_artifact_store(locator)
        record = await store.load(artifact_id)
        if record is None:
            raise AbilityApiError(
                "artifact_not_found",
                f"产物不存在：{artifact_id}",
                status=404,
            )
        return _stream_file(
            request,
            store.path_for(artifact_id),
            mime_type=record.mime_type,
            filename=record.filename,
        )

    router.add_api_route(
        "/projects/{project_id}/artifacts/{artifact_id}/content",
        _artifact_content,
        methods=["GET"],
    )
    return router


class ArtifactControlUnit(SubjectUnit):
    """工作区浏览 + 产物只读面 REST unit；挂在观测端 HTTP 应用上。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._router: APIRouter | None = None
        self._meta = UnitMeta(
            name="artifact_control",
            requires=frozenset({OBSERVER_ENDPOINT, PROJECT_MANAGER, WORKSPACE_MANAGER}),
            routes=RouteSpec(),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def router(self) -> APIRouter:
        if self._router is None:
            raise RuntimeError("ArtifactControlUnit has not been initialized.")
        return self._router

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        project_manager = ctx.get(PROJECT_MANAGER.name)
        workspace_manager = ctx.get(WORKSPACE_MANAGER.name)

        async def _project_record(project_id: str) -> dict[str, Any] | None:
            result = await project_manager.handle_command("project_get", {"project_id": project_id})
            if not result.get("success"):
                return None
            project = (result.get("data") or {}).get("project") or {}
            return project or None

        async def resolve_project(project_id: str) -> str | None:
            project = await _project_record(project_id)
            if project is None:
                return None
            locator = str(project.get("project_root_locator") or "")
            return locator or None

        async def resolve_workspace(project_id: str, workspace_id: str) -> WorkspaceContext | None:
            project = await _project_record(project_id)
            if project is None:
                return None
            root_locator = str(project.get("project_root_locator") or "")
            if not root_locator:
                return None
            mounts = project.get("workspaces") or []
            mounted = any(
                isinstance(mount, dict) and str(mount.get("workspace_id") or "") == workspace_id
                for mount in mounts
            )
            if not mounted:
                return None
            record = workspace_manager.get_record(workspace_id)
            if record is None or record.deleted_at is not None:
                return None
            try:
                root = Path(locator_to_path(record.locator)).resolve()
            except ValueError:
                return None
            if not root.is_dir():
                return None
            return WorkspaceContext(
                project_id=project_id,
                project_root_locator=root_locator,
                workspace_id=workspace_id,
                root=root,
                archived=bool(project.get("archived_at") or project.get("deleted_at")),
            )

        router = build_artifact_control_router(
            config=self._config,
            resolve_project=resolve_project,
            resolve_workspace=resolve_workspace,
        )
        endpoint.app.include_router(router)
        self._router = router
        logger.info(
            "artifact control REST mounted (enabled=%s)", self._config.observer_http_api_enabled
        )

    async def stop(self) -> None:
        self._ctx = None
