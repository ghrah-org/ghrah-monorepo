# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Subject 侧 MCP 探测编排：真实安装根与临时 agentconf 库，网络走注入 connector。

这里不启动 HTTP 端点：被测的是「解析安装包 → 单飞/并发闸门 → 调用 Core 探测
→ 映射协议视图」这条编排链。失败分类的固定文案、无敏感信息回显，以及同
服务器单飞/全局并发 2 的立即失败语义都在本文件锁定。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from agentconf import AgentsConfig
from ghrah.ability.loading.ability_schema import AbilityDefinition
from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import AbilityInstaller, InstallerError
from ghrah.mcp.agentconf_source import AgentconfMcpSource
from ghrah.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpCredentialMissingError,
    McpProtocolError,
    McpRequestError,
    McpUnsupportedTransportError,
)
from ghrah.mcp.probe import McpProbeReport
from ghrah.protocol.ability_control import AbilityMcpProbeRequest

from ghrah.subject.units import ability_mcp_probe as mcp_probe
from ghrah.subject.units.ability_catalog import CatalogError

URL = "https://gw.example.com/api/v1/mcps/fetch/mcp"
KEY = "sk-test-0123456789abcdef"
BODY = f"gateway-body-{KEY}"


# --------------------------------------------------------------------- fixtures


def _binding() -> dict[str, str]:
    return {
        "target": "http_header",
        "name": "Authorization",
        "value_template": "Bearer {{auth}}",
    }


def provider_db(path: Any, *, names: tuple[str, ...] = ("fetch",)) -> Any:
    """临时 agentconf 库：一个 provider 加若干 streamable_http 服务器。"""
    with AgentsConfig(path) as config:
        provider = config.create_mcp_provider(
            "vendor/account", api_key=KEY, default_binding=_binding()
        )
        for name in names:
            config.create_mcp_server(
                name,
                "streamable_http",
                {"url": URL},
                auth={"mode": "provider", "provider_id": provider.id, "binding_mode": "inherit"},
            )
    return path


MCP_DECLARATION = """format: ghrah.ability-definition
schema_version: '1'
origin: mcp
identity: {namespace: mcp, name: local, version: '0.0.1'}
title: Local
description: Local tools
mcp: {server: fetch}
"""

SOURCE_DECLARATION = """format: ghrah.ability-definition
schema_version: '1'
identity: {namespace: acme, name: demo, version: '1.0.0'}
title: Demo
description: Demo package
"""

OPERATION_MODULE = """from pydantic import BaseModel, ConfigDict
from ghrah.types.results import ActionResult, ActionOutcome


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


TITLE = "Run"
SUMMARY = "Demo operation"
TAGS = []
EFFECTS = []
PERMISSIONS = {}
TIMEOUT_MS = 1000


async def execute(call, services):
    return ActionResult(ActionOutcome.SUCCESS, {"value": 1})
"""


async def install_mcp_package(tmp_path: Any) -> AbilityInstaller:
    """真实安装一个 MCP 快照包：显式声明 + 注入 connector 观测工具清单。"""
    declaration = tmp_path / "declaration"
    declaration.mkdir()
    (declaration / "ability.yaml").write_text(MCP_DECLARATION, encoding="utf-8", newline="\n")
    installer = AbilityInstaller(tmp_path / "installed")
    db_path = provider_db(tmp_path / "install-config.db")
    with AgentsConfig(db_path) as config:
        await installer.install_mcp(
            declaration,
            source=AgentconfMcpSource(config),
            connector=Connector(FakeClient()),
        )
    return installer


def install_source_package(tmp_path: Any) -> AbilityInstaller:
    """真实安装一个 source 能力包，用来验证非 MCP 包被拒绝。"""
    source = tmp_path / "source-demo"
    (source / "operations").mkdir(parents=True)
    (source / "ability.yaml").write_text(SOURCE_DECLARATION, encoding="utf-8", newline="\n")
    (source / "operations" / "run.py").write_text(OPERATION_MODULE, encoding="utf-8", newline="\n")
    build(source)
    installer = AbilityInstaller(tmp_path / "installed")
    installer.install(source)
    return installer


