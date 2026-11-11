# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agentconf 连接配置 REST：脱敏、引用、代次冲突与迁移拒绝。"""

from __future__ import annotations

from pathlib import Path

from agentconf import AgentsConfig
from agentconf.models.secure import reveal
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ghrah.subject.config import SubjectConfig
from ghrah.subject.units.ability_control import register_ability_control_error_handlers
from ghrah.subject.units.agentconf_control import build_agentconf_control_router


def _client(path: Path, *, enabled: bool = True) -> TestClient:
    app = FastAPI()
    register_ability_control_error_handlers(app)
    app.include_router(
        build_agentconf_control_router(
            config=SubjectConfig(
                observer_http_api_enabled=enabled,
                agentconf_db_path=str(path),
            )
        )
    )
    return TestClient(app)


def test_provider_hides_secrets_and_keeps_masked_options(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.db")
    created = client.post(
        "/api/v1/agentconf/providers",
        json={
            "name": "gateway",
            "provider_type": "openai",
            "base_url": "https://user:secret@example.com/v1",
            "api_key": "sk-test",
            "connection_options": {
                "timeout": 30,
                "default_headers": {"Authorization": "Bearer raw"},
            },
        },
    )
    assert created.status_code == 201
    assert created.headers["location"].endswith(f"/providers/{created.json()['id']}")
    body = created.json()
    assert body["base_url"] == "https://example.com/v1"
    assert body["api_key_configured"] is True
    assert "sk-test" not in created.text
    assert "Bearer raw" not in created.text
    assert body["connection_options"]["timeout"] == 30
    assert body["connection_options"]["default_headers"]["Authorization"] == "**********"

    renamed = client.patch(
        f"/api/v1/agentconf/providers/{body['id']}",
        json={
            "expected_revision": body["revision"],
            "name": "gateway-2",
            "connection_options": body["connection_options"],
        },
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "gateway-2"
    with AgentsConfig(tmp_path / "config.db") as cfg:
        stored = reveal(cfg.get_provider("gateway-2").connection_options)
    assert stored["default_headers"]["Authorization"] == "Bearer raw"
    assert stored["timeout"] == 30


def test_delete_provider_reports_endpoint_references(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.db")
    provider = client.post(
        "/api/v1/agentconf/providers",
        json={
            "name": "gateway",
            "provider_type": "openai",
            "base_url": "https://example.com/v1",
        },
    ).json()
    endpoint = client.post(
        "/api/v1/agentconf/endpoints",
        json={
            "name": "chat-main",
            "provider_name": "gateway",
            "model_name": "actual-model",
            "api_format": "openai.responses",
        },
    )
    assert endpoint.status_code == 201
    blocked = client.request(
        "DELETE",
        f"/api/v1/agentconf/providers/{provider['id']}",
        json={"expected_revision": provider["revision"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "resource_in_use"
    assert blocked.json()["error"]["detail"]["references"] == ["chat-main"]


def test_endpoint_resolve_omits_api_key_and_conflict_returns_view(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.db")
    provider = client.post(
        "/api/v1/agentconf/providers",
        json={
            "name": "gateway",
            "provider_type": "openai",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test",
        },
    ).json()
    endpoint = client.post(
        "/api/v1/agentconf/endpoints",
        json={
            "name": "chat-main",
            "provider_name": "gateway",
            "model_name": "actual-model",
            "model_type": "llm",
            "api_format": "openai.responses",
            "capabilities_key": "example-VLA",
        },
    )
    assert endpoint.status_code == 201
    resolved = client.post(f"/api/v1/agentconf/endpoints/{endpoint.json()['id']}/resolve")
    assert resolved.status_code == 200
    preview = resolved.json()
    assert preview["model_name"] == "actual-model"
    assert preview["sdk_hint"] == "openai:openai"
    assert preview["api_key_configured"] is True
    assert preview["metadata_model_key"] == "example-VLA"
    assert "sk-test" not in resolved.text
    listed = client.get("/api/v1/agentconf/endpoints", params={"model_type": "llm"})
    assert [item["name"] for item in listed.json()["endpoints"]] == ["chat-main"]
    stale = client.patch(
        f"/api/v1/agentconf/providers/{provider['id']}",
        json={"expected_revision": 99, "name": "other"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"
    assert stale.json()["error"]["detail"]["view"]["name"] == "gateway"


def test_mcp_provider_name_with_slash_and_server_resolve(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.db")
    provider = client.post(
        "/api/v1/agentconf/mcp-providers",
        json={
            "name": "vendor/work-account",
            "vendor": "vendor",
            "api_key": "mcp-secret",
            "default_binding": {
                "target": "http_header",
                "name": "Authorization",
                "value_template": "Bearer {{auth}}",
            },
        },
    )
    assert provider.status_code == 201
    assert "mcp-secret" not in provider.text
    server = client.post(
        "/api/v1/agentconf/mcp-servers",
        json={
            "name": "search",
            "transport": "streamable_http",
            "url": "https://tools.example.com/search/mcp",
            "auth_mode": "provider",
            "provider_id": provider.json()["id"],
            "binding_mode": "inherit",
        },
    )
    assert server.status_code == 201
    resolved = client.post(f"/api/v1/agentconf/mcp-servers/{server.json()['id']}/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["key_configured"] is True
    assert resolved.json()["provider_name"] == "vendor/work-account"
    assert "mcp-secret" not in resolved.text
    blocked = client.request(
        "DELETE",
        f"/api/v1/agentconf/mcp-providers/{provider.json()['id']}",
        json={"expected_revision": provider.json()["revision"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "resource_in_use"


def test_capabilities_search_and_disabled_surface(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.db")
    found = client.get(
        "/api/v1/agentconf/capabilities",
        params={"model_type": "llm", "q": "example-VLA"},
    )
    assert found.status_code == 200
    keys = [item["model_key"] for item in found.json()["capabilities"]]
    assert "example-VLA" in keys
    disabled = _client(tmp_path / "other.db", enabled=False)
    status = disabled.get("/api/v1/agentconf/status")
    assert status.status_code == 503
    assert status.json()["error"]["code"] == "capability_unavailable"


def test_old_database_requires_migration(tmp_path: Path) -> None:
    from agentconf.storage.schema_v5 import Database as Schema5

    path = tmp_path / "old.db"
    legacy = Schema5(path)
    legacy.connect()
    assert legacy._connection is not None
    legacy._connection.close()
    legacy._connection = None
    client = _client(path)
    status = client.get("/api/v1/agentconf/status")
    assert status.status_code == 503
    assert status.json()["error"]["code"] == "migration_required"


def test_unknown_field_is_invalid_request(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.db")
    response = client.post(
        "/api/v1/agentconf/providers",
        json={
            "name": "gateway",
            "provider_type": "openai",
            "base_url": "https://example.com/v1",
            "surprise": True,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_equivalent_public_url_keeps_embedded_userinfo(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.db")
    created = client.post(
        "/api/v1/agentconf/providers",
        json={
            "name": "gateway",
            "provider_type": "openai",
            "base_url": "https://user:secret@example.com/v1/",
        },
    )
    assert created.status_code == 201
    body = created.json()
    kept = client.patch(
        f"/api/v1/agentconf/providers/{body['id']}",
        json={
            "expected_revision": body["revision"],
            "base_url": "HTTPS://Example.COM:443/v1",
        },
    )
    assert kept.status_code == 200
    with AgentsConfig(tmp_path / "config.db") as cfg:
        assert reveal(cfg.get_provider("gateway").base_url) == "https://user:secret@example.com/v1/"
    replaced = client.patch(
        f"/api/v1/agentconf/providers/{body['id']}",
        json={
            "expected_revision": kept.json()["revision"],
            "base_url": "https://other.example/v1",
        },
    )
    assert replaced.status_code == 200
    explicit = client.patch(
        f"/api/v1/agentconf/providers/{body['id']}",
        json={
            "expected_revision": replaced.json()["revision"],
            "base_url": "https://new:secret@example.com/v1",
        },
    )
    assert explicit.status_code == 200
    with AgentsConfig(tmp_path / "config.db") as cfg:
        stored = reveal(cfg.get_provider("gateway").base_url)
    assert stored == "https://new:secret@example.com/v1"


def test_top_level_api_key_mask_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.db")
    provider = client.post(
        "/api/v1/agentconf/providers",
        json={
            "name": "gateway",
            "provider_type": "openai",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test",
        },
    ).json()
    mcp = client.post(
        "/api/v1/agentconf/mcp-providers",
        json={"name": "vendor/work", "api_key": "mcp-secret"},
    ).json()
    masked = client.patch(
        f"/api/v1/agentconf/providers/{provider['id']}",
        json={"expected_revision": provider["revision"], "api_key": "**********"},
    )
    assert masked.status_code == 400
    assert masked.json()["error"]["code"] == "invalid_request"
    mcp_masked = client.patch(
        f"/api/v1/agentconf/mcp-providers/{mcp['id']}",
        json={"expected_revision": mcp["revision"], "api_key": "**********"},
    )
    assert mcp_masked.status_code == 400
    cleared = client.patch(
        f"/api/v1/agentconf/providers/{provider['id']}",
        json={"expected_revision": provider["revision"], "api_key": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["api_key_configured"] is False
    with AgentsConfig(tmp_path / "config.db") as cfg:
        assert reveal(cfg.get_mcp_provider("vendor/work").api_key) == "mcp-secret"
        assert cfg.get_mcp_provider("vendor/work").revision == mcp["revision"]
