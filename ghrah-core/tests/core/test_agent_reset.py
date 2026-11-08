# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""agent_reset 命令。

覆盖：
- 回执字段与稳定路由（project_id + agent_id）；
- 旧 Session 保留、lifecycle 不动；
- 立即持久化（reset 返回即落库）+ 增量不变量（只写新实体与 active 指针）；
- Core 重启恢复一致（active Session/Branch/Root/Head 与旧 Session 可读）；
- 驱动循环中显式拒绝；
- session_created/session_activated 既有事件路径广播。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.unit import CoreUnit, CoreUnitConfig, create_core_unit
from ghrah.types.config_types import AgentConfig


class FakeCtx:
    """宿主上下文 stub（与 test_core_unit 同形状）。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.services: dict[str, Any] = {}
        self._handlers: dict[str, Any] = {}

    def emit(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, payload))

    def provide(self, name: str, value: Any) -> None:
        self.services[name] = value

    def get(self, name: str) -> Any:
        return self.services.get(name)

    def on(self, name: str, handler: Any) -> None:
        self._handlers[name] = handler

    async def serial(self, name: str, payload: dict[str, Any]) -> Any:
        handler = self._handlers.get(name)
        if handler is None:
            return None
        return await handler(payload)


def _spawn_payload_with_id(name: str, agent_id: str) -> dict[str, Any]:
    return {
        "project_id": "default",
        "manifest_ref": "tests.agent",
        "config": {
            "name": name,
            "agent_id": agent_id,
            "system_prompt": "You are a test agent.",
        },
    }


def _actor_of(unit: CoreUnit, name: str) -> Any:
    assert unit.supervisor is not None
    return unit.supervisor._registry.get_info(name).actor_handle


def _make_unit(
    tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
) -> CoreUnitConfig:
    db_path = tmp_path / "reset-action.db"

    def persistence_factory(config: AgentConfig) -> SqliteBackend:
        return SqliteBackend(db_path=db_path, run_id=f"run-{config.effective_agent_id}")

    return ability_core_config(
        project_id="default",
        persistence_factory=persistence_factory,
    )


async def _commit_node(unit: Any, name: str, state_key: str) -> str:
    cm = _actor_of(unit, name)._context_manager
    cm.begin_iteration()
    cm.apply_state_changes({state_key: state_key})
    node = cm.commit_iteration(ability_names=["conversation"])
    await cm.wait_for_persist()
    return node.id


class TestAgentResetReceipt:
    """回执字段与错误路径。"""

    async def test_reset_receipt_carries_new_session_pointers(
        self, tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        config = _make_unit(tmp_path, ability_core_config)
        unit = create_core_unit(config)
        await unit.init(FakeCtx())
        await unit.handle_command("spawn_agent", _spawn_payload_with_id("writer", "a" * 32), None)

        result = await unit.handle_command(
            "agent_reset", {"project_id": "default", "agent_id": "a" * 32}, None
        )
        assert result["success"] is True
        data = result["data"]
        assert set(data) == {"session_id", "branch_id", "root_node_id"}
        # 有效运行头推导链：session_id ≠ 空、branch/root 与新 Session 内部一致
        session = _actor_of(unit, "writer")._context_manager.get_active_session()
        assert session.session_id == data["session_id"]
        assert session.active_branch_id == data["branch_id"]
        assert session.root_node_id == data["root_node_id"]
        await unit.stop()

    async def test_reset_unknown_agent_replies_error(
        self, tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        config = _make_unit(tmp_path, ability_core_config)
        unit = create_core_unit(config)
        await unit.init(FakeCtx())

        result = await unit.handle_command(
            "agent_reset", {"project_id": "default", "agent_id": "no-such"}, None
        )
        assert result["success"] is False
        assert result["error"]
        await unit.stop()

    async def test_reset_project_mismatch_replies_error(
        self, tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        config = _make_unit(tmp_path, ability_core_config)
        unit = create_core_unit(config)
        await unit.init(FakeCtx())
        await unit.handle_command("spawn_agent", _spawn_payload_with_id("writer", "a" * 32), None)

        result = await unit.handle_command(
            "agent_reset", {"project_id": "other", "agent_id": "a" * 32}, None
        )
        assert result["success"] is False
        await unit.stop()


class TestAgentResetSemantics:
    """旧 Session 保留 + 既有事件路径。"""

    async def test_old_sessions_preserved_and_lifecycle_untouched(
        self, tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        ctx = FakeCtx()
        config = _make_unit(tmp_path, ability_core_config)
        unit = create_core_unit(config)
        await unit.init(ctx)
        await unit.handle_command("spawn_agent", _spawn_payload_with_id("writer", "a" * 32), None)

        old_active = _actor_of(unit, "writer")._context_manager.active_session_id
        await _commit_node(unit, "writer", "mark")

        result = await unit.handle_command(
            "agent_reset", {"project_id": "default", "agent_id": "a" * 32}, None
        )
        assert result["success"] is True

        session_list = await unit.handle_command(
            "session_list",
            {"project_id": "default", "agent_id": "a" * 32, "agent_name": "writer"},
            None,
        )
        sessions = {s["session_id"]: s for s in session_list["data"]["sessions"]}
        # 旧 Session 仍在、lifecycle 不动
        assert old_active in sessions
        assert sessions[old_active]["lifecycle"] == "open"
        # 新 Session 成为 active
        assert session_list["data"]["active_session_id"] == result["data"]["session_id"]
        assert result["data"]["session_id"] != old_active
        await unit.stop()

    async def test_reset_emits_existing_session_events(
        self, tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        ctx = FakeCtx()
        config = _make_unit(tmp_path, ability_core_config)
        unit = create_core_unit(config)
        await unit.init(ctx)
        await unit.handle_command("spawn_agent", _spawn_payload_with_id("writer", "a" * 32), None)

        result = await unit.handle_command(
            "agent_reset", {"project_id": "default", "agent_id": "a" * 32}, None
        )
        assert result["success"] is True

        emitted = [name for name, _ in ctx.events]
        assert "core:session_created" in emitted
        assert "core:session_activated" in emitted
        # 事件携带的新 Session 与回执一致（实体以事件为准，回执只作即时反馈）
        created = next(payload for name, payload in ctx.events if name == "core:session_created")
        assert created["session"]["session_id"] == result["data"]["session_id"]
        await unit.stop()


class TestAgentResetPersistence:
    """立即持久化 + 增量不变量 + 重启恢复。"""

    async def test_reset_persisted_before_receipt_returns(
        self, tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        """reset 返回时数据已在库（重启口径验证）。"""
        db_path = tmp_path / "reset-action.db"

        def persistence_factory(config: AgentConfig) -> SqliteBackend:
            return SqliteBackend(db_path=db_path, run_id=f"run-{config.effective_agent_id}")

        config = ability_core_config(
            project_id="default",
            persistence_factory=persistence_factory,
        )
        unit = create_core_unit(config)
        await unit.init(FakeCtx())
        await unit.handle_command("spawn_agent", _spawn_payload_with_id("writer", "a" * 32), None)
        await _commit_node(unit, "writer", "mark")

        result = await unit.handle_command(
            "agent_reset", {"project_id": "default", "agent_id": "a" * 32}, None
        )
        assert result["success"] is True
        await unit.stop()

        # 不经运行实例，直读 sqlite 验证已落库
        backend = SqliteBackend(db_path=db_path)
        await backend.connect()
        checkpoint = await backend.load_checkpoint("a" * 32)
        assert checkpoint is not None
        assert checkpoint.active_session_id == result["data"]["session_id"]
        session_ids = {s.session_id for s in checkpoint.sessions}
        assert result["data"]["session_id"] in session_ids
        branch_ids = {b.branch_id for b in checkpoint.branches}
        assert result["data"]["branch_id"] in branch_ids
        node_ids = {n.id for n in checkpoint.nodes}
        assert result["data"]["root_node_id"] in node_ids
        await backend.close()

    async def test_reset_writes_only_new_entities_and_active_pointer(
        self, tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        """增量不变量：旧 Node 零冗余写（rowid 集合在 reset 前后不变）。"""
        import aiosqlite

        db_path = tmp_path / "reset-action.db"

        def persistence_factory(config: AgentConfig) -> SqliteBackend:
            return SqliteBackend(db_path=db_path, run_id=f"run-{config.effective_agent_id}")

        config = ability_core_config(
            project_id="default",
            persistence_factory=persistence_factory,
        )
        unit = create_core_unit(config)
        await unit.init(FakeCtx())
        await unit.handle_command("spawn_agent", _spawn_payload_with_id("writer", "a" * 32), None)
        old_active = _actor_of(unit, "writer")._context_manager.active_session_id
        await _commit_node(unit, "writer", "mark")
        old_node_id = _actor_of(unit, "writer")._context_manager.active_head.id

        async def _old_node_rowids() -> set[int]:
            async with aiosqlite.connect(str(db_path)) as db:
                cursor = await db.execute("SELECT rowid FROM nodes WHERE id = ?", (old_node_id,))
                return {row[0] async for row in cursor}

        rowids_before = await _old_node_rowids()
        assert len(rowids_before) == 1

        result = await unit.handle_command(
            "agent_reset", {"project_id": "default", "agent_id": "a" * 32}, None
        )
        assert result["success"] is True

        rowids_after = await _old_node_rowids()
        # 旧 Node 无重复写（若重写会插入新行，rowid 集合变大）
        assert rowids_after == rowids_before

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM nodes WHERE session_id = ?",
                (result["data"]["session_id"],),
            )
            count = (await cursor.fetchone())[0]  # type: ignore[index]
        # 新 Session 只有 Root 一个节点
        assert count == 1
        # 旧 Session 行保留
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT lifecycle FROM sessions WHERE session_id = ?", (old_active,)
            )
            row = await cursor.fetchone()
        assert row is not None and row[0] == "open"
        await unit.stop()

    async def test_core_restart_after_reset_restores_new_head_and_old_history(
        self, tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        """reset → stop → 重建：active 指向新 Session，旧 Session 历史可读。"""
        db_path = tmp_path / "reset-action.db"

        def persistence_factory(config: AgentConfig) -> SqliteBackend:
            return SqliteBackend(db_path=db_path, run_id=f"run-{config.effective_agent_id}")

        config = ability_core_config(
            project_id="default",
            persistence_factory=persistence_factory,
        )
        unit = create_core_unit(config)
        await unit.init(FakeCtx())
        await unit.handle_command("spawn_agent", _spawn_payload_with_id("writer", "a" * 32), None)
        old_active = _actor_of(unit, "writer")._context_manager.active_session_id
        await _commit_node(unit, "writer", "mark")

        result = await unit.handle_command(
            "agent_reset", {"project_id": "default", "agent_id": "a" * 32}, None
        )
        assert result["success"] is True
        new_session_id = result["data"]["session_id"]
        await unit.stop()

        restarted = create_core_unit(config)
        await restarted.init(FakeCtx())
        spawn = await restarted.handle_command(
            "spawn_agent", _spawn_payload_with_id("writer", "a" * 32), None
        )
        assert spawn["data"]["recovery_mode"] == "restored"

        cm = _actor_of(restarted, "writer")._context_manager
        session = cm.get_active_session()
        assert session.session_id == new_session_id
        assert session.root_node_id == result["data"]["root_node_id"]
        assert session.active_branch_id == result["data"]["branch_id"]
        # reset 后运行头状态干净（ability 默认状态重写前的空态）
        assert cm.get_current_state() == {}
        # 旧 Session 历史仍可读（get_chain_history 口径：runtime 历史非空）
        old_history = cm.get_history(session_id=old_active)
        assert len(old_history) >= 1

        # 旧 Session 可显式激活回去（reset 不删除任何路径）
        cm.activate_session(old_active)
        assert cm.active_session_id == old_active
        await restarted.stop()


class TestAgentResetConcurrency:
    """驱动循环中显式拒绝。"""

    async def test_reset_rejected_while_drive_loop_active(
        self, tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        config = _make_unit(tmp_path, ability_core_config)
        unit = create_core_unit(config)
        await unit.init(FakeCtx())
        await unit.handle_command("spawn_agent", _spawn_payload_with_id("writer", "a" * 32), None)

        actor = _actor_of(unit, "writer")
        active_before = actor._context_manager.active_session_id
        actor._drive_active = True
        try:
            result = await unit.handle_command(
                "agent_reset", {"project_id": "default", "agent_id": "a" * 32}, None
            )
            assert result["success"] is False
            assert "drive loop" in result["error"]
            # 拒绝时零副作用
            assert actor._context_manager.active_session_id == active_before
        finally:
            actor._drive_active = False
        # 空闲后同一命令成功（拒绝不是永久性）
        result = await unit.handle_command(
            "agent_reset", {"project_id": "default", "agent_id": "a" * 32}, None
        )
        assert result["success"] is True
        await unit.stop()
