# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""安装存储三层枚举（``InstalledStore``）的验收测试。

钉住 §3.8 的读取口径：只认 ``<namespace>/<name>/<version>``、忽略临时项、
链接不跟随、旁 lock 按路径配对、lock 缺失可降级、孤儿 lock 不是安装事实。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ghrah.ability.package.digest import DIGEST_ALGORITHM, collect_files, tree_digest
from ghrah.ability.package.installed_store import (
    InstalledStore,
    is_link,
    parse_full_name,
    version_sort_key,
)
from ghrah.ability.package.lock import (
    LOCK_HEADER,
    InstallLock,
    InstallSource,
    lock_filename,
    render_lock,
)

WINDOWS = os.name == "nt"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _make_version(
    root: Path,
    namespace: str,
    name: str,
    version: str,
    *,
    lock: str = "ok",
    ability_text: str = "format: ghrah.ability-definition\n",
) -> Path:
    """落一个版本目录；``lock`` 取 ``ok`` / ``missing`` / ``invalid``。"""
    version_root = root / namespace / name / version
    version_root.mkdir(parents=True)
    _write(version_root / "ability.yaml", ability_text)
    if lock == "missing":
        return version_root
    files = collect_files(version_root)
    install_lock = InstallLock(
        package_digest=tree_digest(files),
        digest_algorithm=DIGEST_ALGORITHM,
        installed_at="2026-11-03T12:34:56Z",
        source=InstallSource(kind="directory", ref="/tmp/pkg"),
        files=files,
    )
    text = render_lock(install_lock)
    if lock == "invalid":
        text = text.replace("schema: 1", "schema: 2")
    _write(root / namespace / name / lock_filename(version), text)
    return version_root


def test_parse_full_name_cases() -> None:
    assert parse_full_name("fixtures.reporting") == ("fixtures", "reporting")
    assert parse_full_name("solo") == ("solo", "solo")
    assert parse_full_name("a.b.c") is None
    assert parse_full_name("Upper.name") is None
    assert parse_full_name("") is None
    assert parse_full_name(None) is None  # type: ignore[arg-type]


def test_version_sort_key() -> None:
    assert version_sort_key("0.10.2") == (0, 10, 2)
    versions = ["1.0.10", "1.0.2", "0.9.0"]
    assert sorted(versions, key=version_sort_key) == ["0.9.0", "1.0.2", "1.0.10"]


def test_missing_root_enumerates_empty(tmp_path: Path) -> None:
    store = InstalledStore(tmp_path / "no-such-root")
    assert store.packages() == ()
    assert store.versions_of("fixtures.reporting") == ()
    assert store.resolve("fixtures.reporting", "0.1.0") is None


