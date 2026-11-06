# SPDX-License-Identifier: Apache-2.0
"""本地 MCP 测试端点：真实 Streamable HTTP 传输的调用、凭据与生命周期回归。

这些用例启动 ``scripts/mock_mcp_server.py`` 子进程（回环地址、随机端口），覆盖建连
握手、请求头传递、工具结果投影、执行超时、取消与卸载；不访问外网。凭据只出现在测试
构造的临时配置库里，断言时检查它没有进入模型可见的工具结果。

用例按“配置 → 激活 → 调用 → 清理”的真实顺序走 ``AgentconfMcpSource`` 与
``McpRuntime``，不替换传输层；进程内假 connector 的回归另见 ``test_runtime.py`` 与
``test_mcp_wiring.py``。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from _mock_mcp import MockMcpEndpoint
from agentconf import AgentsConfig

from ghrah.mcp.agentconf_source import AgentconfMcpSource
from ghrah.mcp.errors import (
    FailureKind,
    McpCredentialMissingError,
    McpNotActivatedError,
    McpRequestError,
    McpToolMissingError,
)
from ghrah.mcp.runtime import McpRuntime
from ghrah.mcp.spec import McpServerSpec, McpToolSpec

AUTH_BINDING = {
    "target": "http_header",
    "name": "Authorization",
    "value_template": "Bearer {{auth}}",
}
TOOLS: tuple[McpToolSpec, ...] = (
    McpToolSpec(
        "echo",
        "回显输入文本",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "query",
        "按参数返回结果",
        {
            "type": "object",
            "properties": {"server": {"type": "string"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "auth_probe",
        "报告最近一次鉴权指纹",
        {"type": "object", "properties": {}, "additionalProperties": False},
        read_only=True,
    ),
    McpToolSpec(
        "fail",
        "返回工具级错误",
        {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "slow",
        "等待指定秒数",
        {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
)


def _fingerprint(secret: str) -> str:
    return hashlib.sha256(f"Bearer {secret}".encode()).hexdigest()[:12]


def _make_source(db: Path, url: str, *, mode: str, secret: str | None) -> AgentconfMcpSource:
    """在临时 agentconf 库里准备 schema 6 的 Server 与（可选）Provider。"""
    with AgentsConfig(db) as config:
        if mode == "provider":
            provider = config.create_mcp_provider(
                "local-endpoint", api_key=secret, default_binding=AUTH_BINDING
            )
            config.create_mcp_server(
                "local-server",
                "streamable_http",
                {"url": url},
                auth={"mode": "provider", "provider_id": provider.id, "binding_mode": "inherit"},
            )
        else:
            assert secret is not None
            config.create_mcp_server(
                "local-server",
                "streamable_http",
                {"url": url, "headers": {"Authorization": f"Bearer {secret}"}},
                auth={"mode": "inline"},
            )
    return AgentconfMcpSource(db_path=db)


@asynccontextmanager
async def _activated(
    tmp_path: Path,
    endpoint: MockMcpEndpoint,
    *,
    mode: str = "provider",
    missing_key: bool = False,
    **spec_kwargs: Any,
) -> AsyncIterator[tuple[McpRuntime, McpServerSpec, AgentconfMcpSource]]:
    """建好配置、激活声明并交出运行时；退出时关闭会话与配置源。"""
    secret: str | None = endpoint.token
    if mode == "provider" and missing_key:
        secret = None
    source = _make_source(tmp_path / "agentconf.db", endpoint.url, mode=mode, secret=secret)
    runtime = McpRuntime(
        credentials_for_spec=source.credential_for_spec,
        headers_for_spec=source.headers_for_spec,
    )
    try:
        spec = source.connection_spec(
            "local-server",
            skill_full_name="diagnostic/local-endpoint",
            server="local",
            tools=TOOLS,
            **spec_kwargs,
        )
        await runtime.activate(spec, agent_scope="run")
        yield runtime, spec, source
    finally:
        await runtime.close()
        source.close()


async def _wait_until(check: Callable[[], bool], *, timeout: float = 10.0) -> None:
    """轮询服务端观测记录，避免用固定 sleep 赌时序。"""
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            pytest.fail("mock MCP server did not observe the expected state in time")
        await asyncio.sleep(0.02)


async def test_complete_call_chain_over_local_endpoint(
    tmp_path: Path, mock_mcp_endpoint: MockMcpEndpoint
) -> None:
    async with _activated(tmp_path, mock_mcp_endpoint) as (runtime, _, _):
        echoed = await runtime.call(
            agent_scope="run", server="local", tool="echo", arguments={"text": "hello"}
        )
        queried = await runtime.call(
            agent_scope="run", server="local", tool="query", arguments={"server": "alpha"}
        )
        probed = await runtime.call(
            agent_scope="run", server="local", tool="auth_probe", arguments={}
        )
    stats = mock_mcp_endpoint.stats()
    expected = _fingerprint(mock_mcp_endpoint.token)
    assert not echoed.is_error and echoed.text == "echo: hello"
    assert not queried.is_error and queried.text == "query result for alpha"
    assert expected in probed.text
    assert expected in stats["authorizations"]
    assert stats["calls"] == ["echo", "query", "auth_probe"]
    assert len(stats["authorizations"]) >= 2
    for visible in (echoed.text, queried.text, probed.text, str(probed.structured)):
        assert mock_mcp_endpoint.token not in visible


async def test_inline_header_auth_reaches_local_endpoint(
    tmp_path: Path, mock_mcp_endpoint: MockMcpEndpoint
) -> None:
    async with _activated(tmp_path, mock_mcp_endpoint, mode="inline") as (runtime, _, _):
        outcome = await runtime.call(
            agent_scope="run", server="local", tool="echo", arguments={"text": "inline"}
        )
    stats = mock_mcp_endpoint.stats()
    assert outcome.text == "echo: inline"
    assert _fingerprint(mock_mcp_endpoint.token) in stats["authorizations"]
    assert mock_mcp_endpoint.token not in outcome.text


async def test_tool_level_failure_is_result_not_exception(
    tmp_path: Path, mock_mcp_endpoint: MockMcpEndpoint
) -> None:
    async with _activated(tmp_path, mock_mcp_endpoint) as (runtime, _, _):
        outcome = await runtime.call(
            agent_scope="run", server="local", tool="fail", arguments={"reason": "boom"}
        )
    assert outcome.is_error
    assert "boom" in outcome.text
    assert mock_mcp_endpoint.stats()["calls"] == ["fail"]


async def test_execution_timeout_resets_connection_and_allows_next_call(
    tmp_path: Path, mock_mcp_endpoint: MockMcpEndpoint
) -> None:
    async with _activated(tmp_path, mock_mcp_endpoint, execution_timeout_ms=500) as (
        runtime,
        _,
        _,
    ):
        with pytest.raises(McpRequestError) as caught:
            await runtime.call(
                agent_scope="run", server="local", tool="slow", arguments={"seconds": 10}
            )
        recovered = await runtime.call(
            agent_scope="run", server="local", tool="echo", arguments={"text": "after-timeout"}
        )
    error = caught.value
    assert error.kind is FailureKind.TRANSIENT
    assert not error.uncertain  # 只读工具不产生“可能已生效”的歧义。
    assert recovered.text == "echo: after-timeout"
    assert mock_mcp_endpoint.stats()["calls"] == ["slow", "echo"]
    assert len(mock_mcp_endpoint.stats()["authorizations"]) >= 2


async def test_cancellation_propagates_and_connection_rebuilds(
    tmp_path: Path, mock_mcp_endpoint: MockMcpEndpoint
) -> None:
    async with _activated(tmp_path, mock_mcp_endpoint) as (runtime, _, _):
        task = asyncio.create_task(
            runtime.call(agent_scope="run", server="local", tool="slow", arguments={"seconds": 30})
        )
        await _wait_until(lambda: "slow" in mock_mcp_endpoint.stats()["calls"])
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        recovered = await runtime.call(
            agent_scope="run", server="local", tool="echo", arguments={"text": "after-cancel"}
        )
    assert recovered.text == "echo: after-cancel"


async def test_missing_credential_and_non_whitelisted_tool_never_reach_endpoint(
    tmp_path: Path, mock_mcp_endpoint: MockMcpEndpoint
) -> None:
    async with _activated(tmp_path, mock_mcp_endpoint, missing_key=True) as (runtime, _, _):
        with pytest.raises(McpCredentialMissingError):
            await runtime.call(
                agent_scope="run", server="local", tool="echo", arguments={"text": "x"}
            )
        with pytest.raises(McpToolMissingError):
            await runtime.call(
                agent_scope="run", server="local", tool="add", arguments={"a": 1, "b": 2}
            )
    stats = mock_mcp_endpoint.stats()
    assert stats["authorizations"] == []
    assert stats["calls"] == []


async def test_deactivate_closes_session_and_denies_new_calls(
    tmp_path: Path, mock_mcp_endpoint: MockMcpEndpoint
) -> None:
    async with _activated(tmp_path, mock_mcp_endpoint) as (runtime, spec, _):
        first = await runtime.call(
            agent_scope="run", server="local", tool="echo", arguments={"text": "first"}
        )
        await runtime.deactivate(agent_scope="run", skill_full_name=spec.skill_full_name)
        with pytest.raises(McpNotActivatedError):
            await runtime.call(
                agent_scope="run", server="local", tool="echo", arguments={"text": "second"}
            )
    assert first.text == "echo: first"
    assert mock_mcp_endpoint.stats()["calls"] == ["echo"]
