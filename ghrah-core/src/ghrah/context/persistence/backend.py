# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ActionSession/ActionBranch checkpoint 持久化后端接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ghrah.context.persistence.catalog import ContextChainMeta
from ghrah.context.persistence.changes import ContextChanges
from ghrah.context.persistence.checkpoint import ContextCheckpoint, SnapshotPayloads

__all__ = ["PersistenceBackend", "SnapshotPayloads"]


class PersistenceBackend(ABC):
    """按 Agent 保存多 Session 拓扑的异步持久化接口。"""

    @abstractmethod
    async def apply_changes(self, changes: ContextChanges) -> None:
        """在一个原子操作中应用最小增量变更集。"""

    @abstractmethod
    async def load_checkpoint(
        self,
        agent_name: str,
        *,
        snapshot_payloads: SnapshotPayloads = "all",
    ) -> ContextCheckpoint | None:
        """加载 Agent 的完整多 Session checkpoint。

        Args:
            agent_name: Agent 名称
            snapshot_payloads: 快照载荷投影策略。
                ``all`` 保留全部 ``messages_snapshot``（默认，完全兼容）；
                ``boundaries`` 仅保留 root 与 compact 语义边界的载荷；
                ``none`` 全部不加载载荷（只需要链拓扑/元数据时使用）。
        """

    @abstractmethod
    async def delete_checkpoint(self, agent_name: str) -> None:
        """删除指定 Agent 的完整 checkpoint。"""

    @abstractmethod
    async def list_agents(self) -> list[str]:
        """列出所有存在 checkpoint 的 Agent 名称。"""

    async def has_checkpoint(self, agent_name: str) -> bool:
        """判断 Agent 是否存在持久化目录（轻量查询）。

        默认实现委托 ``load_checkpoint``，因此会继承各后端的 schema
        校验与 legacy/v2 fail-fast 语义；SQLite 等后端应覆写为单条
        轻量查询。
        """
        return await self.load_checkpoint(agent_name, snapshot_payloads="none") is not None

    async def load_chain_meta(self, agent_name: str) -> ContextChainMeta | None:
        """只读取 Session/Branch/active 指针，不加载任何链节点。"""
        checkpoint = await self.load_checkpoint(agent_name, snapshot_payloads="none")
        if checkpoint is None:
            return None
        return ContextChainMeta(
            agent_name=checkpoint.agent_name,
            active_session_id=checkpoint.active_session_id,
            sessions=checkpoint.sessions,
            branches=checkpoint.branches,
        )

    async def list_node_ids(self, agent_name: str) -> set[str]:
        """返回 Agent 已持久化的节点 ID 集合（用于避免重复写）。"""
        checkpoint = await self.load_checkpoint(agent_name, snapshot_payloads="none")
        if checkpoint is None:
            return set()
        return {node.id for node in checkpoint.nodes}
