# SPDX-License-Identifier: Apache-2.0
"""MCP 底层并发关闭、白名单、协议失败与可选依赖边界。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from mcp import types

from ghrah.mcp.errors import McpConfigError, McpConnectionError, McpToolMissingError
from ghrah.mcp.runtime import McpRuntime
from ghrah.mcp.session import McpSession
from ghrah.mcp.spec import McpServerSpec, McpToolSpec


def spec(**kwargs):
    return McpServerSpec(
        "guide/skill",
        "remote",
        "https://example.invalid/mcp",
        tools=(McpToolSpec("query", "读取", read_only=True),),
        **kwargs,
    )


async def test_whitelist_checked_before_credentials_and_connector():
    def forbidden(*args, **kwargs):
        pytest.fail("白名单外不能取凭据或建连")

    runtime = McpRuntime(credentials=forbidden, connector=forbidden)
    await runtime.activate(spec(), agent_scope="run")
    with pytest.raises(McpToolMissingError):
        await runtime.call(agent_scope="run", server="remote", tool="unknown")
    with pytest.raises(McpConfigError):
        await runtime.activate(replace(spec(), url="https://different.invalid"), agent_scope="run")
    await runtime.close()


async def test_close_while_connecting_never_dispatches_tool_or_leaks_handle():
    entered = asyncio.Event()
    release = asyncio.Event()
    closed = []

    @asynccontextmanager
    async def connect(*args, **kwargs):
        entered.set()
        await release.wait()
        try:
            yield object()  # 任何实际调用都会失败，关闭前不该交出此对象。
        finally:
            closed.append(True)

    session = McpSession(spec(), connector=connect)
    task = asyncio.create_task(session.call_tool(spec().tools[0]))
    await asyncio.wait_for(entered.wait(), 1)
    close = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(McpConnectionError):
        await task
    await close
    assert closed == [True] and not session.connected


async def test_connect_timeout_disposes_connector_and_read_only_reconnect_is_bounded():
    from ghrah.mcp.errors import FailureKind, McpRequestError

    closed = []

    @asynccontextmanager
    async def slow(*args, **kwargs):
        try:
            await asyncio.Event().wait()
            yield object()
        finally:
            closed.append("timeout")

    session = McpSession(spec(connect_timeout_ms=5), connector=slow)
    with pytest.raises(TimeoutError):
        await session.call_tool(spec().tools[0])
    assert closed == ["timeout"]
    await session.close()

    attempts = []

    class Server:
        async def call_tool(self, name, arguments, **kwargs):
            attempts.append(name)
            if len(attempts) == 1:
                raise McpRequestError("expired", kind=FailureKind.SESSION_TERMINATED)
            return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

    @asynccontextmanager
    async def connect(*args, **kwargs):
        try:
            yield Server()
        finally:
            closed.append("connection")

    session = McpSession(spec(), connector=connect)
    assert (await session.call_tool(spec().tools[0])).text == "ok"
    assert attempts == ["query", "query"]
    await session.close()
    assert closed == ["timeout", "connection", "connection"]


def test_disabled_mcp_imports_do_not_require_sdk():
    code = """
import importlib.abc
import sys
class WithoutSdk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'mcp', 'httpx2'}:
            raise ImportError('SDK intentionally unavailable')
sys.meta_path.insert(0, WithoutSdk())
import ghrah.core.unit
import ghrah.ability.source
import ghrah.mcp
assert 'mcp' not in sys.modules
assert 'httpx2' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("credential", [None, "shared-provider-key"])
async def test_literal_header_echo_redacted_with_or_without_provider(credential):
    secret = "separate-static-header-key"

    class Server:
        async def call_tool(self, name, arguments, **kwargs):
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"echo {secret}")],
                structured_content={"echo": secret},
            )

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield Server()

    runtime = McpRuntime(credentials=lambda _: credential, connector=connect)
    await runtime.activate(spec(headers={"X-Extra-Key": secret}), agent_scope="run")
    try:
        result = await runtime.call(agent_scope="run", server="remote", tool="query")
        assert secret not in str(result)
        assert result.structured == {"echo": "***"}
    finally:
        await runtime.close()
