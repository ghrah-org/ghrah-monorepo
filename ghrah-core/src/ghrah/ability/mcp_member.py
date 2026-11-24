# SPDX-License-Identifier: Apache-2.0
"""成员驱动的 MCP 执行：连接按成员冻结的 server/tool 解析，不再依赖部署绑定。

页面安装的 MCP Ability 把 ``server`` / ``tool`` / ``read_only`` 写进每个成员；
运行期只需要一份受信配置（agentconf）和一个按 run 隔离的运行时。旧的包级绑定
层已退役，本模块是唯一执行入口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ghrah.mcp.agentconf_source import AgentconfMcpSource
from ghrah.mcp.errors import McpConfigError, McpNotActivatedError
from ghrah.mcp.runtime import McpRuntime
from ghrah.mcp.spec import McpToolSpec

__all__ = [
    "McpMemberDeployment",
    "McpRuntimeConfig",
    "MemberMcpService",
]


@dataclass(frozen=True)
class McpRuntimeConfig:
    """成员驱动 MCP 的部署配置；只由受信宿主注入。

    Attributes:
        enabled: 是否启用 MCP 成员调用；关闭时成员显示不可用。
        allowed_servers: 允许连接的 agentconf 服务名；``None`` 表示不限制。
        source: 可选的受信配置源；缺省由部署自建并在关闭时释放。
        connector: 测试/宿主注入的连接器；生产留空。
    """

    enabled: bool = False
    allowed_servers: tuple[str, ...] | None = None
    db_path: str | None = None
    source: Any = field(default=None, repr=False, compare=False)
    connector: Any = field(default=None, repr=False, compare=False)


class McpMemberDeployment:
    """一个 CoreUnit 持有的 MCP 运行时；每个 Agent 一份服务，不共享 run。"""

    def __init__(self, config: McpRuntimeConfig, store: Any) -> None:
        self.config = config
        self.store = store
        self.source = (
            config.source
            if config.source is not None
            else AgentconfMcpSource(db_path=config.db_path)
        )
        self._owned_source = config.source is None
        self._services: set[MemberMcpService] = set()
        self._closed = False
        if config.enabled:
            self.store.register_member_backend(self, self.member_available)

    def member_available(self, agent: str, reference: Any) -> bool:
        """成员是否可能被本部署调用；具体连接在调用期解析。"""
        del agent, reference
        return not self._closed and self.config.enabled

    def for_agent(self, agent_id: str) -> MemberMcpService:
        """为一次装配创建空活跃集合；不联网、不读取凭据。"""
        if self._closed:
            raise McpConfigError("MCP deployment is closed")
        service = MemberMcpService(self, agent_id)
        self._services.add(service)
        return service

    async def refresh_agent(self, agent: str, run: str) -> None:
        """成员驱动的连接按调用更新；这里只保留接线点。"""
        del agent, run

    async def close(self) -> None:
        """先停止接纳，再关闭所有 Agent 的会话与自建配置源。"""
        self._closed = True
        self.store.unregister_member_backend(self)
        services = tuple(self._services)
        for service in services:
            service.stop_admission()
        try:
            for service in services:
                await service.close()
        finally:
            if self._owned_source:
                self.source.close()


class MemberMcpService:
    """一个 Agent/run 的 MCP 调用端口；连接由成员字段决定。"""

    def __init__(self, deployment: McpMemberDeployment, agent_id: str) -> None:
        self._deployment = deployment
        self._agent_id = agent_id
        self._run = ""
        self._accepting = True
        self._closed = False
        self._runtime = McpRuntime(
            connector=deployment.config.connector,
            credentials_for_spec=self._credential,
            headers_for_spec=self._headers,
        )

    @property
    def scope(self) -> str:
        """无分隔歧义的 agent/run scope。"""
        return f"{self._agent_id}/{self._run}"

    def bind_run(self, run: str) -> None:
        """只绑定一次当前运行代次；旧对象不能借用新 run。"""
        if self._closed or not run or (self._run and self._run != run):
            raise McpConfigError("Cannot bind MCP service to another run")
        self._run = run

    def stop_admission(self) -> None:
        """同步关闭新调用接纳；已发出的调用仍可完成或由 close 结束。"""
        self._accepting = False

    def _credential(self, spec: Any) -> str | None:
        return self._deployment.source.credential_for_spec(spec)

    def _headers(self, spec: Any) -> dict[str, str] | None:
        return self._deployment.source.headers_for_spec(spec)

    def _ensure_active(self, server: str) -> None:
        if self._closed or not self._accepting:
            raise McpNotActivatedError("MCP service is not active")
        if not self._run:
            raise McpNotActivatedError("MCP service is not bound to a run")
        allowed = self._deployment.config.allowed_servers
        if allowed is not None and server not in allowed:
            raise McpConfigError("MCP server is not allowed for this deployment")

    async def call_member(
        self,
        *,
        server: str,
        tool: McpToolSpec,
        arguments: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> Any:
        """按成员冻结的 server/tool 调用；参数不能切换服务器。"""
        self._ensure_active(server)
        spec = self._deployment.source.connection_spec(
            server,
            skill_full_name=f"member/{self._agent_id}",
            tools=(),
        )
        runtime_server = spec.server
        previous = self._runtime.spec_for(agent_scope=self.scope, server=runtime_server)
        if previous is None:
            await self._runtime.activate(spec, agent_scope=self.scope)
        elif previous != spec:
            await self._runtime.deactivate_server(agent_scope=self.scope, server=runtime_server)
            await self._runtime.activate(spec, agent_scope=self.scope)
        return await self._runtime.call_tool(
            agent_scope=self.scope,
            server=runtime_server,
            tool_spec=tool,
            arguments=arguments,
            timeout_ms=timeout_ms,
        )

    async def refresh(self) -> None:
        """成员驱动没有“加载技能”步骤；保留内容端口的接线点。"""

    async def skill_loaded(self, reference: Any) -> None:
        """技能读取不激活 MCP；保留接口。"""
        del reference

    async def unload_skill(self, alias: str, member: str) -> None:
        """技能卸载不改变 MCP 连接；保留接口。"""
        del alias, member

    async def close(self) -> None:
        """关闭本 Agent/run 的连接；重复调用安全。"""
        if self._closed:
            return
        self._closed = True
        self._deployment._services.discard(self)
        await self._runtime.close()
