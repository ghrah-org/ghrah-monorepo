# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力域管理面（Observer REST）的只读/写回执数据契约。

本模块只定义观察端 HTTP 管理面使用的传输模型；授权事实与写入语义仍由
``ghrah.ability`` 领域维护。所有模型严格且拒绝未知字段，TS 侧由
``scripts/export_ability_contracts.py`` 单向生成，不手写。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ghrah.protocol.ability_tree import (
    AbilityTreeView,
    MemberAxes,
    TreeId,
    TreeKey,
    TreeModel,
)

__all__ = [
    "AbilityAdminView",
    "AbilityApiErrorCode",
    "AbilityApiErrorView",
    "AbilityApiFeatures",
    "AbilityApiStatus",
    "AbilityBoundToolView",
    "AbilityChangeView",
    "AbilityChangesPage",
    "AbilityGrantPolicyView",
    "AbilityGrantView",
    "AbilityInstanceVersionView",
    "AbilityInstanceView",
    "AbilityInstancesView",
    "AbilityLifecycleView",
    "AbilityPackageCatalogView",
    "AbilityPackageDetailView",
    "AbilityPackageGroupAgentView",
    "AbilityPackageGroupProjectView",
    "AbilityPackageGroupVersionView",
    "AbilityPackageGroupView",
    "AbilityPackageGroupsView",
    "AbilityPackageVersionDetail",
    "AbilityPackageVersionRow",
    "AbilityPendingRespawnView",
    "AbilitySurfaceEntryView",
    "AbilitySurfaceView",
    "AbilityTreeChangeResult",
    "AbilityUnboundVersionView",
    "AbilityVerifyView",
]

#: 管理面错误码（HTTP 状态由服务端映射；前端只做分支与文案）。
AbilityApiErrorCode = Literal[
    "invalid_request",
    "access_denied",
    "not_found",
    "conflict",
    "invalid_change",
    "unavailable",
    "capability_unavailable",
    "internal",
]


class AbilityApiFeatures(TreeModel):
    """管理面当前可用的功能子集；前端据此隐藏入口而不是点击后失败。"""

    tree: bool = True
    grants_write: bool = True
    versions_read: bool = True
    versions_write: bool = False
    packages_read: bool = False
    packages_install: bool = False
    surface_write: bool = False


class AbilityApiStatus(TreeModel):
    """管理面状态；未启用时也是唯一可达端点。"""

    format: Literal["ghrah.ability-control.v1"] = "ghrah.ability-control.v1"
    api_version: int = Field(default=1, ge=1)
    ability_source: Literal["builtin", "legacy"]
    legacy_deprecated: bool = False
    operator: str | None = None
    features: AbilityApiFeatures = AbilityApiFeatures()
    reason: str | None = None


class AbilityGrantView(TreeModel):
    """关系库中的一条获授事实（操作员视角的原始快照）。"""

    alias: TreeKey
    instance_id: TreeId
    instance_revision: int = Field(ge=0)
    version_digest: str
    definition_digest: str
    members: list[str] = Field(default_factory=list)
    content: list[str] = Field(default_factory=list)
    roots: list[str] = Field(default_factory=list)
    axes: list[MemberAxes] = Field(default_factory=list)
    revision: int = Field(ge=0)
    source: Literal["admin", "deployment", "agent-default", "migration"]


class AbilityBoundToolView(TreeModel):
    """已写入的工具选择；``revision`` 是工具行代次，不是关系 revision。"""

    name: TreeKey
    alias: TreeKey
    member: TreeKey
    grant_revision: int = Field(ge=0)
    revision: int = Field(ge=0)


class AbilitySurfaceEntryView(TreeModel):
    """表面投影的一个条目；仅表示��选 ∩ 可见 ∩ 可暴露"，不等于可执行。"""

    tool_name: TreeKey
    member: TreeKey
    package: str
    static: bool = False
    available: bool = True


class AbilityPendingRespawnView(TreeModel):
    """本次运行与关系库目标事实的差；新增/扩大需下次 spawn 才生效。"""

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class AbilityLifecycleView(TreeModel):
    """Agent 在当前项目中的装配与运行状态。"""

    state: Literal["running", "stopped", "unassembled"]
    requires_respawn: bool = False
    pending_reasons: list[str] = Field(default_factory=list)


class AbilityGrantPolicyView(TreeModel):
    """部署级启动策略的只读摘要；UI 不编辑。"""

    mode: Literal["replace", "merge"]
    digest: str
    rules: int = Field(ge=0)


