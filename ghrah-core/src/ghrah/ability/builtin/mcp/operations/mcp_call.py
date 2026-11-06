# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""McpCallAbility：调用 MCP 服务器上的一个工具。

**面向模型的 MCP 入口只有这一个。** 工具不是被注册成独立函数，而是作为本能力的
``(server, tool, arguments)`` 参数传入。这样带来三件事：工具列表恒定不变（技能加
载不会改动它，因而不会打断提示词前缀缓存）；无需把服务器上的任意工具名改造成合法
函数名（截断、加哈希、反解）；协议的 ``tools/call`` 与本能力的形状天然一致。

本能力**只执行，不负责发现**。工具目录与参数 schema 由 ``load_skill`` 随技能正文
一起提供——那是本地配置，读取时零网络。因此本能力运行前，调用方应当已经加载过声明
该服务器的技能；没加载过会得到明确的授权失败，而不是"服务器不存在"。

接线语义（零隐式）：运行时经装配期注入（``MCP_RUNTIME`` 执行服务）；
未接线时明确 FAILURE 说明缺席，**不**静默降级。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ghrah.ability.execution.base import Ability
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.hooks import Hook
    from ghrah.ability.execution.invocation import OperationCall

__all__ = ["McpCallAbility", "McpCallInput"]

# --- 成员声明（build 生成 members 的唯一来源；改完须重新 build）
PERMISSIONS = {
    # 调用外部 MCP 服务器：网络访问是它的实质，必须在权限面上显式声明。
    "net_access": True,
}
HOOKS: dict[str, list[dict[str, object]]] = {}
TITLE = "MCP Call"
SUMMARY = (
    "Call a tool on an MCP server. The tools available to you are listed in the MCP "
    "catalog of the skill you loaded; 'server' and 'tool' are case-sensitive."
)
TAGS = [
    "mcp",
    "tool",
]
EFFECTS = ["net:request"]
TIMEOUT_MS = 300_000
# --- 成员声明结束

logger = logging.getLogger(__name__)


class McpCallInput(BaseModel):
    """mcp_call 工具入参契约（extra=forbid）。"""

    model_config = ConfigDict(extra="forbid")

    server: str = Field(
        description=(
            "MCP server name exactly as listed in the skill's MCP catalog. "
            "Case-sensitive; copy it verbatim."
        ),
    )
    tool: str = Field(
        description=(
            "Tool name exactly as listed in the skill's MCP catalog. "
            "Case-sensitive; copy it verbatim."
        ),
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Tool arguments matching the tool's input schema. Call load_skill with "
            "server= and tool= to read the exact schema before guessing."
        ),
    )
    skill: str = Field(
        default="",
        description=(
            "Optional. The skill full name that declares this server. Only needed "
            "when several loaded skills could own the same server name."
        ),
    )


Input = McpCallInput  # v1 生成器入口别名（生成器只读 module.Input）


class McpCallAbility(Ability):
    """调用 MCP 服务器上的工具（不可缓存、不可重放写操作）。"""

    @property
    def name(self) -> str:
        """工具名（模型看到的名字，与定义面成员键一致）。

        Returns:
            固定为 ``"mcp_call"``。
        """
        return "mcp_call"

    def bind_tool(self) -> dict[str, Any]:
        """产出注入模型工具面的 schema。

        ``arguments`` 刻意是开放对象：不让本地 schema 成为第二套参数校验，
        参数合法性以 MCP 服务器为准，错误原样回灌给模型。

        Returns:
            OpenAI 工具格式的字典（``type`` / ``function``）。
        """
        return {
            "type": "function",
            "function": {
                "name": "mcp_call",
                "description": (
                    "Call a tool on an MCP server. The tools available to you are "
                    "listed in the MCP catalog of the skill you loaded. Both 'server' "
                    "and 'tool' are case-sensitive and must be copied verbatim. If a "
                    "tool's arguments are unclear, call load_skill again with server= "
                    "and tool= to read its input schema."
                ),
                # arguments 刻意是开放对象：不让本地 schema 成为第二套参数校验，
                # 参数合法性以服务器为准，错误原样回灌给模型。
                "parameters": McpCallInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        """给提示词用的一行描述（非工具面路径下的简版说明）。

        Returns:
            形如 ``mcp_call(server: str, tool: str, arguments: object) -> dict`` 的字符串。
        """
        return (
            "mcp_call(server: str, tool: str, arguments: object) -> dict: "
            "invoke a tool on an MCP server declared by a loaded skill"
        )

    def get_hooks(self) -> list[Hook]:
        """本能力自带的 Hook。

        MCP 调用的授权由技能与运行时协作裁决，本能力不额外挂钩子；
        需要人工闸门时由装配侧按权限声明包装（``HITLRequiredAbility``）。

        Returns:
            恒为空列表。
        """
        return []

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        """退役入口明确失败；MCP 执行由受信配置固定到各自成员。"""
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={"error": "mcp_call is retired; use a granted MCP member through tool discovery"},
        )
