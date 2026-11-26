# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""真实 SQLite 与 agent loop 的最小持久通信闭环。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import aiosqlite
import pytest
from _helpers import ScriptedLLM

from ghrah.ability.builtin.core.operations.conversation import ConversationAbility
from ghrah.agents.builder import AgentBuilder
from ghrah.communication.message_manager import DeliveryState, MessageKind, MessageManager
from ghrah.communication.sqlite_message_store import SqliteMessageStore
from ghrah.context.turn import TurnRevokedError
from ghrah.core.config import AgentConfig
from ghrah.core.message import AgentMessage, MessageType


def make_manager(path: Path, **kwargs) -> MessageManager:
    """显式声明项目与在线、离线身份。"""
    return MessageManager(path, project_id="project", agent_ids=("a", "b", "c"), **kwargs)


def make_agent(agent_id: str, replies: list[str]):
    """使用脚本 LLM 创建真实 agent，显示名与稳定 ID 故意不同。"""
    llm = ScriptedLLM(replies)
    agent = AgentBuilder.from_config(
        AgentConfig(name=f"display-{agent_id}", agent_id=agent_id),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: llm,
    )
    return agent, llm


async def send_request(manager: MessageManager, key: str = "request"):
    """提交 A 到 B/C 的请求。"""
    return await manager.send(
        sender_id="a",
        recipient_ids=("b", "c", "b"),
        content="检查设计",
        kind=MessageKind.REQUEST,
        idempotency_key=key,
    )


async def test_real_agents_fanout_offline_restart_replies_and_idle_wakeup(tmp_path):
    """B 在线、C 离线，重建管理器后 C 收到，空闲 A 消费两条回复。"""
    path = tmp_path / "messages.sqlite"
    b, b_llm = make_agent("b", ["B 检查完成"])
    async with make_manager(path) as manager:
        manager.bind("b", b.receive)
        request = await send_request(manager)
        await manager.dispatch_pending()
        deliveries = await manager.get_deliveries(request.message_id)
        assert {d.recipient_id: d.state for d in deliveries} == {
            "b": DeliveryState.COMPLETED,
            "c": DeliveryState.PENDING,
        }
        assert len(b_llm.calls) == 1
        (reply,) = await manager.get_replies(request.message_id)
        assert (reply.sender_id, reply.recipient_ids, reply.reply_to) == (
            "b",
            ("a",),
            request.message_id,
        )
        assert (await manager.get_deliveries(reply.message_id))[0].state == DeliveryState.PENDING

    a, a_llm = make_agent("a", ["收到 B", "收到 C"])
    c, c_llm = make_agent("c", ["C 检查完成"])
    async with make_manager(path) as manager:
        manager.bind("a", a.receive)
        manager.bind("c", c.receive)
        await manager.dispatch_pending()
        assert await manager.get_message(request.message_id) == request
        assert all(
            d.state == DeliveryState.COMPLETED
            for d in await manager.get_deliveries(request.message_id)
        )
        replies = await manager.get_replies(request.message_id)
        assert {r.content for r in replies} == {"B 检查完成", "C 检查完成"}
        assert len(a_llm.calls) == 2
        assert len(c_llm.calls) == 1
        for reply in replies:
            assert (await manager.get_deliveries(reply.message_id))[
                0
            ].state == DeliveryState.COMPLETED
            assert await manager.get_replies(reply.message_id) == ()
        b_input = next(m for m in b_llm.calls[0] if m.source == "agent:a")
        assert b_input.metadata["message_id"] == request.message_id
        assert b_input.metadata["delivery_id"] == deliveries[0].delivery_id
        assert b_input.metadata["agent_id"] == "b"


