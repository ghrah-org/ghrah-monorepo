# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""优雅停机回归：SIGTERM 必须走到 Context 退出与 unit.stop。

背景（2026-11-07 生产事故）：``uvicorn`` 0.5x 的 ``Server.capture_signals`` 会在
优雅停机结束后恢复原信号处理器并**重放**捕获到的 SIGTERM/SIGINT。进程启动时的
原处理器是默认处置，重放会直接把进程杀掉，于是 ``serve()`` 里
``async with Context()`` 的 ``__aexit__`` 永不执行：
``unit.stop → Supervisor.shutdown → terminate_agent → release_registration``
全部被跳过，``ability_registrations`` 的 ``state='active'`` 占位永久残留；下次
启动 reconcile 重 spawn 同一稳定 ``agent_id`` 时抛 ``AccessError("conflict")``。

本文件用**真子进程 + 真信号**回归（进程内直调 ``ctx.dispose()`` 测不到 uvicorn
的信号重放）：启动 Subject、等健康检查通过、对进程组发一次 SIGTERM，断言
① 进程仍以 SIGTERM 语义退出（退出码 -15，不破坏下游按退出码判断）；
② 停机日志出现收尾链痕迹；③ 不是"只打了 uvicorn 三行就死"。
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ghrah.subject.server import app as app_module

_BOOT_TIMEOUT_SECONDS = 60.0
_SHUTDOWN_TIMEOUT_SECONDS = 30.0


def _free_port() -> int:
    """取一个当前空闲的 loopback 端口（绑定后立即释放，测试间串行使用）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_env(tmp_path: Path, port: int) -> dict[str, str]:
    """构造与生产同构的子进程环境：数据面全部落在 tmp_path。"""
    data = tmp_path / "data"
    for sub in ("manifests", "projects", "workspace"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env.update(
        {
            "GHRAH_SUBJECT_DB_PATH": str(data / "subject.db"),
            "GHRAH_SUBJECT_TASKSTORE_DB": str(data / "taskstore.sqlite3"),
            "GHRAH_SUBJECT_MANIFEST_ROOT": str(data / "manifests"),
            "GHRAH_SUBJECT_PROJECT_DEFAULT_ROOT_LOCATOR_TEMPLATE": str(
                data / "projects" / "{project_id}"
            ),
            "GHRAH_SUBJECT_PROJECT_BOOTSTRAP_WORKSPACE_LOCATOR": str(data / "workspace"),
            "GHRAH_SUBJECT_WORKSPACE_ROOT": str(data / "workspace"),
            "GHRAH_SUBJECT_ABILITY_SOURCE": "builtin",
            "GHRAH_SUBJECT_ABILITY_INSTALL_ROOT": str(tmp_path / "ability"),
            "GHRAH_SUBJECT_SERVER_HOST": "127.0.0.1",
            "GHRAH_SUBJECT_SERVER_PORT": str(port),
        }
    )
    return env


def _wait_healthy(proc: subprocess.Popen[bytes], port: int, log_path: Path) -> None:
    """轮询 /health 直到就绪；子进程提前退出则带日志失败。"""
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + _BOOT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                "Subject 子进程在就绪前退出（退出码 "
                f"{proc.returncode}）：\n{log_path.read_text(errors='replace')}"
            )
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except urllib.error.URLError, ConnectionError, TimeoutError:
            pass
        time.sleep(0.1)
    raise AssertionError(
        f"Subject 在 {_BOOT_TIMEOUT_SECONDS}s 内未就绪：\n{log_path.read_text(errors='replace')}"
    )


@pytest.mark.skipif(os.name == "nt", reason="依赖 POSIX 进程组信号语义")
def test_sigterm_runs_context_disposal_and_keeps_exit_semantics(tmp_path: Path) -> None:
    """对真子进程发 SIGTERM：Context 收尾必须执行，且进程仍以 SIGTERM 退出。"""
    port = _free_port()
    log_path = tmp_path / "subject.log"
    env = _server_env(tmp_path, port)

    with log_path.open("wb") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ghrah.subject.server.app"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        _wait_healthy(proc, port, log_path)
        # 等 reconcile 挂载 cluster：CoreUnit 只有被挂载后才会在停机时留下收尾日志。
        _wait_log(log_path, "mounted cluster", _BOOT_TIMEOUT_SECONDS)
        # 生产停机路径：工具链对进程组发一次 SIGTERM。
        os.killpg(proc.pid, signal.SIGTERM)
        returncode = proc.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)

    output = log_path.read_text(errors="replace")
    # ① uvicorn 的 capture_signals 重放信号后仍以 SIGTERM 语义退出（exit -15）。
    assert returncode == -signal.SIGTERM, output
    # ② uvicorn 自身的优雅停机日志（旧代码也有的部分）作为基线存在。
    assert "Application shutdown complete" in output, output
    # ③ 收尾链必须执行：CoreUnit.stop → Supervisor.shutdown → cluster dispose。
    assert "Supervisor shutdown (" in output, output
    assert "CoreUnit stopped" in output, output
    assert "disposed cluster" in output, output
    # ④ 明确断言不是"进程被信号直接杀掉、什么都没收尾"的旧行为。
    assert "收到停止信号" in output, output


def _wait_log(log_path: Path, needle: str, timeout: float) -> None:
    """轮询日志文件直到出现 needle。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in log_path.read_text(errors="replace"):
            return
        time.sleep(0.1)
    raise AssertionError(
        f"{timeout}s 内日志未出现 {needle!r}：\n{log_path.read_text(errors='replace')}"
    )


