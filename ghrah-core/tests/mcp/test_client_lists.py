# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""资源与资源模板的分页包装。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ghrah.mcp.client import (
    list_resource_templates,
    list_resources,
    server_supports_resources,
)
from ghrah.mcp.errors import McpProtocolError


def resource(uri: str = "file:///a.txt", name: str = "a") -> SimpleNamespace:
    return SimpleNamespace(
        uri=uri,
        name=name,
        title="标题",
        description="说明",
        mime_type="text/plain",
        size=12,
    )


def template(uri_template: str = "file:///{name}", name: str = "item") -> SimpleNamespace:
    return SimpleNamespace(
        uri_template=uri_template,
        name=name,
        title="模板",
        description="按名读取",
        mime_type="text/plain",
    )


class ResourcePages:
    async def list_resources(self, *, cursor):
        item = resource("file:///a.txt", "a") if cursor is None else resource("file:///b.txt", "b")
        return SimpleNamespace(resources=[item], next_cursor=None if cursor else "next")


class TemplatePages:
    async def list_resource_templates(self, *, cursor):
        item = template("file:///{a}", "a") if cursor is None else template("file:///{b}", "b")
        return SimpleNamespace(resource_templates=[item], next_cursor=None if cursor else "next")


async def test_list_resources_paginates_and_keeps_fields() -> None:
    rows = await list_resources(ResourcePages())

    assert [row["uri"] for row in rows] == ["file:///a.txt", "file:///b.txt"]
    assert rows[0] == {
        "uri": "file:///a.txt",
        "name": "a",
        "title": "标题",
        "description": "说明",
        "mime_type": "text/plain",
        "size": 12,
    }


async def test_list_resource_templates_paginates_and_keeps_fields() -> None:
    rows = await list_resource_templates(TemplatePages())

    assert [row["uri_template"] for row in rows] == ["file:///{a}", "file:///{b}"]
    assert rows[1] == {
        "uri_template": "file:///{b}",
        "name": "b",
        "title": "模板",
        "description": "按名读取",
        "mime_type": "text/plain",
    }


async def test_list_resources_rejects_duplicate_uri_and_cursor() -> None:
    class DuplicateUri:
        async def list_resources(self, *, cursor):
            return SimpleNamespace(
                resources=[resource("file:///same"), resource("file:///same")],
                next_cursor=None,
            )

    class RepeatCursor:
        async def list_resources(self, *, cursor):
            uri = "file:///a" if cursor is None else "file:///b"
            return SimpleNamespace(resources=[resource(uri)], next_cursor="again")

    with pytest.raises(McpProtocolError, match="duplicate resource uri"):
        await list_resources(DuplicateUri())
    with pytest.raises(McpProtocolError, match="repeated resources cursor"):
        await list_resources(RepeatCursor())


async def test_list_resource_templates_rejects_duplicate_template_and_cursor() -> None:
    class DuplicateTemplate:
        async def list_resource_templates(self, *, cursor):
            return SimpleNamespace(
                resource_templates=[template("file:///{same}"), template("file:///{same}")],
                next_cursor=None,
            )

    class RepeatCursor:
        async def list_resource_templates(self, *, cursor):
            uri_template = "file:///{a}" if cursor is None else "file:///{b}"
            return SimpleNamespace(resource_templates=[template(uri_template)], next_cursor="again")

    with pytest.raises(McpProtocolError, match="duplicate resource template"):
        await list_resource_templates(DuplicateTemplate())
    with pytest.raises(McpProtocolError, match="repeated resource templates cursor"):
        await list_resource_templates(RepeatCursor())


async def test_resource_listings_honor_page_and_size_limits() -> None:
    with pytest.raises(McpProtocolError, match="resource listing exceeds page limit"):
        await list_resources(ResourcePages(), max_pages=1)
    with pytest.raises(McpProtocolError, match="resource listing exceeds size limit"):
        await list_resources(ResourcePages(), max_bytes=1)
    with pytest.raises(McpProtocolError, match="resource template listing exceeds page limit"):
        await list_resource_templates(TemplatePages(), max_pages=1)
    with pytest.raises(McpProtocolError, match="resource template listing exceeds size limit"):
        await list_resource_templates(TemplatePages(), max_bytes=1)


def test_server_supports_resources_reads_capabilities_only() -> None:
    assert server_supports_resources(SimpleNamespace()) is False
    assert server_supports_resources(SimpleNamespace(server_capabilities=None)) is False
    hidden = SimpleNamespace(server_capabilities=SimpleNamespace(resources=None))
    assert server_supports_resources(hidden) is False
    present = SimpleNamespace(server_capabilities=SimpleNamespace(resources=object()))
    assert server_supports_resources(present) is True
