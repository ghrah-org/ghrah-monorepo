# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""轮次的持久写入。调用方持有所在数据库的事务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import aiosqlite

from ghrah.context import hitl
from ghrah.context.branch import ActionBranch
from ghrah.context.node import ContextNode
from ghrah.context.turn_store import TurnRecord

TurnCloseStatus = Literal["closed", "failed", "interrupted"]
TurnTerminalStatus = Literal[TurnCloseStatus, "rolled_back", "aborted"]
TERMINAL_STATUSES: tuple[TurnTerminalStatus, ...] = (
    "closed",
    "failed",
    "interrupted",
    "rolled_back",
    "aborted",
)
_CLOSE_STATUSES = ("closed", "failed", "interrupted")
# 中止轮有有效锚点时也支持主动回滚。资格由回滚契约单独定义。
_ROLLBACK_STATUSES = ("closed", "failed", "interrupted", "aborted")
_ROLLBACK_PLACEHOLDERS = ",".join("?" for _ in _ROLLBACK_STATUSES)
RollbackTarget = Literal["base", "node"]
INCOMPLETE_KIND = "incomplete"
NOT_INCOMPLETE_SQL = f"COALESCE(json_extract(metadata, '$.node_kind'), '') <> '{INCOMPLETE_KIND}'"
_ACTIVE = "status IN ('open', 'waiting_hitl')"


class TurnInputs(Protocol):
    async def __call__(
        self, db: aiosqlite.Connection, *, turn_id: str, opener_delivery_id: str | None
    ) -> list[str]: ...


@dataclass(frozen=True)
class ActiveTurn:
    """执行期间的轮次引用，锚点由数据库保存。"""

    turn_id: str
    agent_name: str
    session_id: str
    opener_delivery_id: str | None
    owner: Literal["mm", "self"]


@dataclass(frozen=True)
class TurnClosure:
    """终态事件需要的轮次与锚点事实。"""

    turn_id: str
    session_id: str
    status: str
    closing_kind: str
    end_node_id: str | None

    @classmethod
    def from_record(cls, record: TurnRecord) -> TurnClosure:
        """从已终结的持久轮次构造关闭通知。"""
        return cls(
            record.turn_id,
            record.session_id,
            record.status,
            record.closing_kind or "",
            record.end_node_id,
        )


