# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力包版本画像只读函数 unit：内置、已安装、遮蔽、降级与路径安全。

全部直接单测 ``build_version_profile``，不启 HTTP。内置侧用真实内置包；
已安装侧用临时安装根装真实源包，MCP 快照与校验异常用 monkeypatch 覆盖。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from ghrah.ability.package.installer import (
    AbilityInstaller,
    InstallerError,
    InstallOrigin,
    VerifyResult,
)

from ghrah.subject.units.ability_catalog import CatalogError, build_version_profile

_OPERATION_TEMPLATE: dict[str, Any] = {
    "title": "Echo",
    "description": "Echo the input.",
    "entry": "operations/echo.py",
    "input_schema": {"type": "object", "additionalProperties": False},
    "output_schema": {"type": "object", "additionalProperties": False},
    "execution": {
        "mode": "request",
        "timeout_ms": 1000,
        "cancellation": "cooperative",
        "on_revoke": "drain",
        "retry": "never",
    },
}


def _write_source_package(
    parent: Path,
    *,
    namespace: str = "fixtures",
    name: str = "sample",
    version: str = "0.1.0",
    title: str = "Sample",
) -> Path:
    """写一个最小合法 v1 源包（一个 Operation），返回包目录。"""
    source = parent / f"{namespace}-{name}-{version}-source"
    (source / "operations").mkdir(parents=True)
    definition = {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": {"namespace": namespace, "name": name, "version": version},
        "title": title,
        "description": "测试用源包。",
        "members": {"operations": {"echo": dict(_OPERATION_TEMPLATE)}, "skills": {}},
    }
    (source / "ability.yaml").write_text(
        yaml.safe_dump(definition, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (source / "operations" / "echo.py").write_text('"""Echo."""\n', encoding="utf-8")
    return source


def _install(store_root: Path, source: Path, *, distro: bool = False) -> None:
    """把源包装进临时安装根；``ghrah`` 命名空间须走发行版通道。"""
    origin = InstallOrigin.DISTRO if distro else InstallOrigin.USER
    AbilityInstaller(store_root, origin=origin).install(source)


def _has_key(value: Any, key: str) -> bool:
    """递归检查 JSON 形状里是否出现某个键名。"""
    if isinstance(value, dict):
        return key in value or any(_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_has_key(item, key) for item in value)
    return False


def _assert_no_install_paths(payload: dict[str, Any], install_root: str | None = None) -> None:
    """响应任何位置都不得出现 root 键、``/home/`` 或安装根字符串。"""
    text = json.dumps(payload, ensure_ascii=False)
    assert not _has_key(payload, "root")
    assert '"root"' not in text
    assert "/home/" not in text
    if install_root is not None:
        assert install_root not in text


def _assert_relative_path(path: str) -> None:
    """内容路径必须是包内相对路径。"""
    assert path
    assert not path.startswith("/")
    assert ".." not in path.split("/")
    assert not (len(path) > 1 and path[1] == ":")


def test_builtin_source_version_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """内置 fs 版本：7 个 Operation、entry 是相对路径、无校验与 MCP 快照。"""
    profile = build_version_profile(None, "ghrah", "fs", "0.1.0")

    assert profile.origin == "builtin"
    assert profile.definition_origin == "source"
    assert profile.lock_state == "builtin"
    assert profile.verify is None
    assert profile.mcp is None
    assert profile.shadowed_builtin is False
    assert profile.full_name == "ghrah.fs"
    assert profile.version == "0.1.0"
    assert [operation.key for operation in profile.operations] == sorted(
        operation.key for operation in profile.operations
    )
    assert len(profile.operations) == 7
    for operation in profile.operations:
        assert operation.entry is not None
        _assert_relative_path(operation.entry.path)
        assert operation.entry.path == f"operations/{operation.key}.py"
        assert operation.execution.mode == "request"
        assert operation.execution.timeout_ms >= 1
        assert operation.mcp is None

    delete = next(operation for operation in profile.operations if operation.key == "delete_file")
    assert delete.execution.timeout_ms == 60000
    assert delete.effects == ["filesystem:write"]
    assert delete.permissions["fs_write"] is True
    assert delete.expose is True

    _assert_no_install_paths(profile.model_dump(mode="json"))


def test_builtin_skill_member_profile() -> None:
    """内置含 Skill 的包：正文 path 是 ``skills/<name>/SKILL.md``。"""
    profile = build_version_profile(None, "ghrah", "ability-builder", "0.1.1")

    assert profile.origin == "builtin"
    assert profile.definition_origin == "source"
    assert profile.operations == []
    assert len(profile.skills) == 1
    skill = profile.skills[0]
    assert skill.key == "ability-package-development-guide"
    assert skill.title
    assert skill.description
    assert skill.body.path == "skills/ability-package-development-guide/SKILL.md"
    assert skill.body.media_type == "text/markdown"
    _assert_relative_path(skill.body.path)
    assert skill.assets == []
    assert skill.references == []
    assert skill.scripts == []

    _assert_no_install_paths(profile.model_dump(mode="json"))


def test_builtin_source_package_does_not_fake_mcp() -> None:
    """内置 ``ghrah.mcp`` 是 source 包：不得出现 MCP 快照或 MCP 成员引用。"""
    profile = build_version_profile(None, "ghrah", "mcp", "0.1.0")

    assert profile.origin == "builtin"
    assert profile.definition_origin == "source"
    assert profile.mcp is None
    assert [operation.key for operation in profile.operations] == ["mcp_call"]
    operation = profile.operations[0]
    assert operation.mcp is None
    assert operation.entry is not None
    assert operation.entry.path == "operations/mcp_call.py"

    _assert_no_install_paths(profile.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("namespace", "name", "version"),
    [
        ("Bad", "fs", "0.1.0"),
        ("ghrah", "fs.x", "0.1.0"),
        ("ghrah", "-fs", "0.1.0"),
        ("ghrah", "fs", "0.1"),
        ("ghrah", "fs", "0.1.0-alpha"),
        ("ghrah", "fs", "01.0.0"),
        ("ghrah", "fs", "1.0.0.0"),
    ],
)
def test_invalid_identity_or_version_is_invalid_request(
    namespace: str, name: str, version: str
) -> None:
    """身份段或版本不合法 → invalid_request，不落到 404。"""
    with pytest.raises(CatalogError) as caught:
        build_version_profile(None, namespace, name, version)
    assert caught.value.code == "invalid_request"


def test_unknown_version_is_not_found_without_or_missing_install_root() -> None:
    """版本既不在内置也不在安装存储 → not_found；安装根缺失不算不可用。"""
    with pytest.raises(CatalogError) as caught:
        build_version_profile(None, "fixtures", "ghost", "0.1.0")
    assert caught.value.code == "not_found"


def test_missing_install_root_keeps_builtin_readable(tmp_path: Path) -> None:
    """安装根不存在时内置版本仍可读，不因安装侧缺失变 503。"""
    missing_root = tmp_path / "missing-ability"
    profile = build_version_profile(str(missing_root), "ghrah", "fs", "0.1.0")
    assert profile.origin == "builtin"
    assert len(profile.operations) == 7

    with pytest.raises(CatalogError) as caught:
        build_version_profile(str(missing_root), "fixtures", "ghost", "0.1.0")
    assert caught.value.code == "not_found"


def test_installed_source_version_profile(tmp_path: Path) -> None:
    """临时安装根里的真实源包：origin installed、校验通过、路径相对。"""
    store_root = tmp_path / "ability"
    source = _write_source_package(tmp_path)
    _install(store_root, source)

    profile = build_version_profile(str(store_root), "fixtures", "sample", "0.1.0")

    assert profile.origin == "installed"
    assert profile.definition_origin == "source"
    assert profile.shadowed_builtin is False
    assert profile.title == "Sample"
    assert profile.lock_state == "ok"
    assert profile.problem is None
    assert [operation.key for operation in profile.operations] == ["echo"]
    assert profile.operations[0].entry is not None
    assert profile.operations[0].entry.path == "operations/echo.py"
    assert profile.mcp is None
    assert profile.verify is not None
    assert profile.verify.ok is True
    assert profile.verify.code is None

    _assert_no_install_paths(profile.model_dump(mode="json"), str(store_root))


def test_installed_version_shadows_same_builtin_version(tmp_path: Path) -> None:
    """同版本的已安装包遮蔽内置包；成员与标题以已安装定义为准。"""
    store_root = tmp_path / "ability"
    source = _write_source_package(tmp_path, namespace="ghrah", name="fs", title="Installed FS")
    _install(store_root, source, distro=True)

    profile = build_version_profile(str(store_root), "ghrah", "fs", "0.1.0")

    assert profile.origin == "installed"
    assert profile.shadowed_builtin is True
    assert profile.title == "Installed FS"
    assert [operation.key for operation in profile.operations] == ["echo"]

    _assert_no_install_paths(profile.model_dump(mode="json"), str(store_root))


def test_installed_unreadable_definition_degrades_to_200_profile(tmp_path: Path) -> None:
    """已安装定义不可读 → 降级画像：problem 非空、成员为空、不泄露安装根。"""
    store_root = tmp_path / "ability"
    source = _write_source_package(tmp_path)
    _install(store_root, source)
    version_root = store_root / "fixtures" / "sample" / "0.1.0"
    (version_root / "ability.yaml").write_text("not: [a valid definition\n", encoding="utf-8")

    profile = build_version_profile(str(store_root), "fixtures", "sample", "0.1.0")

    assert profile.origin == "installed"
    assert profile.definition_origin is None
    assert profile.title is None
    assert profile.description is None
    assert profile.verify is None
    assert profile.mcp is None
    assert profile.operations == []
    assert profile.skills == []
    assert profile.problem
    assert str(store_root) not in profile.problem

    _assert_no_install_paths(profile.model_dump(mode="json"), str(store_root))


def test_installed_verify_error_maps_to_failure_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """校验失败按 InstallerError 转失败视图，不让整页变 500。"""
    store_root = tmp_path / "ability"
    source = _write_source_package(tmp_path)
    _install(store_root, source)

    def reject_verify(
        self: AbilityInstaller, full_name: str, version: str | None = None
    ) -> list[VerifyResult]:
        raise InstallerError("lock_missing", "安装记录缺失")

    monkeypatch.setattr(AbilityInstaller, "verify", reject_verify)

    profile = build_version_profile(str(store_root), "fixtures", "sample", "0.1.0")

    assert profile.origin == "installed"
    assert profile.definition_origin == "source"
    assert profile.verify is not None
    assert profile.verify.ok is False
    assert profile.verify.code == "lock_missing"
    assert profile.verify.message == "安装记录缺失"


def test_installed_mcp_version_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """已安装 MCP 包：definition_origin mcp、快照来自 show 载荷、成员仅 MCP。"""
    store_root = tmp_path / "ability"
    store_root.mkdir()
    definition = {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": {"namespace": "mcp", "name": "demo", "version": "0.1.0"},
        "title": "Demo MCP",
        "description": "MCP 快照包。",
        "origin": "mcp",
        "mcp": {"server": "demo-server"},
        "operations": {
            "lookup": {
                "title": "Lookup",
                "description": "Look up a record.",
                "input_schema": {"type": "object", "additionalProperties": False},
                "output_schema": {"type": "object", "additionalProperties": False},
                "execution": _OPERATION_TEMPLATE["execution"],
                "mcp": {"server": "demo-server", "tool": "lookup"},
            }
        },
        "skills": {},
    }
    payload = {
        "version": "0.1.0",
        "root": str(store_root / "mcp" / "demo" / "0.1.0"),
        "installed_at": "2026-11-12T00:00:00Z",
        "source": {"kind": "mcp", "ref": None},
        "mcp": {
            "fetched_at": "2026-11-12T00:00:00Z",
            "stale": True,
            "last_error": "timeout",
        },
        "lock_state": "ok",
        "problem": None,
        "definition": definition,
    }

    def fake_show(
        self: AbilityInstaller, full_name: str, version: str | None = None
    ) -> dict[str, Any]:
        assert full_name == "mcp.demo"
        assert version == "0.1.0"
        return payload

    def fake_verify(
        self: AbilityInstaller, full_name: str, version: str | None = None
    ) -> list[VerifyResult]:
        return [
            VerifyResult(
                full_name=full_name,
                version=version or "0.1.0",
                root=Path("/nonexistent"),
                ok=True,
                package_digest="sha256:" + "0" * 64,
                actual_digest="sha256:" + "0" * 64,
            )
        ]

    monkeypatch.setattr(AbilityInstaller, "show", fake_show)
    monkeypatch.setattr(AbilityInstaller, "verify", fake_verify)

    profile = build_version_profile(str(store_root), "mcp", "demo", "0.1.0")

    assert profile.origin == "installed"
    assert profile.definition_origin == "mcp"
    assert profile.title == "Demo MCP"
    assert profile.shadowed_builtin is False
    assert profile.mcp is not None
    assert profile.mcp.server == "demo-server"
    assert profile.mcp.fetched_at == "2026-11-12T00:00:00Z"
    assert profile.mcp.stale is True
    assert profile.mcp.last_error == "timeout"
    assert [operation.key for operation in profile.operations] == ["lookup"]
    operation = profile.operations[0]
    assert operation.mcp is not None
    assert operation.mcp.server == "demo-server"
    assert operation.mcp.tool == "lookup"
    assert operation.entry is None
    assert profile.verify is not None
    assert profile.verify.ok is True

    _assert_no_install_paths(profile.model_dump(mode="json"), str(store_root))


def test_installed_definition_without_mcp_payload_has_no_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """source 定义即使载荷带 mcp 块也不伪造快照。"""
    store_root = tmp_path / "ability"
    store_root.mkdir()
    flat_operation = dict(_OPERATION_TEMPLATE)
    flat_operation["entry"] = {"path": "operations/echo.py", "media_type": "text/x-python"}
    definition = {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": {"namespace": "fixtures", "name": "sample", "version": "0.1.0"},
        "title": "Sample",
        "description": "测试用源包。",
        "origin": "source",
        "operations": {"echo": flat_operation},
        "skills": {},
    }
    payload = {
        "version": "0.1.0",
        "root": str(store_root / "fixtures" / "sample" / "0.1.0"),
        "lock_state": "ok",
        "problem": None,
        "mcp": {"fetched_at": "2026-11-12T00:00:00Z", "stale": False, "last_error": None},
        "definition": definition,
    }

    def fake_show(
        self: AbilityInstaller, full_name: str, version: str | None = None
    ) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(AbilityInstaller, "show", fake_show)

    profile = build_version_profile(str(store_root), "fixtures", "sample", "0.1.0")

    assert profile.definition_origin == "source"
    assert profile.mcp is None
    _assert_no_install_paths(profile.model_dump(mode="json"), str(store_root))


def test_unreadable_install_root_reports_unavailable_for_unknown_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """安装根存在但不可枚举：未知版本 → unavailable，内置版本仍可读。"""
    store_root = tmp_path / "ability"
    store_root.mkdir()
    real_scandir = os.scandir

    def deny_scandir(path: Any) -> Any:
        if Path(path) == store_root:
            raise PermissionError(13, "permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", deny_scandir)

    with pytest.raises(CatalogError) as caught:
        build_version_profile(str(store_root), "fixtures", "ghost", "0.1.0")
    assert caught.value.code == "unavailable"

    builtin = build_version_profile(str(store_root), "ghrah", "fs", "0.1.0")
    assert builtin.origin == "builtin"
