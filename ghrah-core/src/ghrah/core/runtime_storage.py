# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 读写存储的装配接口及 SQLite 默认实现。"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ghrah.communication import MessageManager
from ghrah.communication.sqlite_message_store import SqliteMessageStore
from ghrah.context.persistence import PersistenceBackend
from ghrah.core.read.chain import ChainReadStore
from ghrah.core.read.mailbox import MailboxReadStore
from ghrah.core.read.queries import ChainQueries, MailboxQueries, TranscriptQueries, TurnQueries
from ghrah.core.read.transcript import TranscriptReadStore
from ghrah.core.read.turn import TurnReadStore


class RuntimeStorage(Protocol):
    """一个作用域内配套的链、消息与查询，读能力在启动写侧前可用。"""

    scope_id: str
    db_path: Path
    chain: ChainQueries
    mailbox: MailboxQueries
    turns: TurnQueries
    transcript: TranscriptQueries

    def context_backend(self, agent_config: object) -> PersistenceBackend: ...

    async def start_messages(self, *, start_dispatcher: bool = True) -> MessageManager: ...

    async def aclose(self) -> None: ...


type RuntimeStorageFactory = Callable[[str], RuntimeStorage]


class SqliteRuntimeStorage:
    """用同一个存储域提供 SQLite 写事务及离线查询。"""

    def __init__(
        self,
        db_path: str | Path,
        scope_id: str,
        *,
        adopt_legacy_chain: bool = False,
        immutable_reads: bool = False,
    ) -> None:
        self.scope_id = scope_id
        self.db_path = Path(db_path).resolve()
        self.immutable_reads = immutable_reads
        self._store = SqliteMessageStore(
            self.db_path,
            scope_id,
            adopt_legacy_chain=adopt_legacy_chain,
            immutable_reads=immutable_reads,
        )
        self.chain: ChainQueries = ChainReadStore(self._store.reads)
        self.mailbox: MailboxQueries = MailboxReadStore(self._store.reads)
        self.turns: TurnQueries = TurnReadStore(self._store.reads)
        self.transcript: TranscriptQueries = TranscriptReadStore(self._store.reads)

    def context_backend(self, agent_config: object) -> PersistenceBackend:
        """建立同域 Agent 链存储。"""
        return self._store.context_backend(agent_config)

    async def start_messages(self, *, start_dispatcher: bool = True) -> MessageManager:
        """取得写侧所有权并启动消息管理器。"""
        manager = MessageManager.from_store(self._store)
        await manager.open(prepare_context=True, start_dispatcher=start_dispatcher)
        return manager

    async def aclose(self) -> None:
        """在注册表关闭写运行时后释放本域只读资源。"""
        await self._store.reads.aclose()
