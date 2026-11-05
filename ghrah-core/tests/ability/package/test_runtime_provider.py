# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""安装包通过真实解析、授权、接纳执行；卸载后旧对象与再次启动均拒绝。"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

from ghrah.ability.access import (
    AbilityAccessStore,
    AccessError,
    AccessIdentity,
    GrantPolicy,
    GrantRule,
    StartupGrantMaterializer,
)
from ghrah.ability.assembly import AbilityAssembly, AbilityAssemblyError
from ghrah.ability.execution.execution_services import ExecutionServices
from ghrah.ability.execution.invocation import OperationCall
from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import AbilityInstaller
from ghrah.ability.package.provider import InstalledAbilityProvider
from ghrah.manifest.errors import ManifestValidationError
from ghrah.manifest.parser import parse_agent_manifest
from ghrah.types.results import ActionOutcome

AGENT = "a" * 32
ACTOR = AccessIdentity("project", "deployment", "control", True, "deployment")

AGENT_YAML = """\
manifest: agent
version: '2'
metadata: {namespace: demo, name: consumer, description: Installed operation consumer}
model: {endpoint_name: default}
system_prompt: Read the report.
abilities:
  - type: conversation
  - ref: acme.reports.render
"""

MODULE = """\
from pathlib import Path
from pydantic import BaseModel, ConfigDict
from ghrah.types.results import ActionResult, ActionOutcome

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str = "memo.txt"

class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str

SUMMARY = "Read a report"
TITLE = "Read report"
TAGS = []
EFFECTS = []
PERMISSIONS = {}
RESOURCES = {"paths": ["file_path"]}
TIMEOUT_MS = 1000

async def execute(call, services):
    return ActionResult(
        ActionOutcome.SUCCESS, {"text": Path(call.arguments["file_path"]).read_text()}
    )
"""


def _install(
    tmp_path: Path, module: str = MODULE, version: str = "1.0.0", *, helpers: dict | None = None
):
    source = tmp_path / f"source-{version}"
    (source / "operations").mkdir(parents=True)
    (source / "ability.yaml").write_text(
        "format: ghrah.ability-definition\nschema_version: '1'\n"
        f"identity: {{namespace: acme, name: reports, version: {version}}}\n"
        "title: Reports\ndescription: Installed reports\n",
        encoding="utf-8",
    )
    (source / "operations" / "render.py").write_text(module, encoding="utf-8")
    for name, text in (helpers or {}).items():
        (source / "operations" / name).write_text(text, encoding="utf-8")
    build(source)
    installer = AbilityInstaller(tmp_path / "installed")
    installer.install(source)
    return installer, source


@pytest.fixture
async def store(tmp_path):
    db = await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=lambda agent: "run-one",
    )
    try:
        yield db
    finally:
        await db.close()


async def _materialize(store, tmp_path, installer, *, policy=GrantPolicy()):
    assembly = AbilityAssembly.select("builtin", install_root=str(installer.root))
    resolved = assembly.manifest_resolver().resolve(parse_agent_manifest(AGENT_YAML))
    materializer = StartupGrantMaterializer(
        store=store,
        source=assembly.source,
        actor=ACTOR,
        policy=policy,
        workspace_root=str(tmp_path),
    )
    result = await materializer.materialize(agent=AGENT, resolved=resolved)
    await materializer.bind_run(AGENT, result)
    tool = next(item for item in result.abilities if item.name == "render")
    return assembly, materializer, result, tool


