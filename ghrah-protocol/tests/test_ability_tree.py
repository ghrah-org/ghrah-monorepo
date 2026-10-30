# SPDX-License-Identifier: Apache-2.0
"""公共切片模型与 TypeScript 使用相同的已导出样例。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghrah.protocol.ability_tree import AbilityTreeView, BindToolRequest, TreeChangeResult

FIXTURE = Path(__file__).resolve().parents[2] / (
    "ghrah-observer-webui/packages/protocol/src/__fixtures__/ability-tree.json"
)


def test_shared_consumer_cases():
    fixture = json.loads(FIXTURE.read_text())
    for key in ("initial", "selected", "revoked", "unavailable"):
        assert AbilityTreeView.model_validate(fixture[key]).model_dump(mode="json") == fixture[key]
    for key in ("bound", "conflict"):
        assert TreeChangeResult.model_validate(fixture[key]).model_dump(mode="json") == fixture[key]


@pytest.mark.parametrize("extra", [{"expected_revision": True}, {"principal": "admin"}])
def test_requests_do_not_coerce_or_accept_self_reported_authority(extra):
    request = dict(alias="files", member="read_file", tool_name="read_report", expected_revision=1)
    with pytest.raises(ValidationError):
        BindToolRequest.model_validate({**request, **extra})


def test_result_requires_exactly_one_result_or_error():
    with pytest.raises(ValidationError):
        TreeChangeResult()


@pytest.mark.parametrize("duplicate", ["ability", "tool", "operation"])
def test_view_rejects_ambiguous_node_names(duplicate):
    fixture = json.loads(FIXTURE.read_text())["selected"]
    if duplicate == "ability":
        fixture["abilities"].append(fixture["abilities"][0])
    elif duplicate == "tool":
        fixture["bound_tools"].append(fixture["bound_tools"][0])
    else:
        operations = fixture["abilities"][0]["operations"]
        operations.append(operations[0])
    with pytest.raises(ValidationError):
        AbilityTreeView.model_validate(fixture)