async def test_idempotency_validation_and_query_survive_reopen(tmp_path):
    path = tmp_path / "messages.sqlite"
    async with make_manager(path) as manager:
        request = await send_request(manager)
        assert await send_request(manager) == request
        assert len(await manager.get_deliveries(request.message_id)) == 2
        with pytest.raises(ValueError, match="conflicts"):
            await manager.send(
                sender_id="a",
                recipient_ids=("b",),
                content="different",
                kind=MessageKind.REQUEST,
                idempotency_key="request",
            )
        with pytest.raises(ValueError, match="Unknown"):
            await manager.send(
                sender_id="a",
                recipient_ids=("b", "unknown"),
                content="invalid",
                kind=MessageKind.REQUEST,
                idempotency_key="invalid",
            )
        with pytest.raises(KeyError):
            await manager.get_deliveries("unknown")
        with pytest.raises(ValueError, match="reserved"):
            await manager.send(
                sender_id="b",
                recipient_ids=("a",),
                content="spoof",
                kind=MessageKind.NOTIFICATION,
                idempotency_key="response:reserved",
            )
        with pytest.raises(ValueError, match="collection"):
            await manager.send(
                sender_id="a",
                recipient_ids="b",
                content="invalid",
                kind=MessageKind.NOTIFICATION,
                idempotency_key="string-target",
            )
    async with make_manager(path) as manager:
        assert await send_request(manager) == request
    async with aiosqlite.connect(path) as db:
        assert (await (await db.execute("SELECT count(*) FROM mm_messages")).fetchone())[0] == 1


async def test_one_agent_serial_other_agents_parallel_and_ordered(tmp_path):
    entered = asyncio.Event()
    other_entered = asyncio.Event()
    release = asyncio.Event()
    calls = []
    active = 0
    max_active = 0

    async def b_receive(message):
        nonlocal active, max_active
        active += 1
        max_active = max(active, max_active)
        calls.append(message.content)
        entered.set()
        await release.wait()
        active -= 1
        return AgentMessage.create_reply(message, "ok")

    async def c_receive(message):
        other_entered.set()
        return AgentMessage.create_reply(message, "ok")

    async with make_manager(tmp_path / "messages.sqlite") as manager:
        manager.bind("b", b_receive)
        manager.bind("c", c_receive)
        for index in range(3):
            await manager.send(
                sender_id="a",
                recipient_ids=("b",),
                content=str(index),
                kind=MessageKind.NOTIFICATION,
                idempotency_key=f"b-{index}",
            )
        await manager.send(
            sender_id="a",
            recipient_ids=("c",),
            content="parallel",
            kind=MessageKind.NOTIFICATION,
            idempotency_key="c",
        )
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(other_entered.wait(), 1)
        assert calls == ["0"]
        release.set()
        await asyncio.gather(manager.dispatch_pending(), manager.dispatch_pending())
        assert calls == ["0", "1", "2"]
        assert max_active == 1


async def test_failed_recipient_does_not_prevent_other_reply_and_explicit_retry(tmp_path):
    async def fail(message):
        return AgentMessage.create_reply(message, "tool failed", MessageType.ERROR)

    async def reply(message):
        return AgentMessage.create_reply(message, "ok")

    async with make_manager(tmp_path / "messages.sqlite") as manager:
        manager.bind("b", fail)
        manager.bind("c", reply)
        request = await send_request(manager)
        await manager.dispatch_pending()
        deliveries = await manager.get_deliveries(request.message_id)
        b_delivery = next(d for d in deliveries if d.recipient_id == "b")
        assert b_delivery.state == DeliveryState.FAILED
        assert b_delivery.error == "tool failed"
        assert len(await manager.get_replies(request.message_id)) == 1
        await manager.unbind("b")
        manager.bind("b", reply)
        await manager.retry(b_delivery.delivery_id)
        await manager.dispatch_pending()
        assert (await manager.get_deliveries(request.message_id))[0].attempts == 2
        assert len(await manager.get_replies(request.message_id)) == 2
        with pytest.raises(ValueError, match="not retryable"):
            await manager.retry(b_delivery.delivery_id)


async def test_close_interrupts_worker_and_reopen_requires_explicit_retry(tmp_path):
    path = tmp_path / "messages.sqlite"
    entered = asyncio.Event()

    async def hang(message):
        entered.set()
        await asyncio.Event().wait()

    async with make_manager(path) as manager:
        manager.bind("b", hang)
        request = await send_request(manager)
        await asyncio.wait_for(entered.wait(), 1)
    async with make_manager(path) as manager:
        deliveries = await manager.get_deliveries(request.message_id)
        assert deliveries[0].state == DeliveryState.INTERRUPTED
        b, llm = make_agent("b", ["retry done"])
        manager.bind("b", b.receive)
        await manager.dispatch_pending()
        assert llm.calls == []
        await manager.retry(deliveries[0].delivery_id)
        await manager.dispatch_pending()
        assert len(llm.calls) == 1


