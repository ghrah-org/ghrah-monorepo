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
    "AbilityLifecycleView",
    "AbilityPendingRespawnView",
    "AbilitySurfaceEntryView",
    "AbilitySurfaceView",
    "AbilityTreeChangeResult",
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
    """一次授权/工具写入的权威响应；失败由 HTTP 错误体表达。"""

    format: Literal["ghrah.ability-control.change.v1"] = "ghrah.ability-control.change.v1"
    view: AbilityTreeView
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
