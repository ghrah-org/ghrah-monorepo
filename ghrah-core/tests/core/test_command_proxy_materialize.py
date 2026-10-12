# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""command_proxy 物化器测试（Core 侧强制闸门）。

覆盖：坏 descriptor 拒绝、非白名单强制 HITL 包装（含声明
``require_hitl=False`` 不具自批效力）、白名单放行裸实例、
standalone（bridge=None）注册不拒绝。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from ghrah.abilities.context import AbilityExecutionContext
from ghrah.abilities.hooks import HookPoint
from ghrah.core.subject_bridge import (
    COMMAND_BRIDGE_UNAVAILABLE_ERROR,
    SubjectCommandBridge,
)
from ghrah.core.unit import _materialize_command_proxy
from ghrah.types.results import ActionOutcome


def _descriptor(**overrides: Any) -> dict[str, Any]:
    base = {"tool_name": "foo", "command": "foo_run", "description": "Run foo"}
    base.update(overrides)
    return base


class _FakeSerial:
    async def __call__(self, name: str, payload: dict[str, Any]) -> Any:
        return {"success": True, "data": {}}


def _hitl_hooks(ability: Any) -> list[Any]:
    return [
        h for h in ability.get_hooks() if getattr(h, "hook_point", None) is HookPoint.PRE_EXECUTE
    ]


def test_bad_descriptor_rejected() -> None:
    """descriptor 非法（未知字段/缺必填）→ 物化拒绝。"""
    with pytest.raises(ValidationError, match="hooks"):
        _materialize_command_proxy(_descriptor(hooks=[]), None)
    with pytest.raises(ValidationError):
        _materialize_command_proxy({"tool_name": "foo"}, None)


def test_non_whitelisted_forced_hitl_wrap() -> None:
    """非白名单 → 强制 HITL 包装（PRE_EXECUTE 审批 hook 挂接）。"""
    ability = _materialize_command_proxy(
        _descriptor(),
        SubjectCommandBridge(_FakeSerial()),
        auto_approve_abilities=(),
    )
    assert ability.name == "foo"
    assert len(_hitl_hooks(ability)) == 1
    # bind_tool 穿透包装保持工具面投影
    schema = ability.bind_tool()
    assert schema["function"]["name"] == "foo"


def test_whitelisted_bare_instance() -> None:
    """白名单 → 裸实例（无 HITL 包装）。"""
    ability = _materialize_command_proxy(
        _descriptor(),
        SubjectCommandBridge(_FakeSerial()),
        auto_approve_abilities=("foo",),
    )
    assert ability.get_hooks() == []


def test_declared_require_hitl_false_does_not_self_approve() -> None:
    """descriptor 声明 require_hitl=False 不具自批效力（强制闸门）。"""
    ability = _materialize_command_proxy(
        _descriptor(require_hitl=False),
        SubjectCommandBridge(_FakeSerial()),
        auto_approve_abilities=(),
    )
    assert len(_hitl_hooks(ability)) == 1


async def test_standalone_bridge_none_materializes() -> None:
    """standalone（无 serial）：注册不拒绝，执行期明确报错。"""
    ability = _materialize_command_proxy(_descriptor(), None, auto_approve_abilities=("foo",))
    assert ability.name == "foo"

    result = await ability.execute(
        AbilityExecutionContext(current_ability_name="foo", tool_args={})
    )
    assert result.outcome is ActionOutcome.FAILURE
    assert result.data["error"] == COMMAND_BRIDGE_UNAVAILABLE_ERROR
