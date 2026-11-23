#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""把旧 YAML 模板库导入 ``agent_templates``（一次性迁移工具）。

背景：模板的权威位置从「发行版目录里的 YAML」改为「实例的 subject.db」。本脚本
读取**一个**发行版的 manifest 目录，把其中的 agent 模板逐条写入目标库。

用法::

    # 预演（默认）：只报告，不写库
    uv run python scripts/migrate_agent_templates.py \\
        --source ~/.ghrah/generic/data/manifests/agents \\
        --db ~/.ghrah/generic/data/subject.db

    # 落库
    uv run python scripts/migrate_agent_templates.py --source ... --db ... --apply

行为约定：

- ``--source`` 必须显式指定**一个**发行版的 manifest 目录，不自动合并多个发行版
  （同名模板会互相覆盖）。
- 业务身份 = ``metadata.namespace`` + ``metadata.name``；已存在时默认 ``skip``，
  ``--on-conflict=update`` 会追加一个新版本（revision + 1）。
- v1 的 ``model.agent_config_name`` 归一为 ``model.endpoint_name``；缺 ``version``
  行的按 v1 处理并在报告里标注。
- 控制成员（``conversation`` / ``end_task``）不写入 ``ability_request``：它们是
  Agent 循环的控制机制，不属于授权面。
- 无法表达的部分一律进报告，不静默丢弃：``hooks``（模板层无落点）、
  ``permissions.allowed_paths``（模板是 workspace_role + subpath，绝对路径无法反解）、
  成员级 shell/net 权限（只能塌成 Agent 级 ``permission_request``）、
  无法解析的成员名。
- ``--strict`` 时，出现上述未解析项即失败（退出码 3），便于先修数据再导入。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from ghrah.subject.template_store import AgentTemplateConfig, TemplateStore
from ghrah.subject.template_store.store import TemplateConflictError

#: 循环控制成员：始终由 Agent 原控制机制提供，不进授权面。
CONTROL_MEMBERS: frozenset[str] = frozenset({"conversation", "end_task"})

#: 默认扫描仓内内置能力包（导入脚本通常在仓库内执行）。
_DEFAULT_ABILITY_ROOT = (
    Path(__file__).resolve().parent.parent / "ghrah-core/src/ghrah/ability/builtin"
)

_EXIT_OK = 0
_EXIT_REFUSED = 2
_EXIT_STRICT = 3


@dataclass
class AbilityIndex:
    """``(package, member)`` 索引：用于把 ``type`` / ``ref`` 映射成申请。"""

    packages: set[str] = field(default_factory=set)
    members: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def add_package(self, package: str, keys: list[str]) -> None:
        """登记一个包及其成员键。"""
        self.packages.add(package)
        for key in keys:
            self.members.setdefault(key, []).append((package, key))

    def resolve_type(self, name: str) -> tuple[str, str] | None:
        """按成员键解析 ``type``；多个包同名成员视为不可判定。"""
        hits = self.members.get(name)
        if not hits or len(hits) > 1:
            return None
        return hits[0]

    def resolve_ref(self, ref: str) -> tuple[str, str | None] | None:
        """解析 ``ref``：整包 → ``(package, None)``；成员 → ``(package, member)``。"""
        if ref in self.packages:
            return (ref, None)
        if "." in ref:
            package, member = ref.rsplit(".", 1)
            if (package, member) in self.members.get(member, []):
                return (package, member)
            if package in self.packages:
                return (package, member)
        return None


@dataclass
class Report:
    """导入报告（预演与落库共用）。"""

    imported: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    dropped_hooks: list[str] = field(default_factory=list)
    requires_path_review: list[dict[str, str]] = field(default_factory=list)
    coarsened_permissions: list[dict[str, str]] = field(default_factory=list)
    unresolved_members: list[dict[str, str]] = field(default_factory=list)
    assumed_v1: list[str] = field(default_factory=list)

    @property
    def unresolved(self) -> int:
        """需要人工处理的条目数。"""
        return (
            len(self.dropped_hooks)
            + len(self.requires_path_review)
            + len(self.coarsened_permissions)
            + len(self.unresolved_members)
            + len(self.failed)
        )

    def as_dict(self, *, applied: bool) -> dict[str, Any]:
        """报告视图（JSON 可序列化）。"""
        return {
            "applied": applied,
            "imported": self.imported,
            "updated": self.updated,
            "skipped_existing": self.skipped_existing,
            "failed": self.failed,
            "dropped_hooks": self.dropped_hooks,
            "requires_path_review": self.requires_path_review,
            "coarsened_permissions": self.coarsened_permissions,
            "unresolved_members": self.unresolved_members,
            "assumed_version_1": self.assumed_v1,
            "unresolved_total": self.unresolved,
        }


