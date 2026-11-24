# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""轮次存储的装配契约：独立链库自带轮次能力，受管消息运行时绑定同域实例。"""

import pytest

from ghrah.communication import MessageManager
from ghrah.communication.sqlite_message_store import SqliteMessageStore
from ghrah.context import ContextManager
from ghrah.context.persistence import ContextChanges
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.persistence.sqlite_turn_store import SqliteTurnStore


async def test_standalone_backend_carries_turn_store(tmp_path):
    """直接构造的 SQLite 后端默认可在自己的事务里开轮次。"""
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        assert isinstance(backend.turn_store, SqliteTurnStore)
        assert backend.turn_store is not None
        cm = ContextManager("agent", persistence=backend, auto_persist=True)
        turn_id = await cm.open_turn()
        assert turn_id is not None
        record = await backend.turn_store.load_turn("agent", turn_id)
        assert record is not None and record.status == "open"
        await cm.close_turn(status="closed", closing_kind="completed")
        record = await backend.turn_store.load_turn("agent", turn_id)
        assert record is not None and record.status == "closed"


async def test_read_only_backend_has_no_turn_store(tmp_path):
    """只读入口不提供轮次写能力。"""
    async with SqliteBackend(tmp_path / "core.sqlite") as writer:
        await writer.apply_changes(ContextChanges(agent_name="agent"))
    async with SqliteBackend(tmp_path / "core.sqlite", read_only=True) as reader:
        assert reader.turn_store is None


async def test_managed_runtime_binds_same_domain_turn_store(tmp_path):
    """经 MessageManager 创建的链后端，轮次存储指向消息存储本身。"""
    path = tmp_path / "core.sqlite"
    store = SqliteMessageStore(path, "scope")
    await store.open(prepare_context=True)
    try:
        backend = store.context_backend()
        assert backend.turn_store is store
        assert isinstance(store, SqliteTurnStore)
    finally:
        await store.close()


async def test_foreign_settlement_is_rejected_inside_turn_transaction(tmp_path):
    """不属于本存储域的结算方不能在轮次事务内结算。"""
    path = tmp_path / "core.sqlite"
    mm = MessageManager(path, project_id="p", agent_ids=("a",))
    await mm.open(start_dispatcher=False, prepare_context=True)
    try:
        store = mm._sqlite
        backend = store.context_backend()
        await backend.connect()
        try:
            cm = ContextManager("a", persistence=backend, auto_persist=True)
            turn_id = await cm.open_turn()

            class ForeignSettlement:
                async def converge(self, **kwargs):
                    raise AssertionError("must not be called")

                async def release_inputs(self, **kwargs):
                    raise AssertionError("must not be called")

                async def pending_inputs(self, **kwargs):
                    raise AssertionError("must not be called")

            with pytest.raises(TypeError, match="does not share this storage domain"):
                await cm.abort_turn(turn_id=turn_id, settlement=ForeignSettlement())
            record = await store.load_turn("a", turn_id)
            assert record is not None and record.status == "open"
        finally:
            await backend.close()
    finally:
        await mm.close()


async def test_same_domain_settlement_converges_deliveries_in_turn_transaction(tmp_path):
    """同域结算方在轮次事务内一并收敛开轮投递。"""
    path = tmp_path / "core.sqlite"
    mm = MessageManager(path, project_id="p", agent_ids=("a",), session_routing=True)
    await mm.open(start_dispatcher=False, prepare_context=True)
    try:
        store = mm._sqlite
        backend = store.context_backend()
        await backend.connect()
        try:
            cm = ContextManager("a", persistence=backend, auto_persist=True)
            await cm.persist()
            request = await mm.send_external(
                sender="user", recipient_ids=("a",), content="hi", idempotency_key="k"
            )
            claim = await store.claim_delivery(
                "a", session_aware=True, session_routing=True, require_turns=True
            )
            assert claim is not None and claim.execution.turn_id is not None
            cm.attach_turn(
                turn_id=claim.execution.turn_id,
                session_id=claim.target_session_id,
                opener_delivery_id=claim.execution.delivery_id,
            )
            outcome = await cm.abort_turn(
                turn_id=claim.execution.turn_id, settlement=mm.delivery_settlement
            )
            assert outcome.changed and outcome.record.status == "aborted"
            assert outcome.released_deliveries == 0
            (delivery,) = await mm.get_deliveries(request.message_id)
            assert delivery.state.value == "interrupted"
        finally:
            await backend.close()
    finally:
        await mm.close()
