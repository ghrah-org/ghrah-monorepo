# SPDX-License-Identifier: Apache-2.0
"""内部 ``mcp_call``：成员驱动的 MCP 执行适配，不是模型可见工具。

页面安装的 MCP 成员把 ``server`` / ``tool`` / ``read_only`` 冻结在自己的 data 里；
本函数只负责把一次已接纳的调用交给当前 Agent/run 的 MCP 服务，并把失败翻译成
模型可读的 ActionResult。它不接收连接参数，也不做服务器发现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ghrah.ability.execution.invocation import OperationCall
from ghrah.mcp.errors import (
    McpCredentialMissingError,
    McpError,
    McpNotActivatedError,
    McpRequestError,
)
from ghrah.mcp.spec import McpToolSpec

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.types.results import ActionResult

__all__ = ["mcp_call"]


async def mcp_call(
    call: OperationCall,
    services: ExecutionServices,
    *,
    server: str = "",
    tool: str = "",
    read_only: bool = False,
    description: str = "",
    input_schema: dict[str, Any] | None = None,
    timeout_ms: int | None = None,
) -> ActionResult:
    """按成员冻结的 server/tool 调用；失败不向模型回灌配置源或传输异常原文。"""
    from ghrah.ability.execution.execution_services import MCP_RUNTIME
    from ghrah.types.results import ActionOutcome, ActionResult

    service = services.get(MCP_RUNTIME)
    if service is None or not hasattr(service, "call_member"):
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "MCP service not wired"})
    if not server or not tool:
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={"error": "MCP member is missing server/tool data"},
        )
    try:
        tool_spec = McpToolSpec(
            name=tool,
            description=description or tool,
            input_schema=input_schema or {"type": "object"},
            read_only=read_only,
        )
        result = await service.call_member(
            server=server,
            tool=tool_spec,
            arguments=dict(call.arguments),
            timeout_ms=timeout_ms,
        )
    except McpCredentialMissingError:
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "MCP credential missing"})
    except McpRequestError as error:
        data = {"error": "MCP request failed", "uncertain": error.uncertain}
        if error.uncertain:
            data["note"] = "Verify external state before retrying; this write may have taken effect"
        return ActionResult(outcome=ActionOutcome.FAILURE, data=data)
    except McpNotActivatedError:
        return ActionResult(
            outcome=ActionOutcome.FAILURE, data={"error": "MCP member not activated"}
        )
    except McpError:
        return ActionResult(
            outcome=ActionOutcome.FAILURE, data={"error": "MCP configuration failed"}
        )
    except Exception:
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "MCP transport failed"})
    return ActionResult(
        outcome=ActionOutcome.FAILURE if result.is_error else ActionOutcome.SUCCESS,
        data={"content": result.render_for_model()},
    )
