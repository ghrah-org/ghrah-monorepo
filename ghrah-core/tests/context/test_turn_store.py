# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""链侧轮次、锚点与恢复。"""

from datetime import UTC, datetime

from ghrah.context import ContextManager, turn
from ghrah.context.persistence import ContextChanges
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.session_runtime import SessionRuntime


async def test_first_compact_member_anchors_turn(tmp_path):
    runtime = SessionRuntime.create(agent_name="agent")
    base = runtime.session.root_node_id
    branch = runtime.session.active_branch_id
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        active = turn.ActiveTurn("turn", "agent", runtime.session.session_id, None, "self")
        async with backend.transaction() as db:
            await turn.insert_turn_placeholder(db, active, opened_at=datetime.now(UTC).isoformat())
        compact = runtime.commit_node(turn_id=active.turn_id, metadata={"node_kind": "compact"})
        await backend.apply_changes(
            ContextChanges(
                agent_name="agent",
                active_session_id=runtime.session.session_id,
                sessions=(runtime.session,),
                branches=(runtime.get_branch(branch),),
                nodes=tuple(runtime.chain.nodes),
            )
        )
        row = await backend.load_turn("agent", active.turn_id)
        assert row["start_node_id"] == compact.id
        ordinary = runtime.commit_node(turn_id=active.turn_id)
        await backend.apply_changes(
            ContextChanges(
                agent_name="agent", branches=(runtime.get_branch(branch),), nodes=(ordinary,)
            )
        )
        async with backend.transaction() as db:
            row = await turn.close_turn(
                db,
                turn_id=active.turn_id,
                status="closed",
                closing_kind="completed",
                end_node_id=ordinary.id,
                closed_at=datetime.now(UTC).isoformat(),
            )
            assert (
                await turn.close_turn(
                    db,
                    turn_id=active.turn_id,
                    status="interrupted",
                    closing_kind="cancelled",
                    end_node_id=None,
                    closed_at=datetime.now(UTC).isoformat(),
                )
                is None
            )
        assert (row["base_node_id"], row["start_node_id"], row["end_node_id"]) == (
            base,
            compact.id,
            ordinary.id,
        )
        assert row["branch_id"] == row["end_branch_id"] == branch
        assert (await backend.load_node("agent", compact.id)).turn_id == active.turn_id


async def test_context_restore_only_normalizes_its_own_turns(tmp_path):
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        first = ContextManager("a", persistence=backend)
        second = ContextManager("b", persistence=backend)
        await first.persist()
        await second.persist()
        first_turn = await first.open_turn()
        second_turn = await second.open_turn()

        restored = ContextManager("a", persistence=backend)
        await restored.restore("a")
        assert restored.active_turn_id is None
        interrupted = await backend.load_turn("a", first_turn)
        assert interrupted["status"] == "interrupted"
        assert interrupted["start_node_id"] is None
        assert interrupted["base_node_id"] == interrupted["end_node_id"] == restored.active_head.id
        assert (await backend.load_turn("b", second_turn))["status"] == "open"


async def test_suggested_rollback_targets_include_compact_member(tmp_path):
    """compact 是普通成员：建议回滚目标包含它，排除逻辑只剩 incomplete。"""
    runtime = SessionRuntime.create(agent_name="agent")
    branch = runtime.session.active_branch_id
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        active = turn.ActiveTurn("turn", "agent", runtime.session.session_id, None, "self")
        async with backend.transaction() as db:
            await turn.insert_turn_placeholder(db, active, opened_at=datetime.now(UTC).isoformat())
        compact = runtime.commit_node(turn_id=active.turn_id, metadata={"node_kind": "compact"})
        await backend.apply_changes(
            ContextChanges(
                agent_name="agent",
                sessions=(runtime.session,),
                branches=(runtime.get_branch(branch),),
                nodes=(compact,),
            )
        )
        ordinary = runtime.commit_node(turn_id=active.turn_id)
        await backend.apply_changes(
            ContextChanges(
                agent_name="agent",
                branches=(runtime.get_branch(branch),),
                nodes=(ordinary,),
            )
        )
        async with backend.transaction() as db:
            row = await turn.close_turn(
                db,
                turn_id=active.turn_id,
                status="interrupted",
                closing_kind="cancelled",
                end_node_id=ordinary.id,
                closed_at=datetime.now(UTC).isoformat(),
            )
            suggestions = await turn.suggest_rollback_targets(db, row, limit=10)
        assert compact.id in suggestions
        assert ordinary.id in suggestions
