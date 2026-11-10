# SPDX-License-Identifier: Apache-2.0
"""系统宿主的真实启动、自检、失败门禁、HTTP 与停止回收。"""

import asyncio
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from unittest.mock import AsyncMock

import pytest

from ghrah.subject.host import app
from ghrah.subject.host.app import HostRuntime
from ghrah.subject.host.catalog import map_host_configuration, map_supervisor
from ghrah.subject.host.kernel_process import start_kernel
from ghrah.subject.host.state import SystemPhase
from ghrah.subject.host.supervisor import Supervisor


async def wait_boot(runtime):
    async with asyncio.timeout(12):
        while runtime.state.phase == SystemPhase.BOOTING:
            await asyncio.sleep(0.01)
    assert runtime.state.phase == SystemPhase.READY, runtime.error


async def test_boot_starts_real_namespace_and_shutdown_reaps_it(generation):
    runtime = HostRuntime(generation)
    task = asyncio.create_task(runtime.boot())
    try:
        await wait_boot(runtime)
        assert runtime.snapshot()["services"][0]["phase"] == "Running"
        assert runtime.bootstrap.process.returncode is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()
    assert runtime.state.phase == SystemPhase.STOPPED, runtime.error
    assert runtime.bootstrap.process.returncode == 0
    report = runtime.bootstrap.final_report
    assert report["errors"] == []
    assert report["stats"]["domains_live"] == 0
    assert len(report["domains"]) == 1 and all(item["reaped"] for item in report["domains"])


async def test_real_namespace_accepts_configuration_larger_than_windows_commandline(catalog):
    catalog["services"][0]["configuration"]["large"] = "x" * 40000
    host = await start_kernel(map_host_configuration(catalog, {}))
    specs, schemes, _ = map_supervisor(catalog, {})
    supervisor = Supervisor(host.control, specs, schemes)
    try:
        await supervisor.start()
        assert supervisor.view()[0]["phase"] == "Running"
        assert (await supervisor.shutdown())["clean"]
    finally:
        await host.aclose()
    assert host.final_report["stats"]["domains_live"] == 0


async def test_catalog_failure_never_spawns_kernel(generation, monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr(app, "start_kernel", spawn)
    (generation / "catalog.json").write_bytes(b"{}")
    runtime = HostRuntime(generation)
    await runtime.boot()
    await runtime.close()
    assert runtime.state.phase == SystemPhase.FAILED
    spawn.assert_not_called()


async def test_missing_catalog_fails_before_spawn(tmp_path, monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr(app, "start_kernel", spawn)
    runtime = HostRuntime(tmp_path)
    await runtime.boot()
    assert runtime.failed.is_set()
    spawn.assert_not_called()


async def test_service_failure_does_not_open_ready_gate(generation, monkeypatch):
    class FailedSupervisor:
        def __init__(self, *_):
            pass

        async def start(self, **_):
            raise RuntimeError("service failed")

        async def shutdown(self, **_):
            return {"clean": True}

        def view(self):
            return []

    monkeypatch.setattr(app, "Supervisor", FailedSupervisor)
    runtime = HostRuntime(generation)
    await runtime.boot()
    await runtime.close()
    assert runtime.state.phase == SystemPhase.FAILED
    assert runtime.bootstrap.process.returncode == 0


async def test_kernel_death_is_fatal_and_not_restarted(generation):
    runtime = HostRuntime(generation)
    task = asyncio.create_task(runtime.boot())
    try:
        await wait_boot(runtime)
        original = runtime.bootstrap.process
        # 关闭管理连接模拟内核退出；内核自身会先回收全部受管服务。
        await runtime.bootstrap.control.aclose()
        await asyncio.wait_for(original.wait(), 5)
        await asyncio.wait_for(runtime.failed.wait(), 5)
        assert runtime.bootstrap.process is original
        assert runtime.state.phase == SystemPhase.FAILED
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


@pytest.mark.parametrize("stop_method", ["control_file", "signal"])
def test_console_host_health_and_graceful_stop(generation, tmp_path, stop_method):
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    report = tmp_path / "boot-report.json"
    stop_file = tmp_path / "stop"
    env = {
        **os.environ,
        "GHRAH_SUBJECT_SERVER_HOST": "127.0.0.1",
        "GHRAH_SUBJECT_SERVER_PORT": str(port),
        "GHRAH_BOOT_REPORT_PATH": str(report),
        "GHRAH_HOST_STOP_FILE": str(stop_file),
    }
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
    with (tmp_path / "host.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "ghrah.subject.host.app"],
            cwd=generation,
            env=env,
            stdout=log,
            stderr=log,
            **options,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                assert process.poll() is None, (tmp_path / "host.log").read_text("utf-8")
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=1
                    ) as response:
                        health = json.load(response)
                    break
                except urllib.error.URLError, TimeoutError:
                    assert time.monotonic() < deadline, (tmp_path / "host.log").read_text("utf-8")
                    time.sleep(0.02)
            assert health["status"] == "Ready"
            assert health["boot_id"]
            assert health["services"][0]["phase"] == "Running"
            if stop_method == "control_file":
                stop_file.touch()
            else:
                process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
            assert process.wait(timeout=15) == 0, (tmp_path / "host.log").read_text("utf-8")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    result = json.loads(report.read_text("utf-8"))
    assert result["status"] == "Stopped"
    assert result["kernel"]["errors"] == []
    assert all(item["reaped"] for item in result["kernel"]["domains"])
    assert result["kernel"]["stats"]["domains_live"] == 0


def test_stop_during_booting_still_runs_lifespan_cleanup(generation, tmp_path, catalog):
    service = catalog["services"][0]
    service["module"] = "_slow_namespace"
    (tmp_path / "_slow_namespace.py").write_text(
        "import time\ntime.sleep(3)\n"
        "from ghrah_kernel.integration.entry import main\nraise SystemExit(main())\n",
        "utf-8",
    )
    raw = json.dumps(catalog).encode()
    (generation / "catalog.json").write_bytes(raw)
    (generation / "GENERATION.toml").write_text(
        '[generation]\ncatalog_sha256 = "' + hashlib.sha256(raw).hexdigest() + '"\n',
        "utf-8",
    )
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    report = tmp_path / "interrupted-boot.json"
    env = {
        **os.environ,
        "GHRAH_SUBJECT_SERVER_HOST": "127.0.0.1",
        "GHRAH_SUBJECT_SERVER_PORT": str(port),
        "GHRAH_BOOT_REPORT_PATH": str(report),
        "PYTHONPATH": str(tmp_path),
    }
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
    with (tmp_path / "boot.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "ghrah.subject.host.app"],
            cwd=generation,
            env=env,
            stdout=log,
            stderr=log,
            **options,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                except urllib.error.HTTPError as response:
                    assert response.code == 503
                    break
                except urllib.error.URLError, TimeoutError:
                    assert process.poll() is None
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                else:
                    raise AssertionError("慢启动服务不应提前 Ready")
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
            assert process.wait(timeout=15) == 0, (tmp_path / "boot.log").read_text()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    result = json.loads(report.read_text())
    assert result["status"] == "Stopped"
    if result["kernel"]:
        assert result["kernel"]["stats"]["domains_live"] == 0
        assert all(item["reaped"] for item in result["kernel"]["domains"])
