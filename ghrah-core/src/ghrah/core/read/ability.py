# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力关系库（ability access）读取。

数据源是 Core 自己的 :class:`ghrah.ability.access.AbilityAccessStore`——
它打开的就是作用域固定的 ActionChain sqlite，因此本层的库路径与
``chain`` / ``mailbox`` 一样在构造时给定，实例本身就是作用域。

**离线读语义**：端口不假设 Agent 正在运行，``runs`` 回调固定返回 ``None``。
需要「当前是否在跑 / 可见工具表」这类运行时事实时走 ``agents.runtime``，
而不是在这里伪造。
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiFeatures,
    AbilityApiStatus,
    AbilityChangesPage,
    AbilityGrantWriteView,
    AbilityInstancesView,
    AbilityMcpProbeRequest,
    AbilityMcpProbeView,
    AbilityPackageCatalogView,
    AbilityPackageDetailView,
    AbilityPackageGroupsView,
    AbilityPackageImportView,
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
from ghrah.ability.package.archive import ArchiveError, detect_format, extract_package
from ghrah.ability.package.installed_store import parse_full_name
from ghrah.ability.package.installer import (
    AbilityInstaller,
    InstallerError,
    relock_source,
)
from ghrah.ability.package.provider import validate_source_tree
from ghrah.core.read.ability_catalog import (
    CatalogError,
    build_instances_view,
    build_package_catalog,
    build_package_detail,
    build_package_groups,
    build_version_profile,
)
from ghrah.manifest.errors import ManifestValidationError

__all__ = ["AbilityConflictError", "AbilityReadStore", "AbilityUnavailableError"]

#: 归档域错误码 → Core 目录错误码。
_ARCHIVE_CODES = {
    "invalid_request": "invalid_request",
    "unsupported_format": "unsupported_format",
    "invalid_package": "invalid_package",
}

