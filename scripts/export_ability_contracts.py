# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""从 Python 作者模型单向生成 Ability 消费者的 Zod 校验器。

三组生成物共享同一个受限 schema 子集：
- ``ability-tree.generated.ts``：既有 Scheme 能力树契约；
- ``ability-control.generated.ts``：观察端能力管理面契约；
- ``agent-control.generated.ts``：观察端 Agent 资源契约。
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ghrah.ability.content import ContentDescription, ContentReadRequest, ContentReadResult
from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiErrorResponse,
    AbilityApiErrorView,
    AbilityApiFeatures,
    AbilityApiStatus,
    AbilityBoundToolView,
    AbilityChangesPage,
    AbilityChangeView,
    AbilityGrantPolicyView,
    AbilityGrantView,
    AbilityInstancesView,
    AbilityInstanceVersionView,
    AbilityInstanceView,
    AbilityLifecycleView,
    AbilityPackageCatalogView,
    AbilityPackageDetailView,
    AbilityPackageVersionDetail,
    AbilityPackageVersionRow,
    AbilityPendingRespawnView,
    AbilitySurfaceEntryView,
    AbilitySurfaceView,
    AbilityTreeChangeResult,
    AbilityUnboundVersionView,
    AbilityVerifyView,
)
from ghrah.protocol.ability_tree import (
    AbilityTreeView,
    BindToolRequest,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    TreeChangeResult,
    UnbindToolRequest,
)
from ghrah.protocol.agent_control import (
    AgentAbilitySummary,
    AgentAdminView,
    AgentCreateRequest,
    AgentDeletedView,
    AgentDesiredStateRequest,
    AgentListView,
    AgentPromptSummary,
    AgentRuntimeView,
    AgentSpecificationDetail,
    AgentSpecificationSummary,
    AgentSpecPatch,
    AgentSummaryView,
)
from pydantic import BaseModel

TREE_MODELS = (
    AbilityTreeView,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    BindToolRequest,
    UnbindToolRequest,
    TreeChangeResult,
    ContentReadRequest,
    ContentDescription,
    ContentReadResult,
)

CONTROL_MODELS = (
    AbilityApiStatus,
    AbilityApiFeatures,
    AbilityGrantView,
    AbilityBoundToolView,
    AbilitySurfaceEntryView,
    AbilityPendingRespawnView,
    AbilityLifecycleView,
    AbilityGrantPolicyView,
    AbilitySurfaceView,
    AbilityAdminView,
    AbilityChangeView,
    AbilityChangesPage,
    AbilityTreeChangeResult,
    AbilityApiErrorView,
    AbilityApiErrorResponse,
    AbilityPackageVersionRow,
    AbilityPackageCatalogView,
    AbilityVerifyView,
    AbilityPackageVersionDetail,
    AbilityPackageDetailView,
    AbilityInstanceVersionView,
    AbilityInstanceView,
    AbilityUnboundVersionView,
    AbilityInstancesView,
)

AGENT_MODELS = (
    AgentPromptSummary,
    AgentRuntimeView,
    AgentAbilitySummary,
    AgentSpecificationSummary,
    AgentSpecificationDetail,
    AgentSummaryView,
    AgentListView,
    AgentAdminView,
    AgentCreateRequest,
    AgentSpecPatch,
    AgentDesiredStateRequest,
    AgentDeletedView,
)

TREE_TARGET = "ghrah-observer-webui/packages/protocol/src/ability-tree.generated.ts"
CONTROL_TARGET = "ghrah-observer-webui/packages/protocol/src/ability-control.generated.ts"
AGENT_TARGET = "ghrah-observer-webui/packages/protocol/src/agent-control.generated.ts"