def load_ability_index(roots: list[Path]) -> AbilityIndex:
    """从 ``ability.yaml`` 汇总包与成员索引（静态读取，不启动 Core）。"""
    index = AbilityIndex()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("ability.yaml")):
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except OSError, yaml.YAMLError:
                continue
            identity = document.get("identity") or {}
            namespace = str(identity.get("namespace") or "").strip()
            name = str(identity.get("name") or "").strip()
            if not namespace or not name:
                continue
            members = document.get("members") or {}
            if isinstance(members, list):
                operations = [
                    str(item.get("member", "")).rsplit(".", 1)[-1]
                    for item in members
                    if isinstance(item, dict) and item.get("type") == "operation"
                ]
            else:
                operations = list(members.get("operations") or {})
            index.add_package(f"{namespace}.{name}", operations)
    return index


def _split_model(
    model: Any, report: Report, source: Path
) -> tuple[str | None, dict[str, Any] | None]:
    """``model`` → ``(endpoint_name, model_overrides)``；v1 的 agent_config_name 归一。"""
    if not isinstance(model, dict):
        return (None, None)
    endpoint = model.get("endpoint_name") or model.get("agent_config_name")
    if model.get("agent_config_name") and not model.get("endpoint_name"):
        report.assumed_v1.append(f"{source}: model.agent_config_name → endpoint_name")
    overrides = {
        key: value
        for key, value in model.items()
        if key not in {"endpoint_name", "agent_config_name"} and value is not None
    }
    return (None if endpoint is None else str(endpoint), overrides or None)


def _abilities(
    entries: Any, index: AbilityIndex, report: Report, source: Path
) -> list[dict[str, Any]]:
    """``abilities[]`` → ``available_members``（三段成员名，按成员展开）。"""
    members: dict[str, dict[str, Any]] = {}

    def member_entry(package: str, key: str) -> dict[str, Any]:
        namespace, _, name = package.partition(".")
        name = name or namespace
        full_name = f"{namespace}.{name}.{key}"
        entry = {
            "namespace": namespace,
            "name": name,
            "member": full_name,
            "type": "operation",
            "version": None,
            "ability_type": "kernel",
        }
        return members.setdefault(full_name, entry)

    def expand(package: str) -> None:
        for key, hits in index.members.items():
            if any(hit_package == package for hit_package, _ in hits):
                member_entry(package, key)

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("ref"):
            ref = str(entry["ref"])
            resolved = index.resolve_ref(ref)
            if resolved is None:
                report.unresolved_members.append({"source": str(source), "ref": ref})
                continue
            package, member = resolved
            if member is None:
                expand(package)
            else:
                member_entry(package, member)
        elif entry.get("type"):
            name = str(entry["type"])
            if name in CONTROL_MEMBERS:
                continue
            resolved = index.resolve_type(name)
            if resolved is None:
                report.unresolved_members.append({"source": str(source), "type": name})
                continue
            member_entry(resolved[0], resolved[1])
    return sorted(members.values(), key=lambda item: str(item["member"]))


def _context_policy(document: dict[str, Any]) -> dict[str, Any]:
    """``max_iterations`` / ``communication_timeout`` / ``context.*`` → 一组。"""
    policy: dict[str, Any] = {}
    for key in ("max_iterations", "communication_timeout"):
        if document.get(key) is not None:
            policy[key] = document[key]
    context = document.get("context") or {}
    if isinstance(context, dict):
        if context.get("window") is not None:
            policy["window"] = context["window"]
        persistence = context.get("persistence")
        if isinstance(persistence, dict):
            policy["overrides"] = {"persistence": persistence}
    return policy


