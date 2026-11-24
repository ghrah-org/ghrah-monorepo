# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""SQLite 存储域的离线读资源。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from ghrah.context.persistence.sqlite_backend import SqliteBackend


class StorageScopeMismatchError(RuntimeError):
    """已登记的库归属与装配的作用域不一致。"""


type ScopeCheck = Callable[[aiosqlite.Connection, bool], Awaitable[bool]]


@asynccontextmanager
async def sqlite_read_snapshot(
    path: Path,
    *,
    immutable: bool = False,
) -> AsyncIterator[aiosqlite.Connection]:
    """以独立只读连接固定一次查询的快照。"""
    uri = path.as_uri() + "?mode=ro"
    if immutable:
        uri += "&immutable=1"
    async with aiosqlite.connect(uri, uri=True, isolation_level=None) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA query_only=ON")
        await db.execute("BEGIN")
        yield db


class SqliteReadDomain:
    """拥有同域的只读连接，读请求不取得写所有权。"""

    def __init__(
        self,
        path: str | Path,
        scope_id: str | None = None,
        *,
        check_scope: ScopeCheck | None = None,
        immutable: bool = False,
    ) -> None:
        self._path = Path(path).resolve()
        self.scope_id = scope_id
        self._check_scope = check_scope
        self._immutable = immutable

    @asynccontextmanager
    async def chain_snapshot(
        self,
    ) -> AsyncIterator[tuple[SqliteBackend | None, aiosqlite.Connection | None]]:
        """在同一快照内校验归属并读取链和轮次。"""
        if not self._path.is_file():
            yield None, None
            return
        async with (
            SqliteBackend(self._path, read_only=True, immutable=self._immutable) as backend,
            backend.read_snapshot() as db,
        ):
            if self._check_scope is not None:
                await self._check_scope(db, False)
            yield backend, db

    @asynccontextmanager
    async def message_snapshot(
        self, *, allow_uninitialized: bool = False
    ) -> AsyncIterator[tuple[aiosqlite.Connection | None, bool]]:
        """为跨消息和链的查询提供一次只读快照，兼容尚无消息表的库。"""
        if not self._path.is_file():
            yield None, False
            return
        async with sqlite_read_snapshot(self._path, immutable=self._immutable) as db:
            has_messages = self._check_scope is not None and await self._check_scope(
                db, allow_uninitialized
            )
            yield db, has_messages

    async def aclose(self) -> None:
        """查询在退出自己的快照时释放连接。"""
