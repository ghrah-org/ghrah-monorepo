#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""清空 ``subject_projects.agents`` 里的旧 Agent 元素（一次性，不可逆）。

背景：``AgentRecord`` 取代旧 ``AgentSpec`` 后，旧元素**不做兼容解析**——含
``manifest_ref`` 等旧字段的行会让整个项目记录解析失败。所以升级前必须显式清空，
并把被丢弃的 Agent 逐条留档，而不是让运行期静默丢数据或直接崩。

用法::

    python scripts/purge_legacy_agents.py --db ~/.ghrah/subject.db --backup-dir /tmp/backup
    python scripts/purge_legacy_agents.py ... --apply

默认 dry-run：只报告「会清掉哪些项目、多少条 Agent」。``--apply`` 才写库，
并且**必须先备份**：脚本自己把 db 复制进 ``--backup-dir``，复制失败即拒绝执行。

退出码：0 = 成功（含 dry-run）；1 = 执行错误；2 = 拒绝执行（未备份/库不存在）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["PurgePlan", "PurgeRefusedError", "apply_purge", "build_plan", "main"]

_QUERY = (
    "SELECT project_id, agents FROM subject_projects "
    "WHERE deleted_at IS NULL AND agents <> '[]' ORDER BY project_id"
)


@dataclass
class PurgePlan:
    """一次清理的影响面；``projects`` 里是被丢弃元素的原文。"""

    db_path: Path
    projects: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def project_count(self) -> int:
        """涉及的项目数。"""
        return len(self.projects)

    @property
    def agent_count(self) -> int:
        """将被丢弃的 Agent 元素总数。"""
        return sum(len(items) for items in self.projects.values())

    def summary(self) -> dict[str, object]:
        """人类可读摘要（写进报告与标准输出）。"""
        return {
            "db": str(self.db_path),
            "projects": self.project_count,
            "agents": self.agent_count,
            "by_project": {key: len(value) for key, value in self.projects.items()},
        }


class PurgeRefusedError(RuntimeError):
    """拒绝执行（未备份、库不存在或不含目标表）。"""


def build_plan(db_path: Path) -> PurgePlan:
    """读取将被丢弃的旧元素；不写库。"""
    if not db_path.is_file():
        raise PurgeRefusedError(f"subject db not found: {db_path}")
    plan = PurgePlan(db_path=db_path)
    with sqlite3.connect(db_path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "subject_projects" not in tables:
            raise PurgeRefusedError(f"subject_projects not found in {db_path}")
        for project_id, raw in db.execute(_QUERY):
            try:
                items = json.loads(raw) if isinstance(raw, str) else list(raw)
            except TypeError, ValueError:
                items = [{"unparsable": raw}]
            if items:
                plan.projects[str(project_id)] = list(items)
    return plan


def _backup(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{db_path.name}.{stamp}"
    shutil.copy2(db_path, target)
    if not target.is_file() or target.stat().st_size == 0:
        raise PurgeRefusedError(f"backup copy failed: {target}")
    return target


def apply_purge(
    plan: PurgePlan, *, backup_dir: Path, report_dir: Path | None = None
) -> dict[str, object]:
    """备份 → 写丢弃报告 → 清空 ``agents`` 列；返回执行摘要。"""
    if plan.project_count == 0:
        return {**plan.summary(), "backup": None, "report": None, "cleared_rows": 0}
    backup = _backup(plan.db_path, backup_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target_dir = report_dir or backup_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    report_path = target_dir / f"agents_discarded_{stamp}.json"
    report_path.write_text(
        json.dumps(
            {"discarded_at": stamp, "db": str(plan.db_path), "projects": plan.projects},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    cleared = 0
    with sqlite3.connect(plan.db_path) as db:
        for project_id in plan.projects:
            cursor = db.execute(
                "UPDATE subject_projects SET agents='[]' WHERE project_id=? AND deleted_at IS NULL",
                (project_id,),
            )
            cleared += int(cursor.rowcount or 0)
        # 派生缓存里也是旧形状，一并清掉；它可由 ProjectStore 重建。
        db.execute("DELETE FROM subject_desired_state")
        db.commit()
    return {
        **plan.summary(),
        "backup": str(backup),
        "report": str(report_path),
        "cleared_rows": cleared,
    }


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="清空旧 Agent 元素（AgentRecord 不兼容旧形状）")
    parser.add_argument("--db", required=True, help="subject.db 路径")
    parser.add_argument("--backup-dir", required=True, help="备份目录（--apply 时必须可用）")
    parser.add_argument("--report-dir", default=None, help="丢弃报告目录（默认与备份同目录）")
    parser.add_argument("--apply", action="store_true", help="真正写入；缺省 dry-run")
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser()
    try:
        plan = build_plan(db_path)
    except PurgeRefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2

    if not args.apply:
        print(json.dumps({**plan.summary(), "dry_run": True}, ensure_ascii=False, indent=2))
        return 0

    try:
        result = apply_purge(
            plan,
            backup_dir=Path(args.backup_dir).expanduser(),
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
    except PurgeRefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps({**result, "dry_run": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
