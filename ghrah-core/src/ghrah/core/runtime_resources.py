"""Core 自持的作用域资源；多个集群共享同一投递权威。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ghrah.communication.message_manager import MessageManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend


@dataclass
class _ScopeResources:
    path: Path
    manager: MessageManager
    users: int = 0


class RuntimeResources:
    """按作用域打开资源，在最后一个使用者退出后关闭。"""

    def __init__(self, *, room_projector: Callable[[Any], Awaitable[None]] | None = None) -> None:
        """保存显式提供的过渡期 Room 投影端口，不执行 IO。"""
        self._scopes: dict[str, _ScopeResources] = {}
        self._lock = asyncio.Lock()
        self._room_projector = room_projector

    async def acquire(self, scope_ref: str, database_path: str) -> MessageManager:
        """准备作用域存储与投递恢复，调度由调用者就绪后启动。"""
        if not scope_ref or not database_path:
            raise ValueError("scope_ref and database_path are required")
        path = Path(database_path).resolve()
        async with self._lock:
            entry = self._scopes.get(scope_ref)
            if entry is not None:
                if entry.path != path:
                    raise ValueError("Scope database owner conflict")
                entry.users += 1
                return entry.manager
            manager = MessageManager(
                path,
                project_id=scope_ref,
                agent_ids=(),
                session_routing=True,
                allow_persisted_agents=True,
            )
            try:
                # 先取得投递所有权，再由该所有者准备上下文 schema。
                await manager.open(start_dispatcher=False, prepare_context=True)
                if self._room_projector is not None:
                    manager.configure_room_projector(self._room_projector)
            except BaseException:
                await manager.close()
                raise
            self._scopes[scope_ref] = _ScopeResources(path, manager, 1)
            return manager

    async def release(self, scope_ref: str) -> None:
        """释放一次成功取得的使用权，最后一个使用者关闭资源。"""
        async with self._lock:
            entry = self._scopes[scope_ref]
            entry.users -= 1
            if entry.users == 0:
                try:
                    await entry.manager.close()
                finally:
                    del self._scopes[scope_ref]

    @staticmethod
    def persistence(database_path: str) -> SqliteBackend:
        """为显式启动的 Agent 构造延迟打开的持久化后端。"""
        return SqliteBackend(db_path=database_path)
