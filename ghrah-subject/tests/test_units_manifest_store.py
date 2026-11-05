# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from typing import Any

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


async def test_manifest_store_unit_refreshes_permission_index_after_crud(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    unit = ManifestStoreUnit(config)
    emitted: list[tuple[str, Any]] = []

    async with Context() as ctx:
        # 同步 collector：ctx.emit 内联调用，命令返回后即完成
        ctx.on(
            "event/manifest_ability_created",
            lambda payload: emitted.append(("manifest_ability_created", payload)),
        )

        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)
        permission_index = unit.permission_index

        assert "custom_needs_hitl" not in permission_index.get_permissions()

        put_result = await unit.handle_command(
            "manifest_put_ability",
            {"full_name": "custom.needs_hitl", "content": CUSTOM_ABILITY_YAML},
            CommandContext.internal(),
        )

        permissions = permission_index.get_permissions()
        assert put_result["success"] is True
        assert permissions["custom_needs_hitl"].require_hitl is True
        assert (
            "manifest_ability_created",
            {
                "full_name": "custom.needs_hitl",
                "namespace": "custom",
                "manifest": put_result["data"]["manifest"],
                "source": put_result["data"]["source"],
            },
        ) in emitted

        delete_result = await unit.handle_command(
            "manifest_delete_ability",
            {"full_name": "custom.needs_hitl"},
            CommandContext.internal(),
        )

        assert delete_result["success"] is True
        assert "custom_needs_hitl" not in permission_index.get_permissions()
