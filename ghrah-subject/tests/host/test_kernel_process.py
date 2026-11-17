# SPDX-License-Identifier: Apache-2.0
"""真实内核启动、启动前拒绝与握手失败回收；Windows 不跳过。"""

import copy
from unittest.mock import AsyncMock

import pytest

from ghrah.subject.host import kernel_process
from ghrah.subject.host.catalog import map_host_configuration
from ghrah.subject.host.kernel_process import HostBootError, start_kernel


async def test_real_kernel_handshake_and_clean_exit(catalog):
    host = await start_kernel(map_host_configuration(catalog, {}))
    try:
        assert host.control.session["boot_id"]
        assert host.control.session["sys_handle"]
        assert host.control.session["kernel_pid"] == host.process.pid
    finally:
        await host.aclose()
    await host.aclose()
    assert host.process.returncode == 0
    assert host.final_report["errors"] == []
    assert host.final_report["stats"]["domains_live"] == 0


@pytest.mark.parametrize("mismatch", [False, True])
async def test_bad_configuration_is_rejected_without_spawning(catalog, monkeypatch, mismatch):
    data = map_host_configuration(catalog, {})
    if mismatch:
        import json

        identity = copy.deepcopy(data["identities"])
        identity["subjects"][0]["subject_id"] = "other"
        data["launches"][0]["configuration"] = json.dumps({"identities": identity})
    else:
        del data["callers"]
    spawn = AsyncMock()
    monkeypatch.setattr(kernel_process, "start_process", spawn)
    with pytest.raises(HostBootError, match="预检"):
        await start_kernel(data)
    spawn.assert_not_called()


async def test_failed_handshake_reaps_kernel(catalog, monkeypatch):
    original = kernel_process.start_process
    processes = []

    async def wrong_token(*command, **kwargs):
        command = list(command)
        command[command.index("--control-token") + 1] = "wrong"
        process = await original(*command, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(kernel_process, "start_process", wrong_token)
    with pytest.raises(HostBootError):
        await start_kernel(map_host_configuration(catalog, {}))
    assert len(processes) == 1 and processes[0].returncode is not None
