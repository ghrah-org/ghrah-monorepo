"""恢复方式词表映射：supervisor 的 memory/initialized/restored → wire 的 created/resumed。"""

from __future__ import annotations

from ghrah.core.unit import _wire_recovery_mode


def test_supervisor_modes_map_to_wire_vocabulary() -> None:
    """wire 契约只认 created/resumed，运行时另有 memory/initialized/restored。"""
    assert _wire_recovery_mode("initialized") == "created"
    assert _wire_recovery_mode("memory") == "created"
    assert _wire_recovery_mode("restored") == "resumed"
    assert _wire_recovery_mode("created") == "created"
    assert _wire_recovery_mode("resumed") == "resumed"


def test_unknown_mode_degrades_to_created() -> None:
    """未知值不能让 AgentStartView 校验失败——按"新建"返回。"""
    assert _wire_recovery_mode("something-new") == "created"
