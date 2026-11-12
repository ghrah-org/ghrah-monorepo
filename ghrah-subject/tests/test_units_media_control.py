# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目级附件 REST：上传/幂等/读取/Range/错误码/鉴权/路径穿越。

用 FastAPI TestClient 在进程内打真实路由（不启端口）：``client=("10.x", ...)``
模拟非回环请求，验证 Bearer 鉴权分支；默认 ``testclient`` 主机属回环直通。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ghrah.subject.config import MediaConfig, SubjectConfig
from ghrah.subject.project.attachments import (
    AttachmentStore,
    is_valid_attachment_id,
    project_attachment_store,
)
from ghrah.subject.units.ability_control import register_ability_control_error_handlers
from ghrah.subject.units.media_control import (
    MediaControlUnit,
    build_media_control_router,
    parse_byte_range,
)

PROJECT_ID = "proj-1"
TOKEN = "s3cret"


def _app(
    tmp_path: Path,
    *,
    media: MediaConfig | None = None,
    token: str = "",
    observer_http_api_enabled: bool = True,
) -> tuple[TestClient, Path]:
    root = tmp_path / "project"
    root.mkdir(parents=True)

    config = SubjectConfig(
        media_slice=media or MediaConfig(),
        observer_http_api_token=token,
        observer_http_api_enabled=observer_http_api_enabled,
    )

    async def project_root(project_id: str) -> str | None:
        return str(root) if project_id == PROJECT_ID else None

    app = FastAPI()
    register_ability_control_error_handlers(app)
    app.include_router(build_media_control_router(config=config, project_root=project_root))
    return TestClient(app), root


def _upload_url() -> str:
    return f"/api/v1/projects/{PROJECT_ID}/attachments"


def _download_url(attachment_id: str) -> str:
    return f"{_upload_url()}/{attachment_id}"


PAYLOAD = b"0123456789"
PAYLOAD_MIME = "video/mp4"


def test_upload_idempotent_get_and_headers(tmp_path: Path) -> None:
    client, root = _app(tmp_path)
    filename = quote("演示.mp4")
    first = client.post(
        _upload_url(),
        content=PAYLOAD,
        headers={"Content-Type": PAYLOAD_MIME, "X-Filename": filename},
    )
    assert first.status_code == 201, first.text
    body = first.json()
    expected_id = hashlib.sha256(PAYLOAD).hexdigest()
    assert body == {
        "attachment_id": expected_id,
        "size": len(PAYLOAD),
        "mime_type": PAYLOAD_MIME,
        "filename": "演示.mp4",
    }
    assert first.headers["Location"].endswith(f"/attachments/{expected_id}")

    # 内容寻址落盘：字节 + sidecar 元数据
    attachments = root / "attachments"
    assert (attachments / expected_id).read_bytes() == PAYLOAD
    sidecar = json.loads((attachments / f"{expected_id}.json").read_text(encoding="utf-8"))
    assert sidecar["mime_type"] == PAYLOAD_MIME
    assert sidecar["filename"] == "演示.mp4"
    assert sidecar["size"] == len(PAYLOAD)
    assert sidecar["created_at"]

    # 同内容再传：同 id（幂等），元数据以首次为准
    second = client.post(
        _upload_url(),
        content=PAYLOAD,
        headers={"Content-Type": PAYLOAD_MIME, "X-Filename": quote("other.mp4")},
    )
    assert second.status_code == 201
    assert second.json()["attachment_id"] == expected_id
    assert second.json()["filename"] == "演示.mp4"

    got = client.get(_download_url(expected_id))
    assert got.status_code == 200
    assert got.content == PAYLOAD
    assert got.headers["content-type"] == PAYLOAD_MIME
    assert got.headers["content-length"] == str(len(PAYLOAD))
    assert got.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert "mp4" in got.headers["content-disposition"]
    assert got.headers["accept-ranges"] == "bytes"


def test_get_sidecar_missing_falls_back_to_bytes(tmp_path: Path) -> None:
    """sidecar 丢失不把附件判成不存在：按字节文件兜底元数据。"""
    client, root = _app(tmp_path)
    created = client.post(
        _upload_url(), content=b"bytes", headers={"Content-Type": "text/plain"}
    ).json()
    (root / "attachments" / f"{created['attachment_id']}.json").unlink()
    got = client.get(_download_url(created["attachment_id"]))
    assert got.status_code == 200
    assert got.content == b"bytes"
    assert got.headers["content-type"] == "application/octet-stream"


