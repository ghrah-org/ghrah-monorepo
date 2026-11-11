# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 资源的观察端 REST 契约。

规格、运行快照和能力摘要放在同一次读取里。能力授权的明细仍走
``ability_control``，本模块不接收授权字段。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ghrah.protocol.ability_tree import TreeModel

__all__ = [
    "AgentAbilitySummary",
    "AgentAdminView",
    "AgentCreateRequest",
    "AgentDeletedView",
    "AgentDesiredStateRequest",
    "AgentListView",
    "AgentPromptSummary",
    "AgentRuntimeView",
    "AgentSpecPatch",
    "AgentSpecificationDetail",
    "AgentSpecificationSummary",
    "AgentSummaryView",
]


class AgentPromptSummary(TreeModel):
    """提示词正文的摘要。列表不返回正文。"""

    digest: str
    size_bytes: int = Field(ge=0)


class AgentRuntimeView(TreeModel):
    """进程是否在线。不含会话和邮箱。"""

    availability: Literal["online", "offline", "unknown"]
    incarnation_id: str | None = None
    busy: bool | None = None
    error: str | None = None


class AgentAbilitySummary(TreeModel):
    """能力关系的摘要。明细仍读取 ability-admin。"""

    revision: int = Field(ge=0)
    state: Literal["running", "stopped", "unassembled"]
    requires_respawn: bool = False
    pending_reasons: list[str] = Field(default_factory=list)


class AgentSpecificationSummary(TreeModel):
    """列表用的规格。不含提示词正文。"""

    agent_id: str
    name: str
    cluster_id: str
    endpoint_name: str | None = None
    prompt: AgentPromptSummary
    desired_state: Literal["running", "stopped"]
    manifest_ref: str = ""
    runtime_status: str
    runtime_error: str = ""
    project_version: int = Field(ge=0)


class AgentSpecificationDetail(AgentSpecificationSummary):
    """单体规格。包含提示词正文。"""

    system_prompt: str = ""


class AgentSummaryView(TreeModel):
    """列表中的一个 Agent。"""

    format: Literal["ghrah.agent-control.summary.v1"] = "ghrah.agent-control.summary.v1"
    project_id: str
    specification: AgentSpecificationSummary
    runtime: AgentRuntimeView
    ability: AgentAbilitySummary | None = None
    ability_unavailable: str | None = None


class AgentListView(TreeModel):
    """项目内的 Agent 列表。"""

    format: Literal["ghrah.agent-control.list.v1"] = "ghrah.agent-control.list.v1"
    project_id: str
    agents: list[AgentSummaryView] = Field(default_factory=list)


class AgentAdminView(TreeModel):
    """一次读取返回的完整 Agent 资源。"""

    format: Literal["ghrah.agent-control.v1"] = "ghrah.agent-control.v1"
    project_id: str
    specification: AgentSpecificationDetail
    runtime: AgentRuntimeView
    ability: AgentAbilitySummary | None = None
    ability_unavailable: str | None = None
    effective: Literal["next_start", "stored"] | None = None
    spec_overrides_template: bool = False


class AgentCreateRequest(TreeModel):
    """创建 Agent。默认停止，避免未声明就尝试启动。"""

    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    endpoint_name: str | None = None
    system_prompt: str = ""
    desired_state: Literal["running", "stopped"] = "stopped"


class AgentSpecPatch(TreeModel):
    """修改已保存的端点或提示词。至少带一个可写字段。"""

    expected_revision: int = Field(ge=0)
    endpoint_name: str | None = None
    system_prompt: str | None = None

    @model_validator(mode="after")
    def require_one_field(self) -> AgentSpecPatch:
        """省略两个可写字段时没有可保存的变更。"""
        provided = self.model_fields_set - {"expected_revision"}
        if not provided:
            raise ValueError("至少提供 endpoint_name 或 system_prompt")
        return self


class AgentDesiredStateRequest(TreeModel):
    """声明期望的运行状态。"""

    expected_revision: int = Field(ge=0)
    state: Literal["running", "stopped"]


class AgentDeletedView(TreeModel):
    """删除成功后的项目版本，供下一次条件写使用。"""

    format: Literal["ghrah.agent-control.deleted.v1"] = "ghrah.agent-control.deleted.v1"
    project_id: str
    agent_id: str
    project_version: int = Field(ge=0)
