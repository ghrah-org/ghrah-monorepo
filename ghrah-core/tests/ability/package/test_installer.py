# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""安装器领域层的验收测试（install / uninstall / list / show / verify / refresh）。

fixtures 是持久产物：测试一律复制到 ``tmp_path`` 再动盘，且**不改 fixture 原件**。
重点覆盖 M0 冻结口径里的硬约束：安装不 import 包代码、索引与内容严格比对、
重复安装幂等、同版本不同内容拒绝、lock 缺失降级、verify 的 added/removed/changed、
保留名/旧锁/旧布局/归档/MCP/链接/路径预算拒绝。
"""

from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.package.digest import (
    DIGEST_ALGORITHM,
    collect_files,
    file_digest,
    tree_digest,
)
from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import (
    MAX_ABSOLUTE_UTF16,
    AbilityInstaller,
    InstallerError,
    InstallResult,
    UninstallResult,
    VerifyResult,
    _content_limits,
)
from ghrah.ability.package.lock import (
    InstallLock,
    InstallSource,
    McpLock,
    lock_filename,
    read_lock,
    render_lock,
    write_lock,
)

FIXTURES = Path(__file__).parent / "fixtures"
WINDOWS = os.name == "nt"
REPORTING_FILES = {
    "ability.yaml",
    "expected-definition.json",
    "operations/render.py",
    "skills/reporting-guide/SKILL.md",
    "skills/reporting-guide/assets/template.txt",
    "skills/reporting-guide/references/style-guide.md",
}


def _copy_fixture(tmp_path: Path, name: str, *, build_index: bool = True) -> Path:
    source = tmp_path / "sources" / name
    shutil.copytree(FIXTURES / name, source)
    if build_index:
        build(source)
    return source


def _installer(tmp_path: Path) -> AbilityInstaller:
    return AbilityInstaller(tmp_path / "store" / "ability")


def _reporting(tmp_path: Path) -> tuple[AbilityInstaller, Path]:
    return _installer(tmp_path), _copy_fixture(tmp_path, "reporting")


def _edit_definition(source: Path, mutator: Any) -> None:
    path = source / "ability.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutator(data)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _set_version(source: Path, version: str) -> None:
    path = source / "ability.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("version: 0.1.0", f"version: {version}"), encoding="utf-8", newline="\n"
    )
    build(source)


def _assert_error(code: str, call: Any) -> InstallerError:
    with pytest.raises(InstallerError) as excinfo:
        call()
    assert excinfo.value.code == code, excinfo.value.message
    assert str(excinfo.value) == excinfo.value.message
    return excinfo.value


# ------------------------------------------------------------------- install


@pytest.mark.parametrize("name", ["reporting", "multi-skill", "builder"])
def test_install_fixture_succeeds_and_verify_is_clean(tmp_path: Path, name: str) -> None:
    installer = _installer(tmp_path)
    source = _copy_fixture(tmp_path, name)
    result = installer.install(source)

    assert isinstance(result, InstallResult)
    assert result.full_name == f"fixtures.{name}"
    assert result.version == "0.1.0"
    assert result.root == installer.root / "fixtures" / name / "0.1.0"
    assert result.already_installed is False
    assert result.package_digest == tree_digest(collect_files(result.root))

    lock_path = result.root.parent / lock_filename("0.1.0")
    lock = read_lock(lock_path)
    assert lock.files == collect_files(result.root)
    assert lock.package_digest == result.package_digest == tree_digest(lock.files)
    assert lock.digest_algorithm == DIGEST_ALGORITHM
    assert lock.source == InstallSource(kind="directory", ref=str(source))
    assert lock.installed_at.endswith("Z")
    assert lock.mcp is None
    assert (result.root / "ability.yaml").read_bytes() == (source / "ability.yaml").read_bytes()

    verify = installer.verify(f"fixtures.{name}")
    assert len(verify) == 1
    assert verify[0].ok is True
    assert verify[0].package_digest == lock.package_digest
    assert verify[0].actual_digest == lock.package_digest
    assert verify[0].added == verify[0].removed == verify[0].changed == ()


def test_install_writes_exact_file_set_and_no_temp_leftovers(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    result = installer.install(source)
    assert set(collect_files(result.root)) == REPORTING_FILES
    assert sorted(path.name for path in result.root.parent.iterdir()) == [
        "0.1.0",
        "0.1.0.lock.yaml",
    ]


def test_install_is_idempotent(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    first = installer.install(source)
    lock_path = first.root.parent / lock_filename("0.1.0")
    lock_before = lock_path.read_bytes()
    second = installer.install(source)
    assert second.already_installed is True
    assert second.package_digest == first.package_digest
    assert second.root == first.root
    assert lock_path.read_bytes() == lock_before


def test_install_same_version_different_content_is_rejected(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    _write(source / "README.md", "# 非成员文件也参与 package_digest\n")
    _assert_error("version_conflict", lambda: installer.install(source))


def test_install_allows_non_member_root_files(tmp_path: Path) -> None:
    """§3.1：包根的其他非成员文件可以存在，参与 package_digest，但不进索引。"""
    installer, source = _reporting(tmp_path)
    _write(source / "README.md", "# readme\n")
    result = installer.install(source)
    assert result.package_digest == tree_digest(collect_files(source))
    assert "README.md" in collect_files(result.root)


def test_install_allows_underscore_helper_modules(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    _write(source / "operations" / "_shared.py", "SHARED = 1\n")
    result = installer.install(source)
    assert "operations/_shared.py" in collect_files(result.root)


def test_install_does_not_import_package_code(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    _write(source / "operations" / "boom.py", "raise RuntimeError('安装期不得 import 包代码')\n")

    def add_boom(definition: dict[str, Any]) -> None:
        boom = copy.deepcopy(definition["members"]["operations"]["render"])
        boom["entry"] = "operations/boom.py"
        definition["members"]["operations"]["boom"] = boom

    _edit_definition(source, add_boom)
    result = installer.install(source)
    assert "operations/boom.py" in collect_files(result.root)
    # 同时确认安装期确实没有执行该模块：若 import 会在 install 内抛 RuntimeError。
    assert installer.verify("fixtures.reporting")[0].ok is True


def test_install_rejects_missing_declared_file(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    (source / "operations" / "render.py").unlink()
    _assert_error("index_mismatch", lambda: installer.install(source))


def test_install_rejects_undeclared_operation(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    _write(source / "operations" / "extra.py", "x = 1\n")
    _assert_error("index_mismatch", lambda: installer.install(source))


def test_install_rejects_unregistered_skill_file(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    _write(source / "skills" / "reporting-guide" / "notes.md", "x\n")
    _assert_error("index_mismatch", lambda: installer.install(source))


def test_install_rejects_lock_in_source(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    _write(source / "ability.lock.yaml", "schema: 2\n")
    _assert_error("lock_in_source", lambda: installer.install(source))


def test_install_rejects_nested_lock_in_source(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    _write(source / "skills" / "reporting-guide" / "1.0.0.lock.yaml", "schema: 2\n")
    _assert_error("lock_in_source", lambda: installer.install(source))


def test_install_rejects_legacy_layout(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    _write(source / "SKILL.md", "---\nname: x\n---\n")
    _assert_error("legacy_layout", lambda: installer.install(source))


def test_install_rejects_legacy_definition(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    path = source / "ability.yaml"
    text = path.read_text(encoding="utf-8").replace("schema_version: '1'", "schema_version: '0.2'")
    path.write_text(text, encoding="utf-8", newline="\n")
    _assert_error("invalid_definition", lambda: installer.install(source))


def test_install_rejects_missing_definition(tmp_path: Path) -> None:
    installer = _installer(tmp_path)
    source = tmp_path / "empty"
    source.mkdir()
    _assert_error("invalid_definition", lambda: installer.install(source))


def test_install_rejects_archive_source(tmp_path: Path) -> None:
    installer = _installer(tmp_path)
    archive = tmp_path / "pkg.tar.gz"
    archive.write_bytes(b"PK\x03\x04")
    _assert_error("unsupported_source", lambda: installer.install(archive))


def test_install_rejects_missing_source(tmp_path: Path) -> None:
    installer = _installer(tmp_path)
    _assert_error("unsupported_source", lambda: installer.install(tmp_path / "missing"))


def test_install_rejects_reserved_namespaces(tmp_path: Path) -> None:
    for namespace in ("ghrah", "mcp"):
        installer = _installer(tmp_path / namespace)
        source = _copy_fixture(tmp_path / namespace, "reporting")

        def set_namespace(definition: dict[str, Any], ns: str = namespace) -> None:
            definition["identity"]["namespace"] = ns

        _edit_definition(source, set_namespace)
        _assert_error("reserved_namespace", lambda: installer.install(source))


def test_distro_origin_allows_ghrah_but_rejects_mcp(tmp_path: Path) -> None:
    from ghrah.ability.package.installer import InstallOrigin

    store = tmp_path / "store" / "ability"
    source = _copy_fixture(tmp_path, "reporting")

    def set_ghrah(definition: dict[str, Any]) -> None:
        definition["identity"]["namespace"] = "ghrah"
        definition["identity"]["name"] = "reporting"

    _edit_definition(source, set_ghrah)
    build(source, shipped=True)
    distro = AbilityInstaller(store, origin=InstallOrigin.DISTRO)
    result = distro.install(source)
    assert result.already_installed is False
    assert (store / "ghrah" / "reporting" / "0.1.0").is_dir()

    mcp_source = _copy_fixture(tmp_path / "mcp-case", "reporting")

    def set_mcp(definition: dict[str, Any]) -> None:
        definition["identity"]["namespace"] = "mcp"
        definition["identity"]["name"] = "reporting"

    _edit_definition(mcp_source, set_mcp)
    _assert_error("reserved_namespace", lambda: distro.install(mcp_source))


def test_distro_origin_still_accepts_third_party(tmp_path: Path) -> None:
    from ghrah.ability.package.installer import InstallOrigin

    installer = AbilityInstaller(tmp_path / "store" / "ability", origin=InstallOrigin.DISTRO)
    source = _copy_fixture(tmp_path, "reporting")
    result = installer.install(source)
    assert result.already_installed is False


def test_install_mcp_reports_missing_configuration(tmp_path: Path, monkeypatch) -> None:
    from ghrah.mcp.agentconf_source import AgentconfMcpSource
    from ghrah.mcp.errors import McpConfigError

    def missing(*args, **kwargs):
        raise McpConfigError("missing server")

    monkeypatch.setattr(AgentconfMcpSource, "connection_spec", missing)
    installer = _installer(tmp_path)
    source = tmp_path / "mcp-src"
    _write(
        source / "ability.yaml",
        "format: ghrah.ability-definition\n"
        "schema_version: '1'\n"
        "origin: mcp\n"
        "identity: {namespace: mcp, name: github, version: 0.0.1}\n"
        "title: GitHub MCP\n"
        "description: 快照\n"
        "mcp: {server: github-mcp}\n"
        "members: {}\n",
    )
    _assert_error("mcp_config_invalid", lambda: installer.install(source))


def test_install_rejects_reserved_member_names(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    (source / "operations" / "render.py").rename(source / "operations" / "search_tools.py")

    def rename_member(definition: dict[str, Any]) -> None:
        operations = definition["members"]["operations"]
        operations["search_tools"] = operations.pop("render")
        operations["search_tools"]["entry"] = "operations/search_tools.py"

    _edit_definition(source, rename_member)
    _assert_error("reserved_member_name", lambda: installer.install(source))


def test_install_rejects_resource_digest_mismatch(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    asset = source / "skills" / "reporting-guide" / "assets" / "template.txt"
    asset.write_text(asset.read_text(encoding="utf-8") + "被篡改\n", encoding="utf-8", newline="\n")
    _assert_error("invalid_content", lambda: installer.install(source))


def test_install_rejects_resource_size_mismatch(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)

    def bump_size(definition: dict[str, Any]) -> None:
        assets = definition["members"]["skills"]["reporting-guide"]["assets"]
        assets["template"]["size"] = assets["template"]["size"] + 1

    _edit_definition(source, bump_size)
    _assert_error("invalid_content", lambda: installer.install(source))


def test_content_limits_derive_from_actual_declared_sizes(tmp_path: Path) -> None:
    """捕获额度 = 索引声明文件的实际大小之和/最大值/条目数，不是硬编码预算。"""
    _installer_obj, source = _reporting(tmp_path)
    definition = parse_definition(
        (source / "ability.yaml").read_text(encoding="utf-8"), source=str(source)
    )
    declared = [source / definition.skills["reporting-guide"].body.path]
    for role in ("assets", "references", "scripts"):
        declared.extend(
            source / resource.path
            for resource in getattr(definition.skills["reporting-guide"], role).values()
        )
    sizes = [path.stat().st_size for path in declared]
    limits = _content_limits(definition, source)
    assert limits.file_bytes == max(sizes)
    assert limits.total_bytes == sum(sizes)
    assert limits.entries == len(declared)
    assert limits.file_bytes < 8 * 1024 * 1024


def test_install_accepts_resource_over_default_capture_limit(tmp_path: Path) -> None:
    """内容捕获额度按声明文件实际大小推导，不被 8MiB 默认单文件上限误拒。

    参考口径：M0 没有包大小预算；安装期只要求 files 与索引登记的 digest/size
    一致。这里用真实 >8MiB 的 ``.txt`` 资源（该后缀不触发 BOM/CRLF 文本检查），
    证明 install / verify 不再带硬编码的 8MiB / 32MiB 内容捕获预算。
    """
    installer, source = _reporting(tmp_path)
    asset = source / "skills" / "reporting-guide" / "assets" / "template.txt"
    oversized = 8 * 1024 * 1024 + 1
    asset.write_bytes(b"x" * oversized)
    build(source)

    result = installer.install(source)
    assert result.package_digest == tree_digest(collect_files(source))
    installed_asset = result.root / "skills" / "reporting-guide" / "assets" / "template.txt"
    assert installed_asset.stat().st_size == oversized
    verify = installer.verify("fixtures.reporting")
    assert [item.ok for item in verify] == [True]


def test_install_rejects_unparsable_skill_frontmatter(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    body = source / "skills" / "reporting-guide" / "SKILL.md"
    text = body.read_text(encoding="utf-8").replace("description: ", "description: [", 1)
    body.write_text(text, encoding="utf-8", newline="\n")
    _assert_error("invalid_content", lambda: installer.install(source))


def test_install_rejects_duplicate_keys_in_definition(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    path = source / "ability.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("title: Reporting", "title: Reporting\ntitle: Duplicated"),
        encoding="utf-8",
        newline="\n",
    )
    _assert_error("invalid_definition", lambda: installer.install(source))


def test_install_rejects_corrupt_existing_version_without_lock(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    slot = installer.root / "fixtures" / "reporting"
    (slot / "0.1.0").mkdir(parents=True)
    _assert_error("corrupt_install", lambda: installer.install(source))


def test_install_rejects_corrupt_existing_lock(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    slot = installer.root / "fixtures" / "reporting"
    (slot / "0.1.0").mkdir(parents=True)
    _write(slot / lock_filename("0.1.0"), "schema: 2\n")
    _assert_error("corrupt_install", lambda: installer.install(source))


def test_install_degenerate_identity_uses_namespace_slot(tmp_path: Path) -> None:
    installer = _installer(tmp_path)
    source = _copy_fixture(tmp_path, "builder")
    path = source / "ability.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "  namespace: fixtures\n  name: builder\n", "  namespace: solo\n"
    )
    path.write_text(text, encoding="utf-8", newline="\n")
    build(source)
    result = installer.install(source)
    assert result.full_name == "solo"
    assert result.root == installer.root / "solo" / "solo" / "0.1.0"
    listed = installer.list()
    assert [record.full_name for record in listed] == ["solo"]


def test_install_root_symlink_rejected(tmp_path: Path) -> None:
    if WINDOWS:
        pytest.skip("Windows 上创建符号链接需要额外权限")
    real = tmp_path / "real-store"
    real.mkdir()
    link = tmp_path / "linked-store"
    link.symlink_to(real, target_is_directory=True)
    source = _copy_fixture(tmp_path, "reporting")
    _assert_error("symlink_rejected", lambda: AbilityInstaller(link).install(source))


def test_install_source_symlink_rejected(tmp_path: Path) -> None:
    if WINDOWS:
        pytest.skip("Windows 上创建符号链接需要额外权限")
    installer = _installer(tmp_path)
    source = _copy_fixture(tmp_path, "reporting")
    link = tmp_path / "source-link"
    link.symlink_to(source, target_is_directory=True)
    _assert_error("symlink_rejected", lambda: installer.install(link))


def test_install_source_nested_symlink_rejected(tmp_path: Path) -> None:
    if WINDOWS:
        pytest.skip("Windows 上创建符号链接需要额外权限")
    installer, source = _reporting(tmp_path)
    (source / "skills" / "reporting-guide" / "assets" / "linked.txt").symlink_to(
        source / "skills" / "reporting-guide" / "assets" / "template.txt"
    )
    _assert_error("portable_tree_violation", lambda: installer.install(source))


def test_install_path_budget_too_long(tmp_path: Path) -> None:
    if WINDOWS:
        pytest.skip("Windows 默认 MAX_PATH 限制下无法构造长根")
    source = _copy_fixture(tmp_path, "reporting")
    target_length = MAX_ABSOLUTE_UTF16 + 10
    segment_length = target_length - len(str(tmp_path)) - 1
    assert 0 < segment_length <= 200
    long_root = tmp_path / ("r" * segment_length)
    _assert_error("path_too_long", lambda: AbilityInstaller(long_root).install(source))


# --------------------------------------------------------------- list / show


def test_list_empty_root_returns_empty(tmp_path: Path) -> None:
    assert _installer(tmp_path).list() == []


def test_list_records_include_lock_facts(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    result = installer.install(source)
    records = installer.list()
    assert len(records) == 1
    record = records[0]
    assert record.full_name == "fixtures.reporting"
    assert record.namespace == "fixtures"
    assert record.name == "reporting"
    assert record.version == "0.1.0"
    assert record.root == result.root
    assert record.lock_state == "ok"
    assert record.installed_at is not None and record.installed_at.endswith("Z")
    assert record.source == InstallSource(kind="directory", ref=str(source))
    assert record.problem is None
    payload = record.to_dict()
    assert payload["root"] == str(result.root)
    assert payload["source"] == {"kind": "directory", "ref": str(source)}


def test_show_omitted_version_returns_all_versions(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    _set_version(source, "0.1.1")
    installer.install(source)
    payloads = installer.show("fixtures.reporting")
    assert isinstance(payloads, list)
    assert [payload["version"] for payload in payloads] == ["0.1.0", "0.1.1"]
    assert all(payload["lock_state"] == "ok" for payload in payloads)
    assert all(
        payload["definition"]["format"] == "ghrah.ability-definition" for payload in payloads
    )


def test_show_single_version_shape(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    payload = installer.show("fixtures.reporting", "0.1.0")
    assert isinstance(payload, dict)
    assert set(payload) == {
        "version",
        "root",
        "installed_at",
        "source",
        "mcp",
        "lock_state",
        "problem",
        "definition",
    }
    assert payload["definition"]["identity"]["namespace"] == "fixtures"


def test_show_missing_lock_degrades(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    result = installer.install(source)
    result.root.parent.joinpath(lock_filename("0.1.0")).unlink()
    payload = installer.show("fixtures.reporting", "0.1.0")
    assert payload["lock_state"] == "missing"
    assert payload["installed_at"] is None
    assert payload["source"] is None
    assert payload["definition"] is not None
    record = installer.list()[0]
    assert record.lock_state == "missing"


def test_show_errors(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    _assert_error("not_installed", lambda: installer.show("fixtures.missing"))
    _assert_error("not_installed", lambda: installer.show("fixtures.reporting", "9.9.9"))
    _assert_error("invalid_full_name", lambda: installer.show("a.b.c"))
    _assert_error("invalid_version", lambda: installer.show("fixtures.reporting", "v1"))


# ------------------------------------------------------------------- verify


def test_verify_missing_lock_is_rejected(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    result = installer.install(source)
    result.root.parent.joinpath(lock_filename("0.1.0")).unlink()
    results = installer.verify("fixtures.reporting")
    assert [item.ok for item in results] == [False]
    assert results[0].code == "lock_missing"
    assert results[0].package_digest is None
    assert isinstance(results[0], VerifyResult)


def test_verify_surfaces_unsupported_lock_schema(tmp_path: Path) -> None:
    """旧版 lock 不读：verify 报 ``lock_schema_unsupported``，要求重装。"""
    installer, source = _reporting(tmp_path)
    result = installer.install(source)
    lock_path = result.root.parent / lock_filename("0.1.0")
    lock_path.write_text(
        lock_path.read_text(encoding="utf-8").replace("schema: 1", "schema: 2"),
        encoding="utf-8",
        newline="\n",
    )
    item = installer.verify("fixtures.reporting")[0]
    assert item.ok is False
    assert item.code == "lock_schema_unsupported"
    assert installer.show("fixtures.reporting", "0.1.0")["lock_state"] == "invalid"


def test_verify_reports_added_removed_changed(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    result = installer.install(source)
    installed = result.root
    (installed / "operations" / "render.py").write_text(
        "# changed\n", encoding="utf-8", newline="\n"
    )
    (installed / "skills" / "reporting-guide" / "assets" / "template.txt").unlink()
    _write(installed / "extra.txt", "added\n")

    results = installer.verify("fixtures.reporting")
    assert len(results) == 1
    item = results[0]
    assert item.ok is False
    assert item.code == "digest_mismatch"
    assert item.changed == ("operations/render.py",)
    assert item.removed == ("skills/reporting-guide/assets/template.txt",)
    assert item.added == ("extra.txt",)
    payload = item.to_dict()
    assert payload["added"] == ["extra.txt"]
    assert payload["changed"] == ["operations/render.py"]


def test_verify_omitted_version_checks_all_versions(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    _set_version(source, "0.1.1")
    second = installer.install(source)
    (second.root / "operations" / "render.py").write_text(
        "# broken\n", encoding="utf-8", newline="\n"
    )
    results = installer.verify("fixtures.reporting")
    assert [(item.version, item.ok) for item in results] == [("0.1.0", True), ("0.1.1", False)]


# ---------------------------------------------------------------- uninstall


def test_uninstall_requires_version_or_all(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    _assert_error("version_required", lambda: installer.uninstall("fixtures.reporting"))


def test_uninstall_specific_version(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    first = installer.install(source)
    _set_version(source, "0.1.1")
    second = installer.install(source)

    result = installer.uninstall("fixtures.reporting", "0.1.0")
    assert isinstance(result, UninstallResult)
    assert result.full_name == "fixtures.reporting"
    assert [removed.version for removed in result.removed] == ["0.1.0"]
    assert result.removed[0].root == first.root
    assert not first.root.exists()
    assert not first.root.parent.joinpath(lock_filename("0.1.0")).exists()
    assert second.root.exists()
    assert [record.version for record in installer.list()] == ["0.1.1"]


def test_uninstall_all_removes_everything_but_keeps_root(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    _set_version(source, "0.1.1")
    installer.install(source)
    result = installer.uninstall("fixtures.reporting", all=True)
    assert [removed.version for removed in result.removed] == ["0.1.0", "0.1.1"]
    assert installer.list() == []
    assert installer.root.exists() and installer.root.is_dir()
    assert not (installer.root / "fixtures").exists()


def test_uninstall_calls_invalidate_before_delete(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    result = installer.install(source)
    seen: list[tuple[str, bool]] = []

    def invalidate(item: Any) -> None:
        seen.append((item.version, item.root.exists()))

    installer.uninstall("fixtures.reporting", "0.1.0", invalidate=invalidate)
    assert seen == [("0.1.0", True)]
    assert not result.root.exists()


def test_uninstall_invalidate_failure_aborts_delete(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    result = installer.install(source)

    def invalidate(item: Any) -> None:
        raise RuntimeError("授予尚未标记")

    with pytest.raises(RuntimeError):
        installer.uninstall("fixtures.reporting", "0.1.0", invalidate=invalidate)
    assert result.root.exists()


def test_uninstall_all_cleans_orphan_locks(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    installer.uninstall("fixtures.reporting", all=True)
    slot = installer.root / "fixtures" / "reporting"
    orphan = _write(slot / lock_filename("9.9.9"), "schema: 1\n")
    result = installer.uninstall("fixtures.reporting", all=True)
    assert result.removed == ()
    assert not orphan.exists()
    assert not slot.exists()
    assert installer.root.exists()


def test_uninstall_errors(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    _assert_error(
        "invalid_arguments", lambda: installer.uninstall("fixtures.reporting", "0.1.0", all=True)
    )
    _assert_error("invalid_full_name", lambda: installer.uninstall("a.b.c", all=True))
    _assert_error("invalid_version", lambda: installer.uninstall("fixtures.reporting", "latest"))
    _assert_error("not_installed", lambda: installer.uninstall("fixtures.reporting", "9.9.9"))
    _assert_error("not_installed", lambda: installer.uninstall("fixtures.other", all=True))


# ------------------------------------------------------------------ refresh


def test_refresh_source_is_unsupported(tmp_path: Path) -> None:
    installer, source = _reporting(tmp_path)
    installer.install(source)
    _assert_error("unsupported_refresh", lambda: installer.refresh())
    _assert_error("unsupported_refresh", lambda: installer.refresh(full_name="fixtures.reporting"))


def test_refresh_mcp_rejects_corrupt_snapshot(tmp_path: Path) -> None:
    installer = _installer(tmp_path)
    version_root = installer.root / "mcp" / "github" / "0.0.1"
    _write(version_root / "ability.yaml", "format: ghrah.ability-definition\n")
    files = collect_files(version_root)
    lock = InstallLock(
        package_digest=tree_digest(files),
        digest_algorithm=DIGEST_ALGORITHM,
        installed_at="2026-11-03T12:34:56Z",
        source=InstallSource(kind="mcp"),
        files=files,
        mcp=McpLock(fetched_at="2026-11-03T12:34:56Z", stale=False, last_error=None),
    )
    write_lock(installer.root / "mcp" / "github" / lock_filename("0.0.1"), lock)
    _assert_error("corrupt_install", lambda: installer.refresh(full_name="mcp.github"))


def test_install_result_to_list_to_verify_roundtrip(tmp_path: Path) -> None:
    """内部一致性：同一份源包安装后，摘要/文件/lock 三方互相对得上。"""
    installer, source = _reporting(tmp_path)
    result = installer.install(source)
    lock = read_lock(result.root.parent / lock_filename("0.1.0"))
    assert lock.files == collect_files(result.root)
    assert render_lock(lock)  # 可再次渲染
    assert file_digest(result.root / "ability.yaml") == lock.files["ability.yaml"]