def turn_record(row: aiosqlite.Row) -> TurnRecord:
    """把 ``turns`` 表的行转换为类型化记录。"""
    return TurnRecord(
        turn_id=row["turn_id"],
        agent_name=row["agent_name"],
        session_id=row["session_id"],
        opener_delivery_id=row["opener_delivery_id"],
        status=row["status"],
        closing_kind=row["closing_kind"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        start_node_id=row["start_node_id"],
        base_node_id=row["base_node_id"],
        end_node_id=row["end_node_id"],
        branch_id=row["branch_id"],
        end_branch_id=row["end_branch_id"],
        metadata=json.loads(row["metadata"] or "{}"),
    )


def ordinary_closure(record: TurnRecord | None) -> TurnClosure | None:
    """提取普通关闭事实。显式中止由其提交方通知。"""
    if record is None or record.status not in _CLOSE_STATUSES:
        return None
    return TurnClosure.from_record(record)


async def insert_turn_placeholder(
    db: aiosqlite.Connection, turn: ActiveTurn, *, opened_at: str
) -> None:
    """在执行前登记轮次，提交首个有效节点时再补锚点。"""
    await db.execute(
        "INSERT INTO turns (turn_id, agent_name, session_id, "
        "opener_delivery_id, status, opened_at) "
        "VALUES (?, ?, ?, ?, 'open', ?)",
        (turn.turn_id, turn.agent_name, turn.session_id, turn.opener_delivery_id, opened_at),
    )


async def backfill_start(db: aiosqlite.Connection, node: ContextNode) -> None:
    """从首个有效成员节点回填轮次起点及首个轮外祖先。"""
    if node.metadata.get("node_kind") == INCOMPLETE_KIND:
        return
    await db.execute(
        """
        WITH RECURSIVE ancestors(id, parent_id, turn_id, node_kind) AS (
            SELECT id, parent_id, turn_id, json_extract(metadata, '$.node_kind')
            FROM nodes WHERE id=?
            UNION ALL
            SELECT n.id, n.parent_id, n.turn_id, json_extract(n.metadata, '$.node_kind')
            FROM nodes n JOIN ancestors a ON n.id=a.parent_id
            WHERE a.turn_id=? OR a.node_kind=?
        )
        UPDATE turns SET start_node_id=?, branch_id=?,
            base_node_id=(SELECT id FROM ancestors
                          WHERE turn_id IS NOT ? AND COALESCE(node_kind, '')<>? LIMIT 1)
        WHERE turn_id=? AND start_node_id IS NULL AND status IN ('open', 'waiting_hitl')
        """,
        (
            node.parent_id,
            node.turn_id,
            INCOMPLETE_KIND,
            node.id,
            node.created_on_branch_id,
            node.turn_id,
            INCOMPLETE_KIND,
            node.turn_id,
        ),
    )


async def set_waiting_hitl(db: aiosqlite.Connection, turn_id: str, now: str) -> bool:
    """开始一次审批等待阶段，返回本次是否发生状态转换。"""
    cursor = await db.execute(
        "UPDATE turns SET status='waiting_hitl', waiting_since=? WHERE turn_id=? AND status='open'",
        (now, turn_id),
    )
    return cursor.rowcount == 1


async def set_open(db: aiosqlite.Connection, turn_id: str) -> None:
    """最后一条待审批项解决之后恢复轮次。"""
    await db.execute(
        "UPDATE turns SET status='open', waiting_since=NULL "
        "WHERE turn_id=? AND status='waiting_hitl' AND NOT EXISTS "
        "(SELECT 1 FROM hitl_requests WHERE turn_id=? AND status='pending')",
        (turn_id, turn_id),
    )


async def close_turn(
    db: aiosqlite.Connection,
    *,
    turn_id: str,
    status: TurnCloseStatus,
    closing_kind: str,
    end_node_id: str | None,
    closed_at: str,
) -> aiosqlite.Row | None:
    """幂等终结轮次，同时清理仍待审批的请求。"""
    if status not in _CLOSE_STATUSES:
        raise ValueError("Invalid turn closing status")
    return await _finalize_turn(
        db,
        turn_id=turn_id,
        status=status,
        closing_kind=closing_kind,
        end_node_id=end_node_id,
        closed_at=closed_at,
    )


class TurnRevokedError(RuntimeError):
    """当前执行对应的轮次已失去提交资格。"""


class CompactClaimedError(RuntimeError):
    """空闲压缩期间已领取新轮次，压缩改由该轮执行。"""


async def has_active_turn(db: aiosqlite.Connection, agent_name: str) -> bool:
    """检查该 Agent 是否已有持久活动轮次。"""
    async with db.execute(
        f"SELECT 1 FROM turns WHERE agent_name=? AND {_ACTIVE} LIMIT 1", (agent_name,)
    ) as cursor:
        return await cursor.fetchone() is not None


async def require_active(db: aiosqlite.Connection, turn_id: str) -> None:
    """在写事务中验证轮次仍持有执行权。"""
    async with db.execute(
        f"SELECT 1 FROM turns WHERE turn_id=? AND {_ACTIVE}", (turn_id,)
    ) as cursor:
        if await cursor.fetchone() is None:
            raise TurnRevokedError("turn is no longer active")


async def abort_turn(
    db: aiosqlite.Connection,
    *,
    turn_id: str,
    end_node_id: str | None,
    closed_at: str,
    metadata_patch: dict[str, Any],
) -> aiosqlite.Row | None:
    """终结活动轮次并取消待审批请求。"""
    return await _finalize_turn(
        db,
        turn_id=turn_id,
        status="aborted",
        closing_kind="external_abort",
        end_node_id=end_node_id,
        closed_at=closed_at,
        metadata_patch=metadata_patch,
    )


async def _finalize_turn(
    db: aiosqlite.Connection,
    *,
    turn_id: str,
    status: TurnTerminalStatus,
    closing_kind: str,
    end_node_id: str | None,
    closed_at: str,
    metadata_patch: dict[str, Any] | None = None,
) -> aiosqlite.Row | None:
    async with db.execute(
        f"""
        WITH RECURSIVE head_path(id, parent_id, created_on_branch_id, metadata, depth) AS (
            SELECT n.id, n.parent_id, n.created_on_branch_id, n.metadata, 0
            FROM turns t JOIN sessions s USING (session_id, agent_name)
            JOIN branches b ON b.branch_id=s.active_branch_id
            JOIN nodes n ON n.id=b.head_node_id WHERE t.turn_id=?
            UNION ALL
            SELECT n.id, n.parent_id, n.created_on_branch_id, n.metadata, h.depth+1
            FROM nodes n JOIN head_path h ON n.id=h.parent_id
            WHERE NOT ({NOT_INCOMPLETE_SQL.replace("metadata", "h.metadata")})
        )
        SELECT t.start_node_id, s.active_branch_id, h.id AS base,
               json_extract(h.metadata, '$.node_kind') AS base_kind
        FROM turns t LEFT JOIN sessions s USING (session_id, agent_name)
        LEFT JOIN head_path h ON {NOT_INCOMPLETE_SQL.replace("metadata", "h.metadata")}
        WHERE t.turn_id=? AND t.{_ACTIVE} ORDER BY h.depth LIMIT 1
        """,
        (turn_id, turn_id),
    ) as cursor:
        anchors = await cursor.fetchone()
    if anchors is None:
        return None
    branch = anchors["active_branch_id"]
    base = anchors["base"]
    end = None if anchors["base_kind"] == "compact" else base
    end_branch = branch if end is not None else None
    if anchors["start_node_id"] is not None:
        async with db.execute(
            f"SELECT id, created_on_branch_id FROM nodes WHERE turn_id=? "
            f"AND {NOT_INCOMPLETE_SQL} ORDER BY (id IS ?) DESC, rowid DESC LIMIT 1",
            (turn_id, end_node_id),
        ) as cursor:
            node = await cursor.fetchone()
        if node is not None:
            end, end_branch = node
    cursor = await db.execute(
        f"""
        UPDATE turns SET status=?, closing_kind=?, closed_at=?, waiting_since=NULL,
            end_node_id=?, end_branch_id=?, metadata=COALESCE(json_patch(metadata, ?), metadata),
            base_node_id=CASE WHEN start_node_id IS NULL THEN ? ELSE base_node_id END,
            branch_id=CASE WHEN start_node_id IS NULL THEN ? ELSE branch_id END
        WHERE turn_id=? AND {_ACTIVE}
        RETURNING *
        """,
        (
            status,
            closing_kind,
            closed_at,
            end,
            end_branch,
            json.dumps(metadata_patch) if metadata_patch is not None else None,
            base,
            branch,
            turn_id,
        ),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is not None:
        await db.execute(
            "UPDATE hitl_requests SET status='cancelled', resolved_at=? "
            "WHERE turn_id=? AND status='pending'",
            (closed_at, turn_id),
        )
    return row


async def normalize_open_turns(
    db: aiosqlite.Connection,
    *,
    now: str,
    agent_name: str | None = None,
    session_id: str | None = None,
) -> None:
    """归一已停止的执行，仅补写本次收敛的活动轮次。"""
    await hitl.orphan_all(db, now=now, agent_name=agent_name, session_id=session_id)
    rows = await (
        await db.execute(
            f"SELECT turn_id FROM turns WHERE {_ACTIVE} "
            "AND (? IS NULL OR agent_name=?) AND (? IS NULL OR session_id=?)",
            (agent_name, agent_name, session_id, session_id),
        )
    ).fetchall()
    for row in rows:
        await close_turn(
            db,
            turn_id=row["turn_id"],
            status="interrupted",
            closing_kind="cancelled",
            end_node_id=None,
            closed_at=now,
        )


class RollbackError(ValueError):
    """可映射为 Core 能力错误的回滚拒绝。"""

    def __init__(self, reason: str, *, suggested_node_ids: list[str] | None = None) -> None:
        super().__init__(reason)
        self.details = (
            {} if suggested_node_ids is None else {"suggested_node_ids": suggested_node_ids}
        )


@dataclass(frozen=True)
class RollbackPoint:
    """目标节点及执行前的会话 Head。"""

    node_id: str
    branch_id: str
    previous_branch_id: str
    previous_head_id: str
    target_is_compact: bool


@dataclass(frozen=True)
class RollbackOutcome:
    """回滚记录和本次新建的分支。重放只返回持久记录。"""

    record: TurnRecord
    branch: ActionBranch | None = None

    def as_payload(self) -> dict[str, Any]:
        """从持久回滚记录生成 Core 能力回执。"""
        metadata = self.record.metadata
        return {
            "turn_id": self.record.turn_id,
            "session_id": self.record.session_id,
            "status": self.record.status,
            "closing_kind": self.record.closing_kind,
            "target": metadata["rollback_target"],
            "target_node_id": metadata["rollback_to_node_id"],
            "branch_id": metadata["rollback_branch_id"],
            "parent_branch_id": metadata["rollback_parent_branch_id"],
            "released_delivery_ids": metadata["rollback_released_delivery_ids"],
            "already_applied": self.branch is None,
            "rolled_back_at": metadata["rolled_back_at"],
        }


async def load_turn_for_rollback(
    db: aiosqlite.Connection, *, turn_id: str, agent_name: str
) -> aiosqlite.Row:
    """按 Agent 作用域读取轮次。"""
    async with db.execute(
        "SELECT * FROM turns WHERE turn_id=? AND agent_name=?", (turn_id, agent_name)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise RollbackError("turn_not_found")
    return row


def replay_rollback(
    row: aiosqlite.Row, target: RollbackTarget, node_id: str | None
) -> RollbackOutcome:
    """同一请求重放首次结果，目标变化时拒绝。"""
    metadata = json.loads(row["metadata"] or "{}")
    expected_node_id = row["base_node_id"] if target == "base" else node_id
    if (
        metadata.get("rollback_target") != target
        or metadata.get("rollback_to_node_id") != expected_node_id
    ):
        raise RollbackError("rollback_conflict")
    return RollbackOutcome(turn_record(row))


async def resolve_rollback_point(
    db: aiosqlite.Connection, row: aiosqlite.Row, target: RollbackTarget, node_id: str | None
) -> RollbackPoint:
    """执行与预览使用相同的前置条件和目标解析。"""
    if row["status"] in ("open", "waiting_hitl"):
        raise RollbackError("turn_active")
    if row["status"] == "rolled_back":
        raise RollbackError("already_rolled_back")
    if row["status"] not in _ROLLBACK_STATUSES:
        raise RollbackError("turn_not_rollbackable")
    if row["start_node_id"] is None:
        raise RollbackError("turn_has_no_nodes")
    if target == "base" and row["base_node_id"] is None:
        raise RollbackError("turn_base_missing")

    scope = (row["agent_name"], row["session_id"])
    async with db.execute(
        """
        SELECT s.active_branch_id, b.head_node_id,
            EXISTS(SELECT 1 FROM turns a WHERE a.agent_name=s.agent_name
                   AND a.session_id=s.session_id AND a.status IN ('open', 'waiting_hitl')),
            EXISTS(SELECT 1 FROM hitl_requests h WHERE h.turn_id=? AND h.status='pending')
        FROM sessions s JOIN branches b
            ON b.branch_id=s.active_branch_id AND b.session_id=s.session_id
        WHERE s.agent_name=? AND s.session_id=? AND s.lifecycle!='deleted'
        """,
        (row["turn_id"], *scope),
    ) as cursor:
        session = await cursor.fetchone()
    if session is None:
        raise RollbackError("turn_session_missing")
    previous_branch_id, previous_head_id, active, pending_hitl = session
    if active:
        raise RollbackError("session_has_active_turn")
    if pending_hitl:
        raise RollbackError("turn_has_pending_hitl")

    effective_node_id = row["base_node_id"] if target == "base" else node_id
    async with db.execute(
        """
        SELECT n.id, n.created_on_branch_id, json_extract(n.metadata, '$.node_kind'), b.lifecycle
        FROM nodes n LEFT JOIN branches b
            ON b.branch_id=n.created_on_branch_id AND b.session_id=n.session_id
        WHERE n.agent_name=? AND n.session_id=? AND n.id=? AND (?='base' OR n.turn_id=?)
        """,
        (*scope, effective_node_id, target, row["turn_id"]),
    ) as cursor:
        node = await cursor.fetchone()
    if node is None:
        raise RollbackError("rollback_target_not_found")
    resolved_node_id, branch_id, node_kind, lifecycle = node
    if node_kind == INCOMPLETE_KIND:
        suggestions = await suggest_rollback_targets(db, row)
        raise RollbackError("rollback_target_not_usable", suggested_node_ids=suggestions)
    if lifecycle is None or lifecycle == "deleted":
        raise RollbackError("target_branch_deleted")
    return RollbackPoint(
        resolved_node_id, branch_id, previous_branch_id, previous_head_id, node_kind == "compact"
    )


async def suggest_rollback_targets(
    db: aiosqlite.Connection, row: aiosqlite.Row, *, limit: int = 20
) -> list[str]:
    """列出该轮仍可选作回滚点的节点。"""
    async with db.execute(
        f"""
        SELECT n.id FROM nodes n JOIN branches b
            ON b.branch_id=n.created_on_branch_id AND b.session_id=n.session_id
        WHERE n.turn_id=? AND n.agent_name=? AND n.session_id=? AND b.lifecycle!='deleted'
            AND {NOT_INCOMPLETE_SQL.replace("metadata", "n.metadata")}
        ORDER BY n.rowid LIMIT ?
        """,
        (row["turn_id"], row["agent_name"], row["session_id"], limit),
    ) as cursor:
        return [item[0] for item in await cursor.fetchall()]


async def count_truncated_nodes(
    db: aiosqlite.Connection, row: aiosqlite.Row, *, target_node_id: str
) -> tuple[int, bool]:
    """计算回滚分支祖先之外的本轮节点与 compact 标记。"""
    async with db.execute(
        """
        WITH RECURSIVE ancestors(id, parent_id) AS (
            SELECT id, parent_id FROM nodes WHERE id=?
            UNION ALL
            SELECT n.id, n.parent_id FROM nodes n JOIN ancestors a ON n.id=a.parent_id
            WHERE n.agent_name=? AND n.session_id=?
        )
        SELECT COUNT(*), COALESCE(MAX(json_extract(metadata, '$.node_kind')='compact'), 0)
        FROM nodes WHERE turn_id=? AND agent_name=? AND session_id=?
            AND id NOT IN (SELECT id FROM ancestors)
        """,
        (
            target_node_id,
            row["agent_name"],
            row["session_id"],
            row["turn_id"],
            row["agent_name"],
            row["session_id"],
        ),
    ) as cursor:
        counts = await cursor.fetchone()
    assert counts is not None
    return counts[0], bool(counts[1])


async def rollback_turn(
    db: aiosqlite.Connection,
    *,
    turn_id: str,
    agent_name: str,
    target: RollbackTarget,
    point: RollbackPoint,
    new_branch_id: str,
    rolled_back_at: str,
) -> None:
    """认领回滚。此更新须先于新分支与输入解绑。"""
    async with db.execute(
        f"""
        UPDATE turns SET status='rolled_back', closing_kind='rolled_back', waiting_since=NULL,
            metadata=json_set(
                COALESCE(metadata, '{{}}'),
                '$.rollback_target', ?, '$.rollback_to_node_id', ?,
                '$.rollback_branch_id', ?, '$.rollback_parent_branch_id', ?,
                '$.rollback_from_end_node_id', COALESCE(end_node_id, ''), '$.rolled_back_at', ?
            )
        WHERE turn_id=? AND agent_name=? AND status IN ({_ROLLBACK_PLACEHOLDERS})
            AND start_node_id IS NOT NULL
        """,
        (
            target,
            point.node_id,
            new_branch_id,
            point.branch_id,
            rolled_back_at,
            turn_id,
            agent_name,
            *_ROLLBACK_STATUSES,
        ),
    ) as cursor:
        if cursor.rowcount != 1:
            raise RollbackError("rollback_conflict")


async def record_released_inputs(
    db: aiosqlite.Connection, *, turn_id: str, released_delivery_ids: list[str]
) -> aiosqlite.Row:
    """保留首次回滚的投递结果，供重放返回。"""
    async with db.execute(
        "UPDATE turns SET metadata=json_set(metadata, '$.rollback_released_delivery_ids', json(?)) "
        "WHERE turn_id=? RETURNING *",
        (json.dumps(released_delivery_ids), turn_id),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    return row
