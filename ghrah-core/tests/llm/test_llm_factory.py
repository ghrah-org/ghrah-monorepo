# SPDX-License-Identifier: Apache-2.0
"""按 SDK 端点契约分派，检查参数、用途和来源。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agentconf import ResolvedModelEndpoint

from ghrah.agents.builder import _default_llm_factory
from ghrah.chat.format.anthropic import AnthropicFormat
from ghrah.chat.format.deepseek import DeepSeekFormat
from ghrah.chat.format.openai import OpenAIFormat
from ghrah.chat.format.openai_responses import OpenAIResponsesFormat
from ghrah.llm.errors import LLMError, UnsupportedFormat
from ghrah.llm.factory import LLMFactory, _get_secret_value
from ghrah.types.config_types import AgentConfig, ModelOverrides


def endpoint(name="chat", **connection):
    data = json.loads((Path(__file__).parent / "fixtures/resolved_endpoints.json").read_text())[
        name
    ]
    data["connection"].update(connection)
    return ResolvedModelEndpoint.model_validate(data)


@pytest.mark.parametrize(
    "name,adapter",
    [
        ("chat", OpenAIFormat),
        ("responses", OpenAIResponsesFormat),
        ("deepseek-chat", DeepSeekFormat),
    ],
)
def test_frozen_sdk_samples(name, adapter):
    resolved = endpoint(name)
    llm = LLMFactory.create(resolved, ModelOverrides(temperature=0, max_tokens=32, top_p=0))
    assert isinstance(llm, adapter)
    assert llm.model == resolved.model.model_name
    assert llm._temperature == 0
    assert llm._top_p == 0
    assert llm._max_tokens == 32
    assert llm.model_capabilities == resolved.capabilities.model_dump(mode="json")
    assert llm.capability_sources == resolved.capability_sources
    assert llm._base_url == resolved.connection.base_url.get_secret_value()
    assert llm._api_key == resolved.connection.api_key.get_secret_value()
    assert llm.model_metadata == (
        resolved.metadata.model_dump(mode="json") if resolved.metadata else None
    )


@pytest.mark.parametrize("provider", ["custom", "future-vendor", "anthropic"])
def test_format_is_explicit_and_independent_of_provider(provider):
    assert isinstance(LLMFactory.create(endpoint(provider_type=provider)), OpenAIFormat)
    assert isinstance(
        LLMFactory.create(endpoint(provider_type=provider, api_format="anthropic.messages")),
        AnthropicFormat,
    )


def test_unknown_format_and_non_llm_are_rejected():
    with pytest.raises(UnsupportedFormat, match="api_format"):
        LLMFactory.create(endpoint(api_format="unknown"))
    resolved = endpoint()
    resolved = resolved.model_copy(
        update={"model": resolved.model.model_copy(update={"model_type": "stt"})}
    )
    with pytest.raises(LLMError, match="purpose"):
        LLMFactory.create(resolved)


def test_connection_options_and_secrets():
    llm = LLMFactory.create(endpoint(connection_options={"timeout": 8, "max_retries": 0}))
    assert llm._connection_options == {"timeout": 8, "max_retries": 0}
    for value in [None, "test"]:
        assert _get_secret_value(value) == value
    with pytest.raises(LLMError, match="connection_options"):
        LLMFactory.create(endpoint(connection_options={"api_key": "override"}))


def test_builder_resolves_explicit_endpoint_and_passes_own_params():
    params = ModelOverrides(temperature=0, extra_params={"stop": ["END"]})
    with patch("agentconf.AgentsConfig") as sdk:
        client = sdk.return_value.__enter__.return_value
        client.resolve_model_endpoint.return_value = endpoint()
        llm = _default_llm_factory(
            AgentConfig(name="runtime", endpoint_name="shared", model_overrides=params)
        )
        client.resolve_model_endpoint.assert_called_once_with("shared")
        client.resolve_agent.assert_not_called()
    assert llm._temperature == 0
    assert llm._extra_params == {"stop": ["END"]}


@pytest.mark.parametrize("name", [None, "", " "])
def test_builder_never_falls_back_to_runtime_name(name):
    with patch("agentconf.AgentsConfig") as sdk:
        with pytest.raises(ValueError, match="endpoint_name"):
            _default_llm_factory(AgentConfig(name="runtime", endpoint_name=name))
        sdk.assert_not_called()
