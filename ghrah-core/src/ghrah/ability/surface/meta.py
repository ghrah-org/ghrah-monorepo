# SPDX-License-Identifier: Apache-2.0
"""元工具的共用形状；执行依赖显式注入的当前 Agent 工具面服务。"""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError

from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import TOOL_SURFACE, ExecutionServices
from ghrah.ability.execution.invocation import OperationCall
from ghrah.types.results import ActionOutcome, ActionResult


class SearchInput(BaseModel):
    """关键词查询与有界结果条数。"""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, description="Keywords; English keywords may improve matches.")
    limit: int = Field(default=8, ge=1, le=20, description="Maximum number of results, at most 20.")


class DescribeInput(BaseModel):
    """只按绑定工具名描述，不接受成员路径。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, description="Exact tool name returned by search_tools.")


class ExecuteInput(DescribeInput):
    """目标参数仍由目标成员自己的输入契约校验。"""

    arguments: dict[str, Any] = Field(description="Arguments matching the target input schema.")


class SearchItem(BaseModel):
    """可返回给模型的一行搜索摘要。"""

    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    package: str


class SearchOutput(BaseModel):
    """搜索结果不含 schema。"""

    model_config = ConfigDict(extra="forbid")
    tools: list[SearchItem]


class DescribeOutput(BaseModel):
    """已获授工具的确定契约与当前可用性。"""

    model_config = ConfigDict(extra="forbid")
    name: str
    package: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effects: list[str]
    available: bool
    skills: list[str]


class ExecuteOutput(RootModel[dict[str, Any]]):
    """透传目标成功数据；具体结构由 describe_tool 返回的目标契约确定。"""

    model_config = ConfigDict(
        json_schema_extra={
            "additionalProperties": False,
            "patternProperties": {".*": {}},
        }
    )


class MetaToolAbility(Ability):
    """三个 core Operation 的共同实现，名称由各实现模块固定。"""

    tool_name: ClassVar[str]
    summary: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    @property
    def name(self) -> str:
        """返回固定元工具名。"""
        return self.tool_name

    def bind_tool(self) -> dict[str, Any]:
        """返回声明生成器与模型共用的输入契约。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.summary,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    def get_hooks(self) -> list[Any]:
        """目标审批不转移到元工具。"""
        return []

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        """校验包装参数与受信服务，实际目标执行交给同一个执行器。"""
        surface = services.get(TOOL_SURFACE)
        if surface is None:
            return ActionResult(
                ActionOutcome.FAILURE, {"error": "Tool surface service is not wired"}
            )
        try:
            request = self.input_model.model_validate(call.arguments)
        except ValidationError:
            return ActionResult(ActionOutcome.FAILURE, {"error": "Invalid meta-tool arguments"})
        return await surface.invoke(self.name, request, call, services)
