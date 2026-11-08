# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""存量周期性快照清理（prune）测试。

用临时 sqlite 库手工构造旧版 v3 结构：根节点（root）→ delta → 周期性快照
（is_snapshot=1、parent_id 非空）→ delta → 压缩节点（node_kind=compact）→
delta → 周期性快照 → head，并用 ``serialize_node`` 生成各列值。覆盖：

* 候选行与载荷统计；
* dry-run 只读（不修改库）；
* 门禁通过时 apply 后的等价性（周期行载荷 NULL、is_snapshot=0，root/compact
  不变，全部 branch head 重建消息逐条一致）；
* 周期快照被篡改时门禁失败且不写库（退出码 2）；
* ``context_schema.version != 3`` 时拒绝执行（退出码 1）。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from ghrah.chat.message import ChatMessage
from ghrah.context import ContextNode
from ghrah.context.persistence.prune import (
    apply_prune,
    check_equivalence,
    count_candidates,
    scan_stats,
)
from ghrah.context.persistence.serialization import serialize_node

_SCHEMA_SQL = """
CREATE TABLE context_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version   INTEGER NOT NULL
);

CREATE TABLE branches (
    branch_id          TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    name               TEXT NOT NULL,
    head_node_id       TEXT NOT NULL,
    lifecycle          TEXT NOT NULL DEFAULT 'open',
    parent_branch_id   TEXT,
    fork_point_node_id TEXT,
    created_at         TEXT NOT NULL,
    metadata           TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE nodes (
    id                   TEXT PRIMARY KEY,
    parent_id            TEXT,
    agent_name           TEXT NOT NULL,
    session_id           TEXT NOT NULL DEFAULT '',
    created_on_branch_id TEXT NOT NULL,
    timestamp            TEXT NOT NULL,
    iteration            INTEGER NOT NULL DEFAULT 0,
    is_snapshot          INTEGER NOT NULL DEFAULT 0,
    ability_names        TEXT NOT NULL DEFAULT '[]',
    agent_state          TEXT NOT NULL DEFAULT '{}',
    messages_delta       TEXT NOT NULL DEFAULT '[]',
    messages_snapshot    TEXT,
    action_results       TEXT NOT NULL DEFAULT '[]',
    metadata             TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_NODE_INSERT = """
