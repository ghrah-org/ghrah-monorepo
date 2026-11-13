"""同一组作者模型正反例供 Python 与生成后的 TS 校验器使用。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghrah.protocol import runtime, runtime_agents, runtime_resources
from ghrah.protocol.runtime import RuntimeMeta, RuntimeProblem, RuntimeStandardProblem
from ghrah.protocol.runtime_resources import (
    RuntimeEvent,
    RuntimeMessageCreate,
    RuntimeOperation,
    RuntimePage,
)

CONTRACTS = Path(__file__).resolve().parents[3] / "ghrah-protocol/contracts/runtime"
CASES = json.loads((CONTRACTS / "schema-cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_python_accepts_exactly_the_shared_contract_cases(case):
    model = (
        getattr(runtime, case["schema"], None)
        or getattr(runtime_resources, case["schema"], None)
        or getattr(runtime_agents, case["schema"])
    )
    if case["valid"]:
        model.model_validate(case["body"])
    else:
        with pytest.raises(ValidationError):
            model.model_validate(case["body"])


def test_optional_problem_fields_are_omitted_but_nullable_fields_survive():
    problem = RuntimeProblem.model_validate_json(
        (CONTRACTS / "fixtures/problem.conflict.json").read_text()
    )
    assert "trace_id" not in problem.model_dump()
    assert "details" not in problem.model_dump()
    operation = RuntimeOperation.model_validate_json(
        (CONTRACTS / "fixtures/operation.json").read_text()
    )
    assert operation.model_dump()["problem"] is None


@pytest.mark.parametrize("name", ["detail", "instance", "details", "trace_id"])
def test_optional_non_nullable_problem_fields_reject_explicit_null(name):
    body = json.loads((CONTRACTS / "fixtures/problem.method.json").read_text())
    with pytest.raises(ValidationError):
        RuntimeStandardProblem.model_validate({**body, name: None})


def test_generic_page_preserves_typed_items_and_cursor_invariant():
    page = RuntimePage[RuntimeMeta](
        items=[RuntimeMeta.model_validate_json((CONTRACTS / "fixtures/meta.json").read_text())],
        next_cursor=None,
        has_more=False,
    )
    assert page.items[0].contract == "v1"
    with pytest.raises(ValidationError):
        RuntimePage[RuntimeMeta](items=page.items, next_cursor="next", has_more=False)


def test_message_quota_uses_utf8_bytes_without_truncation():
    text = "界" * (256 * 1024 // 3) + "a"
    assert len(text.encode("utf-8")) == 256 * 1024
    assert RuntimeMessageCreate(text=text, kind="user").text == text
    with pytest.raises(ValidationError):
        RuntimeMessageCreate(text=text + "a", kind="user")


def test_metadata_quota_and_non_json_values():
    metadata = {"x": "界" * 1362 + "aa"}
    assert len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode()) == 4096
    assert RuntimeMessageCreate(text="hello", kind="user", metadata=metadata).metadata == metadata
    for invalid in ({"x": metadata["x"] + "a"}, {"x": float("nan")}, {"x": object()}):
        with pytest.raises(ValidationError):
            RuntimeMessageCreate(text="hello", kind="user", metadata=invalid)


def test_event_representation_does_not_serialize_business_payload():
    body = json.loads((CONTRACTS / "fixtures/event.json").read_text())
    event = RuntimeEvent.model_validate({**body, "payload": {"secret": "not-a-resource-field"}})
    assert "payload" not in event.model_dump(mode="json")
    assert "payload" not in RuntimeEvent.model_json_schema()["properties"]


def test_openapi_only_lists_implemented_routes_and_marks_response_readonly():
    document = json.loads((CONTRACTS / "openapi.json").read_text())
    assert set(document["paths"]) == {
        "/v1/status",
        "/v1/meta",
        "/v1/commands",
        "/v1/agents",
        "/v1/agents/{agent_id}",
        "/v1/agents/{agent_id}/state",
        "/v1/agents/{agent_id}/messages",
        "/v1/deliveries/{delivery_id}",
        "/v1/agents/{agent_id}/timeline",
        "/v1/nodes/{node_id}",
    }
    schemas = document["components"]["schemas"]
    assert schemas["RuntimeDeliveryView"]["readOnly"]
    assert schemas["RuntimeSystemStatus"]["properties"]["ready"]["readOnly"]
    assert schemas["RuntimeMessageCreate"]["additionalProperties"] is False
    assert schemas["RuntimeAgentCreate"]["additionalProperties"] is False
    assert schemas["RuntimeModelInput"]["additionalProperties"] is False
    assert "additionalProperties" not in schemas["RuntimeModelView"]
    method = document["paths"]["/v1/status"]["get"]["responses"]["405"]
    assert method["content"]["application/problem+json"]["schema"]["$ref"].endswith(
        "/RuntimeStandardProblem"
    )
