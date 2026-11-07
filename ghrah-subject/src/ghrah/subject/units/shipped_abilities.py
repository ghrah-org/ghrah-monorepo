# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ShippedAbilityUnit：启动时校验并补齐发行版预装能力包。

读 ``distro.toml`` 的 ``[distro.abilities].required``，对缺失项从
``ghrah.subject.shipped_abilities`` 源树以 ``InstallOrigin.DISTRO`` 安装进
``ability.install_root``。必须排在 ``desired_state`` 之后、``recovery`` 之前，
以便 reconcile 拉起 Agent 时预装包已就位。

摘要不符（同版本不同内容）记诊断错误并继续启动，不阻断 Subject。
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from ghrah.ability.package.installed_store import InstalledStore
from ghrah.ability.package.installer import AbilityInstaller, InstallerError, InstallOrigin

from ghrah.subject.config import SubjectConfig
from ghrah.subject.unit.base import SubjectUnit, UnitMeta

__all__ = ["RequiredAbility", "ShippedAbilityUnit", "load_required_abilities"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequiredAbility:
    """预装清单的一条：包全名 + 期望版本。"""

    name: str
    version: str


def load_required_abilities(distro_toml: Path) -> list[RequiredAbility]:
    """从 ``distro.toml`` 读 ``[distro.abilities].required``；缺失段返回空。"""
    if not distro_toml.is_file():
        return []
    try:
        data = tomllib.loads(distro_toml.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        logger.error("shipped abilities: distro.toml 解析失败 (%s): %s", distro_toml, exc)
        return []
    distro = data.get("distro", {})
    if not isinstance(distro, dict):
        return []
    abilities = distro.get("abilities", {})
    if not isinstance(abilities, dict):
        return []
    required = abilities.get("required", [])
    if not isinstance(required, list):
        logger.error("shipped abilities: distro.abilities.required 必须是数组 (%s)", distro_toml)
        return []
    items: list[RequiredAbility] = []
    for index, raw in enumerate(required):
        if not isinstance(raw, dict):
            logger.error(
                "shipped abilities: required[%s] 必须是 table (%s)", index, distro_toml
            )
            continue
        name = raw.get("name", "")
        version = raw.get("version", "")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            logger.error(
                "shipped abilities: required[%s] 缺 name/version (%s)", index, distro_toml
            )
            continue
        items.append(RequiredAbility(name=name, version=version))
    return items


def _shipped_source_root() -> Path | None:
    """定位随 Subject 交付的 ``shipped_abilities`` 源根。"""
    try:
        traversable = resources.files("ghrah.subject.shipped_abilities")
    except ModuleNotFoundError:
        return None
    with resources.as_file(traversable) as path:
        return Path(path)


def _package_dir_name(full_name: str) -> str | None:
    """``ghrah.project_agent_manage`` → ``project_agent_manage``。"""
    parts = full_name.split(".")
    if len(parts) != 2 or parts[0] != "ghrah":
        return None
    return parts[1]


class ShippedAbilityUnit(SubjectUnit):
    """发行版预装能力包的启动校验与补齐。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._meta = UnitMeta(name="shipped_abilities")

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def init(self, ctx: Any) -> None:
        """按清单安装缺失预装包；摘要冲突记诊断并继续。"""
        del ctx
        install_root = self._config.ability.install_root
        if not install_root:
            logger.info(
                "shipped abilities: ability.install_root 未配置，跳过预装检查"
            )
            return
        root = Path(install_root)
        distro_toml = root.parent / "distro.toml"
        required = load_required_abilities(distro_toml)
        if not required:
            logger.info(
                "shipped abilities: 无预装清单（distro=%s），跳过",
                distro_toml,
            )
            return
        source_root = _shipped_source_root()
        if source_root is None or not source_root.is_dir():
            logger.error(
                "shipped abilities: 找不到 shipped_abilities 源目录；"
                "清单有 %d 项但无法安装",
                len(required),
            )
            return
        store = InstalledStore(root)
        installer = AbilityInstaller(root, origin=InstallOrigin.DISTRO)
        for item in required:
            await self._ensure_one(store, installer, source_root, item)

    async def _ensure_one(
        self,
        store: InstalledStore,
        installer: AbilityInstaller,
        source_root: Path,
        item: RequiredAbility,
    ) -> None:
        existing = store.resolve(item.name, item.version)
        if existing is not None and existing.lock is not None:
            # 已装：交给 install 做摘要比对（同摘要幂等；不同摘要领域错误）
            pass
        dirname = _package_dir_name(item.name)
        if dirname is None:
            logger.error(
                "shipped abilities: 非法预装全名 %r（须为 ghrah.<name>）", item.name
            )
            return
        source = source_root / dirname
        if not source.is_dir() or not (source / "ability.yaml").is_file():
            logger.error(
                "shipped abilities: 源包缺失 %s（期望目录 %s）",
                item.name,
                source,
            )
            return
        try:
            result = installer.install(source)
        except InstallerError as exc:
            if exc.code == "version_conflict":
                logger.error(
                    "shipped abilities: %s@%s 已装但摘要不符，跳过覆盖（%s）",
                    item.name,
                    item.version,
                    exc,
                )
                return
            logger.error(
                "shipped abilities: 安装 %s@%s 失败：%s",
                item.name,
                item.version,
                exc,
            )
            return
        if result.already_installed:
            logger.info(
                "shipped abilities: %s@%s 已就位（跳过）", item.name, item.version
            )
        else:
            logger.info(
                "shipped abilities: 已安装 %s@%s → %s",
                item.name,
                item.version,
                result.root,
            )