#: 安装器错误码 → Core 目录错误码；没列出的都按「包不合法」处理。
_INSTALL_CODES = {
    "version_exists": "conflict",
    "install_failed": "unavailable",
    "unsupported_source": "invalid_package",
    "symlink_rejected": "invalid_package",
    "lock_in_source": "invalid_package",
    "reserved_namespace": "invalid_package",
    "reserved_member_name": "invalid_package",
    "invalid_definition": "invalid_package",
    "digest_mismatch": "invalid_package",
}

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
    """作用域固定的能力关系库读取器，只做读操作。

    构造时固定库路径与作用域；``scope_id`` 为 ``None`` 时只提供全局视图
    （包目录、包组、状态），涉及关系库的方法会抛 ``AbilityUnavailableError``。

    Args:
        db_path: ActionChain sqlite 的路径（能力关系库与链共用同一文件）
        scope_id: 作用域标识；``None`` 表示未绑定作用域的全局读面
        resolve_run: Agent 生命周期 run 的解析器；未注入时离线读
        resolve_tools: Agent 当前可见工具表的解析器
        grant_policy: 部署显式配置的启动授权策略
        install_root: 能力包安装根
        agentconf_db_path: MCP 探测使用的受信配置库路径
        reclaim_stale: 打开关系库时是否回收过期运行态
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        scope_id: str | None = None,
        resolve_run: Callable[[str], str | None] | None = None,
        resolve_tools: Callable[[str], tuple[str, ...] | None] | None = None,
        grant_policy: Any = None,
        install_root: str | None = None,
        agentconf_db_path: str | None = None,
        reclaim_stale: bool = False,
    ) -> None:
        self._db_path = Path(db_path)
        self._scope_id = scope_id
        self._resolve_run = resolve_run
        self._resolve_tools = resolve_tools
        self._grant_policy = grant_policy
        self._install_root = install_root
        self._agentconf_db_path = agentconf_db_path
        self._reclaim_stale = reclaim_stale
        self._store: AbilityAccessStore | None = None
        self._lock = asyncio.Lock()

    def _operator(self) -> AccessIdentity:
        """受信操作员身份：作用域来自构造，不从请求体读取。

        Raises:
            AbilityUnavailableError: 实例没有绑定作用域
        """
        if self._scope_id is None:
            raise AbilityUnavailableError("能力读面未绑定作用域")
        return AccessIdentity(
            self._scope_id,
            _DEPLOYMENT_OPERATOR,
            None,
            operator=True,
            source="admin",
        )

    def _run_of(self, agent_id: str) -> str | None:
        """解析某 Agent 的生命周期 run；未注入或离线时为 ``None``。"""
        if self._resolve_run is None:
            return None
        return self._resolve_run(agent_id)

    async def store(self) -> AbilityAccessStore:
        """取（或懒打开）本作用域的能力关系库。

        Raises:
            AbilityUnavailableError: 实例未绑定作用域，或关系库文件不存在
        """
        scope_id = self._scope_id
        if scope_id is None:
            raise AbilityUnavailableError("能力读面未绑定作用域")
        async with self._lock:
            if self._store is not None:
                return self._store
            if not self._db_path.is_file():
                raise AbilityUnavailableError(str(self._db_path))
            store = await AbilityAccessStore.open(
                self._db_path,
                scope_id,
                operators=frozenset({_DEPLOYMENT_OPERATOR}),
                runs=self._run_of,
                reclaim_stale=self._reclaim_stale,
            )
            self._store = store
            return store

    async def tree(self, *, agent_id: str) -> AbilityTreeView:
        """读取某 Agent 的能力树。

        视图含运行时装配状态，因此单元必须在线；离线时关系库会拒绝。

        Args:
            agent_id: 稳定 Agent ID

        Returns:
            能力树视图
        """
        store = await self.store()
        return await store.view(self._operator(), agent_id)

    async def admin(
        self,
        *,
        agent_id: str,
        grant_policy: Any = None,
    ) -> AbilityAdminView:
        """装配某 Agent 的能力管理总览。"""
        from ghrah.core.read.ability_admin import build_admin_view

        store = await self.store()
        operator = self._operator()
        runtime_tools = self._resolve_tools(agent_id) if self._resolve_tools else None
        return await build_admin_view(
            store,
            operator,
            agent_id,
            runtime_tools=runtime_tools,
            grant_policy=grant_policy if grant_policy is not None else self._grant_policy,
        )

    async def content(
        self,
        *,
        agent_id: str,
        alias: str,
        member: str,
        offset: int = 0,
        limit: int = 65536,
    ) -> Any:
        """读取某授权下的成员内容；未授权返回 ``None``。"""
        from ghrah.core.read.ability_admin import read_content

        store = await self.store()
        return await read_content(
            store,
            self._operator(),
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

    async def package_groups(self, *, agent_id: str | None = None) -> AbilityPackageGroupsView:
        """能力包分组视图；实例未绑定作用域时只返回全局发行版视图。"""
        if self._scope_id is None:
            return await build_package_groups(self._install_root)
        store = await self.store()
        operator = self._operator()
        return await build_package_groups(
            self._install_root,
            store=store,
            operator=operator,
            project_id=self._scope_id,
            agent_id=agent_id,
        )

    async def package_import(
        self, *, archive_path: str, source_name: str
    ) -> AbilityPackageImportView:
        """导入一个能力包归档：解包、校验、安装、改写安装记录。

        归档由调用方（Subject）落地成临时文件；本方法只负责把它变成安装根下的一代。
        安装记录由安装器写出，导入完成后把来源改写成 ``archive`` + 上传文件名。

        **信任边界**：安装期 ABI 预检会加载并 ``exec`` 上传包里的 operation 源码，
        也就是「上传安装 = 在当前进程内执行一次包代码」，且不在沙箱内。本方法
        假定调用方已经完成鉴权（Subject 上传端点在调用前先过 ``_authorized``），
        并且只把它暴露给受信操作者；不要新增绕过鉴权的调用路径。

        Args:
            archive_path: 归档的绝对路径
            source_name: 用户看到的原始文件名

        Returns:
            导入结果；同摘要重复导入时 ``already_installed=True``

        Raises:
            CatalogError: 安装根未配置、格式不支持、包内容不合法或版本冲突
        """
        root = self._install_root
        if not root:
            raise CatalogError("unavailable", "能力包安装根未配置，无法导入")
        archive = Path(archive_path)
        if not archive.is_file():
            raise CatalogError("invalid_request", "上传的归档不存在")
        try:
            archive_format = detect_format(archive, name=source_name)
        except ArchiveError as exc:
            raise CatalogError(
                _ARCHIVE_CODES.get(exc.code, "invalid_package"), exc.message
            ) from exc
        with tempfile.TemporaryDirectory(prefix="ghrah-ability-import-") as workspace:
            extraction = Path(workspace) / "src"
            try:
                source_root = extract_package(archive, extraction, format=archive_format)
            except ArchiveError as exc:
                raise CatalogError(
                    _ARCHIVE_CODES.get(exc.code, "invalid_package"), exc.message
                ) from exc
            installer = AbilityInstaller(root)
            # 先建后验：扫盘提取成员、补写标准 ability.yaml，再对生成的定义做 ABI 预检。
            # 归档没有顶层目录时，包身份取上传文件名（解包目录名只是临时的 "src"）。
            hint = source_name if source_root == extraction else None
            try:
                prepared = installer.prepare(source_root, name_hint=hint)
            except InstallerError as exc:
                raise CatalogError(
                    _INSTALL_CODES.get(exc.code, "invalid_package"), exc.message
                ) from exc
            if not prepared.is_mcp:
                # MCP 声明的成员来自远端服务器，安装器现场观测；没有本地入口可预检。
                try:
                    validate_source_tree(source_root)
                except ManifestValidationError as exc:
                    raise CatalogError("invalid_package", str(exc)) from exc
            try:
                result = installer.install(source_root)
            except InstallerError as exc:
                raise CatalogError(
                    _INSTALL_CODES.get(exc.code, "invalid_package"), exc.message
                ) from exc
        namespace, name = parse_full_name(result.full_name) or (
            result.full_name,
            result.full_name,
        )
        if not result.already_installed:
            try:
                relock_source(
                    root,
                    namespace=namespace,
                    name=name,
                    version=result.version,
                    kind="archive",
                    ref=source_name or None,
                )
            except InstallerError as exc:
                raise CatalogError("unavailable", exc.message) from exc
        profile = None
        try:
            profile = await self.package_version_profile(
                namespace=namespace, name=name, version=result.version
            )
        except CatalogError:
            profile = None
        return AbilityPackageImportView(
            namespace=namespace,
            name=name,
            full_name=result.full_name,
            version=result.version,
            package_digest=result.package_digest,
            operation_count=len(profile.operations) if profile else 0,
            skill_count=len(profile.skills) if profile else 0,
            archive_format=archive_format,
            source_name=source_name or None,
            already_installed=result.already_installed,
            lock_written=not result.already_installed,
            verify=profile.verify if profile else None,
        )

    async def instances(self) -> AbilityInstancesView:
        """本作用域已提交的能力实例清单；链库尚未建立时返回空清单。

        Raises:
            AbilityUnavailableError: 实例没有绑定作用域
        """
        operator = self._operator()
        try:
            store = await self.store()
        except AbilityUnavailableError:
            return AbilityInstancesView(project_id=operator.project_id)
        return await build_instances_view(store, operator)

    async def status(self) -> AbilityApiStatus:
        """读取能力管理面的可用性（作用域级，不查库）。

        这里如实反映 **Core 侧已实现** 的面：树、变更流与四个写操作已就绪，
        版本/包/可见面写尚未提供——调用方据此决定按钮是否可用。

        Returns:
            管理面状态
        """
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

    async def grant(self, *, agent_id: str, change: GrantAbilityRequest) -> AbilityTreeChangeResult:
        """授予能力；返回写后的视图与修订号。"""
        store = await self.store()
        operator = self._operator()
        return await self._write(
            store, operator, agent_id, lambda: store.write_grant(operator, agent_id, change)
        )

    async def revoke(
        self, *, agent_id: str, change: RevokeAbilityRequest
    ) -> AbilityTreeChangeResult:
        """撤销能力；返回写后的视图与修订号。"""
        store = await self.store()
        operator = self._operator()
        return await self._write(
            store, operator, agent_id, lambda: store.write_revoke(operator, agent_id, change)
        )

    async def write_grant(
        self, *, agent_id: str, change: GrantAbilityRequest
    ) -> AbilityGrantWriteView:
        """只写授权事实（装配期），不读运行实例、不产出工具表投影。

        与 :meth:`grant` 的分工：创建 Agent 时授权必须先于注册落库，此时没有
        受信 run 可读，因此不能走带投影的路径。写入来源记为 ``assembly``。
        """
        store = await self.store()
        revision = await store.write_grant(self._assembly_operator(), agent_id, change)
        return AbilityGrantWriteView(agent_id=agent_id, revision=revision)

    async def write_revoke(
        self, *, agent_id: str, change: RevokeAbilityRequest
    ) -> AbilityGrantWriteView:
        """只撤销授权事实，用于创建失败时的补偿回滚；不产出投影。"""
        store = await self.store()
        revision = await store.write_revoke(self._assembly_operator(), agent_id, change)
        return AbilityGrantWriteView(agent_id=agent_id, revision=revision)

    def _assembly_operator(self) -> AccessIdentity:
        """装配期写入身份：来源记为 ``assembly``，与管理员写入在审计上区分。"""
        return replace(self._operator(), source="assembly")

    async def enable_tool(
        self, *, agent_id: str, change: BindToolRequest
    ) -> AbilityTreeChangeResult:
        """绑定工具；返回写后的视图与修订号。"""
        store = await self.store()
        operator = self._operator()
        return await self._write(
            store, operator, agent_id, lambda: store.write_bind_tool(operator, agent_id, change)
        )

    async def disable_tool(
        self, *, agent_id: str, change: UnbindToolRequest
    ) -> AbilityTreeChangeResult:
        """解绑工具；返回写后的视图与修订号。"""
        store = await self.store()
        operator = self._operator()
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

    async def changes(self, *, after_seq: int, limit: int) -> AbilityChangesPage:
        """读取关系库的变更流（作用域级，不按 Agent 过滤）。

        Args:
            after_seq: 只返回序号大于它的变更
            limit: 页大小（1..200）

        Returns:
            变更分页（``next_after_seq`` 为下一页游标）
        """
        store = await self.store()
        rows = await store.changes(
            self._operator(),
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
        """关闭已打开的关系库（幂等）。"""
        async with self._lock:
            store = self._store
            self._store = None
        if store is not None:
            await store.close()
