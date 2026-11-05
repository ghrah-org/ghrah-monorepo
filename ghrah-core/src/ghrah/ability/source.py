# SPDX-License-Identifier: Apache-2.0
"""将新增驱动的只读能力投影接入现行 Agent 声明解析器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ghrah.ability.execution.base import Ability
from ghrah.ability.loading import ability_catalog
from ghrah.ability.loading.ability_roots import builtin_root
from ghrah.ability.loading.ability_schema import AbilityDefinition
from ghrah.ability.package.provider import InstalledAbilityProvider
from ghrah.ability.versions import AbilityVersion, capture_ability_version
from ghrah.manifest.ability import AbilityManifest
from ghrah.manifest.agent import AgentManifest
from ghrah.manifest.errors import ManifestNotFoundError, ManifestValidationError
from ghrah.manifest.protocols import ManifestStoreProtocol
from ghrah.manifest.resolver import ManifestResolver


class BuiltinAbilitySource:
    """显式选择新内置来源，Agent 声明可委托给现有存储。

    不持有可独立写入的目录，不启动 Agent，不注册运行端点。实例创建仅供受信
    装配者选择后端；生效授权、成员绑定及内容版本固定由后续运行接入负责。
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
        """按确定来源捕获包版本；安装包必须先通过 lock 校验。"""
        if not package.startswith("ghrah.") and self.packages is not None:
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
        """内置版本由当前宿主管理；安装版本由包加载器提供动态可用性。"""
        if version.definition().identity.namespace == "ghrah":
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
        """按完整成员名读取内置能力，返回隔离的兼容投影。"""
        manifest = self._manifests.get(full_name)
        if manifest is None:
            if self.packages is not None:
                return self.packages.manifest(full_name)
            raise ManifestNotFoundError(f"Ability manifest not found: {full_name}")
        return AbilityManifest.model_validate(manifest.model_dump())

    def list_abilities(self, namespace: str | None = None) -> list[str]:
        """列出新增驱动的内置成员，忽略未选用的旧注册表。"""
        names = sorted(self._manifests)
        if namespace is not None:
            names = [name for name in names if name.startswith(f"{namespace}.")]
        return names

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
        return ManifestResolver(self, builtin_catalog=self)

    def create_operation(self, full_name: str, **params: Any) -> Ability:
        """为受信装配者实例化确定内置成员，不接受模块路径或用户脚本。"""
        manifest = self.load_ability(full_name)
        handler = manifest.implementation.handler
        if manifest.implementation.type != "builtin" or handler is None:
            raise ManifestValidationError(f"Unsupported operation backend: {full_name}")
        if self.load_builtin(handler).full_name != full_name:
            raise ManifestValidationError(f"Operation identity changed: {full_name}")
        return self._classes[handler](**params)
