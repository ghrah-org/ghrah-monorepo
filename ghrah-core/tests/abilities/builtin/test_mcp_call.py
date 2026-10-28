# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""mcp_call 能力：入参契约、未接线、失败映射、is_error 转真失败。"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.mcp.errors import (
    FailureKind,
    McpCredentialMissingError,
    McpNotActivatedError,
    McpRequestError,
    McpToolMissingError,
)
from ghrah.mcp.result import ToolOutcome
from ghrah.scheme.ability.mcp.operations.mcp_call import McpCallAbility, McpCallInput
from ghrah.scheme.execution.context import AbilityExecutionContext
from ghrah.types.results import ActionOutcome


class StubRuntime:
    """可控的运行时替身：记录调用并返回预设结果/抛出预设异常。"""

    def __init__(
        self,
        *,
        outcome: ToolOutcome | None = None,
        error: Exception | None = None,
    ) -> None:
        self.outcome = outcome or ToolOutcome(text="ok", is_error=False)
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> ToolOutcome:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.outcome


def make_context(*, tool_args: dict[str, Any], runtime: Any = None) -> AbilityExecutionContext:
    return AbilityExecutionContext(
        current_ability_name="mcp_call",
        tool_args=tool_args,
        agent_name="a1",
        mcp_runtime=runtime,
    )


class TestContract:
    def test_name_and_schema(self) -> None:
        ability = McpCallAbility()
        assert ability.name == "mcp_call"
        func = ability.bind_tool()["function"]
        assert func["name"] == "mcp_call"
        assert set(func["parameters"]["required"]) == {"server", "tool"}

    def test_arguments_is_an_open_object(self) -> None:
        # 不让本地 schema 成为第二套参数校验：合法性以服务器为准。
        func = McpCallAbility().bind_tool()["function"]
        arguments = func["parameters"]["properties"]["arguments"]
        assert arguments["type"] == "object"
        assert "properties" not in arguments

    def test_input_forbids_extra_keys(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            McpCallInput.model_validate({"server": "s", "tool": "t", "bogus": 1})

    def test_no_hooks(self) -> None:
        assert McpCallAbility().get_hooks() == []


class TestWiring:
    async def test_unwired_runtime_is_explicit_failure(self) -> None:
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "s", "tool": "t"})
        )
        assert result.outcome is ActionOutcome.FAILURE
        assert "not wired" in result.data["error"]

    async def test_missing_arguments_are_reported(self) -> None:
        result = await McpCallAbility().execute(make_context(tool_args={"server": "s"}))
        assert result.outcome is ActionOutcome.FAILURE
        assert "required" in result.data["error"]


class TestDispatch:
    async def test_forwards_scope_and_arguments(self) -> None:
        runtime = StubRuntime()
        result = await McpCallAbility().execute(
            make_context(
                tool_args={"server": "sentry", "tool": "list_issues", "arguments": {"q": "x"}},
                runtime=runtime,
            )
        )
        assert result.outcome is ActionOutcome.SUCCESS
        assert runtime.calls[0]["agent_scope"] == "a1"
        assert runtime.calls[0]["server"] == "sentry"
        assert runtime.calls[0]["tool"] == "list_issues"
        assert runtime.calls[0]["arguments"] == {"q": "x"}

    async def test_optional_skill_is_passed_through(self) -> None:
        runtime = StubRuntime()
        await McpCallAbility().execute(
            make_context(
                tool_args={"server": "s", "tool": "t", "skill": "sentry-ops"},
                runtime=runtime,
            )
        )
        assert runtime.calls[0]["skill"] == "sentry-ops"

    async def test_non_dict_arguments_become_empty(self) -> None:
        runtime = StubRuntime()
        await McpCallAbility().execute(
            make_context(
                tool_args={"server": "s", "tool": "t", "arguments": "oops"}, runtime=runtime
            )
        )
        assert runtime.calls[0]["arguments"] == {}

    async def test_success_returns_rendered_content(self) -> None:
        runtime = StubRuntime(outcome=ToolOutcome(text="hello", is_error=False))
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "s", "tool": "t"}, runtime=runtime)
        )
        assert result.outcome is ActionOutcome.SUCCESS
        assert result.data["content"] == "hello"


class TestToolLevelFailure:
    async def test_is_error_becomes_real_failure(self) -> None:
        # 包装成「成功但带 error 字段」会让模型以为调用成功，继续沿着错误前提推理。
        runtime = StubRuntime(outcome=ToolOutcome(text="boom", is_error=True))
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "s", "tool": "t"}, runtime=runtime)
        )
        assert result.outcome is ActionOutcome.FAILURE
        assert "boom" in result.data["error"]

    async def test_diagnostics_survive(self) -> None:
        runtime = StubRuntime(
            outcome=ToolOutcome(
                text="body", is_error=False, diagnostics=("[image unavailable: image/png]",)
            )
        )
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "s", "tool": "t"}, runtime=runtime)
        )
        assert "image unavailable" in result.data["content"]


class TestFailureMapping:
    async def test_not_activated_is_actionable(self) -> None:
        runtime = StubRuntime(error=McpNotActivatedError("server 'x' is not activated"))
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "x", "tool": "t"}, runtime=runtime)
        )
        assert result.outcome is ActionOutcome.FAILURE
        assert "Load the skill" in result.data["note"]

    async def test_tool_missing_echoes_candidates(self) -> None:
        runtime = StubRuntime(
            error=McpToolMissingError("sentry", "list-issues", ("list_issues", "get_issue"))
        )
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "sentry", "tool": "list-issues"}, runtime=runtime)
        )
        assert result.outcome is ActionOutcome.FAILURE
        assert "list_issues, get_issue" in result.data["error"]
        assert "Did you mean" in result.data["error"]

    async def test_credential_missing_message(self) -> None:
        runtime = StubRuntime(error=McpCredentialMissingError("sentry", "https://x/mcp"))
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "sentry", "tool": "t"}, runtime=runtime)
        )
        assert result.outcome is ActionOutcome.FAILURE
        # 凭据登记在 agentconf，文案要指向那里。
        assert "agentconf" in result.data["error"]

    async def test_uncertain_write_tells_model_not_to_retry(self) -> None:
        runtime = StubRuntime(
            error=McpRequestError(
                "session terminated mid-call",
                kind=FailureKind.SESSION_TERMINATED,
                uncertain=True,
            )
        )
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "s", "tool": "t"}, runtime=runtime)
        )
        assert result.outcome is ActionOutcome.FAILURE
        assert "Do not retry" in result.data["note"]

    async def test_non_uncertain_error_has_no_retry_note(self) -> None:
        runtime = StubRuntime(error=McpRequestError("boom", kind=FailureKind.PERMANENT))
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "s", "tool": "t"}, runtime=runtime)
        )
        assert "note" not in result.data

    async def test_unexpected_exception_does_not_escape(self) -> None:
        runtime = StubRuntime(error=RuntimeError("surprise"))
        result = await McpCallAbility().execute(
            make_context(tool_args={"server": "s", "tool": "t"}, runtime=runtime)
        )
        assert result.outcome is ActionOutcome.FAILURE
        assert "Unexpected MCP failure" in result.data["error"]
