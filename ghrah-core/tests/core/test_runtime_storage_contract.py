# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""通过装配工厂重跑读面、作用域与领取凭证契约。"""

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import ProblemCode
from ghrah.protocol.views.observer_room import RoomFactsQuery, RoomMessageReference

from ghrah.communication.sqlite_message_store import SqliteMessageStore
from ghrah.communication.storage import FinalDeliveryResult
from ghrah.context import ContextManager
from ghrah.context.persistence import ContextChanges
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.persistence.sqlite_read import SqliteReadDomain
from ghrah.context.turn import TurnRevokedError
from ghrah.core.runtime_storage import RuntimeStorage, SqliteRuntimeStorage
from ghrah.core.wiring import build_core_port


@pytest.fixture(params=[SqliteRuntimeStorage], ids=["sqlite"])
def backend_factory(request: pytest.FixtureRequest) -> Callable[[Path, str], RuntimeStorage]:
    """候选后端由同一工厂夹具装配，行为断言只查询端口和存储契约。"""
    return request.param


async def test_offline_and_live_reads_share_scope(tmp_path, backend_factory):
    path = tmp_path / "core.db"
    grants = tmp_path / "authorization" / "grants.db"
    storage = backend_factory(path, "p")
    starts = []

    def factory(scope_id: str) -> RuntimeStorage:
        starts.append(scope_id)
        return storage

    port = build_core_port(
        scope_id="p",
        db_path=str(path),
        storage_factory=factory,
        ability_db_path=str(grants),
    )
    query = RoomFactsQuery(room_id="room")
    try:
        assert starts == ["p"]
        assert (await port.sessions.catalog(agent_id="worker")).sessions == []
        assert (await port.transcript.facts(query)).messages == []
        assert not path.exists()
        assert not grants.exists()

        sent = await port.mailbox.publish_external(
            sender="reader",
            recipient_ids=[],
            content="正文",
            idempotency_key="request",
            metadata={"room_ids": ["room"]},
        )
        query.messages = [RoomMessageReference(message_id=sent.message_id, author="reader")]
        live = await port.transcript.facts(query)
        assert [message.content for message in live.messages] == ["正文"]
        assert grants.is_file()
    finally:
        await port.aclose()

    offline = build_core_port(
        scope_id="p",
        db_path=str(path),
        storage_factory=lambda scope: backend_factory(path, scope),
    )
    foreign = build_core_port(
        scope_id="other",
        db_path=str(path),
        storage_factory=lambda scope: backend_factory(path, scope),
    )
    try:
        assert await offline.transcript.facts(query) == live
        assert (
            await offline.transcript.facts(query.model_copy(update={"room_id": "other"}))
        ).messages == []
        wrong_author = [query.messages[0].model_copy(update={"author": "other"})]
        assert (
            await offline.transcript.facts(query.model_copy(update={"messages": wrong_author}))
        ).messages == []
        with pytest.raises(ApiError) as error:
            await foreign.transcript.facts(query)
        assert error.value.problem.code == ProblemCode.FAILED_PRECONDITION
        with pytest.raises(ApiError) as error:
            await foreign.sessions.catalog(agent_id="worker")
        assert error.value.problem.code == ProblemCode.FAILED_PRECONDITION
    finally:
        await offline.aclose()
        await foreign.aclose()


async def test_claim_token_is_rejected_by_new_storage_domain(tmp_path, backend_factory):
    path = tmp_path / "core.db"
    claims = []
    for scope in ("first", "second"):
        domain = backend_factory(path.with_name(f"{scope}.db"), "p")
        manager = await domain.start_messages(start_dispatcher=False)
        try:
            async with domain.context_backend(None) as backend:
                chain = ContextManager("worker", persistence=backend)
                await chain.persist()
            await manager.refresh_agent_ids()
            request = await manager.send_external(
                sender="reader",
                recipient_ids=["worker"],
                content="input",
                idempotency_key="same",
            )
            operations = manager._delivery_store
            claim = await operations.claim_delivery(
                "worker",
                session_aware=True,
                session_routing=True,
                require_turns=True,
            )
            assert claim is not None
            execution = claim.execution
            result = FinalDeliveryResult(content="output", node_id=None, content_blocks=None)
            if claims:
                # 相同作用域中的投递、收件人、领取次数与轮次均可匹配，只替换凭证。
                foreign = replace(execution, domain_token=claims[0].domain_token)
                with pytest.raises(TurnRevokedError):
                    await operations.save_final_result(foreign, result)
                assert (await manager.get_deliveries(request.message_id))[0].state == "processing"
            claims.append(execution)
            assert (await operations.save_final_result(execution, result)).needs_completion
            if scope == "second":
                await operations.finish_delivery(execution.delivery_id)
                assert (await manager.get_deliveries(request.message_id))[0].state == "completed"
        finally:
            await manager.close()
            await domain.aclose()

        if scope == "first":
            reopened = backend_factory(path.with_name("first.db"), "p")
            manager = await reopened.start_messages(start_dispatcher=False)
            try:
                assert (await manager.get_deliveries(request.message_id))[0].state == "completed"
                with pytest.raises(TurnRevokedError):
                    await manager._delivery_store.save_final_result(execution, result)
                (reply,) = await manager.get_replies(request.message_id)
                assert reply.content == "output"
                await manager.recover_completions()
                assert await manager.get_replies(request.message_id) == (reply,)
            finally:
                await manager.close()
                await reopened.aclose()


def _database_dump(path: Path) -> list[str]:
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


