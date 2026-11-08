# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path

from ghrah.ability.package.installer import AbilityInstaller, InstallOrigin
from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import mount_unit
from ghrah.subject.unit.base import CommandContext
from ghrah.subject.units._helpers import (
    manifest_command_to_event,
    manifest_result_to_event_payload,
)
from ghrah.subject.units.manifest_store import ManifestStoreUnit

CUSTOM_ABILITY_YAML = """\
manifest: ability
version: "1"
metadata:
  namespace: custom
  name: needs_hitl
  description: Needs approval
  permissions:
    require_hitl: true
tool:
  name: needs_hitl
  description: Needs approval
  parameters: {}
implementation:
  type: builtin
  handler: custom_needs_hitl
"""

AGENT_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: agents
  name: reader
model:
  endpoint_name: reader
system_prompt: Read the current session.
abilities:
  - type: conversation
  - ref: ghrah.core.session_info
"""


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
    )


def test_manifest_command_to_event_maps_only_crud_commands() -> None:
    assert manifest_command_to_event("manifest_put_ability", {}) == ("manifest_ability_created")
    assert manifest_command_to_event("manifest_put_ability", {"overwrite": True}) == (
        "manifest_ability_updated"
    )
    assert manifest_command_to_event("manifest_delete_ability", {}) == ("manifest_ability_deleted")
    assert manifest_command_to_event("manifest_put_agent", {}) == "manifest_agent_created"
    assert manifest_command_to_event("manifest_delete_agent", {}) == "manifest_agent_deleted"
    assert manifest_command_to_event("manifest_list_abilities", {}) is None
    assert manifest_command_to_event("manifest_validate", {}) is None


def test_manifest_result_to_event_payload_keeps_wire_shape() -> None:
    result = {
        "success": True,
        "data": {
            "full_name": "custom.needs_hitl",
            "manifest": {"manifest": "ability"},
            "source": "manifest: ability",
        },
    }

    assert manifest_result_to_event_payload(result) == {
        "full_name": "custom.needs_hitl",
        "namespace": "custom",
        "manifest": {"manifest": "ability"},
        "source": "manifest: ability",
    }


async def test_builtin_manifest_unit_uses_v1_sources_and_leaves_legacy_files_untouched(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.ability_source = "builtin"
    install_root = tmp_path / "installed"
    config.ability.install_root = str(install_root)
    root = Path(config.manifest.manifest_root)
    unit = ManifestStoreUnit(config)
    async with Context() as ctx:
        await wait_active(ctx.plugin(mount_unit(unit)))
        assert (root / "agents").is_dir()
        assert not (root / "abilities").exists()

        stale = root / "abilities" / "custom" / "needs_hitl.yaml"
        stale.parent.mkdir(parents=True)
        stale.write_text(CUSTOM_ABILITY_YAML, encoding="utf-8")
        command_ctx = CommandContext.internal()
        for command in (
            "manifest_list_abilities",
            "manifest_get_ability",
            "manifest_put_ability",
            "manifest_delete_ability",
        ):
            result = await unit.handle_command(
                command, {"full_name": "custom.needs_hitl", "content": ""}, command_ctx
            )
            assert result["error"] == "legacy_ability_manifest_disabled"
        assert stale.read_text(encoding="utf-8") == CUSTOM_ABILITY_YAML

        for content, expected in ((AGENT_YAML, True), (CUSTOM_ABILITY_YAML, False)):
            validated = await unit.handle_command(
                "manifest_validate", {"content": content, "manifest_type": "agent"}, command_ctx
            )
            assert validated["success"], validated
            assert validated["data"]["valid"] is expected

        saved = await unit.handle_command(
            "manifest_put_agent",
            {"full_name": "agents.reader", "content": AGENT_YAML},
            command_ctx,
        )
        assert saved["success"], saved
        preview = await unit.handle_command(
            "manifest_resolve_agent", {"agent_full_name": "agents.reader"}, command_ctx
        )
        assert preview["success"], preview
        assert [item["ability_name"] for item in preview["data"]["abilities"]] == [
            "ghrah.core.conversation",
            "ghrah.core.session_info",
        ]

        package = files("ghrah.subject.shipped_abilities") / "project_agent_manage"
        with as_file(package) as source:
            AbilityInstaller(install_root, origin=InstallOrigin.DISTRO).install(source)
        installed_member = "ghrah.project_agent_manage.list_project_agents"
        unit.service.put_agent(
            "agents.reader",
            AGENT_YAML.replace("ghrah.core.session_info", installed_member),
            overwrite=True,
        )
        installed = await unit.handle_command(
            "manifest_resolve_agent", {"agent_full_name": "agents.reader"}, command_ctx
        )
        assert installed["success"], installed
        assert installed["data"]["abilities"][1]["ability_name"] == installed_member

        unit.service.put_agent(
            "agents.reader",
            AGENT_YAML.replace("ghrah.core.session_info", "custom.needs_hitl"),
            overwrite=True,
        )
        rejected = await unit.handle_command(
            "manifest_resolve_agent", {"agent_full_name": "agents.reader"}, command_ctx
        )
        assert not rejected["success"]
        assert "custom" in rejected["error"]
