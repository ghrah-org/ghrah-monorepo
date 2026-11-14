# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 管理 REST 契约测试（Python 侧）。

覆盖 REST 专用请求/响应模型与 pause/resume/stop 命令载荷的严格性：
未知字段拒绝、版本必填/可选、空更新拒绝、删除视图字段约束。
这些模型不进入 WS message 路由，只作为 Subject REST 层的入参/出参形状。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ghrah.protocol.types import (
    ProjectDeletedView,
    ProjectLifecycleRequest,
    ProjectTransitionPayload,
    ProjectTransitionRequest,
    ProjectUpdateRequest,
)


class TestProjectUpdateRequest:
    def test_requires_expected_version(self) -> None:
        with pytest.raises(ValidationError):
            ProjectUpdateRequest.model_validate({"name": "renamed"})

    def test_requires_at_least_one_field(self) -> None:
        with pytest.raises(ValidationError):
            ProjectUpdateRequest.model_validate({"expected_version": 3})

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            ProjectUpdateRequest.model_validate(
                {"name": "renamed", "expected_version": 3, "project_id": "p"}
            )

    def test_rejects_zero_version(self) -> None:
        with pytest.raises(ValidationError):
            ProjectUpdateRequest.model_validate({"name": "renamed", "expected_version": 0})

    def test_accepts_single_field_patch(self) -> None:
        parsed = ProjectUpdateRequest.model_validate({"shell_cwd": "", "expected_version": 3})
        assert parsed.shell_cwd == ""
        assert parsed.name is None
        assert parsed.expected_version == 3


class TestProjectLifecycleRequest:
    def test_requires_expected_version(self) -> None:
        with pytest.raises(ValidationError):
            ProjectLifecycleRequest.model_validate({})

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            ProjectLifecycleRequest.model_validate({"expected_version": 3, "cascade_rooms": True})

    def test_accepts_version(self) -> None:
        assert ProjectLifecycleRequest.model_validate({"expected_version": 3}).expected_version == 3


class TestProjectTransitionRequest:
    def test_allows_missing_version(self) -> None:
        assert ProjectTransitionRequest.model_validate({}).expected_version is None

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            ProjectTransitionRequest.model_validate({"expected_version": 3, "status": "paused"})

    def test_rejects_zero_version(self) -> None:
        with pytest.raises(ValidationError):
            ProjectTransitionRequest.model_validate({"expected_version": 0})


class TestProjectTransitionPayload:
    def test_requires_project_id(self) -> None:
        with pytest.raises(ValidationError):
            ProjectTransitionPayload.model_validate({"expected_version": 3})

    def test_inherits_project_id_and_optional_version(self) -> None:
        parsed = ProjectTransitionPayload.model_validate({"project_id": "proj-001"})
        assert parsed.project_id == "proj-001"
        assert parsed.expected_version is None

    def test_with_version(self) -> None:
        parsed = ProjectTransitionPayload.model_validate(
            {"project_id": "proj-001", "expected_version": 3}
        )
        assert parsed.expected_version == 3


class TestProjectDeletedView:
    def test_accepts_full_view(self) -> None:
        parsed = ProjectDeletedView.model_validate(
            {
                "project_id": "proj-001",
                "deleted": True,
                "storage_purged": True,
                "rooms_deleted": 2,
            }
        )
        assert parsed.rooms_deleted == 2

    def test_rejects_negative_rooms_deleted(self) -> None:
        with pytest.raises(ValidationError):
            ProjectDeletedView.model_validate(
                {
                    "project_id": "proj-001",
                    "deleted": True,
                    "storage_purged": True,
                    "rooms_deleted": -1,
                }
            )

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            ProjectDeletedView.model_validate(
                {
                    "project_id": "proj-001",
                    "deleted": True,
                    "storage_purged": True,
                    "rooms_deleted": 0,
                    "room_ids": [],
                }
            )
