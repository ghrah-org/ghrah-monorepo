# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""按服务器名做只读探测：临时 agentconf 库，网络全部走注入的 connector。"""

from __future__ import annotations

import asyncio
import dataclasses
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from agentconf import AgentsConfig

from ghrah.mcp import (
    McpProbeFailure,
    McpProbeReport,
    McpResourceInfo,
    McpServerNotConfiguredError,
    list_resource_templates,
    list_resources,
    probe_server,
    server_supports_resources,
)
from ghrah.mcp.diagnostics import redact_secrets
from ghrah.mcp.errors import McpProtocolError, McpRequestError

URL = "https://gw.example.com/api/v1/mcps/WebFetch/mcp"
KEY = "sk-test-0123456789abcdef"


def _binding() -> dict[str, str]:
    return {
        "target": "http_header",
        "name": "Authorization",
        "value_template": "Bearer {{auth}}",
    }


def provider_db(path, *, api_key: str | None = KEY):
    with AgentsConfig(path) as config:
        provider = config.create_mcp_provider(
            "vendor/account",
            api_key=api_key,
            default_binding=_binding(),
        )
        config.create_mcp_server(
            "fetch",
            "streamable_http",
            {"url": URL},
            auth={"mode": "provider", "provider_id": provider.id, "binding_mode": "inherit"},
        )
    return path


def inline_db(path):
    with AgentsConfig(path) as config:
        config.create_mcp_server(
            "inline",
            "streamable_http",
            {"url": URL, "headers": {"X-Key": KEY}},
            auth={"mode": "inline"},
        )
    return path


def tool() -> SimpleNamespace:
    return SimpleNamespace(
        name="echo",
        title="Echo",
        description="回显",
        input_schema={"type": "object"},
        output_schema=None,
    )


def resource() -> SimpleNamespace:
    return SimpleNamespace(
        uri="file:///notes.txt",
        name="notes",
        title="笔记",
        description="一份说明",
        mime_type="text/plain",
        size=4,
    )


def template() -> SimpleNamespace:
    return SimpleNamespace(
        uri_template="file:///{name}.txt",
        name="note",
        title="笔记模板",
        description="按名读取",
        mime_type="text/plain",
    )


class FakeClient:
    """握手结果与清单都在进程内。写操作被调用即失败。"""

    def __init__(
        self,
        *,
        resources: Any = None,
        resource_error: Exception | None = None,
        template_error: Exception | None = None,
        guard_capabilities: bool = False,
    ) -> None:
        self.protocol_version = "2025-06-18"
        self.server_info = SimpleNamespace(name="remote", version="1.2.3")
        self.calls: list[str] = []
        self.resource_error = resource_error
        self.template_error = template_error
        self._guard_capabilities = guard_capabilities
        if not guard_capabilities:
            self.server_capabilities = SimpleNamespace(resources=resources)

    @property
    def server_capabilities(self):  # type: ignore[no-redef]
        if self._guard_capabilities:
            raise AssertionError("capabilities")
        return self._server_capabilities

    @server_capabilities.setter
    def server_capabilities(self, value: Any) -> None:
        self._server_capabilities = value

    async def list_tools(self, *, cursor):
        self.calls.append("list_tools")
        return SimpleNamespace(tools=[tool()], next_cursor=None)

    async def list_resources(self, *, cursor):
        self.calls.append("list_resources")
        if self.resource_error is not None:
            raise self.resource_error
        return SimpleNamespace(resources=[resource()], next_cursor=None)

    async def list_resource_templates(self, *, cursor):
        self.calls.append("list_resource_templates")
        if self.template_error is not None:
            raise self.template_error
        return SimpleNamespace(resource_templates=[template()], next_cursor=None)

    async def call_tool(self, *args, **kwargs):
        raise AssertionError("call_tool")

    async def read_resource(self, *args, **kwargs):
        raise AssertionError("read_resource")


class Connector:
    def __init__(self, client: Any = None, *, error: Exception | None = None, delay: float = 0):
        self.client = client
        self.error = error
        self.delay = delay
        self.calls = 0
        self.spec = None
        self.kwargs: dict[str, Any] | None = None

    @asynccontextmanager
    async def __call__(self, spec, **kwargs):
        self.calls += 1
        self.spec = spec
        self.kwargs = kwargs
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        yield self.client


def test_public_probe_exports_and_report_omits_secrets() -> None:
    assert probe_server.__name__ == "probe_server"
    assert issubclass(McpServerNotConfiguredError, Exception)
    report_fields = {item.name for item in dataclasses.fields(McpProbeReport)}
    failure_fields = {item.name for item in dataclasses.fields(McpProbeFailure)}
    assert {"url", "headers", "body_snippet", "credential"} & report_fields == set()
    assert {"url", "headers", "body_snippet", "message"} & failure_fields == set()
    assert {McpResourceInfo, list_resources, list_resource_templates, server_supports_resources}


def test_redact_secrets_replaces_known_values() -> None:
    assert redact_secrets(f"Bearer {KEY} at {URL}", (KEY, URL)) == "Bearer *** at ***"


