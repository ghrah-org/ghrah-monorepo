"""新 Runtime 的显式接入，不参与旧 Subject 装配或恢复流程。"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from ghrah.protocol.runtime import (
    RuntimeCommand,
    RuntimeCommandRequest,
    RuntimeDispatchResponse,
    RuntimeMeta,
    RuntimePrincipal,
    RuntimeSystemStatus,
)
from ghrah.protocol.types import SendMessagePayload, SpawnAgentPayload


class RuntimeTransport(Protocol):
    """客户端仅依赖版本化 dispatch 契约。"""

    async def dispatch(
        self,
        method: str,
        path: str,
        *,
        principal: RuntimePrincipal,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> RuntimeDispatchResponse:
        """以显式主体调用资源。"""
        ...


class OwnedRuntime(RuntimeTransport, Protocol):
    """仅受管接入层使用的生命周期端口。"""

    async def init(self) -> None:
        """初始化受管实例。"""
        ...

    async def stop(self) -> None:
        """释放受管实例。"""
        ...


class RuntimeClient:
    """新 Subject 通过契约等待就绪并调用业务，不读取 Core 私有对象或存储。"""

    def __init__(self, transport: RuntimeTransport, *, scope_ref: str) -> None:
        """绑定受信作用域，不能由外部请求体覆盖。"""
        self._transport = transport
        self._principal = RuntimePrincipal(scope_ref=scope_ref)

    async def get(self, path: str) -> RuntimeDispatchResponse:
        """查询资源并保留原始错误状态和正文。"""
        return await self._transport.dispatch("GET", path, principal=self._principal)

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> RuntimeDispatchResponse:
        """调用 Runtime 接口，作用域由客户端固定。"""
        return await self._transport.dispatch(
            method, path, principal=self._principal, headers=headers, body=body
        )

    async def create_agent(
        self,
        body: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> RuntimeDispatchResponse:
        """按资源契约创建定义；保留 201、Location 和 ETag。"""
        return await self.request(
            "POST", "/v1/agents", headers={"Idempotency-Key": idempotency_key}, body=body
        )

    async def get_agent(self, agent_id: str) -> RuntimeDispatchResponse:
        """读取完整 Agent 资源。"""
        return await self.get(f"/v1/agents/{agent_id}")

    async def list_agent_resources(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> RuntimeDispatchResponse:
        """读取摘要页；与旧命令 list_agents 的回执保持区分。"""
        from urllib.parse import urlencode

        query = {"limit": str(limit)}
        if cursor is not None:
            query["cursor"] = cursor
        return await self.get("/v1/agents?" + urlencode(query))

    async def set_agent_state(
        self,
        agent_id: str,
        state: str,
        *,
        etag: str,
    ) -> RuntimeDispatchResponse:
        """使用配置 ETag 显式启停。"""
        return await self.request(
            "PUT", f"/v1/agents/{agent_id}/state", headers={"If-Match": etag}, body={"state": state}
        )

    async def submit_message(
        self,
        agent_id: str,
        text: str,
        *,
        idempotency_key: str,
        kind: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeDispatchResponse:
        """提交消息并取得 202 投递引用，不伪造同步回复。"""
        return await self.request(
            "POST",
            f"/v1/agents/{agent_id}/messages",
            headers={"Idempotency-Key": idempotency_key},
            body={"text": text, "kind": kind, "metadata": metadata or {}},
        )

    async def get_delivery(self, delivery_id: str, *, wait: float = 0) -> RuntimeDispatchResponse:
        """查询 Core 权威投递状态，可显式长轮询至多 30 秒。"""
        return await self.get(f"/v1/deliveries/{delivery_id}?wait={wait}")

    async def get_timeline(
        self,
        agent_id: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
        session_id: str | None = None,
        branch_id: str | None = None,
    ) -> RuntimeDispatchResponse:
        """读取 Core 轨迹摘要页，不依赖本地 sqlite 或事件投影。"""
        params: dict[str, str | int] = {"limit": limit}
        for key, value in (
            ("cursor", cursor),
            ("session_id", session_id),
            ("branch_id", branch_id),
        ):
            if value is not None:
                params[key] = value
        return await self.get(f"/v1/agents/{agent_id}/timeline?{urlencode(params)}")

    async def get_node(
        self,
        node_id: str,
        *,
        schema_version: int = 1,
        etag: str | None = None,
    ) -> RuntimeDispatchResponse:
        """按声明的 payload 版本读取详情，支持 ETag 条件读取。"""
        return await self.request(
            "GET",
            f"/v1/nodes/{node_id}?schema_version={schema_version}",
            headers={"If-None-Match": etag} if etag is not None else None,
        )

    async def command(self, command: RuntimeCommand, payload: dict[str, Any]) -> dict[str, Any]:
        """复用旧命令 DTO 与回执，调用失败不重试，也不复制运行事实。"""
        request = RuntimeCommandRequest(command=command, payload=payload)
        response = await self.request("POST", "/v1/commands", body=request.model_dump(mode="json"))
        if response.status != 200 or response.body is None:
            raise RuntimeError(
                f"Runtime command request failed ({response.status}): {response.body}"
            )
        return response.body

    async def spawn_agent(self, payload: SpawnAgentPayload) -> dict[str, Any]:
        """显式启动 Agent，沿用既有 SpawnAgentPayload。"""
        return await self.command("spawn_agent", payload.model_dump(mode="json"))

    async def list_agents(self) -> list[dict[str, Any]]:
        """查询 Core 当前运行事实，不读 Subject sqlite 或资源定义表。"""
        result = await self.command("list_agents", {"project_id": self._principal.scope_ref})
        if not result.get("success"):
            raise RuntimeError(f"Agent listing failed: {result}")
        return result["data"]["agents"]

    async def send_message(self, payload: SendMessagePayload) -> dict[str, Any]:
        """沿用既有消息调用和持久回复语义。"""
        return await self.command("send_message", payload.model_dump(mode="json"))

    async def terminate_agent(self, agent_id: str, agent_name: str) -> dict[str, Any]:
        """显式停止 Agent；Core 保持唯一运行事实。"""
        return await self.command(
            "terminate_agent",
            {
                "project_id": self._principal.scope_ref,
                "agent_id": agent_id,
                "name": agent_name,
            },
        )

    async def status(self) -> RuntimeSystemStatus:
        """读取权威状态，不启动 Runtime。"""
        response = await self.get("/v1/status")
        if response.status != 200:
            raise RuntimeError(f"Runtime status request failed: {response.body}")
        return RuntimeSystemStatus.model_validate(response.body)

    async def wait_ready(self, *, timeout: float, poll_interval: float = 0.05) -> None:
        """有界轮询就绪；失败或停止即退出，事件丢失不影响判断。"""
        for value in (timeout, poll_interval):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Ready timeout and poll interval must be positive and finite")
        async with asyncio.timeout(timeout):
            while True:
                response = await self.get("/v1/meta")
                if response.status != 200:
                    raise RuntimeError(f"Runtime meta request failed: {response.body}")
                meta = RuntimeMeta.model_validate(response.body)
                if not any(
                    c.name == "runtime.lifecycle" and c.version == 1 for c in meta.capabilities
                ):
                    raise RuntimeError("Runtime lifecycle v1 capability is required")
                state = await self.status()
                if state.phase == "ready" and state.ready:
                    return
                if state.phase != "booting":
                    raise RuntimeError(f"Runtime {state.phase}: {state.error}")
                await asyncio.sleep(poll_interval)


@dataclass(frozen=True)
class RuntimeConnectionConfig:
    """新配置默认关闭，独立于旧 SubjectConfig.core 和 recovery。"""

    enabled: bool = False
    scope_ref: str = ""
    database_path: str = ""
    init_timeout: float = 60.0
    ready_timeout: float = 65.0
    stop_timeout: float = 30.0
    ability_source: str = "builtin"
    enabled_commands: tuple[str, ...] = ()
    enabled_agent_api: bool = False
    config_quota: int = 8 * 1024 * 1024


class RuntimeConnection:
    """由新入口显式持有的运行时连接，不修改旧 Context/service key。"""

    def __init__(
        self, config: RuntimeConnectionConfig, runtime: OwnedRuntime | None = None
    ) -> None:
        """构造时不启动 Runtime；注入实例也必须显式启用。"""
        self.config = config
        self._runtime = runtime
        self.client: RuntimeClient | None = None
        self._lock = asyncio.Lock()
        self._connected = False
        self._closed = False
        self._attempted = False

    async def start(self) -> None:
        """启动新受管实例并等待就绪；禁用时不产生副作用。"""
        if not self.config.enabled:
            return
        async with self._lock:
            if self._connected:
                return
            if self._closed:
                raise RuntimeError("Connection is closed; create a new instance")
            if not math.isfinite(self.config.ready_timeout) or self.config.ready_timeout <= 0:
                raise ValueError("ready_timeout must be positive and finite")
            RuntimePrincipal(scope_ref=self.config.scope_ref)
            if self.config.ready_timeout < self.config.init_timeout:
                raise ValueError("ready_timeout must be >= init_timeout")
            if self._runtime is None:
                from ghrah.core.runtime import CoreRuntime, RuntimeConfig

                self._runtime = CoreRuntime(
                    RuntimeConfig(
                        scope_ref=self.config.scope_ref,
                        database_path=self.config.database_path,
                        init_timeout=self.config.init_timeout,
                        stop_timeout=self.config.stop_timeout,
                        ability_source=self.config.ability_source,
                        enabled_commands=self.config.enabled_commands,
                        enabled_agent_api=self.config.enabled_agent_api,
                        config_quota=self.config.config_quota,
                    )
                )
            self.client = RuntimeClient(self._runtime, scope_ref=self.config.scope_ref)
            self._attempted = True
            task = asyncio.create_task(self._runtime.init())
            try:
                await self.client.wait_ready(timeout=self.config.ready_timeout)
                await task
                self._connected = True
            except BaseException as exc:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                try:
                    await self._runtime.stop()
                except BaseException as cleanup_error:
                    exc.add_note(f"Owned runtime cleanup failed: {cleanup_error}")
                self._closed = True
                raise

    async def stop(self) -> None:
        """停止并回收本连接显式持有的 Core 实例。"""
        async with self._lock:
            if self._attempted and not self._closed:
                if self._runtime is not None:
                    await self._runtime.stop()
                self._closed = True
                self._connected = False
