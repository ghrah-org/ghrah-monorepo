# SPDX-License-Identifier: Apache-2.0
"""草稿探测的失败视图：provider 缺失也必须返回完整 view，而不是 500。"""

from __future__ import annotations

from pathlib import Path

from ghrah.protocol.ability_control import AbilityMcpDraftAuth, AbilityMcpDraftProbeRequest

from ghrah.core.read.ability_mcp_install import probe_mcp_draft


async def test_missing_provider_returns_structured_view(tmp_path: Path) -> None:
    request = AbilityMcpDraftProbeRequest(
        url="https://example.test/mcp",
        auth=AbilityMcpDraftAuth(mode="provider", provider_id="missing-provider", key=""),
    )

    view = await probe_mcp_draft(request, db_path=tmp_path / "agentconf.db")

    assert view.ok is False
    assert view.phase == "config"
    assert view.resource_count == 0
    assert view.tool_count == 0
    assert view.error is not None
    assert view.error.reason == "credential_missing"
