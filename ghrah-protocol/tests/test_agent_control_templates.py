# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 模板 wire 契约：默认值、严格字段、互斥约束与 patch 语义。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ghrah.protocol.agent_control import (
    AgentTemplateArchiveRequest,
    AgentTemplateContextPolicyView,
    AgentTemplateCreateRequest,
    AgentTemplateDetailView,
    AgentTemplateListView,
    AgentTemplateMemberView,
    AgentTemplateMetadataView,
    AgentTemplatePathRequestView,
    AgentTemplatePermissionRequestView,
    AgentTemplateReferencesView,
    AgentTemplateRevisionsView,
    AgentTemplateUpdateRequest,
)

MINIMAL_METADATA = {"namespace": "writing", "name": "weekly_report"}


def test_create_request_only_requires_metadata() -> None:
    change = AgentTemplateCreateRequest.model_validate({"metadata": MINIMAL_METADATA})
    assert change.metadata.title == ""
    assert change.metadata.tags == []
    assert change.metadata.deprecated is False
    # 省略的组保持 None（由服务端补默认值）；path_request 是列表默认
    assert change.model is None
    assert change.prompt is None
    assert change.context_policy is None
    assert change.available_members is None
    assert change.permission_request is None
    assert change.path_request == []


def test_context_policy_defaults_to_unlimited_iterations() -> None:
    policy = AgentTemplateContextPolicyView.model_validate({})
    assert policy.max_iterations == -1
    assert policy.communication_timeout == 300
    assert policy.window is None
    assert policy.overrides is None
    assert (
        AgentTemplateContextPolicyView.model_validate({"max_iterations": 200}).max_iterations == 200
    )
    with pytest.raises(ValidationError):
        AgentTemplateContextPolicyView.model_validate({"max_iterations": -2})


def test_member_view_rejects_bad_shapes() -> None:
    member = AgentTemplateMemberView.model_validate(
        {"namespace": "a", "name": "b", "member": "a.b.c"}
    )
    assert member.member == "a.b.c"
    with pytest.raises(ValidationError):
        AgentTemplateMemberView.model_validate({"namespace": "a", "name": "b", "member": ""})
    with pytest.raises(ValidationError):
        AgentTemplateMemberView.model_validate({"namespace": "a", "name": "b", "member": "a.b.c", "unknown": 1})


def test_permission_request_defaults() -> None:
    permission = AgentTemplatePermissionRequestView.model_validate({})
    assert permission.auto_approve == []
    assert permission.require_approval_by_default is True
    assert permission.safe_extra_commands == []
    assert permission.safe_extra_sub_commands is None
    pairs = AgentTemplatePermissionRequestView.model_validate(
        {"safe_extra_sub_commands": [["git", ["status", "diff"]]]}
    )
    assert pairs.safe_extra_sub_commands == [("git", ["status", "diff"])]


def test_unknown_fields_are_rejected_everywhere() -> None:
    with pytest.raises(ValidationError):
        AgentTemplateMetadataView.model_validate({**MINIMAL_METADATA, "unknown": 1})
    with pytest.raises(ValidationError):
        AgentTemplateCreateRequest.model_validate({"metadata": MINIMAL_METADATA, "unknown": True})
    with pytest.raises(ValidationError):
        AgentTemplateMemberView.model_validate(
            {"namespace": "a", "name": "b", "member": "a.b.c", "unknown": 1}
        )


def test_namespace_and_name_patterns() -> None:
    for metadata in (
        {"namespace": "Writing", "name": "weekly_report"},
        {"namespace": "writing", "name": "weekly-report"},
        {"namespace": "w", "name": "weekly_report"},
        {"namespace": "writing.", "name": "weekly_report"},
    ):
        with pytest.raises(ValidationError):
            AgentTemplateMetadataView.model_validate(metadata)


def test_three_segment_member_names_round_trip() -> None:
    members = [
        AgentTemplateMemberView.model_validate(
            {
                "namespace": "ghrah",
                "name": "fs",
                "member": "ghrah.fs.read_file",
                "type": "operation",
                "ability_type": "kernel",
            }
        ),
        AgentTemplateMemberView.model_validate(
            {
                "namespace": "research",
                "name": "research",
                "member": "research.research.research",
                "type": "skill",
                "version": "0.1.0",
            }
        ),
    ]
    assert [item.member for item in members] == [
        "ghrah.fs.read_file",
        "research.research.research",
    ]
    assert members[0].version is None
    assert members[1].version == "0.1.0"


