# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""测试用本地 MCP 端点的进程管理。

把 ``scripts/mock_mcp_server.py`` 当作真实子进程启动，因此测试覆盖的是真实 HTTP 传输
与 MCP 握手，而不是进程内替身。启动后从 stdout 读取一行 JSON 取得实际地址（端口写 0
由系统分配，避免固定端口冲突）；关闭时先发终止信号，超时后强杀，保证不留下孤儿进程。

观测数据由服务端的 ``/stats`` 提供：调用顺序、鉴权指纹与新会话标识。指纹只是请求头
值的散列前缀，测试不会读回密钥原文。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

__all__ = ["MockMcpEndpoint", "start_mock_server"]

SERVER_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mock_mcp_server.py"
STARTUP_TIMEOUT_S = 30.0
SHUTDOWN_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class MockMcpEndpoint:
    """已就绪的本地端点。"""

    url: str
    """Streamable HTTP 端点地址，直接填进 agentconf 的 MCP 服务器设置。"""

    stats_url: str
    """观测端点，返回调用记录、鉴权指纹与会话标识。"""

    token: str
    """启动时使用的 Bearer 令牌；测试据此计算期望指纹。"""

    port: int
    """实际监听端口（由系统分配）。"""

    def stats(self) -> dict:
        """同步读取统计（回环地址、毫秒级）；失败时抛 ``OSError``。"""
        with urllib.request.urlopen(self.stats_url, timeout=5.0) as response:
            payload = json.loads(response.read().decode())
        if not isinstance(payload, dict):
            raise ValueError("mock MCP server returned a non-object stats payload")
        return payload


@asynccontextmanager
async def start_mock_server(*, token: str, state_dir: Path) -> AsyncIterator[MockMcpEndpoint]:
    """启动本地端点并等待就绪；退出时确保子进程被回收。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "server.log"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        # 不弹控制台窗口；与产品子进程处理的平台分支保持一致。
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    with log_path.open("wb") as stderr:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SERVER_SCRIPT),
            "--port",
            "0",
            "--token",
            token,
            "--state-dir",
            str(state_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            env=env,
            **kwargs,
        )
    assert process.stdout is not None
    try:
        try:
            line = await asyncio.wait_for(process.stdout.readline(), STARTUP_TIMEOUT_S)
        except TimeoutError:
            raise RuntimeError(
                f"mock MCP server did not become ready in {STARTUP_TIMEOUT_S}s: "
                f"{_log_tail(log_path)}"
            ) from None
        if not line:
            raise RuntimeError(f"mock MCP server exited during startup: {_log_tail(log_path)}")
        info = json.loads(line)
        yield MockMcpEndpoint(
            url=info["listening"],
            stats_url=info["stats"],
            token=token,
            port=int(info["port"]),
        )
    finally:
        await _stop(process)


async def _stop(process: asyncio.subprocess.Process) -> None:
    """先终止再强杀；重复调用安全。"""
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), SHUTDOWN_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()


def _log_tail(path: Path, limit: int = 2000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return "(no server log)"