def test_root_is_file_enumerates_empty(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _write(root, "not a directory\n")
    assert InstalledStore(root).packages() == ()


def test_three_layer_enumeration_and_order(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "fixtures", "reporting", "1.0.10")
    _make_version(root, "fixtures", "reporting", "1.0.2")
    _make_version(root, "fixtures", "multi-skill", "0.1.0")
    _make_version(root, "vendor", "tool", "2.0.0")

    store = InstalledStore(root)
    packages = store.packages()
    assert [package.full_name for package in packages] == [
        "fixtures.multi-skill",
        "fixtures.reporting",
        "vendor.tool",
    ]
    reporting = store.versions_of("fixtures.reporting")
    assert [item.version for item in reporting] == ["1.0.2", "1.0.10"]
    assert all(item.lock is not None for item in reporting)
    assert reporting[0].root == root / "fixtures" / "reporting" / "1.0.2"
    assert reporting[0].lock_path == root / "fixtures" / "reporting" / "1.0.2.lock.yaml"
    assert reporting[0].lock_state == "ok"


def test_degenerate_named_slot_uses_namespace(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "solo", "solo", "0.1.0")
    store = InstalledStore(root)
    packages = store.packages()
    assert [package.full_name for package in packages] == ["solo"]
    assert store.versions_of("solo")[0].full_name == "solo"
    # 点分写法也落到同一个槽（不引入第二种路径形状）。
    assert store.versions_of("solo.solo")[0].full_name == "solo"


def test_ignores_temp_items_and_invalid_names(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "fixtures", "reporting", "0.1.0")
    _write(root / ".tmp" / "fixtures" / "reporting" / "9.9.9" / "ability.yaml", "x: 1\n")
    _write(root / ".trash" / "junk", "x\n")
    _write(root / "fixtures" / "reporting" / ".trash" / "0.9.9" / "ability.yaml", "x: 1\n")
    _write(root / "fixtures" / "reporting" / "1.0" / "ability.yaml", "x: 1\n")  # 非 SemVer
    _write(root / "fixtures" / "reporting" / "v1.0.0" / "ability.yaml", "x: 1\n")
    _write(root / "Upper" / "reporting" / "0.1.0" / "ability.yaml", "x: 1\n")
    _write(root / "fixtures" / "Bad" / "0.1.0" / "ability.yaml", "x: 1\n")
    _write(root / "not-a-dir.txt", "x\n")

    store = InstalledStore(root)
    assert [package.full_name for package in store.packages()] == ["fixtures.reporting"]
    assert [item.version for item in store.versions_of("fixtures.reporting")] == ["0.1.0"]


def test_orphan_lock_is_not_installation_fact(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "fixtures", "reporting", "0.1.0")
    orphan = _write(root / "fixtures" / "reporting" / lock_filename("0.2.0"), "schema: 1\n")
    assert [item.version for item in InstalledStore(root).versions_of("fixtures.reporting")] == [
        "0.1.0"
    ]
    assert InstalledStore(root).orphan_locks("fixtures.reporting") == (orphan,)


def test_missing_lock_degrades(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "fixtures", "reporting", "0.1.0", lock="missing")
    item = InstalledStore(root).versions_of("fixtures.reporting")[0]
    assert item.lock is None
    assert item.problem == "lock_missing"
    assert item.lock_state == "missing"
    assert item.installed_at is None
    assert item.source is None
    assert item.mcp is None


def test_invalid_lock_degrades(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "fixtures", "reporting", "0.1.0", lock="invalid")
    item = InstalledStore(root).versions_of("fixtures.reporting")[0]
    assert item.lock is None
    assert item.problem is not None
    assert item.problem.startswith("lock_schema_unsupported")
    assert item.lock_state == "invalid"


def test_lock_header_only_file_is_invalid(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "fixtures", "reporting", "0.1.0", lock="missing")
    _write(root / "fixtures" / "reporting" / lock_filename("0.1.0"), LOCK_HEADER)
    item = InstalledStore(root).versions_of("fixtures.reporting")[0]
    assert item.lock is None
    assert item.lock_state == "invalid"


def test_resolve_hit_and_miss(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "fixtures", "reporting", "0.1.0")
    store = InstalledStore(root)
    item = store.resolve("fixtures.reporting", "0.1.0")
    assert item is not None
    assert item.root == root / "fixtures" / "reporting" / "0.1.0"
    assert store.resolve("fixtures.reporting", "0.2.0") is None
    assert store.resolve("fixtures.missing", "0.1.0") is None


def test_versions_of_rejects_invalid_full_name(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "fixtures", "reporting", "0.1.0")
    store = InstalledStore(root)
    assert store.versions_of("a.b.c") == ()
    assert store.versions_of("Upper.reporting") == ()
    assert store.versions_of("") == ()
    assert store.package_dir("a.b.c") is None


def test_is_link_semantics(tmp_path: Path) -> None:
    regular = _write(tmp_path / "file.txt", "x\n")
    assert is_link(regular) is False
    assert is_link(tmp_path / "missing") is False


@pytest.mark.skipif(WINDOWS, reason="Windows 上创建符号链接需要额外权限")
def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "ability"
    _make_version(root, "fixtures", "reporting", "0.1.0")
    outside = _make_version(tmp_path / "outside", "evil", "pkg", "9.9.9")
    (root / "evil").mkdir()
    (root / "evil" / "pkg").symlink_to(outside.parent)
    (root / "fixtures" / "reporting" / "1.0.0").symlink_to(outside)

    assert is_link(root / "evil" / "pkg") is True
    assert is_link(root / "fixtures" / "reporting" / "1.0.0") is True
    packages = InstalledStore(root).packages()
    assert [package.full_name for package in packages] == ["fixtures.reporting"]
    assert [item.version for item in InstalledStore(root).versions_of("fixtures.reporting")] == [
        "0.1.0"
    ]
