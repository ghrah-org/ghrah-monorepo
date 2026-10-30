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


class AbilityNode(TreeModel):
    """获授关联；revision 固定此次授予，实例版本另行表示。"""

    alias: TreeKey
    instance_id: TreeId
    instance_revision: int = Field(ge=0)
    definition_digest: str
    version_digest: str
    revision: int = Field(ge=0)
    allowed_roots: list[str]
    operations: list[OperationNode]
    availability: Literal["ready", "unavailable", "unknown"] = "unknown"

    @model_validator(mode="after")
    def unique_operations(self) -> "AbilityNode":
        """同一实例下的成员键不能含糊地指向多个描述。"""
        if len({item.key for item in self.operations}) != len(self.operations):
            raise ValueError("Operation key 重复")
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
    observed_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

    @model_validator(mode="after")
    def utc_time(self) -> "RuntimeObservation":
        """观测时间明确使用 UTC，便于跨 host 区分。"""
        parsed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        if not self.observed_at.endswith("Z") or parsed.tzinfo is None:
            raise ValueError("观测时间必须是 UTC ISO 时间")
        return self


class AbilityTreeView(TreeModel):
    """以同一次读取中的权威关系生成的 Agent 投影。"""

    format: Literal["ghrah.ability-tree.v1"] = "ghrah.ability-tree.v1"
    project_id: TreeId
    agent_id: TreeId
    run_id: TreeId
    revision: int = Field(ge=0)
    abilities: list[AbilityNode]
    bound_tools: list[BoundToolNode]
    runtime: RuntimeObservation | None = None

    @model_validator(mode="after")
    def unique_nodes(self) -> "AbilityTreeView":
        """树中一个 alias 或工具名只能定位一个节点。"""
        if len({item.alias for item in self.abilities}) != len(self.abilities):
            raise ValueError("Ability alias 重复")
        if len({item.name for item in self.bound_tools}) != len(self.bound_tools):
            raise ValueError("工具名重复")
        return self


class EmptyTreeRequest(TreeModel):
    """目标取自资源路径，无需在输入中重复自报身份。"""


class GrantAbilityRequest(TreeModel):
    """对当前关系修订提交一次明确获授变更。"""

    alias: TreeKey
    instance_id: TreeId
    members: list[TreeKey] = Field(min_length=1)
    allowed_roots: list[str] = Field(min_length=1)
    expected_revision: int = Field(ge=0)


class RevokeAbilityRequest(TreeModel):
    """移除获授关联及其工具选择。"""

    alias: TreeKey
    expected_revision: int = Field(ge=0)


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
    def one_result(self) -> "TreeChangeResult":
        """结果与错误必须且只能有一项。"""
        if (self.view is None) == (self.error is None):
            raise ValueError("必须返回投影或错误之一")
        return self
