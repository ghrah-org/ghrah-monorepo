# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ShippedAbilityUnit：预装清单安装 / 幂等 / 摘要冲突诊断。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ghrah.ability.package.installer import AbilityInstaller, InstallOrigin
from ghrah.ability.package.lock import lock_filename

from ghrah.subject.config import AbilityConfig, SubjectConfig
from ghrah.subject.units.shipped_abilities import (
    ShippedAbilityUnit,
    load_required_abilities,
)


def _write_distro(distro_dir: Path, *, version: str = "0.1.0") -> None:
    distro_dir.mkdir(parents=True, exist_ok=True)
    (distro_dir / "distro.toml").write_text(
        "[distro]\n"
        'name = "test"\n'
        "[distro.abilities]\n"
        "required = [\n"
        f'  {{ name = "ghrah.project_agent_manage", version = "{version}" }},\n'
        "]\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_missing_package_is_installed_on_init(tmp_path: Path) -> None:
    distro = tmp_path / "distro"
    install_root = distro / "ability"
    _write_distro(distro)
    config = SubjectConfig(
        ability_source="builtin",
        ability=AbilityConfig(install_root=str(install_root)),
    )
    unit = ShippedAbilityUnit(config)
    await unit.init(None)
    slot = install_root / "ghrah" / "project_agent_manage" / "0.1.0"
    assert slot.is_dir()
    assert (install_root / "ghrah" / "project_agent_manage" / "0.1.0.lock.yaml").is_file()


@pytest.mark.asyncio
async def test_already_installed_is_idempotent(tmp_path: Path) -> None:
    distro = tmp_path / "distro"
    install_root = distro / "ability"
    _write_distro(distro)
    config = SubjectConfig(
        ability_source="builtin",
        ability=AbilityConfig(install_root=str(install_root)),
    )
    unit = ShippedAbilityUnit(config)
    await unit.init(None)
    lock_path = install_root / "ghrah" / "project_agent_manage" / lock_filename("0.1.0")
    before = lock_path.read_text(encoding="utf-8")
    await unit.init(None)
    assert lock_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_digest_mismatch_logs_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import shutil
    from importlib import resources

    from ghrah.ability.package.generate import build

    distro = tmp_path / "distro"
    install_root = distro / "ability"
    _write_distro(distro)
    # 先装一份「同版本不同内容」的包（改 description 后 build）
    alt = tmp_path / "alt-source"
    with resources.as_file(
        resources.files("ghrah.subject.shipped_abilities").joinpath("project_agent_manage")
    ) as shipped:
        shutil.copytree(shipped, alt)
    yaml_path = alt / "ability.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["description"] = "alternate content for conflict test"
    # members 由 build 重写；先清掉以免与新 description 无关的噪声
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "format": data["format"],
                "schema_version": data["schema_version"],
                "identity": data["identity"],
                "title": data["title"],
                "description": data["description"],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
        newline="\n",
    )
    build(alt, shipped=True)
    AbilityInstaller(install_root, origin=InstallOrigin.DISTRO).install(alt)

    config = SubjectConfig(
        ability_source="builtin",
        ability=AbilityConfig(install_root=str(install_root)),
    )
    unit = ShippedAbilityUnit(config)
    with caplog.at_level("ERROR"):
        await unit.init(None)
    assert any("摘要不符" in record.message for record in caplog.records)


def test_load_required_abilities_empty_when_missing(tmp_path: Path) -> None:
    assert load_required_abilities(tmp_path / "missing.toml") == []
