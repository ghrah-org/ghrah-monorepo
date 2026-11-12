"""从 Runtime 作者模型生成 OpenAPI、fixtures 和 TS/Zod，不宣称未实现路由可用。"""

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from ghrah.protocol.runtime import (
    RuntimeCapability,
    RuntimeCommandRequest,
    RuntimeMeta,
    RuntimePrincipal,
    RuntimeProblem,
    RuntimeStandardProblem,
    RuntimeSystemStatus,
)
from ghrah.protocol.runtime_agents import (
    RuntimeAgentCreate,
    RuntimeAgentResource,
    RuntimeAgentsPage,
    RuntimeAgentSummary,
    RuntimeAssemblyInput,
    RuntimeStateTransitionResult,
)
from ghrah.protocol.runtime_digest import assembly_digest
from ghrah.protocol.runtime_resources import (
    RuntimeDeliveryView,
    RuntimeEvent,
    RuntimeMessageAccepted,
    RuntimeMessageCreate,
    RuntimeNodePayload,
    RuntimeNodeView,
    RuntimeOperation,
    RuntimeRef,
    RuntimeStateTransition,
    RuntimeTimelinePage,
)
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[1]
MODELS = (
    RuntimePrincipal,
    RuntimeCapability,
    RuntimeMeta,
    RuntimeProblem,
    RuntimeStandardProblem,
    RuntimeSystemStatus,
    RuntimeCommandRequest,
    RuntimeStateTransition,
    RuntimeMessageCreate,
    RuntimeMessageAccepted,
    RuntimeDeliveryView,
    RuntimeOperation,
    RuntimeRef,
    RuntimeEvent,
    RuntimeTimelinePage,
    RuntimeNodeView,
    RuntimeAgentCreate,
    RuntimeAssemblyInput,
    RuntimeAgentResource,
    RuntimeAgentSummary,
    RuntimeAgentsPage,
    RuntimeStateTransitionResult,
)