async def test_serve_restores_signal_handlers_when_assembly_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """装配异常退出时必须原样恢复信号处理器（不改变非信号退出语义）。"""
    from ghrah.subject.runtime import assembly as assembly_module

    marker = RuntimeError("assembly boom")

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise marker

    monkeypatch.setattr(assembly_module, "assemble_subject", _boom)
    watched = app_module._stop_signals()
    before = {sig: signal.getsignal(sig) for sig in watched}
    with pytest.raises(RuntimeError, match="assembly boom"):
        await app_module.serve()
    for sig in watched:
        assert signal.getsignal(sig) is before[sig]


async def test_serve_degrades_when_signal_handlers_cannot_be_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无法安装信号处理器（如非主线程）时只告警降级，不阻断启动。"""
    from ghrah.subject.runtime import assembly as assembly_module

    marker = RuntimeError("assembly boom")

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise marker

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise ValueError("signal only works in main thread")

    # monkeypatch.context()：断言结束后立即撤销 signal.signal 的替换，避免
    # pytest-asyncio 在事件循环收尾时恢复 SIGINT 处理器被替身干扰。
    with monkeypatch.context() as patched:
        patched.setattr(assembly_module, "assemble_subject", _boom)
        patched.setattr(signal, "signal", _refuse)
        with pytest.raises(RuntimeError, match="assembly boom"):
            await app_module.serve()


def test_stop_signals_include_sigbreak_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGBREAK 只在 signal 模块提供该常量时进入停机信号集。"""
    baseline = app_module._stop_signals()
    assert signal.SIGINT in baseline
    assert signal.SIGTERM in baseline
    if hasattr(signal, "SIGBREAK"):
        assert signal.SIGBREAK in baseline
        return
    assert set(baseline) == {signal.SIGINT, signal.SIGTERM}
    monkeypatch.setattr(signal, "SIGBREAK", 21, raising=False)
    assert app_module._stop_signals() == (signal.SIGINT, signal.SIGTERM, 21)


def test_windows_replay_does_not_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows 收尾后正常返回：os.kill 对这几个信号是 TerminateProcess。"""
    actions: list[tuple[object, ...]] = []

    def _signal(sig: int, handler: object) -> object:
        actions.append(("signal", sig, handler))
        return signal.SIG_DFL

    def _kill(pid: int, sig: int) -> None:
        actions.append(("kill", pid, sig))

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(signal, "signal", _signal)
    monkeypatch.setattr(os, "kill", _kill)
    app_module._replay_stop_signals([int(signal.SIGINT), int(signal.SIGTERM)])
    assert actions == []


def test_posix_replay_resets_default_and_kills(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX 收尾后把处理器设回默认处置，再向自己补发信号。"""
    recorded: list[tuple[object, ...]] = []

    def _signal(sig: int, handler: object) -> object:
        recorded.append(("signal", sig, handler))
        return signal.SIG_DFL

    def _kill(pid: int, sig: int) -> None:
        recorded.append(("kill", pid, sig))

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(signal, "signal", _signal)
    monkeypatch.setattr(os, "kill", _kill)
    app_module._replay_stop_signals([int(signal.SIGTERM)])
    assert recorded == [
        ("signal", signal.SIGTERM, signal.SIG_DFL),
        ("kill", os.getpid(), signal.SIGTERM),
    ]
