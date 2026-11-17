# SPDX-License-Identifier: Apache-2.0
"""相同消息协议跨平台继承、关闭唤醒及 Windows 句柄白名单恢复。"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghrah_kernel import subprocesses
from ghrah_kernel.context import CallerBinding
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.launch import LaunchRecord
from ghrah_kernel.subprocesses import module_command, start_process
from ghrah_kernel.wire import MessageConnection, message_pipe


async def test_inherited_connection_roundtrip_and_close_unblocks_reader():
    parent, child = message_pipe()
    endpoint = AsyncEndpoint(MessageConnection(parent))
    process = None
    try:
        process = await start_process(
            sys.executable,
            "-c",
            "import sys; from multiprocessing.connection import Connection; "
            "c=Connection(int(sys.argv[1])); c.send_bytes(c.recv_bytes()); c.close()",
            str(child.fileno()),
            connections=(child,),
        )
        child.close()
        await endpoint.send("中文".encode())
        assert await asyncio.wait_for(endpoint.receive(), 3) == "中文".encode()
        assert await asyncio.wait_for(process.wait(), 3) == 0
    finally:
        child.close()
        await endpoint.aclose()
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


async def test_close_interrupts_blocked_receive_without_waiting_for_peer():
    parent, peer = message_pipe()
    endpoint = AsyncEndpoint(MessageConnection(parent))
    receiving = asyncio.create_task(endpoint.receive())
    try:
        await asyncio.sleep(0.02)
        await asyncio.wait_for(endpoint.aclose(), 2)
        result = await asyncio.gather(receiving, return_exceptions=True)
        assert isinstance(result[0], (EOFError, OSError, ConnectionError))
    finally:
        peer.close()
        await endpoint.aclose()


@pytest.mark.parametrize("fail", [False, True])
async def test_windows_inherits_only_allowlisted_handles_and_restores_flags(monkeypatch, fail):
    flags = {11: False, 22: True}
    os_proxy = SimpleNamespace(
        name="nt",
        get_handle_inheritable=flags.__getitem__,
        set_handle_inheritable=lambda key, value: flags.__setitem__(key, value),
    )
    monkeypatch.setattr(subprocesses, "os", os_proxy)
    monkeypatch.setattr(subprocesses.subprocess, "STARTUPINFO", SimpleNamespace, raising=False)
    monkeypatch.setattr(subprocesses.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    result = object()

    async def spawn(*_, **options):
        assert flags == {11: True, 22: True}
        assert options["startupinfo"].lpAttributeList == {"handle_list": [11, 22]}
        assert options["close_fds"] is True
        assert options["creationflags"] == 512
        assert "pass_fds" not in options
        if fail:
            raise OSError("failed CreateProcess")
        return result

    monkeypatch.setattr(subprocesses.asyncio, "create_subprocess_exec", spawn)
    connections = tuple(SimpleNamespace(fileno=lambda key=key: key) for key in flags)
    if fail:
        with pytest.raises(OSError):
            await start_process("python.exe", connections=connections, new_session=True)
    else:
        assert (
            await start_process("python.exe", connections=connections, new_session=True) is result
        )
    assert flags == {11: False, 22: True}


def test_launch_executable_uses_native_absolute_path_semantics():
    assert Path(sys.executable).is_absolute()
    LaunchRecord("namespace", "namespace", "ghrah_kernel.integration.entry", CallerBinding("admin"))
    for invalid in ("python", r"C:relative\python.exe"):
        with pytest.raises(ValueError, match="绝对路径"):
            LaunchRecord(
                "namespace", "namespace", "example", CallerBinding("admin"), executable=invalid
            )
    if os.name == "nt":
        with pytest.raises(ValueError, match="绝对路径"):
            LaunchRecord(
                "namespace", "namespace", "example", CallerBinding("admin"), executable="/python"
            )


async def test_trusted_parameter_file_preserves_long_quoted_unicode_arguments(tmp_path):
    payload = "x" * 40000 + ' 中文 "引号" \\path\\'
    output = tmp_path / "result.txt"
    (tmp_path / "_argument_probe.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        'Path(sys.argv[1]).write_text(sys.argv[2], encoding="utf-8")\n',
        encoding="utf-8",
    )
    command, files = module_command(
        sys.executable,
        "_argument_probe",
        (str(output), payload),
        file_arguments=True,
    )
    directory = Path(files.name)
    assert len(" ".join(command)) < 1024
    process = None
    try:
        process = await start_process(
            *command,
            connections=(),
            env={**os.environ, "PYTHONPATH": str(tmp_path)},
        )
        assert await asyncio.wait_for(process.wait(), 5) == 0
        assert output.read_text("utf-8") == payload
        assert not (directory / "arguments.json").exists()
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        files.cleanup()
    assert not directory.exists()