def _schema_expression(
    schema: dict[str, Any],
    emit: Callable[[str], None],
    external: frozenset[str],
    imported: set[str],
) -> str:
    """把受限 JSON Schema 片段翻译成 zod 表达式；未知规则直接失败。"""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in external:
            imported.add(name)
            return name + "Schema"
        emit(name)
        return name + "Schema"
    if "const" in schema:
        return f"z.literal({json.dumps(schema['const'], ensure_ascii=False)})"
    if "enum" in schema:
        return f"z.enum({json.dumps(schema['enum'], ensure_ascii=False)})"
    if "anyOf" in schema:
        items = ",".join(
            _schema_expression(item, emit, external, imported) for item in schema["anyOf"]
        )
        return "z.union([" + items + "])"
    kind = schema.get("type")
    if kind == "object":
        if "properties" not in schema:
            return "z.record(z.unknown())"
        fields = []
        for key, item in schema["properties"].items():
            value = _schema_expression(item, emit, external, imported)
            if key not in schema.get("required", []):
                if "default" in item:
                    value += f".default({json.dumps(item['default'], ensure_ascii=False)})"
                elif item.get("type") == "array":
                    value += ".default([])"
            fields.append(f"{json.dumps(key)}:{value}")
        return "z.object({" + ",".join(fields) + "}).strict()"
    if kind == "array":
        value = "z.array(" + _schema_expression(schema["items"], emit, external, imported) + ")"
        for rule, method in (("minItems", "min"), ("maxItems", "max")):
            if rule in schema:
                value += f".{method}({schema[rule]})"
        return value
    if kind == "null":
        return "z.null()"
    if kind == "boolean":
        return "z.boolean()"
    if kind == "string":
        value = "z.string()"
        if schema.get("format") == "date-time":
            value += ".datetime()"
        if "pattern" in schema:
            value += ".regex(new RegExp(" + json.dumps(schema["pattern"]) + "))"
        return value
    if kind in {"integer", "number"}:
        value = "z.number()" + (".int()" if kind == "integer" else "")
        for rule, method in (("minimum", "min"), ("maximum", "max")):
            if rule in schema:
                value += f".{method}({schema[rule]})"
        return value
    raise ValueError(f"未支持的 schema: {schema}")


def _generate(
    models: tuple[type[BaseModel], ...],
    *,
    external: frozenset[str] = frozenset(),
    preamble: tuple[str, ...] = (),
) -> str:
    """生成一组模型对应的 zod 校验器；同一 schema 子集，未知规则拒绝生成。"""
    definitions: dict[str, Any] = {}
    for model in models:
        schema = model.model_json_schema()
        definitions.update(schema.pop("$defs", {}))
        definitions[model.__name__] = schema
    lines = [
        # 生成物头部的 SPDX 标签以字面量形式出现在本文件里；用 REUSE ignore 块
        # 阻止 reuse lint 把它误读成本文件自身的许可声明。
        # REUSE-IgnoreStart
        "// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>",
        "//",
        "// SPDX-License-Identifier: Apache-2.0",
        # REUSE-IgnoreEnd
        "",
        "/** Generated by scripts/export_ability_contracts.py; edit the Python author. */",
        'import { z } from "zod";',
        *preamble,
    ]
    preamble_end = len(lines)
    imported: set[str] = set()
    emitted: set[str] = set()

    def emit(name: str) -> None:
        if name in emitted:
            return
        value = _schema_expression(definitions[name], emit, external, imported)
        if name == "AbilityNode":
            value += ".refine(v=>new Set(v.operations.map(x=>x.key)).size===v.operations.length)"
        if name == "AbilityTreeView":
            for field, key in (("abilities", "alias"), ("bound_tools", "name")):
                value += f".refine(v=>new Set(v.{field}.map(x=>x.{key})).size===v.{field}.length)"
        if name == "TreeChangeResult":
            value += ".refine(v=>(v.view===null)!==(v.error===null))"
        lines.append(f"export const {name}Schema = {value};")
        lines.append(f"export type {name} = z.infer<typeof {name}Schema>;")
        emitted.add(name)

    for model in models:
        emit(model.__name__)
    if imported:
        names = ", ".join(f"{name}Schema" for name in sorted(imported))
        lines.insert(preamble_end, f'import {{ {names} }} from "./ability-tree.generated.js";')
    return "\n".join(lines) + "\n"


def _targets() -> list[tuple[Path, str]]:
    """返回三组生成物的目标路径与预期内容。"""
    root = Path(__file__).resolve().parents[1]
    tree_text = _generate(
        TREE_MODELS,
        preamble=(
            "export const AbilityTreeIdSchema = z.string().regex("
            "/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/);",
        ),
    )
    exported = frozenset(re.findall(r"export const (\w+)Schema", tree_text))
    control_text = _generate(CONTROL_MODELS, external=exported)
    agent_text = _generate(AGENT_MODELS)
    return [
        (root / TREE_TARGET, tree_text),
        (root / CONTROL_TARGET, control_text),
        (root / AGENT_TARGET, agent_text),
    ]


def main() -> None:
    """生成或者核对字段和约束的单向产物。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale: list[str] = []
    for target, text in _targets():
        current = target.read_text() if target.exists() else ""
        if current == text:
            continue
        if args.check:
            stale.append(str(target))
            continue
        target.write_text(text)
    if stale:
        raise SystemExit("Ability contracts are stale: " + ", ".join(stale))


if __name__ == "__main__":
    main()
