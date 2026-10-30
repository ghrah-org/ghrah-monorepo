# SPDX-License-Identifier: Apache-2.0
"""单一契约作者、严格 JSON 校验和管理主体隔离。"""

import pytest
from pydantic import BaseModel, ConfigDict

from ghrah.scheme import (
    ActionSpec,
    CodecArguments,
    ControlPrincipal,
    JsonSchemaCodec,
    Principal,
    PydanticCodec,
    SchemeModel,
    SchemeSpec,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"count": {"type": "integer", "minimum": 0}},
                "required": ["count"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["enabled", "items"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    "value",
    [
        {"enabled": 1, "items": []},
        {"enabled": True, "items": [{"count": False}]},
        {"enabled": True, "items": [{"count": -1}]},
        {"enabled": True, "items": [{"count": 1, "extra": 0}]},
        {"enabled": True, "items": [], "extra": None},
    ],
)
def test_nested_json_contract_rejects_coercion_and_extra(value):
    codec = JsonSchemaCodec(SCHEMA, SCHEMA)
    with pytest.raises(ValueError):
        codec.decode(value)
    with pytest.raises(ValueError):
        codec.encode(value)


def test_schema_and_values_are_isolated_and_have_one_author():
    codec = JsonSchemaCodec(SCHEMA, SCHEMA)
    action = ActionSpec("check", codec=codec)
    spec = SchemeSpec(kind="ability", contract="test.codec", version="1", actions=(action,))
    exported = codec.input_schema
    exported["properties"].clear()
    source = {"enabled": True, "items": [{"count": 2}]}
    decoded = action.decode(source)
    assert isinstance(decoded, CodecArguments)
    source["items"][0]["count"] = 9
    assert action.encode(decoded)["items"][0]["count"] == 2
    assert spec.descriptor().actions[0].input_schema == SCHEMA
    with pytest.raises(ValueError, match="一个契约作者"):
        ActionSpec("bad", SchemeModel, SchemeModel, codec=codec)


@pytest.mark.parametrize(
    "extra",
    [
        {"pattern": "a.*"},
        {"$ref": "#/$defs/thing"},
        {"oneOf": [SCHEMA]},
        {"unevaluatedProperties": False},
    ],
)
def test_unsupported_schema_is_rejected_before_publication(extra):
    with pytest.raises(ValueError):
        JsonSchemaCodec({**SCHEMA, **extra}, SCHEMA)


def test_domain_pydantic_model_is_not_copied_into_a_second_author():
    class DomainModel(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
        count: int

    action = ActionSpec("inspect", codec=PydanticCodec(DomainModel, DomainModel))
    assert action.schema() == DomainModel.model_json_schema()
    with pytest.raises(ValueError):
        action.decode({"count": True})
    assert action.encode(action.decode({"count": 1})) == {"count": 1}


def test_control_scope_does_not_alias_an_agent_with_identical_strings():
    operator = ControlPrincipal("project", "alice", "run")
    agent = Principal("project", "alice", "run")
    assert operator.scope != agent.scope
    assert len({operator.scope, agent.scope}) == 2


def test_nested_pydantic_model_must_not_silently_drop_extra_fields():
    class LooseChild(BaseModel):
        count: int

    class StrictParent(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
        child: LooseChild

    with pytest.raises(ValueError, match="领域模型必须严格"):
        PydanticCodec(StrictParent, StrictParent)
