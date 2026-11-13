"""显式启用的独立 Core Runtime，旧 CoreUnit 生命周期保持原样。"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from ghrah.protocol.runtime import (
    RuntimeCapability,
    RuntimeCommandRequest,
    RuntimeMeta,
    RuntimePrincipal,
    RuntimeProblem,
    RuntimeStandardProblem,
    RuntimeSystemStatus,
)
from ghrah.protocol.runtime import (
    RuntimeDispatchResponse as DispatchResponse,
)
from ghrah.protocol.types import (
    ListAgentsPayload,
    SendMessagePayload,
    SpawnAgentPayload,
    TerminateAgentPayload,
)
from pydantic import ValidationError

from ghrah.ability.access import AbilityAccessStore, GrantPolicy, GrantRule
from ghrah.core.lifecycle import RuntimePhase, RuntimeStatus
from ghrah.core.runtime_agent_api import ResourceError, RuntimeAgentAPI
from ghrah.core.runtime_resources import RuntimeResources
from ghrah.core.unit import CoreUnit, CoreUnitConfig

logger = logging.getLogger(__name__)
_COMMAND_PAYLOADS = {
    "spawn_agent": SpawnAgentPayload,
    "list_agents": ListAgentsPayload,
    "send_message": SendMessagePayload,
    "terminate_agent": TerminateAgentPayload,
}


@dataclass(frozen=True)
class RuntimeConfig:
    """新入口的独立配置，路径和作用域由部署者显式提供。"""

    scope_ref: str
    database_path: str
    init_timeout: float = 60.0
    stop_timeout: float = 30.0
    ability_source: str = "builtin"
    enabled_commands: tuple[str, ...] = ()
    enabled_agent_api: bool = False
    config_quota: int = 8 * 1024 * 1024


class CoreRuntime:
    """唯一拥有新路径资源与生命周期，不依赖 Subject Context。"""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        llm_factory: Callable[[Any], Any] | None = None,
        unit_config: CoreUnitConfig | None = None,
    ) -> None:
        """保存配置，构造时不打开数据库、不创建后台任务。"""
        self.config = config
        self._unit_config = unit_config or CoreUnitConfig()
        self._llm_factory = (
            llm_factory if llm_factory is not None else self._unit_config.llm_factory
        )
        self._ability_store: AbilityAccessStore | None = None
        self._command_tasks: set[asyncio.Task[Any]] = set()
        self._status = RuntimeStatus("booting", uuid4().hex, self._now())
        self._resources = RuntimeResources()
        self._acquired = False
        self._unit: CoreUnit | None = None
        self._agent_api: RuntimeAgentAPI | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self._stop_task: asyncio.Task[None] | None = None
        self._stop_requested = False
        self._init_since: float | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def status(self) -> RuntimeStatus:
        """返回不可变的内部状态快照，不执行 IO。"""
        return self._status

    def _phase(self, phase: RuntimePhase, error: str | None = None) -> None:
        self._status = RuntimeStatus(phase, self._status.boot_id, self._now(), error)
        elapsed = time.monotonic() - self._init_since if self._init_since is not None else 0
        logger.info(
            "Core Runtime phase=%s boot_id=%s elapsed=%.3fs", phase, self._status.boot_id, elapsed
        )

    async def init(self) -> None:
        """有界且幂等地准备资源；失败后须新建实例。"""
        async with self._lock:
            if self._status.ready:
                return
            if self._started or self._stop_task is not None:
                raise RuntimeError("Runtime initialization cannot restart; create a new instance")
            self._started = True
            self._init_since = time.monotonic()
            self._phase("booting")
            try:
                RuntimePrincipal(scope_ref=self.config.scope_ref)
                if not Path(self.config.database_path).is_absolute():
                    raise ValueError("database_path must be an explicit absolute path")
                for value in (self.config.init_timeout, self.config.stop_timeout):
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError("Runtime timeouts must be positive and finite")
                if set(self.config.enabled_commands) - _COMMAND_PAYLOADS.keys():
                    raise ValueError("Unsupported enabled_commands")
                if self.config.enabled_agent_api and self.config.ability_source != "builtin":
                    raise ValueError("Agent API requires explicit builtin ability_source")
                if type(self.config.config_quota) is not int or self.config.config_quota <= 0:
                    raise ValueError("config_quota must be a positive integer")
                async with asyncio.timeout(self.config.init_timeout):
                    await self._initialize()
                    manager = self._unit.supervisor.message_manager
                    await manager.prepare_dispatch()
                    if not self._stop_requested:
                        manager.start_dispatcher()
                        self._phase("ready")
            except BaseException as exc:
                self._phase("failed", str(exc) or type(exc).__name__)
                try:
                    await self._cleanup()
                except BaseException as cleanup_error:
                    exc.add_note(f"Runtime cleanup failed: {cleanup_error}")
                    self._phase("failed", f"{self._status.error}; cleanup failed: {cleanup_error}")
                raise

    async def _initialize(self) -> None:
        manager = await self._resources.acquire(self.config.scope_ref, self.config.database_path)
        self._acquired = True
        options = self._unit_config
        if options.project_id and options.project_id != self.config.scope_ref:
            raise ValueError("CoreUnit project does not match Runtime scope")
        if options.message_manager is not None or options.persistence_factory is not None:
            raise ValueError("Runtime owns message and context persistence resources")
        if (
            options.ability_store is not None
            and options.ability_store.project_id != self.config.scope_ref
        ):
            raise ValueError("Ability store scope does not match Runtime scope")
        if (
            self.config.enabled_agent_api or "spawn_agent" in self.config.enabled_commands
        ) and options.ability_store is None:
            self._ability_store = await AbilityAccessStore.open(
                self.config.database_path,
                self.config.scope_ref,
                operators=frozenset({"deployment"}),
                runs=lambda agent: (
                    self._unit.supervisor.incarnation_for_agent(agent)
                    if self._unit is not None
                    else None
                ),
            )
            # 本入口只承诺已显式请求的只读 session_info 子集，策略不授予其他业务成员。
            options = replace(
                options,
                ability_store=self._ability_store,
                ability_grant_policy=options.ability_grant_policy
                or GrantPolicy(
                    mode="replace",
                    rules=(GrantRule(package="ghrah.core", operation="session_info"),),
                ),
            )
        self._unit = CoreUnit(
            replace(
                options,
                project_id=self.config.scope_ref,
                message_manager=manager,
                persistence_factory=lambda _: RuntimeResources.persistence(
                    self.config.database_path
                ),
                ability_source=self.config.ability_source,
                llm_factory=self._llm_factory,
            )
        )
        await self._unit.init(None)
        await self._unit.start()
        if self.config.enabled_agent_api:
            self._agent_api = RuntimeAgentAPI(
                database_path=self.config.database_path,
                scope_ref=self.config.scope_ref,
                supervisor=self._unit.supervisor,
                config_quota=self.config.config_quota,
                boot_id=self._status.boot_id,
                ability_store=options.ability_store,
            )
            await self._agent_api.open()

    async def _cleanup(self) -> None:
        try:
            if self._unit is not None:
                await self._unit.stop()
        finally:
            try:
                if self._agent_api is not None:
                    await self._agent_api.close()
            finally:
                try:
                    if self._ability_store is not None:
                        await self._ability_store.close()
                        self._ability_store = None
                finally:
                    if self._acquired:
                        self._acquired = False
                        await self._resources.release(self.config.scope_ref)

    async def stop(self) -> None:
        """幂等释放新入口的资源，宿主取消不会遗留无主写者。"""
        if self._stop_task is None:
            self._stop_requested = True
            if self._status.phase != "failed":
                self._phase("stopping", self._status.error)
            self._stop_task = asyncio.create_task(self._stop())
        try:
            await asyncio.shield(self._stop_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._stop_task)
            raise

    async def _stop(self) -> None:
        async with self._lock:
            if self._status.phase == "failed":
                await self._cleanup()
                return
            self._phase("stopping", self._status.error)
            try:
                try:
                    deadline = asyncio.get_running_loop().time() + self.config.stop_timeout
                    if self._command_tasks:
                        _, pending = await asyncio.wait(
                            tuple(self._command_tasks), timeout=self.config.stop_timeout
                        )
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                    if self._unit is not None and self._acquired:
                        agents = await self._unit.supervisor.list_agents()
                        await self._unit.supervisor.message_manager.drain(
                            [agent["agent_id"] for agent in agents],
                            timeout=max(0, deadline - asyncio.get_running_loop().time()),
                        )
                finally:
                    await self._cleanup()
            except BaseException as exc:
                self._phase("failed", str(exc) or type(exc).__name__)
                raise
            self._phase("stopped", self._status.error)

    @staticmethod
    def _problem(
        status: int,
        code: str | None,
        path: str,
        detail: str,
        details: dict[str, Any] | None = None,
    ) -> DispatchResponse:
        fields: dict[str, Any] = {
            "type": f"https://ghrah.org/problems/{code.lower()}" if code else "about:blank",
            "title": code
            or {
                405: "Method Not Allowed",
                413: "Payload Too Large",
                428: "Precondition Required",
            }[status],
            "status": status,
            "detail": detail,
            "instance": path,
            "retryable": status in {410, 429, 503} or code == "BUSY",
        }
        if details is not None:
            fields["details"] = details
        if code is None:
            problem = RuntimeStandardProblem.model_validate(fields)
        else:
            fields["code"] = code
            problem = RuntimeProblem.model_validate(fields)
        return DispatchResponse(
            status, {"Content-Type": "application/problem+json"}, problem.model_dump(mode="json")
        )

    async def _dispatch_command(
        self, method: str, path: str, body: dict[str, Any] | None, principal: RuntimePrincipal
    ) -> DispatchResponse:
        if not self.config.enabled_commands:
            return self._problem(404, "NOT_FOUND", path, "Runtime commands are disabled")
        if self._stop_requested or not self._status.ready:
            return self._problem(503, "UNAVAILABLE", path, "Runtime is not ready")
        if method != "POST":
            response = self._problem(405, None, path, "Only POST is allowed")
            return DispatchResponse(
                response.status, {**response.headers, "Allow": "POST"}, response.body
            )
        try:
            request = RuntimeCommandRequest.model_validate(body)
            if request.command not in self.config.enabled_commands:
                return self._problem(403, "ACCESS_DENIED", path, "Command is not enabled")
            payload = _COMMAND_PAYLOADS[request.command].model_validate(request.payload)
        except ValidationError as exc:
            return self._problem(422, "INVALID_ARGUMENT", path, str(exc))
        if payload.project_id != principal.scope_ref:
            return self._problem(403, "ACCESS_DENIED", path, "Project does not match scope")
        async with self._lock:
            if self._stop_requested or not self._status.ready:
                return self._problem(503, "UNAVAILABLE", path, "Runtime is not ready")
            task = asyncio.create_task(
                self._unit.handle_command(request.command, payload.model_dump(mode="json"), None)
            )
            self._command_tasks.add(task)
            task.add_done_callback(self._command_tasks.discard)
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            if self._stop_requested and task.cancelled():
                return self._problem(503, "UNAVAILABLE", path, "Command interrupted during stop")
            raise
        return DispatchResponse(200, {"Content-Type": "application/json"}, result)

    async def _dispatch_agent_api(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None,
        body: Any,
    ) -> DispatchResponse:
        if not self.config.enabled_agent_api:
            return self._problem(404, "NOT_FOUND", path, "Agent API is disabled")
        if self._stop_requested or not self._status.ready:
            return self._problem(503, "UNAVAILABLE", path, "Runtime is not ready")

        async def execute() -> DispatchResponse:
            try:
                return await self._agent_api.dispatch(method, path, headers=headers, body=body)
            except ResourceError as exc:
                details = {key: value for key, value in exc.details.items() if key != "allow"}
                result = self._problem(exc.status, exc.code, path, str(exc), details or None)
                if "allow" in exc.details:
                    return DispatchResponse(
                        result.status,
                        {**result.headers, "Allow": exc.details["allow"]},
                        result.body,
                    )
                return result
            except ValidationError, ValueError, TypeError, UnicodeError:
                return self._problem(
                    422, "INVALID_ARGUMENT", path, "Request does not match the resource contract"
                )
            except Exception:
                logger.exception("Agent resource request failed: %s %s", method, path)
                return self._problem(500, "INTERNAL", path, "Resource operation failed")

        async with self._lock:
            if self._stop_requested or not self._status.ready:
                return self._problem(503, "UNAVAILABLE", path, "Runtime is not ready")
            task = asyncio.create_task(execute())
            self._command_tasks.add(task)
            task.add_done_callback(self._command_tasks.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if self._stop_requested and task.cancelled():
                return self._problem(
                    503, "UNAVAILABLE", path, "Resource request interrupted during stop"
                )
            raise

    async def dispatch(
        self,
        method: str,
        path: str,
        *,
        principal: RuntimePrincipal,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> DispatchResponse:
        """派发生命周期查询与显式启用的既有业务命令。"""
        if principal.scope_ref != self.config.scope_ref:
            return self._problem(403, "ACCESS_DENIED", path, "Scope does not match")
        resource_path = urlsplit(path).path
        if resource_path.startswith(
            ("/v1/agents", "/v1/deliveries", "/v1/nodes", "/v1/abilities", "/v1/ability-instances")
        ):
            return await self._dispatch_agent_api(method, path, headers, body)
        if resource_path == "/v1/commands":
            return await self._dispatch_command(method, path, body, principal)
        if path not in {"/v1/status", "/v1/meta"}:
            if not self._status.ready and method != "GET":
                return self._problem(503, "UNAVAILABLE", path, "Runtime is not ready")
            return self._problem(404, "NOT_FOUND", path, "Resource is not implemented")
        if method != "GET":
            response = self._problem(405, None, path, "Only GET is allowed")
            return DispatchResponse(
                response.status, {**response.headers, "Allow": "GET"}, response.body
            )
        if path == "/v1/meta":
            body = RuntimeMeta(
                capabilities=[RuntimeCapability(name="runtime.lifecycle", version=1)]
                + (
                    [RuntimeCapability(name="runtime.commands", version=1)]
                    if self.config.enabled_commands
                    else []
                )
                + (
                    [
                        RuntimeCapability(name="runtime.agents.conversation", version=1),
                        RuntimeCapability(name="runtime.timeline", version=1),
                        RuntimeCapability(name="runtime.agents.management", version=1),
                        RuntimeCapability(name="runtime.abilities.management", version=1),
                    ]
                    if self.config.enabled_agent_api
                    else []
                ),
                server_time=self._now(),
            ).model_dump(mode="json")
        else:
            error = None
            if self._status.phase == "failed":
                error = RuntimeProblem.model_validate(
                    self._problem(
                        503, "UNAVAILABLE", path, self._status.error or "Initialization failed"
                    ).body
                )
            body = RuntimeSystemStatus(
                phase=self._status.phase,
                ready=self._status.ready,
                boot_id=self._status.boot_id,
                since=self._status.since,
                error=error,
            ).model_dump(mode="json")
        return DispatchResponse(200, {"Content-Type": "application/json"}, body)
