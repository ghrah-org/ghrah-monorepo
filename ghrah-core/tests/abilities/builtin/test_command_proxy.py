# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""CommandProxyAbility / CommandProxyDescriptor 测试。

覆盖：descriptor 封闭校验（extra="forbid"）、bind_tool schema 直出、
execute 经 bridge 派发（成功/失败/超时/缺席）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from ghrah.abilities.builtin.command_proxy import (
    CommandProxyAbility,
    CommandProxyDescriptor,
)
from ghrah.abilities.context import AbilityExecutionContext
from ghrah.core.subject_bridge import (
    COMMAND_BRIDGE_UNAVAILABLE_ERROR,
    SubjectCommandBridge,
)
from ghrah.types.results import ActionOutcome


def _descriptor(**overrides: Any) -> dict[str, Any]:
    """最小合法 descriptor dict。"""
    base = {"tool_name": "foo", "command": "foo_run", "description": "Run foo"}
    base.update(overrides)
    return base


class _FakeSerial:
    """记录调用并可注入行为的 fake serial。"""

    def __init__(
        self,
        *,
        result: Any = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._result = result
        self._error = error
        self._delay = delay

    async def __call__(self, name: str, payload: dict[str, Any]) -> Any:
        self.calls.append((name, payload))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result


def _ctx(tool_args: dict[str, Any] | None = None) -> AbilityExecutionContext:
    return AbilityExecutionContext(current_ability_name="foo", tool_args=tool_args or {})


class TestCommandProxyDescriptor:
    """descriptor 封闭校验"""

    def test_valid_descriptor_defaults(self) -> None:
        d = CommandProxyDescriptor.model_validate(_descriptor())
        assert d.tool_name == "foo"
        assert d.command == "foo_run"
        assert d.require_hitl is True
        assert d.parameters is None
        assert d.timeout is None

    def test_extra_fields_rejected(self) -> None:
        """未知字段拒绝——hooks 等注入字段无从混入。"""
        with pytest.raises(ValidationError, match="hooks"):
            CommandProxyDescriptor.model_validate(_descriptor(hooks=["evil"]))

    def test_hooks_key_explicitly_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandProxyDescriptor.model_validate(_descriptor(permission_checker=object()))

    def test_empty_tool_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandProxyDescriptor.model_validate(_descriptor(tool_name=""))

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandProxyDescriptor.model_validate(_descriptor(command=""))

    def test_non_positive_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandProxyDescriptor.model_validate(_descriptor(timeout=0))

    def test_declared_require_hitl_false_is_tolerated(self) -> None:
        """require_hitl=False 声明可校验通过（自批效力由 Core 闸门裁决，
        不在 descriptor 层拒绝）。"""
        d = CommandProxyDescriptor.model_validate(_descriptor(require_hitl=False))
        assert d.require_hitl is False


class TestCommandProxyAbilityBindTool:
    """bind_tool：descriptor 参数直出 OpenAI function schema。"""

    def test_schema_from_params(self) -> None:
        parameters = {"type": "object", "properties": {"x": {"type": "integer"}}}
        ability = CommandProxyAbility(
            tool_name="foo",
            description="Run foo",
            parameters=parameters,
            command="foo_run",
        )
        schema = ability.bind_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "foo"
        assert schema["function"]["description"] == "Run foo"
        assert schema["function"]["parameters"] == parameters

    def test_default_empty_object_schema(self) -> None:
        ability = CommandProxyAbility(tool_name="foo", command="foo_run")
        schema = ability.bind_tool()
        assert schema["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_name_is_tool_name(self) -> None:
        assert CommandProxyAbility(tool_name="foo", command="foo_run").name == "foo"

    def test_no_hooks(self) -> None:
        assert CommandProxyAbility(tool_name="foo", command="foo_run").get_hooks() == []


class TestCommandProxyAbilityExecute:
    """execute：经 bridge 派发宿主协议命令。"""

    async def test_execute_success(self) -> None:
        serial = _FakeSerial(result={"success": True, "data": {"echo": "ok"}})
        ability = CommandProxyAbility(
            tool_name="foo", bridge=SubjectCommandBridge(serial), command="foo_run"
        )

        result = await ability.execute(_ctx({"payload": 1}))

        assert result.outcome is ActionOutcome.SUCCESS
        assert result.data == {"echo": "ok"}
        assert serial.calls == [("command/foo_run", {"payload": 1})]

    async def test_execute_failure_carries_error(self) -> None:
        serial = _FakeSerial(result={"success": False, "error": "handler crashed"})
        ability = CommandProxyAbility(
            tool_name="foo", bridge=SubjectCommandBridge(serial), command="foo_run"
        )

        result = await ability.execute(_ctx())

        assert result.outcome is ActionOutcome.FAILURE
        assert result.data["error"] == "handler crashed"

    async def test_execute_failure_without_error_message(self) -> None:
        serial = _FakeSerial(result={"success": False})
        ability = CommandProxyAbility(
            tool_name="foo", bridge=SubjectCommandBridge(serial), command="foo_run"
        )

        result = await ability.execute(_ctx())

        assert result.outcome is ActionOutcome.FAILURE
        assert "foo_run" in result.data["error"]

    async def test_execute_bridge_absent_clear_error(self) -> None:
        """bridge 缺席 → 明确错误（对齐 ROOM_BRIDGE_UNAVAILABLE_ERROR 形状）。"""
        ability = CommandProxyAbility(tool_name="foo", command="foo_run")

        result = await ability.execute(_ctx())

        assert result.outcome is ActionOutcome.FAILURE
        assert result.data["error"] == COMMAND_BRIDGE_UNAVAILABLE_ERROR

    async def test_execute_timeout(self) -> None:
        serial = _FakeSerial(result={"success": True}, delay=0.2)
        ability = CommandProxyAbility(
            tool_name="foo",
            bridge=SubjectCommandBridge(serial),
            command="foo_run",
            timeout=0.01,
        )

        result = await ability.execute(_ctx())

        assert result.outcome is ActionOutcome.FAILURE
        assert "timed out" in result.data["error"]
