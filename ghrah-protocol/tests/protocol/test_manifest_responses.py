"""Manifest 响应与 TS 消费端共享样例，避免字段形状单侧漂移。"""

import json
from pathlib import Path

from ghrah.protocol.payloads.manifest import (
    ManifestGetAbilityResponsePayload,
    ManifestGetAgentResponsePayload,
    ManifestListAbilitiesResponsePayload,
    ManifestPutPayload,
    ManifestResolveAgentResponsePayload,
    ManifestResolvePayload,
    ManifestValidateResponsePayload,
)

FIXTURE = Path(__file__).resolve().parents[3] / (
    "ghrah-observer-webui/packages/protocol/src/__fixtures__/manifest-responses.json"
)


def test_shared_manifest_responses() -> None:
    """权威响应模型可无损读取前后端共同使用的样例。"""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for key, model in (
        ("ability", ManifestGetAbilityResponsePayload),
        ("agent", ManifestGetAgentResponsePayload),
        ("ability_list", ManifestListAbilitiesResponsePayload),
        ("validation_valid", ManifestValidateResponsePayload),
        ("validation_invalid", ManifestValidateResponsePayload),
        ("resolved_agent", ManifestResolveAgentResponsePayload),
    ):
        assert model.model_validate(fixture[key]).model_dump(mode="json") == fixture[key]
    assert (
        ManifestResolvePayload.model_validate(fixture["resolve_request"]).model_dump(mode="json")
        == fixture["resolve_request"]
    )
    assert ManifestPutPayload.model_validate({"full_name": "a", "content": "b"}).model_dump() == {
        "full_name": "a",
        "content": "b",
        "overwrite": False,
        "expected_source_hash": None,
    }


def test_list_read_errors_are_explicit() -> None:
    """损坏文件仅进入错误集合，不冒充能力摘要。"""
    payload = {"abilities": [], "errors": [{"full_name": "audit.broken", "error": "Invalid YAML"}]}
    assert ManifestListAbilitiesResponsePayload.model_validate(payload).model_dump() == payload
