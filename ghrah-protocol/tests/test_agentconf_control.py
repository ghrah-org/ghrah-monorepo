# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agentconf 管理面契约：未知字段拒绝，省略和 null 可以区分。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ghrah.protocol.agentconf_control import (
    AgentconfProviderPatchRequest,
    AgentconfProviderView,
)


def test_provider_patch_distinguishes_omit_and_null() -> None:
    omitted = AgentconfProviderPatchRequest.model_validate(
        {"expected_revision": 1, "name": "gateway"}
    )
    assert "api_key" not in omitted.model_fields_set
    cleared = AgentconfProviderPatchRequest.model_validate(
        {"expected_revision": 1, "api_key": None}
    )
    assert cleared.api_key is None
    assert "api_key" in cleared.model_fields_set


def test_provider_view_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AgentconfProviderView.model_validate(
            {
                "id": "id",
                "name": "gateway",
                "provider_type": "openai",
                "base_url": "https://example.com/v1",
                "api_key_configured": True,
                "connection_options": {},
                "revision": 1,
                "created_at": "2026-11-11T00:00:00Z",
                "updated_at": "2026-11-11T00:00:00Z",
                "api_key": "secret",
            }
        )