def test_range_206_and_416(tmp_path: Path) -> None:
    client, _ = _app(tmp_path)
    attachment_id = client.post(
        _upload_url(), content=PAYLOAD, headers={"Content-Type": PAYLOAD_MIME}
    ).json()["attachment_id"]

    partial = client.get(_download_url(attachment_id), headers={"Range": "bytes=2-5"})
    assert partial.status_code == 206, partial.text
    assert partial.content == b"2345"
    assert partial.headers["content-range"] == f"bytes 2-5/{len(PAYLOAD)}"
    assert partial.headers["content-length"] == "4"

    suffix = client.get(_download_url(attachment_id), headers={"Range": "bytes=-3"})
    assert suffix.status_code == 206
    assert suffix.content == b"789"

    tail = client.get(_download_url(attachment_id), headers={"Range": "bytes=5-"})
    assert tail.status_code == 206
    assert tail.content == b"56789"

    beyond = client.get(_download_url(attachment_id), headers={"Range": "bytes=99-"})
    assert beyond.status_code == 416
    assert beyond.json()["error"]["code"] == "range_not_satisfiable"

    # 语法不合法的 Range 按 HTTP 语义可忽略 → 完整 200
    ignored = client.get(_download_url(attachment_id), headers={"Range": "items=0-1"})
    assert ignored.status_code == 200
    assert ignored.content == PAYLOAD


def test_range_disabled_returns_full_bytes(tmp_path: Path) -> None:
    client, _ = _app(tmp_path, media=MediaConfig(range_requests=False))
    attachment_id = client.post(
        _upload_url(), content=PAYLOAD, headers={"Content-Type": PAYLOAD_MIME}
    ).json()["attachment_id"]
    response = client.get(_download_url(attachment_id), headers={"Range": "bytes=0-1"})
    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["accept-ranges"] == "none"


def test_get_errors_404_400_and_unknown_project(tmp_path: Path) -> None:
    client, _ = _app(tmp_path)
    missing = client.get(_download_url("b" * 64))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "attachment_not_found"

    invalid = client.get(_download_url("not-a-sha256"))
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_attachment_id"

    unknown_project = client.post(
        "/api/v1/projects/missing/attachments",
        content=b"x",
        headers={"Content-Type": "text/plain"},
    )
    assert unknown_project.status_code == 404
    assert unknown_project.json()["error"]["code"] == "not_found"


def test_path_traversal_attempts_never_touch_files(tmp_path: Path) -> None:
    client, root = _app(tmp_path)
    (root / "secret.txt").write_text("secret", encoding="utf-8")
    for attempt in ("../secret.txt", "..%2Fsecret.txt", "./" + "a" * 64, "../" + "a" * 64):
        response = client.get(f"{_upload_url()}/{quote(attempt, safe='')}")
        # 路由不匹配（404）或非法 id（400）都行，关键是绝不返回文件内容
        assert response.status_code in (400, 404), attempt
        assert b"secret" not in response.content
    store = project_attachment_store(str(root))
    with pytest.raises(ValueError, match="invalid attachment_id"):
        store.path_for("../secret.txt")


def test_upload_errors_413_415_400(tmp_path: Path) -> None:
    client, _ = _app(tmp_path, media=MediaConfig(max_upload_bytes=8))
    too_large = client.post(_upload_url(), content=b"x" * 9, headers={"Content-Type": "text/plain"})
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "attachment_too_large"

    disallowed = client.post(
        _upload_url(), content=b"x", headers={"Content-Type": "chemical/x-pdb"}
    )
    assert disallowed.status_code == 415
    assert disallowed.json()["error"]["code"] == "unsupported_attachment_type"

    empty = client.post(_upload_url(), content=b"", headers={"Content-Type": "text/plain"})
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "invalid_request"

    # 显式置空白名单 = 不限制
    client_open, _ = _app(tmp_path / "open", media=MediaConfig(allowed_mime_prefixes=()))
    allowed = client_open.post(
        _upload_url(), content=b"x", headers={"Content-Type": "chemical/x-pdb"}
    )
    assert allowed.status_code == 201


