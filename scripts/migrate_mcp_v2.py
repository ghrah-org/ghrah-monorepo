#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""清理旧版 MCP 接线遗留数据（破坏性迁移，默认只预演）。

旧版把 ``ghrah.mcp.mcp_call`` 作为模型可见工具，并在模板/实例里保存包级绑定引用。
新执行层只接受页面安装的成员（``type=mcp`` + 成员 data 里的 server/tool）。本脚本
从 ``subject.db`` 的模板与实例快照中移除退役引用；安装根里的新 MCP 包不会被删除。

用法::

    # 预演（默认）：只报告
    uv run python scripts/migrate_mcp_v2.py --subject-db ~/.ghrah/subject.db

    # 落库
    uv run python scripts/migrate_mcp_v2.py --subject-db ~/.ghrah/subject.db --apply

行为约定：

- 只删除精确的退役引用：``ghrah.mcp.mcp_call`` / ``mcp_call`` / ``ghrah.mcp``。
- 不清除合法的新 MCP 成员（``type=mcp`` 且 member 形如 ``mcp.<name>.<tool>``）。
- 模板只改 ``available_members``；实例只改 ``members``、``mcp_refs`` 与
  ``surface.static``。其他 JSON 字段不动。
- 每个受影响的版本都会写回 revision 不变（数据清理不是业务版本升级）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_RETIRED_MEMBERS = frozenset({"ghrah.mcp.mcp_call", "mcp_call"})
_RETIRED_PACKAGES = frozenset({"ghrah.mcp"})
_EXIT_OK = 0
_EXIT_REFUSED = 2


def _is_retired(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value in _RETIRED_MEMBERS or value in _RETIRED_PACKAGES


def scrub(value: Any) -> Any:
    """递归删除退役成员引用；保持其他结构不变。"""
    if isinstance(value, list):
        cleaned = []
        for item in value:
            if isinstance(item, dict):
                if _is_retired(item.get("member")) or _is_retired(item.get("ref")):
                    continue
                if _is_retired(item.get("type")) and item.get("member") in _RETIRED_MEMBERS:
                    continue
                cleaned.append(scrub(item))
            elif not _is_retired(item):
                cleaned.append(scrub(item))
        return cleaned
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"static", "mcp_refs"} and isinstance(item, list):
                result[key] = [entry for entry in item if not _is_retired(entry)]
            elif key in {"members", "available_members"} and isinstance(item, list):
                result[key] = scrub(item)
            else:
                result[key] = scrub(item)
        return result
    return value


def _load(text: str) -> Any:
    try:
        return json.loads(text)
    except TypeError, ValueError:
        return text


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _has_retired(value: Any) -> bool:
    if isinstance(value, str):
        return _is_retired(value)
    if isinstance(value, list):
        return any(_has_retired(item) for item in value)
    if isinstance(value, dict):
        return any(_has_retired(item) for item in value.values())
    return False


def _clean_template_db(path: Path, apply: bool) -> int:
    if not path.is_file():
        print(f"skip: subject db not found: {path}")
        return 0
    changed = 0
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT template_id, revision, available_members FROM agent_templates"
        ).fetchall()
        for template_id, revision, raw in rows:
            value = _load(raw)
            if not _has_retired(value):
                continue
            cleaned = scrub(value)
            changed += 1
            print(f"template {template_id}@{revision}: remove retired MCP refs")
            if apply:
                db.execute(
                    "UPDATE agent_templates SET available_members = ? "
                    "WHERE template_id = ? AND revision = ?",
                    (_dump(cleaned), template_id, revision),
                )
        if apply:
            db.commit()
    return changed


def _clean_instance_db(path: Path, apply: bool) -> int:
    if not path.is_file():
        print(f"skip: agent instances db not found: {path}")
        return 0
    changed = 0
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT agent_id, members, surface, mcp_refs FROM agent_instances"
        ).fetchall()
        for agent_id, members, surface, mcp_refs in rows:
            members_value = _load(members)
            surface_value = _load(surface) if surface else None
            refs_value = _load(mcp_refs)
            if not (
                _has_retired(members_value)
                or _has_retired(surface_value)
                or _has_retired(refs_value)
            ):
                continue
            changed += 1
            print(f"instance {agent_id}: remove retired MCP refs")
            if apply:
                db.execute(
                    "UPDATE agent_instances SET members = ?, surface = ?, mcp_refs = ? "
                    "WHERE agent_id = ?",
                    (
                        _dump(scrub(members_value)),
                        _dump(scrub(surface_value)) if surface_value is not None else None,
                        _dump(scrub(refs_value)),
                        agent_id,
                    ),
                )
        if apply:
            db.commit()
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subject-db",
        default=str(Path.home() / ".ghrah" / "subject.db"),
        help="模板库路径（默认 ~/.ghrah/subject.db）",
    )
    parser.add_argument(
        "--agents-db",
        default="",
        help="Agent 实例库路径；缺省跳过",
    )
    parser.add_argument("--apply", action="store_true", help="实际写库；缺省只预演")
    args = parser.parse_args(argv)

    subject_db = Path(args.subject_db).expanduser()
    if not subject_db.exists():
        print(f"refused: subject db not found: {subject_db}", file=sys.stderr)
        return _EXIT_REFUSED

    changed = _clean_template_db(subject_db, args.apply)
    if args.agents_db:
        changed += _clean_instance_db(Path(args.agents_db).expanduser(), args.apply)

    mode = "applied" if args.apply else "dry-run"
    print(f"{mode}: {changed} record(s) or path(s) would change")
    if not args.apply and changed:
        print("re-run with --apply to write changes")
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