async def test_single_active_manager_and_project_identity(tmp_path):
    path = tmp_path / "messages.sqlite"
    async with make_manager(path):
        with pytest.raises(RuntimeError, match="active manager"):
            async with make_manager(path):
                pass
    with pytest.raises(ValueError, match="mismatch"):
        async with MessageManager(path, project_id="other", agent_ids=("a",)):
            pass
    async with make_manager(path):
        pass


async def test_acceptance_is_atomic_on_recipient_insert_failure(tmp_path):
    path = tmp_path / "messages.sqlite"
    async with make_manager(path) as manager:
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "CREATE TRIGGER reject_c BEFORE INSERT ON mm_deliveries "
                "WHEN NEW.recipient_id='c' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
            await db.commit()
        with pytest.raises(Exception, match="injected failure"):
            await send_request(manager)
        async with aiosqlite.connect(path) as db:
            assert (await (await db.execute("SELECT count(*) FROM mm_messages")).fetchone())[0] == 0
            assert (await (await db.execute("SELECT count(*) FROM mm_deliveries")).fetchone())[
                0
            ] == 0


async def test_reply_and_completion_commit_atomically(tmp_path):
    path = tmp_path / "messages.sqlite"
    b, _ = make_agent("b", ["done"])
    async with make_manager(path) as manager:
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "CREATE TRIGGER reject_completion BEFORE UPDATE ON mm_deliveries "
                "WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
            await db.commit()
        manager.bind("b", b.receive)
        request = await send_request(manager)
        await manager.dispatch_pending()
        assert await manager.get_replies(request.message_id) == ()
        assert (await manager.get_deliveries(request.message_id))[
            0
        ].state == DeliveryState.PROCESSING
        delivery = (await manager.get_deliveries(request.message_id))[0]
        assert delivery.result_ready
        with pytest.raises(TimeoutError):
            await manager.wait_replies(request.message_id, timeout=0.02)
        with pytest.raises(ValueError, match="not retryable"):
            await manager.retry(delivery.delivery_id)
        async with aiosqlite.connect(path) as db:
            await db.execute("DROP TRIGGER reject_completion")
            await db.commit()
        await manager.recover_completions()
        assert len(await manager.get_replies(request.message_id)) == 1
        assert (await manager.get_deliveries(request.message_id))[
            0
        ].state == DeliveryState.COMPLETED


async def test_old_claim_cannot_save_result_or_interrupt_a_new_claim(tmp_path):
    manager = make_manager(tmp_path / "messages.sqlite")
    await manager.open(start_dispatcher=False)
    try:
        request = await send_request(manager)
        claim = manager._delivery_store.claim_delivery
        routing = {"session_aware": False, "session_routing": False, "require_turns": False}
        first = await claim("b", **routing)
        assert first is not None
        delivery_id = first.execution.delivery_id
        await manager._record_failure(
            delivery_id,
            DeliveryState.INTERRUPTED,
            "previous worker stopped",
            execution=first.execution,
        )
        await manager.retry(delivery_id)
        current = await claim("b", **routing)
        assert current is not None
        assert current.execution.delivery_id == delivery_id
        assert current.execution.attempt == first.execution.attempt + 1

        result = AgentMessage(sender="b", recipient="a", content="final result")
        with pytest.raises(TurnRevokedError, match="no longer owns the claim"):
            await manager._complete_delivery(first.execution, result)
        await manager._record_failure(
            delivery_id, DeliveryState.INTERRUPTED, "late failure", execution=first.execution
        )
        (processing, _) = await manager.get_deliveries(request.message_id)
        assert processing.state == DeliveryState.PROCESSING and not processing.result_ready
        assert await manager.get_replies(request.message_id) == ()

        await manager._complete_delivery(current.execution, result)
        await manager._complete_delivery(current.execution, result)
        (reply,) = await manager.get_replies(request.message_id)
        assert reply.content == "final result"
    finally:
        await manager.close()


