# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力关系库（ability access）读取。

数据源是 Core 自己的 :class:`ghrah.ability.access.AbilityAccessStore`——
它打开的就是项目 ActionChain sqlite，因此本层的 :class:`DbPathResolver`
与 ``chain`` / ``mailbox`` 共用同一个解析器。

**离线读语义**：端口不假设 Agent 正在运行，``runs`` 回调固定返回 ``None``。
需要「当前是否在跑 / 可见工具表」这类运行时事实时走 ``agents.runtime``，
而不是在这里伪造。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiFeatures,
    AbilityApiStatus,
    AbilityChangesPage,
    AbilityChangeView,
    AbilityInstancesView,
    AbilityMcpProbeRequest,
    AbilityMcpProbeView,
    AbilityPackageCatalogView,
    AbilityPackageDetailView,
    AbilityPackageGroupsView,
    AbilityPackageVersionProfileView,
    AbilityTreeChangeResult,
)
from ghrah.protocol.ability_tree import (
    AbilityTreeView,
    BindToolRequest,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    UnbindToolRequest,
)

from ghrah.ability.access import AbilityAccessStore, AccessIdentity
from ghrah.core.read.ability_catalog import (
    build_instances_view,
    build_package_catalog,
    build_package_detail,
    build_package_groups,
    build_version_profile,
)
from ghrah.core.read.chain import DbPathResolver

__all__ = ["AbilityConflictError", "AbilityReadStore", "AbilityUnavailableError"]

_DEPLOYMENT_OPERATOR = "deployment"


class AbilityConflictError(RuntimeError):
    """关系修订冲突：携带最新修订与视图，供调用方重读后重试。"""

    def __init__(self, *, revision: Any = None, view: Any = None) -> None:
        super().__init__("conflict")
        self.revision = revision
        self.view = view


class AbilityUnavailableError(RuntimeError):
    """能力关系库不可用（文件缺失或未迁移）。"""


