# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""将新增驱动的只读能力投影接入现行 Agent 声明解析器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ghrah.ability.execution.base import Ability
from ghrah.ability.loading import ability_catalog
from ghrah.ability.loading.ability_roots import builtin_root
from ghrah.ability.loading.ability_schema import AbilityDefinition
from ghrah.ability.package.provider import InstalledAbilityProvider
from ghrah.ability.versions import AbilityVersion, capture_ability_version
from ghrah.manifest.ability import AbilityManifest, AbilityMetadata
from ghrah.manifest.agent import AgentManifest
from ghrah.manifest.errors import ManifestNotFoundError, ManifestValidationError
from ghrah.manifest.protocols import ManifestStoreProtocol
from ghrah.manifest.resolver import ManifestResolver
from ghrah.manifest.types import ImplementationDef, PermissionFlags

__all__ = ["AbilityOrigin", "BuiltinAbilitySource"]


@dataclass(frozen=True)
class AbilityOrigin:
    """一个能力包全名的确定来源。

    Attributes:
        kind: ``builtin`` 走内置树；``installed`` 走安装存储。
        root: 安装存储根；仅 ``installed`` 时非 ``None``。
    """

    kind: Literal["builtin", "installed"]
    root: Path | None = None


class BuiltinAbilitySource:
    """显式选择新内置来源，Agent 声明可委托给现有存储。

    不持有可独立写入的目录，不启动 Agent，不注册运行端点。实例创建仅供受信
    装配者选择后端；生效授权、成员绑定及内容版本固定由后续运行接入负责。

    已登记的包只认 :attr:`_origins`，不按 ``ghrah.*`` 前缀改判。装配之后才装进
    同一安装根的第三方包不改接已有来源，捕获与读取仍交给安装提供器。
    """

    def __init__(
        self,
        *,
        agent_store: ManifestStoreProtocol | None = None,
        install_root: str | Path | None = None,
    ) -> None:
        self._agent_store = agent_store
        # 显式准备时固定来源；后续查询不因来源目录变化而偷偷改接。
        self._manifests = dict(ability_catalog.load_all_builtin_manifests())
        self._classes = {
            key: ability_catalog.ability_class(key) for key in ability_catalog.builtin_handlers()
        }
        self.packages = InstalledAbilityProvider(install_root) if install_root is not None else None
        self._origins = self._build_origins()
        # 包级解析缓存：``包 → 成员清单``（operation / skill / mcp）。
        # ``_manifests`` 是历史"成员索引"视图，逐步退化为这张缓存的派生。
        self._package_members: dict[str, list[AbilityManifest]] = {}

    def _builtin_package_members(self, package: str) -> list[AbilityManifest]:
        """内置包 → 全部成员（工具面 + 技能面）。

        以**包**为单位取，而不是按全名前缀拼：一个包里可能同时有 operation 与
        skill，后续还会有 mcp 成员，各自投影规则不同，不能在调用点各拼一次。
        """
        cached = self._package_members.get(package)
        if cached is not None:
            return cached
        prefix = f"{package}."
        members: list[AbilityManifest] = [
            AbilityManifest.model_validate(manifest.model_dump())
            for name, manifest in self._manifests.items()
            if name.startswith(prefix)
        ]
        seen = {item.full_name for item in members}
        for name, manifest in self._builtin_skill_manifests().items():
            if name != package and not name.startswith(prefix):
                continue
            if manifest.full_name in seen:
                continue
            seen.add(manifest.full_name)
            members.append(AbilityManifest.model_validate(manifest.model_dump()))
        self._package_members[package] = members
        return members

    def _build_origins(self) -> dict[str, AbilityOrigin]:
        """联合登记内置树与安装存储；同全名双源时明确失败。"""
        origins: dict[str, AbilityOrigin] = {}
        for item in ability_catalog.load_ability_definitions().values():
            origins[item.identity.full_name] = AbilityOrigin(kind="builtin")
        if self.packages is None:
            return origins
        install_root = self.packages.store.root
        builtin_path = builtin_root().path
        for package in self.packages.store.packages():
            name = package.full_name
            if name in origins:
                raise ManifestValidationError(
                    f"能力包全名冲突：{name} 同时存在于内置树"
                    f"（{builtin_path}）与安装存储（{install_root}）；"
                    "拒绝静默覆盖"
                )
            origins[name] = AbilityOrigin(kind="installed", root=install_root)
        return origins

    @property
    def origins(self) -> dict[str, AbilityOrigin]:
        """只读来源登记表（包全名 → 来源）。"""
        return self._origins

    def definitions(self) -> dict[str, AbilityDefinition]:
        """组合内置与显式安装来源的定义；不导入外部实现。"""
        definitions = {
            item.identity.full_name: item
            for item in ability_catalog.load_ability_definitions().values()
        }
        if self.packages is not None:
            definitions.update(self.packages.definitions())
        return definitions

    def capture_package(self, package: str) -> AbilityVersion:
        """按登记表捕获包版本；安装包必须先通过 lock 校验。

        登记表没有的第三方包，与 :meth:`load_ability` 一样走已配置的安装提供器。
        ``ghrah.*`` 不在登记表里时拒绝，避免把后来放进安装根的同名包当成内置来源。
        """
        origin = self._origins.get(package)
        if origin is None:
            if self.packages is not None and not package.startswith("ghrah."):
                return self.packages.capture(package)
            raise ManifestValidationError(f"能力包来源不可用：{package}")
        if origin.kind == "installed":
            if self.packages is None:
                raise ManifestValidationError(f"能力包来源不可用：{package}")
            return self.packages.capture(package)
        definition = next(
            (
                item
                for item in ability_catalog.load_ability_definitions().values()
                if item.identity.full_name == package
            ),
            None,
        )
        root = builtin_root().path
        if definition is None or root is None:
            raise ManifestValidationError(f"能力包来源不可用：{package}")
        root = root / definition.container_name
        return capture_ability_version(
            (root / "ability.yaml").read_text(encoding="utf-8"), root=root
        )

    def version_available(self, version: AbilityVersion) -> bool:
        """按登记表判定版本是否仍可用。

        登记表没有的第三方包，按安装提供器里已经捕获的记录判断。
        """
        name = version.definition().identity.full_name
        origin = self._origins.get(name)
        if origin is None:
            if name.startswith("ghrah.") or self.packages is None:
                return False
            return self.packages.available(version)
        if origin.kind == "builtin":
            return True
        return self.packages is not None and self.packages.available(version)

    def load_builtin(self, ability_type: str) -> AbilityManifest:
        """从新增驱动读取内置类型，缺失时不回退旧注册表。"""
        manifests = self._manifests
        matches = [
            manifest
            for manifest in manifests.values()
            if manifest.implementation.handler == ability_type
        ]
        if len(matches) > 1:
            raise ManifestValidationError(f"Ambiguous builtin ability type: {ability_type!r}")
        if not matches:
            if ability_type in self._classes:
                raise ManifestNotFoundError(
                    f"Builtin ability requires explicit assembly: {ability_type!r}"
                )
            raise ManifestValidationError(f"Unknown builtin ability type: {ability_type!r}")
        return AbilityManifest.model_validate(matches[0].model_dump())

    def load_ability(self, full_name: str) -> AbilityManifest:
        """按完整成员名读取能力，返回隔离的兼容投影。"""
        manifest = self._manifests.get(full_name)
        if manifest is not None:
            return AbilityManifest.model_validate(manifest.model_dump())
        # 按**包**取成员：一个包里 operation / skill / mcp 的投影规则不同，
        # 统一从包级缓存里查，不在调用点各拼一次。
        for package in (full_name, full_name.rpartition(".")[0]):
            if not package:
                continue
            for item in self._builtin_package_members(package):
                if item.full_name == full_name:
                    return AbilityManifest.model_validate(item.model_dump())
        # 内置技能成员不在 ``_manifests``（那张表是工具面）；技能包（例如
        # ``ghrah.ability-builder``）只有技能，必须单独投影，否则整包解析不到。
        skill_manifest = self._builtin_skill_manifests().get(full_name)
        if skill_manifest is not None:
            return AbilityManifest.model_validate(skill_manifest.model_dump())
        if self.packages is not None:
            package, _, _member = full_name.rpartition(".")
            origin = self._origins.get(package)
            if origin is not None and origin.kind == "installed":
                return self.packages.manifest(full_name)
            # 非 ghrah 安装包（第三方）仍走 Provider；登记表无条目时也尝试。
            if origin is None and not package.startswith("ghrah."):
                return self.packages.manifest(full_name)
        raise ManifestNotFoundError(f"Ability manifest not found: {full_name}")

    def _builtin_skill_manifests(self) -> dict[str, AbilityManifest]:
        """内置技能成员 → 能力清单投影（惰性一次，成员键做名字归一）。"""
        cached = getattr(self, "_skill_manifests", None)
        if cached is not None:
            return cached
        manifests: dict[str, AbilityManifest] = {}
        for skill in ability_catalog.scan_ability_definitions().skills:
            raw = skill.full_name
            package, _, key = raw.rpartition(".")
            package = package or raw
            safe_name = key.replace("-", "_") or package
            manifest = AbilityManifest(
                manifest="ability",
                version="1",
                metadata=AbilityMetadata(
                    namespace=package,
                    name=safe_name,
                    title=skill.title,
                    description=skill.description,
                    tags=list(skill.tags),
                    permissions=PermissionFlags(),
                ),
                tool=None,
                implementation=ImplementationDef(type="skill", handler=skill.key),
            )
            # 装配侧既可能用声明里的原始键（kebab-case），也可能用归一后的 member 名；
            # 两个键都登记，取值都指向同一个投影（`manifest.full_name` 是归一后的）。
            manifests[raw] = manifest
            manifests[manifest.full_name] = manifest
            if raw == package:
                # 退化身份（成员键与包同名，如 `research`）也要能按包名取到。
                manifests[package] = manifest
        self._skill_manifests = manifests
        return manifests

    def load_package_members(self, full_name: str) -> list[AbilityManifest]:
        """把一个包的全部成员投影成可授予清单。

        包是解析单位：包内 operation / skill / mcp 各自按自己的规则投影，
        调用方只拿到"成员清单"，不需要知道某个成员属于哪一类。

        Args:
            full_name: 包全名（``namespace.name``）

        Returns:
            成员清单；先内置成员，再安装包成员

        Raises:
            ManifestNotFoundError: 该名字不是包，或包内没有任何成员
        """
        members = self._builtin_package_members(full_name)
        if members:
            return members
        # 内置没有该包时回退到安装包仓库。不能按 ``ghrah.`` 前缀排除：安装包里
        # 也有 ``ghrah.*`` 名字（例如 ``ghrah.ability-builder``），按前缀排除会让
        # 这类包永远解析不到。
        if self.packages is not None:
            try:
                # 「整包」没有指定版本：多版本时取已安装的最高版本。模板若显式钉了
                # 版本，走 select_version 之后的解析（见 ``package_members`` 调用方）。
                versions = tuple(self.packages.store.versions_of(full_name))
                if len(versions) > 1:
                    self.packages.select_version(full_name, versions[-1].version)
                return self.packages.package_members(full_name)
            except ManifestNotFoundError:
                pass
        raise ManifestNotFoundError(f"Ability package not found: {full_name}")

    def list_abilities(self, namespace: str | None = None) -> list[str]:
        """列出内置成员全名（工具面 + 技能面），忽略未选用的旧注册表。

        技能成员曾经不在名单里：列表能列出包、装配却解析不到成员。现在两者
        同源（都来自包级投影），目录与解析结果保持一致。
        """
        names = set(self._manifests)
        names.update(self._builtin_skill_manifests())
        ordered = sorted(names)
        if namespace is not None:
            ordered = [name for name in ordered if name.startswith(f"{namespace}.")]
        return ordered

    def load_agent(self, full_name: str) -> AgentManifest:
        """只委托给显式注入的 Agent 声明存储，不猜测文件位置。"""
        if self._agent_store is None:
            raise ManifestNotFoundError("Agent manifest store is not wired")
        return self._agent_store.load_agent(full_name)

    def list_agents(self, namespace: str | None = None) -> list[str]:
        """从同一显式 Agent 声明存储读取名字。"""
        if self._agent_store is None:
            return []
        return self._agent_store.list_agents(namespace)

    def resolver(self) -> ManifestResolver:
        """创建同时对 ref/type 使用本来源的现行版本 2 解析器。"""
        return ManifestResolver(self)

    def create_operation(self, full_name: str, **params: Any) -> Ability:
        """为受信装配者实例化确定内置成员，不接受模块路径或用户脚本。"""
        manifest = self.load_ability(full_name)
        handler = manifest.implementation.handler
        if manifest.implementation.type != "builtin" or handler is None:
            raise ManifestValidationError(f"Unsupported operation backend: {full_name}")
        if self.load_builtin(handler).full_name != full_name:
            raise ManifestValidationError(f"Operation identity changed: {full_name}")
        return self._classes[handler](**params)
