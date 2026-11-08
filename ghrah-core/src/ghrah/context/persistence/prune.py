# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""存量周期性快照清理：候选判定、等价性门禁与库统计。

背景
----
旧版本的 ghrah 会按 ``MessageStore.snapshot_interval`` 机制周期性写入完整消息
快照（``nodes.is_snapshot = 1`` 且 ``messages_snapshot`` 非空）。该机制删除后，
这些历史快照行只剩下冗余载荷：消息可以从最近一次根节点/压缩节点快照与后续
delta 完整重建。本模块只依赖 sqlite 表结构与节点列值（不依赖 ``MessageStore``
或 ``ContextManager`` 的快照机制），用于离线清理存量老库。

清理规则
--------
候选行（可清空载荷的行）必须同时满足::

    is_snapshot = 1
    AND parent_id IS NOT NULL
    AND (metadata.node_kind IS NULL OR metadata.node_kind <> 'compact')

即根节点（``parent_id IS NULL``）与压缩节点（``metadata.node_kind = 'compact'``）
必须保留。清理动作是单条 UPDATE：把候选行的 ``messages_snapshot`` 置 NULL、
``is_snapshot`` 置 0；节点本身、``messages_delta`` 与其它列全部保留。

等价性门禁
----------
对 ``branches`` 表每个 head 回溯到根，分别按两条路径重建消息：

* A 路径：最近一个「任意有载荷快照」+ 后续 delta，等价于当前运行时的
  ``ContextManager._messages_from_history`` 读取路径；
* B 路径：最近一个「根/压缩边界快照」+ 后续 delta，即清理后的读取路径。

逐条比较两路消息的规范化 JSON（``json.dumps(..., ensure_ascii=False,
sort_keys=True)``）；任一节点处不一致即视为门禁失败，返回不一致清单。门禁全
通过意味着：清空周期性快照载荷不会改变任何 branch head 的消息视图。

用法概览::

    from ghrah.context.persistence.prune import (
        apply_prune,
        check_equivalence,
        count_candidates,
        scan_stats,
    )

    mismatches = check_equivalence(connection)
    with connection:
        apply_prune(connection)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "CANDIDATE_PREDICATE",
    "SCHEMA_VERSION",
    "EquivalenceMismatch",
    "ScanStats",
    "apply_prune",
    "check_equivalence",
    "count_candidates",
    "scan_stats",
]

SCHEMA_VERSION = 3
"""本工具认识的 ``context_schema.version``。"""

CANDIDATE_PREDICATE = (
    "is_snapshot = 1"
    " AND parent_id IS NOT NULL"
    " AND (json_extract(metadata, '$.node_kind') IS NULL"
    " OR json_extract(metadata, '$.node_kind') <> 'compact')"
)
"""候选行判定条件；根节点与压缩节点必须保留。"""

# 回溯 branch head 到根的递归查询（depth 上限用于防御环形 parent 链）
_CHAIN_SQL = """
WITH RECURSIVE chain (
    id, parent_id, is_snapshot, messages_delta, messages_snapshot, metadata, depth
) AS (
    SELECT id, parent_id, is_snapshot, messages_delta, messages_snapshot, metadata, 0
    FROM nodes
    WHERE id = ?
    UNION ALL
    SELECT n.id, n.parent_id, n.is_snapshot, n.messages_delta,
           n.messages_snapshot, n.metadata, chain.depth + 1
    FROM nodes AS n
    JOIN chain ON n.id = chain.parent_id
    WHERE chain.depth < 100000
)
SELECT id, parent_id, is_snapshot, messages_delta, messages_snapshot, metadata
FROM chain
ORDER BY depth DESC
"""


@dataclass(frozen=True)
class EquivalenceMismatch:
    """一处等价性门禁不一致。

    Attributes:
        branch_id: 分支 ID。
        head_node_id: 分支 head 节点 ID。
        node_id: 首次出现不一致的节点 ID。
        detail: 人类可读的差异说明（首条差异下标或消息条数差）。
    """

    branch_id: str
    head_node_id: str
    node_id: str
    detail: str