async def test_legacy_adoption_and_context_writer_are_domain_bound(tmp_path):
    path = tmp_path / "chain.db"
    async with SqliteBackend(path) as standalone:
        chain = ContextManager("worker", persistence=standalone)
        await chain.persist()

    original = _database_dump(path)
    foreign = SqliteMessageStore(path, "foreign")
    with pytest.raises(ValueError, match="explicit adoption"):
        await foreign.open(prepare_context=True)
    assert _database_dump(path) == original

    owned = SqliteMessageStore(path, "owner", adopt_legacy_chain=True)
    await owned.open(prepare_context=True)
    try:
        managed = owned.context_backend()
        async with managed, managed.transaction() as db:
            assert db is owned.require_connection()
            await managed.apply_changes_in_transaction(db, ContextChanges(agent_name="new"))
        async with SqliteBackend(path) as bare:
            assert await bare.list_agents() == ["new", "worker"]
            with pytest.raises(RuntimeError, match="require the storage domain"):
                await bare.apply_changes(ContextChanges(agent_name="foreign"))
        with pytest.raises(ValueError, match="same-domain"):
            foreign.validate_context_backend(managed)
    finally:
        await owned.close()

    with pytest.raises(ValueError, match="scope/schema mismatch"):
        await SqliteMessageStore(path, "foreign", adopt_legacy_chain=True).open(
            prepare_context=True
        )


async def test_invalid_message_version_leaves_database_untouched(tmp_path):
    path = tmp_path / "bad.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE mm_meta (singleton INTEGER PRIMARY KEY, project_id TEXT, version INTEGER)"
        )
        db.execute("INSERT INTO mm_meta VALUES (1, 'p', 99)")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="scope/schema mismatch"):
        await SqliteMessageStore(path, "p").open(prepare_context=True)
    assert path.read_bytes() == original


async def test_startup_ddl_rolls_back_before_releasing_owner(tmp_path, monkeypatch):
    from ghrah.communication import sqlite_message_store

    async def fail_recovery(_db):
        raise RuntimeError("simulated initialization failure")

    path = tmp_path / "new.db"
    store = SqliteMessageStore(path, "p")
    with monkeypatch.context() as patch:
        patch.setattr(sqlite_message_store, "settle_terminal_turns", fail_recovery)
        with pytest.raises(RuntimeError, match="simulated initialization failure"):
            await store.open(prepare_context=True)
    assert _database_dump(path) == ["BEGIN TRANSACTION;", "COMMIT;"]
    await store.open(prepare_context=True)
    await store.close()


async def test_frozen_wal_and_ability_reads_leave_database_untouched(tmp_path):
    path = tmp_path / "core.db"
    async with SqliteBackend(path) as backend:
        chain = ContextManager("worker", persistence=backend)
        await chain.persist()
    original = path.read_bytes()
    file_names = sorted(p.name for p in tmp_path.iterdir())
    path.chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        port = build_core_port(scope_id="p", db_path=str(path), immutable_reads=True)
        try:
            catalog = await port.sessions.catalog(agent_id="worker")
            assert catalog.active_session_id == chain.active_session_id
            assert (
                await port.transcript.facts(
                    RoomFactsQuery(room_id="room", active_agent_ids=["worker"])
                )
            ).turns == []
            with pytest.raises(ApiError) as error:
                await port.abilities.tree(agent_id="worker")
            assert error.value.problem.code == ProblemCode.NOT_FOUND
        finally:
            await port.aclose()
        assert sorted(p.name for p in tmp_path.iterdir()) == file_names
        assert path.read_bytes() == original
    finally:
        tmp_path.chmod(0o755)
        path.chmod(0o644)


async def test_offline_ability_reader_does_not_initialize_shared_database(tmp_path):
    path = tmp_path / "core.db"
    async with SqliteBackend(path) as backend:
        await ContextManager("worker", persistence=backend).persist()
    before = _database_dump(path)
    port = build_core_port(scope_id="p", db_path=str(path))
    try:
        with pytest.raises(ApiError) as error:
            await port.abilities.tree(agent_id="worker")
        assert error.value.problem.code == ProblemCode.NOT_FOUND
        assert (await port.abilities.instances()).instances == []
    finally:
        await port.aclose()
    assert _database_dump(path) == before


def test_storage_factory_rejects_mismatched_path(tmp_path):
    with pytest.raises(ValueError, match="database path mismatch"):
        build_core_port(
            scope_id="p",
            db_path=str(tmp_path / "advertised.db"),
            storage_factory=lambda scope: SqliteRuntimeStorage(tmp_path / "actual.db", scope),
        )


async def test_chain_queries_have_independent_snapshots(tmp_path):
    path = tmp_path / "core.db"
    async with SqliteBackend(path) as backend:
        await ContextManager("worker", persistence=backend).persist()
    domain = SqliteReadDomain(path)

    async def other_snapshot():
        async with domain.chain_snapshot() as (_, db):
            return db

    async with domain.chain_snapshot() as (_, first):
        second = await asyncio.wait_for(other_snapshot(), timeout=1)
        assert second is not None and second is not first


async def test_cancelled_close_holds_owner_until_connection_closes(tmp_path, monkeypatch):
    path = tmp_path / "core.db"
    store = SqliteMessageStore(path, "p")
    await store.open(prepare_context=True)
    closing, release = asyncio.Event(), asyncio.Event()
    db = store.require_connection()
    original_close = db.close

    async def slow_close():
        closing.set()
        await release.wait()
        await original_close()

    monkeypatch.setattr(db, "close", slow_close)
    task = asyncio.create_task(store.close())
    try:
        await closing.wait()
        task.cancel()
        with pytest.raises(RuntimeError, match="active manager"):
            await SqliteMessageStore(path, "p").open(prepare_context=True)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    other = SqliteMessageStore(path, "p")
    await other.open(prepare_context=True)
    await other.close()
