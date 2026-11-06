# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""真实 Subject 工厂与生产 spawn 的获授内容闭环；不手工给 Context 注入端口。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from ghrah.ability.access import AccessError, AccessIdentity, GrantPolicy, GrantRule
from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import AbilityInstaller
from ghrah.chat.content import TextBlock, ToolCallBlock
from ghrah.chat.format import LLMResponse
from ghrah.manifest.parser import parse_agent_manifest
from ghrah.protocol.ability_tree import GrantAbilityRequest
from ghrah.types.results import ActionOutcome
from ouroboros import Context

from ghrah.subject.config import AbilityConfig, SubjectConfig
from ghrah.subject.core_cluster.registry import CoreClusterRegistry, default_core_unit_factory

AGENT = "a" * 32
GUIDE = "ability-builder/ability-package-development-guide"
MANIFEST = """\
manifest: agent
version: '2'
metadata:
  namespace: content
  name: reader
model:
  endpoint_name: default
system_prompt: Read only granted Skill content.
max_iterations: 6
abilities:
  - type: conversation
  - ref: ghrah.core.list_abilities
  - ref: ghrah.core.load_skill
"""


class ManifestStore:
    def load_agent(self, full_name):
        return parse_agent_manifest(
            MANIFEST
            + ("  - ref: ghrah.cluster.spawn_agent\n" if full_name.endswith("parent") else "")
        )

    def list_agents(self, namespace=None):
        return ["content.reader", "content.parent"]


class ContentLLM:
    model = "content-consumer"

    def __init__(self, rounds=()):
        self.rounds = list(rounds)
        self.calls = []

    def configure_tools(self, tools):
        self.tools = tools

    def apply_model_overrides(self, overrides):
        pass

    async def generate(self, messages, tools=None):
        self.calls.append(list(messages))
        if self.rounds:
            return LLMResponse(content_blocks=self.rounds.pop(0))
        return LLMResponse(content_blocks=[TextBlock(text="read complete")])


def config(tmp_path, *, policy=None):
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.sqlite"),
        ability_source="builtin",
        ability=AbilityConfig(install_root=str(tmp_path / "installed")),
        ability_grant_policy=policy
        if policy is not None
        else GrantPolicy(
            rules=(
                GrantRule(
                    package="ghrah.ability-builder", skill="ability-package-development-guide"
                ),
            )
        ),
    )


async def spawn(unit, *, name="reader", agent=AGENT, manifest="content.reader"):
    result = await unit.handle_command(
        "spawn_agent",
        {
            "project_id": unit._config.project_id,
            "config": {"name": name, "agent_id": agent},
            "manifest_ref": manifest,
        },
        None,
    )
    assert result["success"], result
    return await unit.supervisor.get_agent_handle(name)


