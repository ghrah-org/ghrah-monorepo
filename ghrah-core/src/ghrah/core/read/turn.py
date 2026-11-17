# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""本作用域轮次及审批的只读投影。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from ghrah.protocol.views.core import HitlRequestListView, HitlRequestView, TurnListView, TurnView

from ghrah.context.persistence.sqlite_backend import SqliteBackend


def _hitl_view(row: aiosqlite.Row) -> HitlRequestView:
    data = {key: row[key] for key in HitlRequestView.model_fields}
    for field in ("tool_call", "options", "resolution"):
        data[field] = json.loads(data[field])
    return HitlRequestView.model_validate(data)


def _turn_view(row: aiosqlite.Row) -> TurnView:
    data = dict(row)
    del data["metadata"]
    return TurnView.model_validate(data)


class TurnReadStore:
    """按 Agent 查询轮次和待审批项，每次查询使用只读连接。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @asynccontextmanager
    async def _read(self) -> AsyncIterator[SqliteBackend | None]:
        if not self._db_path.is_file():
            yield None
            return
        async with SqliteBackend(self._db_path, read_only=True) as backend:
            yield backend

    async def turn(self, *, agent_id: str, turn_id: str) -> TurnView | None:
        """读取所属 Agent 的单个轮次。"""
        async with self._read() as backend:
            row = await backend.load_turn(agent_id, turn_id) if backend else None
            return _turn_view(row) if row is not None else None

    async def turns(
        self, *, agent_id: str, session_id: str | None, status: str | None, limit: int
    ) -> TurnListView:
        """列出最近轮次。"""
        async with self._read() as backend:
            rows = await backend.list_turns(agent_id, session_id, status, limit) if backend else []
            return TurnListView(agent_id=agent_id, items=[_turn_view(row) for row in rows])

    async def hitl_request(self, *, agent_id: str, promise_id: str) -> HitlRequestView | None:
        """读取所属 Agent 的审批请求及终态。"""
        async with self._read() as backend:
            row = await backend.load_hitl_request(agent_id, promise_id) if backend else None
            return _hitl_view(row) if row is not None else None

    async def pending_hitl(self, *, agent_id: str, session_id: str | None) -> HitlRequestListView:
        """列出尚待人工处理的请求。"""
        async with self._read() as backend:
            rows = await backend.list_pending_hitl(agent_id, session_id) if backend else []
            return HitlRequestListView(agent_id=agent_id, items=[_hitl_view(row) for row in rows])
