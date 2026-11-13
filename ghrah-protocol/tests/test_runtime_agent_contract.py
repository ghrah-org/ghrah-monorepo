"""完整装配、响应扩展和摘要规范化独立验收。"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghrah.protocol.runtime_agents import RuntimeAgentCreate, RuntimeAgentResource
from ghrah.protocol.runtime_digest import assembly_digest, canonical_json

CONTRACTS = Path(__file__).resolve().parents[2] / "ghrah-protocol/contracts/runtime/fixtures"


def test_agent_config_requires_every_field_and_preserves_explicit_null():
    body = json.loads((CONTRACTS / "agent.create.json").read_text())
    request = RuntimeAgentCreate.model_validate(body)
    assert request.config.prompt.template_ref is None
    incomplete = copy.deepcopy(body)
    del incomplete["config"]["model"]["model_overrides"]
    with pytest.raises(ValidationError):
        RuntimeAgentCreate.model_validate(incomplete)
    body["config"]["prompt"]["unknown"] = "rejected"
    with pytest.raises(ValidationError):
        RuntimeAgentCreate.model_validate(body)


def test_response_config_strips_nested_additions_and_keeps_future_enums():
    body = json.loads((CONTRACTS / "agent.json").read_text())
    body["config"]["model"]["future"] = True
    body["config"]["ability_request"]["packages"][0]["origin"] = "future-origin"
    body["config"]["ability_request"]["surface"]["mode"] = "future-mode"
    parsed = RuntimeAgentResource.model_validate(body).model_dump(mode="json")
    assert "future" not in parsed["config"]["model"]
    assert parsed["config"]["ability_request"]["packages"][0]["origin"] == "future-origin"
    assert parsed["config"]["ability_request"]["surface"]["mode"] == "future-mode"


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (300.0, "300"),
        (-0.0, "0"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (1.23e-7, "1.23e-7"),
    ],
)
def test_canonical_number_boundary_vectors(number, expected):
    assert canonical_json(number) == expected


def test_digest_set_order_does_not_change_but_ordered_subcommands_do():
    body = json.loads((CONTRACTS / "agent.create.json").read_text())
    first = copy.deepcopy(body)
    first["config"]["permission_request"]["auto_approve"] = ["b", "a", "b"]
    second = copy.deepcopy(first)
    second["external_ref"] = "another-identity"
    second["config"]["permission_request"]["auto_approve"] = ["a", "b"]
    one, two = (RuntimeAgentCreate.model_validate(item) for item in (first, second))
    assert assembly_digest(one.config) == assembly_digest(two.config)
    first["config"]["permission_request"]["safe_extra_sub_commands"] = [
        ["git", ["status"]],
        ["uv", ["run"]],
    ]
    second["config"]["permission_request"]["safe_extra_sub_commands"] = [
        ["uv", ["run"]],
        ["git", ["status"]],
    ]
    one, two = (RuntimeAgentCreate.model_validate(item) for item in (first, second))
    assert assembly_digest(one.config) != assembly_digest(two.config)


def test_non_json_model_values_are_rejected():
    body = json.loads((CONTRACTS / "agent.create.json").read_text())
    body["config"]["model"]["model_overrides"] = {"temperature": float("nan")}
    with pytest.raises(ValidationError):
        RuntimeAgentCreate.model_validate(body)
