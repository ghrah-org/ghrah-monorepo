# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""旧 Agent 元素的一次性清理：不兼容旧形状，必须显式清空并留档。

覆盖 ``AgentRecord`` 拒绝旧字段、清理脚本的计划/备份/报告/幂等，以及拒绝执行
的出口（库不存在、不给备份目录时不得写库）。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from ghrah.subject.project.models import AgentOrigin, AgentRecord

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "purge_legacy_agents.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("purge_legacy_agents", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _db(tmp_path: Path, agents: list[dict]) -> Path:
    path = tmp_path / "subject.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE subject_projects (
                project_id TEXT PRIMARY KEY,
                agents     TEXT NOT NULL DEFAULT '[]',
                deleted_at TEXT
            );
            CREATE TABLE subject_desired_state (
                subject_id TEXT PRIMARY KEY,
                snapshot   TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO subject_projects(project_id, agents) VALUES(?, ?)",
            ("p1", json.dumps(agents)),
        )
        db.execute(
            "INSERT INTO subject_projects(project_id, agents, deleted_at) "
            "VALUES(?, ?, '2026-01-01T00:00:00Z')",
            ("p2", json.dumps(agents)),
        )
        db.execute(
            "INSERT INTO subject_desired_state VALUES('default', '{}', '2026-01-01T00:00:00Z')"
        )
        db.commit()
    return path


_OLD_AGENTS = [
    {"name": "weekly", "agent_id": "a" * 32, "manifest_ref": "writing.weekly"},
    {"name": "daily", "agent_id": "b" * 32, "manifest_ref": "writing.daily"},
]


def test_agent_record_rejects_legacy_fields() -> None:
    with pytest.raises(ValidationError):
        AgentRecord.model_validate({"name": "weekly", "manifest_ref": "writing.weekly"})
    with pytest.raises(ValidationError):
        AgentRecord.model_validate({"name": "weekly", "path_grants": []})


def test_agent_record_carries_no_configuration() -> None:
    record = AgentRecord(
        name="weekly",
        agent_id="a" * 32,
        origin=AgentOrigin(template_id="t" * 32, revision=2),
    )
    dumped = record.model_dump(mode="json")
    for forbidden in ("manifest_ref", "system_prompt", "abilities", "path_grants"):
        assert forbidden not in dumped
    assert dumped["origin"] == {"template_id": "t" * 32, "revision": 2}


def test_plan_counts_live_project_only(tmp_path: Path) -> None:
    module = _script()
    db = _db(tmp_path, _OLD_AGENTS)

    plan = module.build_plan(db)

    assert plan.project_count == 1  # 软删项目不参与
    assert plan.agent_count == 2
    assert list(plan.projects) == ["p1"]


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    module = _script()
    db = _db(tmp_path, _OLD_AGENTS)

    assert module.main(["--db", str(db), "--backup-dir", str(tmp_path / "bk")]) == 0

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT agents FROM subject_projects WHERE project_id='p1'").fetchone()
    assert json.loads(row[0]) == _OLD_AGENTS


def test_apply_backs_up_reports_and_clears(tmp_path: Path) -> None:
    module = _script()
    db = _db(tmp_path, _OLD_AGENTS)
    backup_dir = tmp_path / "bk"

    result = module.apply_purge(module.build_plan(db), backup_dir=backup_dir)

    assert result["cleared_rows"] == 1
    assert Path(result["backup"]).is_file()
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert report["projects"]["p1"][0]["manifest_ref"] == "writing.weekly"

    with sqlite3.connect(db) as conn:
        assert (
            conn.execute("SELECT agents FROM subject_projects WHERE project_id='p1'").fetchone()[0]
            == "[]"
        )
        # 派生缓存一并清掉，避免旧形状残留在 load() 路径上炸掉
        assert conn.execute("SELECT COUNT(*) FROM subject_desired_state").fetchone()[0] == 0


def test_apply_is_idempotent(tmp_path: Path) -> None:
    module = _script()
    db = _db(tmp_path, _OLD_AGENTS)
    module.apply_purge(module.build_plan(db), backup_dir=tmp_path / "bk")

    second = module.apply_purge(module.build_plan(db), backup_dir=tmp_path / "bk")

    assert second["cleared_rows"] == 0
    assert second["backup"] is None


def test_missing_db_is_refused(tmp_path: Path) -> None:
    module = _script()
    assert module.main(["--db", str(tmp_path / "nope.db"), "--backup-dir", str(tmp_path)]) == 2


def test_missing_table_is_refused(tmp_path: Path) -> None:
    module = _script()
    path = tmp_path / "empty.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE other(x TEXT)")
        conn.commit()
    assert module.main(["--db", str(path), "--backup-dir", str(tmp_path)]) == 2
