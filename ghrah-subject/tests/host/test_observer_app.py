# SPDX-License-Identifier: Apache-2.0
"""宿主探针、WS 门禁与旧装配栈导入边界。"""

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ghrah.subject.host.app import HostRuntime
from ghrah.subject.host.observer import create_observer_app
from ghrah.subject.host.state import SystemPhase
from ghrah.subject.server.config import ObserverServerConfig


@pytest.mark.parametrize(
    "phase,expected",
    [
        (SystemPhase.BOOTING, 503),
        (SystemPhase.READY, 200),
        (SystemPhase.STOPPING, 503),
        (SystemPhase.FAILED, 503),
    ],
)
def test_health_gates_only_on_init_phase(tmp_path, phase, expected):
    runtime = HostRuntime(tmp_path)
    if phase != SystemPhase.BOOTING:
        runtime.state.transition(phase, writer=runtime._writer)
    app = create_observer_app(runtime, ObserverServerConfig())
    with TestClient(app) as client:
        assert client.get("/health").status_code == expected
        assert client.get("/").json()["name"] == "ghrah-subject-host"
        if phase != SystemPhase.READY:
            with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws"):
                pass


def test_host_import_never_loads_old_units_or_assembly():
    code = """
import sys
import ghrah.subject.host.app
assert not any(m == 'ouroboros' or m.startswith('ouroboros.') for m in sys.modules)
assert 'ghrah.subject.runtime.assembly' not in sys.modules
assert not any(m.startswith('ghrah.subject.units') for m in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