class AbilityReadStore:
    """按项目懒打开并复用能力关系库，只做读操作。

    Args:
        resolve_db_path: 项目到 ActionChain sqlite 路径的解析器
    """

    def __init__(
        self,
        resolve_db_path: DbPathResolver,
        resolve_run: Callable[[str, str], str | None] | None = None,
        resolve_tools: Callable[[str, str], tuple[str, ...] | None] | None = None,
        grant_policy: Any = None,
        install_root: str | None = None,
        agentconf_db_path: str | None = None,
        reclaim_stale: bool = False,
    ) -> None:
        self._resolve = resolve_db_path
        self._resolve_run = resolve_run
        self._resolve_tools = resolve_tools
        self._grant_policy = grant_policy
        self._install_root = install_root
        self._agentconf_db_path = agentconf_db_path
        self._reclaim_stale = reclaim_stale
        self._stores: dict[str, AbilityAccessStore] = {}
        self._lock = asyncio.Lock()

    def _operator(self, project_id: str) -> AccessIdentity:
        """受信操作员身份：由装配注入，不从请求体读取。"""
        return AccessIdentity(
            project_id,
            _DEPLOYMENT_OPERATOR,
            None,
            operator=True,
            source="admin",
        )

    def _run_of(self, project_id: str, agent_id: str) -> str | None:
        """解析某 Agent 的生命周期 run；未注入或离线时为 ``None``。"""
        if self._resolve_run is None:
            return None
        return self._resolve_run(project_id, agent_id)

    async def project_store(self, project_id: str) -> AbilityAccessStore:
        """取（或懒打开）项目的能力关系库。"""
        async with self._lock:
            store = self._stores.get(project_id)
            if store is not None:
                return store
            path = Path(self._resolve(project_id))
            if not path.is_file():
                raise AbilityUnavailableError(str(path))
            store = await AbilityAccessStore.open(
                path,
                project_id,
                operators=frozenset({_DEPLOYMENT_OPERATOR}),
                runs=lambda agent: self._run_of(project_id, agent),
                reclaim_stale=self._reclaim_stale,
            )
            self._stores[project_id] = store
            return store

    async def tree(self, *, project_id: str, agent_id: str) -> AbilityTreeView:
        """读取某 Agent 的能力树。

        视图含运行时装配状态，因此单元必须在线；离线时关系库会拒绝。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID

        Returns:
            能力树视图
        """
        store = await self.project_store(project_id)
        return await store.view(self._operator(project_id), agent_id)

    async def admin(
        self,
        *,
        project_id: str,
        agent_id: str,
        grant_policy: Any = None,
    ) -> AbilityAdminView:
        """装配某 Agent 的能力管理总览。"""
        from ghrah.core.read.ability_admin import build_admin_view

        store = await self.project_store(project_id)
        runtime_tools = self._resolve_tools(project_id, agent_id) if self._resolve_tools else None
        return await build_admin_view(
            store,
            self._operator(project_id),
            agent_id,
            runtime_tools=runtime_tools,
            grant_policy=grant_policy if grant_policy is not None else self._grant_policy,
        )

    async def content(
        self,
        *,
        project_id: str,
        agent_id: str,
        alias: str,
        member: str,
        offset: int = 0,
        limit: int = 65536,
    ) -> Any:
        """读取某授权下的成员内容；未授权返回 ``None``。"""
        from ghrah.core.read.ability_admin import read_content

        store = await self.project_store(project_id)
        return await read_content(
            store,
            self._operator(project_id),
            agent_id,
            alias=alias,
            member=member,
            offset=offset,
            limit=limit,
        )

    async def packages(self) -> AbilityPackageCatalogView:
        """已安装能力包目录（全局，不查库）。"""
        return build_package_catalog(self._install_root)

    async def package_detail(self, *, namespace: str, name: str) -> AbilityPackageDetailView:
        """单个能力包的详情。"""
        return build_package_detail(self._install_root, namespace, name)

    async def package_version_profile(
        self, *, namespace: str, name: str, version: str
    ) -> AbilityPackageVersionProfileView:
        """读取单个包版本的画像。"""
        return build_version_profile(self._install_root, namespace, name, version)

    async def package_mcp_probe(
        self, *, namespace: str, name: str, version: str, request: AbilityMcpProbeRequest
    ) -> AbilityMcpProbeView:
        """探测指定包版本声明的 MCP 服务。"""
        from ghrah.core.read.ability_mcp_probe import probe_package_mcp

        return await probe_package_mcp(
            self._install_root,
            namespace,
            name,
            version,
            request,
            db_path=self._agentconf_db_path,
        )

    async def package_groups(
        self, *, project_id: str | None = None, agent_id: str | None = None
    ) -> AbilityPackageGroupsView:
        """能力包分组视图；``project_id`` 为空时返回全局视图。"""
        if project_id is None:
            return await build_package_groups(self._install_root)
        store = await self.project_store(project_id)
        operator = self._operator(project_id)
        return await build_package_groups(
            self._install_root,
            store=store,
            operator=operator,
            project_id=project_id,
            agent_id=agent_id,
        )

    async def instances(self, *, project_id: str) -> AbilityInstancesView:
        """项目下的能力实例清单；链库尚未建立时返回空清单。"""
        try:
            store = await self.project_store(project_id)
        except AbilityUnavailableError:
            return AbilityInstancesView(project_id=project_id)
        operator = self._operator(project_id)
        return await build_instances_view(store, operator, project_id)

    async def status(self, *, project_id: str) -> AbilityApiStatus:
        """读取能力管理面的可用性（项目级，不查库）。

        这里如实反映 **Core 侧已实现**的面：树、变更流与四个写操作已就绪，
        版本/包/可见面写尚未提供——调用方据此决定按钮是否可用。

        Args:
            project_id: 项目标识

        Returns:
            管理面状态
        """
        del project_id
        return AbilityApiStatus(
            ability_source="builtin",
            operator=_DEPLOYMENT_OPERATOR,
            features=AbilityApiFeatures(
                tree=True,
                grants_write=True,
                versions_read=False,
                versions_write=False,
                packages_read=False,
                packages_install=False,
                surface_write=False,
            ),
            reason=None,
        )

    async def grant(
        self, *, project_id: str, agent_id: str, change: GrantAbilityRequest
    ) -> AbilityTreeChangeResult:
        """授予能力；返回写后的视图与修订号。"""
        store = await self.project_store(project_id)
        operator = self._operator(project_id)
        return await self._write(
            store, operator, agent_id, lambda: store.write_grant(operator, agent_id, change)
        )

    async def revoke(
        self, *, project_id: str, agent_id: str, change: RevokeAbilityRequest
    ) -> AbilityTreeChangeResult:
        """撤销能力；返回写后的视图与修订号。"""
        store = await self.project_store(project_id)
        operator = self._operator(project_id)
        return await self._write(
            store, operator, agent_id, lambda: store.write_revoke(operator, agent_id, change)
        )

    async def enable_tool(
        self, *, project_id: str, agent_id: str, change: BindToolRequest
    ) -> AbilityTreeChangeResult:
        """绑定工具；返回写后的视图与修订号。"""
        store = await self.project_store(project_id)
        operator = self._operator(project_id)
        return await self._write(
            store, operator, agent_id, lambda: store.write_bind_tool(operator, agent_id, change)
        )

    async def disable_tool(
        self, *, project_id: str, agent_id: str, change: UnbindToolRequest
    ) -> AbilityTreeChangeResult:
        """解绑工具；返回写后的视图与修订号。"""
        store = await self.project_store(project_id)
        operator = self._operator(project_id)
        return await self._write(
            store, operator, agent_id, lambda: store.write_unbind_tool(operator, agent_id, change)
        )

    async def _write(
        self,
        store: AbilityAccessStore,
        operator: AccessIdentity,
        agent_id: str,
        perform: Any,
    ) -> AbilityTreeChangeResult:
        """执行一次写操作；冲突时带上最新 revision 与视图供调用方重读。"""
        from ghrah.ability.access import AccessError as _AccessError

        try:
            revision = await perform()
        except _AccessError as error:
            if "conflict" not in str(getattr(error, "code", "") or error):
                raise
            current: Any = None
            view: Any = None
            try:
                current = await store.agent_revision(operator, agent_id)
            except _AccessError:
                current = None
            try:
                view = await store.view(operator, agent_id)
            except _AccessError:
                view = None
            raise AbilityConflictError(revision=current, view=view) from error
        return await self._after_write(store, operator, agent_id, revision)

    @staticmethod
    async def _after_write(
        store: AbilityAccessStore,
        operator: AccessIdentity,
        agent_id: str,
        revision: int,
    ) -> AbilityTreeChangeResult:
        """写后重读视图：与旧的管理面语义一致（写操作回执带最新视图）。"""
        try:
            view = await store.view(operator, agent_id)
        except Exception:  # noqa: BLE001 - 视图读失败不掩盖写入成功
            view = None
        return AbilityTreeChangeResult(view=view, revision=revision)

    async def changes(self, *, project_id: str, after_seq: int, limit: int) -> AbilityChangesPage:
        """读取关系库的变更流（项目级，不按 Agent 过滤）。

        Args:
            project_id: 项目标识
            after_seq: 只返回序号大于它的变更
            limit: 页大小（1..200）

        Returns:
            变更分页（``next_after_seq`` 为下一页游标）
        """
        store = await self.project_store(project_id)
        rows = await store.changes(
            self._operator(project_id),
            after_seq=after_seq,
            limit=limit,
        )
        from ghrah.core.read.ability_admin import build_change_view

        items = [build_change_view(row) for row in rows]
        return AbilityChangesPage(
            changes=items,
            next_after_seq=items[-1].seq if items else after_seq,
            has_more=len(items) >= limit,
        )

    async def aclose(self) -> None:
        """关闭全部已打开的关系库。"""
        async with self._lock:
            stores = list(self._stores.values())
            self._stores.clear()
        for store in stores:
            await store.close()
