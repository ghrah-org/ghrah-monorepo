# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Action-chain ledger built-in Subject unit（读侧经 Core 端口）。

``chain_history`` 读命令经 ``CorePort`` 取链历史——**不再直连 Core 的 sqlite**，
也不再自己派生库路径。数据权威仍在 Core，本单元只是调用方。
"""

from __future__ import annotations

import logging
from typing import Any

from ghrah.protocol.errors import ApiError
from ghrah.protocol.ports.core import CorePort
from ghrah.protocol.ports.keys import CORE_PORT
from ghrah.protocol.problem import ProblemCode
from ghrah.protocol.types import GetChainHistoryPayload

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.service_keys import PROJECT_MANAGER
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units._commands import CHAIN_HISTORY_COMMANDS

__all__ = ["LedgerUnit"]

logger = logging.getLogger(__name__)


class LedgerUnit(SubjectUnit):
    """Owns the read-side ActionChain projection (Core port caller)."""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ports: dict[str, CorePort] = {}
        self._frozen_projects: set[str] = set()
        self._ctx: Any | None = None
        self._meta = UnitMeta(
            name="ledger",
            # 不发布实例级服务：读永远走 Core 端口，不留全局库读后门。
            provides=frozenset(),
            routes=RouteSpec(commands=CHAIN_HISTORY_COMMANDS),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        # 作用域端口是轻量视图，共享 Core 根端口的连接池；
        # 关闭由装配层负责，这里只丢弃引用。
        self._ports.clear()

    async def _port_for_project(self, project_id: str) -> CorePort:
        """取（或绑定）某项目的 Core 端口视图。

        Args:
            project_id: 项目标识

        Returns:
            绑定到该项目的端口视图

        Raises:
            ValueError: 项目标识缺失、项目已归档，或容器没有提供端口
        """
        if not project_id:
            raise ValueError("project_id required")
        if self._ctx is None:
            raise ValueError("project_manager unavailable")
        if project_id in self._frozen_projects:
            raise ValueError("resource_archived")
        existing = self._ports.get(project_id)
        if existing is not None:
            return existing
        base: CorePort | None = self._ctx.get(CORE_PORT, strict=False)
        if base is None:
            raise ValueError("core_port_unavailable")
        port = base.project(project_id)
        self._ports[project_id] = port
        return port

    async def close_project(self, project_id: str) -> None:
        """Freeze a Project and drop its cached port view."""

        self._frozen_projects.add(project_id)
        self._ports.pop(project_id, None)

    async def restore_project(self, project_id: str) -> None:
        self._frozen_projects.discard(project_id)

    async def evict_project(self, project_id: str) -> None:
        await self.close_project(project_id)
        self._frozen_projects.discard(project_id)

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        if command not in CHAIN_HISTORY_COMMANDS:
            return {"success": False, "error": f"Unknown ledger command: {command}"}
        return await self._handle_chain_history(payload)

    async def _handle_chain_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = GetChainHistoryPayload.model_validate(payload)
            agent_name = request.agent_name
            agent_id = request.agent_id
            project_id = request.project_id
            session_id = request.session_id
            branch_id = request.branch_id
            limit = request.limit
            persistence_key = await self._resolve_persistence_key(
                agent_name, project_id=project_id, agent_id=agent_id
            )
            port = await self._port_for_project(project_id)
            nodes: list[dict[str, Any]] = []
            active_session_id = ""
            try:
                history = await port.sessions.history(
                    agent_id=persistence_key,
                    session_id=session_id,
                    branch_id=branch_id,
                    limit=limit,
                )
            except ApiError as exc:
                # 存量语义：会话不存在时返回空历史，而不是报错。
                if exc.problem.code is not ProblemCode.NOT_FOUND:
                    raise
            else:
                nodes = [node.model_dump(mode="json") for node in history.nodes]
                active_session_id = history.active_session_id
            return {
                "success": True,
                "data": {
                    "project_id": project_id,
                    "agent_name": agent_name,
                    "agent_id": persistence_key,
                    "session_id": session_id,
                    "branch_id": branch_id,
                    "nodes": nodes,
                    "active_session_id": active_session_id,
                },
            }
        except Exception as exc:
            logger.exception("Failed to get chain history")
            return {"success": False, "error": str(exc)}

    async def _resolve_persistence_key(
        self, agent_name: str, *, project_id: str, agent_id: str
    ) -> str:
        """把显示名解析为 Core checkpoint 使用的稳定 agent_id。

        ContextManager 以 ``agent_id`` 分区 SQLite；读取前验证该稳定身份确实
        属于目标 Project，绝不按全局 name 搜索或回退实例级数据库。
        """
        if not project_id:
            raise ValueError("project_id required")
        if not agent_id:
            raise ValueError("agent_id required")
        if self._ctx is None:
            raise ValueError("project_manager unavailable")
        manager = self._ctx.get(PROJECT_MANAGER.name)
        result = await manager.handle_command("project_get", {"project_id": project_id})
        project = (result.get("data") or {}).get("project") if result.get("success") else None
        if not project:
            raise ValueError(f"project not found: {project_id}")
        matches = [
            agent for agent in project.get("agents") or [] if agent.get("agent_id") == agent_id
        ]
        if len(matches) != 1:
            raise ValueError("agent_project_mismatch")
        if matches[0].get("name") != agent_name:
            raise ValueError("agent_identity_mismatch")
        return agent_id
