# SPDX-License-Identifier: Apache-2.0
"""系统阶段不能由健康端点或另一个 Init 以外的写者更新。"""

import pytest

from ghrah.subject.host.state import SystemPhase, SystemState


def test_only_init_can_advance_lifecycle():
    writer = object()
    state = SystemState(writer)
    assert state.snapshot() == {"status": "Booting"}
    with pytest.raises(PermissionError):
        state.transition(SystemPhase.READY, writer=object())
    with pytest.raises(ValueError):
        state.transition(SystemPhase.STOPPED, writer=writer)
    for phase in (SystemPhase.READY, SystemPhase.STOPPING, SystemPhase.STOPPED):
        state.transition(phase, writer=writer)
    with pytest.raises(ValueError):
        state.transition(SystemPhase.READY, writer=writer)


def test_failed_boot_is_terminal():
    writer = object()
    state = SystemState(writer)
    state.transition(SystemPhase.FAILED, writer=writer)
    with pytest.raises(ValueError):
        state.transition(SystemPhase.READY, writer=writer)