INSERT INTO nodes (
    id, parent_id, agent_name, session_id, created_on_branch_id, timestamp,
    iteration, is_snapshot, ability_names, agent_state, messages_delta,
    messages_snapshot, action_results, metadata
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_BRANCH_INSERT = """
INSERT INTO branches (branch_id, session_id, name, head_node_id, created_at)
VALUES (?, ?, ?, ?, ?)
"""

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "prune_periodic_snapshots.py"

# 三个 branch head：(branch_id, head_node_id)
_BRANCH_HEADS = (
    ("branch-1", "head"),
    ("branch-2", "n3"),
    ("branch-3", "p2"),
)


def _load_cli_module() -> ModuleType:
    """按文件路径加载 CLI 脚本，避免依赖 scripts 目录的包结构。"""
    spec = importlib.util.spec_from_file_location("prune_periodic_snapshots", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_database(path: Path, *, version: int = 3) -> sqlite3.Connection:
    """建立最小 v3 结构（nodes/branches/context_schema）。"""
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA_SQL)
    connection.execute("INSERT INTO context_schema (singleton, version) VALUES (1, ?)", (version,))
    connection.commit()
    return connection


def _make_node(
    node_id: str,
    *,
    parent_id: str | None,
    iteration: int,
    delta: list[ChatMessage],
    snapshot: list[ChatMessage] | None,
    is_snapshot: bool,
    metadata: dict[str, Any] | None = None,
) -> ContextNode:
    """构造一个上下文节点（用真实序列化函数落库）。"""
    return ContextNode(
        id=node_id,
        parent_id=parent_id,
        agent_name="agent",
        session_id="session-1",
        created_on_branch_id="branch-1",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        iteration=iteration,
        messages_delta=delta,
        messages_snapshot=snapshot,
        is_snapshot=is_snapshot,
        metadata=metadata or {},
    )


def _insert_node(connection: sqlite3.Connection, node: ContextNode) -> None:
    """把节点按 v3 列布局写入 nodes 表。"""
    data = serialize_node(node)
    snapshot = data["messages_snapshot"]
    connection.execute(
        _NODE_INSERT,
        (
            data["id"],
            data["parent_id"],
            data["agent_name"],
            data["session_id"],
            data["created_on_branch_id"],
            data["timestamp"],
            data["iteration"],
            1 if data["is_snapshot"] else 0,
            json.dumps(data["ability_names"], ensure_ascii=False),
            json.dumps(data["agent_state"], ensure_ascii=False),
            json.dumps(data["messages_delta"], ensure_ascii=False),
            None if snapshot is None else json.dumps(snapshot, ensure_ascii=False),
            json.dumps(data["action_results"], ensure_ascii=False),
            json.dumps(data["metadata"], ensure_ascii=False),
        ),
    )


def _build_legacy_database(path: Path, *, tamper_periodic: bool = False) -> sqlite3.Connection:
    """构造历史老库并返回连接。

    链形态::

        root(snapshot) → n1(delta) → p1(周期快照) → n3(delta) → comp(compact)
          → n5(delta) → p2(周期快照) → head(delta)

    ``tamper_periodic=True`` 时给 p2 的快照载荷追加一条多余消息，用于制造
    门禁不一致。
    """
    connection = _create_database(path)
    m0 = ChatMessage.system(text="系统提示：你好")
    m1 = ChatMessage.user(text_or_blocks="问题一")
    m2 = ChatMessage.user(text_or_blocks="问题二")
    m3 = ChatMessage.user(text_or_blocks="问题三")
    m4 = ChatMessage.user(text_or_blocks="压缩后问题四")
    m5 = ChatMessage.user(text_or_blocks="问题五")
    m6 = ChatMessage.user(text_or_blocks="问题六")

    # 压缩视图：压缩节点自身的完整消息（压缩节点不写 delta）
    compact_view = [
        ChatMessage.system(text="压缩摘要"),
        ChatMessage.user(text_or_blocks="问题三"),
    ]
    p2_snapshot = [*compact_view, m4, m5]
    if tamper_periodic:
        p2_snapshot.append(ChatMessage.user(text_or_blocks="被篡改的多余消息"))

    nodes = [
        _make_node(
            "root",
            parent_id=None,
            iteration=0,
            delta=[],
            snapshot=[m0],
            is_snapshot=True,
        ),
        _make_node(
            "n1", parent_id="root", iteration=1, delta=[m1], snapshot=None, is_snapshot=False
        ),
        _make_node(
            "p1",
            parent_id="n1",
            iteration=2,
            delta=[m2],
            snapshot=[m0, m1, m2],
            is_snapshot=True,
        ),
        _make_node("n3", parent_id="p1", iteration=3, delta=[m3], snapshot=None, is_snapshot=False),
        _make_node(
            "comp",
            parent_id="n3",
            iteration=4,
            delta=[],
            snapshot=compact_view,
            is_snapshot=True,
            metadata={"node_kind": "compact"},
        ),
        _make_node(
            "n5", parent_id="comp", iteration=5, delta=[m4], snapshot=None, is_snapshot=False
        ),
        _make_node(
            "p2",
            parent_id="n5",
            iteration=6,
            delta=[m5],
            snapshot=p2_snapshot,
            is_snapshot=True,
        ),
        _make_node(
            "head", parent_id="p2", iteration=7, delta=[m6], snapshot=None, is_snapshot=False
        ),
    ]
    for node in nodes:
        _insert_node(connection, node)
    for branch_id, head_node_id in _BRANCH_HEADS:
        connection.execute(
            _BRANCH_INSERT,
            (branch_id, "session-1", branch_id, head_node_id, "2026-01-01T00:00:00+00:00"),
        )
    connection.commit()
    return connection


def _rebuild_messages(connection: sqlite3.Connection, head_node_id: str) -> list[Any]:
    """按运行时语义独立重建消息：最近有载荷快照 + 后续 delta。"""
    chain: list[tuple[Any, ...]] = []
    current: str | None = head_node_id
    while current is not None:
        row = connection.execute(
            "SELECT id, parent_id, is_snapshot, messages_delta, messages_snapshot "
            "FROM nodes WHERE id = ?",
            (current,),
        ).fetchone()
        assert row is not None
        chain.append(row)
        current = row[1]
    chain.reverse()
    snapshot_index: int | None = None
    for index, row in enumerate(chain):
        if bool(row[2]) and row[4] is not None:
            snapshot_index = index
    if snapshot_index is None:
        return [message for row in chain for message in json.loads(row[3])]
    messages: list[Any] = json.loads(chain[snapshot_index][4])
    for row in chain[snapshot_index + 1 :]:
        messages.extend(json.loads(row[3]))
    return messages


def _normalized(messages: list[Any]) -> list[str]:
    """消息列表的规范化 JSON 序列，用于逐条比较。"""
    return [json.dumps(message, ensure_ascii=False, sort_keys=True) for message in messages]


def _snapshot_bytes(connection: sqlite3.Connection, node_id: str) -> int:
    """某节点 messages_snapshot 载荷的 UTF-8 字节数。"""
    row = connection.execute(
        "SELECT messages_snapshot FROM nodes WHERE id = ?", (node_id,)
    ).fetchone()
    assert row is not None
    return len(str(row[0]).encode("utf-8"))


def test_count_candidates_and_scan_stats(tmp_path: Path) -> None:
    """候选行数与载荷字节数、库统计正确。"""
    db_path = tmp_path / "legacy.db"
    connection = _build_legacy_database(db_path)
    try:
        rows, payload_bytes = count_candidates(connection)
        assert rows == 2
        assert payload_bytes == _snapshot_bytes(connection, "p1") + _snapshot_bytes(
            connection, "p2"
        )

        stats = scan_stats(connection)
        assert stats.nodes_total == 8
        assert stats.snapshot_nodes == 4
        assert stats.root_nodes == 1
        assert stats.compact_nodes == 1
        assert stats.candidate_rows == 2
        assert stats.candidate_payload_bytes == payload_bytes
        assert stats.branches == 3
        assert stats.page_size > 0
        assert stats.page_count > 0
        assert stats.db_bytes == db_path.stat().st_size
    finally:
        connection.close()


def test_equivalence_gate_passes_on_consistent_legacy_library(tmp_path: Path) -> None:
    """结构一致的老库应通过等价性门禁。"""
    connection = _build_legacy_database(tmp_path / "legacy.db")
    try:
        assert check_equivalence(connection) == []
    finally:
        connection.close()


def test_apply_prune_keeps_reconstruction_and_boundaries(tmp_path: Path) -> None:
    """apply 后周期行载荷为 NULL、is_snapshot=0，root/compact 不变且重建消息一致。"""
    connection = _build_legacy_database(tmp_path / "legacy.db")
    try:
        before = {
            branch_id: _normalized(_rebuild_messages(connection, head_node_id))
            for branch_id, head_node_id in _BRANCH_HEADS
        }
        protected = {
            node_id: connection.execute(
                "SELECT is_snapshot, messages_snapshot FROM nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            for node_id in ("root", "comp")
        }

        connection.execute("BEGIN EXCLUSIVE")
        updated = apply_prune(connection)
        connection.commit()

        assert updated == 2
        assert count_candidates(connection) == (0, 0)
        for node_id in ("p1", "p2"):
            is_snapshot, snapshot = connection.execute(
                "SELECT is_snapshot, messages_snapshot FROM nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            assert is_snapshot == 0
            assert snapshot is None
        for node_id, expected in protected.items():
            actual = connection.execute(
                "SELECT is_snapshot, messages_snapshot FROM nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            assert actual == expected

        assert check_equivalence(connection) == []
        for branch_id, head_node_id in _BRANCH_HEADS:
            assert _normalized(_rebuild_messages(connection, head_node_id)) == before[branch_id]
    finally:
        connection.close()


def test_gate_detects_tampered_periodic_snapshot(tmp_path: Path) -> None:
    """周期快照载荷被篡改时，门禁报告不一致节点。"""
    connection = _build_legacy_database(tmp_path / "tampered.db", tamper_periodic=True)
    try:
        mismatches = check_equivalence(connection)
        assert [mismatch.branch_id for mismatch in mismatches] == [
            "branch-1",
            "branch-3",
        ]
        for mismatch in mismatches:
            assert mismatch.head_node_id in {"head", "p2"}
            assert mismatch.node_id == "p2"
            assert "不一致" in mismatch.detail
    finally:
        connection.close()


def test_cli_dry_run_is_read_only(tmp_path: Path, capsys: Any) -> None:
    """默认 dry-run：退出码 0，输出 dry-run 提示且不修改库。"""
    db_path = tmp_path / "legacy.db"
    connection = _build_legacy_database(db_path)
    before_rows = connection.execute(
        "SELECT id, is_snapshot, messages_snapshot FROM nodes ORDER BY id"
    ).fetchall()
    connection.close()

    cli = _load_cli_module()
    assert cli.main(["--db", str(db_path)]) == 0

    output = capsys.readouterr().out
    assert "等价性门禁通过" in output
    assert "dry-run：库未修改" in output

    connection = sqlite3.connect(db_path)
    try:
        after_rows = connection.execute(
            "SELECT id, is_snapshot, messages_snapshot FROM nodes ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    assert after_rows == before_rows


def test_cli_apply_prunes_and_rechecks(tmp_path: Path, capsys: Any) -> None:
    """--apply 清理候选行并输出前后统计与复检结果。"""
    db_path = tmp_path / "legacy.db"
    connection = _build_legacy_database(db_path)
    connection.close()

    cli = _load_cli_module()
    assert cli.main(["--db", str(db_path), "--apply"]) == 0

    output = capsys.readouterr().out
    assert "已清理候选行：2 行" in output
    assert "清理后等价性复检通过" in output

    connection = sqlite3.connect(db_path)
    try:
        assert count_candidates(connection) == (0, 0)
        assert check_equivalence(connection) == []
    finally:
        connection.close()


def test_cli_gate_failure_exits_2_without_writing(tmp_path: Path, capsys: Any) -> None:
    """门禁失败时退出码 2，且库保持原样（周期行仍保留载荷）。"""
    db_path = tmp_path / "tampered.db"
    connection = _build_legacy_database(db_path, tamper_periodic=True)
    before_p2 = connection.execute(
        "SELECT is_snapshot, messages_snapshot FROM nodes WHERE id = 'p2'"
    ).fetchone()
    connection.close()

    cli = _load_cli_module()
    assert cli.main(["--db", str(db_path), "--apply"]) == 2

    captured = capsys.readouterr()
    assert "等价性门禁未通过" in captured.err
    assert "未写库" in captured.err

    connection = sqlite3.connect(db_path)
    try:
        after_p2 = connection.execute(
            "SELECT is_snapshot, messages_snapshot FROM nodes WHERE id = 'p2'"
        ).fetchone()
        assert after_p2 == before_p2
        assert count_candidates(connection)[0] == 2
    finally:
        connection.close()


def test_cli_rejects_non_v3_schema(tmp_path: Path, capsys: Any) -> None:
    """context_schema.version != 3 时拒绝执行并以退出码 1 结束。"""
    db_path = tmp_path / "v2.db"
    connection = _create_database(db_path, version=2)
    connection.close()

    cli = _load_cli_module()
    assert cli.main(["--db", str(db_path)]) == 1

    captured = capsys.readouterr()
    assert "context_schema.version = 2" in captured.err


def test_cli_apply_with_backup_and_vacuum(tmp_path: Path, capsys: Any) -> None:
    """--backup 先备份原库，--vacuum 后仍保持清理后等价性。"""
    db_path = tmp_path / "legacy.db"
    backup_path = tmp_path / "legacy-backup.db"
    connection = _build_legacy_database(db_path)
    connection.close()

    cli = _load_cli_module()
    args = [
        "--db",
        str(db_path),
        "--apply",
        "--backup",
        str(backup_path),
        "--vacuum",
    ]
    assert cli.main(args) == 0

    output = capsys.readouterr().out
    assert "已备份到" in output
    assert "已执行 VACUUM" in output
    assert backup_path.is_file()

    backup = sqlite3.connect(backup_path)
    try:
        rows, payload_bytes = count_candidates(backup)
        assert rows == 2
        assert payload_bytes > 0
        assert check_equivalence(backup) == []
    finally:
        backup.close()

    connection = sqlite3.connect(db_path)
    try:
        assert count_candidates(connection) == (0, 0)
    finally:
        connection.close()


def test_cli_missing_database(tmp_path: Path, capsys: Any) -> None:
    """数据库文件不存在时以退出码 1 结束并给出明确错误。"""
    cli = _load_cli_module()
    assert cli.main(["--db", str(tmp_path / "missing.db")]) == 1
    assert "数据库文件不存在" in capsys.readouterr().err
