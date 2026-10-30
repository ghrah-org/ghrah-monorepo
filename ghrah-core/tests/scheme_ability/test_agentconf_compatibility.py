# SPDX-License-Identifier: Apache-2.0
"""新驱动通过现行 Manifest 消费 agentconf 0.3 端点。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentconf import AgentsConfig

from ghrah.llm.factory import LLMFactory
from ghrah.manifest.agent import AgentManifest as RuntimeAgentManifest
from ghrah.scheme_ability.agent_manifest import AgentManifest
from ghrah.scheme_ability.agent_resolver import ManifestResolver
from ghrah.scheme_ability.loading.manifest_fields import ManifestValidationError
from ghrah.scheme_ability.loading.manifest_io import (
    BuiltinManifestStore,
    parse_agent_manifest,
    validate_manifest,
)


def manifest_data() -> dict:
    """给出带零值参数和新格式扩展参数的合法 Agent 声明。"""
    return {
        "manifest": "agent",
        "version": "2",
        "metadata": {"namespace": "test_ns", "name": "source_agent"},
        "model": {
            "endpoint_name": "local/ability",
            "temperature": 0,
            "top_p": 0,
            "extra_params": {"stop": ["END"]},
        },
        "system_prompt": "Test source integration.",
        "communication_timeout": 0,
        "abilities": [{"type": "read_file"}],
    }


def test_new_driver_consumes_the_runtime_manifest_author() -> None:
    assert AgentManifest is RuntimeAgentManifest
    manifest = parse_agent_manifest(json.dumps(manifest_data()), format="json")
    resolved = ManifestResolver(BuiltinManifestStore()).resolve(manifest, runtime_name="renamed")
    assert resolved.config.name == "renamed"
    assert resolved.config.endpoint_name == "local/ability"
    assert resolved.config.communication_timeout == 0
    assert resolved.config.model_overrides.temperature == 0
    assert resolved.config.model_overrides.top_p == 0
    assert resolved.config.model_overrides.extra_params == {"stop": ["END"]}
    assert resolved.abilities[0].ability_name == "ghrah.fs.read_file"


@pytest.mark.parametrize("legacy_version", [True, False])
def test_legacy_agent_configuration_requires_explicit_migration(legacy_version: bool) -> None:
    data = manifest_data()
    if legacy_version:
        data["version"] = "1"
    else:
        data["model"] = {"agent_config_name": "old-agent"}
    raw = json.dumps(data)
    with pytest.raises(ManifestValidationError):
        parse_agent_manifest(raw, format="json")
    valid, errors = validate_manifest(raw, format="json")
    assert not valid
    assert errors


def test_resolved_new_driver_config_reaches_agentconf_endpoint(tmp_path: Path) -> None:
    manifest = parse_agent_manifest(json.dumps(manifest_data()), format="json")
    config = ManifestResolver(BuiltinManifestStore()).resolve(manifest).config
    with AgentsConfig(tmp_path / "agentconf.db") as sdk:
        sdk.create_provider("local", "openai", "https://local.invalid/v1", "test-key")
        sdk.create_model_endpoint(
            "local",
            "local/ability",
            "request-deployment",
            api_format="openai.chat_completions",
        )
        endpoint = sdk.resolve_model_endpoint(config.endpoint_name)
        llm = LLMFactory.create(endpoint, config.model_overrides)
    assert llm.model == "request-deployment"
    assert llm._temperature == 0
    assert llm._top_p == 0
    assert llm._extra_params == {"stop": ["END"]}
