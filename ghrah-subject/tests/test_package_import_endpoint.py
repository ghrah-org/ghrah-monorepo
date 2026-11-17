# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力包导入端点：上传 → 体积上限 → 扩展名 → Core 安装。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ghrah.ability.package.lock import lock_filename, read_lock
from ghrah.ability.package.provider import strip_schema_metadata
from ghrah.core.wiring import build_core_catalog
from ghrah.protocol.enums import EventType

from ghrah.subject.config import SubjectConfig
from ghrah.subject.units.ability_control import (
    build_ability_control_router,
    register_ability_control_error_handlers,
)

OP_SOURCE = '''\
"""最小可授予 operation：入口签名与 schema 都必须能被运行时接受。"""

from __future__ import annotations

from typing import Any

from ghrah.types.results import ActionOutcome, ActionResult
from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(description="探活目标")


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="是否可达")


async def execute(call: Any, services: Any) -> ActionResult:
    """入口：返回固定结果。"""
    del call, services
    return ActionResult(outcome=ActionOutcome.SUCCESS, data=Output(ok=True).model_dump())
'''


def _load_op_module() -> Any:
    """在内存里执行 operation 源码，拿到与运行期一致的 Input/Output。"""
    module = ModuleType("subject_fixture_op")
    module.__dict__["__builtins__"] = __builtins__
    exec(compile(OP_SOURCE, "<fixture>", "exec"), module.__dict__)
    return module


def manifest_text() -> str:
    """按模块真实 pydantic 模型生成 manifest，保证 schema 与入口一致。"""
    module = _load_op_module()
    document = {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": {"namespace": "acme", "name": "demo", "version": "1.0.0"},
        "title": "Demo Package",
        "description": "上传导入测试包。",
        "members": {
            "operations": {
                "ping": {
                    "title": "Ping",
                    "description": "探活。",
                    "entry": "operations/ping.py",
                    "input_schema": strip_schema_metadata(module.Input.model_json_schema()),
                    "output_schema": strip_schema_metadata(module.Output.model_json_schema()),
                    "tags": ["probe"],
                    "effects": [],
                    "permissions": {},
                    "execution": {
                        "mode": "request",
                        "timeout_ms": 5000,
                        "cancellation": "unsupported",
                        "on_revoke": "drain",
                        "retry": "never",
                    },
                }
            }
        },
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def package_zip(*, top: str | None = "demo", payload: bytes = b"") -> bytes:
    """内存里造一个可安装的能力包归档。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as handle:
        prefix = f"{top}/" if top else ""

        def add(name: str, data: bytes) -> None:
            handle.writestr(f"{prefix}{name}", data)

        add("ability.yaml", manifest_text().encode("utf-8"))
        add("operations/ping.py", OP_SOURCE.encode("utf-8"))
        if payload:
            add("assets/blob.bin", payload)
    return buffer.getvalue()


class _Projects:
    """导入端点不需要项目，只需要 project_store 存在。"""

    async def get(self, project_id: str) -> None:
        del project_id
        return None


class _Bus:
    """只记录事件的假总线；导入成功应发出一条变更事件。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_type: str, payload: dict[str, Any]) -> int:
        self.calls.append((event_type, payload))
        return 1


def make_client(
    install_root: Path, *, max_bytes: int = 256 << 20, bus: _Bus | None = None
) -> tuple[TestClient, Any]:
    catalog = build_core_catalog(install_root=str(install_root))
    config = SubjectConfig(
        ability_source="builtin",
        observer_http_api_enabled=True,
        ability_packages_install=True,
        ability_upload_max_bytes=max_bytes,
    )
    app = FastAPI()
    app.include_router(
        build_ability_control_router(
            config=config,
            project_store=_Projects(),
            catalog=catalog,
            event_bus=bus,
        )
    )
    register_ability_control_error_handlers(app)
    return TestClient(app), catalog


def test_import_zip_installs_package(tmp_path: Path) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    bus = _Bus()
    client, catalog = make_client(install_root, bus=bus)

    response = client.post(
        "/api/v1/ability-packages/import",
        files={"file": ("demo.zip", package_zip(), "application/zip")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "ghrah.ability-control.package-import.v1"
    assert body["namespace"] == "acme"
    assert body["name"] == "demo"
    assert body["version"] == "1.0.0"
    assert body["archive_format"] == "zip"
    assert body["operation_count"] == 1
    assert body["already_installed"] is False
    assert (install_root / "acme" / "demo" / "1.0.0" / "ability.yaml").is_file()
    lock = read_lock(install_root / "acme" / "demo" / lock_filename("1.0.0"))
    assert lock.source.kind == "archive"
    assert lock.source.ref == "demo.zip"
    assert bus.calls and bus.calls[0][0] == EventType.ABILITY_PACKAGE_CHANGED.value
    assert bus.calls[0][1]["name"] == "demo"
    del catalog


def test_import_rejects_unknown_extension(tmp_path: Path) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    client, _ = make_client(install_root)

    response = client.post(
        "/api/v1/ability-packages/import",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_archive"


def test_import_rejects_oversized_upload(tmp_path: Path) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    client, _ = make_client(install_root, max_bytes=2048)

    response = client.post(
        "/api/v1/ability-packages/import",
        files={"file": ("big.zip", b"x" * 8192, "application/zip")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "too_large"


def test_import_reports_invalid_package(tmp_path: Path) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    client, _ = make_client(install_root)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("readme.txt", "no ability.yaml")

    response = client.post(
        "/api/v1/ability-packages/import",
        files={"file": ("broken.zip", buffer.getvalue(), "application/zip")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_package"


@pytest.mark.parametrize("flag", [False])
def test_import_can_be_disabled_by_config(tmp_path: Path, flag: bool) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    catalog = build_core_catalog(install_root=str(install_root))
    config = SubjectConfig(
        ability_source="builtin",
        observer_http_api_enabled=True,
        ability_packages_install=flag,
    )
    app = FastAPI()
    app.include_router(
        build_ability_control_router(config=config, project_store=_Projects(), catalog=catalog)
    )
    register_ability_control_error_handlers(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/ability-packages/import",
        files={"file": ("demo.zip", package_zip(), "application/zip")},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capability_unavailable"
