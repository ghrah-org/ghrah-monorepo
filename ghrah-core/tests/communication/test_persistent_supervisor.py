# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Supervisor 和真实发送工具的持久链路、回滚与生命周期验收。"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest
from _helpers import ScriptedLLM

from ghrah.abilities.builtin.broadcast_message import BroadcastMessageAbility
from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.abilities.builtin.send_message import SendMessageAbility
from ghrah.chat.content import TextBlock, ToolCallBlock
from ghrah.chat.format import LLMResponse
from ghrah.communication import DeliveryState, MessageKind, MessageManager, SupervisorActor
from ghrah.communication.errors import RegistryError
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.config import AgentConfig
from ghrah.core.message import AgentMessage


class SenderLLM(ScriptedLLM):
    """首轮发信，此后文本收束；持久回复触发新回合也不会再次发信。"""

    def __init__(self, broadcast=False):
        super().__init__([])
        self.broadcast = broadcast

    async def generate(self, messages, tools=None):
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            name = "broadcast_message" if self.broadcast else "send_message"
            args = {"content": "work"}
            if not self.broadcast:
                args["target"] = "display-b"
            return LLMResponse(
                content_blocks=[ToolCallBlock(id="send-1", name=name, arguments=args)]
            )
        return LLMResponse(content_blocks=[TextBlock(text="done")])


