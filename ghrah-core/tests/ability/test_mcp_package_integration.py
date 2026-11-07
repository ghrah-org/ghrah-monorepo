# SPDX-License-Identifier: Apache-2.0
"""真实安装快照经 Subject、模型工具面、受信端口到回环 MCP 的生产合流。"""

from __future__ import annotations

from ast import literal_eval
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from _helpers import ToolScriptedLLM
from agentconf import AgentsConfig
from ghrah.protocol.ability_tree import RevokeAbilityRequest, UnbindToolRequest
from ouroboros import Context

from ghrah.ability.access import AccessIdentity, GrantPolicy, GrantRule
from ghrah.ability.execution.invocation import MemberReference
from ghrah.ability.mcp_wiring import McpDeploymentConfig, McpMemberBinding, McpPackageBinding
from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import AbilityInstaller
from ghrah.ability.package.provider import InstalledAbilityProvider
from ghrah.chat.content import ToolCallBlock
from ghrah.manifest.parser import parse_agent_manifest
from ghrah.mcp.agentconf_source import AgentconfMcpSource
from ghrah.mcp.spec import McpToolSpec

AGENT = "a" * 32
ADMIN = AccessIdentity("project", "deployment", "control", True)
MANIFEST = """manifest: agent
version: '2'
metadata: {namespace: test, name: mcp}
model: {endpoint_name: default}
system_prompt: Search and call the granted echo tool.
max_iterations: 12
abilities:
  - type: conversation
  - ref: ghrah.fs.read_file
  - ref: ghrah.core.load_skill
  - ref: mcp.local.echo
    permissions: {net_access: true}
surface: {mode: progressive, static: [ghrah.fs.read_file, ghrah.core.load_skill]}
"""