async def test_installed_operation_survives_source_removal_and_uninstall_invalidates(
    store, tmp_path
):
    installer, source = _install(tmp_path)
    shutil.rmtree(source)
    (tmp_path / "memo.txt").write_text("real installed result", encoding="utf-8")
    assembly, materializer, result, tool = await _materialize(store, tmp_path, installer)
    assert tool.member_reference.version_digest.startswith("sha256:")
    instance = await store.instance("acme-reports")
    assert instance["root"] == str(tmp_path)
    assert (
        instance["digest"] != installer.store.resolve("acme.reports", "1.0.0").lock.package_digest
    )
    assert (
        tool.bind_tool()["function"]["parameters"]["properties"]["file_path"]["default"]
        == "memo.txt"
    )
    response = await tool.execute(OperationCall(tool_call_id="call-one"), ExecutionServices())
    assert response.outcome == ActionOutcome.SUCCESS
    assert response.data == {"text": "real installed result"}
    assert await materializer.tool_table_projector(AGENT, result)() == ("render",)

    installer.uninstall("acme.reports", "1.0.0")
    response = await tool.execute(OperationCall(), ExecutionServices())
    assert response.outcome == ActionOutcome.FAILURE
    assert "not available" in response.data["error"]
    assert await materializer.tool_table_projector(AGENT, result)() == ()
    view = await store.view(ACTOR, AGENT)
    assert view.abilities[0].operations[0].availability == "unavailable"
    assert await store.grants(ACTOR, AGENT)  # 授予事实和审计保留。
    with pytest.raises(ManifestValidationError, match="版本不存在"):
        await materializer.materialize(
            agent=AGENT,
            resolved=assembly.manifest_resolver().resolve(parse_agent_manifest(AGENT_YAML)),
        )


@pytest.mark.parametrize(
    "arguments", [{"file_path": "../escape.txt"}, {"file_path": 42}, {"extra": 1}]
)
async def test_path_escape_and_invalid_input_have_no_effect(store, tmp_path, arguments):
    installer, _ = _install(tmp_path)
    _, _, _, tool = await _materialize(store, tmp_path, installer)
    response = await tool.execute(OperationCall(arguments=arguments), ExecutionServices())
    assert response.outcome == ActionOutcome.FAILURE
    assert not (tmp_path.parent / "escape.txt").exists()


async def test_effective_denied_path_is_checked_before_approval(store, tmp_path):
    module = MODULE.replace("PERMISSIONS = {}", 'PERMISSIONS = {"denied_paths": ["secret"]}')
    installer, _ = _install(tmp_path, module)
    _, _, _, tool = await _materialize(store, tmp_path, installer)
    response = tool.check_call(
        OperationCall(arguments={"file_path": "secret/data.txt"}), ExecutionServices()
    )
    assert response.outcome == ActionOutcome.FAILURE
    assert "denied paths" in response.data["error"]


async def test_symlink_escape_is_rejected(store, tmp_path):
    installer, _ = _install(tmp_path)
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    _, _, _, tool = await _materialize(store, tmp_path, installer)
    response = await tool.execute(
        OperationCall(arguments={"file_path": "escape/file.txt"}), ExecutionServices()
    )
    assert response.outcome == ActionOutcome.FAILURE


async def test_no_policy_does_not_grant_or_import(store, tmp_path):
    marker = tmp_path / "imported"
    installer, _ = _install(tmp_path, MODULE + f"\nPath({str(marker)!r}).touch()\n")
    marker.unlink()  # build 在发布侧 import；安装和声明解析不 import。
    assembly = AbilityAssembly.select("builtin", install_root=str(installer.root))
    resolved = assembly.manifest_resolver().resolve(parse_agent_manifest(AGENT_YAML))
    assert not marker.exists()
    materializer = StartupGrantMaterializer(
        store=store, source=assembly.source, actor=ACTOR, policy=None, workspace_root=str(tmp_path)
    )
    with pytest.raises(Exception, match="未配置启动授权"):
        await materializer.materialize(agent=AGENT, resolved=resolved)
    assert not marker.exists()


@pytest.mark.parametrize(
    "entry", ["def execute(call, services):", "async def execute(other, services):"]
)
async def test_invalid_entry_is_refused_at_runtime(store, tmp_path, entry):
    installer, _ = _install(tmp_path, MODULE.replace("async def execute(call, services):", entry))
    with pytest.raises(ManifestValidationError, match="execute"):
        await _materialize(store, tmp_path, installer)


async def test_bad_output_is_failure(store, tmp_path):
    module = MODULE.replace(
        '{"text": Path(call.arguments["file_path"]).read_text()}', '{"wrong": 1}'
    )
    installer, _ = _install(tmp_path, module)
    _, _, _, tool = await _materialize(store, tmp_path, installer)
    response = await tool.execute(OperationCall(), ExecutionServices())
    assert response.outcome == ActionOutcome.FAILURE
    assert "Output" in response.data["error"]