class GateManager(MessageManager):
    """暂停发送轮提交，观察准备与持久受理的边界。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prepared = asyncio.Event()
        self.release = asyncio.Event()
        self.draft = None

    async def _apply_atomic(self, scope, changes):
        if scope.drafts:
            self.draft = scope.drafts[0]
            self.prepared.set()
            await self.release.wait()
        await super()._apply_atomic(scope, changes)


async def bootstrap(path):
    async with SqliteBackend(path) as backend:
        await backend.load_checkpoint("bootstrap")


async def assemble(mm, sender_llm):
    supervisor = SupervisorActor(cluster_id="cluster", message_manager=mm)
    await supervisor.spawn_agent(
        AgentConfig(name="controller"),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: ScriptedLLM([]),
    )
    # 保留来源 session，但保持离线，不执行启动请求的回复。
    await supervisor.terminate_agent("controller")
    b_llm = ScriptedLLM(["b reply"])
    await supervisor.spawn_agent(
        AgentConfig(name="display-b", agent_id="b"),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: b_llm,
    )
    await supervisor.spawn_agent(
        AgentConfig(name="display-c", agent_id="c"),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: ScriptedLLM(["c reply"]),
    )
    await supervisor.spawn_agent(
        AgentConfig(name="display-a", agent_id="a"),
        abilities=[SendMessageAbility(), BroadcastMessageAbility(), ConversationAbility()],
        llm_factory=lambda config: sender_llm,
    )
    return supervisor, b_llm


async def start(mm):
    return await mm.send(
        sender_id="controller",
        recipient_ids=("a",),
        content="start",
        kind=MessageKind.REQUEST,
        idempotency_key="start",
    )


@pytest.mark.parametrize("broadcast", [False, True])
async def test_tools_commit_before_delivery_and_reply_through_mailbox(tmp_path, broadcast):
    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    sender_llm = SenderLLM(broadcast)
    async with GateManager(
        path, project_id="p", agent_ids=("controller", "a", "b", "c"), session_routing=True
    ) as mm:
        supervisor, b_llm = await assemble(mm, sender_llm)
        try:
            root = await start(mm)
            await asyncio.wait_for(mm.prepared.wait(), 2)
            with pytest.raises(KeyError):
                await mm.get_message(mm.draft.message_id)
            assert b_llm.calls == []
            mm.release.set()
            await mm.dispatch_pending()
            outbound = await mm.get_message(mm.draft.message_id)
            assert outbound.sender_id == "a"
            assert outbound.recipient_ids == (("b", "c") if broadcast else ("b",))
            assert outbound.origin_node_id is not None
            assert outbound.sender_session_id is not None
            assert all(
                d.state == DeliveryState.COMPLETED
                for d in await mm.get_deliveries(outbound.message_id)
            )
            replies = await mm.get_replies(outbound.message_id)
            assert {r.content for r in replies} == (
                {"b reply", "c reply"} if broadcast else {"b reply"}
            )
            for reply in replies:
                assert (await mm.get_deliveries(reply.message_id))[
                    0
                ].state == DeliveryState.COMPLETED
            assert any("b reply" in str(m) for call in sender_llm.calls for m in call)
            assert (await mm.get_deliveries(root.message_id))[0].state == DeliveryState.COMPLETED
            source = await supervisor.get_agent_handle("display-a")
            checkpoint = await source._context_manager.persistence.load_checkpoint("a")
            node = next(node for node in checkpoint.nodes if node.id == outbound.origin_node_id)
            assert node.action_results[0]["ability_name"] in ("send_message", "broadcast_message")
            assert node.action_results[0]["action_result"].data["status"] == "prepared"
        finally:
            mm.release.set()
            await supervisor.shutdown()


async def test_failed_source_commit_does_not_send(tmp_path):
    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    async with GateManager(
        path, project_id="p", agent_ids=("controller", "a", "b", "c"), session_routing=True
    ) as mm:
        supervisor, b_llm = await assemble(mm, SenderLLM())
        try:
            source = await supervisor.get_agent_handle("display-a")
            head = source._context_manager.active_head.id
            root = await start(mm)
            await asyncio.wait_for(mm.prepared.wait(), 2)
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    "CREATE TRIGGER fail_outbound BEFORE INSERT ON mm_messages "
                    "WHEN NEW.sender_id='a' AND NEW.kind='request' "
                    "BEGIN SELECT RAISE(ABORT, 'outbound write failed'); END"
                )
                await db.commit()
            mm.release.set()
            await mm.dispatch_pending()
            assert b_llm.calls == []
            assert source._context_manager.active_head.id == head
            with pytest.raises(KeyError):
                await mm.get_message(mm.draft.message_id)
            (failed,) = await mm.get_deliveries(root.message_id)
            assert failed.state == DeliveryState.FAILED
            assert failed.consumed_node_id is None
        finally:
            mm.release.set()
            await supervisor.shutdown()


async def test_terminated_recipient_is_offline_and_respawn_restores_pending(tmp_path):
    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    async with GateManager(
        path, project_id="p", agent_ids=("controller", "a", "b", "c"), session_routing=True
    ) as mm:
        supervisor, old_llm = await assemble(mm, SenderLLM())
        try:
            root = await start(mm)
            await asyncio.wait_for(mm.prepared.wait(), 2)
            await supervisor.terminate_agent("display-b")
            mm.release.set()
            await mm.dispatch_pending()
            (waiting,) = await mm.get_deliveries(mm.draft.message_id)
            assert waiting.state == DeliveryState.PENDING
            assert old_llm.calls == []
            new_llm = ScriptedLLM(["b after restart"])
            await supervisor.spawn_agent(
                AgentConfig(name="new-b", agent_id="b"),
                abilities=[ConversationAbility()],
                llm_factory=lambda config: new_llm,
            )
            assert supervisor.recovery_mode("new-b") == "restored"
            await mm.dispatch_pending()
            assert (await mm.get_deliveries(mm.draft.message_id))[
                0
            ].state == DeliveryState.COMPLETED
            assert (await mm.get_deliveries(root.message_id))[0].state == DeliveryState.COMPLETED
            assert len(new_llm.calls) == 1
        finally:
            mm.release.set()
            await supervisor.shutdown()


async def test_no_router_fallback_for_agents():
    supervisor = SupervisorActor(cluster_id="cluster")
    await supervisor.spawn_agent(
        AgentConfig(name="a"),
        abilities=[ConversationAbility()],
        persistence_factory=lambda config: None,
    )
    try:
        with pytest.raises(RuntimeError, match="MessageManager"):
            await supervisor.prepare_message(
                targets=("b",), content="work", sender="a", idempotency_key="work"
            )
        with pytest.raises(RuntimeError, match="removed"):
            await supervisor.send("a", "work", sender="a")
        with pytest.raises(RuntimeError, match="removed"):
            await supervisor.route_message(AgentMessage(sender="a", recipient="a", content="work"))
        with pytest.raises(RuntimeError, match="removed"):
            await supervisor.broadcast("work", sender="a")
        with pytest.raises(RuntimeError, match="removed"):
            await supervisor.delegate("a", "a", "work")
    finally:
        await supervisor.shutdown()


async def test_supervisor_requires_scoped_manager_and_declared_identity(tmp_path):
    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    with pytest.raises(ValueError, match="session_routing"):
        SupervisorActor(message_manager=MessageManager(path, project_id="p", agent_ids=("a",)))
    async with MessageManager(path, project_id="p", agent_ids=("a",), session_routing=True) as mm:
        supervisor = SupervisorActor(cluster_id="cluster", message_manager=mm)
        with pytest.raises(RegistryError, match="not declared"):
            await supervisor.spawn_agent(
                AgentConfig(name="unknown"), abilities=[ConversationAbility()]
            )
        assert await supervisor.list_agents() == []


async def test_wrong_backend_is_rejected_before_registering_or_binding(tmp_path):
    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    async with MessageManager(path, project_id="p", agent_ids=("a",), session_routing=True) as mm:
        supervisor = SupervisorActor(cluster_id="cluster", message_manager=mm)
        with pytest.raises(RegistryError, match="same-file"):
            await supervisor.spawn_agent(
                AgentConfig(name="a"),
                abilities=[ConversationAbility()],
                persistence_factory=lambda config: SqliteBackend(tmp_path / "wrong.sqlite"),
            )
        assert await supervisor.list_agents() == []
        assert not (tmp_path / "wrong.sqlite").exists()


async def test_room_send_fails_before_log_or_background_delivery():
    from ghrah.abilities.builtin.send import SendAbility
    from ghrah.abilities.context import AbilityExecutionContext

    class Bridge:
        def __init__(self):
            self.appended = False

        async def resolve_recipients(self, rooms, targets):
            return {"success": True, "data": {"recipients": ["a"]}}

        async def append_room_log(self, *args, **kwargs):
            self.appended = True
            raise AssertionError("Should reject before appending a false sent log")

    bridge = Bridge()
    supervisor = SupervisorActor(cluster_id="cluster", room_bridge=bridge)
    await supervisor.spawn_agent(
        AgentConfig(name="a"),
        abilities=[ConversationAbility()],
        persistence_factory=lambda config: None,
    )
    await supervisor.spawn_agent(
        AgentConfig(name="b"),
        abilities=[ConversationAbility()],
        persistence_factory=lambda config: None,
    )
    try:
        result = await SendAbility().execute(
            AbilityExecutionContext(
                supervisor=supervisor,
                agent_name="b",
                tool_args={"room_ids": ["room"], "message": "work"},
            )
        )
        assert "persistent action" in result.data["error"]
        assert not bridge.appended
    finally:
        await supervisor.shutdown()


async def test_two_tool_calls_in_one_action_have_independent_message_ids(tmp_path):
    class TwoCallsLLM(SenderLLM):
        async def generate(self, messages, tools=None):
            if not self.calls:
                self.calls.append(list(messages))
                return LLMResponse(
                    content_blocks=[
                        ToolCallBlock(
                            id=call_id,
                            name="send_message",
                            arguments={"target": "display-b", "content": "work"},
                        )
                        for call_id in ("send-1", "send-2")
                    ]
                )
            return await super().generate(messages, tools)

    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    async with GateManager(
        path, project_id="p", agent_ids=("controller", "a", "b", "c"), session_routing=True
    ) as mm:
        supervisor, b_llm = await assemble(mm, TwoCallsLLM())
        b_llm._replies.append("second b reply")
        try:
            await start(mm)
            await asyncio.wait_for(mm.prepared.wait(), 2)
            mm.release.set()
            await mm.dispatch_pending()
            async with aiosqlite.connect(path) as db:
                rows = await (
                    await db.execute(
                        "SELECT message_id, origin_node_id FROM mm_messages "
                        "WHERE sender_id='a' AND kind='request' ORDER BY seq"
                    )
                ).fetchall()
            assert len(rows) == 2
            assert rows[0][0] != rows[1][0]
            assert rows[0][1] == rows[1][1]
            assert len(b_llm.calls) == 2
            for message_id, _ in rows:
                assert all(
                    d.state == DeliveryState.COMPLETED for d in await mm.get_deliveries(message_id)
                )
        finally:
            mm.release.set()
            await supervisor.shutdown()
