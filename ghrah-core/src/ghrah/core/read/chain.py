# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""链表族（ActionChain）只读访问：与集群生命周期解耦。

这是「禁止直连 Core 存储」之后消费方读链路的唯一入口。

两条必须守住的约束：

1. **不依赖 ``CoreUnit`` 挂载**：按 ``project_id`` 懒开只读连接即可回答，
   否则「读一个已停止 Agent 的历史」会先复活项目；
2. **不缓存数据**：只读连接 + WAL 已能保证读己之写；缓存会让 ``head_node_id``
   落后于刚提交的写。

⚠ **持久层分区键是 ``agent_id``，不是显示名。**
``SqliteBackend`` 的参数叫 ``agent_name``，但实际写入的是稳定 Agent ID。
本模块统一用 ``agent_id`` 命名，避免把显示名传进去导致读空。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from ghrah.protocol.views.core import (
    BranchView,
    ChainMetaView,
    HistoryView,
    NodeView,
    SessionCatalogView,
    SessionView,
)

from ghrah.context.persistence import serialize_node
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.session_runtime import SessionRuntime

__all__ = ["ChainReadStore", "ChainDatabaseUnavailableError"]

type DbPathResolver = Callable[[str], str | Path]


class ChainDatabaseUnavailableError(RuntimeError):
    """项目链库不存在或不可打开。

    这是**正常情况**（项目尚未产生任何链数据），不是故障：
    调用方应返回 ``NOT_FOUND`` 而不是 ``INTERNAL``。
    """


