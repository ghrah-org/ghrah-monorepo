# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``abilities`` 分组的处理器（读面）。"""

from __future__ import annotations

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiStatus,
    AbilityChangesPage,
    AbilityInstancesView,
    AbilityMcpProbeView,
    AbilityPackageCatalogView,
    AbilityPackageDetailView,
    AbilityPackageGroupsView,
    AbilityPackageVersionProfileView,
    AbilityTreeChangeResult,
)
from ghrah.protocol.ability_tree import AbilityTreeView
from ghrah.protocol.capability import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.core import ContentReadResult

from ghrah.core.api.capabilities import (
    AbilitiesAdminRequest,
    AbilitiesChangesRequest,
    AbilitiesContentRequest,
    AbilitiesDisableToolRequest,
    AbilitiesEnableToolRequest,
    AbilitiesGrantRequest,
    AbilitiesInstancesRequest,
    AbilitiesPackageDetailRequest,
    AbilitiesPackageGroupsRequest,
    AbilitiesPackageMcpProbeRequest,
    AbilitiesPackagesRequest,
    AbilitiesPackageVersionRequest,
    AbilitiesRevokeRequest,
    AbilitiesStatusRequest,
    AbilitiesTreeRequest,
)
from ghrah.core.api.errors import CoreApiError
from ghrah.core.read.ability import AbilityReadStore

__all__ = ["make_ability_handlers"]


def make_ability_handlers(store: AbilityReadStore) -> dict[str, Handler]:
    """构造 ``abilities`` 分组处理器。

    Args:
        store: 能力关系库只读适配层

    Returns:
        能力名到处理器的映射
    """

    async def changes(request: AbilitiesChangesRequest) -> AbilityChangesPage:
        """读取关系库变更流。"""
        return await store.changes(
            project_id=request.project_id,
            after_seq=request.after_seq,
            limit=request.limit,
        )

    async def tree(request: AbilitiesTreeRequest) -> AbilityTreeView:
        """读取某 Agent 的能力树。"""
        return await store.tree(
            project_id=request.project_id,
            agent_id=request.agent_id,
        )

    async def admin(request: AbilitiesAdminRequest) -> AbilityAdminView:
        """装配能力管理总览。"""
        return await store.admin(
            project_id=request.project_id,
            agent_id=request.agent_id,
        )

    async def content(request: AbilitiesContentRequest) -> ContentReadResult:
        """读取某授权下的成员内容。"""
        result = await store.content(
            project_id=request.project_id,
            agent_id=request.agent_id,
            alias=request.alias,
            member=request.member,
            offset=request.offset,
            limit=request.limit,
        )
        if result is None:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.NOT_FOUND,
                    capability="abilities.content",
                    detail=f"alias {request.alias!r} is not granted",
                ),
            )
        return result

    async def status(request: AbilitiesStatusRequest) -> AbilityApiStatus:
        """读取能力管理面可用性。"""
        return await store.status(project_id=request.project_id)

    async def grant(request: AbilitiesGrantRequest) -> AbilityTreeChangeResult:
        """授予能力。"""
        return await store.grant(
            project_id=request.project_id,
            agent_id=request.agent_id,
            change=request.change,
        )

    async def revoke(request: AbilitiesRevokeRequest) -> AbilityTreeChangeResult:
        """撤销能力。"""
        return await store.revoke(
            project_id=request.project_id,
            agent_id=request.agent_id,
            change=request.change,
        )

    async def enable_tool(request: AbilitiesEnableToolRequest) -> AbilityTreeChangeResult:
        """绑定工具。"""
        return await store.enable_tool(
            project_id=request.project_id,
            agent_id=request.agent_id,
            change=request.change,
        )

    async def disable_tool(request: AbilitiesDisableToolRequest) -> AbilityTreeChangeResult:
        """解绑工具。"""
        return await store.disable_tool(
            project_id=request.project_id,
            agent_id=request.agent_id,
            change=request.change,
        )

    async def packages(request: AbilitiesPackagesRequest) -> AbilityPackageCatalogView:
        """已安装能力包目录。"""
        del request
        return await store.packages()

    async def package_detail(request: AbilitiesPackageDetailRequest) -> AbilityPackageDetailView:
        """单个能力包详情。"""
        return await store.package_detail(namespace=request.namespace, name=request.name)

    async def package_version_profile(
        request: AbilitiesPackageVersionRequest,
    ) -> AbilityPackageVersionProfileView:
        return await store.package_version_profile(
            namespace=request.namespace, name=request.name, version=request.version
        )

    async def package_mcp_probe(request: AbilitiesPackageMcpProbeRequest) -> AbilityMcpProbeView:
        return await store.package_mcp_probe(
            namespace=request.namespace,
            name=request.name,
            version=request.version,
            request=request.request,
        )

    async def package_groups(request: AbilitiesPackageGroupsRequest) -> AbilityPackageGroupsView:
        """能力包分组视图。"""
        return await store.package_groups(
            project_id=request.project_id or None,
            agent_id=request.agent_id,
        )

    async def instances(request: AbilitiesInstancesRequest) -> AbilityInstancesView:
        """项目下的能力实例清单。"""
        return await store.instances(project_id=request.project_id)

    return {
        "abilities.tree": tree,
        "abilities.changes": changes,
        "abilities.admin": admin,
        "abilities.content": content,
        "abilities.status": status,
        "abilities.grant": grant,
        "abilities.revoke": revoke,
        "abilities.enable_tool": enable_tool,
        "abilities.disable_tool": disable_tool,
        "abilities.packages": packages,
        "abilities.package_detail": package_detail,
        "abilities.package_version_profile": package_version_profile,
        "abilities.package_mcp_probe": package_mcp_probe,
        "abilities.package_groups": package_groups,
        "abilities.instances": instances,
    }
