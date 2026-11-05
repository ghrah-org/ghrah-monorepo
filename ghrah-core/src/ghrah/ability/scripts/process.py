# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""已核对脚本的子进程协议。

调用方必须先完成地址、授权和路径核对。这里只负责信封、环境、超时、取消和
结果解码，不接受任意命令行，也不经过 shell。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROTOCOL = "ghrah.script/1"
STREAM_LIMIT = 1024 * 1024
"""stdin / stdout 上限，1 MiB。"""

STDERR_LIMIT = 64 * 1024
"""stderr 只保留这么多进诊断日志，不回灌模型。"""

DEFAULT_DEADLINE_MS = 120_000
POSIX_GRACE_SECONDS = 2.0
WINDOWS_KILL_WAIT_SECONDS = 5.0
CREATE_NO_WINDOW = 0x08000000

_KINDS = frozenset(
    {
        "invalid_input",
        "not_found",
        "unsupported_language",
        "permission_denied",
        "timeout",
        "cancelled",
        "result_too_large",
        "internal",
    }
)
_POSIX_ENV = ("PATH", "TZ")
_WINDOWS_ENV = ("Path", "PATHEXT", "SystemRoot", "ComSpec", "TEMP", "TMP")
_FORCED_ENV = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}

Spawner = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ScriptRun:
    """一次子进程的解码结果。``stderr`` 只给诊断日志，不进模型结果。"""

    ok: bool
    kind: str | None
    error: str | None
    data: Any
    effect_unknown: bool
    stderr: bytes = b""


def ensure_script_runtime() -> None:
    """Windows 上拒绝不能创建子进程的事件循环。

    非 Windows 解释器没有 ``ProactorEventLoop``，这里直接返回。真正的
    Windows 宿主若停在 ``SelectorEventLoop``，装配或调用一开始就失败，
    而不是等到子进程创建时挂起。

    Raises:
        RuntimeError: 当前循环不能在 Windows 上运行脚本子进程。
    """
    if os.name != "nt":
        return
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    if proactor is None:
        return
    loop = asyncio.get_running_loop()
    if isinstance(loop, proactor):
        return
    raise RuntimeError(
        "Windows 上运行 Skill 脚本需要 ProactorEventLoop，当前事件循环无法创建子进程。"
        "请在装配时改用 ProactorEventLoop。"
    )


def script_environment() -> dict[str, str]:
    """按平台白名单构造子进程环境，不继承宿主的其余变量。

    Returns:
        传给解释器的环境。POSIX 强制 ``LANG`` / ``LC_ALL``；两边都强制
        UTF-8 和无字节码写入。不包含 ``PYTHONPATH``、``HOME`` 或 ``USERPROFILE``。
    """
    if os.name == "nt":
        env = {name: os.environ[name] for name in _WINDOWS_ENV if name in os.environ}
    else:
        env = {name: os.environ[name] for name in _POSIX_ENV if name in os.environ}
        env["LANG"] = "C.UTF-8"
        env["LC_ALL"] = "C.UTF-8"
    env.update(_FORCED_ENV)
    env.pop("PYTHONPATH", None)
    env.pop("HOME", None)
    env.pop("USERPROFILE", None)
    return env


def build_envelope(
    *,
    script: str,
    arguments: dict[str, Any],
    tool_call_id: str,
    deadline_ms: int,
    workspace: Path,
    package_root: Path,
    script_path: Path,
    env: dict[str, str],
) -> bytes:
    """编码 stdin 上的唯一 JSON 对象。

    Args:
        script: 模型地址（不带扩展名）。
        arguments: 已确认是 JSON 对象的参数。
        tool_call_id: 本次调用标识。
        deadline_ms: 剩余预算，原样交给脚本。
        workspace: 工作区绝对路径。
        package_root: 包根绝对路径。
        script_path: 脚本文件绝对路径。
        env: 实际传给子进程的环境，同时写入信封供脚本自查。

    Returns:
        无 BOM 的 UTF-8 字节。

    Raises:
        ValueError: 参数无法编码，或整份信封超过 1 MiB。
    """
    payload = {
        "protocol": PROTOCOL,
        "script": script,
        "arguments": arguments,
        "call": {"tool_call_id": tool_call_id, "deadline_ms": deadline_ms},
        "context": {
            "workspace": os.fspath(workspace),
            "package_root": os.fspath(package_root),
            "script_path": os.fspath(script_path),
            "env": env,
        },
    }
    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("arguments 必须是可编码的 JSON 对象") from exc
    if raw.startswith(b"\xef\xbb\xbf") or len(raw) > STREAM_LIMIT:
        raise ValueError("调用信封超过 1 MiB")
    return raw