async def test_subject_spawn_catalog_body_attachment_and_tool_result_persistence(tmp_path):
    llm = ContentLLM(
        [
            [ToolCallBlock(id="discover", name="list_abilities", arguments={})],
            [ToolCallBlock(id="body", name="load_skill", arguments={"full_name": "reports/guide"})],
            [
                ToolCallBlock(
                    id="attachment",
                    name="load_skill",
                    arguments={"full_name": "reports/guide", "path": "references/style.md"},
                )
            ],
        ]
    )
    factory = default_core_unit_factory(config(tmp_path), ManifestStore(), lambda _: llm)
    registry = CoreClusterRegistry(unit_factory=factory)
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            cluster = await registry.ensure_cluster(
                "cluster", project_id="project", project_root_locator=str(tmp_path / "project")
            )
            unit = cluster._unit
            actor = await spawn(unit)
            cm = actor._context_manager
            provider = cm.ability_content
            assert provider is not None and cm.skill_provider is None
            assert provider._identity.run_id == unit.supervisor.incarnation_id("reader")
            assert GUIDE in await provider.catalog_text()

            # 外部内容经过安装校验与同一来源捕获，再显式选择和授予。
            source = tmp_path / "source"
            example = (
                Path(__file__).resolve().parents[2]
                / "ghrah-core/examples/ability_content/reporting"
            )
            shutil.copytree(example, source)
            build(source)
            AbilityInstaller(tmp_path / "installed").install(source)
            store = unit._config.ability_store
            admin = AccessIdentity("project", "deployment", "control", True, "deployment")
            version = unit._assembly.source.capture_package("reporting")
            await store.commit_version(admin, version)
            await store.select_instance(admin, "reporting", version.digest, tmp_path)
            await store.grant(
                admin,
                AGENT,
                GrantAbilityRequest(
                    alias="reports",
                    instance_id="reporting",
                    whole_container=True,
                    expected_revision=await store.agent_revision(admin, AGENT),
                ),
            )
            shutil.rmtree(source)
            assert await unit.supervisor.send("reader", "read granted content") == "read complete"
            assert len(llm.calls) == 4
            assert "reports/guide" in llm.calls[0][-1].text
            assert "# 报告编写" not in llm.calls[0][-1].text
            for call_index, tool_id, text in (
                (2, "body", "# 报告编写"),
                (3, "attachment", "短段落"),
            ):
                results = [
                    block
                    for message in llm.calls[call_index]
                    for block in getattr(message, "tool_results", [])
                ]
                assert any(
                    block.tool_call_id == tool_id and text in block.content for block in results
                )
            names = [name for node in cm.get_history() for name in node.ability_names]
            assert names.count("load_skill") == 2 and "list_abilities" in names
            assert await store.view(admin, AGENT)  # 读取过程没有启动额外服务。
            session = cm.get_active_session()
            history = actor.get_entity_history(session.session_id, session.active_branch_id, 32)
            assert history
            receipt = await unit.handle_command(
                "execute_ability",
                {
                    "request_id": "explicit-content",
                    "agent_name": "reader",
                    "ability_name": "load_skill",
                    "tool_args": {"full_name": "reports/guide", "path": "references/style.md"},
                },
                None,
            )
            assert receipt["success"] and receipt["data"]["success"], receipt
            assert "短段落" in receipt["data"]["result"]["content"]
            await unit.supervisor.terminate_agent("reader")
            with pytest.raises(AccessError):
                await provider.load_skill(GUIDE)
            # 不配置新策略时，生产恢复保留已获授内置内容，不复用旧端口。
            unit._startup_materializer._policy = None
            replacement = await spawn(unit)
            assert unit.supervisor.recovery_mode("reader") == "restored"
            assert replacement._context_manager.ability_content is not provider
            assert GUIDE in await replacement._context_manager.ability_content.catalog_text()
            with pytest.raises(AccessError):
                await provider.load_skill(GUIDE)
        finally:
            await registry.stop()


async def test_child_spawn_owns_content_service_and_does_not_inherit_parent_grant(tmp_path):
    policy = GrantPolicy(
        rules=(
            GrantRule(
                package="ghrah.ability-builder",
                skill="ability-package-development-guide",
                agent=AGENT,
            ),
        )
    )
    registry = CoreClusterRegistry(
        unit_factory=default_core_unit_factory(
            config(tmp_path, policy=policy), ManifestStore(), lambda _: ContentLLM()
        )
    )
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            cluster = await registry.ensure_cluster(
                "cluster", project_id="project", project_root_locator=str(tmp_path / "project")
            )
            unit = cluster._unit
            parent = await spawn(unit, name="parent", manifest="content.parent")
            tool = parent._abilities["spawn_agent"]
            call, services = parent._build_ability_call(
                tool, {"manifest_ref": "content.reader", "name": "child"}, {}
            )
            result = await tool.execute(call, services)
            assert result.outcome == ActionOutcome.SUCCESS, result.data
            child = await unit.supervisor.get_agent_handle("child")
            parent_content = parent._context_manager.ability_content
            child_content = child._context_manager.ability_content
            assert child_content is not parent_content
            assert child_content.agent_id == child.config.agent_id != AGENT
            assert GUIDE in await parent_content.catalog_text()
            assert GUIDE not in await child_content.catalog_text()
            with pytest.raises(AccessError):
                await child_content.load_skill(GUIDE)
        finally:
            await registry.stop()