class AbilitySurfaceView(TreeModel):
    """有效面、当前运行工具表与下次 spawn 差异。"""

    mode: Literal["auto", "static", "progressive"] = "auto"
    static: list[str] = Field(default_factory=list)
    effective: list[AbilitySurfaceEntryView] = Field(default_factory=list)
    current_runtime: list[str] | None = None
    pending_respawn: AbilityPendingRespawnView = AbilityPendingRespawnView()


class AbilityAdminView(TreeModel):
    """装配页的组合只读视图；只是同一事实的裁切，不是第五层权威。"""

    format: Literal["ghrah.ability-control.admin.v1"] = "ghrah.ability-control.admin.v1"
    project_id: TreeId
    agent_id: TreeId
    revision: int = Field(ge=0)
    run_id: str | None = None
    lifecycle: AbilityLifecycleView
    grant_policy: AbilityGrantPolicyView | None = None
    grants: list[AbilityGrantView] = Field(default_factory=list)
    bound_tools: list[AbilityBoundToolView] = Field(default_factory=list)
    surface: AbilitySurfaceView = AbilitySurfaceView()
    changes_cursor: int = Field(default=0, ge=0)


class AbilityChangeView(TreeModel):
    """一条关系修订审计记录（``ability_changes`` 的投影）。"""

    seq: int = Field(ge=0)
    agent: TreeId
    revision: int = Field(ge=0)
    reason: str
    actor: dict[str, Any] = Field(default_factory=dict)
    before_revision: int = Field(ge=0)
    version_digest: str | None = None
    source: str
    policy: str | None = None


class AbilityChangesPage(TreeModel):
    """审计分页；``next_after_seq`` 是下一页游标。"""

    format: Literal["ghrah.ability-control.changes.v1"] = "ghrah.ability-control.changes.v1"
    changes: list[AbilityChangeView] = Field(default_factory=list)
    next_after_seq: int = Field(ge=0)
    has_more: bool = False


class AbilityTreeChangeResult(TreeModel):
    """一次授权/工具写入的权威响应；失败由 HTTP 错误体表达。

    Agent 未运行时关系库仍可写（授权是 Agent 级持久事实），此时无法构造
    需要受信 run 的 ``AbilityTreeView``，``view`` 明确为 null，调用方重读
    admin/tree 或等启动后再取运行视图。
    """

    format: Literal["ghrah.ability-control.change.v1"] = "ghrah.ability-control.change.v1"
    view: AbilityTreeView | None = None
    revision: int = Field(ge=0)


class AbilityApiErrorView(TreeModel):
    """管理面错误体。"""

    code: AbilityApiErrorCode
    message: str
    detail: dict[str, Any] | None = None


class AbilityApiErrorResponse(TreeModel):
    """``{"error": {...}}`` 的统一包装。"""

    error: AbilityApiErrorView

    @model_validator(mode="after")
    def error_present(self) -> AbilityApiErrorResponse:
        """错误体必须带 code；空错误视为编程错误。"""
        if not self.error.code:
            raise ValueError("错误码不能为空")
        return self


class AbilityPackageVersionRow(TreeModel):
    """目录里的一个版本。一行一个版本，不含安装根路径。"""

    namespace: str
    name: str | None = None
    full_name: str
    title: str | None = None
    origin: Literal["builtin", "installed"]
    version: str
    member_count: int | None = Field(default=None, ge=0)
    lock_state: str
    problem: str | None = None
    shadowed_builtin: bool = False


class AbilityPackageCatalogView(TreeModel):
    """发行版能力目录。不含项目当前选中的版本。"""

    format: Literal["ghrah.ability-control.packages.v1"] = "ghrah.ability-control.packages.v1"
    installed_available: bool
    packages: list[AbilityPackageVersionRow] = Field(default_factory=list)


class AbilityVerifyView(TreeModel):
    """一次安装校验的结果。差异路径是包内相对路径。"""

    ok: bool
    code: str | None = None
    message: str | None = None
    package_digest: str | None = None
    actual_digest: str | None = None
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)


class AbilityPackageVersionDetail(TreeModel):
    """一个包版本的可读详情。不含定义全文和文件字节。"""

    version: str
    origin: Literal["builtin", "installed"]
    title: str | None = None
    description: str | None = None
    operations: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    lock_state: str
    problem: str | None = None
    verify: AbilityVerifyView | None = None
    shadowed_builtin: bool = False


