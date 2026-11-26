# SPDX-License-Identifier: Apache-2.0
"""草稿探测的失败视图：provider 缺失也必须返回完整 view，而不是 500。"""

from __future__ import annotations

from pathlib import Path

from agentconf import AgentsConfig
from ghrah.protocol.ability_control import AbilityMcpDraftAuth, AbilityMcpDraftProbeRequest

from ghrah.core.read.ability_mcp_install import (
    _draft_headers,
    _save_connection,
    probe_mcp_draft,
)


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


def test_keyless_provider_probes_and_saves_without_auth(tmp_path: Path) -> None:
    db = tmp_path / "agentconf.db"
    with AgentsConfig(db) as cfg:
        provider = cfg.create_mcp_provider("keyless", vendor="Local")

    headers, credential = _draft_headers(
        AbilityMcpDraftAuth(mode="provider", provider_id=provider.id), db_path=db
    )
    assert headers == {}
    assert credential == ""

    saved = _save_connection(
        db,
        ref="keyless_mcp",
        vendor="Local",
        url="https://example.test/mcp",
        auth_mode="bearer",
        auth_header=None,
        api_key="",
        provider_id=provider.id,
        timeout_ms=5000,
    )

    assert saved.provider_id == provider.id
    with AgentsConfig(db) as cfg:
        server = cfg.get_mcp_server("keyless_mcp")
        assert server.auth.mode == "none"
        assert cfg.get_mcp_provider("keyless").api_key is None