def test_media_and_global_switches_return_503(tmp_path: Path) -> None:
    disabled, _ = _app(tmp_path, media=MediaConfig(enabled=False))
    response = disabled.post(_upload_url(), content=b"x", headers={"Content-Type": "text/plain"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "media_disabled"

    api_off, _ = _app(tmp_path / "api-off", observer_http_api_enabled=False)
    response = api_off.get(_download_url("a" * 64))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capability_unavailable"


def test_non_loopback_requires_bearer_token(tmp_path: Path) -> None:
    client, _ = _app(tmp_path, token=TOKEN)
    remote = TestClient(client.app, client=("10.9.8.7", 4321))

    denied = remote.post(_upload_url(), content=b"x", headers={"Content-Type": "text/plain"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "access_denied"

    wrong = remote.post(
        _upload_url(),
        content=b"x",
        headers={"Content-Type": "text/plain", "Authorization": "Bearer wrong"},
    )
    assert wrong.status_code == 403

    allowed = remote.post(
        _upload_url(),
        content=b"x",
        headers={"Content-Type": "text/plain", "Authorization": f"Bearer {TOKEN}"},
    )
    assert allowed.status_code == 201
    attachment_id = allowed.json()["attachment_id"]
    assert (
        remote.get(
            _download_url(attachment_id), headers={"Authorization": f"Bearer {TOKEN}"}
        ).status_code
        == 200
    )


def test_attachment_is_project_scoped(tmp_path: Path) -> None:
    """附件是项目级资源：同一 attachment_id 在别的项目里不可读。"""
    roots = {"p1": tmp_path / "p1", "p2": tmp_path / "p2"}
    for root in roots.values():
        root.mkdir()

    async def lookup(project_id: str) -> str | None:
        root = roots.get(project_id)
        return str(root) if root is not None else None

    app = FastAPI()
    register_ability_control_error_handlers(app)
    app.include_router(build_media_control_router(config=SubjectConfig(), project_root=lookup))
    client = TestClient(app)

    created = client.post(
        "/api/v1/projects/p1/attachments",
        content=b"payload",
        headers={"Content-Type": "text/plain"},
    ).json()
    attachment_id = created["attachment_id"]
    assert client.get(f"/api/v1/projects/p1/attachments/{attachment_id}").status_code == 200
    cross = client.get(f"/api/v1/projects/p2/attachments/{attachment_id}")
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "attachment_not_found"

    # 同内容在 p2 上传：同 id，但落在 p2 自己的目录（内容寻址 + 项目隔离）
    duplicated = client.post(
        "/api/v1/projects/p2/attachments",
        content=b"payload",
        headers={"Content-Type": "text/plain"},
    ).json()
    assert duplicated["attachment_id"] == attachment_id
    assert (roots["p1"] / "attachments" / attachment_id).is_file()
    assert (roots["p2"] / "attachments" / attachment_id).is_file()


async def test_cors_preflight_allows_attachment_headers(tmp_path: Path) -> None:
    """观测端跨源上传：预检必须放行 X-Filename / Range 并暴露 Range 响应头。"""
    from ghrah.ability.access import GrantPolicy
    from ouroboros import Context  # type: ignore[import-untyped]

    from ghrah.subject.units import mount_builtin_units

    config = SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        ability_grant_policy=GrantPolicy(mode="merge"),
    )
    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        endpoint = ctx.get("observer_endpoint")
        client = TestClient(endpoint.app)
        response = client.options(
            "/api/v1/projects/p1/attachments",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-filename",
            },
        )
        assert response.status_code == 200
        allowed = response.headers["access-control-allow-headers"].lower()
        assert "x-filename" in allowed
        assert "authorization" in allowed
        # 实际响应上暴露 Range 相关头（预检响应只带 allow-*）
        actual = client.get(
            "/api/v1/projects/missing/attachments/" + "a" * 64,
            headers={"Origin": "http://localhost:5173"},
        )
        exposed = actual.headers.get("access-control-expose-headers", "").lower()
        assert "content-range" in exposed


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        ("bytes=0-0", 10, (0, 0)),
        ("bytes=0-99", 10, (0, 9)),
        ("bytes=9-", 10, (9, 9)),
        ("bytes=-1", 10, (9, 9)),
        ("bytes=-99", 10, (0, 9)),
        ("items=0-1", 10, None),
        ("bytes=0-1,2-3", 10, None),
        ("bytes=abc", 10, None),
    ],
)
def test_parse_byte_range(header: str, size: int, expected: Any) -> None:
    assert parse_byte_range(header, size) == expected


def test_attachment_store_rejects_invalid_ids() -> None:
    assert is_valid_attachment_id("c" * 64)
    for invalid in ("C" * 64, "c" * 63, "../" + "c" * 61, "", None, 7):
        assert not is_valid_attachment_id(invalid)


def test_media_unit_meta(tmp_path: Path) -> None:
    unit = MediaControlUnit(SubjectConfig())
    assert unit.meta.name == "media_control"
    assert {"observer_endpoint", "project_manager"} <= {key.name for key in unit.meta.requires}
    assert unit.meta.routes.commands == frozenset()


async def test_attachment_store_save_and_load(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments")
    record = await store.save(b"payload", mime_type="text/plain", filename="a.txt")
    assert record.attachment_id == hashlib.sha256(b"payload").hexdigest()
    assert record.size == len(b"payload")
    assert record.mime_type == "text/plain"
    assert record.filename == "a.txt"
    assert await store.load(record.attachment_id) == record
    assert await store.load("c" * 64) is None
    # 同内容幂等：id 相同、sidecar 元数据以首次为准
    again = await store.save(b"payload", mime_type="text/other", filename="b.txt")
    assert again == record
    assert store.directory == tmp_path / "attachments"