async def test_probe_reports_remote_identity_and_tool_shape(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db")
    client = FakeClient()
    connector = Connector(client)

    report = await probe_server("fetch", db_path=db, connector=connector)

    assert report.ok is True
    assert report.failure is None
    assert report.phase == "list_tools"
    assert report.transport == "streamable_http"
    assert report.protocol_version == "2025-06-18"
    assert report.server_name == "remote"
    assert report.server_version == "1.2.3"
    assert report.tools == (
        {
            "name": "echo",
            "title": "Echo",
            "description": "回显",
            "input_schema": {"type": "object"},
            "output_schema": None,
        },
    )
    assert report.resources_supported is False
    assert report.resources == ()
    assert client.calls == ["list_tools"]
    assert connector.kwargs is not None
    assert connector.kwargs["credential"] == KEY
    assert connector.kwargs["read_timeout_s"] <= 5
    assert KEY not in str(dict(connector.spec.headers))
    assert connector.spec.headers["Authorization"] == "Bearer {{auth}}"


async def test_inline_headers_are_merged_before_connect(tmp_path) -> None:
    db = inline_db(tmp_path / "config.db")
    connector = Connector(FakeClient(guard_capabilities=True))

    report = await probe_server("inline", db_path=db, connector=connector)

    assert report.ok is True
    assert connector.kwargs is not None
    assert connector.kwargs["credential"] is None
    assert dict(connector.spec.headers) == {"X-Key": KEY}


async def test_unknown_server_does_not_connect(tmp_path) -> None:
    with AgentsConfig(tmp_path / "config.db"):
        pass
    connector = Connector(FakeClient())

    report = await probe_server("fetch", db_path=tmp_path / "config.db", connector=connector)

    assert report.ok is False
    assert report.phase == "config"
    assert report.failure is not None
    assert report.failure.reason == "server_not_configured"
    assert connector.calls == 0
    assert URL not in report.failure.detail


async def test_missing_credential_does_not_connect(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db", api_key=None)
    connector = Connector(FakeClient())

    report = await probe_server("fetch", db_path=db, connector=connector)

    assert report.ok is False
    assert report.phase == "config"
    assert report.failure is not None
    assert report.failure.reason == "credential_missing"
    assert connector.calls == 0
    assert URL not in report.failure.detail
    assert KEY not in report.failure.detail
    assert "https://" not in report.failure.detail


async def test_stdio_transport_is_unsupported(tmp_path) -> None:
    with AgentsConfig(tmp_path / "config.db") as config:
        config.create_mcp_server("stdio", "stdio", {"command": "python"})
    connector = Connector(FakeClient())

    report = await probe_server("stdio", db_path=tmp_path / "config.db", connector=connector)

    assert report.failure is not None
    assert report.failure.reason == "unsupported_transport"
    assert report.phase == "config"
    assert connector.calls == 0


async def test_unreadable_agentconf_is_unavailable(tmp_path) -> None:
    db = tmp_path / "not-a-database"
    db.mkdir()
    connector = Connector(FakeClient())

    report = await probe_server("fetch", db_path=db, connector=connector)

    assert report.failure is not None
    assert report.failure.reason == "agentconf_unavailable"
    assert report.phase == "config"
    assert connector.calls == 0


async def test_external_credential_is_config_invalid(tmp_path) -> None:
    with AgentsConfig(tmp_path / "config.db") as config:
        config.create_mcp_server(
            "external",
            "streamable_http",
            {"url": URL},
            auth={
                "mode": "external",
                "reference": "opaque",
                "binding": {
                    "target": "http_header",
                    "name": "X-Key",
                    "value_template": "{{auth}}",
                },
            },
        )
    connector = Connector(FakeClient())

    report = await probe_server("external", db_path=tmp_path / "config.db", connector=connector)

    assert report.failure is not None
    assert report.failure.reason == "config_invalid"
    assert report.phase == "config"
    assert connector.calls == 0


async def test_connector_delay_is_bounded_timeout(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db")
    connector = Connector(delay=30)

    report = await probe_server("fetch", db_path=db, timeout_ms=1000, connector=connector)

    assert report.ok is False
    assert report.phase == "connect"
    assert report.failure is not None
    assert report.failure.reason == "timeout"
    assert report.failure.failure_kind == "transient"
    assert report.elapsed_ms < 5_000


@pytest.mark.parametrize(
    ("status", "reason", "hint"),
    [
        (401, "auth_failed", "鉴权"),
        (403, "auth_failed", "鉴权"),
        (404, "endpoint_not_found", "地址"),
        (405, "method_not_allowed", "GET"),
        (429, "rate_limited", "限流"),
        (500, "server_error", "服务端"),
        (503, "server_error", "服务端"),
    ],
)
async def test_http_status_is_classified_without_body(tmp_path, status, reason, hint) -> None:
    db = provider_db(tmp_path / "config.db")
    snippet = f"gateway-body-{KEY}"
    connector = Connector(
        error=McpRequestError(
            f"failed {URL} Bearer {KEY}",
            http_status=status,
            body_snippet=snippet,
        )
    )

    report = await probe_server("fetch", db_path=db, connector=connector)

    assert report.ok is False
    assert report.phase == "initialize"
    assert report.failure is not None
    assert report.failure.reason == reason
    assert report.failure.http_status == status
    assert hint in report.failure.hint
    assert KEY not in report.failure.detail
    assert URL not in report.failure.detail
    assert "***" in report.failure.detail
    assert snippet not in report.failure.detail
    assert snippet not in repr(report)


async def test_resources_are_skipped_when_capability_is_absent(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db")
    client = FakeClient(resources=None)
    connector = Connector(client)

    report = await probe_server("fetch", db_path=db, include_resources=True, connector=connector)

    assert report.ok is True
    assert report.phase == "list_resources"
    assert report.resources_supported is False
    assert report.resources == ()
    assert "list_resources" not in client.calls
    assert "list_resource_templates" not in client.calls


async def test_method_not_found_means_resources_are_unsupported(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db")
    client = FakeClient(
        resources=object(),
        resource_error=McpRequestError("resources/list is missing", code=-32601),
    )
    connector = Connector(client)

    report = await probe_server("fetch", db_path=db, include_resources=True, connector=connector)

    assert report.ok is True
    assert report.failure is None
    assert report.resources_supported is False
    assert report.resources == ()
    assert report.tools[0]["name"] == "echo"
    assert "list_resource_templates" not in client.calls


async def test_missing_templates_keep_listed_resources(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db")
    client = FakeClient(
        resources=object(),
        template_error=McpRequestError("templates are missing", code=-32601),
    )
    connector = Connector(client)

    report = await probe_server("fetch", db_path=db, include_resources=True, connector=connector)

    assert report.ok is True
    assert report.resources_supported is True
    assert report.resources == (
        McpResourceInfo(
            uri="file:///notes.txt",
            name="notes",
            title="笔记",
            description="一份说明",
            mime_type="text/plain",
            size=4,
        ),
    )
    assert report.resource_templates == ()


class EmptyResourceClient(FakeClient):
    """声明了资源能力，但资源与模板都是空清单。"""

    async def list_resources(self, *, cursor):
        self.calls.append("list_resources")
        return SimpleNamespace(resources=[], next_cursor=None)

    async def list_resource_templates(self, *, cursor):
        self.calls.append("list_resource_templates")
        return SimpleNamespace(resource_templates=[], next_cursor=None)


async def test_supported_but_empty_resources_are_not_unsupported(tmp_path) -> None:
    """``resources=[]`` 与「对端不支持资源」必须分开：前者 supported 仍为真。"""
    db = provider_db(tmp_path / "config.db")
    client = EmptyResourceClient(resources=object())
    connector = Connector(client)

    report = await probe_server("fetch", db_path=db, include_resources=True, connector=connector)

    assert report.ok is True
    assert report.resources_supported is True
    assert report.resources == ()
    assert report.resource_templates == ()
    assert client.calls == ["list_tools", "list_resources", "list_resource_templates"]


async def test_resources_and_templates_are_reported_together(tmp_path) -> None:
    """资源与模板都成功时，两份清单都要进报告。"""
    db = provider_db(tmp_path / "config.db")
    client = FakeClient(resources=object())
    connector = Connector(client)

    report = await probe_server("fetch", db_path=db, include_resources=True, connector=connector)

    assert report.ok is True
    assert report.resources_supported is True
    assert report.resources == (
        McpResourceInfo(
            uri="file:///notes.txt",
            name="notes",
            title="笔记",
            description="一份说明",
            mime_type="text/plain",
            size=4,
        ),
    )
    assert report.resource_templates == (
        {
            "uri_template": "file:///{name}.txt",
            "name": "note",
            "title": "笔记模板",
            "description": "按名读取",
            "mime_type": "text/plain",
        },
    )


async def test_oversized_resource_list_keeps_tools(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db")
    client = FakeClient(
        resources=object(),
        resource_error=McpProtocolError("resource listing exceeds size limit"),
    )
    connector = Connector(client)

    report = await probe_server("fetch", db_path=db, include_resources=True, connector=connector)

    assert report.ok is False
    assert report.phase == "list_resources"
    assert report.failure is not None
    assert report.failure.reason == "too_large"
    assert report.tools[0]["name"] == "echo"


async def test_include_resources_defaults_to_off(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db")
    client = FakeClient(guard_capabilities=True)
    connector = Connector(client)

    report = await probe_server("fetch", db_path=db, connector=connector)

    assert report.ok is True
    assert report.phase == "list_tools"
    assert client.calls == ["list_tools"]


async def test_include_neither_stops_at_initialize(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db")
    client = FakeClient(guard_capabilities=True)
    connector = Connector(client)

    report = await probe_server(
        "fetch",
        db_path=db,
        include_tools=False,
        include_resources=False,
        connector=connector,
    )

    assert report.ok is True
    assert report.phase == "initialize"
    assert report.tools == ()
    assert client.calls == []


async def test_cancellation_propagates(tmp_path) -> None:
    db = provider_db(tmp_path / "config.db")
    connector = Connector(delay=30)
    task = asyncio.create_task(
        probe_server("fetch", db_path=db, timeout_ms=10_000, connector=connector)
    )

    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
