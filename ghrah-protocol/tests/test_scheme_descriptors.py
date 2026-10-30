# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""公共描述结构及 Python 生成样本与 TS 消费者的一致性。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghrah.protocol.scheme import (
    ActionDescriptor,
    CatalogSnapshot,
    ContractDescriptor,
    DiscoveredAction,
    DiscoveredResource,
)

FIXTURE = (
    Path(__file__).parents[2]
    / "ghrah-observer-webui/packages/protocol/src/__snapshots__/scheme-discovery.json"
)


def sample() -> dict:
    action = ActionDescriptor(
        name="render",
        input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
        output_schema={"type": "object"},
        mutating=False,
        cancellable=True,
        description="渲染",
    )
    descriptor = ContractDescriptor(
        kind="ability", contract="acme.reports", version="1", actions=[action]
    )
    resource = DiscoveredResource(
        kind="ability",
        alias="reports",
        contract=descriptor.contract,
        contract_version="1",
        contract_digest=descriptor.fingerprint(),
        binding_revision=1,
        available=True,
        actions=[
            DiscoveredAction(
                name=action.name,
                description=action.description,
                mutating=False,
                cancellable=True,
                invokable=True,
                input_schema=action.input_schema,
                output_schema=action.output_schema,
            )
        ],
    )
    return {
        "descriptor": descriptor.model_dump(mode="json", by_alias=True),
        "catalog": CatalogSnapshot(entries=[resource]).model_dump(mode="json"),
        "schemas": {
            cls.__name__: cls.model_json_schema(by_alias=True)
            for cls in (
                ActionDescriptor,
                ContractDescriptor,
                DiscoveredAction,
                DiscoveredResource,
                CatalogSnapshot,
            )
        },
    }


def test_python_generated_snapshot_is_current() -> None:
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == sample()


def test_complete_descriptor_rejects_duplicates_and_unknown_fields() -> None:
    descriptor = sample()["descriptor"]
    with pytest.raises(ValidationError):
        ContractDescriptor.model_validate({**descriptor, "actions": descriptor["actions"] * 2})
    with pytest.raises(ValidationError):
        ContractDescriptor.model_validate({**descriptor, "grant": True})
    parsed = ContractDescriptor.model_validate(descriptor)
    assert parsed.fingerprint() == sample()["catalog"]["entries"][0]["contract_digest"]
