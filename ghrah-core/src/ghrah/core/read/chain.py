# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""链表族（ActionChain）只读访问：与集群生命周期解耦。

这是「禁止直连 Core 存储」之后消费方读链路的唯一入口。

三条必须守住的约束：

1. **作用域在构造时固定**：实例只读一个已给定的库文件，不再接收作用域参数，
   消费方拿到的端口实例本身就是作用域；
2. **不依赖 ``CoreUnit`` 挂载**：只读连接按库路径懒开即可回答，
   否则「读一个已停止 Agent 的历史」会先复活项目；
3. **不缓存数据**：只读连接 + WAL 已能保证读己之写；缓存会让 ``head_node_id``
   落后于刚提交的写。

⚠ **持久层分区键是 ``agent_id``，不是显示名。**
``SqliteBackend`` 的参数叫 ``agent_name``，但实际写入的是稳定 Agent ID。
本模块统一用 ``agent_id`` 命名，避免把显示名传进去导致读空。
"""

from __future__ import annotations

from ghrah.protocol.views.core import (
    BranchView,
    ChainMetaView,
    HistoryView,
    NodeView,
    SessionCatalogView,
    SessionView,
)

from ghrah.context.persistence import serialize_node
from ghrah.context.persistence.sqlite_read import SqliteReadDomain

__all__ = ["ChainReadStore", "ChainDatabaseUnavailableError"]


class ChainDatabaseUnavailableError(RuntimeError):
    """注入的链查询后端可用此异常表示库不可用，端口会映射为 NOT_FOUND。

    SQLite 文件尚未创建时直接按查询返回空值或空目录。
    """


class ChainReadStore:
    """将本存储域的链查询转换为 Core 视图。"""

    def __init__(self, domain: SqliteReadDomain) -> None:
        self._domain = domain

    async def node(self, *, agent_id: str, node_id: str) -> NodeView | None:
        """读取单个节点（精确查询，不加载整条链）。

        Args:
            agent_id: 稳定 Agent ID（持久层分区键）
            node_id: 节点标识

        Returns:
            节点视图；不存在时返回 ``None``
        """
        async with self._domain.chain_snapshot() as (backend, _):
            if backend is None:
                return None
            node = await backend.load_node(agent_id, node_id)
            if node is None:
                return None
            return NodeView.model_validate(serialize_node(node))

    async def catalog(self, *, agent_id: str) -> SessionCatalogView:
        """读取会话目录与分支表。

        语义与面向用户的读法一致：**过滤软删除**，无链数据时返回空目录。

        Args:
            agent_id: 稳定 Agent ID

        Returns:
            会话目录（永远不是 ``None``）
        """
        async with self._domain.chain_snapshot() as (backend, _):
            if backend is None:
                return SessionCatalogView(availability="offline")
            raw = await backend.load_session_catalog(agent_id)
            return SessionCatalogView(
                availability="offline",
                active_session_id=raw["active_session_id"],
                sessions=[SessionView.model_validate(item) for item in raw["sessions"]],
                branches=[BranchView.model_validate(item) for item in raw["branches"]],
            )

    async def meta(self, *, agent_id: str) -> ChainMetaView:
        """读取链目录元数据（active 指针 + 会话/分支表，不含节点）。

        Args:
            agent_id: 稳定 Agent ID

        Returns:
            元数据（永远不是 ``None``；无链数据时为空表）
        """
        async with self._domain.chain_snapshot() as (backend, _):
            if backend is None:
                return ChainMetaView(agent_id=agent_id)
            raw = await backend.load_session_catalog(agent_id)
            return ChainMetaView(
                agent_id=agent_id,
                active_session_id=raw["active_session_id"],
                sessions=[SessionView.model_validate(item) for item in raw["sessions"]],
                branches=[BranchView.model_validate(item) for item in raw["branches"]],
            )

    async def branches(self, *, agent_id: str, session_id: str) -> list[BranchView] | None:
        """列出某会话下的分支。

        Args:
            agent_id: 稳定 Agent ID
            session_id: 会话标识

        Returns:
            分支列表；会话不存在时返回 ``None``
        """
        async with self._domain.chain_snapshot() as (backend, _):
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
        agent_id: str,
        session_id: str,
        branch_id: str,
    ) -> str | None:
        """按 ID 读取某分支的头节点，包含软删除的会话和分支。

        Args:
            agent_id: 稳定 Agent ID
            session_id: 会话标识
            branch_id: 分支标识

        Returns:
            头节点 id；会话或分支不存在时返回 ``None``
        """
        async with self._domain.chain_snapshot() as (backend, _):
            if backend is None:
                return None
            return await backend.load_branch_head(agent_id, session_id, branch_id)

    async def history(
        self,
        *,
        agent_id: str,
        session_id: str,
        branch_id: str,
        limit: int = -1,
    ) -> HistoryView | None:
        """按 ID 读取某分支的链历史，包含软删除的会话和分支。

        Args:
            agent_id: 稳定 Agent ID
            session_id: 会话标识
            branch_id: 分支标识
            limit: ``-1`` 表示全部；``>0`` 表示只取末尾 N 个节点

        Returns:
            链历史；会话或分支不存在时返回 ``None``
        """
        async with self._domain.chain_snapshot() as (backend, _):
            if backend is None:
                return None
            history = await backend.load_branch_history(
                agent_id, session_id, branch_id, limit=limit
            )
            if history is None:
                return None
            active_session_id, nodes = history
            return HistoryView(
                agent_name=agent_id,
                agent_id=agent_id,
                session_id=session_id,
                branch_id=branch_id,
                active_session_id=active_session_id,
                nodes=[NodeView.model_validate(serialize_node(node)) for node in nodes],
            )
