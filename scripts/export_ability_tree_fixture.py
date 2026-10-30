# SPDX-License-Identifier: Apache-2.0
"""从真实领域定义和公共模型导出切片消费者样例；不注册端点或授予许可。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ghrah.ability.contracts import operation_result_schema
from ghrah.ability.versions import capture_file_version
from ghrah.protocol.ability_tree import (
    AbilityNode,
    AbilityTreeView,
    BoundToolNode,
    OperationNode,
    RuntimeObservation,
    TreeChangeResult,
)


def fixtures() -> dict:
    """给 Python、TS、UI 提供同源的正常、空、失效和不可用样例。"""
    version = capture_file_version()
    definition = version.definition()
    ability = AbilityNode(
        alias="files",
        instance_id="files-alice",
        instance_revision=1,
        definition_digest=version.definition_digest,
        version_digest=version.digest,
        revision=1,
        allowed_roots=["/workspace/alice"],
        availability="ready",
        operations=[
            OperationNode(
                key=key,
                description=operation.description,
                input_schema=operation.input_schema,
                output_schema=operation_result_schema(operation.output_schema),
            )
            for key, operation in sorted(definition.operations.items())
        ],
    )
    initial = AbilityTreeView(
        project_id="project",
        agent_id="alice",
        run_id="alice-run",
        revision=1,
        abilities=[ability],
        bound_tools=[],
        runtime=RuntimeObservation(host_id="fixture", observed_at="2026-10-29T00:00:00Z"),
    )
    selected = initial.model_copy(
        update={
            "revision": 2,
            "bound_tools": [
                BoundToolNode(
                    name="read_report",
                    alias="files",
                    member="read_file",
                    grant_revision=1,
                    revision=2,
                )
            ],
        }
    )
    revoked = initial.model_copy(update={"revision": 3, "abilities": [], "bound_tools": []})
    unavailable = initial.model_copy(
        update={"abilities": [ability.model_copy(update={"availability": "unavailable"})]}
    )
    return {
        "initial": initial.model_dump(mode="json"),
        "selected": selected.model_dump(mode="json"),
        "revoked": revoked.model_dump(mode="json"),
        "unavailable": unavailable.model_dump(mode="json"),
        "conflict": TreeChangeResult(error="conflict").model_dump(mode="json"),
        "bound": TreeChangeResult(view=selected).model_dump(mode="json"),
    }


def main() -> None:
    """生成或校验提交入仓的消费者样例。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / (
        "ghrah-observer-webui/packages/protocol/src/__fixtures__/ability-tree.json"
    )
    expected = json.dumps(fixtures(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if target.read_text() != expected:
            raise SystemExit("Ability tree fixture is stale")
    else:
        target.write_text(expected)


if __name__ == "__main__":
    main()