async def test_actual_process_exit_keeps_pending_and_marks_processing_interrupted(tmp_path):
    """独立子进程强制退出，验证 SQLite 持久受理和进程锁释放。"""
    path = tmp_path / "messages.sqlite"
    script = """
import asyncio, os, sys
from ghrah.communication.message_manager import MessageManager, MessageKind
async def stop(message):
    os._exit(23)
async def main():
    async with MessageManager(sys.argv[1], project_id='project', agent_ids=('a','b','c')) as mm:
        mm.bind('b', stop)
        await mm.send(sender_id='a', recipient_ids=('b','c'), content='persisted',
                      kind=MessageKind.REQUEST, idempotency_key='crash')
        await mm.dispatch_pending()
asyncio.run(main())
"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(path)],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 23, result.stderr.decode()
    async with make_manager(path) as manager:
        request = await manager.send(
            sender_id="a",
            recipient_ids=("b", "c"),
            content="persisted",
            kind=MessageKind.REQUEST,
            idempotency_key="crash",
        )
        deliveries = await manager.get_deliveries(request.message_id)
        assert [d.state for d in deliveries] == [DeliveryState.INTERRUPTED, DeliveryState.PENDING]
        c, _ = make_agent("c", ["recovered"])
        manager.bind("c", c.receive)
        await manager.dispatch_pending()
        (reply,) = await manager.get_replies(request.message_id)
        assert reply.sender_id == "c"


async def test_v1_schema_upgrade_preserves_messages_and_pending_deliveries(tmp_path):
    """旧消息 schema 原位升级，ID、正文和待收记录保持不变。"""
    path = tmp_path / "messages.sqlite"
    async with make_manager(path) as manager:
        request = await send_request(manager)
        original = await manager.get_deliveries(request.message_id)
    async with aiosqlite.connect(path) as db:
        await db.execute("ALTER TABLE mm_messages DROP COLUMN origin_node_id")
        await db.execute("ALTER TABLE mm_deliveries DROP COLUMN consumed_node_id")
        await db.execute("ALTER TABLE mm_deliveries DROP COLUMN consumed_session_id")
        await db.execute("UPDATE mm_meta SET version=1")
        await db.commit()
    async with make_manager(path) as manager:
        assert await manager.get_message(request.message_id) == request
        assert await manager.get_deliveries(request.message_id) == original
        b, _ = make_agent("b", ["after migration"])
        manager.bind("b", b.receive)
        await manager.dispatch_pending()
        assert len(await manager.get_replies(request.message_id)) == 1


async def test_failed_predecessor_releases_same_session_queue(tmp_path):
    """前序失败释放顺序门，保留错误且不生成回复。"""

    async def receive(message):
        if message.content == "first":
            return AgentMessage.create_reply(message, "missing ability", MessageType.ERROR)
        return AgentMessage.create_reply(message, "later reply")

    async with make_manager(tmp_path / "messages.sqlite") as manager:
        manager.bind("b", receive)
        first = await manager.send(
            sender_id="a",
            recipient_ids=("b",),
            content="first",
            idempotency_key="first",
            kind=MessageKind.REQUEST,
        )
        await manager.dispatch_pending()
        (prior,) = await manager.get_deliveries(first.message_id)
        assert (prior.state, prior.error) == (DeliveryState.FAILED, "missing ability")
        assert await manager.get_replies(first.message_id) == ()
        later = await manager.send(
            sender_id="a",
            recipient_ids=("b",),
            content="later",
            idempotency_key="later",
            kind=MessageKind.REQUEST,
        )
        await manager.dispatch_pending()
        (completed,) = await manager.get_deliveries(later.message_id)
        assert (completed.state, completed.attempts) == (DeliveryState.COMPLETED, 1)
        (reply,) = await manager.wait_replies(later.message_id, timeout=1)
        assert reply.content == "later reply"
        assert await manager.get_queue_blocker(completed.delivery_id) is None
        assert await manager.get_deliveries(first.message_id) == (prior,)
        assert await manager.get_replies(first.message_id) == ()


async def test_failed_predecessor_is_not_a_blocker_for_offline_recipient(tmp_path):
    """后续投递在收件人离线时仍待处理，前序失败不影响阻塞诊断。"""

    async def fail(message):
        return AgentMessage.create_reply(message, "missing ability", MessageType.ERROR)

    async with make_manager(tmp_path / "messages.sqlite") as manager:
        manager.bind("b", fail)
        first = await send_request(manager)
        await manager.dispatch_pending()
        assert (await manager.get_deliveries(first.message_id))[0].state == DeliveryState.FAILED
        await manager.unbind("b")
        later = await send_request(manager, key="later")
        pending = (await manager.get_deliveries(later.message_id))[0]
        assert (pending.recipient_id, pending.state, pending.attempts) == (
            "b",
            DeliveryState.PENDING,
            0,
        )
        assert await manager.get_queue_blocker(pending.delivery_id) is None


async def test_inflight_predecessor_blocks_same_session(tmp_path):
    """执行中的前序维持同会话 FIFO，完成后下一条只执行一次。"""
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def receive(message):
        calls.append(message.content)
        if message.content == "first":
            entered.set()
            await release.wait()
        return AgentMessage.create_reply(message, f"{message.content} reply")

    async with make_manager(tmp_path / "messages.sqlite") as manager:
        manager.bind("b", receive)
        first = await manager.send(
            sender_id="a",
            recipient_ids=("b",),
            content="first",
            idempotency_key="first",
            kind=MessageKind.REQUEST,
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        (prior,) = await manager.get_deliveries(first.message_id)
        assert prior.state == DeliveryState.PROCESSING
        later = await manager.send(
            sender_id="a",
            recipient_ids=("b",),
            content="later",
            idempotency_key="later",
            kind=MessageKind.REQUEST,
        )
        (pending,) = await manager.get_deliveries(later.message_id)
        assert (pending.state, pending.attempts) == (DeliveryState.PENDING, 0)
        assert await manager.get_queue_blocker(pending.delivery_id) == prior
        with pytest.raises(RuntimeError, match="message_queue_blocked") as caught:
            await manager.wait_replies(later.message_id, timeout=1)
        assert prior.delivery_id in str(caught.value)
        assert "Wait for the blocking delivery to finish" in str(caught.value)
        assert await manager.get_deliveries(later.message_id) == (pending,)

        release.set()
        await manager.dispatch_pending()
        assert calls == ["first", "later"]
        (first_reply,) = await manager.get_replies(first.message_id)
        (later_reply,) = await manager.get_replies(later.message_id)
        assert (first_reply.content, later_reply.content) == ("first reply", "later reply")
        for request in (first, later):
            (delivery,) = await manager.get_deliveries(request.message_id)
            assert (delivery.state, delivery.attempts) == (DeliveryState.COMPLETED, 1)


async def test_interrupted_predecessor_releases_after_reopen(tmp_path):
    """重开数据库后，后续请求直接执行，已中断投递仍可单独处理。"""
    path = tmp_path / "messages.sqlite"
    entered = asyncio.Event()

    async def hang(message):
        entered.set()
        await asyncio.Event().wait()

    async with make_manager(path) as manager:
        manager.bind("b", hang)
        first = await manager.send(
            sender_id="a",
            recipient_ids=("b",),
            content="interrupted",
            idempotency_key="first",
            kind=MessageKind.REQUEST,
        )
        await asyncio.wait_for(entered.wait(), timeout=1)

    async with make_manager(path) as manager:
        (prior,) = await manager.get_deliveries(first.message_id)
        assert (prior.state, prior.attempts) == (DeliveryState.INTERRUPTED, 1)
        b, llm = make_agent("b", ["after reopen"])
        manager.bind("b", b.receive)
        later = await manager.send(
            sender_id="a",
            recipient_ids=("b",),
            content="later",
            idempotency_key="later",
            kind=MessageKind.REQUEST,
        )
        await manager.dispatch_pending()
        (completed,) = await manager.get_deliveries(later.message_id)
        assert (completed.state, completed.attempts) == (DeliveryState.COMPLETED, 1)
        (reply,) = await manager.get_replies(later.message_id)
        assert reply.content == "after reopen"
        assert len(llm.calls) == 1
        assert await manager.get_deliveries(first.message_id) == (prior,)
        assert await manager.get_replies(first.message_id) == ()


@pytest.mark.parametrize("turn_id", [None, "missing"])
async def test_stranded_callback_delivery_recovers_without_context_schema(tmp_path, turn_id):
    """纯回调模式可以恢复无主投递，后续消息仍可正常领取。"""
    manager = make_manager(tmp_path / "messages.sqlite")
    await manager.open(start_dispatcher=False)
    try:
        first = await send_request(manager, "stranded")
        later = await send_request(manager, "later")
        deliveries = await manager.get_deliveries(first.message_id)
        prior = next(item for item in deliveries if item.recipient_id == "b")
        async with manager._sqlite.transaction() as db:
            await db.execute(
                "UPDATE mm_deliveries SET state='processing', turn_id=?, attempts=1 "
                "WHERE delivery_id=?",
                (turn_id, prior.delivery_id),
            )

        async def receive(message):
            assert message.id == later.message_id
            return AgentMessage.create_reply(message, "next", MessageType.CHAT)

        manager.bind("b", receive)
        await manager.dispatch_pending()
        deliveries = await manager.get_deliveries(first.message_id)
        stopped = next(item for item in deliveries if item.delivery_id == prior.delivery_id)
        assert stopped.state == DeliveryState.INTERRUPTED
        assert stopped.error == ("worker_lost" if turn_id is None else "turn_missing")
        assert (stopped.attempts, stopped.turn_id) == (1, turn_id)
        assert (await manager.get_replies(later.message_id))[0].content == "next"
    finally:
        await manager.close()


@pytest.mark.parametrize("source_version", [6, 7])
async def test_reopen_rebuilds_message_indexes(tmp_path, source_version):
    """补建 v6 和现有 v7 库的索引，保留待投递记录及投递策略约束。"""
    path = tmp_path / "messages.sqlite"
    async with make_manager(path) as manager:
        request = await send_request(manager)
        original_deliveries = await manager.get_deliveries(request.message_id)

    indexes = {
        "mm_bound_inflight",
        "mm_delivery_candidates",
        "mm_delivery_unfinished",
        "mm_inline_waits_waiting",
        "mm_room_projection_pending",
        "mm_session_order",
        "mm_unbound_processing",
    }
    if source_version == 6:
        indexes.add("idx_deliveries_turn")
    async with aiosqlite.connect(path) as db:
        for index in sorted(indexes):
            await db.execute(f"DROP INDEX IF EXISTS {index}")
        if source_version == 6:
            for column in ("turn_id", "delivery_policy", "parked", "parked_reason"):
                await db.execute(f"ALTER TABLE mm_deliveries DROP COLUMN {column}")
        await db.execute("UPDATE mm_meta SET version=?", (source_version,))
        await db.commit()

    for _ in range(2):
        async with make_manager(path) as manager:
            assert await manager.get_message(request.message_id) == request
            assert await manager.get_deliveries(request.message_id) == original_deliveries
        async with aiosqlite.connect(path) as db:
            schema_indexes = await db.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
            assert indexes <= {row[0] for row in schema_indexes}
            assert await (await db.execute("SELECT version FROM mm_meta")).fetchone() == (7,)
            deliveries = await db.execute_fetchall(
                "SELECT state, turn_id, delivery_policy, parked, parked_reason FROM mm_deliveries"
            )
            assert deliveries == [("pending", None, "queue", 0, None)] * 2
            plan = await db.execute_fetchall(
                "EXPLAIN QUERY PLAN " + SqliteMessageStore._next_delivery_sql(with_turns=False),
                ("b",),
            )
            searches = [row[3] for row in plan if row[3].startswith("SEARCH ")]
            for index in ("mm_delivery_candidates", "mm_session_order"):
                assert any(f"INDEX {index} " in detail for detail in searches), plan
            with pytest.raises(aiosqlite.IntegrityError):
                await db.execute("UPDATE mm_deliveries SET delivery_policy='unknown'")


def test_scheduler_holds_no_database_access():
    """调度器只经存储域协议读写，不直接持有连接或 SQL。"""
    import inspect

    from ghrah.communication import message_manager as module

    source = inspect.getsource(module)
    for marker in ("import aiosqlite", ".execute(", "SELECT ", "UPDATE ", "INSERT "):
        assert marker not in source, f"MessageManager must not contain {marker!r}"
    for private in ("require_connection(", "._read_message(", ".delivery_record("):
        assert private not in source, (
            f"MessageManager must not reach into the store via {private!r}"
        )
