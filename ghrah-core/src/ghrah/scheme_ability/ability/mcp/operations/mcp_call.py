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

接线语义（零隐式）：运行时经装配期注入（``AbilityExecutionContext.mcp_runtime``）；
未接线时明确 FAILURE 说明缺席，**不**静默降级。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.mcp.errors import (
    McpCredentialMissingError,
    McpError,
    McpNotActivatedError,
    McpRequestError,
    McpToolMissingError,
)
from ghrah.mcp.identity import format_unknown_tool_message
from ghrah.scheme_ability.execution.base import Ability
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme_ability.execution.context import AbilityExecutionContext
    from ghrah.scheme_ability.execution.hooks import Hook

__all__ = ["McpCallAbility", "McpCallInput"]

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
PERMISSIONS = {
    # 调用外部 MCP 服务器：网络访问是它的实质，必须在权限面上显式声明。
    "net_access": True,
}
HOOKS: dict[str, list[dict[str, object]]] = {}
SUMMARY = (
    "Call a tool on an MCP server. The tools available to you are listed in the MCP "
    "catalog of the skill you loaded; 'server' and 'tool' are case-sensitive."
)
TAGS = [
    "mcp",
    "tool",
]
# --- catalogue 声明结束

logger = logging.getLogger(__name__)


class McpCallInput(BaseModel):
    """mcp_call 工具入参契约（extra=forbid）。"""

    model_config = {"extra": "forbid"}

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

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        """执行一次 MCP 工具调用。

        失败一律转成 FAILURE 结果而非抛异常：工具边界不泄漏异常给模型循环。
        服务器声明的工具级失败也报 FAILURE——包装成「成功但带 error 字段」会让
        模型以为调用成功并沿错误前提继续推理。

        Args:
            context: 执行上下文；``tool_args`` 取 ``server`` / ``tool`` /
                ``arguments`` / ``skill``，``mcp_runtime`` 是装配期注入的运行时。

        Returns:
            SUCCESS（``content`` / ``server`` / ``tool``）或 FAILURE
            （``error`` 及必要的 ``note``）。
        """
        tool_args = context.tool_args or {}
        server = str(tool_args.get("server", "") or "").strip()
        tool = str(tool_args.get("tool", "") or "").strip()
        skill = str(tool_args.get("skill", "") or "").strip()

        if not server or not tool:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": "both 'server' and 'tool' are required",
                    "note": "Read the skill's MCP catalog for the exact names.",
                },
            )

        runtime = context.mcp_runtime
        if runtime is None:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": (
                        "McpRuntime not wired — MCP calling is unavailable in this "
                        "deployment (assembly-time injection, not implicit)"
                    )
                },
            )

        raw_arguments = tool_args.get("arguments")
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}

        try:
            outcome = await runtime.call(
                agent_scope=context.agent_name,
                server=server,
                tool=tool,
                arguments=arguments,
                skill=skill or None,
            )
        except McpNotActivatedError as exc:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": str(exc),
                    "note": (
                        "Load the skill that declares this server first, so its MCP "
                        "catalog becomes available."
                    ),
                },
            )
        except McpToolMissingError as exc:
            # 名字写错是最常见的失败，回灌候选让模型自我纠正而不是盲试。
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": format_unknown_tool_message(exc.server, exc.tool, exc.available)},
            )
        except McpCredentialMissingError as exc:
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": str(exc)})
        except McpRequestError as exc:
            payload: dict[str, Any] = {"error": str(exc)}
            if exc.uncertain:
                # 写操作可能已在服务器生效：必须明确要求先核验，不能重试。
                payload["note"] = (
                    "This call may or may not have taken effect on the server. "
                    "Do not retry it blindly — verify the external state first."
                )
            return ActionResult(outcome=ActionOutcome.FAILURE, data=payload)
        except McpError as exc:
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 兜底：绝不让异常逃出工具边界
            logger.warning("mcp_call failed for %s.%s", server, tool, exc_info=True)
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Unexpected MCP failure: {exc}"},
            )

        rendered = outcome.render_for_model()
        if outcome.is_error:
            # 服务器声明的工具级失败必须变成真失败：包装成「成功但带 error 字段」
            # 会让模型以为调用成功，继续沿着错误前提推理。
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": rendered, "server": server, "tool": tool},
            )

        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={
                "content": rendered,
                "server": server,
                "tool": tool,
            },
        )