@asynccontextmanager
async def environment(tmp_path, endpoint, *, wired=True, route="echo", large=False):
    subject = pytest.importorskip("ghrah.subject.config")
    from ghrah.subject.core_cluster.registry import CoreClusterRegistry, default_core_unit_factory

    config = AgentsConfig(tmp_path / "agentconf.db")
    config.create_mcp_server(
        "local-server",
        "streamable_http",
        {
            "url": endpoint.url,
            "headers": {"Authorization": f"Bearer {endpoint.token}"},
        },
        auth={"mode": "inline"},
    )
    source = AgentconfMcpSource(config)
    declaration = tmp_path / "declaration"
    declaration.mkdir()
    (declaration / "ability.yaml").write_text(
        "format: ghrah.ability-definition\nschema_version: '1'\norigin: mcp\n"
        "identity: {namespace: mcp, name: local, version: '0.0.1'}\n"
        "title: Local\ndescription: Local tools\nmcp: {server: local-server}\n",
        encoding="utf-8",
    )
    installer = AbilityInstaller(tmp_path / "installed")
    installed = await installer.install_mcp(declaration, source=source)
    local = tmp_path / "local-content"
    skill = local / "skills" / "guide"
    (skill / "scripts").mkdir(parents=True)
    (skill / "references").mkdir()
    (local / "ability.yaml").write_text(
        "format: ghrah.ability-definition\nschema_version: '1'\n"
        "identity: {namespace: acme, name: content, version: '1.0.0'}\n"
        "title: Content\ndescription: Granted local content\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        "---\nname: guide\ndescription: Local guide\n---\n# Full task guide\n",
        encoding="utf-8",
    )
    (skill / "references" / "note.md").write_text("Local task reference.\n", encoding="utf-8")
    (skill / "scripts" / "export.py").write_text(
        "import json,sys\nfrom pathlib import Path\nrequest=json.load(sys.stdin)\n"
        "Path('script-output.txt').write_text('local script effect',encoding='utf-8')\n"
        "json.dump({'ok':True,'data':{'call':request['call']['tool_call_id']}},sys.stdout)\n",
        encoding="utf-8",
    )
    if large:
        operations = local / "operations"
        operations.mkdir()
        module = """from pydantic import BaseModel, ConfigDict
from ghrah.types.results import ActionResult, ActionOutcome
class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int
TITLE = "Fixture"
SUMMARY = "Local numbered fixture"
TAGS = []
EFFECTS = []
PERMISSIONS = {}
TIMEOUT_MS = 1000
async def execute(call, services):
    return ActionResult(ActionOutcome.SUCCESS, {"value": NUMBER})
"""
        for number in range(49):
            (operations / f"op_{number:03}.py").write_text(
                module.replace("NUMBER", str(number)), encoding="utf-8"
            )
    build(local)
    installer.install(local)
    (tmp_path / "note.txt").write_text("local file input", encoding="utf-8")
    version = InstalledAbilityProvider(installer.root).capture("mcp.local")
    operation = version.definition().operations["echo"]
    reference = MemberReference("mcp-local", "local", "echo", version.digest)
    binding = McpPackageBinding(
        "project",
        "cluster",
        AGENT,
        "local-server",
        "local",
        (
            McpMemberBinding(
                reference, McpToolSpec(route, operation.description, operation.input_schema)
            ),
        ),
    )

    class Store:
        def load_agent(self, name):
            return parse_agent_manifest(MANIFEST)

    class Model(ToolScriptedLLM):
        model_capabilities = {"max_context_length": 128000}

        def __init__(self):
            super().__init__(
                [
                    [
                        ToolCallBlock(
                            id="file",
                            name="read_file",
                            arguments={"file_path": str(tmp_path / "note.txt")},
                        )
                    ],
                    [
                        ToolCallBlock(
                            id="body", name="load_skill", arguments={"full_name": "content/guide"}
                        )
                    ],
                    [
                        ToolCallBlock(
                            id="attachment",
                            name="load_skill",
                            arguments={"full_name": "content/guide", "path": "references/note.md"},
                        )
                    ],
                    [
                        ToolCallBlock(
                            id="script",
                            name="run_script",
                            arguments={"script": "skills/guide/scripts/export", "arguments": {}},
                        )
                    ],
                    [ToolCallBlock(id="search", name="search_tools", arguments={"query": "echo"})],
                    [
                        ToolCallBlock(
                            id="describe", name="describe_tool", arguments={"name": "echo"}
                        )
                    ],
                    [
                        ToolCallBlock(
                            id="execute",
                            name="execute_tool",
                            arguments={"name": "echo", "arguments": {"text": "snapshot result"}},
                        )
                    ],
                ]
            )
            self.definitions = []
            self.credential_reads = []

        async def generate(self, messages, tools=None):
            self.definitions.append(list(self.tools))
            self.credential_reads.append(len(reads))
            return await super().generate(messages, tools)

    model = Model()
    reads = []
    for method in ("credential_for_spec", "headers_for_spec"):
        original = getattr(source, method)

        def read(spec, *, original=original, method=method):
            reads.append(method)
            return original(spec)

        setattr(source, method, read)
    cfg = subject.SubjectConfig(
        workspace_root=str(tmp_path),
        db_path=str(tmp_path / "subject.db"),
        ability_source="builtin",
        skill_slice=subject.SkillConfig(root=str(tmp_path / "unused-skills")),
        ability=subject.AbilityConfig(install_root=str(installer.root)),
        ability_grant_policy=GrantPolicy(
            rules=(
                GrantRule(package="mcp.local", operation="echo"),
                GrantRule(package="acme.content"),
                GrantRule(package="acme.content", skill="guide", script="export", available=True),
            )
        ),
        mcp=McpDeploymentConfig((binding,), source=source) if wired else None,
    )
    registry = CoreClusterRegistry(
        unit_factory=default_core_unit_factory(cfg, Store(), lambda _: model)
    )
    try:
        async with Context() as ctx:
            registry.bind(ctx)
            try:
                cluster = await registry.ensure_cluster(
                    "cluster", project_id="project", project_root_locator=str(tmp_path / "project")
                )
                unit = cluster._unit
                spawned = await unit.handle_command(
                    "spawn_agent",
                    {
                        "project_id": "project",
                        "config": {"name": "consumer", "agent_id": AGENT},
                        "manifest_ref": "test.mcp",
                    },
                    None,
                )
                assert spawned["success"], spawned
                actor = await unit.supervisor.get_agent_handle("consumer")
                yield SimpleNamespace(
                    unit=unit,
                    actor=actor,
                    model=model,
                    installer=installer,
                    installed=installed,
                    binding=binding,
                    source=source,
                    reads=reads,
                    store=unit._config.ability_store,
                )
            finally:
                await registry.stop()
    finally:
        config.close()


async def direct(env, request="direct"):
    return await env.unit.handle_command(
        "execute_ability",
        {
            "project_id": "project",
            "request_id": request,
            "agent_name": "consumer",
            "ability_name": "echo",
            "tool_args": {"text": "direct result"},
        },
        None,
    )


