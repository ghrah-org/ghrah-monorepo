# SPDX-FileCopyrightText: 2026 chenya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力包导入：归档格式、安全解包、安装与安装记录。"""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import ProblemCode

from ghrah.ability.package.installed_store import parse_full_name
from ghrah.ability.package.lock import lock_filename, read_lock
from ghrah.ability.package.provider import strip_schema_metadata
from ghrah.core.wiring import build_core_catalog

OP_SOURCE = '''\
"""最小可授予 operation：入口签名与 schema 都必须能被运行时接受。"""

from __future__ import annotations

from typing import Any

from ghrah.types.results import ActionOutcome, ActionResult
from pydantic import BaseModel, ConfigDict, Field

MARKER = "{marker}"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(description="探活目标")


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="是否可达")


async def execute(call: Any, services: Any) -> ActionResult:
    """入口：返回固定结果，供安装期 ABI 校验使用。"""
    del call, services
    return ActionResult(outcome=ActionOutcome.SUCCESS, data=Output(ok=True).model_dump())
'''


def manifest_text(
    *,
    namespace: str = "acme",
    name: str = "demo",
    version: str = "1.0.0",
    schema_override: dict | None = None,
    with_skill: bool = False,
) -> str:
    """按模块真实 pydantic 模型生成 manifest，保证 schema 与入口一致。"""
    module = _load_op_module("alpha")
    members: dict = {
        "operations": {
            "ping": {
                "title": "Ping",
                "description": "探活。",
                "entry": "operations/ping.py",
                "input_schema": schema_override
                if schema_override is not None
                else strip_schema_metadata(module.Input.model_json_schema()),
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
    }
    if with_skill:
        members["skills"] = {
            "guide": {
                "title": "Guide",
                "description": "说明。",
                "tags": ["docs"],
                "body": {"path": "skills/guide/SKILL.md", "media_type": "text/markdown"},
            }
        }
    document = {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": {"namespace": namespace, "name": name, "version": version},
        "title": "Demo Package",
        "description": "导入测试用的最小能力包。",
        "members": members,
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _load_op_module(marker: str):
    """在内存里执行 operation 源码，拿到它与运行期一致的 Input/Output。"""
    module = ModuleType("pkg_fixture_op")
    module.__dict__["__builtins__"] = __builtins__
    exec(compile(OP_SOURCE.format(marker=marker), "<fixture>", "exec"), module.__dict__)
    return module


def write_package(
    root: Path,
    *,
    namespace: str = "acme",
    name: str = "demo",
    version: str = "1.0.0",
    marker: str = "alpha",
    schema_override: dict | None = None,
    with_skill: bool = False,
) -> Path:
    """造一个最小可安装源包目录。"""
    root.mkdir(parents=True, exist_ok=True)
    # 用字节写，避免 Windows 文本模式把 LF 变成 CRLF（包内容要求 LF）。
    (root / "ability.yaml").write_bytes(
        manifest_text(
            namespace=namespace,
            name=name,
            version=version,
            schema_override=schema_override,
            with_skill=with_skill,
        ).encode("utf-8")
    )
    (root / "operations").mkdir(exist_ok=True)
    (root / "operations" / "ping.py").write_bytes(OP_SOURCE.format(marker=marker).encode())
    if with_skill:
        (root / "skills" / "guide").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "guide" / "SKILL.md").write_bytes(
            b"---\nname: guide\ndescription: docs\ntags: [docs]\n---\n\n# Guide\n"
        )
    return root

def zip_package(source: Path, archive: Path, *, top: str | None = "demo") -> Path:
    """把源包目录压成 zip；``top`` 决定是否套一层顶层目录。"""
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            handle.write(path, f"{top}/{relative}" if top else relative)
    return archive


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """(安装根, 归档路径)。"""
    install_root = tmp_path / "ability"
    install_root.mkdir()
    archive = zip_package(write_package(tmp_path / "src"), tmp_path / "demo.zip")
    return install_root, archive


async def test_import_installs_and_rewrites_lock_source(workspace: tuple[Path, Path]) -> None:
    install_root, archive = workspace
    catalog = build_core_catalog(install_root=str(install_root))
    try:
        view = await catalog.package_import(archive_path=str(archive), source_name="demo.zip")
    finally:
        await catalog.aclose()

    assert view.full_name == "acme.demo"
    assert view.version == "1.0.0"
    assert view.archive_format == "zip"
    assert view.source_name == "demo.zip"
    assert view.operation_count == 1
    assert view.skill_count == 0
    assert view.already_installed is False
    assert view.lock_written is True
    assert view.package_digest.startswith("sha256:")

    version_root = install_root / "acme" / "demo" / "1.0.0"
    assert (version_root / "ability.yaml").is_file()
    lock = read_lock(install_root / "acme" / "demo" / lock_filename("1.0.0"))
    assert lock.source.kind == "archive"
    assert lock.source.ref == "demo.zip"
    assert parse_full_name(view.full_name) == ("acme", "demo")


async def test_reimport_same_archive_is_idempotent(workspace: tuple[Path, Path]) -> None:
    install_root, archive = workspace
    catalog = build_core_catalog(install_root=str(install_root))
    try:
        first = await catalog.package_import(archive_path=str(archive), source_name="demo.zip")
        second = await catalog.package_import(archive_path=str(archive), source_name="demo.zip")
    finally:
        await catalog.aclose()

    assert first.already_installed is False
    assert second.already_installed is True
    assert second.lock_written is False
    assert second.package_digest == first.package_digest


async def test_archive_without_top_directory_is_accepted(tmp_path: Path) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    archive = zip_package(write_package(tmp_path / "src"), tmp_path / "flat.zip", top=None)
    catalog = build_core_catalog(install_root=str(install_root))
    try:
        view = await catalog.package_import(archive_path=str(archive), source_name="flat.zip")
    finally:
        await catalog.aclose()
    assert view.full_name == "acme.demo"


async def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    source = tmp_path / "src"
    source.mkdir()
    (source / "readme.txt").write_text("no manifest", encoding="utf-8")
    archive = zip_package(source, tmp_path / "bad.zip")
    catalog = build_core_catalog(install_root=str(install_root))
    try:
        with pytest.raises(ApiError) as failure:
            await catalog.package_import(archive_path=str(archive), source_name="bad.zip")
    finally:
        await catalog.aclose()
    assert failure.value.problem.code is ProblemCode.INVALID_PACKAGE
    assert not (install_root / "acme").exists()


async def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "boom")
    catalog = build_core_catalog(install_root=str(install_root))
    try:
        with pytest.raises(ApiError) as failure:
            await catalog.package_import(archive_path=str(archive), source_name="evil.zip")
    finally:
        await catalog.aclose()
    assert failure.value.problem.code is ProblemCode.INVALID_PACKAGE
    assert not (tmp_path / "escape.txt").exists()


async def test_unknown_format_is_rejected(tmp_path: Path) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    archive = tmp_path / "package.txt"
    archive.write_text("not an archive", encoding="utf-8")
    catalog = build_core_catalog(install_root=str(install_root))
    try:
        with pytest.raises(ApiError) as failure:
            await catalog.package_import(archive_path=str(archive), source_name="package.txt")
    finally:
        await catalog.aclose()
    assert failure.value.problem.code is ProblemCode.UNSUPPORTED_FORMAT


async def test_real_zip_extension_with_wrong_bytes_is_invalid(tmp_path: Path) -> None:
    install_root = tmp_path / "ability"
    install_root.mkdir()
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"hello world")
    catalog = build_core_catalog(install_root=str(install_root))
    try:
        with pytest.raises(ApiError) as failure:
            await catalog.package_import(archive_path=str(archive), source_name="broken.zip")
    finally:
        await catalog.aclose()
    assert failure.value.problem.code is ProblemCode.INVALID_PACKAGE