async def test_same_agent_name_and_uuid_in_two_projects_use_separate_content_services(tmp_path):
    registry = CoreClusterRegistry(
        unit_factory=default_core_unit_factory(
            config(tmp_path), ManifestStore(), lambda _: ContentLLM()
        )
    )
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            providers = []
            for project in ("one", "two"):
                cluster = await registry.ensure_cluster(
                    project, project_id=project, project_root_locator=str(tmp_path / project)
                )
                actor = await spawn(cluster._unit)
                providers.append(actor._context_manager.ability_content)
            assert providers[0].store is not providers[1].store
            assert providers[0]._identity.project_id == "one"
            assert providers[1]._identity.project_id == "two"
            assert providers[0]._identity.run_id != providers[1]._identity.run_id
        finally:
            await registry.stop()


async def test_mcp_service_is_shared_by_direct_and_tool_call_and_closed_on_terminate(
    tmp_path, monkeypatch
):
    from ghrah.ability.builtin.core.operations.load_skill import LoadSkillAbility
    from ghrah.ability.execution.execution_services import MCP_RUNTIME
    from ghrah.ability.mcp_wiring import McpDeploymentConfig

    class UnusedSource:
        def __getattr__(self, name):
            raise AssertionError("没有显式关联时不能读取或关闭借用的配置源")

    cfg = config(tmp_path)
    cfg.mcp = McpDeploymentConfig((), source=UnusedSource())
    llm = ContentLLM(
        [[ToolCallBlock(id="load", name="load_skill", arguments={"full_name": GUIDE})]]
    )
    seen = []
    original = LoadSkillAbility.execute

    async def observe(self, call, services):
        service = services.get(MCP_RUNTIME)
        seen.append((service, call.identity))
        return await original(self, call, services)

    monkeypatch.setattr(LoadSkillAbility, "execute", observe)
    factory = default_core_unit_factory(cfg, ManifestStore(), lambda _: llm)
    registry = CoreClusterRegistry(unit_factory=factory)
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            cluster = await registry.ensure_cluster(
                "cluster", project_id="project", project_root_locator=str(tmp_path / "project")
            )
            unit = cluster._unit
            actor = await spawn(unit)
            service = actor._context_manager.ability_content.mcp_service
            receipt = await unit.handle_command(
                "execute_ability",
                {
                    "request_id": "mcp-direct",
                    "agent_name": "reader",
                    "ability_name": "load_skill",
                    "tool_args": {"full_name": GUIDE},
                },
                None,
            )
            assert receipt["success"] and receipt["data"]["success"], receipt
            await unit.supervisor.send("reader", "load the guide")
            assert len(seen) == 2
            assert all(s is service and identity.agent == AGENT for s, identity in seen)
            assert {identity.run for _, identity in seen} == {
                unit.supervisor.incarnation_id("reader")
            }
            assert service._active == {}  # 读取本身不产生任何新授权或服务器关联。
            await unit.supervisor.terminate_agent("reader")
            assert service._closed and not unit._mcp_deployment._agents
        finally:
            await registry.stop()


async def test_mcp_spawn_rollback_releases_only_failed_agent_resources(tmp_path, monkeypatch):
    from ghrah.ability.mcp_wiring import McpDeploymentConfig

    cfg = config(tmp_path)
    cfg.mcp = McpDeploymentConfig(())
    factory = default_core_unit_factory(cfg, ManifestStore(), lambda _: ContentLLM())
    registry = CoreClusterRegistry(unit_factory=factory)
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            cluster = await registry.ensure_cluster(
                "cluster", project_id="project", project_root_locator=str(tmp_path / "project")
            )
            unit = cluster._unit
            actor = await spawn(unit)
            existing = actor._context_manager.ability_content.mcp_service
            original = unit._startup_materializer.bind_run

            async def fail_after_bind(agent, materialization):
                await original(agent, materialization)
                raise RuntimeError("注入注册失败")

            monkeypatch.setattr(unit._startup_materializer, "bind_run", fail_after_bind)
            result = await unit.handle_command(
                "spawn_agent",
                {
                    "config": {"name": "failed", "agent_id": "b" * 32},
                    "manifest_ref": "content.reader",
                },
                None,
            )
            assert not result["success"]
            assert unit._mcp_deployment._agents == {existing}
            assert not existing._closed
            await unit.stop()
            assert existing._closed and not unit._mcp_deployment._agents
        finally:
            await registry.stop()
