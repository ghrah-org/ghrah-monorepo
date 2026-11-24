# SPDX-License-Identifier: Apache-2.0
"""元工具的共用形状；执行依赖显式注入的当前 Agent 工具面服务。"""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError

from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import TOOL_SURFACE, ExecutionServices
from ghrah.ability.execution.invocation import OperationCall
from ghrah.types.results import ActionOutcome, ActionResult


class SearchInput(BaseModel):
    """无参数：扫描当前 Agent 实例的可见未绑定成员。"""

    model_config = ConfigDict(extra="forbid")


class DescribeInput(BaseModel):
    """只按绑定工具名描述，不接受成员路径。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, description="Exact tool name returned by search_tools.")


class ExecuteInput(DescribeInput):
    """目标参数仍由目标成员自己的输入契约校验。"""

    arguments: dict[str, Any] = Field(description="Arguments matching the target input schema.")


class BindInput(BaseModel):
    """按工具名或技能目录名绑定/解绑成员；成员必须已被授予且可见。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        min_length=1,
        description=(
            "Exact tool name returned by search_tools, or a skill catalog name "
            "such as `alias/skill` from the skill catalog."
        ),
    )


class BindOutput(BaseModel):
    """绑定操作的回执：目标工具名与当前绑定成员列表。"""

    model_config = ConfigDict(extra="forbid")
    name: str
    bound: list[str]


class SearchItem(BaseModel):
    """可返回给模型的一行发现结果（当前可见但未绑定）。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        description="Exact tool name for describe_tool, execute_tool, or bind_member."
    )
    member: str = Field(description="Member full name recorded in the Agent instance snapshot.")
    package: str = Field(description="Package alias of the member.")
    title: str = Field(description="Member title.")
    description: str = Field(description="Member description.")
    available: bool = Field(description="Whether the member backend is currently available.")


class SearchOutput(BaseModel):
    """扫描结果；空结果也给出原因，便于模型调整下一步。"""

    model_config = ConfigDict(extra="forbid")
    status: str = Field(description="ok, empty, unavailable, not_found, or error.")
    data: list[SearchItem] = Field(description="Visible but unbound granted tools.")
    reason: str = Field(description="Why data is empty, when it is.")


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
