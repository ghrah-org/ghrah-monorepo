# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Core 端口契约：一个端口实例 = 一个作用域（C3/M2）。

覆盖：
- 作用域端口只读自己的数据根——两个实例互不串数据；
- 缺库时读面返回既有「空结果 / NOT_FOUND」语义，而不是内部错误；
- ``aclose`` 幂等，关闭后可重建新实例；
- 单元在命令入口统一补齐作用域：载荷不带 ``project_id`` 也能解析。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from ghrah.protocol.problem import ProblemCode

from ghrah.context.persistence import ContextChanges
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.session_runtime import SessionRuntime
from ghrah.core.api.errors import CoreApiError
from ghrah.core.unit import CoreUnitConfig, create_core_unit
from ghrah.core.wiring import build_core_port


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


async def _seed_chain(path: Path, agent_id: str) -> SessionRuntime:
    """在指定 sqlite 上落一份最小链数据（一个会话 + 一个节点）。"""
    runtime = SessionRuntime.create(agent_name=agent_id)
    runtime.commit_node(ability_names=["tool-a"], agent_state={"marker": "seed"})
    backend = SqliteBackend(db_path=path)
    await backend.connect()
    await backend.apply_changes(
        ContextChanges(
            agent_name=agent_id,
            active_session_id=runtime.session.session_id,
            sessions=(runtime.session,),
            branches=tuple(runtime.branches.values()),
            nodes=tuple(runtime.chain.nodes),
        )
    )
    await backend.close()
    return runtime


def _port(scope_id: str, db_path: Path):
    return build_core_port(scope_id=scope_id, db_path=str(db_path))


async def test_scoped_ports_do_not_share_data(tmp_path: Path) -> None:
    db_a = tmp_path / "scope-a.db"
    db_b = tmp_path / "scope-b.db"
    runtime = await _seed_chain(db_a, "agent-a")
    port_a = _port("scope-a", db_a)
    port_b = _port("scope-b", db_b)
    try:
        catalog_a = await port_a.sessions.catalog(agent_id="agent-a")
        assert catalog_a.active_session_id == runtime.session.session_id
        assert [item.session_id for item in catalog_a.sessions] == [runtime.session.session_id]

        # 另一个作用域（另一个数据根）看到的是空目录，而不是 A 的数据。
        catalog_b = await port_b.sessions.catalog(agent_id="agent-a")
        assert catalog_b.active_session_id is None
        assert catalog_b.sessions == []
    finally:
        await port_a.aclose()
        await port_b.aclose()


async def test_missing_data_root_returns_empty_semantics(tmp_path: Path) -> None:
    port = _port("scope-empty", tmp_path / "not-created.db")
    try:
        catalog = await port.sessions.catalog(agent_id="agent-x")
        assert catalog.sessions == []
        with pytest.raises(CoreApiError) as missing_node:
            await port.nodes.get(agent_id="agent-x", node_id="n-1")
        assert missing_node.value.problem.code is ProblemCode.NOT_FOUND
        summary = await port.mailbox.summary(agent_id="agent-x")
        assert summary.availability in {"available", "empty"}
    finally:
        await port.aclose()


async def test_close_is_idempotent_and_port_can_be_rebuilt(tmp_path: Path) -> None:
    db = tmp_path / "scope-a.db"
    await _seed_chain(db, "agent-a")
    port = _port("scope-a", db)
    await port.aclose()
    await port.aclose()

    rebuilt = _port("scope-a", db)
    try:
        catalog = await rebuilt.sessions.catalog(agent_id="agent-a")
        assert catalog.sessions
    finally:
        await rebuilt.aclose()


async def test_unit_fills_scope_at_command_entry(
    tmp_path: Path, ability_core_config: Callable[..., CoreUnitConfig]
) -> None:
    db_path = tmp_path / "command-entry.db"

    def persistence_factory(_config: Any) -> SqliteBackend:
        return SqliteBackend(db_path=db_path)

    config = ability_core_config(persistence_factory=persistence_factory)
    unit = create_core_unit(config)
    await unit.init(FakeCtx())
    try:
        # 命令载荷刻意不带 project_id：作用域由单元在入口统一补齐。
        result = await unit.handle_command("list_agents", {}, None)
        assert result["success"] is True
        assert (result.get("data") or {}).get("agents") == []
    finally:
        await unit.stop()