async def test_snapshot_spawn_model_direct_and_uninstall(tmp_path, mock_mcp_endpoint):
    endpoint = mock_mcp_endpoint
    async with environment(tmp_path, endpoint, large=True) as env:
        assert endpoint.stats()["calls"] == []
        assert not env.reads
        assert (
            await env.unit.supervisor.send("consumer", "Echo through the installed package")
            == "done"
        )
        assert len(env.model.calls) == 8
        assert env.model.credential_reads[:7] == [0] * 7
        assert env.model.credential_reads[-1] > 0
        from ghrah.ability.surface.runtime import _cost

        assert max(map(len, env.model.definitions)) <= 16
        assert all(_cost(row) < 128000 * 0.02 for row in env.model.definitions)
        results = {
            block.tool_call_id: block.content
            for message in env.model.calls[-1]
            for block in message.content_blocks
            if getattr(block, "tool_call_id", None)
        }
        assert "local file input" in results["file"]
        assert "Full task guide" in results["body"]
        assert "Local task reference" in results["attachment"]
        assert literal_eval(results["script"])["action"] == "script.call"
        assert (tmp_path / "script-output.txt").read_text() == "local script effect"
        assert literal_eval(results["describe"])["available"]
        assert "echo: snapshot result" in results["execute"]
        assert all("credential" not in row for row in literal_eval(results["search"])["tools"])
        assert endpoint.token not in str(env.model.calls)
        assert "echo" not in [item["function"]["name"] for item in env.model.definitions[0]]
        assert "echo" in [item["function"]["name"] for item in env.model.definitions[6]]
        audit = [
            row
            for node in env.actor._context_manager.chain.nodes
            for row in node.metadata.get("tool_surface_calls", [])
        ]
        assert any(row["kind"] == "tool.call" and row["tool_call_id"] == "execute" for row in audit)
        assert (await direct(env))["data"]["success"]
        assert endpoint.stats()["calls"] == ["echo", "echo"]
        service = env.actor._context_manager.ability_content.mcp_service
        assert service._active
        env.installer.uninstall(env.installed.full_name, env.installed.version)
        result = await direct(env, "after-uninstall")
        assert not result["success"] or not result["data"]["success"]
        assert endpoint.stats()["calls"] == ["echo", "echo"]
        assert not service._active
        await env.unit.supervisor.terminate_agent("consumer")
        assert service._closed and not env.unit._mcp_deployment._agents


@pytest.mark.parametrize("wired,route", [(False, "echo"), (True, "query")])
async def test_unwired_or_mismatched_route_never_calls_server(
    tmp_path, mock_mcp_endpoint, wired, route
):
    async with environment(tmp_path, mock_mcp_endpoint, wired=wired, route=route) as env:
        assert await env.unit.supervisor.send("consumer", "Attempt echo") == "done"
        assert mock_mcp_endpoint.stats()["calls"] == []
        assert endpoint_secret_absent(env.model, mock_mcp_endpoint.token)
        assert not env.reads
        if not wired:
            result = next(
                block
                for message in env.model.calls[-1]
                for block in message.content_blocks
                if getattr(block, "tool_call_id", None) == "describe"
            )
            assert not literal_eval(result.content)["available"]


def endpoint_secret_absent(model, secret):
    return secret not in str(model.calls)


@pytest.mark.parametrize("change", ["revoke", "unselect", "version", "run"])
async def test_snapshot_changes_reject_calls_and_release_sessions(
    tmp_path, mock_mcp_endpoint, change
):
    async with environment(tmp_path, mock_mcp_endpoint) as env:
        assert (await direct(env))["data"]["success"]
        service = env.actor._context_manager.ability_content.mcp_service
        actor = AccessIdentity("project", AGENT, env.unit.supervisor.incarnation_id("consumer"))
        revision = await env.store.agent_revision(ADMIN, AGENT)
        if change == "revoke":
            await env.store.revoke(
                ADMIN,
                AGENT,
                RevokeAbilityRequest(
                    alias="local", members=["operations/echo"], expected_revision=revision
                ),
            )
        elif change == "unselect":
            await env.store.unbind_tool(
                actor, AGENT, UnbindToolRequest(tool_name="echo", expected_revision=revision)
            )
        elif change == "version":
            instance = await env.store.instance("mcp-local")
            declaration = tmp_path / "next"
            declaration.mkdir()
            raw = (
                (env.installed.root / "ability.yaml")
                .read_text()
                .replace("version: 0.0.1", "version: 0.0.2")
            )
            (declaration / "ability.yaml").write_text(raw, encoding="utf-8")
            await env.installer.install_mcp(declaration, source=env.source)
            version = env.unit._assembly.source.packages.capture("mcp.local", version="0.0.2")
            await env.store.commit_version(ADMIN, version)
            await env.store.select_instance(
                ADMIN,
                "mcp-local",
                version.digest,
                tmp_path,
                expected_revision=instance["revision"],
            )
        else:
            await env.unit.supervisor.terminate_agent("consumer")
        if change != "run":
            await direct(env, "after-change")
            await service.refresh()
        assert not service._active
        assert mock_mcp_endpoint.stats()["calls"] == ["echo"]


async def test_closing_another_cluster_does_not_remove_current_backend(tmp_path, mock_mcp_endpoint):
    from dataclasses import replace

    from ghrah.ability.mcp_wiring import McpDeployment

    async with environment(tmp_path, mock_mcp_endpoint) as env:
        assert (await direct(env))["data"]["success"]
        other = McpDeployment(
            McpDeploymentConfig(
                (replace(env.binding, cluster_id="other", agent_id="b" * 32),),
                source=env.source,
            ),
            env.store,
            "other",
        )
        await other.close()
        assert (await direct(env, "still-wired"))["data"]["success"]
        await env.unit._mcp_deployment.close()
        unavailable = await direct(env, "closed")
        assert not unavailable["success"] or not unavailable["data"]["success"]
        view = await env.store.view(ADMIN, AGENT)
        node = next(node for node in view.abilities if node.alias == "local")
        assert all(operation.availability == "unavailable" for operation in node.operations)
        assert mock_mcp_endpoint.stats()["calls"] == ["echo", "echo"]
