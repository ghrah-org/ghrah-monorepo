#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""只读预览 ghrah 配置迁移；--apply 只创建新文件，不改动输入。"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from ghrah.chat.format import ChatFormat
from ghrah.manifest.agent import AgentManifest

FIELDS = ("temperature", "max_tokens", "top_p", "top_k")


class MigrationError(ValueError):
    """导出或旧配置不满足可确定转换的条件。"""


def load_export(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """校验版本化的完整导出与模型绑定，生成唯一的旧 Agent 名称索引。"""
    if not isinstance(data, dict):
        raise MigrationError("Agent export must be an object")
    payload = {k: v for k, v in data.items() if k != "checksum"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if (
        data.get("export_version") != 1
        or data.get("checksum") != hashlib.sha256(canonical.encode()).hexdigest()
    ):
        raise MigrationError("Agent export version/checksum is invalid")
    if data.get("target_version") != 5 or not isinstance(data.get("agents"), list):
        raise MigrationError("Agent export must describe schema 5 model bindings")
    agents: dict[str, dict[str, Any]] = {}
    endpoints: dict[str, tuple[str, str]] = {}
    endpoint_ids: dict[str, str] = {}
    for row in data["agents"]:
        required = ("agent_id", "agent_name", "endpoint_id", "endpoint_name", "model_name")
        if not isinstance(row, dict) or any(
            not isinstance(row.get(k), str) or not row[k].strip() for k in required
        ):
            raise MigrationError("Agent export has a missing model/endpoint binding")
        name, endpoint = row["agent_name"], row["endpoint_name"]
        binding = (row["endpoint_id"], row["model_name"])
        if name in agents or endpoint in endpoints and endpoints[endpoint] != binding:
            raise MigrationError("Conflicting Agent/endpoint mapping in export")
        if row["endpoint_id"] in endpoint_ids and endpoint_ids[row["endpoint_id"]] != endpoint:
            raise MigrationError("Conflicting endpoint identity in export")
        if not isinstance(row.get("request_params"), dict):
            raise MigrationError("Agent request_params must be an object")
        agents[name] = row
        endpoints[endpoint] = binding
        endpoint_ids[row["endpoint_id"]] = endpoint
    return agents


def convert_config(
    config: dict[str, Any], exported: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """转换 Manifest、spawn/config 与 Project 的 Agent 规格，报告逐字段来源。"""
    agents = load_export(exported)
    result = copy.deepcopy(config)
    report: list[dict[str, Any]] = []

    def params_for(row: dict[str, Any], explicit: dict[str, Any], path: str) -> dict[str, Any]:
        source = row["request_params"]
        values: dict[str, Any] = {}
        source_extra = source.get("extra_params")
        explicit_extra = explicit.get("extra_params")
        source_extra = {} if source_extra is None else source_extra
        explicit_extra = {} if explicit_extra is None else explicit_extra
        if not isinstance(source_extra, dict) or not isinstance(explicit_extra, dict):
            raise MigrationError(f"{path}: extra_params must be an object")
        extra = copy.deepcopy(source_extra)
        extra_sources = {k: "agent_export" for k in extra}
        for k, v in source.items():
            if k not in FIELDS and k != "extra_params":
                extra[k] = copy.deepcopy(v)
                extra_sources[k] = "agent_export"
        for k, v in explicit_extra.items():
            extra[k] = copy.deepcopy(v)
            extra_sources[k] = "ghrah"
        ChatFormat._validate_extra_params(extra)
        for key in FIELDS:
            value, origin = source.get(key), "agent_export"
            if explicit.get(key) is not None:
                value, origin = explicit[key], "ghrah"
            if value is not None:
                values[key] = value
                # 显式类型字段胜出；重复扩展字段不进入运行时第二条覆盖链。
                extra.pop(key, None)
                extra_sources.pop(key, None)
                report.append({"path": f"{path}.{key}", "source": origin, "value": value})
        values["extra_params"] = extra
        report.extend(
            {"path": f"{path}.extra_params.{k}", "source": extra_sources[k], "value": v}
            for k, v in extra.items()
        )
        return values

    def reference(target: dict[str, Any], fallback: str, path: str, manifest: bool) -> None:
        if "endpoint_name" in target:
            if "agent_config_name" in target:
                raise MigrationError(f"{path}: old and new model references conflict")
            raise MigrationError(f"{path}: already has endpoint_name; input must be legacy config")
        old = target.get("agent_config_name")
        if old is None or old == "":
            old = fallback
        if not isinstance(old, str) or old not in agents:
            raise MigrationError(f"{path}: no exported mapping for legacy Agent {old!r}")
        row = agents[old]
        explicit = (
            {k: v for k, v in target.items() if k != "agent_config_name"}
            if manifest
            else target.get("model_overrides")
        )
        explicit = {} if explicit is None else explicit
        if not isinstance(explicit, dict) or explicit.keys() - {*FIELDS, "extra_params"}:
            raise MigrationError(f"{path}: unknown model override fields")
        params = params_for(row, explicit, path if manifest else f"{path}.model_overrides")
        target.pop("agent_config_name", None)
        target["endpoint_name"] = row["endpoint_name"]
        if manifest:
            for field in (*FIELDS, "extra_params"):
                target.pop(field, None)
            target.update(params)
        else:
            target["model_overrides"] = params
        report.append(
            {
                "path": f"{path}.endpoint_name",
                "source": f"agent_export:{old}",
                "value": row["endpoint_name"],
            }
        )

    def walk(node: Any, path: str = "$", context: str = "root") -> None:
        if isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]", context)
            return
        if not isinstance(node, dict):
            return
        if node.get("manifest") == "agent":
            if str(node.get("version", "")).split(".")[0] != "1":
                raise MigrationError(f"{path}: expected legacy Agent Manifest major 1")
            if not isinstance(node.get("model"), dict):
                raise MigrationError(f"{path}: missing model configuration")
            reference(
                node["model"], node.get("metadata", {}).get("name", ""), f"{path}.model", True
            )
            node["version"] = "2"
            AgentManifest.model_validate(node)
            return
        if (
            "agent_config_name" in node
            or context in {"root", "config", "agent", "agents"}
            and "name" in node
            and "agents" not in node
        ):
            if (
                node.get("manifest_ref")
                and "agent_config_name" not in node
                and "endpoint_name" not in node
            ):
                return  # 模型配置由引用的 Manifest 提供，须另行转换该 Manifest。
            reference(node, str(node.get("name", "")), path, False)
            return
        for key, value in node.items():
            walk(value, f"{path}.{key}", key)

    if not isinstance(result, dict):
        raise MigrationError("Config must be an object")
    walk(result)
    if not report:
        raise MigrationError("No legacy Agent configuration found")
    return result, report


def main() -> None:
    """预览差异与来源；显式 apply 时以 0600 权限独占创建新配置。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--agent-export", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.output.resolve() in {args.config.resolve(), args.agent_export.resolve()}:
            raise MigrationError("Output must be a new file, distinct from all inputs")
        original = args.config.read_text(encoding="utf-8")
        data = json.loads(original) if args.config.suffix == ".json" else yaml.safe_load(original)
        converted, report = convert_config(
            data, json.loads(args.agent_export.read_text(encoding="utf-8"))
        )
        new = (
            json.dumps(converted, ensure_ascii=False, indent=2) + "\n"
            if args.output.suffix == ".json"
            else yaml.safe_dump(converted, allow_unicode=True, sort_keys=False)
        )
        print(
            "".join(
                difflib.unified_diff(
                    original.splitlines(True),
                    new.splitlines(True),
                    fromfile=str(args.config),
                    tofile=str(args.output),
                )
            )
        )
        print(json.dumps({"field_sources": report}, ensure_ascii=False, indent=2))
        if args.apply:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(new)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                args.output.unlink(missing_ok=True)
                raise
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Migration refused: {exc}\n")


if __name__ == "__main__":
    main()
