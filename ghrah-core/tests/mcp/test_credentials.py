# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""凭据解析：密钥存在 agentconf，按 MCP 地址关联。

用真实的 agentconf 数据库（建在临时目录）验证桥接，不 mock —— 桥接的正确性正是
「两个组件的约定是否对齐」，mock 掉被桥接的一方就测不到这件事。
"""

from __future__ import annotations

from pathlib import Path

from ghrah.mcp.credentials import (
    AgentconfCredentialResolver,
    StaticCredentials,
    no_credentials,
)

URL = "https://mcp.example.dev/mcp"


def make_agentconf(tmp_path: Path):
    from agentconf import AgentsConfig

    return AgentsConfig(tmp_path / "config.db")


def register(config, *, name: str, base_url: str, api_key: str) -> None:
    # MCP 服务器登记成 custom provider：base_url 放 MCP 地址，api_key 放密钥。
    config.create_provider(name, "custom", base_url, api_key)


class TestAgentconfResolver:
    def test_resolves_by_exact_base_url(self, tmp_path: Path) -> None:
        config = make_agentconf(tmp_path)
        register(config, name="sentry-mcp", base_url=URL, api_key="sntrys_secret")

        with AgentconfCredentialResolver(config) as resolver:
            assert resolver(URL) == "sntrys_secret"

    def test_trailing_slash_is_tolerated(self, tmp_path: Path) -> None:
        config = make_agentconf(tmp_path)
        register(config, name="sentry-mcp", base_url=URL + "/", api_key="s")

        with AgentconfCredentialResolver(config) as resolver:
            assert resolver(URL) == "s"

    def test_unknown_url_returns_none(self, tmp_path: Path) -> None:
        config = make_agentconf(tmp_path)
        register(config, name="sentry-mcp", base_url=URL, api_key="s")

        with AgentconfCredentialResolver(config) as resolver:
            assert resolver("https://other.example.dev/mcp") is None

    def test_scheme_difference_is_not_matched(self, tmp_path: Path) -> None:
        # http 与 https 是真正不同的目的地，不能因为"看起来像"就共用凭据。
        config = make_agentconf(tmp_path)
        register(config, name="sentry-mcp", base_url=URL, api_key="s")

        with AgentconfCredentialResolver(config) as resolver:
            assert resolver(URL.replace("https://", "http://")) is None

    def test_empty_database_returns_none(self, tmp_path: Path) -> None:
        config = make_agentconf(tmp_path)
        with AgentconfCredentialResolver(config) as resolver:
            assert resolver(URL) is None

    def test_multiple_providers_picks_matching_one(self, tmp_path: Path) -> None:
        config = make_agentconf(tmp_path)
        register(config, name="llm", base_url="https://api.example.dev/v1", api_key="llm-key")
        register(config, name="sentry-mcp", base_url=URL, api_key="mcp-key")

        with AgentconfCredentialResolver(config) as resolver:
            assert resolver(URL) == "mcp-key"
            assert resolver("https://api.example.dev/v1") == "llm-key"

    def test_secret_is_read_as_plaintext(self, tmp_path: Path) -> None:
        # agentconf 用 SecretStr 承载密钥，桥接必须能取到明文，否则发出去的会是掩码。
        config = make_agentconf(tmp_path)
        register(config, name="sentry-mcp", base_url=URL, api_key="visible-secret")

        with AgentconfCredentialResolver(config) as resolver:
            value = resolver(URL)
        assert value == "visible-secret"
        assert "**" not in (value or "")

    def test_lazy_open_has_no_side_effect(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nested" / "config.db"
        resolver = AgentconfCredentialResolver(db_path=db_path)
        # 构造时不建库。
        assert not db_path.exists()
        # 首次取值才打开（并自动建 schema）。
        assert resolver(db_path and URL) is None
        assert db_path.exists()
        resolver.close()

    def test_external_config_is_not_closed(self, tmp_path: Path) -> None:
        config = make_agentconf(tmp_path)
        register(config, name="sentry-mcp", base_url=URL, api_key="s")
        resolver = AgentconfCredentialResolver(config)
        resolver.close()
        # 外部传入的 config 不该被我们关掉，仍可继续使用。
        assert config.list_providers()

    def test_broken_database_falls_back_to_none(self, tmp_path: Path) -> None:
        # 凭据读取失败不该让整个 agent 起不来；缺失会在建连时明确暴露。
        broken = tmp_path / "broken.db"
        broken.write_bytes(b"not a sqlite file at all")
        with AgentconfCredentialResolver(db_path=broken) as resolver:
            assert resolver(URL) is None


class TestStaticCredentials:
    def test_url_keyed(self) -> None:
        store = StaticCredentials({URL: "s"})
        assert store(URL) == "s"
        assert store(URL + "/") == "s"
        assert store("https://other.dev/mcp") is None

    def test_set_and_delete(self) -> None:
        store = StaticCredentials()
        store.set(URL, "s")
        assert store(URL) == "s"
        store.delete(URL)
        assert store(URL) is None


class TestNoCredentials:
    def test_always_none(self) -> None:
        assert no_credentials(URL) is None


class TestRuntimeIntegration:
    async def test_runtime_uses_url_keyed_resolver(self, tmp_path: Path) -> None:
        from collections.abc import AsyncIterator
        from contextlib import asynccontextmanager

        from mcp import types

        from ghrah.mcp.runtime import McpRuntime
        from ghrah.mcp.spec import AUTH_PLACEHOLDER, McpServerSpec, McpToolSpec

        config = make_agentconf(tmp_path)
        register(config, name="sentry-mcp", base_url=URL, api_key="from-agentconf")

        seen: list[str | None] = []

        class Client:
            async def call_tool(self, name, arguments=None, *, read_timeout_seconds=None):
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="ok")], is_error=False
                )

        @asynccontextmanager
        async def connector(
            spec: McpServerSpec,
            *,
            credential: str | None = None,
            read_timeout_s: float | None = None,
        ) -> AsyncIterator[Client]:
            seen.append(credential)
            yield Client()

        spec = McpServerSpec(
            skill_full_name="sentry-ops",
            server="sentry",
            url=URL,
            headers={"Authorization": f"Bearer {AUTH_PLACEHOLDER}"},
            tools=(McpToolSpec(name="list_issues", description="列 issue"),),
        )

        runtime = McpRuntime(
            credentials=AgentconfCredentialResolver(config),
            connector=connector,
        )
        await runtime.activate(spec, agent_scope="a1")
        await runtime.call(agent_scope="a1", server="sentry", tool="list_issues")
        await runtime.close()

        assert seen == ["from-agentconf"]
