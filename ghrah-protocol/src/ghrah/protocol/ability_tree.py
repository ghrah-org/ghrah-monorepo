# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Ability 资源关系切片的数据契约；身份由受信入口提供，不取自业务载荷。"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TreeKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
TreeId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]


class TreeModel(BaseModel):
    """严格且拒绝未知字段的关系契约。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class OperationNode(TreeModel):
    """确定实例中获授的 Operation 描述。"""

    key: TreeKey
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    availability: Literal["ready", "unavailable", "unknown"] = "unknown"


class ContentResourceNode(TreeModel):
    """已获授 Skill 子资源的确定内容描述。

    ``available`` 只对脚本有意义：脚本可见（正文可读）不等于可执行，
    ``available=false`` 表示已获授可见但未获授执行。正文、附件与引用始终
    可读，恒为 ``true``。
    """

    key: TreeKey
    member: str
    path: str
    media_type: str
    size: int = Field(ge=0)
    digest: str
    available: bool = True


class SkillNode(TreeModel):
    """单 Skill 及其获授附件；正文权限与附件权限分别投影。

    ``scripts`` 是已获授可见的脚本清单；脚本的可见性在这里表达，执行权由
    ``ContentResourceNode.available`` 单独表达（默认不可执行）。
    """

    key: TreeKey
    title: str
    description: str
    body_allowed: bool
    assets: list[ContentResourceNode]
    references: list[ContentResourceNode]
    scripts: list[ContentResourceNode] = Field(default_factory=list)
    availability: Literal["ready", "unavailable", "unknown"] = "unknown"


class SkillReadScope(TreeModel):
    """None 表示选中该类别全部现有资源，空列表表示不授予该类别。

    ``scripts`` 只授予脚本可见（正文可读）；执行权不在此表达，由
    ``GrantAbilityRequest.axes`` 单独给出。
    """

    key: TreeKey
    body: bool = True
    assets: list[TreeKey] | None = None
    references: list[TreeKey] | None = None
    scripts: list[TreeKey] | None = None


class MemberAxes(TreeModel):
    """一个获授成员的可见性与可用性显式事实。

    地址写全名：Operation 为 ``operations/<key>``，内容成员为
    ``skill/<skill>[/assets|references|scripts/<key>]``。未列出时按默认值：
    Operation 与 Skill 内容可见且可用，脚本可见但不可执行。
    """

    member: str = Field(
        pattern=(
            r"^(operations/[a-z][a-z0-9_-]{0,63}"
            r"|skill/[a-z][a-z0-9_-]{0,63}"
            r"(/(assets|references|scripts)/[a-z][a-z0-9_-]{0,63})?)$"
        )
    )
    visible: bool = True
    available: bool = True


class AbilityNode(TreeModel):
    """获授关联；revision 固定此次授予，实例版本另行表示。

    一个 Ability 包可以有多个 Skill：``skills`` 是完整清单；``skill`` 是向后兼容的
    "第一个技能"快捷字段，两者由同一份投影填充。
    """

    alias: TreeKey
    instance_id: TreeId
    instance_revision: int = Field(ge=0)
    definition_digest: str
    version_digest: str
    revision: int = Field(ge=0)
    allowed_roots: list[str]
    operations: list[OperationNode]
    skills: list[SkillNode] = Field(default_factory=list)
    skill: SkillNode | None = None
    availability: Literal["ready", "unavailable", "unknown"] = "unknown"

    @model_validator(mode="after")
    def unique_operations(self) -> AbilityNode:
        """同一实例下的成员键不能含糊地指向多个描述。"""
        if len({item.key for item in self.operations}) != len(self.operations):
            raise ValueError("Operation key 重复")
        if len({item.key for item in self.skills}) != len(self.skills):
            raise ValueError("Skill key 重复")
        return self


class BoundToolNode(TreeModel):
    """已生效的工具选择，引用同一 Agent 的获授成员。"""

    name: TreeKey
    alias: TreeKey
    member: TreeKey
    grant_revision: int = Field(ge=0)
    revision: int = Field(ge=0)


class RuntimeObservation(TreeModel):
    """本 host 在某一时刻的运行观测，不属于持久授权事实。"""

    host_id: TreeId
    observed_at: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$",
        json_schema_extra={"format": "date-time"},
    )

    @model_validator(mode="after")
    def utc_time(self) -> RuntimeObservation:
        """观测时间明确使用 UTC，便于跨 host 区分。"""
        parsed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        if not self.observed_at.endswith("Z") or parsed.tzinfo is None:
            raise ValueError("观测时间必须是 UTC ISO 时间")
        return self


class AbilityTreeView(TreeModel):
    """以同一次读取中的权威关系生成的 Agent 投影。"""

    format: Literal["ghrah.ability-tree.v2"] = "ghrah.ability-tree.v2"
    project_id: TreeId
    agent_id: TreeId
    run_id: TreeId
    revision: int = Field(ge=0)
    abilities: list[AbilityNode]
    bound_tools: list[BoundToolNode]
    runtime: RuntimeObservation | None = None

    @model_validator(mode="after")
    def unique_nodes(self) -> AbilityTreeView:
        """树中一个 alias 或工具名只能定位一个节点。"""
        if len({item.alias for item in self.abilities}) != len(self.abilities):
            raise ValueError("Ability alias 重复")
        if len({item.name for item in self.bound_tools}) != len(self.bound_tools):
            raise ValueError("工具名重复")
        return self


class EmptyTreeRequest(TreeModel):
    """目标取自资源路径，无需在输入中重复自报身份。"""


class GrantAbilityRequest(TreeModel):
    """对当前关系修订提交一次明确获授变更。

    ``skill`` 与 ``skills`` 是同一件事的单/多写法：只给 ``skill`` 时等价于只授予
    一个技能；``skills`` 用来一次授予同一包里的多个技能。``axes`` 显式覆盖
    成员的可见性与可用性，只能收紧或降级本次已授予的成员，不能新增成员。
    ``policy`` 是本次授予所依据策略的摘要，只用于变更历史审计。
    """

    alias: TreeKey
    instance_id: TreeId
    members: list[TreeKey] = Field(default_factory=list)
    allowed_roots: list[str] = Field(default_factory=list)
    skill: SkillReadScope | None = None
    skills: list[SkillReadScope] = Field(default_factory=list)
    whole_container: bool = False
    axes: list[MemberAxes] = Field(default_factory=list)
    policy: str | None = None
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def unique_scopes(self) -> GrantAbilityRequest:
        """同一技能不能在一次变更里出现两份读范围。"""
        if len({item.key for item in self.skills}) != len(self.skills):
            raise ValueError("Skill 读范围重复")
        if len({item.member for item in self.axes}) != len(self.axes):
            raise ValueError("成员轴重复")
        return self


class RevokeAbilityRequest(TreeModel):
    """移除获授关联及其工具选择。"""

    alias: TreeKey
    expected_revision: int = Field(ge=0)
    members: list[str] | None = None


class BindToolRequest(TreeModel):
    """在当前获授集合中选择工具。"""

    alias: TreeKey
    member: TreeKey
    tool_name: TreeKey
    expected_revision: int = Field(ge=0)


class UnbindToolRequest(TreeModel):
    """移除工具选择，不撤销原成员。"""

    tool_name: TreeKey
    expected_revision: int = Field(ge=0)


TreeErrorCode = Literal["not_found", "access_denied", "conflict", "invalid_change", "unavailable"]


class TreeChangeResult(TreeModel):
    """领域结果；失败不意味着另一次未核对的自动重试。"""

    view: AbilityTreeView | None = None
    error: TreeErrorCode | None = None

    @model_validator(mode="after")
    def one_result(self) -> TreeChangeResult:
        """结果与错误必须且只能有一项。"""
        if (self.view is None) == (self.error is None):
            raise ValueError("必须返回投影或错误之一")
        return self