@dataclass(frozen=True)
class ScanStats:
    """库文件与节点构成的统计快照。

    Attributes:
        db_path: sqlite 主库文件路径（内存库或未知时为空串）。
        db_bytes: 库文件字节数（内存库或未知时为 0）。
        page_size: 页面大小（字节）。
        page_count: 页面数。
        freelist_count: 空闲页数。
        nodes_total: nodes 表总行数。
        snapshot_nodes: ``is_snapshot = 1`` 的行数。
        root_nodes: ``parent_id IS NULL`` 的根节点行数。
        compact_nodes: ``metadata.node_kind = 'compact'`` 的压缩节点行数。
        candidate_rows: 可清理的周期性快照行数。
        candidate_payload_bytes: 这些行的 ``messages_snapshot`` 载荷字节数。
        branches: branches 表总行数。
    """

    db_path: str
    db_bytes: int
    page_size: int
    page_count: int
    freelist_count: int
    nodes_total: int
    snapshot_nodes: int
    root_nodes: int
    compact_nodes: int
    candidate_rows: int
    candidate_payload_bytes: int
    branches: int


@dataclass(frozen=True)
class _ChainNode:
    """祖先链上一个节点的门禁视图（只含重建所需信息）。"""

    node_id: str
    is_root: bool
    is_compact: bool
    has_payload: bool
    snapshot: list[Any]
    delta: list[Any]

    @property
    def is_boundary(self) -> bool:
        """是否为清理后保留载荷的根/压缩边界快照。"""
        return self.is_root or self.is_compact


class _MessagesReconstruction:
    """增量重建的消息视图，缓存规范化 JSON 键用于逐条比较。"""

    def __init__(self) -> None:
        self._keys: list[str] = []

    @property
    def keys(self) -> list[str]:
        """当前消息视图的规范化 JSON 序列。"""
        return self._keys

    def reset(self, messages: list[Any]) -> None:
        """以完整快照重置视图（快照节点的自带 delta 不重复追加）。"""
        self._keys = [_message_key(message) for message in messages]

    def extend(self, messages: list[Any]) -> None:
        """追加一轮 delta。"""
        self._keys.extend(_message_key(message) for message in messages)


def _message_key(message: Any) -> str:
    """单条消息的规范化 JSON 键（键序无关、中文不转义）。"""
    return json.dumps(message, ensure_ascii=False, sort_keys=True)


def _load_json_list(raw: str | None) -> list[Any]:
    """解析 JSON 数组列；NULL 或非数组视为空列表。"""
    if raw is None:
        return []
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, list) else []


def _load_json_dict(raw: str | None) -> dict[str, Any]:
    """解析 JSON 对象列；NULL 或非对象视为空字典。"""
    if not raw:
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _load_chain(conn: sqlite3.Connection, head_node_id: str) -> list[_ChainNode]:
    """读取 branch head 的祖先链（根在前，head 在后）。"""
    rows = conn.execute(_CHAIN_SQL, (head_node_id,)).fetchall()
    chain: list[_ChainNode] = []
    for row in rows:
        node_id, parent_id, is_snapshot, messages_delta, messages_snapshot, metadata = row
        chain.append(
            _ChainNode(
                node_id=str(node_id),
                is_root=parent_id is None,
                is_compact=_load_json_dict(metadata).get("node_kind") == "compact",
                has_payload=bool(is_snapshot) and messages_snapshot is not None,
                snapshot=_load_json_list(messages_snapshot),
                delta=_load_json_list(messages_delta),
            )
        )
    return chain


def _first_difference(left: list[str], right: list[str], start: int) -> int | None:
    """返回两路消息自 ``start`` 起首个不同的下标；完全一致返回 None。"""
    common = min(len(left), len(right))
    for index in range(start, common):
        if left[index] != right[index]:
            return index
    if len(left) != len(right):
        return common
    return None


def _describe_difference(left: list[str], right: list[str], index: int) -> str:
    """生成门禁差异说明。"""
    common = min(len(left), len(right))
    if index >= common:
        return (
            f"消息条数不一致：任意快照重建 {len(left)} 条，"
            f"边界快照重建 {len(right)} 条（自第 {index} 条起不同）"
        )
    return f"第 {index} 条消息内容不一致"


