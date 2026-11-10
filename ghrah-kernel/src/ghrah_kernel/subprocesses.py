# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""受信子进程的连接继承：POSIX fd 列表与 Windows socket 句柄白名单。"""

import asyncio
import json
import os
import subprocess
import tempfile
from multiprocessing.connection import Connection
from pathlib import Path


def module_command(
    executable: str,
    module: str,
    arguments: tuple[str, ...],
    *,
    file_arguments: bool | None = None,
) -> tuple[tuple[str, ...], tempfile.TemporaryDirectory | None]:
    """Windows 经私有文件传入参数，避免 64 KiB 配置撞上 32767 字符命令行上限。"""
    use_file = os.name == "nt" if file_arguments is None else file_arguments
    if not use_file:
        return (executable, "-m", module, *arguments), None
    temporary = tempfile.TemporaryDirectory(prefix="ghrah-launch-")
    try:
        path = Path(temporary.name) / "arguments.json"
        path.write_text(json.dumps({"module": module, "arguments": arguments}), encoding="utf-8")
        return (executable, "-m", "ghrah_kernel._module_entry", str(path)), temporary
    except BaseException:
        temporary.cleanup()
        raise


async def start_process(
    *command: str,
    connections: tuple[Connection, ...],
    env: dict[str, str] | None = None,
    new_session: bool = False,
) -> asyncio.subprocess.Process:
    """只传递声明的子端；启动成功、失败或取消后都恢复句柄继承标志。"""
    handles = tuple(connection.fileno() for connection in connections)
    if os.name != "nt":
        return await asyncio.create_subprocess_exec(
            *command, pass_fds=handles, env=env, start_new_session=new_session
        )
    # handle_list 要求句柄临时可继承；不使用 close_fds=False 放开全部句柄。
    previous = {handle: os.get_handle_inheritable(handle) for handle in handles}
    try:
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO(lpAttributeList={"handle_list": list(handles)})
        return await asyncio.create_subprocess_exec(
            *command,
            env=env,
            close_fds=True,
            startupinfo=startup,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if new_session else 0,
        )
    finally:
        for handle, inheritable in previous.items():
            os.set_handle_inheritable(handle, inheritable)
