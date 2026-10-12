# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SubjectCommandBridge 测试：派发归一化与「报错不炸」语义。

覆盖：成功回执透传、``command/<name>`` 路由名与 payload 透传、
serial 异常转错误回执、非 dict 回执转错误回执。
"""

from __future__ import annotations

from typing import Any

from ghrah.core.subject_bridge import SubjectCommandBridge


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
            import asyncio

            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result


async def test_dispatch_success_passthrough() -> None:
    serial = _FakeSerial(result={"success": True, "data": {"echo": "ok"}})
    bridge = SubjectCommandBridge(serial)

    result = await bridge.dispatch("foo_run", {"payload": 1})

    assert result == {"success": True, "data": {"echo": "ok"}}
    assert serial.calls == [("command/foo_run", {"payload": 1})]


async def test_dispatch_failure_receipt_passthrough() -> None:
    serial = _FakeSerial(result={"success": False, "error": "Unknown command: foo_run"})
    bridge = SubjectCommandBridge(serial)

    result = await bridge.dispatch("foo_run", {})

    assert result == {"success": False, "error": "Unknown command: foo_run"}


async def test_dispatch_serial_exception_becomes_error_receipt() -> None:
    serial = _FakeSerial(error=RuntimeError("route not found"))
    bridge = SubjectCommandBridge(serial)

    result = await bridge.dispatch("foo_run", {})

    assert result["success"] is False
    assert "route not found" in result["error"]


async def test_dispatch_non_dict_result_becomes_error_receipt() -> None:
    serial = _FakeSerial(result="not a dict")
    bridge = SubjectCommandBridge(serial)

    result = await bridge.dispatch("foo_run", {})

    assert result["success"] is False
    assert "Unexpected result" in result["error"]


async def test_dispatch_delay_visible_to_caller() -> None:
    """bridge 不吞耗时——调用方可基于此测 timeout 行为。"""
    serial = _FakeSerial(result={"success": True}, delay=0.05)
    bridge = SubjectCommandBridge(serial)

    result = await bridge.dispatch("slow", {})

    assert result["success"] is True