def mcp_definition(*, server: str = "fetch", version: str = "0.0.1") -> AbilityDefinition:
    """与 Core 快照同形的 MCP 定义；只用于绕过安装存储的编排用例。"""
    return AbilityDefinition.model_validate(
        {
            "format": "ghrah.ability-definition",
            "schema_version": "1",
            "identity": {"namespace": "mcp", "name": "local", "version": version},
            "title": "Local",
            "description": "Local tools",
            "origin": "mcp",
            "mcp": {"server": server},
            "operations": {
                "echo": {
                    "title": "Echo",
                    "description": "回显",
                    "input_schema": {"type": "object"},
                    "output_schema": {},
                    "mcp": {"server": server, "tool": "echo"},
                    "execution": {
                        "mode": "request",
                        "timeout_ms": 1000,
                        "cancellation": "unsupported",
                        "on_revoke": "drain",
                        "retry": "never",
                    },
                }
            },
        }
    )


def stub_definition(monkeypatch: pytest.MonkeyPatch, *, server: str | None = "fetch") -> None:
    """替换安装解析：默认固定 ``fetch``，传 ``server=None`` 时按版本派生，便于并发用例。"""

    def resolve(_install_root: Any, _full_name: str, version: str) -> AbilityDefinition:
        name = server if server is not None else f"server-{version}"
        return mcp_definition(server=name, version=version)

    monkeypatch.setattr(mcp_probe, "_resolve_definition", resolve)