def test_path_request_scope_and_subpath_rules() -> None:
    base = {
        "workspace_role": "default",
        "subpath": "docs",
        "access": "read",
        "deny": False,
    }
    assert AgentTemplatePathRequestView.model_validate({**base, "scope": "agent"}).package is None
    assert (
        AgentTemplatePathRequestView.model_validate(
            {**base, "scope": "package", "package": "writing.beta"}
        ).package
        == "writing.beta"
    )
    with pytest.raises(ValidationError):
        AgentTemplatePathRequestView.model_validate({**base, "scope": "package"})
    with pytest.raises(ValidationError):
        AgentTemplatePathRequestView.model_validate(
            {**base, "scope": "agent", "package": "writing.beta"}
        )
    with pytest.raises(ValidationError):
        AgentTemplatePathRequestView.model_validate(
            {**base, "scope": "agent", "subpath": "../escape"}
        )
    with pytest.raises(ValidationError):
        AgentTemplatePathRequestView.model_validate({**base, "scope": "agent", "subpath": "/abs"})


def test_available_members_defaults_to_none_on_create() -> None:
    change = AgentTemplateCreateRequest.model_validate({"metadata": MINIMAL_METADATA})
    assert change.available_members is None
    member = AgentTemplateMemberView.model_validate(
        {"namespace": "a", "name": "b", "member": "a.b.c"}
    )
    assert (member.type, member.ability_type, member.version) == ("operation", "external", None)


def test_update_request_patch_semantics() -> None:
    change = AgentTemplateUpdateRequest.model_validate({"expected_revision": 2, "title": "周报"})
    assert change.model_fields_set == {"expected_revision", "title"}
    assert change.prompt is None
    # 显式 null 与省略可区分（group 级 null 视为未提供，metadata 字段同）
    cleared = AgentTemplateUpdateRequest.model_validate(
        {"expected_revision": 2, "description": None}
    )
    assert "description" in cleared.model_fields_set
    # 至少一个可写字段
    with pytest.raises(ValidationError):
        AgentTemplateUpdateRequest.model_validate({"expected_revision": 2})
    # 不允许改身份（namespace/name 不是可写字段，严格拒绝）
    with pytest.raises(ValidationError):
        AgentTemplateUpdateRequest.model_validate(
            {
                "expected_revision": 2,
                "metadata": {"namespace": "writing", "name": "other"},
            }
        )
    with pytest.raises(ValidationError):
        AgentTemplateUpdateRequest.model_validate({"expected_revision": 2, "namespace": "x"})


def test_archive_request_requires_revision() -> None:
    assert (
        AgentTemplateArchiveRequest.model_validate({"expected_revision": 1}).expected_revision == 1
    )
    with pytest.raises(ValidationError):
        AgentTemplateArchiveRequest.model_validate({"expected_revision": 0})


def test_view_formats_and_detail_shape() -> None:
    assert AgentTemplateListView.model_validate({}).format == "ghrah.agent-templates.list.v1"
    assert AgentTemplateListView.model_validate({}).templates == []
    assert (
        AgentTemplateRevisionsView.model_validate({"template_id": "t"}).format
        == "ghrah.agent-templates.revisions.v1"
    )
    assert (
        AgentTemplateReferencesView.model_validate({"template_id": "t"}).format
        == "ghrah.agent-templates.references.v1"
    )
    detail = AgentTemplateDetailView.model_validate(
        {
            "template_id": "t",
            "revision": 1,
            "metadata": MINIMAL_METADATA,
            "model": {"endpoint_name": None, "model_overrides": None},
            "prompt": {"system_prompt": ""},
            "context_policy": {
                "max_iterations": -1,
                "communication_timeout": 300,
                "window": None,
                "overrides": None,
            },
            "available_members": [],
            "permission_request": {
                "auto_approve": [],
                "require_approval_by_default": True,
                "safe_extra_commands": [],
                "safe_extra_sub_commands": None,
            },
            "path_request": [],
            "created_at": "2026-11-17T00:00:00Z",
            "updated_at": "2026-11-17T00:00:00Z",
        }
    )
    assert detail.format == "ghrah.agent-templates.v1"
    assert detail.archived_at is None
    assert detail.reference_count == 0
    with pytest.raises(ValidationError):
        AgentTemplateDetailView.model_validate({**detail.model_dump(mode="json"), "unknown": True})
