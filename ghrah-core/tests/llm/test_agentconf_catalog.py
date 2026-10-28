# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""能力档案经过显式绑定和工厂进入 Agent 实际预算的跨仓验收。"""

from __future__ import annotations

import pytest
from agentconf import AgentsConfig, CapabilityProfile

from ghrah.agents.builder import AgentBuilder
from ghrah.llm.factory import LLMFactory
from ghrah.types.config_types import AgentConfig, WindowConfig


@pytest.mark.parametrize(
    "key,expected,field_source",
    [
        ("vendor/fixed-release", 256000, "models.dev"),
        ("zhipuai/glm-5.3", 1000000, "models.dev"),
        ("example-VLA", 1000000, "example"),
        (None, 1000000, "default"),
    ],
)
async def test_bound_catalog_reaches_actual_agent_budget(
    tmp_path, key, expected, field_source
) -> None:
    path = tmp_path / "config.db"
    with AgentsConfig(path) as cfg:
        cfg.catalog_create(
            CapabilityProfile(
                model_key="vendor/fixed-release",
                display_name="Fixed Release",
                source="models.dev",
                source_ref="vendor/fixed-release",
                max_context_length=256000,
                max_output_length=65536,
                supports_reasoning=False,
            )
        )
        cfg.create_provider("local", "openai", "https://local.invalid/v1", "test-key")
        cfg.create_model_endpoint(
            "local",
            "alias",
            "request-deployment",
            api_format="openai.chat_completions",
            capabilities_key=key,
        )
    # 重开后直接消费本地权威档案，不需要数据源在线。
    with AgentsConfig(path) as cfg:
        resolved = cfg.resolve_model_endpoint("alias")
        llm = LLMFactory.create(resolved)
    agent = AgentBuilder.from_config(
        AgentConfig(name="runtime", window=WindowConfig()),
        llm_factory=lambda _: llm,
    )
    await agent._ensure_llm()
    wm = agent._context_manager.window_manager
    assert wm.max_tokens == expected
    assert wm.max_tokens_source == "agentconf"
    assert llm.capability_sources["max_context_length"] == field_source
    assert resolved.model.model_name == "request-deployment"
    if key == "example-VLA":
        assert llm.model_capabilities["max_output_length"] == 242000
        assert llm.model_capabilities["supports_vision"] is True
    if key == "vendor/fixed-release":
        assert llm.model_capabilities["supports_reasoning"] is False


async def test_runtime_window_declaration_precedes_bound_catalog(tmp_path) -> None:
    with AgentsConfig(tmp_path / "config.db") as cfg:
        cfg.create_provider("p", "openai", "https://local.invalid", "test-key")
        cfg.create_model_endpoint(
            "p",
            "m",
            "request",
            api_format="openai.chat_completions",
            capabilities_key="example-VLA",
        )
        llm = LLMFactory.create(cfg.resolve_model_endpoint("m"))
    agent = AgentBuilder.from_config(
        AgentConfig(name="runtime", window=WindowConfig(max_tokens=64000)),
        llm_factory=lambda _: llm,
    )
    await agent._ensure_llm()
    assert agent._context_manager.window_manager.max_tokens == 64000
    assert agent._context_manager.window_manager.max_tokens_source == "declared"