def test_import_rejects_schema_that_does_not_match_module(tmp_path: Path) -> None:
    """手写 schema 与模块 pydantic 不一致：安装期就拒，不留到建 agent。"""
    source = write_package(
        tmp_path / "src",
        schema_override={
            "type": "object",
            "properties": {"other": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
    )
    archive = zip_package(source, tmp_path / "bad-schema.zip")
    catalog = build_core_catalog(install_root=str(tmp_path / "ability"))

    with pytest.raises(ApiError) as failure:
        asyncio.run(catalog.package_import(archive_path=str(archive), source_name="bad-schema.zip"))

    assert failure.value.problem.code is ProblemCode.INVALID_PACKAGE
    assert "Input" in (failure.value.problem.detail or "")
    assert not (tmp_path / "ability" / "acme").exists()


def test_import_rejects_skill_member(tmp_path: Path) -> None:
    """skill 成员没有 ref 授予入口：装进去也进不了模型，直接拒。"""
    source = write_package(tmp_path / "src", with_skill=True)
    archive = zip_package(source, tmp_path / "with-skill.zip")
    catalog = build_core_catalog(install_root=str(tmp_path / "ability"))

    with pytest.raises(ApiError) as failure:
        asyncio.run(catalog.package_import(archive_path=str(archive), source_name="with-skill.zip"))

    assert failure.value.problem.code is ProblemCode.INVALID_PACKAGE
    assert "skill" in (failure.value.problem.detail or "").lower()
    assert not (tmp_path / "ability" / "acme").exists()