def fixtures() -> dict[str, BaseModel]:
    """样例经过作者模型校验，避免手写第二份 wire 结构。"""
    stamp = "2026-11-12T00:00:00.000Z"
    suffix = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    agent, message, delivery, node = (
        f"{prefix}_{suffix}" for prefix in ("agt", "msg", "dlv", "nod")
    )
    conflict = RuntimeProblem(
        type="https://ghrah.org/problems/conflict",
        title="Conflict",
        status=409,
        code="CONFLICT",
        retryable=False,
        detail="external_ref is already bound to a different configuration",
    )
    payload = RuntimeNodePayload(messages_delta=[])
    payload_size = len(payload.model_dump_json().encode("utf-8"))
    timeline = {
        "node_id": node,
        "agent_id": agent,
        "session_id": "session-example",
        "branch_id": "branch-example",
        "parent_id": None,
        "iteration": 1,
        "seq": 1,
        "kind": "iteration",
        "created_at": stamp,
    }
    config = RuntimeAssemblyInput.model_validate(
        {
            "model": {"endpoint_name": "your-endpoint", "model_overrides": None},
            "prompt": {"system_prompt": "You are a helpful assistant.", "template_ref": None},
            "context_policy": {
                "max_iterations": 10,
                "communication_timeout": 300,
                "window": None,
                "overrides": None,
            },
            "ability_request": {
                "surface": {"mode": "auto", "static": [], "max_static": None},
                "packages": [
                    {
                        "package": "ghrah.core",
                        "origin": "builtin",
                        "whole_container": False,
                        "operations": [{"key": "conversation", "visible": True, "available": True}],
                        "skills": [],
                    }
                ],
                "mcp_refs": [],
            },
            "permission_request": {
                "auto_approve": [],
                "require_approval_by_default": True,
                "safe_extra_commands": [],
                "safe_extra_sub_commands": None,
            },
        }
    )
    agent_view = RuntimeAgentResource.model_validate(
        {
            "agent_id": agent,
            "scope_ref": "example",
            "external_ref": "example:worker",
            "config": config.model_dump(mode="json"),
            "state": "stopped",
            "incarnation_id": None,
            "config_revision": 1,
            "assembly_digest": assembly_digest(config),
            "resolved_digest": "sha256:example",
            "capability": {
                "tree_revision": 1,
                "tree_digest": "sha256:example",
                "pending_changes": 0,
            },
            "current_delivery": None,
            "last_error": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
    )
    summary = RuntimeAgentSummary.model_validate(
        {
            **agent_view.model_dump(mode="json"),
            "prompt": {"digest": "sha256:example", "size": 28},
            "etag": '"rev-1"',
        }
    )
    return {
        "agent.create.json": RuntimeAgentCreate(external_ref="example:worker", config=config),
        "agent.json": agent_view,
        "agents.page.summary.json": RuntimeAgentsPage(
            items=[summary], next_cursor=None, has_more=False
        ),
        "agent.state.result.json": RuntimeStateTransitionResult(agent=agent_view, recovered=None),
        "meta.json": RuntimeMeta(
            capabilities=[RuntimeCapability(name="runtime.lifecycle", version=1)], server_time=stamp
        ),
        "status.json": RuntimeSystemStatus(
            phase="ready", ready=True, boot_id="example-boot", since=stamp
        ),
        "problem.conflict.json": conflict,
        "problem.precondition.json": RuntimeProblem(
            type="https://ghrah.org/problems/precondition_failed",
            title="Precondition Failed",
            status=412,
            code="PRECONDITION_FAILED",
            retryable=False,
            details={"current_etag": '"rev-12"'},
        ),
        "problem.method.json": RuntimeStandardProblem(
            type="about:blank", title="Method Not Allowed", status=405, retryable=False
        ),
        "state.put.json": RuntimeStateTransition(state="running"),
        "message.create.json": RuntimeMessageCreate(text="Hello", kind="user"),
        "message.accepted.json": RuntimeMessageAccepted(
            message_id=message, delivery_id=delivery, resource_url=f"/v1/deliveries/{delivery}"
        ),
        "delivery.json": RuntimeDeliveryView(
            delivery_id=delivery,
            message_id=message,
            agent_id=agent,
            state="queued",
            attempts=0,
            created_at=stamp,
            updated_at=stamp,
        ),
        "operation.json": RuntimeOperation(
            operation_id=f"op_{suffix}",
            kind="apply_config",
            state="succeeded",
            created_at=stamp,
            updated_at=stamp,
            resource_url=f"/v1/agents/{agent}",
        ),
        "event.json": RuntimeEvent(
            event_id=f"evt_{suffix}",
            seq=1,
            type="agent.updated",
            scope_ref="example",
            occurred_at=stamp,
            resource=RuntimeRef(kind="agent", id=agent, url=f"/v1/agents/{agent}"),
            resource_etag='"rev-12"',
        ),
        "timeline.page.json": RuntimeTimelinePage(
            items=[
                {
                    **timeline,
                    "ability_names": [],
                    "outcome": "success",
                    "summary": {"text": "Completed", "truncated": False},
                    "payload_ref": {"schema_version": 1, "size": payload_size},
                }
            ],
            next_cursor=None,
            has_more=False,
            snapshot_cursor="opaque-example-snapshot",
            watermark_seq=1,
        ),
        "node.json": RuntimeNodeView(
            **timeline, schema_version=1, payload=payload, size=payload_size
        ),
    }


def schema_cases(examples: dict[str, BaseModel]) -> list[dict[str, Any]]:
    """双侧消费同一组正反例，验证生成器没有丢掉约束。"""
    cases: list[dict[str, Any]] = []

    def case(
        name: str, fixture: str, valid: bool, updates: dict[str, Any], omitted: tuple[str, ...] = ()
    ) -> None:
        model = examples[fixture]
        body = copy.deepcopy(model.model_dump(mode="json"))
        body.update(updates)
        for key in omitted:
            body.pop(key, None)
        try:
            type(model).model_validate(body)
            accepted = True
        except ValidationError:
            accepted = False
        if accepted != valid:
            raise ValueError(f"Author model disagrees with conformance example: {name}")
        cases.append({"name": name, "schema": type(model).__name__, "valid": valid, "body": body})

    for name in examples:
        case(name, name, True, {})
    case("unknown response fields", "meta.json", True, {"future_field": {"value": 1}})
    case("future lifecycle phase", "status.json", True, {"phase": "recovering", "ready": False})
    case("inconsistent ready", "status.json", False, {"ready": False})
    case(
        "non-failed error",
        "status.json",
        False,
        {"error": examples["problem.conflict.json"].model_dump(mode="json")},
    )
    case("invalid calendar date", "status.json", False, {"since": "2026-02-30T00:00:00.000Z"})
    case("timestamp without milliseconds", "status.json", False, {"since": "2026-11-12T00:00:00Z"})
    case("non-UTC timestamp", "status.json", False, {"since": "2026-11-12T00:00:00.000+08:00"})
    case(
        "boolean version",
        "meta.json",
        False,
        {"capabilities": [{"name": "runtime.lifecycle", "version": True}]},
    )
    case(
        "nested additive capability",
        "meta.json",
        True,
        {"capabilities": [{"name": "runtime.lifecycle", "version": 1, "future": True}]},
    )
    case("omitted optional detail", "problem.conflict.json", True, {}, ("detail",))
    case("explicit null detail", "problem.conflict.json", False, {"detail": None})
    case("explicit null details", "problem.conflict.json", False, {"details": None})
    case("future error code", "problem.conflict.json", True, {"code": "FUTURE_ERROR"})
    case("missing domain code", "problem.conflict.json", False, {}, ("code",))
    case("invalid standard status", "problem.method.json", False, {"status": 409})
    case("unknown request field", "message.create.json", False, {"scope_ref": "forged"})
    case("null message metadata", "message.create.json", False, {"metadata": None})
    case("missing message kind", "message.create.json", False, {}, ("kind",))
    case("future input kind", "message.create.json", False, {"kind": "future_kind"})
    case("future target state", "state.put.json", False, {"state": "busy"})
    case("future delivery state", "delivery.json", True, {"state": "retrying"})
    case(
        "invalid opaque id",
        "delivery.json",
        False,
        {"delivery_id": "dlv_OOOOOOOOOOOOOOOOOOOOOOOOOO"},
    )
    case("negative attempts", "delivery.json", False, {"attempts": -1})
    case("inconsistent pagination", "timeline.page.json", False, {"has_more": True})
    case("missing next cursor", "timeline.page.json", False, {}, ("next_cursor",))
    case(
        "valid next page",
        "timeline.page.json",
        True,
        {"has_more": True, "next_cursor": "opaque-next"},
    )
    case("future event type", "event.json", True, {"type": "future.updated"})
    case("agent scope cannot come from body", "agent.create.json", False, {"scope_ref": "forged"})
    config = examples["agent.create.json"].model_dump(mode="json")["config"]
    nested = copy.deepcopy(config)
    nested["model"]["future"] = True
    case("unknown nested input", "agent.create.json", False, {"config": nested})
    case("nested additive config response", "agent.json", True, {"config": nested})
    incomplete = copy.deepcopy(config)
    del incomplete["prompt"]["template_ref"]
    case("full assembly requires explicit null", "agent.create.json", False, {"config": incomplete})
    invalid = copy.deepcopy(config)
    invalid["ability_request"]["source_mode"] = "builtin"
    case("removed ability source mode", "agent.create.json", False, {"config": invalid})
    ordered = copy.deepcopy(config)
    ordered["permission_request"]["safe_extra_sub_commands"] = [["git", ["status"]]]
    case("ordered subcommand JSON arrays", "agent.create.json", True, {"config": ordered})
    return cases


def artifacts() -> dict[Path, str]:
    """生成单向契约产物，未支持的 schema 约束显式报错。"""
    schemas: dict[str, Any] = {}
    for model in MODELS:
        schema = model.model_json_schema()
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    lines = [
        "/** Generated by scripts/export_runtime_contracts.py; edit the Python author. */",
        'import { z } from "zod";',
        "const runtimeUtf8Bytes = (v: string) => new TextEncoder().encode(v).length;",
        "const runtimeJsonBytes = (v: unknown): number => {",
        "  const active = new WeakSet<object>();",
        "  const isJson = (item: unknown): boolean => {",
        '    if (item === null || typeof item === "string"',
        '        || typeof item === "boolean") return true;',
        '    if (typeof item === "number") return Number.isFinite(item);',
        '    if (typeof item !== "object" || active.has(item)) return false;',
        "    active.add(item);",
        "    const plain = Object.getPrototypeOf(item) === Object.prototype",
        "      || Object.getPrototypeOf(item) === null;",
        "    const valid = Array.isArray(item) ? item.every(isJson)",
        "      : plain && Object.values(item).every(isJson);",
        "    active.delete(item);",
        "    return valid;",
        "  };",
        "  try {",
        "    if (!isJson(v)) return Infinity;",
        "    return runtimeUtf8Bytes(JSON.stringify(v));",
        "  } catch { return Infinity; }",
        "};",
    ]
    emitted: set[str] = set()

    def expression(schema: dict[str, Any]) -> str:
        supported = {
            "$ref",
            "type",
            "title",
            "description",
            "default",
            "examples",
            "readOnly",
            "const",
            "enum",
            "anyOf",
            "allOf",
            "properties",
            "required",
            "additionalProperties",
            "propertyNames",
            "items",
            "minItems",
            "maxItems",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "pattern",
            "format",
            "x-max-utf8-bytes",
            "x-max-json-bytes",
            "x-runtime-json",
            "prefixItems",
        }
        if unknown := schema.keys() - supported:
            raise ValueError(f"Unsupported runtime schema keywords: {sorted(unknown)}")
        if not schema:
            return "z.unknown()"
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            emit(name)
            return name + "Schema"
        if "const" in schema:
            return f"z.literal({json.dumps(schema['const'])})"
        if "enum" in schema:
            if all(isinstance(value, str) for value in schema["enum"]):
                return f"z.enum({json.dumps(schema['enum'])})"
            return (
                "z.union(["
                + ",".join(f"z.literal({json.dumps(value)})" for value in schema["enum"])
                + "])"
            )
        if "anyOf" in schema:
            return "z.union([" + ",".join(expression(s) for s in schema["anyOf"]) + "])"
        kind = schema.get("type", "object" if "properties" in schema else None)
        if kind == "object":
            if "properties" not in schema:
                item = schema.get("additionalProperties", {})
                key = expression(schema.get("propertyNames", {"type": "string"}))
                return (
                    "z.record("
                    + key
                    + ","
                    + expression(item if isinstance(item, dict) else {})
                    + ")"
                )
            fields = []
            for key, item in schema["properties"].items():
                value = expression(item)
                if item.get("x-runtime-json"):
                    value += ".refine(v=>runtimeJsonBytes(v)!==Infinity)"
                if "x-max-json-bytes" in item:
                    value += f".refine(v=>runtimeJsonBytes(v)<={item['x-max-json-bytes']})"
                if key not in schema.get("required", []):
                    value += ".optional()"
                fields.append(f"{json.dumps(key)}:{value}")
            strict = schema.get("additionalProperties") is False
            value = "z.object({" + ",".join(fields) + "})" + (".strict()" if strict else ".strip()")
            for rule in schema.get("allOf", []):
                condition = expression(rule["if"])
                passed = expression(rule["then"])
                failed = expression(rule.get("else", {}))
                value += (
                    f".refine(v=>{condition}.safeParse(v).success?"
                    f"{passed}.safeParse(v).success:{failed}.safeParse(v).success)"
                )
            if schema.get("readOnly"):
                value += ".readonly()"
            return value
        if kind == "array":
            if "prefixItems" in schema:
                return (
                    "z.tuple(["
                    + ",".join(expression(item) for item in schema["prefixItems"])
                    + "])"
                )
            value = "z.array(" + expression(schema["items"]) + ")"
            for key, method in (("minItems", "min"), ("maxItems", "max")):
                if key in schema:
                    value += f".{method}({schema[key]})"
            return value
        if kind in {"null", "boolean"}:
            return f"z.{kind}()"
        if kind == "string":
            value = "z.string()"
            if schema.get("format") == "date-time":
                value += ".datetime({precision:3,offset:false})"
            elif "format" in schema:
                raise ValueError(f"Unsupported runtime string format: {schema['format']}")
        elif kind in {"integer", "number"}:
            value = "z.number().finite()" + (".int()" if kind == "integer" else "")
        else:
            raise ValueError(f"Unsupported runtime schema: {schema}")
        for key, method in (
            ("minLength", "min"),
            ("maxLength", "max"),
            ("minimum", "min"),
            ("maximum", "max"),
        ):
            if key in schema:
                value += f".{method}({schema[key]})"
        if "exclusiveMinimum" in schema:
            value += f".gt({schema['exclusiveMinimum']})"
        if "exclusiveMaximum" in schema:
            value += f".lt({schema['exclusiveMaximum']})"
        if "pattern" in schema:
            value += ".regex(new RegExp(" + json.dumps(schema["pattern"]) + "))"
        if "x-max-utf8-bytes" in schema:
            value += f".refine(v=>runtimeUtf8Bytes(v)<={schema['x-max-utf8-bytes']})"
        return value

    def emit(name: str) -> None:
        if name not in emitted:
            value = expression(schemas[name])
            lines.extend(
                (
                    f"export const {name}Schema = {value};",
                    f"export type {name} = z.infer<typeof {name}Schema>;",
                )
            )
            emitted.add(name)

    for model in MODELS:
        emit(model.__name__)
    problem_ref = {"$ref": "#/components/schemas/RuntimeProblem"}
    standard_ref = {"$ref": "#/components/schemas/RuntimeStandardProblem"}
    paths: dict[str, Any] = {}
    for name, model in (("status", "RuntimeSystemStatus"), ("meta", "RuntimeMeta")):
        paths[f"/v1/{name}"] = {
            "get": {
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": f"#/components/schemas/{model}"}
                            }
                        },
                    }
                }
            }
        }
    paths["/v1/commands"] = {
        "post": {
            "description": "Transitional command allowlist; this is not the resource API.",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/RuntimeCommandRequest"}
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Existing CoreUnit command receipt",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }
    }

    def resource_operation(
        method: str, request: str | None, response: str, status: str = "200"
    ) -> dict[str, Any]:
        operation: dict[str, Any] = {
            "description": (
                "Requires enabled_agent_api and runtime.agents.conversation capability; "
                "limited assembly subset."
            ),
            "responses": {
                status: {
                    "description": "Success",
                    "content": {
                        "application/json": {"schema": {"$ref": f"#/components/schemas/{response}"}}
                    },
                }
            },
        }
        if request:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": f"#/components/schemas/{request}"}}
                },
            }
        if status in {"201", "202"}:
            operation["responses"][status]["headers"] = {"Location": {"schema": {"type": "string"}}}
        if response in {"RuntimeAgentResource", "RuntimeStateTransitionResult"}:
            operation["responses"][status].setdefault("headers", {})["ETag"] = {
                "schema": {"type": "string"}
            }
        operation["parameters"] = []
        if request in {"RuntimeAgentCreate", "RuntimeMessageCreate"}:
            operation["parameters"].append(
                {
                    "in": "header",
                    "name": "Idempotency-Key",
                    "required": True,
                    "schema": {"type": "string", "maxLength": 128, "pattern": r"^[\x21-\x7e]+$"},
                }
            )
        if method == "put":
            operation["parameters"].append(
                {"in": "header", "name": "If-Match", "required": True, "schema": {"type": "string"}}
            )
        return operation

    paths["/v1/agents"] = {
        "get": resource_operation("get", None, "RuntimeAgentsPage"),
        "post": resource_operation("post", "RuntimeAgentCreate", "RuntimeAgentResource", "201"),
    }
    paths["/v1/agents"]["get"]["parameters"] = [
        {
            "in": "query",
            "name": "limit",
            "schema": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        },
        {"in": "query", "name": "cursor", "schema": {"type": "string"}},
    ]
    paths["/v1/agents/{agent_id}"] = {
        "get": resource_operation("get", None, "RuntimeAgentResource")
    }
    paths["/v1/agents/{agent_id}/state"] = {
        "put": resource_operation("put", "RuntimeStateTransition", "RuntimeStateTransitionResult")
    }
    paths["/v1/agents/{agent_id}/messages"] = {
        "post": resource_operation("post", "RuntimeMessageCreate", "RuntimeMessageAccepted", "202")
    }
    paths["/v1/deliveries/{delivery_id}"] = {
        "get": resource_operation("get", None, "RuntimeDeliveryView")
    }
    paths["/v1/deliveries/{delivery_id}"]["get"]["parameters"].append(
        {
            "in": "query",
            "name": "wait",
            "schema": {"type": "number", "minimum": 0, "maximum": 30, "default": 0},
        }
    )
    paths["/v1/agents/{agent_id}/timeline"] = {
        "get": resource_operation("get", None, "RuntimeTimelinePage")
    }
    timeline = paths["/v1/agents/{agent_id}/timeline"]["get"]
    timeline["description"] = (
        "Requires runtime.timeline v1. Read a bounded snapshot ordered by branch_id/node_seq; "
        "cursor is bound to filters and the current Core boot. No payload bodies in items."
    )
    timeline["parameters"] = [
        {
            "in": "query",
            "name": "limit",
            "schema": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
        },
        *[
            {"in": "query", "name": name, "schema": {"type": "string"}}
            for name in ("cursor", "session_id", "branch_id")
        ],
    ]
    paths["/v1/nodes/{node_id}"] = {"get": resource_operation("get", None, "RuntimeNodeView")}
    node = paths["/v1/nodes/{node_id}"]["get"]
    node["description"] = (
        "Requires runtime.timeline v1. Immutable public payload; "
        "explicit unsupported versions return 406."
    )
    node["parameters"] = [
        {
            "in": "query",
            "name": "schema_version",
            "schema": {"type": "integer", "minimum": 1, "default": 1},
        },
        {"in": "header", "name": "If-None-Match", "schema": {"type": "string"}},
    ]
    node["responses"]["200"]["headers"] = {"ETag": {"schema": {"type": "string"}}}
    node["responses"]["304"] = {
        "description": "Unchanged",
        "headers": {"ETag": {"schema": {"type": "string"}}},
    }
    for route, methods in paths.items():
        if "{" in route:
            parameter = next(
                name for name in ("agent_id", "delivery_id", "node_id") if "{" + name + "}" in route
            )
            for operation in methods.values():
                operation["parameters"].append(
                    {
                        "in": "path",
                        "name": parameter,
                        "required": True,
                        "schema": {"type": "string"},
                    }
                )
    for methods in paths.values():
        for operation in methods.values():
            for code in (
                "403",
                "404",
                "405",
                "406",
                "409",
                "410",
                "412",
                "413",
                "422",
                "428",
                "500",
                "501",
                "503",
            ):
                response: dict[str, Any] = {
                    "description": "Runtime error",
                    "content": {
                        "application/problem+json": {
                            "schema": standard_ref if code in {"405", "413", "428"} else problem_ref
                        }
                    },
                }
                if code == "405":
                    response["headers"] = {"Allow": {"schema": {"type": "string"}}}
                operation["responses"][code] = response
    document = json.loads(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {
                    "title": "Core Runtime",
                    "version": "1",
                    "description": (
                        "Paths describe implemented dispatch operations. "
                        "Additional resource schemas do not advertise route availability."
                    ),
                },
                "paths": paths,
                "components": {"schemas": schemas},
            }
        ).replace("#/$defs/", "#/components/schemas/")
    )
    examples = fixtures()
    result = {
        ROOT / "ghrah-observer-webui/packages/protocol/src/runtime.generated.ts": "\n".join(lines)
        + "\n",
        ROOT / "ghrah-protocol/contracts/runtime/openapi.json": json.dumps(document, indent=2)
        + "\n",
        ROOT / "ghrah-protocol/contracts/runtime/command.list.json": RuntimeCommandRequest(
            command="list_agents", payload={"project_id": "example"}
        ).model_dump_json(indent=2)
        + "\n",
        ROOT / "ghrah-protocol/contracts/runtime/status.ready.json": examples[
            "status.json"
        ].model_dump_json(indent=2)
        + "\n",
    }
    for name, model in examples.items():
        result[ROOT / "ghrah-protocol/contracts/runtime/fixtures" / name] = (
            model.model_dump_json(indent=2) + "\n"
        )
    result[ROOT / "ghrah-protocol/contracts/runtime/schema-cases.json"] = (
        json.dumps(schema_cases(examples), indent=2, ensure_ascii=False) + "\n"
    )
    return result


def main() -> None:
    """写入产物或核对生成一致性。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, content in artifacts().items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"Runtime contract is stale: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


if __name__ == "__main__":
    main()
