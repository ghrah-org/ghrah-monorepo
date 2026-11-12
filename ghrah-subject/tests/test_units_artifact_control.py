# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""工作区浏览 + 产物只读面 REST：发布/幂等/列表/读取/Range/穿越/鉴权/隔离。

用 FastAPI TestClient 在进程内打真实路由；工作区与项目根用注入的
lookup 闭包（生产装配里由 PROJECT_MANAGER + WORKSPACE_MANAGER 解析）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.artifacts import ArtifactStore
from ghrah.subject.units.ability_control import register_ability_control_error_handlers
from ghrah.subject.units.artifact_control import (
    ArtifactControlUnit,
    WorkspaceContext,
    build_artifact_control_router,
)

PROJECT_ID = "p1"
WORKSPACE_ID = "w1"
TOKEN = "s3cret"
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>"
TEXT = b"hello world"


def _tree(tmp_path: Path, name: str = "project") -> tuple[Path, Path]:
    """造项目根 + 工作区树，返回 (project_root, workspace_root)。"""
    project_root = tmp_path / name
    project_root.mkdir(parents=True)
    (project_root / "artifacts").mkdir()
    ws_root = tmp_path / f"{name}-ws"
    (ws_root / "outputs" / "requirements").mkdir(parents=True)
    (ws_root / "outputs" / "requirements" / "req.svg").write_bytes(SVG)
    (ws_root / "notes.txt").write_bytes(TEXT)
    (ws_root / "index.html").write_bytes(b"<html><script>alert(1)</script></html>")
    outside = tmp_path / f"{name}-outside.txt"
    outside.write_bytes(b"secret")
    (ws_root / "escape.txt").symlink_to(outside)
    return project_root, ws_root


def _client(
    tmp_path: Path,
    *,
    token: str = "",
    api_enabled: bool = True,
    archived: bool = False,
) -> tuple[TestClient, Path, Path]:
    project_root, ws_root = _tree(tmp_path)
    config = SubjectConfig(
        observer_http_api_token=token,
        observer_http_api_enabled=api_enabled,
    )

    async def resolve_project(project_id: str) -> str | None:
        return str(project_root) if project_id == PROJECT_ID else None

    async def resolve_workspace(project_id: str, workspace_id: str) -> WorkspaceContext | None:
        if project_id != PROJECT_ID or workspace_id != WORKSPACE_ID:
            return None
        return WorkspaceContext(
            project_id=project_id,
            project_root_locator=str(project_root),
            workspace_id=workspace_id,
            root=ws_root.resolve(),
            archived=archived,
        )

    app = FastAPI()
    register_ability_control_error_handlers(app)
    app.include_router(
        build_artifact_control_router(
            config=config,
            resolve_project=resolve_project,
            resolve_workspace=resolve_workspace,
        )
    )
    return TestClient(app), project_root, ws_root


def _files_url() -> str:
    return f"/api/v1/projects/{PROJECT_ID}/workspaces/{WORKSPACE_ID}/files"


def _file_url() -> str:
    return f"/api/v1/projects/{PROJECT_ID}/workspaces/{WORKSPACE_ID}/file"


def _artifacts_url() -> str:
    return f"/api/v1/projects/{PROJECT_ID}/artifacts"


# ─── 工作区浏览 ───


