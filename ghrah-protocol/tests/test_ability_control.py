# SPDX-License-Identifier: Apache-2.0
"""能力管理面 REST 契约的模型测试（字段、默认值、互斥与错误体）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiErrorResponse,
    AbilityApiStatus,
    AbilityTreeChangeResult,
)


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


def test_error_response_requires_code() -> None:
    parsed = AbilityApiErrorResponse.model_validate(
        {"error": {"code": "conflict", "message": "revision changed"}}
    )
    assert parsed.error.code == "conflict"
    with pytest.raises(ValidationError):
        AbilityApiErrorResponse.model_validate({"error": {"message": "no code"}})
