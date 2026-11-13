# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import secrets
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from _mock_mcp import MockMcpEndpoint, start_mock_server
from aiosqlite import core as _aiosqlite_core

from ghrah.core.config import AgentConfig


class _DaemonThread(threading.Thread):
    """daemon 版线程：不阻塞解释器退出。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs["daemon"] = True
        super().__init__(*args, **kwargs)


# aiosqlite 的工作线程默认是非 daemon（`aiosqlite/core.py` 里 `from threading import Thread`）。
# 用例漏关连接时，pytest 打印完 summary 后主线程会永久停在
# `threading._shutdown()` 的 join 上：进程不退出、CI 步计时一直涨。
# 这里只把测试进程里的该线程降级为 daemon，保证跑完必退；
# 连接泄漏本身仍按用例逐个修（见各测试的 store/backend 清理）。
_aiosqlite_core.Thread = _DaemonThread


@pytest.fixture
async def mock_mcp_endpoint(tmp_path: Path) -> AsyncIterator[MockMcpEndpoint]:
    """启动一次性的本地 MCP 端点（真实 HTTP，随机端口，随机 Bearer 令牌）。"""
    async with start_mock_server(
        token=secrets.token_hex(8), state_dir=tmp_path / "mock-mcp-state"
    ) as endpoint:
        yield endpoint


@pytest.fixture
def sample_config() -> AgentConfig:
    """创建示例 AgentConfig。"""
    return AgentConfig(
        name="test-agent",
        description="Test agent",
        system_prompt="You are a test agent.",
    )
