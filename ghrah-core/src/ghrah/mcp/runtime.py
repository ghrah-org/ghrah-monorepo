# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""运行时：激活集合 + 调用分发。

两个职责，仅此而已：

**一、记住「哪个 agent 能调哪些服务器上的哪些工具」。** 这是可见性与权限的唯一
权威。技能的 MCP 声明在被加载时登记进来，卸载时移除。

**二、把一次调用路由到正确的会话。** 解析服务器、校验白名单、取凭据、建会话、
执行。

这里**不存放工具信息**（名字、说明、参数 schema）。那些属于技能模块的本地配置，
运行时只持有「授权集合」这一份最小事实。因此运行时不提供任何「列出工具」的查询
接口——一旦这里也能回答「有哪些工具」，就出现了第二份真源。

一条必须遵守的顺序：**先校验权限，再取凭据，最后才建连**。反过来会把「这个工具
你能不能调」与「这台服务器的密钥存不存在」混成同一个错误，既泄露了服务器存在性，
也让排查方向错误。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from ghrah.mcp.client import Connector
from ghrah.mcp.credentials import CredentialResolver, no_credentials
from ghrah.mcp.errors import McpConfigError, McpNotActivatedError
from ghrah.mcp.result import ToolOutcome
from ghrah.mcp.session import McpSession
from ghrah.mcp.spec import McpServerSpec

__all__ = ["McpRuntime"]

logger = logging.getLogger(__name__)


class McpRuntime:
    """激活集合与连接的持有者。装配期注入，生命周期与 agent 会话一致。"""

    def __init__(
        self,
        *,
        credentials: CredentialResolver | None = None,
        connector: Connector | None = None,
    ) -> None:
        self._credentials: CredentialResolver = credentials or no_credentials
        self._connector = connector
        self._activated: dict[tuple[str, str], McpServerSpec] = {}
        self._sessions: dict[tuple[str, str], McpSession] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def activate(self, spec: McpServerSpec, *, agent_scope: str) -> None:
        """登记一台服务器。**纯内存操作，不联网。**

        因此加载技能不会因为服务器慢或不可达而变慢，服务器宕机也不影响模型看到
        能力清单。
        """
        if self._closed:
            raise McpConfigError("runtime is closed")
        key = (agent_scope, spec.server)
        existing = self._activated.get(key)
        if existing is not None and existing.skill_full_name != spec.skill_full_name:
            # 同一 agent 下两台不同技能声明了同名服务器：无法从调用参数里区分，
            # 必须让它在加载期就失败，而不是留到调用期猜一个。
            raise McpConfigError(
                f"server name {spec.server!r} is already declared by skill "
                f"{existing.skill_full_name!r} for this agent; server names must be "
                "unique per agent"
            )
        self._activated[key] = spec

    async def deactivate(self, *, agent_scope: str, skill_full_name: str) -> None:
        """移除某个技能声明的全部服务器，并关闭不再被引用的连接。"""
        removed = [
            key
            for key, spec in self._activated.items()
            if key[0] == agent_scope and spec.skill_full_name == skill_full_name
        ]
        for key in removed:
            self._activated.pop(key, None)
            session = self._sessions.pop(key, None)
            if session is not None:
                await session.close()

    def activated_servers(self, *, agent_scope: str) -> tuple[str, ...]:
        """该 agent 已激活的服务器名（按字典序，便于稳定输出）。"""
        return tuple(sorted(server for (scope, server) in self._activated if scope == agent_scope))

    def spec_for(self, *, agent_scope: str, server: str) -> McpServerSpec | None:
        """取某台已激活服务器的声明；未激活返回 ``None``。"""
        return self._activated.get((agent_scope, server))

    def _resolve_spec(
        self,
        *,
        agent_scope: str,
        server: str,
        skill: str | None,
    ) -> McpServerSpec:
        """定位声明并做授权判定。这是权限边界，不能省。"""
        spec = self._activated.get((agent_scope, server))
        if spec is None:
            raise McpNotActivatedError(
                f"server {server!r} is not activated for this agent; "
                "load the skill that declares it first"
            )
        if skill and spec.skill_full_name != skill:
            raise McpNotActivatedError(
                f"server {server!r} belongs to skill {spec.skill_full_name!r}, not {skill!r}"
            )
        return spec

    async def _session_for(self, agent_scope: str, spec: McpServerSpec) -> McpSession:
        key = (agent_scope, spec.server)
        session = self._sessions.get(key)
        if session is not None:
            return session
        async with self._lock:
            session = self._sessions.get(key)
            if session is None:
                # 凭据按**地址**关联：agentconf 里 base_url 等于该地址的 provider 记录。
                credential = self._credentials(spec.url)
                session = McpSession(
                    spec,
                    credential=credential,
                    connector=self._connector,
                )
                self._sessions[key] = session
        return session

    async def call(
        self,
        *,
        agent_scope: str,
        server: str,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        skill: str | None = None,
    ) -> ToolOutcome:
        """执行一次工具调用。

        异常只表示**连接、协议或授权**层面的失败；服务器声明的工具级失败以
        ``is_error=True`` 的结果返回，调用方必须检查。
        """
        spec = self._resolve_spec(agent_scope=agent_scope, server=server, skill=skill)
        # 白名单校验先于建连：非白名单工具连凭据都不该被使用。
        tool_spec = spec.require_tool(tool)
        session = await self._session_for(agent_scope, spec)
        return await session.call_tool(tool_spec, dict(arguments or {}))

    async def close(self) -> None:
        """关闭全部连接。重复调用安全。"""
        self._closed = True
        sessions = list(self._sessions.values())
        self._sessions.clear()
        self._activated.clear()
        for session in sessions:
            await session.close()