def taskkill_argv(pid: int) -> list[str]:
    """Windows 树杀命令。优先用 System32 里的绝对路径，找不到再走 PATH。"""
    system_root = os.environ.get("SystemRoot")
    executable = "taskkill"
    if system_root:
        candidate = Path(system_root) / "System32" / "taskkill.exe"
        if candidate.is_file():
            executable = str(candidate)
    return [executable, "/F", "/T", "/PID", str(pid)]


async def _invoke_taskkill(argv: list[str]) -> int:
    """执行树杀。输出随系统语言变化，丢弃而不解析；返回码只进诊断。"""
    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    process = await asyncio.create_subprocess_exec(*argv, **kwargs)
    return await process.wait()


async def _spawn(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(*args, **kwargs)


def _spawn_kwargs(cwd: str, env: dict[str, str]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return kwargs


async def run_script_process(
    *,
    script_path: Path,
    cwd: Path,
    env: dict[str, str],
    envelope: bytes,
    deadline_s: float,
    grace_s: float = POSIX_GRACE_SECONDS,
    spawner: Spawner | None = None,
    cancel: asyncio.Event | None = None,
) -> ScriptRun:
    """用当前解释器执行一个已经核对过的脚本文件。

    Args:
        script_path: 包内 ``.py`` 的绝对路径。
        cwd: 工作区。
        env: :func:`script_environment` 的结果。
        envelope: stdin JSON。
        deadline_s: 超时秒数。
        grace_s: POSIX 上 ``SIGTERM`` 之后的宽限。生产默认 2 秒。
        spawner: 测试注入的进程工厂；缺省为 ``create_subprocess_exec``。
        cancel: 置位后按取消终止进程树。

    Returns:
        解码后的结果。超时、取消和超限会先终止进程树。
    """
    kwargs = _spawn_kwargs(os.fspath(cwd), env)
    try:
        process = await (spawner or _spawn)(sys.executable, os.fspath(script_path), **kwargs)
    except OSError as exc:
        logger.warning("script interpreter failed to start: %s", exc)
        return _failure("internal", "脚本解释器无法启动", effect_unknown=False)
    try:
        assert process.stdin is not None
        process.stdin.write(envelope)
        await process.stdin.drain()
        process.stdin.close()
        if hasattr(process.stdin, "wait_closed"):
            await process.stdin.wait_closed()
        return await _collect(process, deadline_s, grace_s, cancel)
    except asyncio.CancelledError:
        await asyncio.shield(_terminate(process, grace_s))
        raise


async def _collect(
    process: Any,
    deadline_s: float,
    grace_s: float,
    cancel: asyncio.Event | None,
) -> ScriptRun:
    loop = asyncio.get_running_loop()
    started = loop.time()
    stdout_task = asyncio.create_task(_read_limited(process.stdout, STREAM_LIMIT))
    stderr_task = asyncio.create_task(_read_stderr(process.stderr))
    cancel_task = asyncio.create_task(cancel.wait()) if cancel is not None else None
    waiting = {stdout_task}
    if cancel_task is not None:
        waiting.add(cancel_task)
    done, _pending = await asyncio.wait(
        waiting, timeout=deadline_s, return_when=asyncio.FIRST_COMPLETED
    )
    if cancel_task is not None and cancel_task in done:
        await _discard(stdout_task)
        effect_unknown = await _terminate(process, grace_s)
        stderr = await _take_stderr(stderr_task)
        return _failure("cancelled", "脚本已取消", effect_unknown=effect_unknown, stderr=stderr)
    if stdout_task not in done:
        await _discard(stdout_task, cancel_task)
        effect_unknown = await _terminate(process, grace_s)
        stderr = await _take_stderr(stderr_task)
        return _failure("timeout", "脚本超时", effect_unknown=effect_unknown, stderr=stderr)
    if cancel_task is not None:
        cancel_task.cancel()
        await _discard(cancel_task)
    stdout, overflow = stdout_task.result()
    if overflow:
        await _discard(stderr_task)
        effect_unknown = await _terminate(process, grace_s)
        return _failure("result_too_large", "结果超过 1 MiB", effect_unknown=effect_unknown)
    remaining = deadline_s - (loop.time() - started)
    if remaining <= 0:
        effect_unknown = await _terminate(process, grace_s)
        stderr = await _take_stderr(stderr_task)
        return _failure("timeout", "脚本超时", effect_unknown=effect_unknown, stderr=stderr)
    try:
        await asyncio.wait_for(process.wait(), remaining)
    except TimeoutError:
        effect_unknown = await _terminate(process, grace_s)
        stderr = await _take_stderr(stderr_task)
        return _failure("timeout", "脚本超时", effect_unknown=effect_unknown, stderr=stderr)
    stderr = await _take_stderr(stderr_task)
    return _interpret(stdout, process.returncode, stderr)


async def _read_limited(stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        block = await stream.read(65536)
        if not block:
            return b"".join(chunks), False
        total += len(block)
        if total > limit:
            return b"".join(chunks), True
        chunks.append(block)
    return b"".join(chunks), True


async def _read_stderr(stream: asyncio.StreamReader | None) -> bytes:
    """读到管道关闭。超过 64 KiB 的部分继续读掉，避免把脚本堵在 stderr 上。"""
    if stream is None:
        return b""
    kept = bytearray()
    while True:
        block = await stream.read(65536)
        if not block:
            return bytes(kept)
        room = STDERR_LIMIT - len(kept)
        if room > 0:
            kept.extend(block[:room])


async def _take_stderr(task: asyncio.Task[bytes]) -> bytes:
    """进程结束后取已截断的 stderr。管道还没关就放弃，不把取消变成脚本结果。"""
    if not task.done():
        try:
            return await asyncio.wait_for(task, 1)
        except TimeoutError:
            await _discard(task)
            return b""
    if task.cancelled():
        return b""
    exc = task.exception()
    if exc is not None:
        return b""
    return task.result()


async def _discard(*tasks: asyncio.Task[Any] | None) -> None:
    for task in tasks:
        if task is None or task.done():
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        except Exception:
            logger.debug("script stream closed", exc_info=True)


async def _terminate(process: Any, grace_s: float) -> bool:
    """终止进程树。返回值是 ``effect_unknown``：强杀或 Windows 树杀为真。"""
    if process.returncode is not None:
        return False
    if os.name == "nt":
        return await _terminate_windows(process)
    return await _terminate_posix(process, grace_s)


async def _terminate_windows(process: Any) -> bool:
    if process.returncode is not None:
        return False
    pid = getattr(process, "pid", None)
    try:
        if isinstance(pid, int) and pid > 0:
            rc = await _invoke_taskkill(taskkill_argv(pid))
            logger.info("taskkill pid=%s rc=%s", pid, rc)
        else:
            process.terminate()
    except OSError:
        logger.warning("taskkill 不可用，回退 TerminateProcess pid=%s", pid)
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), WINDOWS_KILL_WAIT_SECONDS)
    except TimeoutError:
        logger.warning(
            "taskkill 后进程仍在 pid=%s argv=%s",
            pid,
            getattr(process, "args", None),
        )
    return True


async def _terminate_posix(process: Any, grace_s: float) -> bool:
    if process.returncode is not None:
        return False
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        process.kill()
        await process.wait()
        return True
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    try:
        await asyncio.wait_for(process.wait(), grace_s)
    except TimeoutError:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        await process.wait()
        return True
    return False


def _interpret(stdout: bytes, returncode: int | None, stderr: bytes) -> ScriptRun:
    if stdout.startswith(b"\xef\xbb\xbf"):
        return _failure(
            "internal",
            "stdout 含 BOM，脚本须使用 utf-8 而不是 utf-8-sig",
            effect_unknown=False,
            stderr=stderr,
        )
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return _failure(
            "internal",
            f"脚本退出码 {returncode}，stdout 不是 UTF-8",
            effect_unknown=False,
            stderr=stderr,
        )
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return _failure(
            "internal",
            f"脚本退出码 {returncode}，stdout 不是合法结果信封",
            effect_unknown=False,
            stderr=stderr,
        )
    if text[end:].strip() or not isinstance(value, dict) or type(value.get("ok")) is not bool:
        return _failure(
            "internal",
            f"脚本退出码 {returncode}，stdout 不是单一结果信封",
            effect_unknown=False,
            stderr=stderr,
        )
    if value["ok"]:
        if returncode != 0:
            return _failure(
                "internal",
                f"脚本退出码 {returncode}，但 stdout 声明成功",
                effect_unknown=False,
                stderr=stderr,
            )
        return ScriptRun(True, None, None, value.get("data"), False, stderr)
    kind = value.get("kind")
    if kind not in _KINDS:
        kind = "internal"
    error = value.get("error")
    if not isinstance(error, str) or not error:
        error = "脚本失败"
    effect = value.get("effect_unknown")
    return ScriptRun(False, kind, error, None, effect is True, stderr)


def _failure(
    kind: str,
    error: str,
    *,
    effect_unknown: bool,
    stderr: bytes = b"",
) -> ScriptRun:
    return ScriptRun(False, kind, error, None, effect_unknown, stderr)
