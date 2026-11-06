# SPDX-License-Identifier: Apache-2.0
"""受信 MCP 接线：真实关系库、配置源与可注入 connector，不依赖外网。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from _helpers import make_call, make_services
from agentconf import AgentsConfig
from ghrah.protocol.ability_tree import (
    BindToolRequest,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    UnbindToolRequest,
)
from mcp import types

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.execution.invocation import MemberReference
from ghrah.ability.local_content import LocalGrantedContentProvider
from ghrah.ability.mcp_wiring import (
    McpDeployment,
    McpDeploymentConfig,
    McpMemberBinding,
    McpSkillBinding,
    execute_mcp_member,
)
from ghrah.ability.versions import capture_ability_version
from ghrah.mcp.agentconf_source import AgentconfMcpSource
from ghrah.mcp.errors import FailureKind, McpConfigError, McpNotActivatedError, McpRequestError
from ghrah.mcp.spec import McpToolSpec
from ghrah.types.results import ActionOutcome

ALICE = "a" * 32
BOB = "b" * 32
ADMIN = AccessIdentity("project", "deployment", "control", True, "deployment")
SECRET = "test-credential-never-for-model"
SCHEMA = {
    "type": "object",
    "properties": {"server": {"type": "string"}},
    "additionalProperties": False,
}
EXAMPLE = Path(__file__).resolve().parents[2] / "examples/ability_content/reporting"


class Endpoint:
    def __init__(self):
        self.connections = []
        self.calls = []
        self.closed = []
        self.behavior = "success"
        self.entered = asyncio.Event()

    @asynccontextmanager
    async def connect(self, spec, *, credential=None, **kwargs):
        self.connections.append((spec.server, credential))
        try:
            yield self
        finally:
            self.closed.append(spec.server)

    async def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments))
        self.entered.set()
        if self.behavior == "wait":
            await asyncio.Event().wait()
        if self.behavior == "disconnect":
            raise OSError(f"socket lost with Authorization: {SECRET}")
        if self.behavior == "terminated":
            raise McpRequestError(SECRET, kind=FailureKind.SESSION_TERMINATED)
        credential = self.connections[-1][1] or SECRET
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"remote result {credential}")],
            is_error=self.behavior == "error",
            structured_content={"echo": credential},
        )


@asynccontextmanager
async def _wiring(tmp_path, *, mock=None):
    """搭出受信 MCP 接线；``mock`` 非空时改走本地真实端点与生产 open_client。"""
    live = mock is not None
    # 显式提交一个仅供此测试使用的成员定义，不依赖安装或工具面装配。
    root = tmp_path / "source"
    shutil.copytree(EXAMPLE, root)
    raw = yaml.safe_load((root / "ability.yaml").read_text())
    fs = Path(__file__).resolve().parents[2] / "src/ghrah/ability/builtin/fs/ability.yaml"
    operation = yaml.safe_load(fs.read_text())["members"]["operations"]["read_file"]
    operation.update(entry="operations/query.py", input_schema=SCHEMA, permissions={})
    raw["members"]["operations"] = {"query": operation}
    (root / "operations").mkdir()
    (root / "operations/query.py").write_text("# 仅固定测试版本，不作为执行入口。\n")
    version = capture_ability_version(yaml.safe_dump(raw), root=root)
    runs = {ALICE: "alice-run", BOB: "bob-run"}
    store = await AbilityAccessStore.open(
        tmp_path / "project.db", "project", operators=frozenset({"deployment"}), runs=runs.get
    )
    source = AgentconfMcpSource(db_path=tmp_path / "agentconf.db")
    with AgentsConfig(tmp_path / "agentconf.db") as cfg:
        provider = cfg.create_mcp_provider(
            "remote",
            api_key=mock.token if live else SECRET,
            default_binding={
                "target": "http_header",
                "name": "Authorization",
                "value_template": "Bearer {{auth}}",
            },
        )
        for name in ("server-a", "server-b"):
            cfg.create_mcp_server(
                name,
                "streamable_http",
                {"url": mock.url if live else f"https://example.invalid/{name}"},
                auth={"mode": "provider", "provider_id": provider.id, "binding_mode": "inherit"},
            )
    endpoint = None if live else Endpoint()
    connector = None if live else endpoint.connect
    tool_name = "query" if live else "remote-query"
    try:
        await store.commit_version(ADMIN, version)
        await store.select_instance(ADMIN, "reporting", version.digest, tmp_path)
        for agent in runs:
            await store.register_agent(ADMIN, agent)
            await store.grant(
                ADMIN,
                agent,
                GrantAbilityRequest(
                    alias="reports",
                    instance_id="reporting",
                    whole_container=True,
                    expected_revision=0,
                ),
            )
            await store.bind_tool(
                ADMIN,
                agent,
                BindToolRequest(
                    alias="reports",
                    member="query",
                    tool_name="query",
                    expected_revision=await store.agent_revision(ADMIN, agent),
                ),
            )
        reference = MemberReference("reporting", "reports", "query", version.digest)
        binding = McpSkillBinding(
            "project",
            "cluster",
            ALICE,
            MemberReference("reporting", "reports", "skill/guide", version.digest),
            "server-a",
            "remote",
            (
                McpMemberBinding(
                    reference,
                    McpToolSpec(
                        tool_name,
                        "查询测试服务",
                        SCHEMA,
                    ),
                ),
            ),
        )
        owner = McpDeployment(
            McpDeploymentConfig(
                (binding, replace(binding, agent_id=BOB, server_ref="server-b")),
                source=source,
                connector=connector,
            ),
            store,
            "cluster",
        )
        service = owner.for_agent(ALICE)
        provider = LocalGrantedContentProvider(store, ALICE, mcp_service=service)
        provider.bind_run(runs[ALICE])
        call = make_call(
            member="query",
            arguments={"server": "cannot-switch-scope"},
            project_id="project",
            agent=ALICE,
            run=runs[ALICE],
        ).with_member(reference)
        yield store, provider, owner, mock if live else endpoint, call, binding, runs
    finally:
        if "owner" in locals():
            await owner.close()
        source.close()
        await store.close()


@pytest.fixture
async def wired(tmp_path):
    async with _wiring(tmp_path) as value:
        yield value


@pytest.fixture
async def wired_live(tmp_path, mock_mcp_endpoint):
    async with _wiring(tmp_path, mock=mock_mcp_endpoint) as value:
        yield value


async def test_load_is_offline_then_complete_member_call_and_unload(wired, monkeypatch):
    store, provider, owner, endpoint, call, _, _ = wired
    reads = []
    original = owner.source.credential_for_spec
    monkeypatch.setattr(
        owner.source,
        "credential_for_spec",
        lambda spec: (reads.append(spec.connection_identity), original(spec))[1],
    )
    with pytest.raises(McpNotActivatedError):
        await provider.mcp_service.invoke(call)
    await provider.load_skill("reports/guide", path="references/style.md")
    assert provider.mcp_service._active == {}
    await provider.load_skill("reports/guide")
    assert endpoint.connections == [] and reads == []
    before = await store.view(ADMIN, ALICE)
    result = await execute_mcp_member(call, make_services(mcp_runtime=provider.mcp_service))
    assert result.outcome == ActionOutcome.SUCCESS
    assert SECRET not in str(result.data)
    assert endpoint.calls == [("remote-query", {"server": "cannot-switch-scope"})]
    assert len(reads) == 1
    assert await store.view(ADMIN, ALICE) == before
    await provider.unload_skill("reports/guide")
    assert endpoint.closed == ["remote"]
    with pytest.raises(McpNotActivatedError):
        await provider.mcp_service.invoke(call)


@pytest.mark.parametrize("change", ["revoke", "unselect", "run", "version"])
async def test_revocation_selection_version_and_run_close_old_sessions(wired, change):
    store, provider, _, endpoint, call, _, runs = wired
    await provider.load_skill("reports/guide")
    await provider.mcp_service.invoke(call)
    if change == "revoke":
        await store.revoke(
            ADMIN,
            ALICE,
            RevokeAbilityRequest(
                alias="reports",
                expected_revision=await store.agent_revision(ADMIN, ALICE),
            ),
        )
    elif change == "unselect":
        await store.unbind_tool(
            ADMIN,
            ALICE,
            UnbindToolRequest(
                tool_name="query",
                expected_revision=await store.agent_revision(ADMIN, ALICE),
            ),
        )
    elif change == "run":
        runs[ALICE] = "next-run"
    else:
        old = (await store.list_versions(ADMIN))[0]
        definition = json.loads(old.definition_json)
        definition["description"] += " changed"
        version = replace(old, digest="", definition_json=json.dumps(definition))
        version = replace(version, digest=version.expected_digest())
        await store.commit_version(ADMIN, version)
        instance = await store.instance("reporting")
        await store.select_instance(
            ADMIN,
            "reporting",
            version.digest,
            Path(instance["root"]),
            expected_revision=instance["revision"],
        )
    with pytest.raises((AccessError, McpNotActivatedError)):
        await provider.mcp_service.invoke(call)
    assert endpoint.closed == ["remote"]
    assert len(endpoint.calls) == 1


@pytest.mark.parametrize("identity", [{"agent": BOB}, {"run": "other"}, {"project_id": "other"}])
async def test_identity_cannot_be_switched(wired, identity):
    _, provider, _, endpoint, call, _, _ = wired
    await provider.load_skill("reports/guide")
    forged = replace(call, identity=replace(call.identity, **identity))
    with pytest.raises(AccessError):
        await provider.mcp_service.invoke(forged)
    assert endpoint.connections == []


async def test_same_server_alias_different_agents_and_cleanup_are_isolated(wired):
    store, provider, owner, endpoint, call, _, runs = wired
    other = LocalGrantedContentProvider(store, BOB, mcp_service=owner.for_agent(BOB))
    other.bind_run(runs[BOB])
    await provider.load_skill("reports/guide")
    await other.load_skill("reports/guide")
    await provider.mcp_service.invoke(call)
    other_call = replace(call, identity=replace(call.identity, agent=BOB, run=runs[BOB]))
    await other.mcp_service.invoke(other_call)
    assert provider.mcp_service.scope != other.mcp_service.scope
    await provider.mcp_service.close()
    await other.mcp_service.invoke(other_call)
    assert len(endpoint.closed) == 1 and len(endpoint.calls) == 3
    assert owner.source.names() == ("server-a", "server-b")


@pytest.mark.parametrize("behavior", ["error", "disconnect", "terminated"])
async def test_server_failure_uncertain_write_and_secret_redaction(wired, behavior, caplog):
    _, provider, _, endpoint, call, _, _ = wired
    await provider.load_skill("reports/guide")
    endpoint.behavior = behavior
    result = await execute_mcp_member(call, make_services(mcp_runtime=provider.mcp_service))
    assert result.outcome == ActionOutcome.FAILURE
    if behavior != "error":
        assert result.data["uncertain"] is True
    assert len(endpoint.calls) == 1  # 写调用绝不自动重放。
    assert SECRET not in str(result.data) and SECRET not in caplog.text


async def test_cancellation_closes_connection_and_propagates(wired):
    _, provider, _, endpoint, call, _, _ = wired
    await provider.load_skill("reports/guide")
    endpoint.behavior = "wait"
    task = asyncio.create_task(
        execute_mcp_member(call, make_services(mcp_runtime=provider.mcp_service))
    )
    await asyncio.wait_for(endpoint.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert endpoint.closed == ["remote"]


async def test_missing_credential_and_unwired_fail_before_connector(wired, monkeypatch):
    _, provider, owner, endpoint, call, _, _ = wired
    await provider.load_skill("reports/guide")
    monkeypatch.setattr(owner.source, "credential_for_spec", lambda name, **kw: None)
    result = await execute_mcp_member(call, make_services(mcp_runtime=provider.mcp_service))
    assert result.data["error"] == "MCP credential missing"
    assert endpoint.connections == []
    result = await execute_mcp_member(call, make_services())
    assert result.data["error"] == "MCP service not wired"


async def test_non_granted_and_non_selected_members_never_read_credentials(wired, monkeypatch):
    store, provider, owner, endpoint, _, _, _ = wired
    monkeypatch.setattr(
        owner.source, "credential_for_spec", lambda name, **kw: pytest.fail("提前读取凭据")
    )
    await store.unbind_tool(
        ADMIN,
        ALICE,
        UnbindToolRequest(
            tool_name="query",
            expected_revision=await store.agent_revision(ADMIN, ALICE),
        ),
    )
    with pytest.raises(AccessError):
        await provider.load_skill("reports/guide")
    assert endpoint.connections == []
    await store.revoke(
        ADMIN,
        ALICE,
        RevokeAbilityRequest(
            alias="reports",
            expected_revision=await store.agent_revision(ADMIN, ALICE),
        ),
    )
    with pytest.raises(AccessError):
        await provider.load_skill("reports/guide")


def test_same_scope_conflict_is_explicit():
    ref = MemberReference("package", "tools", "query", "version")
    binding = McpSkillBinding(
        "project",
        "cluster",
        ALICE,
        replace(ref, member="skill/guide"),
        "record",
        "server",
        (McpMemberBinding(ref, McpToolSpec("query", "查询")),),
    )
    with pytest.raises(McpConfigError, match="冲突"):
        McpDeploymentConfig((binding, replace(binding, server_ref="different")))


async def test_key_rotation_rebuilds_only_current_agent_connection(wired):
    _, provider, owner, endpoint, call, _, _ = wired
    await provider.load_skill("reports/guide")
    await provider.mcp_service.invoke(call)
    cfg = owner.source._ensure_config()
    cfg.update_mcp_provider("remote", expected_revision=1, api_key="rotated-private-key")
    result = await execute_mcp_member(call, make_services(mcp_runtime=provider.mcp_service))
    assert result.outcome == ActionOutcome.SUCCESS
    assert "rotated-private-key" not in str(result.data)
    assert endpoint.connections == [("remote", SECRET), ("remote", "rotated-private-key")]
    assert endpoint.closed == ["remote"]


async def test_provider_rebinding_requires_explicit_reload(wired):
    _, provider, owner, endpoint, call, _, _ = wired
    await provider.load_skill("reports/guide")
    await provider.mcp_service.invoke(call)
    cfg = owner.source._ensure_config()
    new = cfg.create_mcp_provider(
        "other",
        api_key="other-key",
        default_binding={
            "target": "http_header",
            "name": "Authorization",
            "value_template": "Bearer {{auth}}",
        },
    )
    cfg.update_mcp_server(
        "server-a",
        expected_revision=1,
        auth={"mode": "provider", "provider_id": new.id, "binding_mode": "inherit"},
    )
    with pytest.raises(McpConfigError):
        await provider.mcp_service.invoke(call)
    assert len(endpoint.calls) == 1
    await provider.load_skill("reports/guide")
    await provider.mcp_service.invoke(call)
    assert endpoint.connections[-1] == ("remote", "other-key")


async def test_inline_connection_is_deferred_and_echo_is_redacted(wired):
    _, provider, owner, endpoint, call, _, _ = wired
    cfg = owner.source._ensure_config()
    cfg.update_mcp_server(
        "server-a",
        expected_revision=1,
        settings={"url": "https://example.invalid/server-a", "headers": {"X-Key": SECRET}},
        auth={"mode": "inline"},
    )
    await provider.load_skill("reports/guide")
    assert endpoint.connections == []
    result = await execute_mcp_member(call, make_services(mcp_runtime=provider.mcp_service))
    assert result.outcome == ActionOutcome.SUCCESS and SECRET not in str(result.data)


async def test_complete_member_call_over_local_mcp_endpoint(wired_live):
    """真实 Streamable HTTP 端点上的完整受信调用：配置、激活、调用与结果脱敏。"""
    _, provider, _, endpoint, call, _, _ = wired_live
    await provider.load_skill("reports/guide")
    result = await execute_mcp_member(call, make_services(mcp_runtime=provider.mcp_service))
    assert result.outcome == ActionOutcome.SUCCESS
    assert "query result for cannot-switch-scope" in result.data["content"]
    assert endpoint.token not in str(result.data)
    stats = endpoint.stats()
    assert stats["calls"] == ["query"]
    expected = hashlib.sha256(f"Bearer {endpoint.token}".encode()).hexdigest()[:12]
    assert expected in stats["authorizations"]
