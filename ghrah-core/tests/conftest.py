# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from _mock_mcp import MockMcpEndpoint, start_mock_server

from ghrah.core.config import AgentConfig


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