class ChainReadStore:
    """按项目复用只读连接的链读取器。

    返回 DTO，不返回内部对象——转换发生在最靠近存储的这一层。

    Args:
        resolve_db_path: 项目到 ActionChain sqlite 路径的解析器（装配层注入）
    """

    def __init__(self, resolve_db_path: DbPathResolver) -> None:
        self._resolve = resolve_db_path
        self._backends: dict[str, SqliteBackend] = {}
        self._lock = asyncio.Lock()

    async def _backend(self, project_id: str) -> SqliteBackend:
        """取（或懒建）某项目的只读连接。

        Args:
            project_id: 项目标识

        Returns:
            已连接的只读 backend

        Raises:
            ChainDatabaseUnavailableError: 项目链库文件不存在
        """
        backend = await self._maybe_backend(project_id)
        if backend is None:
            raise ChainDatabaseUnavailableError(str(self._resolve(project_id)))
        return backend

    async def _maybe_backend(self, project_id: str) -> SqliteBackend | None:
        """取（或懒建）只读连接；项目还没有链库时返回 ``None``。

        「文件不存在」是**正常状态**（项目尚未产生链数据），不是故障——
        由调用方决定它对应「空结果」还是 ``NOT_FOUND``。

        Args:
            project_id: 项目标识

        Returns:
            只读 backend，或 ``None``
        """
        async with self._lock:
            backend = self._backends.get(project_id)
            if backend is None:
                db_path = Path(self._resolve(project_id))
                if not db_path.is_file():
                    return None
                backend = SqliteBackend(db_path=db_path, read_only=True)
                await backend.connect()
                self._backends[project_id] = backend
            return backend

    async def node(self, *, project_id: str, agent_id: str, node_id: str) -> NodeView | None:
        """读取单个节点（精确查询，不加载整条链）。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID（持久层分区键）
            node_id: 节点标识

        Returns:
            节点视图；不存在时返回 ``None``
        """
        backend = await self._maybe_backend(project_id)
        if backend is None:
            return None
        node = await backend.load_node(agent_id, node_id)
        if node is None:
            return None
        return NodeView.model_validate(serialize_node(node))

    async def catalog(self, *, project_id: str, agent_id: str) -> SessionCatalogView:
        """读取会话目录与分支表。

        语义与面向用户的读法一致：**过滤软删除**，无链数据时返回空目录。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID

        Returns:
            会话目录（永远不是 ``None``）
        """
        backend = await self._maybe_backend(project_id)
        if backend is None:
            return SessionCatalogView(availability="offline")
        raw = await backend.load_session_catalog(agent_id)
        return SessionCatalogView(
            availability="offline",
            active_session_id=raw["active_session_id"],
            sessions=[SessionView.model_validate(item) for item in raw["sessions"]],
            branches=[BranchView.model_validate(item) for item in raw["branches"]],
        )

    async def meta(self, *, project_id: str, agent_id: str) -> ChainMetaView:
        """读取链目录元数据（active 指针 + 会话/分支表，不含节点）。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID

        Returns:
            元数据（永远不是 ``None``；无链数据时为空表）
        """
        backend = await self._maybe_backend(project_id)
        if backend is None:
            return ChainMetaView(agent_id=agent_id)
        raw = await backend.load_session_catalog(agent_id)
        return ChainMetaView(
            agent_id=agent_id,
            active_session_id=raw["active_session_id"],
            sessions=[SessionView.model_validate(item) for item in raw["sessions"]],
            branches=[BranchView.model_validate(item) for item in raw["branches"]],
        )

    async def branches(
        self, *, project_id: str, agent_id: str, session_id: str
    ) -> list[BranchView] | None:
        """列出某会话下的分支。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID
            session_id: 会话标识

        Returns:
            分支列表；会话不存在时返回 ``None``
        """
        backend = await self._maybe_backend(project_id)
        if backend is None:
            return None
        raw = await backend.load_session_catalog(agent_id)
        sessions = {str(item["session_id"]) for item in raw["sessions"]}
        if session_id not in sessions:
            return None
        return [
            BranchView.model_validate(item)
            for item in raw["branches"]
            if str(item["session_id"]) == session_id
        ]

    async def head(
        self,
        *,
        project_id: str,
        agent_id: str,
        session_id: str,
        branch_id: str,
    ) -> str | None:
        """读取某分支的当前头节点 id。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID
            session_id: 会话标识
            branch_id: 分支标识

        Returns:
            头节点 id；会话或分支不存在时返回 ``None``
        """
        backend = await self._maybe_backend(project_id)
        if backend is None:
            return None
        checkpoint = await backend.load_checkpoint(agent_id, snapshot_payloads="boundaries")
        if checkpoint is None:
            return None
        session = next(
            (item for item in checkpoint.sessions if item.session_id == session_id),
            None,
        )
        if session is None:
            return None
        runtime = SessionRuntime.restore(
            session=session,
            branches=[item for item in checkpoint.branches if item.session_id == session_id],
            nodes=[item for item in checkpoint.nodes if item.session_id == session_id],
        )
        branch = next(
            (item for item in runtime.branches.values() if item.branch_id == branch_id),
            None,
        )
        if branch is None:
            return None
        return str(branch.head_node_id) or None

    async def history(
        self,
        *,
        project_id: str,
        agent_id: str,
        session_id: str,
        branch_id: str,
        limit: int = -1,
    ) -> HistoryView | None:
        """读取某分支上的链历史。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID
            session_id: 会话标识
            branch_id: 分支标识
            limit: ``-1`` 表示全部；``>0`` 表示只取末尾 N 个节点

        Returns:
            链历史；会话不存在时返回 ``None``
        """
        backend = await self._maybe_backend(project_id)
        if backend is None:
            return None
        checkpoint = await backend.load_checkpoint(agent_id, snapshot_payloads="boundaries")
        if checkpoint is None:
            return None
        session = next(
            (item for item in checkpoint.sessions if item.session_id == session_id),
            None,
        )
        if session is None:
            return None
        runtime = SessionRuntime.restore(
            session=session,
            branches=[item for item in checkpoint.branches if item.session_id == session_id],
            nodes=[item for item in checkpoint.nodes if item.session_id == session_id],
        )
        nodes = runtime.get_history(branch_id=branch_id, limit=limit)
        return HistoryView(
            agent_name=agent_id,
            agent_id=agent_id,
            session_id=session_id,
            branch_id=branch_id,
            active_session_id=checkpoint.active_session_id,
            nodes=[NodeView.model_validate(serialize_node(node)) for node in nodes],
        )

    async def aclose(self) -> None:
        """关闭全部只读连接（幂等）。"""
        async with self._lock:
            backends = list(self._backends.values())
            self._backends.clear()
        for backend in backends:
            await backend.close()
