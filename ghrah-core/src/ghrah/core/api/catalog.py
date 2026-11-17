# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``CoreCatalogPort`` 的实现：无作用域的全局读面。

与 :class:`ghrah.core.api.facade.CoreFacade` 的分工：

- 作用域端口：一个实例一个作用域，读链库/消息库/关系库；
- 目录端口：没有作用域，不打开任何数据库、不建单元，只读能力包安装根与
  受信配置（MCP 探测）。因此零项目时也可用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ghrah.protocol.ability_control import (
    AbilityMcpProbeRequest,
    AbilityMcpProbeView,
    AbilityPackageCatalogView,
    AbilityPackageDetailView,
    AbilityPackageGroupsView,
    AbilityPackageImportView,
    AbilityPackageVersionProfileView,
)
from ghrah.protocol.shell_allow import ShellAllowValidationRequest, ShellBuiltinRulesView

from ghrah.core.api.runtime import CoreCatalogRuntime

__all__ = ["CoreCatalog"]


class CoreCatalog:
    """``CoreCatalogPort`` 的实现。

    Args:
        runtime: 目录收敛点（清单 = ``CORE_CATALOG_INTERFACES``）
        closer: 关闭内部资源的回调（由装配层持有）
    """

    def __init__(
        self,
        *,
        runtime: CoreCatalogRuntime,
        closer: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._runtime = runtime
        self._closer = closer

    async def validate_shell_allow(self, *, request: ShellAllowValidationRequest) -> None:
        """调用 Core 校验命令记忆，不保存。"""
        await self._call("shell.validate_allow", **request.model_dump(mode="json"))

    async def shell_builtin_rules(self) -> ShellBuiltinRulesView:
        """读取内置规则说明。"""
        return ShellBuiltinRulesView.model_validate(await self._call("shell.builtin_rules"))

    async def packages(self) -> AbilityPackageCatalogView:
        """已安装能力包目录。"""
        raw = await self._call("abilities.packages")
        return AbilityPackageCatalogView.model_validate(raw)

    async def package_detail(self, *, namespace: str, name: str) -> AbilityPackageDetailView:
        """单个能力包详情。"""
        raw = await self._call("abilities.package_detail", namespace=namespace, name=name)
        return AbilityPackageDetailView.model_validate(raw)

    async def package_version_profile(
        self, *, namespace: str, name: str, version: str
    ) -> AbilityPackageVersionProfileView:
        """读取单个能力包版本的画像。"""
        raw = await self._call(
            "abilities.package_version_profile", namespace=namespace, name=name, version=version
        )
        return AbilityPackageVersionProfileView.model_validate(raw)

    async def package_mcp_probe(
        self, *, namespace: str, name: str, version: str, request: AbilityMcpProbeRequest
    ) -> AbilityMcpProbeView:
        """只读探测指定能力包版本声明的 MCP 服务。"""
        raw = await self._call(
            "abilities.package_mcp_probe",
            namespace=namespace,
            name=name,
            version=version,
            request=request.model_dump(mode="json"),
        )
        return AbilityMcpProbeView.model_validate(raw)

    async def package_groups(self) -> AbilityPackageGroupsView:
        """全局能力包分组视图（不挂实例事实）。"""
        raw = await self._call("abilities.package_groups")
        return AbilityPackageGroupsView.model_validate(raw)

    async def package_import(
        self, *, archive_path: str, source_name: str
    ) -> AbilityPackageImportView:
        """导入能力包归档：解包、校验、安装、改写安装记录。"""
        raw = await self._call(
            "abilities.package_import",
            archive_path=archive_path,
            source_name=source_name,
        )
        return AbilityPackageImportView.model_validate(raw)

    async def aclose(self) -> None:
        """释放内部资源（幂等）。"""
        if self._closer is not None:
            await self._closer()

    async def _call(self, capability: str, **kwargs: Any) -> dict[str, Any]:
        """装箱并转交给收敛点。"""
        return await self._runtime.invoke(capability, dict(kwargs))
