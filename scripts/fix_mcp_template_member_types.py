#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""修复模板中 MCP 成员的错误类型（一次性数据修复，默认只预演）。

旧模板编辑器把 MCP 成员也写成了 ``type: operation``；创建 Agent 时 Core 会按安装
定义校验为 ``mcp``，从而报 ``member type mismatch``。本脚本只改模板存储里的
``available_members[].type``，不创建新 revision、不改其他字段。

用法::

    # 预演
    uv run python scripts/fix_mcp_template_member_types.py \
        --db ~/.ghrah/debug_v2/data/subject.db

    # 落库（写前自动做 sqlite 备份）
    ... --apply

执行前建议先停止目标实例，避免运行中的服务同时写 subject.db。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_EXIT_OK = 0
_EXIT_REFUSED = 2


def _is_mcp_member(member: dict[str, Any]) -> bool:
    name = str(member.get("member", ""))
    namespace = str(member.get("namespace", ""))
    return name.startswith("mcp.") or namespace == "mcp"


def normalize_members(value: Any) -> tuple[Any, int]:
    """把 MCP 成员类型归一为 mcp，返回新值与修改数量。"""
    if not isinstance(value, list):
        return value, 0
    changed = 0
    result = []
    for item in value:
        if not isinstance(item, dict):
            result.append(item)
            continue
        if _is_mcp_member(item) and item.get("type") != "mcp":
            item = {**item, "type": "mcp"}
            changed += 1
        result.append(item)
    return result, changed


def _backup(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    with sqlite3.connect(path) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
    return target


def fix_templates(path: Path, *, apply: bool) -> int:
    if not path.is_file():
        print(f"refused: database not found: {path}", file=sys.stderr)
        return _EXIT_REFUSED
    db = sqlite3.connect(path)
    try:
        rows = db.execute(
            "SELECT template_id, revision, available_members FROM agent_templates"
        ).fetchall()
        updates: list[tuple[str, str, int]] = []
        for template_id, revision, raw in rows:
            try:
                members = json.loads(raw)
            except TypeError, ValueError:
                print(f"skip template {template_id}@{revision}: invalid JSON")
                continue
            normalized, changed = normalize_members(members)
            if changed:
                updates.append(
                    (
                        template_id,
                        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                        revision,
                    )
                )
                print(f"template {template_id}@{revision}: normalize {changed} MCP member(s)")
        if not updates:
            print("no MCP member type changes needed")
            return _EXIT_OK
        if not apply:
            print(f"dry-run: {len(updates)} revision(s) would change; re-run with --apply")
            return _EXIT_OK
        backup = _backup(path)
        print(f"backup: {backup}")
        with db:
            for template_id, raw, revision in updates:
                db.execute(
                    "UPDATE agent_templates SET available_members = ? "
                    "WHERE template_id = ? AND revision = ?",
                    (raw, template_id, revision),
                )
        print(f"applied: {len(updates)} revision(s) updated")
        return _EXIT_OK
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        required=True,
        help="subject.db 路径（实例数据目录）",
    )
    parser.add_argument("--apply", action="store_true", help="实际写库；缺省只预演")
    args = parser.parse_args(argv)
    db_path = Path(args.db).expanduser().resolve()
    if db_path.name in {"subject.db-wal", "subject.db-shm"}:
        print(f"refused: pass the database file, not {db_path.name}", file=sys.stderr)
        return _EXIT_REFUSED
    return fix_templates(db_path, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
