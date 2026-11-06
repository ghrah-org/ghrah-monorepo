# SPDX-License-Identifier: Apache-2.0
"""schema 6 的 MCP Provider 消费与失败诊断，全程只使用临时库。"""

from __future__ import annotations

import httpx2
import pytest
from agentconf import AgentsConfig

from ghrah.mcp import (
    AgentconfCredentialResolver,
    AgentconfMcpSource,
    FailureKind,
    HttpProbe,
    McpConfigError,
    McpRequestError,
    explain_status,
    load_spec,
    probe_endpoint,
)
from ghrah.mcp.diagnostics import enrich_request_error

URL = "https://gw.example.com/api/v1/mcps/WebFetch/mcp"
KEY = "sk-test-0123456789abcdef"


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "config.db"


def configured(db):
    with AgentsConfig(db) as cfg:
        p = cfg.create_mcp_provider(
            "vendor/account",
            api_key=KEY,
            default_binding={
                "target": "http_header",
                "name": "Authorization",
                "value_template": "Bearer {{auth}}",
            },
        )
        s = cfg.create_mcp_server(
            "fetch",
            "streamable_http",
            {"url": URL},
            auth={"mode": "provider", "provider_id": p.id, "binding_mode": "inherit"},
        )
        return p, s


def test_spec_is_offline_and_key_is_read_by_fixed_identity(db, monkeypatch):
    provider, server = configured(db)
    with AgentsConfig(db) as cfg:
        source = AgentconfMcpSource(config=cfg)
        original = cfg.read_mcp_credential
        monkeypatch.setattr(
            cfg, "read_mcp_credential", lambda *a, **kw: pytest.fail("提前读取 Key")
        )
        spec = source.connection_spec("fetch", skill_full_name="demo/guide")
        assert dict(spec.headers) == {"Authorization": "Bearer {{auth}}"}
        assert KEY not in repr(spec)
        monkeypatch.setattr(cfg, "read_mcp_credential", original)
        assert source.credential_for_spec(spec) == KEY
        assert spec.connection_identity == (server.id, 1, provider.id, 1)
        cfg.update_mcp_provider(provider.name, expected_revision=1, api_key="rotated")
        with pytest.raises(McpConfigError):
            source.credential_for_spec(spec)
        latest = source.connection_spec("fetch", skill_full_name="demo/guide")
        assert source.credential_for_spec(latest) == "rotated"


def test_inline_values_are_deferred_and_no_model_provider_lookup(db):
    with AgentsConfig(db) as cfg:
        cfg.create_provider("model", "openai", URL, "must-not-use")
        cfg.create_mcp_server("public", "streamable_http", {"url": URL})
        cfg.create_mcp_server(
            "inline",
            "streamable_http",
            {"url": URL, "headers": {"X-Key": KEY}},
            auth={"mode": "inline"},
        )
        source = AgentconfMcpSource(config=cfg)
        public = source.connection_spec("public", skill_full_name="demo/guide")
        assert source.credential_for_spec(public) is None
        spec = source.connection_spec("inline", skill_full_name="demo/guide")
        assert spec.inline_headers and not spec.headers
        assert KEY not in repr(spec)
        assert source.headers_for_spec(spec) == {"X-Key": KEY}


def test_transport_external_and_legacy_url_resolver_fail_explicitly(db):
    with AgentsConfig(db) as cfg:
        cfg.create_mcp_server("stdio", "stdio", {"command": "python"})
        cfg.create_mcp_server(
            "external",
            "streamable_http",
            {"url": URL},
            auth={
                "mode": "external",
                "reference": "opaque",
                "binding": {"target": "http_header", "name": "X-Key", "value_template": "{{auth}}"},
            },
        )
        source = AgentconfMcpSource(config=cfg)
        for name in ("stdio", "external", "missing"):
            with pytest.raises(McpConfigError):
                source.connection_spec(name, skill_full_name="demo/guide")
        with pytest.raises(McpConfigError):
            AgentconfCredentialResolver(config=cfg)(URL)


def test_load_spec_uses_explicit_mcp_provider(db):
    configured(db)
    spec, key = load_spec("fetch", skill_full_name="demo/guide", db_path=db)
    assert spec.url == URL and key == KEY


@pytest.mark.parametrize(
    "status,keyword",
    [(401, "鉴权"), (403, "鉴权"), (404, "地址"), (405, "GET"), (429, "限流"), (503, "服务端")],
)
def test_explain_status(status, keyword) -> None:
    assert keyword in explain_status(status)


def test_explain_status_is_empty_for_success() -> None:
    assert explain_status(200) == ""


async def test_probe_endpoint_reads_status_and_redacts() -> None:
    body = {"code": "InvalidApiKey", "message": f"Invalid API-key provided: {KEY}"}

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json=body)

    probe = await probe_endpoint(URL, secrets=(KEY,), transport=httpx2.MockTransport(handler))

    assert isinstance(probe, HttpProbe)
    assert probe.status_code == 401
    assert "InvalidApiKey" in (probe.body_snippet or "")
    assert KEY not in (probe.body_snippet or "")
    assert "401" in probe.summary()


async def test_probe_endpoint_returns_none_on_network_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("boom", request=request)

    probe = await probe_endpoint(URL, transport=httpx2.MockTransport(handler))

    assert probe is None


async def test_enrich_ignores_errors_without_needing_probe() -> None:
    """非 McpRequestError，或已带状态码的，原样返回（不再发请求）。"""
    config_error = McpConfigError("bad")
    assert await enrich_request_error(config_error, spec=None, headers={}) is config_error  # type: ignore[arg-type]

    known = McpRequestError(
        "already enriched",
        kind=FailureKind.PERMANENT,
        http_status=401,
        body_snippet="nope",
    )
    assert await enrich_request_error(known, spec=None, headers={}) is known  # type: ignore[arg-type]