def test_list_workspace_root_and_nested(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    root = client.get(_files_url())
    assert root.status_code == 200, root.text
    body = root.json()
    assert body["path"] == ""
    entries = {entry["name"]: entry for entry in body["entries"]}
    assert set(entries) == {"index.html", "notes.txt", "outputs", "escape.txt"}
    assert entries["outputs"]["type"] == "dir"
    assert entries["notes.txt"] == {
        "name": "notes.txt",
        "path": "notes.txt",
        "type": "file",
        "size": len(TEXT),
        "mtime": entries["notes.txt"]["mtime"],
    }

    nested = client.get(_files_url(), params={"path": "outputs/requirements"})
    assert nested.status_code == 200, nested.text
    assert nested.json()["path"] == "outputs/requirements"
    [entry] = nested.json()["entries"]
    assert entry["name"] == "req.svg"
    assert entry["path"] == "outputs/requirements/req.svg"
    assert entry["size"] == len(SVG)


def test_list_errors(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    missing = client.get(_files_url(), params={"path": "nope"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "artifact_not_found"

    not_dir = client.get(_files_url(), params={"path": "notes.txt"})
    assert not_dir.status_code == 400
    assert not_dir.json()["error"]["code"] == "invalid_request"

    # 跨项目：workspace 不属于该项目即不可见
    crossed = client.get(f"/api/v1/projects/other/workspaces/{WORKSPACE_ID}/files")
    assert crossed.status_code == 404
    assert crossed.json()["error"]["code"] == "workspace_not_found"


def test_list_path_safety(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    for bad in ("../project-outside.txt", "/etc/passwd", "outputs/../../x", "..\\x"):
        response = client.get(_files_url(), params={"path": bad})
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "path_outside_workspace"


# ─── 工作区文件读取 ───


def test_read_workspace_file_and_range(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    response = client.get(_file_url(), params={"path": "notes.txt"})
    assert response.status_code == 200
    assert response.content == TEXT
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")

    partial = client.get(_file_url(), params={"path": "notes.txt"}, headers={"Range": "bytes=0-4"})
    assert partial.status_code == 206
    assert partial.content == TEXT[:5]
    assert partial.headers["content-range"] == f"bytes 0-4/{len(TEXT)}"

    beyond = client.get(_file_url(), params={"path": "notes.txt"}, headers={"Range": "bytes=99-"})
    assert beyond.status_code == 416
    assert beyond.json()["error"]["code"] == "range_not_satisfiable"


def test_read_workspace_file_errors(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    missing = client.get(_file_url(), params={"path": "nope.svg"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "artifact_not_found"

    is_dir = client.get(_file_url(), params={"path": "outputs"})
    assert is_dir.status_code == 400
    assert is_dir.json()["error"]["code"] == "invalid_request"

    # 符号链接逃逸：resolve 后不在工作区根下
    escape = client.get(_file_url(), params={"path": "escape.txt"})
    assert escape.status_code == 400
    assert escape.json()["error"]["code"] == "path_outside_workspace"

    # HTML 不作为 HTML 输出（防同源脚本执行）
    html = client.get(_file_url(), params={"path": "index.html"})
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/plain")


# ─── 产物发布 ───


def test_publish_artifact_and_read_back(tmp_path: Path) -> None:
    client, project_root, _ = _client(tmp_path)
    published = client.post(
        _artifacts_url(),
        json={
            "workspace_id": WORKSPACE_ID,
            "path": "outputs/requirements/req.svg",
            "title": "REQ-DRAFT-0001",
        },
    )
    assert published.status_code == 201, published.text
    body = published.json()
    expected_id = hashlib.sha256(SVG).hexdigest()
    assert body["artifact_id"] == expected_id
    assert body["filename"] == "req.svg"
    assert body["mime_type"] == "image/svg+xml"
    assert body["size"] == len(SVG)
    assert body["title"] == "REQ-DRAFT-0001"
    assert body["source"] == {
        "workspace_id": WORKSPACE_ID,
        "path": "outputs/requirements/req.svg",
    }
    assert body["published_at"]
    assert published.headers["location"].endswith(f"/artifacts/{expected_id}")
    assert (project_root / "artifacts" / expected_id).read_bytes() == SVG

    content = client.get(f"{_artifacts_url()}/{expected_id}/content")
    assert content.status_code == 200
    assert content.content == SVG
    assert content.headers["content-type"] == "image/svg+xml"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-length"] == str(len(SVG))
    assert content.headers["content-disposition"].startswith("attachment;")

    partial = client.get(
        f"{_artifacts_url()}/{expected_id}/content", headers={"Range": "bytes=0-3"}
    )
    assert partial.status_code == 206
    assert partial.content == SVG[:4]
    assert partial.headers["content-range"] == f"bytes 0-3/{len(SVG)}"

    beyond = client.get(
        f"{_artifacts_url()}/{expected_id}/content", headers={"Range": "bytes=999-"}
    )
    assert beyond.status_code == 416
    assert beyond.json()["error"]["code"] == "range_not_satisfiable"


def test_publish_is_idempotent(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    first = client.post(
        _artifacts_url(),
        json={
            "workspace_id": WORKSPACE_ID,
            "path": "outputs/requirements/req.svg",
            "title": "first",
        },
    ).json()
    second = client.post(
        _artifacts_url(),
        json={
            "workspace_id": WORKSPACE_ID,
            "path": "outputs/requirements/req.svg",
            "title": "second",
            "mime_type": "text/plain",
        },
    ).json()
    assert second["artifact_id"] == first["artifact_id"]
    assert second["title"] == "first"
    assert second["mime_type"] == "image/svg+xml"


def test_published_artifact_is_an_immutable_copy(tmp_path: Path) -> None:
    """产物是发布副本：工作区源文件后续变化不影响已发布字节。"""
    client, _, ws_root = _client(tmp_path)
    created = client.post(
        _artifacts_url(), json={"workspace_id": WORKSPACE_ID, "path": "notes.txt"}
    ).json()
    (ws_root / "notes.txt").write_bytes(b"changed after publish")
    content = client.get(f"{_artifacts_url()}/{created['artifact_id']}/content")
    assert content.status_code == 200
    assert content.content == TEXT


def test_publish_honours_explicit_mime_type(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    body = client.post(
        _artifacts_url(),
        json={
            "workspace_id": WORKSPACE_ID,
            "path": "notes.txt",
            "mime_type": "text/markdown",
        },
    ).json()
    assert body["mime_type"] == "text/markdown"
    # 后缀猜 MIME 的缺省路径（.svg → image/svg+xml）
    guessed = client.post(
        _artifacts_url(),
        json={"workspace_id": WORKSPACE_ID, "path": "outputs/requirements/req.svg"},
    ).json()
    assert guessed["mime_type"] == "image/svg+xml"


def test_publish_errors(tmp_path: Path, monkeypatch: Any) -> None:
    client, _, _ = _client(tmp_path)
    # HTML 产物拒绝
    html = client.post(
        _artifacts_url(),
        json={"workspace_id": WORKSPACE_ID, "path": "index.html"},
    )
    assert html.status_code == 415
    assert html.json()["error"]["code"] == "unsupported_artifact_type"

    # 源文件不存在 / 穿越 / 工作区不属于项目 / 非法请求体
    missing = client.post(_artifacts_url(), json={"workspace_id": WORKSPACE_ID, "path": "nope.svg"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "artifact_not_found"

    escape = client.post(_artifacts_url(), json={"workspace_id": WORKSPACE_ID, "path": "../x.svg"})
    assert escape.status_code == 400
    assert escape.json()["error"]["code"] == "path_outside_workspace"

    wrong_ws = client.post(_artifacts_url(), json={"workspace_id": "other", "path": "notes.txt"})
    assert wrong_ws.status_code == 404
    assert wrong_ws.json()["error"]["code"] == "workspace_not_found"

    bad_json = client.post(
        _artifacts_url(),
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert bad_json.status_code == 400
    assert bad_json.json()["error"]["code"] == "invalid_request"

    missing_field = client.post(_artifacts_url(), json={"workspace_id": WORKSPACE_ID})
    assert missing_field.status_code == 400
    assert missing_field.json()["error"]["code"] == "invalid_request"

    # 超限：把上限缩到 8 字节（notes.txt 是 11 字节）
    from ghrah.subject.units import artifact_control

    monkeypatch.setattr(artifact_control, "MAX_ARTIFACT_BYTES", 8)
    too_large = client.post(
        _artifacts_url(), json={"workspace_id": WORKSPACE_ID, "path": "notes.txt"}
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "artifact_too_large"


def test_publish_rejected_for_archived_project(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path, archived=True)
    response = client.post(
        _artifacts_url(), json={"workspace_id": WORKSPACE_ID, "path": "notes.txt"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_archived"


# ─── 产物列表与读取 ───


def test_artifact_list_order_and_content_errors(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    for path in ("notes.txt", "outputs/requirements/req.svg"):
        assert (
            client.post(
                _artifacts_url(), json={"workspace_id": WORKSPACE_ID, "path": path}
            ).status_code
            == 201
        )
    listing = client.get(_artifacts_url())
    assert listing.status_code == 200
    artifacts = listing.json()["artifacts"]
    assert len(artifacts) == 2
    published = [item["published_at"] for item in artifacts]
    assert published == sorted(published, reverse=True)
    ids = {item["artifact_id"] for item in artifacts}
    assert ids == {
        hashlib.sha256(TEXT).hexdigest(),
        hashlib.sha256(SVG).hexdigest(),
    }

    missing = client.get(f"{_artifacts_url()}/{'c' * 64}/content")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "artifact_not_found"

    invalid = client.get(f"{_artifacts_url()}/not-a-sha/content")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_request"

    unknown_project = client.get("/api/v1/projects/missing/artifacts")
    assert unknown_project.status_code == 404
    assert unknown_project.json()["error"]["code"] == "not_found"


def test_artifact_content_html_sidecar_is_downgraded(tmp_path: Path) -> None:
    """旁路写入 html 产物（不经过发布校验）时，读取也降级为 text/plain。"""
    client, project_root, _ = _client(tmp_path)
    store = ArtifactStore(project_root / "artifacts")
    import asyncio

    record = asyncio.run(
        store.publish(
            b"<html><script>alert(1)</script></html>",
            filename="evil.html",
            mime_type="text/html",
            title="evil",
            source={},
        )
    )
    response = client.get(f"{_artifacts_url()}/{record.artifact_id}/content")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_artifacts_are_project_scoped(tmp_path: Path) -> None:
    project_a, ws_a = _tree(tmp_path, "a")
    project_b, ws_b = _tree(tmp_path, "b")
    roots = {"pa": (project_a, ws_a), "pb": (project_b, ws_b)}
    config = SubjectConfig()

    async def resolve_project(project_id: str) -> str | None:
        pair = roots.get(project_id)
        return str(pair[0]) if pair else None

    async def resolve_workspace(project_id: str, workspace_id: str) -> WorkspaceContext | None:
        pair = roots.get(project_id)
        if pair is None or workspace_id != "w":
            return None
        return WorkspaceContext(
            project_id=project_id,
            project_root_locator=str(pair[0]),
            workspace_id="w",
            root=pair[1].resolve(),
        )

    app = FastAPI()
    register_ability_control_error_handlers(app)
    app.include_router(
        build_artifact_control_router(
            config=config,
            resolve_project=resolve_project,
            resolve_workspace=resolve_workspace,
        )
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/projects/pa/artifacts", json={"workspace_id": "w", "path": "notes.txt"}
    ).json()
    assert client.get("/api/v1/projects/pa/artifacts").json()["artifacts"]
    assert client.get("/api/v1/projects/pb/artifacts").json()["artifacts"] == []
    crossed = client.get(f"/api/v1/projects/pb/artifacts/{created['artifact_id']}/content")
    assert crossed.status_code == 404
    assert crossed.json()["error"]["code"] == "artifact_not_found"


async def test_real_units_workspace_resolution_and_publish(tmp_path: Path) -> None:
    """真实装配：PROJECT_MANAGER.project_get + WORKSPACE_MANAGER 解析工作区根。

    覆盖 REST 的注入边界之外的部分：project.workspaces 挂载关系 →
    WorkspaceRecord.locator → 工作区根目录。
    """
    from ghrah.ability.access import GrantPolicy
    from ouroboros import Context  # type: ignore[import-untyped]

    from ghrah.subject.config import ProjectConfig
    from ghrah.subject.runtime.ouroboros_bridge import bridge_command
    from ghrah.subject.units import mount_builtin_units
    from ghrah.subject.workspace import path_to_locator

    workspace = tmp_path / "agent-ws"
    (workspace / "outputs").mkdir(parents=True)
    (workspace / "outputs" / "req.svg").write_bytes(SVG)

    config = SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        ability_grant_policy=GrantPolicy(mode="merge"),
        project_slice=ProjectConfig(
            default_root_locator_template=str(tmp_path / "projects" / "{project_id}")
        ),
    )
    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        created = await bridge_command(
            ctx,
            "project_create",
            {
                "name": "artifacts",
                "writable_workspaces": [
                    {
                        "locator": path_to_locator(str(workspace)),
                        "name": "default",
                        "default_for_agents": True,
                    }
                ],
            },
        )
        assert created["success"], created
        project = created["data"]["project"]
        project_id = project["project_id"]
        workspace_id = project["workspaces"][0]["workspace_id"]

        client = TestClient(ctx.get("observer_endpoint").app)
        listing = client.get(
            f"/api/v1/projects/{project_id}/workspaces/{workspace_id}/files",
            params={"path": "outputs"},
        )
        assert listing.status_code == 200, listing.text
        [entry] = listing.json()["entries"]
        assert entry["path"] == "outputs/req.svg"

        published = client.post(
            f"/api/v1/projects/{project_id}/artifacts",
            json={
                "workspace_id": workspace_id,
                "path": "outputs/req.svg",
                "title": "REQ",
            },
        )
        assert published.status_code == 201, published.text
        artifact = published.json()
        assert artifact["source"] == {
            "workspace_id": workspace_id,
            "path": "outputs/req.svg",
        }
        content = client.get(
            f"/api/v1/projects/{project_id}/artifacts/{artifact['artifact_id']}/content"
        )
        assert content.status_code == 200
        assert content.content == SVG


# ─── 鉴权与开关 ───


def test_non_loopback_requires_bearer_token(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path, token=TOKEN)
    remote = TestClient(client.app, client=("10.9.8.7", 4321))

    denied = remote.get(_artifacts_url())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "access_denied"

    allowed = remote.get(_artifacts_url(), headers={"Authorization": f"Bearer {TOKEN}"})
    assert allowed.status_code == 200

    workspace_denied = remote.get(_files_url())
    assert workspace_denied.status_code == 403
    workspace_allowed = remote.get(_files_url(), headers={"Authorization": f"Bearer {TOKEN}"})
    assert workspace_allowed.status_code == 200


def test_http_api_disabled_returns_503(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path, api_enabled=False)
    for response in (
        client.get(_files_url()),
        client.get(_file_url(), params={"path": "notes.txt"}),
        client.get(_artifacts_url()),
        client.post(_artifacts_url(), json={"workspace_id": WORKSPACE_ID, "path": "notes.txt"}),
    ):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "capability_unavailable"


def test_artifact_unit_meta() -> None:
    unit = ArtifactControlUnit(SubjectConfig())
    assert unit.meta.name == "artifact_control"
    assert {key.name for key in unit.meta.requires} == {
        "observer_endpoint",
        "project_manager",
        "workspace_manager",
    }
    assert unit.meta.routes.commands == frozenset()


def test_workspace_context_serializable() -> None:
    # 上下文是冻结 dataclass，路径解析依赖它的 root
    context = WorkspaceContext(
        project_id="p",
        project_root_locator="/tmp/p",
        workspace_id="w",
        root=Path("/tmp/p"),
    )
    assert json.dumps({"workspace_id": context.workspace_id}) == '{"workspace_id": "w"}'
