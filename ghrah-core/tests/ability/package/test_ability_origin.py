# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""AbilityOrigin 来源登记表：安装的 ghrah.* 可被捕获，冲突显式失败。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import AbilityInstaller, InstallOrigin
from ghrah.ability.source import BuiltinAbilitySource
from ghrah.manifest.errors import ManifestValidationError

FIXTURES = Path(__file__).parent / "fixtures"


def _install_ghrah_reporting(tmp_path: Path) -> Path:
    source = tmp_path / "src" / "reporting"
    shutil.copytree(FIXTURES / "reporting", source)
    path = source / "ability.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["identity"]["namespace"] = "ghrah"
    data["identity"]["name"] = "reporting"
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    build(source, shipped=True)
    store = tmp_path / "store" / "ability"
    AbilityInstaller(store, origin=InstallOrigin.DISTRO).install(source)
    return store


def test_capture_installed_ghrah_package(tmp_path: Path) -> None:
    store = _install_ghrah_reporting(tmp_path)
    source = BuiltinAbilitySource(install_root=store)
    assert source.origins["ghrah.reporting"].kind == "installed"
    captured = source.capture_package("ghrah.reporting")
    assert captured.definition().identity.full_name == "ghrah.reporting"
    assert source.version_available(captured) is True


def test_builtin_packages_still_origin_builtin(tmp_path: Path) -> None:
    store = tmp_path / "empty-store"
    store.mkdir()
    source = BuiltinAbilitySource(install_root=store)
    assert source.origins["ghrah.cluster"].kind == "builtin"
    captured = source.capture_package("ghrah.cluster")
    assert captured.definition().identity.full_name == "ghrah.cluster"


def test_same_fullname_builtin_and_installed_hard_fails(tmp_path: Path) -> None:
    """若有人把内置全名装进安装存储，构造来源表时必须硬失败。"""
    source_pkg = tmp_path / "src" / "cluster"
    # 用 reporting fixture 改名为与内置冲突的 ghrah.cluster
    shutil.copytree(FIXTURES / "reporting", source_pkg)
    path = source_pkg / "ability.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["identity"]["namespace"] = "ghrah"
    data["identity"]["name"] = "cluster"
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    build(source_pkg, shipped=True)
    store = tmp_path / "store" / "ability"
    AbilityInstaller(store, origin=InstallOrigin.DISTRO).install(source_pkg)
    with pytest.raises(ManifestValidationError, match="全名冲突"):
        BuiltinAbilitySource(install_root=store)
