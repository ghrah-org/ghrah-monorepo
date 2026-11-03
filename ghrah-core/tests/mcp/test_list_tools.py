# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``list_tools`` / ``list_endpoint_tools`` 的行为。

只验证本包自己的投影、分页边界与鉴定顺序；真正的 ``tools/list`` 线上往返由
SDK 负责，不在这里重测。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp import types

from ghrah.mcp import client as client_module
from ghrah.mcp.client import RemoteTool, list_endpoint_tools, list_tool_names, list_tools
from ghrah.mcp.errors import McpConfigError, McpCredentialMissingError, McpProtocolError
from ghrah.mcp.spec import McpServerSpec, ProtocolPreference

ENDPOINT = "https://mcp.example.com/mcp"
EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


class FakeClient:
    """只实现被调用到的那一个方法的连接替身。"""

    def __init__(self, pages: list[types.ListToolsResult]) -> None:
        self._pages = pages
        self.cursors: list[str | None] = []

    @property
    def calls(self) -> int:
        return len(self.cursors)

    async def list_tools(self, *, cursor: str | None = None) -> types.ListToolsResult:
        self.cursors.append(cursor)
        return self._pages[min(len(self.cursors), len(self._pages)) - 1]


def _tool(
    name: str,
    *,
    description: str | None = None,
    read_only: bool | None = None,
) -> types.Tool:
    annotations = None if read_only is None else types.ToolAnnotations(read_only_hint=read_only)
    return types.Tool(
        name=name,
        description=description,
        input_schema=dict(EMPTY_SCHEMA),
        annotations=annotations,
    )


async def test_list_tools_follows_cursor_and_projects_hints() -> None:
    client = FakeClient(
        [
            types.ListToolsResult(
                tools=[_tool("web_fetch", description="抓取网页", read_only=True)],
                next_cursor="page-2",
            ),
            types.ListToolsResult(tools=[_tool("web_submit")]),
        ]
    )

    tools = await list_tools(client)

    assert client.cursors == [None, "page-2"]
    assert tools == (
        RemoteTool("web_fetch", "抓取网页", EMPTY_SCHEMA, True),
        RemoteTool("web_submit", "", EMPTY_SCHEMA, False),
    )
    # 未标注 ≠ 只读：把「不知道」当只读会让写工具被自动重放。
    assert tools[1].read_only is False


async def test_list_tools_bounds_runaway_pagination() -> None:
    client = FakeClient([types.ListToolsResult(tools=[_tool("a")], next_cursor="again")])

    with pytest.raises(McpProtocolError, match="more than 2 pages"):
        await list_tools(client, max_pages=2)

    assert client.calls == 2


async def test_list_tools_accepts_exact_last_page_without_cursor() -> None:
    client = FakeClient(
        [
            types.ListToolsResult(tools=[_tool("a")], next_cursor="p2"),
            types.ListToolsResult(tools=[_tool("b")]),
        ]
    )

    # 恰好用完页数配额、且最后一页没有 next_cursor：不是跑飞，不该报错。
    tools = await list_tools(client, max_pages=2)

    assert [tool.name for tool in tools] == ["a", "b"]


async def test_list_tools_rejects_non_positive_max_pages() -> None:
    with pytest.raises(McpConfigError, match="max_pages"):
        await list_tools(FakeClient([]), max_pages=0)


async def test_list_tool_names_delegates_to_list_tools() -> None:
    client = FakeClient([types.ListToolsResult(tools=[_tool("a"), _tool("b")])])

    assert await list_tool_names(client) == ("a", "b")


async def test_list_endpoint_tools_builds_transient_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    fake = FakeClient([types.ListToolsResult(tools=[_tool("web_fetch")])])

    @asynccontextmanager
    async def fake_open(
        spec: McpServerSpec,
        *,
        credential: str | None = None,
        read_timeout_s: float | None = None,
    ) -> AsyncIterator[FakeClient]:
        seen.update(spec=spec, credential=credential, read_timeout_s=read_timeout_s)
        yield fake

    monkeypatch.setattr(client_module, "open_client", fake_open)

    tools = await list_endpoint_tools(
        ENDPOINT,
        headers={"Authorization": "Bearer {{auth}}"},
        credential="sk-test",
        protocol=ProtocolPreference.LEGACY,
        read_timeout_s=12.5,
    )

    assert [tool.name for tool in tools] == ["web_fetch"]
    spec: McpServerSpec = seen["spec"]
    assert spec.url == ENDPOINT
    assert dict(spec.headers) == {"Authorization": "Bearer {{auth}}"}
    assert spec.protocol is ProtocolPreference.LEGACY
    assert seen["credential"] == "sk-test"
    assert seen["read_timeout_s"] == 12.5


async def test_list_endpoint_tools_rejects_non_http_url() -> None:
    with pytest.raises(McpConfigError, match="scheme"):
        await list_endpoint_tools("ftp://mcp.example.com/mcp")


async def test_list_endpoint_tools_fails_closed_without_credential() -> None:
    # 占位符缺凭据要在建连之前失败：它不该变成一次 401。
    with pytest.raises(McpCredentialMissingError):
        await list_endpoint_tools(
            ENDPOINT,
            headers={"Authorization": "Bearer {{auth}}"},
        )