def _permissions(entries: Any, report: Report, source: Path) -> dict[str, Any]:
    """``abilities[].permissions`` → Agent 级 ``permission_request``（降维并报告）。"""
    request: dict[str, Any] = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        permissions = entry.get("permissions") or {}
        if not isinstance(permissions, dict) or not permissions:
            continue
        paths = permissions.get("allowed_paths") or []
        if paths:
            report.requires_path_review.append(
                {
                    "source": str(source),
                    "member": str(entry.get("ref") or entry.get("type") or ""),
                    "allowed_paths": json.dumps(paths, ensure_ascii=False),
                }
            )
        commands = permissions.get("allowed_commands")
        if commands:
            request["safe_extra_commands"] = [
                str(item) for item in commands if isinstance(item, str)
            ]
        others = {
            key: value
            for key, value in permissions.items()
            if key not in {"allowed_paths", "allowed_commands"} and value
        }
        if others:
            report.coarsened_permissions.append(
                {
                    "source": str(source),
                    "member": str(entry.get("ref") or entry.get("type") or ""),
                    "dropped": json.dumps(others, ensure_ascii=False, sort_keys=True),
                }
            )
    return request


def build_config(
    document: dict[str, Any],
    *,
    path: Path,
    index: AbilityIndex,
    report: Report,
) -> AgentTemplateConfig:
    """YAML 文档 → 规范化模板配置。"""
    metadata = document.get("metadata") or {}
    endpoint, overrides = _split_model(document.get("model"), report, path)
    abilities = document.get("abilities") or []
    payload: dict[str, Any] = {
        "metadata": metadata,
        "model": {"endpoint_name": endpoint, "model_overrides": overrides},
        "prompt": {"system_prompt": document.get("system_prompt") or ""},
        "context_policy": _context_policy(document),
        "available_members": _abilities(abilities, index, report, path),
        "permission_request": _permissions(abilities, report, path),
        "path_request": [],
    }
    if document.get("hooks"):
        report.dropped_hooks.append(str(path))
    return AgentTemplateConfig.model_validate(payload)


def iter_manifests(source: Path) -> list[Path]:
    """``<source>/<namespace>/<name>.yaml``，按路径排序保证可复现。"""
    return sorted(path for path in source.rglob("*.yaml") if path.is_file())


async def run(args: argparse.Namespace) -> int:
    """执行导入；返回进程退出码。"""
    source = Path(args.source).expanduser()
    if not source.is_dir():
        print(f"refused: --source 不是目录：{source}", file=sys.stderr)
        return _EXIT_REFUSED
    db_path = Path(args.db).expanduser()
    if args.apply and not db_path.parent.is_dir():
        print(f"refused: --db 的父目录不存在：{db_path.parent}", file=sys.stderr)
        return _EXIT_REFUSED

    index = load_ability_index([Path(item).expanduser() for item in args.ability_root])
    report = Report()
    manifests = iter_manifests(source)

    store = TemplateStore(db_path)
    if args.apply:
        await store.start()
    try:
        for path in manifests:
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if document.get("manifest") != "agent":
                    continue
                config = build_config(document, path=path, index=index, report=report)
            except Exception as error:  # noqa: BLE001 - 逐条失败不中断整批
                report.failed.append({"source": str(path), "error": str(error)})
                continue
            label = f"{config.metadata.namespace}.{config.metadata.name}"
            if not args.apply:
                report.imported.append(label)
                continue
            existing = await store.get_by_identity(config.metadata.namespace, config.metadata.name)
            if existing is None:
                record = await store.create(config)
                report.imported.append(f"{label}#{record.revision}")
            elif args.on_conflict == "update":
                try:
                    record = await store.update(existing.template_id, existing.revision, config)
                except TemplateConflictError as error:
                    report.failed.append({"source": str(path), "error": str(error)})
                    continue
                report.updated.append(f"{label}#{record.revision}")
            else:
                report.skipped_existing.append(label)
    finally:
        if args.apply:
            await store.stop()

    payload = report.as_dict(applied=args.apply)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).expanduser().write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.strict and report.unresolved:
        print(f"strict: 存在 {report.unresolved} 项待人工处理", file=sys.stderr)
        return _EXIT_STRICT
    return _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="把旧 YAML 模板库导入 agent_templates")
    parser.add_argument("--source", required=True, help="一个发行版的 manifests/agents 目录")
    parser.add_argument("--db", required=True, help="目标实例的 subject.db")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认仅预演）")
    parser.add_argument(
        "--on-conflict",
        choices=("skip", "update"),
        default="skip",
        help="身份已存在时的处理：跳过（默认）或追加新版本",
    )
    parser.add_argument(
        "--ability-root",
        action="append",
        default=[str(_DEFAULT_ABILITY_ROOT)],
        help="能力包根目录（可重复；用于把 type/ref 映射成包与成员）",
    )
    parser.add_argument("--report", help="把 JSON 报告另存到该路径")
    parser.add_argument("--strict", action="store_true", help="存在未解析项即失败")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
