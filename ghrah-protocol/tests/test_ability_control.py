# SPDX-License-Identifier: Apache-2.0
"""能力管理面 REST 契约的模型测试（字段、默认值、互斥与错误体）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiErrorResponse,
    AbilityApiFeatures,
    AbilityApiStatus,
    AbilityMcpProbeRequest,
    AbilityMcpProbeView,
    AbilityMcpSnapshotView,
    AbilityPackageCatalogView,
    AbilityPackageGroupProjectView,
    AbilityPackageGroupsView,
    AbilityPackageGroupVersionView,
    AbilityPackageGroupView,
    AbilityPackageVersionProfileView,
    AbilityTreeChangeResult,
)
from ghrah.protocol.agent_control import AgentCreateRequest, AgentSpecPatch


def _tree_payload() -> dict[str, object]:
    return {
        "format": "ghrah.ability-tree.v2",
        "project_id": "project",
        "agent_id": "alice",
        "run_id": "run-1",
        "revision": 3,
        "abilities": [],
        "bound_tools": [],
        "runtime": None,
    }


def test_status_defaults_are_explicit_and_legacy_is_marked() -> None:
    status = AbilityApiStatus(ability_source="legacy", legacy_deprecated=True)
    assert status.format == "ghrah.ability-control.v1"
    assert status.api_version == 1
    assert status.features.tree is True
    assert status.features.packages_install is False
    assert status.legacy_deprecated is True


def test_status_rejects_unknown_source_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AbilityApiStatus(ability_source="unknown")
    with pytest.raises(ValidationError):
        AbilityApiStatus(ability_source="builtin", surprise=True)


def test_admin_view_parses_minimal_tree_projection() -> None:
    view = AbilityAdminView.model_validate(
        {
            "format": "ghrah.ability-control.admin.v1",
            "project_id": "project",
            "agent_id": "alice",
            "revision": 3,
            "run_id": "run-1",
            "lifecycle": {"state": "stopped"},
            "grant_policy": None,
            "grants": [],
            "bound_tools": [],
            "surface": {"mode": "auto", "static": [], "effective": []},
            "changes_cursor": 2,
        }
    )
    assert view.lifecycle.requires_respawn is False
    assert view.surface.current_runtime is None
    assert view.changes_cursor == 2


def test_admin_view_rejects_negative_revision() -> None:
    with pytest.raises(ValidationError):
        AbilityAdminView.model_validate(
            {
                "project_id": "project",
                "agent_id": "alice",
                "revision": -1,
                "lifecycle": {"state": "running"},
            }
        )


def test_tree_change_result_allows_stopped_view_and_validates_when_present() -> None:
    result = AbilityTreeChangeResult.model_validate({"view": _tree_payload(), "revision": 3})
    assert result.view is not None
    assert result.view.agent_id == "alice"
    # Agent 未运行时关系库仍可写，view 明确为 null（不伪造 run 视图）。
    stopped = AbilityTreeChangeResult.model_validate({"revision": 3})
    assert stopped.view is None


def test_package_catalog_and_agent_patch_reject_unknown_fields() -> None:
    catalog = AbilityPackageCatalogView(installed_available=False)
    assert catalog.format == "ghrah.ability-control.packages.v1"
    assert catalog.packages == []
    with pytest.raises(ValidationError):
        AbilityPackageCatalogView(installed_available=False, root="/tmp")
    created = AgentCreateRequest(expected_revision=1, name="reviewer", cluster_id="clu")
    assert created.desired_state == "stopped"
    with pytest.raises(ValidationError):
        AgentSpecPatch(expected_revision=1)
    patched = AgentSpecPatch(expected_revision=2, system_prompt="next")
    assert patched.system_prompt == "next"


def test_package_groups_share_one_shape_across_scopes() -> None:
    distribution = AbilityPackageGroupsView(scope="distribution", installed_available=False)
    assert distribution.format == "ghrah.ability-control.package-groups.v1"
    assert distribution.groups == []
    with pytest.raises(ValidationError):
        AbilityPackageGroupsView(scope="distribution", installed_available=False, root="/tmp")
    with pytest.raises(ValidationError):
        AbilityPackageGroupsView(scope="distribution", installed_available=False, project_id="proj")
    project = AbilityPackageGroupsView(
        scope="project",
        installed_available=False,
        project_id="proj",
        groups=[
            AbilityPackageGroupView(
                full_name="sample.reader",
                versions=[
                    AbilityPackageGroupVersionView(
                        version="1",
                        origin="builtin",
                        lock_state="builtin",
                        project=AbilityPackageGroupProjectView(committed=False),
                    )
                ],
            )
        ],
    )
    assert project.groups[0].versions[0].agent is None
    with pytest.raises(ValidationError):
        AbilityPackageGroupsView(
            scope="agent",
            installed_available=False,
            project_id="proj",
            agent_id="a1",
            groups=project.groups,
        )


def test_error_response_requires_code() -> None:
    parsed = AbilityApiErrorResponse.model_validate(
        {"error": {"code": "conflict", "message": "revision changed"}}
    )
    assert parsed.error.code == "conflict"
    with pytest.raises(ValidationError):
        AbilityApiErrorResponse.model_validate({"error": {"message": "no code"}})


def _profile_payload() -> dict[str, object]:
    """一个内置 source 包的版本画像载荷：一个操作 + 一个技能（含附件）。"""
    return {
        "namespace": "ghrah",
        "name": "fs",
        "full_name": "ghrah.fs",
        "version": "0.1.0",
        "origin": "builtin",
        "definition_origin": "source",
        "title": "File system",
        "description": "Read and write files",
        "lock_state": "builtin",
        "operations": [
            {
                "key": "read_file",
                "title": "Read file",
                "description": "Read a file from an allowed root",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "effects": ["fs_read"],
                "tags": ["fs"],
                "resources": {},
                "permissions": {"fs_read_only": True},
                "expose": True,
                "execution": {
                    "mode": "request",
                    "timeout_ms": 30000,
                    "cancellation": "cooperative",
                    "on_revoke": "drain",
                    "retry": "never",
                },
                "entry": {"path": "operations/read_file.py", "media_type": "text/x-python"},
            }
        ],
        "skills": [
            {
                "key": "guide",
                "title": "Guide",
                "description": "How to use the package",
                "body": {"path": "skills/guide/SKILL.md", "media_type": "text/markdown"},
                "assets": [
                    {
                        "key": "diagram",
                        "path": "skills/guide/assets/diagram.png",
                        "media_type": "image/png",
                        "size": 12,
                    }
                ],
            }
        ],
    }


def test_package_version_profile_parses_member_details() -> None:
    profile = AbilityPackageVersionProfileView.model_validate(_profile_payload())
    assert profile.format == "ghrah.ability-control.package-version.v1"
    assert profile.definition_origin == "source"
    assert profile.verify is None and profile.mcp is None
    operation = profile.operations[0]
    assert operation.key == "read_file"
    assert operation.expose is True
    assert operation.execution.timeout_ms == 30000
    assert operation.mcp is None
    assert operation.entry is not None
    assert operation.entry.path == "operations/read_file.py"
    assert operation.entry.size is None
    skill = profile.skills[0]
    assert skill.body.path == "skills/guide/SKILL.md"
    assert skill.assets[0].key == "diagram"
    assert skill.assets[0].size == 12
    assert skill.references == [] and skill.scripts == []


def test_package_version_profile_degrades_and_rejects_absolute_paths_field() -> None:
    degraded = AbilityPackageVersionProfileView.model_validate(
        {
            "namespace": "sample",
            "name": "reader",
            "full_name": "sample.reader",
            "version": "1.2.3",
            "origin": "installed",
            "lock_state": "present",
            "problem": "definition_unreadable",
        }
    )
    assert degraded.definition_origin is None
    assert degraded.operations == [] and degraded.skills == []
    # 画像不允许携带安装根/包目录绝对路径这类字段。
    with pytest.raises(ValidationError):
        AbilityPackageVersionProfileView.model_validate(
            {**_profile_payload(), "root": "/home/user/.ghrah/ability"}
        )


def test_package_version_profile_carries_mcp_snapshot_health() -> None:
    profile = AbilityPackageVersionProfileView.model_validate(
        {
            **_profile_payload(),
            "origin": "installed",
            "definition_origin": "mcp",
            "lock_state": "present",
            "mcp": {
                "server": "fetch",
                "fetched_at": "2026-11-12T00:00:00Z",
                "stale": False,
            },
        }
    )
    assert profile.mcp is not None
    assert profile.mcp.server == "fetch"
    assert profile.mcp.last_error is None
    with pytest.raises(ValidationError):
        AbilityMcpSnapshotView.model_validate(
            {
                "server": "fetch",
                "fetched_at": "2026-11-12T00:00:00Z",
                "stale": False,
                "url": "https://example.com/mcp",
            }
        )


def test_mcp_probe_request_defaults_and_bounds() -> None:
    request = AbilityMcpProbeRequest()
    assert request.timeout_ms == 5000
    assert request.include_resources is False
    assert AbilityMcpProbeRequest(timeout_ms=1000).timeout_ms == 1000
    assert AbilityMcpProbeRequest(timeout_ms=10000).timeout_ms == 10000
    for value in (999, 10001):
        with pytest.raises(ValidationError):
            AbilityMcpProbeRequest(timeout_ms=value)
    with pytest.raises(ValidationError):
        AbilityMcpProbeRequest(url="https://example.com/mcp")


def _probe_payload() -> dict[str, object]:
    return {
        "namespace": "mcp",
        "name": "fetch",
        "version": "1.0.0",
        "server": "fetch",
        "ok": True,
        "phase": "list_tools",
        "elapsed_ms": 12,
        "protocol_version": "2025-06-18",
        "server_info": {"name": "remote", "version": "1.2.3"},
        "tools": [
            {
                "name": "echo",
                "title": "Echo",
                "input_schema": {"type": "object"},
                "output_schema": None,
            }
        ],
        "tool_count": 1,
        "resources_supported": False,
        "resource_count": 0,
    }


def test_mcp_probe_view_keeps_ok_and_error_in_sync() -> None:
    probe = AbilityMcpProbeView.model_validate(_probe_payload())
    assert probe.format == "ghrah.ability-control.mcp-probe.v1"
    assert probe.transport == "streamable_http"
    assert probe.error is None
    assert probe.server_info is not None and probe.server_info.name == "remote"
    assert probe.tools[0].name == "echo" and probe.tools[0].output_schema is None
    assert probe.truncated is False

    failed = AbilityMcpProbeView.model_validate(
        {
            **_probe_payload(),
            "ok": False,
            "phase": "connect",
            "error": {"reason": "timeout", "message": "probe timed out"},
        }
    )
    assert failed.error is not None
    assert failed.error.reason == "timeout"
    assert failed.error.http_status is None

    with pytest.raises(ValidationError):
        AbilityMcpProbeView.model_validate(
            {
                **_probe_payload(),
                "error": {"reason": "internal", "message": "unexpected"},
            }
        )
    with pytest.raises(ValidationError):
        AbilityMcpProbeView.model_validate({**_probe_payload(), "ok": False})
    with pytest.raises(ValidationError):
        AbilityMcpProbeView.model_validate(
            {
                **_probe_payload(),
                "ok": False,
                "error": {"reason": "unknown_reason", "message": "x"},
            }
        )
    with pytest.raises(ValidationError):
        AbilityMcpProbeView.model_validate({**_probe_payload(), "url": "https://example.com/mcp"})


def test_mcp_probe_resources_are_separate_from_support_flag() -> None:
    empty = AbilityMcpProbeView.model_validate({**_probe_payload(), "resources_supported": True})
    assert empty.resources_supported is True
    assert empty.resources == [] and empty.resource_count == 0


def test_features_mcp_probe_defaults_off() -> None:
    assert AbilityApiFeatures().mcp_probe is False
    assert AbilityApiFeatures(mcp_probe=True).mcp_probe is True
