"""Python 与 TS 共用实体 wire 样本，校验默认值和闭集操作。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghrah.protocol.payloads.entity import (
    AgentEntityCreatePayload,
    AgentEntityListPayload,
    AgentEntityPayload,
    AgentSnapshot,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "ghrah-observer-webui/packages/protocol/src/__fixtures__/agent-entity.json"
)


def test_entity_wire_samples() -> None:
    if not FIXTURE.exists():
        pytest.skip("单包 checkout 未包含 TS 对齐样本")
    data = json.loads(FIXTURE.read_text())
    for request in data["requests"]:
        assert AgentEntityPayload.model_validate(request).model_dump(mode="json") == request
    for key, model in [
        ("create", AgentEntityCreatePayload),
        ("list", AgentEntityListPayload),
        ("snapshot", AgentSnapshot),
    ]:
        assert model.model_validate(data[key]).model_dump(mode="json") == data[key]
    for operation in data["invalid_operations"]:
        with pytest.raises(ValidationError):
            AgentEntityPayload.model_validate(
                {"project_id": "p1", "agent_id": "a1", "operation": operation}
            )
    with pytest.raises(ValidationError):
        AgentEntityCreatePayload(project_id="p1", name="a", cluster_id="c1")
