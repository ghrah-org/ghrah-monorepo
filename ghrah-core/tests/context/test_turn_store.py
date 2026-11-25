# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""链侧轮次、锚点与恢复。"""

from datetime import UTC, datetime

from ghrah.context import ContextManager, turn
from ghrah.context.persistence import ContextChanges
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.persistence.sqlite_read import SqliteReadDomain
from ghrah.context.session_runtime import SessionRuntime
from ghrah.core.read.turn import TurnReadStore


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
        ordinary = runtime.commit_node(
            turn_id=active.turn_id,
            metadata={
                "token_usage": {
                    "input_tokens": 40,
                    "output_tokens": 10,
                    "total_tokens": 50,
                    "cache_read_tokens": 20,
                },
            },
        )
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
        domain = SqliteReadDomain(backend.db_path)
        try:
            detail = await TurnReadStore(domain).detail(agent_id="agent", turn_id=active.turn_id)
        finally:
            await domain.aclose()
        assert detail is not None
        assert [node.id for node in detail.nodes] == [compact.id, ordinary.id]
        stats = detail.turn.stats
        assert (stats.steps, stats.node_count) == (1, 2)
        assert (stats.token_input, stats.token_output, stats.token_total) == (40, 10, 50)
        assert stats.cache_hit_rate == 0.5
        assert stats.duration_ms is not None and stats.duration_ms >= 0


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


async def _stored_hitl(tmp_path, options):
    """登记一条待审批请求并返回库文件路径；``options`` 即写路径落列的内容。"""
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        await cm.open_turn()
        stored = await cm.open_hitl_request(
            promise_id="promise",
            ability_name="shell",
            tool_call_id="call",
            tool_call={"command": "git status"},
            reason="needs approval",
            options=options,
            deadline_at=None,
        )
        assert stored.persisted
    return path


async def test_pending_hitl_parses_shell_remember_from_options(tmp_path):
    """shell 审批：读面从 options 还原「批准并记住」候选，options 原样保留。"""
    path = await _stored_hitl(
        tmp_path,
        {
            "exact_key": "git status",
            "exact_label": "git status",
            "class_key": "git",
            "class_label": "git *",
        },
    )
    domain = SqliteReadDomain(path)
    try:
        (pending,) = (
            await TurnReadStore(domain).pending_hitl(agent_id="agent", session_id=None)
        ).items
    finally:
        await domain.aclose()
    assert pending.options["exact_key"] == "git status"
    assert pending.shell_remember is not None
    assert (
        pending.shell_remember.exact_key,
        pending.shell_remember.exact_label,
        pending.shell_remember.class_key,
        pending.shell_remember.class_label,
    ) == ("git status", "git status", "git", "git *")


async def test_pending_hitl_without_options_has_no_shell_remember(tmp_path):
    """非 shell 审批写 ``options={}``：读面给出 ``None``，不是空对象。"""
    path = await _stored_hitl(tmp_path, {})
    domain = SqliteReadDomain(path)
    try:
        (pending,) = (
            await TurnReadStore(domain).pending_hitl(agent_id="agent", session_id=None)
        ).items
    finally:
        await domain.aclose()
    assert pending.options == {}
    assert pending.shell_remember is None


async def test_hitl_request_ignores_malformed_options(tmp_path):
    """旧库脏 ``options`` 形状不符：读面置 ``None``，不打红整条读查询。"""
    path = await _stored_hitl(tmp_path, {"exact_key": {"nested": True}, "exact_label": "x"})
    domain = SqliteReadDomain(path)
    try:
        view = await TurnReadStore(domain).hitl_request(agent_id="agent", promise_id="promise")
    finally:
        await domain.aclose()
    assert view is not None
    assert view.options == {"exact_key": {"nested": True}, "exact_label": "x"}
    assert view.shell_remember is None