async def test_timeout_and_cancellation_propagation(store, tmp_path):
    module = MODULE.replace("TIMEOUT_MS = 1000", "TIMEOUT_MS = 10").replace(
        "async def execute(call, services):",
        "async def execute(call, services):\n    import asyncio\n    await asyncio.sleep(10)",
    )
    installer, _ = _install(tmp_path, module)
    _, _, _, tool = await _materialize(store, tmp_path, installer)
    response = await tool.execute(OperationCall(), ExecutionServices())
    assert "effects may have occurred" in response.data["error"]
    task = asyncio.create_task(tool.execute(OperationCall(), ExecutionServices()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("damage", ["lock", "code"])
def test_missing_lock_or_tampered_code_is_refused(tmp_path, damage):
    installer, _ = _install(tmp_path)
    item = installer.store.resolve("acme.reports", "1.0.0")
    if damage == "lock":
        item.lock_path.unlink()
    else:
        (item.root / "operations" / "render.py").write_text("raise RuntimeError('tampered')")
    with pytest.raises(ManifestValidationError, match="校验失败"):
        InstalledAbilityProvider(installer.root).capture("acme.reports")


def test_multiple_versions_require_selection_and_do_not_replace_existing_binding(tmp_path):
    installer, _ = _install(tmp_path)
    provider = InstalledAbilityProvider(installer.root)
    original = provider.capture("acme.reports")
    _install(tmp_path, version="2.0.0")
    assert provider.capture("acme.reports").digest == original.digest
    fresh = InstalledAbilityProvider(installer.root)
    with pytest.raises(ManifestValidationError, match="显式 select_version"):
        fresh.capture("acme.reports")
    fresh.select_version("acme.reports", "2.0.0")
    assert fresh.capture("acme.reports").definition().identity.version == "2.0.0"


def test_install_root_is_explicit_and_rejected_in_legacy(tmp_path):
    with pytest.raises(AbilityAssemblyError, match="builtin"):
        AbilityAssembly.select("legacy", install_root=str(tmp_path))
    with pytest.raises(ValueError, match="绝对路径"):
        AbilityAssembly.select("builtin", install_root="relative")
    assert AbilityAssembly.select("builtin").source.packages is None


async def test_helper_imports_are_isolated_between_versions(tmp_path):
    module = (
        MODULE.replace(
            '{"text": Path(call.arguments["file_path"]).read_text()}', '{"text": MARKER}'
        )
        + "\nfrom _shared import MARKER\n"
    )
    installer, _ = _install(tmp_path, module, helpers={"_shared.py": "MARKER = 'one'\n"})
    _install(tmp_path, module, version="2.0.0", helpers={"_shared.py": "MARKER = 'two'\n"})
    provider = InstalledAbilityProvider(installer.root)
    previous = sys.modules.get("_shared")
    before = list(sys.path)
    for version, expected in (("1.0.0", "one"), ("2.0.0", "two")):
        captured = provider.capture("acme.reports", version=version)
        tool = provider.create_operation("acme.reports", "render", captured.digest)
        result = await tool.execute(OperationCall(), ExecutionServices())
        assert result.data == {"text": expected}
    assert sys.path == before
    assert sys.modules.get("_shared") is previous


async def test_nested_input_schema_is_preserved_in_authorized_tool(store, tmp_path):
    module = MODULE.replace(
        "class Input(BaseModel):",
        'class Options(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n'
        "    count: int\n\nclass Input(BaseModel):",
    ).replace(
        '    file_path: str = "memo.txt"',
        '    file_path: str = "memo.txt"\n    options: Options | None = None',
    )
    installer, _ = _install(tmp_path, module)
    (tmp_path / "memo.txt").write_text("nested", encoding="utf-8")
    _, _, _, tool = await _materialize(store, tmp_path, installer)
    assert "Options" in tool.bind_tool()["function"]["parameters"]["$defs"]
    result = await tool.execute(
        OperationCall(arguments={"options": {"count": 1}}), ExecutionServices()
    )
    assert result.data == {"text": "nested"}


async def test_installed_content_only_package_uses_granted_content_service(store, tmp_path):
    source = tmp_path / "source"
    shutil.copytree(Path(__file__).parent / "fixtures/builder", source)
    build(source)
    installer = AbilityInstaller(tmp_path / "installed")
    installer.install(source)
    shutil.rmtree(source)
    assembly = AbilityAssembly.select("builtin", install_root=str(installer.root))
    resolved = assembly.manifest_resolver().resolve(
        parse_agent_manifest(AGENT_YAML.replace("  - ref: acme.reports.render\n", ""))
    )
    materializer = StartupGrantMaterializer(
        store=store,
        source=assembly.source,
        actor=ACTOR,
        policy=GrantPolicy(rules=(GrantRule(package="fixtures.builder"),)),
        workspace_root=str(tmp_path),
    )
    result = await materializer.materialize(agent=AGENT, resolved=resolved)
    await materializer.bind_run(AGENT, result)
    content = result.abilities.ability_content
    member = "skill/ability-package-development-guide"
    assert (await content.read("builder", member)).content
    installer.uninstall("fixtures.builder", "0.1.0")
    with pytest.raises(AccessError) as refused:
        await content.read("builder", member)
    assert refused.value.code == "unavailable"
    assert (await store.view(ACTOR, AGENT)).abilities[0].availability == "unavailable"


async def test_missing_lock_rejects_reselection_of_existing_instance(store, tmp_path):
    installer, _ = _install(tmp_path)
    await _materialize(store, tmp_path, installer)
    instance = await store.instance("acme-reports")
    installer.store.resolve("acme.reports", "1.0.0").lock_path.unlink()
    with pytest.raises(AccessError) as refused:
        await store.select_instance(
            ACTOR,
            "acme-reports",
            instance["digest"],
            tmp_path,
            expected_revision=instance["revision"],
        )
    assert refused.value.code == "unavailable"


async def test_external_admission_without_provider_stays_closed(store, tmp_path):
    from ghrah.protocol.ability_tree import GrantAbilityRequest

    installer, _ = _install(tmp_path)
    version = InstalledAbilityProvider(installer.root).capture("acme.reports")
    await store.commit_version(ACTOR, version)
    await store.select_instance(ACTOR, "reports", version.digest, tmp_path)
    await store.ensure_agent(ACTOR, AGENT)
    await store.grant(
        ACTOR,
        AGENT,
        GrantAbilityRequest(
            alias="reports",
            instance_id="reports",
            whole_container=True,
            allowed_roots=[str(tmp_path)],
            expected_revision=0,
        ),
    )
    actor = AccessIdentity("project", AGENT, "run-one")
    fence = await store.resolve(actor, "reports", "render")
    with pytest.raises(AccessError) as refused:
        await store.admit(actor, fence, {"file_path": "memo.txt"})
    assert refused.value.code == "unavailable"


async def test_external_package_cannot_shadow_builtin_alias(store, tmp_path):
    """两个命名空间里的同名包不能覆盖同一个 Agent 局部别名。"""
    from ghrah.ability.access import StartupGrantError

    installer, source = _install(tmp_path)
    definition = source / "ability.yaml"
    definition.write_text(definition.read_text().replace("name: reports", "name: fs"))
    build(source)
    installer.install(source)
    assembly = AbilityAssembly.select("builtin", install_root=str(installer.root))
    manifest = parse_agent_manifest(
        AGENT_YAML.replace(
            "  - ref: acme.reports.render", "  - ref: acme.fs.render\n  - ref: ghrah.fs.read_file"
        )
    )
    materializer = StartupGrantMaterializer(
        store=store,
        source=assembly.source,
        actor=ACTOR,
        policy=GrantPolicy(),
        workspace_root=str(tmp_path),
    )
    with pytest.raises(StartupGrantError, match="别名冲突"):
        await materializer.materialize(
            agent=AGENT, resolved=assembly.manifest_resolver().resolve(manifest)
        )
