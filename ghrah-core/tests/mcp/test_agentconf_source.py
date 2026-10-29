# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""agentconf → McpServerSpec 的装配与凭据解析。

全部用例都用 ``tmp_path`` 现建 agentconf 库，不读开发者机器上的
``~/.agentconf/config.db``，也不联网。
"""

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


# --------------------------------------------------------------------- helpers


def add_provider(db, *, name="gw", base_url=URL, api_key=KEY) -> None:
    with AgentsConfig(db) as cfg:
        cfg.create_provider(name, "openai", base_url, api_key)


def add_server(db, *, name="web_fetch", settings=None) -> None:
    with AgentsConfig(db) as cfg:
        cfg.create_mcp_server(name, "streamable_http", settings or {"url": URL})


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "config.db"


@pytest.fixture()
def source(db):
    src = AgentconfMcpSource(db_path=db)
    yield src
    src.close()


# ------------------------------------------------------------------- spec 装配


def test_spec_replaces_secret_with_placeholder(db, source) -> None:
    add_provider(db)
    add_server(db, settings={"url": URL, "headers": {"Authorization": f"Bearer {KEY}"}})

    spec, credential = source.spec_and_credential("web_fetch", skill_full_name="debug/probe")

    assert spec.server == "web_fetch"
    assert spec.url == URL
    assert dict(spec.headers) == {"Authorization": "Bearer {{auth}}"}
    assert spec.requires_credential is True
    assert spec.credential_headers == ("Authorization",)
    assert credential == KEY
    assert KEY not in repr(spec.headers)


def test_record_metadata_maps_to_spec(db, source) -> None:
    add_provider(db)
    add_server(
        db,
        settings={
            "url": URL,
            "headers": {"Authorization": f"Bearer {KEY}"},
            "connect_timeout": 12.5,
            "protocol_version_hint": "legacy",
        },
    )

    spec, _ = source.spec_and_credential("web_fetch", skill_full_name="debug/probe")

    assert spec.connect_timeout_ms == 12_500
    assert spec.protocol.value == "legacy"


def test_credential_ref_names_a_provider(db) -> None:
    add_provider(db, name="by-name", base_url="https://other.example.com/v1", api_key="sk-ref")
    add_server(db, settings={"url": URL, "credential_ref": "by-name"})

    # 记录里没有明文，占位符靠构造时的 auth_header 兜底注入
    with AgentconfMcpSource(db_path=db, auth_header="Authorization") as src:
        spec, credential = src.spec_and_credential("web_fetch", skill_full_name="debug/probe")

    assert credential == "sk-ref"
    assert dict(spec.headers) == {"Authorization": "Bearer {{auth}}"}


def test_literal_headers_without_credential(db, source) -> None:
    add_server(db, settings={"url": URL, "headers": {"X-Api-Key": "plain-value"}})

    spec, credential = source.spec_and_credential("web_fetch", skill_full_name="debug/probe")

    assert credential is None
    assert spec.requires_credential is False
    assert dict(spec.headers) == {"X-Api-Key": "plain-value"}


def test_existing_placeholder_is_kept(db, source) -> None:
    add_provider(db)
    add_server(db, settings={"url": URL, "headers": {"Authorization": "Bearer {{auth}}"}})

    spec, credential = source.spec_and_credential("web_fetch", skill_full_name="debug/probe")

    assert dict(spec.headers) == {"Authorization": "Bearer {{auth}}"}
    assert spec.requires_credential is True
    assert credential == KEY


def test_auth_header_injected_when_no_secret(db) -> None:
    add_server(db, settings={"url": URL})

    with AgentconfMcpSource(db_path=db, auth_header="X-Api-Key") as src:
        spec, credential = src.spec_and_credential("web_fetch", skill_full_name="debug/probe")

    assert credential is None
    assert dict(spec.headers) == {"X-Api-Key": "Bearer {{auth}}"}
    assert spec.requires_credential is True


def test_stdio_transport_is_rejected(db) -> None:
    with AgentsConfig(db) as cfg:
        cfg.create_mcp_server("local", "stdio", {"command": "python", "args": ["-c", "pass"]})

    with AgentconfMcpSource(db_path=db) as src:
        with pytest.raises(McpConfigError, match="stdio"):
            src.spec("local", skill_full_name="debug/probe")


def test_unknown_server_lists_available(db, source) -> None:
    add_server(db, name="known_one")

    with pytest.raises(McpConfigError, match="known_one"):
        source.spec("missing", skill_full_name="debug/probe")


# --------------------------------------------------------------- 按地址装配


def test_spec_for_url_uses_registered_record(db, source) -> None:
    add_provider(db)
    add_server(db, settings={"url": URL, "headers": {"Authorization": f"Bearer {KEY}"}})

    assert source.server_name_for_url(URL) == "web_fetch"
    assert source.server_name_for_url("https://gw.example.com/other/mcp") is None

    spec, credential = source.spec_for_url(URL, skill_full_name="debug/probe")

    assert spec.server == "web_fetch"
    assert credential == KEY


def test_spec_for_url_falls_back_to_minimal_spec(db, source) -> None:
    """地址没登记过，但只要 provider 的 base_url 对得上，密钥照样能取到。"""
    add_provider(db)

    spec, credential = source.spec_for_url(URL, skill_full_name="debug/probe")

    assert credential == KEY
    assert spec.server == "gw_example_com"  # 由主机名压出
    assert dict(spec.headers) == {"Authorization": "Bearer {{auth}}"}
    assert spec.requires_credential is True


def test_spec_for_url_without_any_configuration(db, source) -> None:
    spec, credential = source.spec_for_url(URL, skill_full_name="debug/probe")

    assert credential is None
    assert spec.headers == {}
    assert spec.requires_credential is False


def test_load_spec_reads_named_server(db) -> None:
    add_provider(db)
    add_server(db, settings={"url": URL, "headers": {"Authorization": f"Bearer {KEY}"}})

    spec, credential = load_spec("web_fetch", skill_full_name="debug/probe", db_path=db)

    assert spec.url == URL
    assert credential == KEY


# ------------------------------------------------------------------- 凭据解析


def test_credential_resolver_returns_secret_for_url(db) -> None:
    """回归：provider 的 base_url 是 SecretStr，早期实现按 str 判断，永远返回 None。"""
    add_provider(db)

    with AgentconfCredentialResolver(db_path=db) as resolver:
        assert resolver(URL) == KEY
        assert resolver(URL + "/") == KEY  # 尾部斜杠不影响匹配
        assert resolver("https://gw.example.com/unknown/mcp") is None


# ------------------------------------------------------------------- HTTP 取证


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
