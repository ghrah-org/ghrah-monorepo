"""解析预览与 Core 启动解析同源；草稿解析不得写盘。"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from ghrah.core.unit import CoreUnitConfig, create_core_unit
from ghrah.manifest.parser import parse_agent_manifest

from ghrah.subject.config import SubjectConfig
from ghrah.subject.manifest_store.service import handle_manifest_command
from ghrah.subject.manifest_store.store import ManifestStore
from ghrah.subject.unit.base import CommandContext
from ghrah.subject.units.manifest_store import ManifestStoreUnit

AGENT = """manifest: agent
version: "2"
metadata:
  namespace: preview
  name: consumer
model:
  endpoint_name: offline-model
  temperature: 0.2
  extra_params: {seed: 42}
system_prompt: Saved prompt
abilities:
  - type: conversation
  - ref: ghrah.fs.read_file
    permissions:
      require_hitl: true
      allowed_paths: [/tmp/docs]
"""

SHADOW = """manifest: ability
version: "1"
metadata:
  namespace: ghrah.fs
  name: read_file
  description: Legacy store shadow
  permissions: {}
tool:
  name: shadow_contract
  description: A deliberately different legacy contract
  parameters: {}
implementation:
  type: builtin
  handler: conversation
"""


def _files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*.yaml")}


@pytest.mark.parametrize("mode", ["legacy", "builtin"])
async def test_unit_preview_matches_the_core_startup_resolver(tmp_path: Path, mode: str) -> None:
    config = SubjectConfig(
        manifest_root=str(tmp_path / "manifests"),
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        ability_source=mode,
    )
    unit = ManifestStoreUnit(config)
    await unit.init(SimpleNamespace(provide=lambda *_args: None))
    store = unit.service
    store.put_agent("preview.consumer", AGENT)
    store.put_ability("ghrah.fs.read_file", SHADOW, overwrite=True)
    before = _files(tmp_path)
    draft = AGENT.replace("Saved prompt", "Unsaved draft prompt")
    response = await unit.handle_command(
        "manifest_resolve_agent",
        {
            "agent_full_name": "preview.consumer",
            "runtime_name": "preview_runtime",
            "content": draft,
            # 模式只能由受信 Subject 配置选择，不能由载荷切换。
            "ability_source": "legacy" if mode == "builtin" else "builtin",
        },
        CommandContext.internal(),
    )
    assert response["success"], response
    data = response["data"]
    assert data["ability_source"] == mode
    assert data["input_source"] == "draft"
    assert data["config"]["system_prompt"] == "Unsaved draft prompt"
    assert data["abilities"][1]["implementation"]["handler"] == (
        "conversation" if mode == "legacy" else "read_file"
    )
    assert data["abilities"][1]["permissions"]["require_hitl"] is True
    assert data["abilities"][1]["permissions"]["allowed_paths"] == ["/tmp/docs"]

    core = create_core_unit(
        CoreUnitConfig(project_id="default", manifest_store=store, ability_source=mode)
    )
    await core.init(object())
    try:
        # 直接使用实际 CoreUnit 初始化后持有的启动解析器，不自建第二套解析规则。
        resolved = (
            core._require_assembly()
            .manifest_resolver()
            .resolve(parse_agent_manifest(draft), runtime_name="preview_runtime")
        )
        expected = asdict(resolved.config)
        assert data["config"] == {key: expected[key] for key in data["config"]}
        for actual, ability in zip(data["abilities"], resolved.abilities, strict=True):
            assert actual["ability_name"] == ability.ability_name
            assert actual["permissions"] == ability.permissions.model_dump()
            assert actual["tool_schema"] == (
                ability.tool_schema.model_dump() if ability.tool_schema else None
            )
            assert actual["implementation"] == ability.implementation.model_dump()
    finally:
        await core.stop()
    assert _files(tmp_path) == before


def test_stored_preview_remains_available_without_draft_content(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path)
    store.put_agent("preview.consumer", AGENT)
    store.put_ability("ghrah.fs.read_file", SHADOW)
    response = handle_manifest_command(
        "manifest_resolve_agent", {"agent_full_name": "preview.consumer"}, store
    )
    assert response["success"], response
    assert response["data"]["input_source"] == "stored"
    assert response["data"]["config"]["system_prompt"] == "Saved prompt"


@pytest.mark.parametrize("mode", ["legacy", "builtin"])
def test_unknown_references_fail_and_do_not_write(tmp_path: Path, mode: str) -> None:
    store = ManifestStore(tmp_path)
    store.put_agent("preview.consumer", AGENT)
    before = _files(tmp_path)
    draft = AGENT.replace("ghrah.fs.read_file", "unknown.missing.ability")
    response = handle_manifest_command(
        "manifest_resolve_agent",
        {"agent_full_name": "preview.consumer", "content": draft},
        store,
        ability_source=mode,
    )
    assert response["success"] is False
    assert "unknown.missing.ability" in response["error"]
    assert _files(tmp_path) == before


def test_builtin_does_not_fall_back_to_legacy_store(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path)
    store.put_ability(
        "legacy.only.ability",
        SHADOW.replace("namespace: ghrah.fs", "namespace: legacy.only").replace(
            "name: read_file", "name: ability"
        ),
    )
    draft = AGENT.replace("ghrah.fs.read_file", "legacy.only.ability")
    for mode, success in (("legacy", True), ("builtin", False)):
        response = handle_manifest_command(
            "manifest_resolve_agent",
            {"agent_full_name": "preview.consumer", "content": draft},
            store,
            ability_source=mode,
        )
        assert response["success"] is success, response


@pytest.mark.parametrize("content", ["manifest: [", "", AGENT.replace("consumer", "another")])
def test_invalid_or_mismatched_drafts_are_rejected(tmp_path: Path, content: str) -> None:
    response = handle_manifest_command(
        "manifest_resolve_agent",
        {"agent_full_name": "preview.consumer", "content": content},
        ManifestStore(tmp_path),
    )
    assert response["success"] is False
    assert _files(tmp_path) == {}


def test_invalid_mode_fails_instead_of_guessing(tmp_path: Path) -> None:
    response = handle_manifest_command(
        "manifest_resolve_agent",
        {"agent_full_name": "preview.consumer", "content": AGENT},
        ManifestStore(tmp_path),
        ability_source="unsupported",
    )
    assert response["success"] is False
    assert "unknown ability source mode" in response["error"]
