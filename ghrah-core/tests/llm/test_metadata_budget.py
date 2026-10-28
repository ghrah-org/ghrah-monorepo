# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""配置层能力通过 LLM 工厂进入 Agent 上下文预算的完整消费链。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agentconf import ProviderType
from agentconf.models.capabilities import LLMCapabilities

from ghrah.agents.builder import AgentBuilder
from ghrah.llm.factory import LLMFactory
from ghrah.types.config_types import AgentConfig, WindowConfig


def resolved_config(window: int | None = 256000) -> SimpleNamespace:
    return SimpleNamespace(
        model=SimpleNamespace(
            model_name="gpt-4o",
            model_type="llm",
            base_url="https://local.invalid/v1",
            api_key="test-key",
        ),
        connection=SimpleNamespace(
            provider_type=ProviderType.OPENAI,
            api_format="openai.chat_completions",
            base_url="https://local.invalid/v1",
            api_key="test-key",
            connection_options={},
        ),
        capabilities=LLMCapabilities(max_context_length=window),
        capability_sources={"max_context_length": "models.dev"},
        metadata=None,
    )


@pytest.mark.parametrize(
    "declared,expected,source",
    [
        (None, 256000, "agentconf"),
        (4096, 4096, "declared"),
    ],
)
async def test_factory_and_agent_prefer_resolved_capabilities(declared, expected, source) -> None:
    resolved = resolved_config()
    llm = LLMFactory.create(resolved)
    assert llm.model_capabilities["max_context_length"] == 256000
    assert llm.capability_sources == {"max_context_length": "models.dev"}
    # 来源描述是客户端自己的副本，后续修改解析对象不影响它。
    resolved.capability_sources.clear()
    assert llm.capability_sources["max_context_length"] == "models.dev"
    agent = AgentBuilder.from_config(
        AgentConfig(name="metadata-test", window=WindowConfig(max_tokens=declared)),
        llm_factory=lambda _: llm,
    )
    await agent._ensure_llm()
    wm = agent._context_manager.window_manager
    assert wm.max_tokens == expected
    assert wm.max_tokens_source == source


async def test_explicit_unknown_window_does_not_reenter_legacy_table() -> None:
    llm = LLMFactory.create(resolved_config(None))
    agent = AgentBuilder.from_config(
        AgentConfig(name="unknown-window", window=WindowConfig()),
        llm_factory=lambda _: llm,
    )
    await agent._ensure_llm()
    wm = agent._context_manager.window_manager
    assert wm.max_tokens == 1000000
    assert wm.max_tokens_source == "default"


async def test_client_without_metadata_keeps_legacy_table() -> None:
    from ghrah.chat.format.openai import OpenAIFormat

    llm = OpenAIFormat(model="gpt-4o")
    agent = AgentBuilder.from_config(
        AgentConfig(name="legacy-window", window=WindowConfig()),
        llm_factory=lambda _: llm,
    )
    await agent._ensure_llm()
    wm = agent._context_manager.window_manager
    assert wm.max_tokens == 128000
    assert wm.max_tokens_source == "model_table"