def check_equivalence(conn: sqlite3.Connection) -> list[EquivalenceMismatch]:
    """校验全部 branch head 在清理前后两条重建路径下消息逐条一致。

    对每个分支分别用「最近任意有载荷快照 + 后续 delta」（A 路径）与「最近
    根/压缩边界快照 + 后续 delta」（B 路径）重建消息，并逐条比较规范化 JSON。

    Args:
        conn: 已打开的 sqlite3 连接（同步）。

    Returns:
        不一致清单；空列表表示门禁通过。每项给出 branch、head 与首个不一致节点。
    """
    mismatches: list[EquivalenceMismatch] = []
    branches = conn.execute(
        "SELECT branch_id, head_node_id FROM branches ORDER BY branch_id"
    ).fetchall()
    for branch_id, head_node_id in branches:
        branch_key = str(branch_id)
        head_key = str(head_node_id)
        chain = _load_chain(conn, head_key)
        if not chain:
            mismatches.append(
                EquivalenceMismatch(
                    branch_id=branch_key,
                    head_node_id=head_key,
                    node_id=head_key,
                    detail="head 节点在 nodes 表中不存在",
                )
            )
            continue

        any_snapshot = _MessagesReconstruction()
        boundary_snapshot = _MessagesReconstruction()
        verified = 0
        mismatch: EquivalenceMismatch | None = None
        for node in chain:
            reset = False
            if node.has_payload:
                any_snapshot.reset(node.snapshot)
                reset = True
            else:
                any_snapshot.extend(node.delta)
            if node.has_payload and node.is_boundary:
                boundary_snapshot.reset(node.snapshot)
                reset = True
            else:
                boundary_snapshot.extend(node.delta)
            if reset:
                verified = 0

            difference = _first_difference(any_snapshot.keys, boundary_snapshot.keys, verified)
            if difference is not None:
                mismatch = EquivalenceMismatch(
                    branch_id=branch_key,
                    head_node_id=head_key,
                    node_id=node.node_id,
                    detail=_describe_difference(
                        any_snapshot.keys, boundary_snapshot.keys, difference
                    ),
                )
                break
            verified = len(any_snapshot.keys)
        if mismatch is not None:
            mismatches.append(mismatch)
    return mismatches


def _scalar_int(conn: sqlite3.Connection, sql: str) -> int:
    """执行只返回单个整数标量的查询。"""
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row is not None else 0


def count_candidates(conn: sqlite3.Connection) -> tuple[int, int]:
    """统计可清理行数与其 ``messages_snapshot`` 载荷字节数。

    Args:
        conn: 已打开的 sqlite3 连接（同步）。

    Returns:
        ``(候选行数, 载荷字节数)``；载荷按 UTF-8 字节计（``CAST(... AS BLOB)``）。
    """
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(messages_snapshot AS BLOB))), 0) "
        f"FROM nodes WHERE {CANDIDATE_PREDICATE}"
    ).fetchone()
    if row is None:
        return 0, 0
    return int(row[0]), int(row[1])


def scan_stats(conn: sqlite3.Connection) -> ScanStats:
    """采集库大小与快照节点构成等统计信息。

    Args:
        conn: 已打开的 sqlite3 连接（同步）。

    Returns:
        统计快照 :class:`ScanStats`。
    """
    db_path = ""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row[1] == "main":
            db_path = str(row[2] or "")
            break
    db_bytes = 0
    if db_path:
        path = Path(db_path)
        if path.is_file():
            db_bytes = path.stat().st_size
    page_size = _scalar_int(conn, "PRAGMA page_size")
    page_count = _scalar_int(conn, "PRAGMA page_count")
    freelist_count = _scalar_int(conn, "PRAGMA freelist_count")
    candidate_rows, candidate_payload_bytes = count_candidates(conn)
    return ScanStats(
        db_path=db_path,
        db_bytes=db_bytes,
        page_size=page_size,
        page_count=page_count,
        freelist_count=freelist_count,
        nodes_total=_scalar_int(conn, "SELECT COUNT(*) FROM nodes"),
        snapshot_nodes=_scalar_int(conn, "SELECT COUNT(*) FROM nodes WHERE is_snapshot = 1"),
        root_nodes=_scalar_int(conn, "SELECT COUNT(*) FROM nodes WHERE parent_id IS NULL"),
        compact_nodes=_scalar_int(
            conn,
            "SELECT COUNT(*) FROM nodes WHERE json_extract(metadata, '$.node_kind') = 'compact'",
        ),
        candidate_rows=candidate_rows,
        candidate_payload_bytes=candidate_payload_bytes,
        branches=_scalar_int(conn, "SELECT COUNT(*) FROM branches"),
    )


def apply_prune(conn: sqlite3.Connection) -> int:
    """在调用方事务内清空候选行载荷，返回更新行数。

    执行的是一条 UPDATE：候选行的 ``messages_snapshot`` 置 NULL、
    ``is_snapshot`` 置 0，其余列与节点本身保持不变。本函数不提交事务，
    由调用方负责 ``commit`` / ``rollback``。

    Args:
        conn: 已打开的 sqlite3 连接，且处于调用方事务中。

    Returns:
        被更新的行数。
    """
    cursor = conn.execute(
        f"UPDATE nodes SET messages_snapshot = NULL, is_snapshot = 0 WHERE {CANDIDATE_PREDICATE}"
    )
    return int(cursor.rowcount)