class AbilityPackageDetailView(TreeModel):
    """两段身份的包详情。只有一段身份的包不走这条路径。"""

    format: Literal["ghrah.ability-control.package.v1"] = "ghrah.ability-control.package.v1"
    namespace: str
    name: str
    full_name: str
    versions: list[AbilityPackageVersionDetail] = Field(default_factory=list)


class AbilityInstanceVersionView(TreeModel):
    """项目里已提交的一个版本，以及它是否为实例当前选择。"""

    digest: str
    definition_digest: str | None = None
    member_count: int | None = Field(default=None, ge=0)
    selected: bool = False
    problem: str | None = None


class AbilityInstanceView(TreeModel):
    """一个项目实例及其同身份的版本。不返回实例根路径。"""

    instance_id: str
    revision: int = Field(ge=0)
    selected_digest: str
    full_name: str | None = None
    title: str | None = None
    member_count: int | None = Field(default=None, ge=0)
    problem: str | None = None
    versions: list[AbilityInstanceVersionView] = Field(default_factory=list)


class AbilityUnboundVersionView(TreeModel):
    """已提交、但没有任何实例按身份引用的版本。"""

    digest: str
    full_name: str | None = None
    definition_digest: str | None = None
    member_count: int | None = Field(default=None, ge=0)
    problem: str | None = None


class AbilityInstancesView(TreeModel):
    """项目关系库中的实例与版本。只读，不改变当前选择。"""

    format: Literal["ghrah.ability-control.instances.v1"] = "ghrah.ability-control.instances.v1"
    project_id: TreeId
    instances: list[AbilityInstanceView] = Field(default_factory=list)
    unbound_versions: list[AbilityUnboundVersionView] = Field(default_factory=list)


class AbilityPackageGroupProjectView(TreeModel):
    """一个版本在项目关系库中的提交与实例事实。没有提交时 ``committed`` 为假。"""

    committed: bool
    digest: str | None = None
    instance_id: TreeId | None = None
    selected: bool = False
    instance_revision: int | None = Field(default=None, ge=0)


class AbilityPackageGroupAgentView(TreeModel):
    """一个版本所在实例有没有授予这个 Agent。没有授予时 ``alias`` 为空。"""

    granted: bool
    alias: TreeKey | None = None


class AbilityPackageGroupVersionView(TreeModel):
    """包组里的一个版本。发行版、项目和 Agent 共用这一行，只填当前范围的事实。"""

    version: str | None = None
    origin: Literal["builtin", "installed"] | None = None
    lock_state: str | None = None
    problem: str | None = None
    shadowed_builtin: bool = False
    member_count: int | None = Field(default=None, ge=0)
    project: AbilityPackageGroupProjectView | None = None
    agent: AbilityPackageGroupAgentView | None = None


class AbilityPackageGroupView(TreeModel):
    """一个 ``namespace.name`` 包组。定义面找不到的已提交版本仍留在组里。"""

    namespace: str | None = None
    name: str | None = None
    full_name: str | None = None
    title: str | None = None
    versions: list[AbilityPackageGroupVersionView] = Field(default_factory=list)


class AbilityPackageGroupsView(TreeModel):
    """按包身份对齐后的只读名单。``scope`` 决定行上哪些事实有值。"""

    format: Literal["ghrah.ability-control.package-groups.v1"] = (
        "ghrah.ability-control.package-groups.v1"
    )
    scope: Literal["distribution", "project", "agent"]
    installed_available: bool
    project_id: TreeId | None = None
    agent_id: TreeId | None = None
    groups: list[AbilityPackageGroupView] = Field(default_factory=list)

    @model_validator(mode="after")
    def scope_facts(self) -> AbilityPackageGroupsView:
        """发行版不带项目或 Agent；项目范围不带授予；Agent 范围两者都要有。"""
        if self.scope == "distribution":
            if self.project_id is not None or self.agent_id is not None:
                raise ValueError("发行版范围不带项目或 Agent")
            self._require(project=False, agent=False)
        elif self.scope == "project":
            if self.project_id is None or self.agent_id is not None:
                raise ValueError("项目范围只带项目")
            self._require(project=True, agent=False)
        else:
            if self.project_id is None or self.agent_id is None:
                raise ValueError("Agent 范围必须带项目和 Agent")
            self._require(project=True, agent=True)
        return self

    def _require(self, *, project: bool, agent: bool) -> None:
        for group in self.groups:
            for version in group.versions:
                has_project = version.project is not None
                has_agent = version.agent is not None
                if has_project != project or has_agent != agent:
                    raise ValueError("版本行的项目或 Agent 事实与范围不一致")