@pytest.fixture(autouse=True)
def fresh_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例一个独立闸门，避免慢用例的占位泄漏到后续用例。"""
    monkeypatch.setattr(mcp_probe, "_gate", mcp_probe._ProbeGate())


# ---------------------------------------------------------------- fake MCP side


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


class FakeClient:
    """握手与清单都在进程内；写方法一旦被调用立刻失败。"""

    def __init__(self, *, resources: Any = None, with_identity: bool = True) -> None:
        self.protocol_version = "2025-06-18" if with_identity else None
        self.server_info = (
            SimpleNamespace(name="remote", version="1.2.3") if with_identity else None
        )
        self.server_capabilities = SimpleNamespace(resources=resources)
        self.calls: list[str] = []

    async def list_tools(self, *, cursor):
        self.calls.append("list_tools")
        return SimpleNamespace(tools=[tool()], next_cursor=None)

    async def list_resources(self, *, cursor):
        self.calls.append("list_resources")
        return SimpleNamespace(resources=[resource()], next_cursor=None)

    async def list_resource_templates(self, *, cursor):
        self.calls.append("list_resource_templates")
        return SimpleNamespace(resource_templates=[], next_cursor=None)

    async def call_tool(self, *args, **kwargs):
        raise AssertionError("probe must not call tools")

    async def read_resource(self, *args, **kwargs):
        raise AssertionError("probe must not read resources")


class Connector:
    """注入的建连函数；用事件控制「正在探测」的窗口，用延迟制造超时。"""

    def __init__(
        self,
        client: Any = None,
        *,
        error: Exception | None = None,
        delay: float = 0.0,
        entered: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.client = client
        self.error = error
        self.delay = delay
        self.entered = entered
        self.release = release
        self.calls = 0
        self.spec = None
        self.kwargs: dict[str, Any] | None = None

    @asynccontextmanager
    async def __call__(self, spec, **kwargs):
        self.calls += 1
        self.spec = spec
        self.kwargs = kwargs
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        yield self.client


async def probe(
    db_path: Any,
    connector: Any,
    *,
    version: str = "0.0.1",
    timeout_ms: int = 5000,
    include_resources: bool = False,
) -> Any:
    """用桩定义调用被测函数；安装根传 ``None``（解析已被替换）。"""
    return await mcp_probe.probe_package_mcp(
        None,
        "mcp",
        "local",
        version,
        AbilityMcpProbeRequest(timeout_ms=timeout_ms, include_resources=include_resources),
        db_path=db_path,
        connector=connector,
    )


# ---------------------------------------------------------------- success mapping


async def test_success_maps_identity_tools_and_no_resources(tmp_path: Any) -> None:
    db = provider_db(tmp_path / "config.db")
    installer = await install_mcp_package(tmp_path)
    connector = Connector(FakeClient())

    view = await mcp_probe.probe_package_mcp(
        str(installer.root),
        "mcp",
        "local",
        "0.0.1",
        AbilityMcpProbeRequest(),
        db_path=db,
        connector=connector,
    )

    assert view.ok is True
    assert view.error is None
    assert view.format == "ghrah.ability-control.mcp-probe.v1"
    assert (view.namespace, view.name, view.version, view.server) == (
        "mcp",
        "local",
        "0.0.1",
        "fetch",
    )
    assert view.transport == "streamable_http"
    assert view.phase == "list_tools"
    assert view.elapsed_ms >= 0
    assert view.protocol_version == "2025-06-18"
    assert view.server_info is not None
    assert (view.server_info.name, view.server_info.version) == ("remote", "1.2.3")
    assert view.tool_count == 1
    assert view.truncated is False
    tool_view = view.tools[0]
    assert tool_view.name == "echo"
    assert tool_view.title == "Echo"
    assert tool_view.description == "回显"
    assert tool_view.input_schema == {"type": "object"}
    assert tool_view.output_schema is None
    assert tool_view.read_only_hint is None
    assert view.resources_supported is False
    assert view.resources == []
    assert view.resource_count == 0
    assert connector.client.calls == ["list_tools"]
    assert URL not in view.model_dump_json()
    assert KEY not in view.model_dump_json()


async def test_include_resources_maps_resource_list(monkeypatch, tmp_path: Any) -> None:
    db = provider_db(tmp_path / "config.db")
    stub_definition(monkeypatch)
    connector = Connector(FakeClient(resources=object()))

    view = await probe(db, connector, include_resources=True)

    assert view.ok is True
    assert view.resources_supported is True
    assert view.resource_count == 1
    item = view.resources[0]
    assert (item.uri, item.name, item.title) == ("file:///notes.txt", "notes", "笔记")
    assert (item.description, item.mime_type, item.size) == ("一份说明", "text/plain", 4)
    assert connector.client.calls == ["list_tools", "list_resources", "list_resource_templates"]


async def test_missing_server_identity_is_none(monkeypatch, tmp_path: Any) -> None:
    db = provider_db(tmp_path / "config.db")
    stub_definition(monkeypatch)

    view = await probe(db, Connector(FakeClient(with_identity=False)))

    assert view.ok is True
    assert view.server_info is None
    assert view.protocol_version is None


# ------------------------------------------------------------- package resolution


async def test_unknown_version_is_not_found(tmp_path: Any) -> None:
    installer = AbilityInstaller(tmp_path / "installed")

    with pytest.raises(CatalogError) as caught:
        await mcp_probe.probe_package_mcp(
            str(installer.root), "mcp", "local", "9.9.9", AbilityMcpProbeRequest()
        )

    assert caught.value.code == "not_found"
    assert str(installer.root) not in caught.value.message


async def test_missing_install_root_is_not_found() -> None:
    with pytest.raises(CatalogError) as caught:
        await mcp_probe.probe_package_mcp(None, "mcp", "local", "0.0.1", AbilityMcpProbeRequest())

    assert caught.value.code == "not_found"


async def test_non_mcp_package_is_invalid_change(tmp_path: Any) -> None:
    installer = install_source_package(tmp_path)

    with pytest.raises(CatalogError) as caught:
        await mcp_probe.probe_package_mcp(
            str(installer.root), "acme", "demo", "1.0.0", AbilityMcpProbeRequest()
        )

    assert caught.value.code == "invalid_change"
    assert caught.value.message == "definition_not_mcp"


async def test_unreadable_definition_is_unavailable(tmp_path: Any) -> None:
    installer = await install_mcp_package(tmp_path)
    item = installer.store.resolve("mcp.local", "0.0.1")
    assert item is not None
    (item.root / "ability.yaml").write_text("format: broken\n", encoding="utf-8", newline="\n")

    with pytest.raises(CatalogError) as caught:
        await mcp_probe.probe_package_mcp(
            str(installer.root), "mcp", "local", "0.0.1", AbilityMcpProbeRequest()
        )

    assert caught.value.code == "unavailable"
    assert str(installer.root) not in caught.value.message


async def test_installer_error_is_unavailable(monkeypatch, tmp_path: Any) -> None:
    root = tmp_path / "installed"
    root.mkdir()

    def broken_show(self, full_name, version=None):
        raise InstallerError("lock_invalid", f"安装记录损坏: {root}")

    monkeypatch.setattr(AbilityInstaller, "show", broken_show)

    with pytest.raises(CatalogError) as caught:
        await mcp_probe.probe_package_mcp(
            str(root), "mcp", "local", "0.0.1", AbilityMcpProbeRequest()
        )

    assert caught.value.code == "unavailable"
    assert "lock_invalid" in caught.value.message
    assert str(root) not in caught.value.message


# ------------------------------------------------------------ failure classification


async def test_unconfigured_server_does_not_connect(monkeypatch, tmp_path: Any) -> None:
    db = provider_db(tmp_path / "config.db")
    stub_definition(monkeypatch, server="ghost")
    connector = Connector(FakeClient())

    view = await probe(db, connector)

    assert view.ok is False
    assert view.error is not None
    assert view.error.reason == "server_not_configured"
    assert view.error.message == mcp_probe._FAILURE_MESSAGES["server_not_configured"]
    assert connector.calls == 0


async def test_broken_agentconf_is_unavailable(monkeypatch, tmp_path: Any) -> None:
    bad = tmp_path / "not-a-database"
    bad.mkdir()
    stub_definition(monkeypatch)
    connector = Connector(FakeClient())

    view = await probe(bad, connector)

    assert view.ok is False
    assert view.error is not None
    assert view.error.reason == "agentconf_unavailable"
    assert connector.calls == 0


@pytest.mark.parametrize(
    ("error", "reason", "http_status"),
    [
        (
            McpRequestError(f"failed {URL} Bearer {KEY}", http_status=401, body_snippet=BODY),
            "auth_failed",
            401,
        ),
        (
            McpRequestError(f"failed {URL}", http_status=403, body_snippet=BODY),
            "auth_failed",
            403,
        ),
        (McpRequestError("failed", http_status=404), "endpoint_not_found", 404),
        (McpRequestError("failed", http_status=405), "method_not_allowed", 405),
        (McpRequestError("failed", http_status=429), "rate_limited", 429),
        (McpRequestError("failed", http_status=500), "server_error", 500),
        (McpCredentialMissingError("fetch", URL), "credential_missing", None),
        (McpUnsupportedTransportError("stdio"), "unsupported_transport", None),
        (McpConnectionError(f"refused {URL} {KEY}"), "connect_failed", None),
        (McpProtocolError("MCP SDK unavailable"), "sdk_unavailable", None),
        (McpProtocolError("tool listing exceeds size limit"), "too_large", None),
        (McpProtocolError("handshake mismatch"), "protocol_error", None),
        (McpConfigError(f"bad spec {URL} {KEY}"), "config_invalid", None),
    ],
)
async def test_failure_uses_closed_reason_and_fixed_message(
    monkeypatch, tmp_path: Any, error: Exception, reason: str, http_status: int | None
) -> None:
    db = provider_db(tmp_path / "config.db")
    stub_definition(monkeypatch)

    view = await probe(db, Connector(error=error))

    assert view.ok is False
    assert view.error is not None
    assert view.error.reason == reason
    assert view.error.message == mcp_probe._FAILURE_MESSAGES[reason]
    assert view.error.http_status == http_status
    assert view.server_info is None
    dump = view.model_dump_json()
    assert URL not in dump
    assert KEY not in dump
    assert BODY not in dump


async def test_timeout_reports_fixed_message(monkeypatch, tmp_path: Any) -> None:
    db = provider_db(tmp_path / "config.db")
    stub_definition(monkeypatch)
    connector = Connector(delay=30)

    started = time.monotonic()
    view = await probe(db, connector, timeout_ms=1000)
    wall = time.monotonic() - started

    assert wall < 5.0
    assert view.ok is False
    assert view.error is not None
    assert view.error.reason == "timeout"
    assert view.error.message == mcp_probe._FAILURE_MESSAGES["timeout"]
    assert view.error.http_status is None
    assert view.elapsed_ms < 5_000
    assert connector.calls == 1


async def test_timeout_is_clamped_before_core(monkeypatch, tmp_path: Any) -> None:
    db = provider_db(tmp_path / "config.db")
    stub_definition(monkeypatch)
    seen: dict[str, Any] = {}

    async def fake_probe(server: str, **kwargs: Any) -> McpProbeReport:
        seen["server"] = server
        seen.update(kwargs)
        return McpProbeReport(ok=True, phase="initialize", elapsed_ms=3)

    monkeypatch.setattr(mcp_probe, "probe_server", fake_probe)

    view = await mcp_probe.probe_package_mcp(
        None,
        "mcp",
        "local",
        "0.0.1",
        AbilityMcpProbeRequest.model_construct(timeout_ms=50_000, include_resources=True),
        db_path=db,
        connector="sentinel",
    )

    assert view.ok is True
    assert seen["server"] == "fetch"
    assert seen["db_path"] == db
    assert seen["timeout_ms"] == 10_000
    assert seen["include_tools"] is True
    assert seen["include_resources"] is True
    assert seen["connector"] == "sentinel"

    await mcp_probe.probe_package_mcp(
        None,
        "mcp",
        "local",
        "0.0.1",
        AbilityMcpProbeRequest.model_construct(timeout_ms=1, include_resources=False),
        db_path=db,
        connector=None,
    )
    assert seen["timeout_ms"] == 1_000


async def test_outer_budget_wraps_core_call(monkeypatch, tmp_path: Any) -> None:
    stub_definition(monkeypatch)

    async def hang(*args: Any, **kwargs: Any) -> McpProbeReport:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    monkeypatch.setattr(mcp_probe, "probe_server", hang)

    started = time.monotonic()
    view = await probe(tmp_path / "unused.db", None, timeout_ms=1000)
    wall = time.monotonic() - started

    assert wall < 3.0
    assert view.ok is False
    assert view.error is not None
    assert view.error.reason == "timeout"
    assert view.error.message == mcp_probe._FAILURE_MESSAGES["timeout"]


async def test_gate_releases_after_failure(monkeypatch, tmp_path: Any) -> None:
    db = provider_db(tmp_path / "config.db")
    stub_definition(monkeypatch)

    failed = await probe(db, Connector(error=McpConnectionError("refused")))
    assert failed.ok is False

    succeeded = await probe(db, Connector(FakeClient()))
    assert succeeded.ok is True


async def test_same_server_is_single_flight(monkeypatch, tmp_path: Any) -> None:
    db = provider_db(tmp_path / "config.db")
    stub_definition(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    slow = Connector(FakeClient(), entered=entered, release=release)

    first = asyncio.create_task(probe(db, slow))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        started = time.monotonic()
        conflict = await probe(db, Connector(FakeClient()))
        blocked_wall = time.monotonic() - started

        assert blocked_wall < 1.0
        assert conflict.ok is False
        assert conflict.error is not None
        assert conflict.error.reason == "probe_in_progress"
        assert conflict.error.message == mcp_probe._FAILURE_MESSAGES["probe_in_progress"]
        assert conflict.tool_count == 0
        assert conflict.resource_count == 0
        assert conflict.elapsed_ms == 0
        assert slow.calls == 1
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)

    assert first.done()
    assert first.exception() is None
    assert first.result().ok is True


async def test_global_concurrency_is_two(monkeypatch, tmp_path: Any) -> None:
    versions = ("1.0.1", "1.0.2", "1.0.3")
    db = provider_db(
        tmp_path / "config.db", names=tuple(f"server-{version}" for version in versions)
    )
    stub_definition(monkeypatch, server=None)

    windows = [(asyncio.Event(), asyncio.Event()) for _ in versions[:2]]
    slow = [
        Connector(FakeClient(), entered=entered, release=release) for entered, release in windows
    ]
    tasks = [
        asyncio.create_task(probe(db, connector, version=version))
        for connector, version in zip(slow, versions[:2], strict=True)
    ]
    try:
        for entered, _ in windows:
            await asyncio.wait_for(entered.wait(), timeout=2)

        started = time.monotonic()
        third = await probe(db, Connector(FakeClient()), version=versions[2])
        blocked_wall = time.monotonic() - started

        assert blocked_wall < 1.0
        assert third.ok is False
        assert third.error is not None
        assert third.error.reason == "probe_busy"
        assert third.error.message == mcp_probe._FAILURE_MESSAGES["probe_busy"]
        assert [connector.calls for connector in slow] == [1, 1]
    finally:
        for _, release in windows:
            release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(not isinstance(result, BaseException) for result in results)
    assert all(result.ok is True for result in results)
