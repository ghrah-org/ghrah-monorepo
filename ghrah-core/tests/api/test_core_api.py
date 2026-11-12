# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 端口打样测试：真链库读取、错误族、parity、语料。

这一组测试同时验证两件事：

1. **台子**：能力清单 ↔ 端口协议一一对应（parity），启动即校验处理器齐全；
2. **打样**：``nodes.get`` / ``sessions.*`` 能在一条真实的 ActionChain 上跑通。
"""

from __future__ import annotations

from typing import get_type_hints

import pytest
from ghrah.protocol.corpus import Corpus, diff_dicts
from ghrah.protocol.ports.core import CorePort
from ghrah.protocol.problem import ProblemCode
from ghrah.protocol.views.core import AgentSpawnSpec, NodeView

from ghrah.chat.factory import ChatMessageFactory
from ghrah.context import ContextManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.api import (
    CORE_CAPABILITIES,
    ChainReadStore,
    CoreApiError,
    build_handlers,
    build_runtime,
)
from ghrah.core.read.ability import AbilityReadStore
from ghrah.core.read.mailbox import MailboxReadStore
from ghrah.core.runtime.registry import RuntimeRegistry
from ghrah.core.unit import CoreUnitConfig
from ghrah.core.wiring import build_core_port

AGENT_ID = "agt-1"


async def _seed_chain(tmp_path) -> tuple[str, str, str]:
    """造一条真实链，返回 (session_id, branch_id, head_node_id)。"""
    backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-a")
    await backend.connect()
    factory = ChatMessageFactory()
    manager = ContextManager(
        agent_name=AGENT_ID,
        persistence=backend,
        auto_persist=True,
        message_factory=factory,
    )
    manager.begin_iteration()
    manager.add_messages([factory.create_message(role="user", text="hello")])
    manager.commit_iteration(ability_names=["step-1"])
    await manager.wait_for_persist()
    return (
        manager.active_session_id,
        manager.get_active_session().active_branch_id,
        manager.active_head.id,
    )


def _port(tmp_path):
    return build_core_port(
        resolve_db_path=lambda project_id: tmp_path / "context.db",
    ).project("proj-001")


def _port_at(tmp_path, filename: str):
    """指向指定库文件的端口（mailbox 测试用独立的 messages.sqlite）。"""
    return build_core_port(
        resolve_db_path=lambda project_id: tmp_path / filename,
    ).project("proj-001")


def _spawn_spec(name: str, manifest_ref: str, agent_id: str | None = None) -> AgentSpawnSpec:
    """构造一个最小 spawn 规格（测试用）。"""
    return AgentSpawnSpec(
        name=name,
        manifest_ref=manifest_ref,
        agent_id=agent_id or "",
    )


async def _make_manager(manager):
    """把同步替身包成可 await 的工厂返回值。"""
    return manager


async def _seed_mailbox(tmp_path, *, count: int = 3) -> str:
    """造一个真实消息库，返回文件名。"""
    from ghrah.communication.message_manager import MessageKind, MessageManager

    db = tmp_path / "messages.sqlite"
    manager = MessageManager(db, project_id="proj-001", agent_ids=["agt-1", "b", "c"])
    await manager.open()
    try:
        for index in range(count):
            await manager.send(
                sender_id="agt-1",
                recipient_ids=["b", "c"],
                content=f"item {index}",
                kind=MessageKind.REQUEST,
                idempotency_key=str(index),
            )
    finally:
        await manager.close()
    return "messages.sqlite"


# ─── parity：台子的可复用性 ───


def _port_surface() -> set[str]:
    """反射出 ``"<分组>.<方法>"`` 全集。"""
    surface: set[str] = set()
    for group_name, member in vars(CorePort).items():
        if not isinstance(member, property) or member.fget is None:
            continue
        group = get_type_hints(member.fget)["return"]
        for method_name in getattr(group, "__protocol_attrs__", ()):
            if not method_name.startswith("_"):
                surface.add(f"{group_name}.{method_name}")
    return surface


def test_port_matches_capability_registry() -> None:
    """端口方法与能力清单必须一一对应。"""
    assert _port_surface() == set(CORE_CAPABILITIES.names())


def test_startup_fails_when_handler_missing() -> None:
    """清单有而处理器没有 → 启动即失败。"""
    with pytest.raises(RuntimeError, match="capability/handler mismatch"):
        build_runtime({"nodes.get": lambda request: None})


def test_views_match_serializers_exactly() -> None:
    """视图字段必须与 Core 序列化输出**完全一致**（多一个少一个都算漂移）。

    ``extra="forbid"`` 只会挡住多出来的字段；少字段是静默丢数据，必须靠这条测试守。
    """
    from ghrah.protocol.views.core import BranchView, SessionView

    from ghrah.context.persistence import (
        serialize_action_session,
        serialize_branch,
        serialize_node,
    )
    from ghrah.context.session_runtime import SessionRuntime

    runtime = SessionRuntime.create(agent_name="agent-x")
    runtime.commit_node(ability_names=["step-1"], agent_state={"m": 1})

    pairs = (
        (NodeView, serialize_node(runtime.chain.nodes[0])),
        (SessionView, serialize_action_session(runtime.session)),
        (BranchView, serialize_branch(runtime.active_branch)),
    )
    for model, serialized in pairs:
        assert set(serialized) == set(model.model_fields), model.__name__


# ─── 打样：真链库读取 ───


async def test_nodes_get_reads_real_node(tmp_path) -> None:
    session_id, branch_id, node_id = await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        node = await port.nodes.get(agent_id=AGENT_ID, node_id=node_id)
        assert isinstance(node, NodeView)
        assert node.id == node_id
        assert node.session_id == session_id
        assert node.created_on_branch_id == branch_id
        assert node.agent_name == AGENT_ID
    finally:
        await port.aclose()


async def test_sessions_catalog_reads_real_chain(tmp_path) -> None:
    session_id, branch_id, _node_id = await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        catalog = await port.sessions.catalog(agent_id=AGENT_ID)
        assert catalog.active_session_id == session_id
        assert [item.session_id for item in catalog.sessions] == [session_id]
        assert [item.branch_id for item in catalog.branches] == [branch_id]
    finally:
        await port.aclose()


async def test_sessions_history_reads_real_chain(tmp_path) -> None:
    session_id, branch_id, node_id = await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        history = await port.sessions.history(
            agent_id=AGENT_ID,
            session_id=session_id,
            branch_id=branch_id,
        )
        assert history.session_id == session_id
        assert history.branch_id == branch_id
        assert node_id in [node.id for node in history.nodes]
    finally:
        await port.aclose()


async def test_port_returns_dto_not_internal_object(tmp_path) -> None:
    _session_id, _branch_id, node_id = await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        node = await port.nodes.get(agent_id=AGENT_ID, node_id=node_id)
        # 返回的必须是契约包里的 DTO，且不带任何 Core 内部类型的方法
        assert type(node).__module__ == "ghrah.protocol.views.core"
        assert not hasattr(node, "to_context_node")
    finally:
        await port.aclose()


async def test_sessions_catalog_is_empty_for_unknown_agent(tmp_path) -> None:
    """无链数据的 agent 返回空目录，**不是报错**（对齐面向用户的读法）。"""
    port = _port(tmp_path)
    try:
        catalog = await port.sessions.catalog(agent_id="never-existed")
        assert catalog.active_session_id is None
        assert catalog.sessions == []
        assert catalog.branches == []
    finally:
        await port.aclose()


async def test_sessions_catalog_hides_soft_deleted_sessions(tmp_path) -> None:
    """软删除的会话不出现在目录里（``lifecycle='deleted'``）。"""
    import sqlite3

    session_id, _branch_id, _node_id = await _seed_chain(tmp_path)
    # 先删再开读连接：只读连接会持有事务快照，先读再删会看不到更新。
    with sqlite3.connect(tmp_path / "context.db") as db:
        db.execute("UPDATE sessions SET lifecycle='deleted'")
        db.execute("UPDATE branches SET lifecycle='deleted'")

    port = _port(tmp_path)
    try:
        catalog = await port.sessions.catalog(agent_id=AGENT_ID)
        assert catalog.sessions == []
        assert catalog.branches == []
        assert session_id not in [item.session_id for item in catalog.sessions]
    finally:
        await port.aclose()


async def test_sessions_head_returns_branch_head(tmp_path) -> None:
    session_id, branch_id, node_id = await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        head = await port.sessions.head(
            agent_id=AGENT_ID,
            session_id=session_id,
            branch_id=branch_id,
        )
        assert head == node_id
    finally:
        await port.aclose()


async def test_sessions_meta_has_pointer_but_no_nodes(tmp_path) -> None:
    """meta 只带 active 指针与两张表，**不含节点载荷**。"""
    session_id, branch_id, _node_id = await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        meta = await port.sessions.meta(agent_id=AGENT_ID)
        assert meta.active_session_id == session_id
        assert [item.session_id for item in meta.sessions] == [session_id]
        assert [item.branch_id for item in meta.branches] == [branch_id]
        assert "nodes" not in meta.model_dump()
    finally:
        await port.aclose()


async def test_branches_list_returns_session_branches(tmp_path) -> None:
    session_id, branch_id, _node_id = await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        branches = await port.branches.list(agent_id=AGENT_ID, session_id=session_id)
        assert [item.branch_id for item in branches] == [branch_id]
    finally:
        await port.aclose()


async def test_branches_list_unknown_session_is_not_found(tmp_path) -> None:
    await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.branches.list(agent_id=AGENT_ID, session_id="absent")
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert excinfo.value.problem.capability == "branches.list"
    finally:
        await port.aclose()


async def test_sessions_head_unknown_branch_is_not_found(tmp_path) -> None:
    session_id, _branch_id, _node_id = await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.sessions.head(
                agent_id=AGENT_ID,
                session_id=session_id,
                branch_id="absent",
            )
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
    finally:
        await port.aclose()


# ─── mailbox ───


async def test_mailbox_summary_counts_outbox(tmp_path) -> None:
    filename = await _seed_mailbox(tmp_path, count=3)
    port = _port_at(tmp_path, filename)
    try:
        summary = await port.mailbox.summary(agent_id="agt-1")
        assert summary.availability == "available"
        assert summary.outbox_count == 3
    finally:
        await port.aclose()


async def test_mailbox_list_paginates_and_hides_other_recipients(tmp_path) -> None:
    filename = await _seed_mailbox(tmp_path, count=3)
    port = _port_at(tmp_path, filename)
    try:
        first = await port.mailbox.list(agent_id="b", inbox=True, limit=2)
        assert len(first.items) == 2
        assert first.has_more is True
        second = await port.mailbox.list(
            agent_id="b", inbox=True, limit=2, cursor=first.next_cursor
        )
        assert len(second.items) == 1
        assert second.has_more is False
        # 收件人只看得到发给自己的投递
        for item in [*first.items, *second.items]:
            assert {d.recipient_id for d in item.deliveries} == {"b"}
    finally:
        await port.aclose()


async def test_mailbox_get_returns_visible_message(tmp_path) -> None:
    filename = await _seed_mailbox(tmp_path, count=1)
    port = _port_at(tmp_path, filename)
    try:
        page = await port.mailbox.list(agent_id="b", inbox=True)
        message_id = page.items[0].message_id
        view = await port.mailbox.get(agent_id="b", message_id=message_id)
        assert view.message_id == message_id
        assert view.content == "item 0"
        assert view.project_id == "proj-001"
        assert [d.recipient_id for d in view.deliveries] == ["b"]
    finally:
        await port.aclose()


async def test_mailbox_get_unknown_message_is_not_found(tmp_path) -> None:
    filename = await _seed_mailbox(tmp_path, count=1)
    port = _port_at(tmp_path, filename)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.mailbox.get(agent_id="b", message_id="absent")
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert excinfo.value.problem.capability == "mailbox.get"
    finally:
        await port.aclose()


async def test_mailbox_missing_database_is_empty_not_error(tmp_path) -> None:
    """消息库不存在 = 正常态（项目还没产生消息）：summary 返空、list 返空页。"""
    port = _port_at(tmp_path, "absent.sqlite")
    try:
        summary = await port.mailbox.summary(agent_id="agt-1")
        assert summary.availability == "empty"
        page = await port.mailbox.list(agent_id="agt-1")
        assert page.items == []
        assert page.has_more is False
    finally:
        await port.aclose()


# ─── deliveries ───


async def test_deliveries_get_returns_own_delivery(tmp_path) -> None:
    filename = await _seed_mailbox(tmp_path, count=1)
    port = _port_at(tmp_path, filename)
    try:
        page = await port.mailbox.list(agent_id="b", inbox=True)
        delivery_id = page.items[0].deliveries[0].delivery_id
        view = await port.deliveries.get(agent_id="b", delivery_id=delivery_id)
        assert view.delivery_id == delivery_id
        assert view.recipient_id == "b"
        assert view.state == "pending"
    finally:
        await port.aclose()


async def test_deliveries_get_is_scoped_to_entity(tmp_path) -> None:
    """收件人不能读取别人的投递。"""
    filename = await _seed_mailbox(tmp_path, count=1)
    port = _port_at(tmp_path, filename)
    try:
        page = await port.mailbox.list(agent_id="b", inbox=True)
        delivery_id = page.items[0].deliveries[0].delivery_id
        with pytest.raises(CoreApiError) as excinfo:
            await port.deliveries.get(agent_id="c", delivery_id=delivery_id)
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert excinfo.value.problem.capability == "deliveries.get"
    finally:
        await port.aclose()


async def _seed_abilities(tmp_path, *, agent_id: str = AGENT_ID) -> str:
    """造一个能力关系库并注册一个 agent，返回文件名。"""
    from ghrah.ability.access import AbilityAccessStore, AccessIdentity

    db = tmp_path / "context.db"
    operator = AccessIdentity("proj-001", "deployment", None, operator=True, source="admin")
    store = await AbilityAccessStore.open(
        db,
        "proj-001",
        operators=frozenset({"deployment"}),
        runs=lambda _agent: "run-seed",
    )
    try:
        await store.register_agent(operator, agent_id)
    finally:
        await store.close()
    return "context.db"


# ─── abilities ───


async def test_abilities_changes_is_empty_for_fresh_store(tmp_path) -> None:
    filename = await _seed_abilities(tmp_path)
    port = _port_at(tmp_path, filename)
    try:
        page = await port.abilities.changes(after_seq=0, limit=50)
        # 全新关系库：注册本身不写变更流，因此是合法空页
        assert page.changes == []
        assert page.next_after_seq == 0
        assert page.has_more is False
    finally:
        await port.aclose()


# ─── agents ───


class _FakeUnit:
    """记录命令的单元替身，用于验证 Core 侧的协议转换。"""

    def __init__(self) -> None:
        self.commands: list[tuple[str, dict]] = []
        self.started = False
        self.stopped = False
        self.reject_spawn = False

    async def init(self, _ctx) -> None:
        return None

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def handle_command(self, command, payload, _ctx):
        self.commands.append((command, payload))
        if command == "spawn_agent":
            if self.reject_spawn:
                return {"success": False, "error": "manifest_not_found"}
            return {"success": True, "data": {"recovery_mode": "fresh"}}
        if command == "terminate_agent":
            return {"success": True}
        if command == "list_agents":
            return {
                "success": True,
                "data": {
                    "agents": [
                        {"name": "alpha", "agent_id": "fixed-id"},
                        {"name": "beta", "agent_id": "other-id"},
                    ]
                },
            }
        if command == "get_agent_info":
            return {
                "success": True,
                "data": {
                    "name": payload.get("name"),
                    "state": "idle",
                    "abilities": ["send_message"],
                },
            }
        if command == "session_create":
            return {"success": True, "data": {"session_id": "sess-1"}}
        if command == "session_activate":
            return {"success": True}
        if command == "branch_create":
            return {"success": True, "data": {"branch_id": "br-1"}}
        if command == "branch_activate":
            return {"success": True}
        if command == "agent_compact_context":
            return {"success": True, "data": {"status": "done"}}
        if command == "agent_reset":
            return {"success": True}
        if command == "send_message":
            return {"success": True, "data": {"message_id": "msg-1"}}
        if command == "broadcast_message":
            return {"success": True, "data": {"message_ids": ["msg-2", "msg-3"]}}
        if command == "delegate":
            return {"success": True, "data": {"delivery_id": "dlv-1"}}
        return {"success": False, "error": "unknown_command"}

    def get_entity_runtime(self, project_id, agent_id):
        return {
            "availability": "online",
            "incarnation_id": "inc-1",
            "busy": False,
            "loaded_abilities": ["send_message"],
        }


def _agent_port(tmp_path, unit: _FakeUnit):
    return build_core_port(
        resolve_db_path=lambda project_id: tmp_path / "context.db",
        unit_factory=lambda _config: unit,
    ).project("proj-001")


async def test_agents_create_spawns_into_core_unit(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        agent_id = await port.agents.create(spec=_spawn_spec("alpha", "agents.coder"))
        assert agent_id
        assert unit.started is True
        command, payload = unit.commands[0]
        assert command == "spawn_agent"
        assert payload["config"]["agent_id"] == agent_id
        assert payload["manifest_ref"] == "agents.coder"
    finally:
        await port.aclose()


async def test_agents_create_honours_explicit_agent_id(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        agent_id = await port.agents.create(spec=_spawn_spec("alpha", "agents.coder", "fixed-id"))
        assert agent_id == "fixed-id"
    finally:
        await port.aclose()


async def test_agents_create_surfaces_rejection(tmp_path) -> None:
    unit = _FakeUnit()
    unit.reject_spawn = True
    port = _agent_port(tmp_path, unit)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.agents.create(spec=_spawn_spec("alpha", "agents.coder"))
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert excinfo.value.problem.capability == "agents.create"
    finally:
        await port.aclose()


async def test_agents_runtime_reads_unit_state(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        # 单元未启动时不存在在线实例；先 create 再查
        await port.agents.create(spec=_spawn_spec("alpha", "agents.coder", "fixed-id"))
        view = await port.agents.runtime(agent_id="fixed-id")
        assert view.availability == "online"
        assert view.incarnation_id == "inc-1"
        assert view.loaded_abilities == ["send_message"]
    finally:
        await port.aclose()


async def test_agents_delete_terminates(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        await port.agents.delete(agent_id="fixed-id")
        assert [c for c, _p in unit.commands] == ["terminate_agent"]
    finally:
        await port.aclose()


async def test_agents_list_returns_online_ids(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        await port.agents.create(spec=_spawn_spec("alpha", "agents.coder", "fixed-id"))
        page = await port.agents.list()
        assert page.items == ["fixed-id", "other-id"]
    finally:
        await port.aclose()


async def test_agents_get_merges_list_and_info(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        await port.agents.create(spec=_spawn_spec("alpha", "agents.coder", "fixed-id"))
        view = await port.agents.get(agent_id="fixed-id")
        assert view.agent_id == "fixed-id"
        assert view.name == "alpha"
        assert view.state == "idle"
        assert view.abilities == ["send_message"]
    finally:
        await port.aclose()


async def test_agents_get_offline_agent_is_not_found(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.agents.get(agent_id="ghost")
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert excinfo.value.problem.capability == "agents.get"
    finally:
        await port.aclose()


async def test_agents_tools_lists_loaded_abilities(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        await port.agents.create(spec=_spawn_spec("alpha", "agents.coder", "fixed-id"))
        tools = await port.agents.tools(agent_id="fixed-id")
        assert [t.name for t in tools] == ["send_message"]
    finally:
        await port.aclose()


async def test_sessions_create_returns_unit_session_id(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        await port.agents.create(spec=_spawn_spec("alpha", "agents.coder", "fixed-id"))
        session_id = await port.sessions.create(agent_id="fixed-id", name="第一会话")
        assert session_id == "sess-1"
        command, payload = unit.commands[-1]
        assert command == "session_create"
        assert payload["agent_name"] == "alpha"
        assert payload["name"] == "第一会话"
    finally:
        await port.aclose()


async def test_branches_create_returns_branch_id(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        await port.agents.create(spec=_spawn_spec("alpha", "agents.coder", "fixed-id"))
        branch_id = await port.branches.create(
            agent_id="fixed-id", session_id="sess-1", name="尝试"
        )
        assert branch_id == "br-1"
    finally:
        await port.aclose()


async def test_sessions_write_requires_online_agent(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.sessions.activate(agent_id="ghost", session_id="sess-1")
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
    finally:
        await port.aclose()


async def test_agents_reset_is_synchronous_ack(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        await port.agents.reset(agent_id="fixed-id")
        assert unit.commands[-1][0] == "agent_reset"
    finally:
        await port.aclose()


async def test_messages_append_uses_resolved_sender_name(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        await port.agents.create(spec=_spawn_spec("alpha", "agents.coder", "fixed-id"))
        message_id = await port.messages.append(agent_id="fixed-id", target="beta", content="hi")
        assert message_id == "msg-1"
        command, payload = unit.commands[-1]
        assert command == "send_message"
        assert payload["sender"] == "alpha"
        assert payload["target"] == "beta"
    finally:
        await port.aclose()


async def test_messages_broadcast_returns_ids(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        ids = await port.messages.broadcast(content="hello")
        assert ids == ["msg-2", "msg-3"]
    finally:
        await port.aclose()


async def test_messages_delegate_resolves_both_names(tmp_path) -> None:
    unit = _FakeUnit()
    port = _agent_port(tmp_path, unit)
    try:
        delivery_id = await port.messages.delegate(
            from_agent_id="fixed-id", to_agent_id="other-id", content="task"
        )
        assert delivery_id == "dlv-1"
        command, payload = unit.commands[-1]
        assert command == "delegate"
        assert (payload["from_agent"], payload["to_agent"]) == ("alpha", "beta")
    finally:
        await port.aclose()


async def test_abilities_tree_requires_online_agent(tmp_path) -> None:
    """能力树含运行时装配状态：单元未启动时拒绝。"""
    filename = await _seed_abilities(tmp_path)
    port = _port_at(tmp_path, filename)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.abilities.tree(agent_id=AGENT_ID)
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert excinfo.value.problem.capability == "abilities.tree"
    finally:
        await port.aclose()


async def test_abilities_status_reports_core_surface(tmp_path) -> None:
    port = _port_at(tmp_path, "context.db")
    try:
        status = await port.abilities.status()
        assert status.ability_source == "builtin"
        assert status.features.tree is True
        assert status.features.grants_write is True
        assert status.features.versions_write is False
    finally:
        await port.aclose()


async def test_abilities_grant_unknown_instance_is_not_found(tmp_path) -> None:
    """授予一个不存在的实例：写路径的错误族必须稳定。"""
    from ghrah.protocol.ability_tree import GrantAbilityRequest

    filename = await _seed_abilities(tmp_path)
    port = _port_at(tmp_path, filename)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.abilities.grant(
                agent_id=AGENT_ID,
                change=GrantAbilityRequest(
                    alias="fs",
                    instance_id="fs",
                    expected_revision=0,
                ),
            )
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert excinfo.value.problem.capability == "abilities.grant"
    finally:
        await port.aclose()


async def test_abilities_grant_unknown_agent_is_not_found(tmp_path) -> None:
    """对不存在的 Agent 授权：同样是 NOT_FOUND。"""
    from ghrah.protocol.ability_tree import GrantAbilityRequest

    filename = await _seed_abilities(tmp_path)
    port = _port_at(tmp_path, filename)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.abilities.grant(
                agent_id="ghost",
                change=GrantAbilityRequest(
                    alias="fs",
                    instance_id="fs",
                    expected_revision=0,
                ),
            )
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
    finally:
        await port.aclose()


async def test_abilities_admin_reports_unassembled_for_unknown_agent(tmp_path) -> None:
    """未登记的关系：state=unassembled，不抛错。"""
    filename = await _seed_abilities(tmp_path)
    port = _port_at(tmp_path, filename)
    try:
        view = await port.abilities.admin(agent_id="ghost")
        assert view.agent_id == "ghost"
        assert view.lifecycle.state == "unassembled"
        assert view.grants == []
    finally:
        await port.aclose()


async def test_abilities_admin_reports_stopped_without_runtime(tmp_path) -> None:
    """已登记但单元未启动：state=stopped，且可见面为空。"""
    filename = await _seed_abilities(tmp_path)
    port = _port_at(tmp_path, filename)
    try:
        view = await port.abilities.admin(agent_id=AGENT_ID)
        assert view.lifecycle.state == "stopped"
        assert view.revision == 0
        assert view.surface.effective == []
        assert view.run_id is None
    finally:
        await port.aclose()


async def test_abilities_content_ungranted_alias_is_not_found(tmp_path) -> None:
    filename = await _seed_abilities(tmp_path)
    port = _port_at(tmp_path, filename)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.abilities.content(agent_id=AGENT_ID, alias="fs", member="ops/read.py")
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert excinfo.value.problem.capability == "abilities.content"
    finally:
        await port.aclose()


async def test_deliveries_retry_delegates_to_manager(tmp_path) -> None:
    """重试要先过可见性校验，再交给消息管理器。"""
    filename = await _seed_mailbox(tmp_path, count=1)
    calls: list[tuple[str, str]] = []

    class _Manager:
        async def retry(self, delivery_id):
            calls.append(("retry", delivery_id))

        async def resume_delivery(self, delivery_id):
            calls.append(("resume", delivery_id))

        async def recover_room_projections(self):
            calls.append(("recover", ""))
            return 3

        async def close(self):
            return None

    manager = _Manager()
    port = build_core_port(
        resolve_db_path=lambda project_id: tmp_path / filename,
        manager_factory=lambda project_id: _make_manager(manager),
        unit_factory=lambda _config: _FakeUnit(),
    ).project("proj-001")
    try:
        page = await port.mailbox.list(agent_id="b", inbox=True)
        delivery_id = page.items[0].deliveries[0].delivery_id
        await port.deliveries.retry(agent_id="b", delivery_id=delivery_id)
        await port.deliveries.resume(agent_id="b", delivery_id=delivery_id)
        await port.rooms.recover()
        assert calls == [("retry", delivery_id), ("resume", delivery_id), ("recover", "")]
    finally:
        await port.aclose()


async def test_deliveries_retry_rejects_foreign_delivery(tmp_path) -> None:
    """别人的投递不可重试：可见性校验先拒。"""
    filename = await _seed_mailbox(tmp_path, count=1)
    calls: list[str] = []

    class _Manager:
        async def retry(self, delivery_id):
            calls.append(delivery_id)

        async def close(self):
            return None

    port = build_core_port(
        resolve_db_path=lambda project_id: tmp_path / filename,
        manager_factory=lambda project_id: _make_manager(_Manager()),
        unit_factory=lambda _config: _FakeUnit(),
    ).project("proj-001")
    try:
        page = await port.mailbox.list(agent_id="b", inbox=True)
        delivery_id = page.items[0].deliveries[0].delivery_id
        with pytest.raises(CoreApiError) as excinfo:
            await port.deliveries.retry(agent_id="c", delivery_id=delivery_id)
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert calls == []
    finally:
        await port.aclose()


async def test_manifests_resolve_without_source_is_precondition_failure(tmp_path) -> None:
    """未注入解析源：明确拒绝，不伪造成功。"""
    port = _port_at(tmp_path, "context.db")
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.manifests.resolve(manifest_ref="agents.coder")
        assert excinfo.value.problem.code is ProblemCode.FAILED_PRECONDITION
        assert excinfo.value.problem.capability == "manifests.resolve"
    finally:
        await port.aclose()


async def test_manifests_resolve_projects_source_result(tmp_path) -> None:
    """注入解析源后：把 manifest 投影成 AgentSpec。"""
    source = {
        "name": "coder",
        "description": "写代码的家伙",
        "system_prompt": "你是 coder",
        "abilities": ["send_message", "fs"],
    }
    port = build_core_port(
        resolve_db_path=lambda project_id: tmp_path / "context.db",
        manifest_resolver=lambda ref: source,
    ).project("proj-001")
    try:
        spec = await port.manifests.resolve(manifest_ref="agents.coder")
        assert spec.manifest_ref == "agents.coder"
        assert spec.name == "coder"
        assert spec.abilities == ["send_message", "fs"]
    finally:
        await port.aclose()


# ─── 错误族 ───


async def test_missing_chain_database_is_not_found(tmp_path) -> None:
    port = build_core_port(
        resolve_db_path=lambda project_id: tmp_path / "absent.db",
    ).project("proj-001")
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.nodes.get(agent_id=AGENT_ID, node_id="n1")
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
        assert excinfo.value.problem.capability == "nodes.get"
    finally:
        await port.aclose()


async def test_unbound_port_refuses_capability_calls(tmp_path) -> None:
    """未绑定项目的端口只能调 ``project()``，直接调能力抛 FAILED_PRECONDITION。"""
    unbound = build_core_port(resolve_db_path=lambda project_id: tmp_path / "context.db")
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await unbound.nodes.get(agent_id=AGENT_ID, node_id="n1")
        assert excinfo.value.problem.code is ProblemCode.FAILED_PRECONDITION
    finally:
        await unbound.aclose()


async def test_scoped_port_shares_root_resources(tmp_path) -> None:
    """作用域视图与根端口共享连接池，关闭根即关闭全部。"""
    _session_id, _branch_id, node_id = await _seed_chain(tmp_path)
    root = build_core_port(resolve_db_path=lambda project_id: tmp_path / "context.db")
    scoped = root.project("proj-001")
    assert await scoped.nodes.get(agent_id=AGENT_ID, node_id=node_id)
    await root.aclose()


async def test_unknown_node_is_not_found(tmp_path) -> None:
    await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.nodes.get(agent_id=AGENT_ID, node_id="absent")
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
    finally:
        await port.aclose()


async def test_unknown_session_is_not_found(tmp_path) -> None:
    _session_id, branch_id, _node_id = await _seed_chain(tmp_path)
    port = _port(tmp_path)
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await port.sessions.history(
                agent_id=AGENT_ID,
                session_id="absent",
                branch_id=branch_id,
            )
        assert excinfo.value.problem.code is ProblemCode.NOT_FOUND
    finally:
        await port.aclose()


# ─── 语料 ───


async def test_corpus_records_and_replays_capability(tmp_path) -> None:
    await _seed_chain(tmp_path)
    store = ChainReadStore(resolve_db_path=lambda project_id: tmp_path / "context.db")
    mailbox = MailboxReadStore(resolve_db_path=lambda project_id: tmp_path / "context.db")
    abilities = AbilityReadStore(resolve_db_path=lambda project_id: tmp_path / "context.db")
    registry = RuntimeRegistry(
        config_factory=lambda project_id: CoreUnitConfig(project_id=project_id)
    )
    runtime = build_runtime(build_handlers(store, mailbox, abilities, registry))
    payload = {"project_id": "proj-001", "agent_id": AGENT_ID, "node_id": "absent"}
    try:
        with pytest.raises(CoreApiError) as excinfo:
            await runtime.invoke("nodes.get", payload)
        recorded = excinfo.value.problem.model_dump(mode="json")

        corpus = Corpus(tmp_path / "corpus")
        corpus.record(
            name="nodes.get.not-found",
            capability="nodes.get",
            payload=payload,
            response=recorded,
        )
        assert corpus.names() == ["nodes.get.not-found"]

        with pytest.raises(CoreApiError) as again:
            await runtime.invoke("nodes.get", corpus.load("nodes.get.not-found").payload)
        assert diff_dicts(recorded, again.value.problem.model_dump(mode="json")) == []
    finally:
        await store.aclose()
