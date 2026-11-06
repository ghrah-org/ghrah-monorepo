# SPDX-License-Identifier: Apache-2.0
"""MCP 快照安装、刷新、失败保留及真实 HTTP 发现回归。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from agentconf import AgentsConfig

from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.loading.manifest_fields import ManifestValidationError
from ghrah.ability.package.installer import AbilityInstaller, InstallerError
from ghrah.ability.package.sources.mcp import tool_keys
from ghrah.mcp.agentconf_source import AgentconfMcpSource
from ghrah.mcp.client import list_tools
from ghrah.mcp.errors import McpProtocolError
from ghrah.mcp.spec import McpServerSpec


def declaration(tmp_path: Path) -> Path:
    root = tmp_path / "declaration"
    root.mkdir(exist_ok=True)
    (root / "ability.yaml").write_text(
        yaml.safe_dump(
            {
                "format": "ghrah.ability-definition",
                "schema_version": "1",
                "origin": "mcp",
                "identity": {"namespace": "mcp", "name": "local", "version": "0.0.1"},
                "title": "本地服务",
                "description": "本地工具快照",
                "mcp": {"server": "local-server"},
            }
        ),
        encoding="utf-8",
    )
    return root


def tool(name="Echo", **kwargs):
    return SimpleNamespace(
        name=name,
        title=None,
        description="回显",
        input_schema={"type": "object"},
        output_schema=None,
        **kwargs,
    )


class Source:
    def connection_spec(self, name, **kwargs):
        assert name == "local-server"
        return McpServerSpec(server="local", url="http://127.0.0.1:1234/mcp", **kwargs)

    def credential_for_spec(self, spec):
        return None

    def headers_for_spec(self, spec):
        return None


class Endpoint:
    def __init__(self):
        self.tools = [tool()]
        self.error = None
        self.cursors = []
        self.delay = 0

    async def list_tools(self, *, cursor):
        self.cursors.append(cursor)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return SimpleNamespace(tools=self.tools, next_cursor=None)

    @asynccontextmanager
    async def connect(self, spec, **kwargs):
        yield self


async def test_install_refresh_candidates_and_failure_preservation(tmp_path):
    installer = AbilityInstaller(tmp_path / "store")
    endpoint = Endpoint()
    first = await installer.install_mcp(
        declaration(tmp_path), source=Source(), connector=endpoint.connect
    )
    assert first.version == "0.0.1"
    assert list(first.root.iterdir()) == [first.root / "ability.yaml"]
    item = installer.store.resolve(first.full_name, first.version)
    assert set(item.lock.files) == {"ability.yaml"}
    assert item.source.kind == "mcp"
    assert installer.verify(first.full_name)[0].ok
    raw = (first.root / "ability.yaml").read_bytes()
    definition = parse_definition(raw.decode(), source="test")
    operation = definition.operations["echo"]
    assert operation.mcp.tool == "Echo" and operation.entry is None
    assert operation.execution.timeout_ms == 300000 and operation.permissions == {
        "net_access": True
    }
    assert b"127.0.0.1" not in raw
    same = await installer.refresh_mcp(first.full_name, source=Source(), connector=endpoint.connect)
    assert same.already_installed and same.package_digest == first.package_digest
    assert (same.root / "ability.yaml").read_bytes() == raw
    endpoint.tools.append(tool("Other"))
    changed = await installer.refresh_mcp(
        first.full_name, source=Source(), connector=endpoint.connect
    )
    assert changed.version == "0.0.2"
    assert (first.root / "ability.yaml").read_bytes() == raw
    assert len(installer.list()) == 2
    before = installer.store.resolve(first.full_name, changed.version).mcp.fetched_at
    endpoint.error = RuntimeError("Bearer SUPERSECRET http://private.example")
    with pytest.raises(InstallerError, match="工具发现失败"):
        await installer.refresh_mcp(first.full_name, source=Source(), connector=endpoint.connect)
    stale = installer.store.resolve(first.full_name, changed.version)
    assert stale.mcp.stale and stale.mcp.fetched_at == before
    assert stale.mcp.last_error == "mcp_observation_failed"
    assert "SUPERSECRET" not in stale.lock_path.read_text()
    assert all(result.ok for result in installer.verify(first.full_name))
    endpoint.error = None
    endpoint.tools = []
    with pytest.raises(InstallerError) as error:
        await installer.refresh_mcp(first.full_name, source=Source(), connector=endpoint.connect)
    assert error.value.code == "no_tools"
    assert len(installer.list()) == 2


async def test_concurrent_refresh_keeps_one_new_candidate(tmp_path):
    installer = AbilityInstaller(tmp_path / "store")
    endpoint = Endpoint()
    first = await installer.install_mcp(
        declaration(tmp_path), source=Source(), connector=endpoint.connect
    )
    endpoint.tools.append(tool("Next"))
    endpoint.delay = 0.03
    results = await asyncio.gather(
        *(
            installer.refresh_mcp(first.full_name, source=Source(), connector=endpoint.connect)
            for _ in range(3)
        )
    )
    assert {result.version for result in results} == {"0.0.2"}
    assert len(installer.list()) == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(mcp={}),
        lambda d: d["mcp"].update(url="http://secret"),
        lambda d: d["identity"].update(namespace="external"),
    ],
)
async def test_invalid_declaration_rejected_before_connection(tmp_path, change):
    root = declaration(tmp_path)
    path = root / "ability.yaml"
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data))
    endpoint = Endpoint()
    with pytest.raises(InstallerError) as error:
        await AbilityInstaller(tmp_path / "store").install_mcp(
            root, source=Source(), connector=endpoint.connect
        )
    assert error.value.code == "invalid_definition"
    assert not endpoint.cursors


def test_tool_key_normalization_is_deterministic():
    names = ["a.b", "a_b", "", "123", "search_tools", "X" * 100]
    with pytest.raises(InstallerError):
        tool_keys(names)
    names.remove("")
    keys = tool_keys(names)
    assert keys == tool_keys(names[::-1])
    assert len(set(keys.values())) == len(names)
    assert keys["123"] == "mcp_123"
    assert keys["search_tools"] == "mcp_search_tools"
    assert all(len(key) <= 64 for key in keys.values())


async def test_pagination_limits_and_schemas():
    class Pages:
        async def list_tools(self, *, cursor):
            return SimpleNamespace(
                tools=[tool("First" if cursor is None else "Second")],
                next_cursor="next" if cursor is None else None,
            )

    tools = await list_tools(Pages())
    assert [t["name"] for t in tools] == ["First", "Second"]
    with pytest.raises(McpProtocolError):
        await list_tools(Pages(), max_pages=1)
    with pytest.raises(McpProtocolError):
        await list_tools(Pages(), max_bytes=1)


async def test_real_http_discovery(tmp_path, mock_mcp_endpoint):
    endpoint = mock_mcp_endpoint
    with AgentsConfig(tmp_path / "agentconf.db") as config:
        config.create_mcp_server(
            "local-server",
            "streamable_http",
            {"url": endpoint.url, "headers": {"Authorization": f"Bearer {endpoint.token}"}},
            auth={"mode": "inline"},
        )
        source = AgentconfMcpSource(config)
        installer = AbilityInstaller(tmp_path / "store")
        result = await installer.install_mcp(declaration(tmp_path), source=source)
        definition = parse_definition((result.root / "ability.yaml").read_text(), source="local")
        assert {"echo", "add", "query"} <= set(definition.operations)
        assert installer.verify(result.full_name)[0].ok
        assert endpoint.token not in (result.root / "ability.yaml").read_text()
        assert not endpoint.stats()["calls"]


async def test_snapshot_parser_rejects_entry_and_connection_fields(tmp_path):
    installer = AbilityInstaller(tmp_path / "store")
    result = await installer.install_mcp(
        declaration(tmp_path), source=Source(), connector=Endpoint().connect
    )
    data = yaml.safe_load((result.root / "ability.yaml").read_text())
    data["members"]["operations"]["echo"]["entry"] = "operations/echo.py"
    with pytest.raises(ManifestValidationError):
        parse_definition(yaml.safe_dump(data), source="test")


async def test_cache_only_grant_describe_and_execution_denial(tmp_path):
    from ghrah.protocol.ability_tree import BindToolRequest, GrantAbilityRequest

    from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
    from ghrah.ability.surface.catalog import read_agent_catalog
    from ghrah.ability.versions import capture_ability_version

    installer = AbilityInstaller(tmp_path / "store")
    result = await installer.install_mcp(
        declaration(tmp_path), source=Source(), connector=Endpoint().connect
    )
    version = capture_ability_version((result.root / "ability.yaml").read_text(), root=result.root)
    admin = AccessIdentity("project", "admin", "control", True)
    actor = AccessIdentity("project", "alice", "run")
    store = await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"admin"}),
        runs=lambda a: "run" if a == "alice" else None,
    )
    try:
        await store.commit_version(admin, version)
        await store.select_instance(admin, "snapshot", version.digest, tmp_path)
        await store.register_agent(admin, "alice")
        view = await store.grant(
            admin,
            "alice",
            GrantAbilityRequest(
                alias="remote", instance_id="snapshot", members=["echo"], expected_revision=0
            ),
        )
        await store.bind_tool(
            actor,
            "alice",
            BindToolRequest(
                alias="remote",
                member="echo",
                tool_name="remote_echo",
                expected_revision=view.revision,
            ),
        )
        catalog = await read_agent_catalog("alice", "run", store=store)
        assert len(catalog.tools) == 1
        assert catalog.tools[0].input_schema == {"type": "object"}
        assert not catalog.tools[0].entry.available
        with pytest.raises(AccessError, match="unavailable"):
            await store.admit_tool(actor, "alice", "remote_echo", {})
    finally:
        await store.close()


async def test_pagination_rejects_duplicate_cursor_and_tool():
    class Repeating:
        def __init__(self, duplicate_name):
            self.number = 0
            self.duplicate_name = duplicate_name

        async def list_tools(self, *, cursor):
            self.number += 1
            return SimpleNamespace(
                tools=[tool("same" if self.duplicate_name else f"tool{self.number}")],
                next_cursor="repeat",
            )

    for duplicate in (True, False):
        with pytest.raises(McpProtocolError):
            await list_tools(Repeating(duplicate))


async def test_cancelled_discovery_releases_lock_and_keeps_snapshot(tmp_path):
    installer = AbilityInstaller(tmp_path / "store")
    endpoint = Endpoint()
    first = await installer.install_mcp(
        declaration(tmp_path), source=Source(), connector=endpoint.connect
    )
    entered = asyncio.Event()

    @asynccontextmanager
    async def blocked(spec, **kwargs):
        entered.set()
        await asyncio.Event().wait()
        yield

    task = asyncio.create_task(
        installer.refresh_mcp(first.full_name, source=Source(), connector=blocked)
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert installer.verify(first.full_name)[0].ok
    assert (
        await installer.refresh_mcp(first.full_name, source=Source(), connector=endpoint.connect)
    ).already_installed


@pytest.mark.parametrize(
    "failure,code", [("transport", "unsupported_transport"), ("credential", "credential_missing")]
)
async def test_configuration_failures_do_not_create_snapshot(tmp_path, failure, code):
    from ghrah.mcp.errors import McpCredentialMissingError, McpUnsupportedTransportError

    class InvalidSource(Source):
        def connection_spec(self, name, **kwargs):
            if failure == "transport":
                raise McpUnsupportedTransportError("stdio")
            return super().connection_spec(name, **kwargs)

        def credential_for_spec(self, spec):
            raise McpCredentialMissingError("local", "http://private")

    installer = AbilityInstaller(tmp_path / "store")
    with pytest.raises(InstallerError) as error:
        await installer.install_mcp(
            declaration(tmp_path), source=InvalidSource(), connector=Endpoint().connect
        )
    assert error.value.code == code
    assert installer.list() == []


def test_cli_install_and_refresh_json(tmp_path, monkeypatch, capsys):
    import json

    from ghrah.ability.package.sources import mcp as source_module
    from ghrah.ability.tools.cli import main

    endpoint = Endpoint()
    original = source_module._observe

    async def observe(header, source, connector):
        return await original(header, Source(), endpoint.connect)

    monkeypatch.setattr(source_module, "_observe", observe)
    root = tmp_path / "store"
    assert main(["install", str(declaration(tmp_path)), "--root", str(root), "--json"]) == 0
    installed = json.loads(capsys.readouterr().out)
    assert installed["ok"] and installed["data"]["version"] == "0.0.1"
    assert main(["refresh", "mcp.local", "--root", str(root), "--json"]) == 0
    refreshed = json.loads(capsys.readouterr().out)
    assert refreshed["ok"] and refreshed["data"]["already_installed"]
