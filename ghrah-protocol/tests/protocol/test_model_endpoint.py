# SPDX-License-Identifier: Apache-2.0
"""配置 wire 拒绝旧 Agent 引用，并无损保留调用者参数。"""

import pytest
from pydantic import ValidationError

from ghrah.protocol.payloads.agent import AgentConfigPayload, SpawnAgentPayload
from ghrah.protocol.payloads.project import AgentSpecSchema


@pytest.mark.parametrize(
    "model,data",
    [
        (AgentConfigPayload, {"name": "worker"}),
        (AgentSpecSchema, {"name": "worker", "cluster_id": "c"}),
    ],
)
def test_old_reference_is_never_ignored(model, data):
    with pytest.raises(ValidationError, match="migrate.*endpoint_name"):
        model.model_validate({**data, "agent_config_name": "old"})
    with pytest.raises(ValidationError, match="migrate.*endpoint_name"):
        model.model_validate({**data, "agent_config_name": "old", "endpoint_name": "new"})


def test_spawn_serialization_preserves_request_params():
    payload = SpawnAgentPayload.model_validate(
        {
            "project_id": "p",
            "config": {
                "name": "worker",
                "endpoint_name": "shared",
                "model_overrides": {
                    "temperature": 0,
                    "max_tokens": 42,
                    "top_p": 0,
                    "extra_params": {"vendor": False},
                },
            },
        }
    )
    result = SpawnAgentPayload.model_validate_json(payload.model_dump_json())
    assert result.config.endpoint_name == "shared"
    assert result.config.model_overrides["temperature"] == 0
    assert result.config.model_overrides["extra_params"] == {"vendor": False}


def test_spawn_rejects_legacy_reference_at_envelope_level_too():
    with pytest.raises(ValidationError, match="agent_config_name was removed"):
        SpawnAgentPayload.model_validate(
            {
                "project_id": "p",
                "agent_config_name": "old",
                "config": {"name": "worker", "endpoint_name": "shared"},
            }
        )
