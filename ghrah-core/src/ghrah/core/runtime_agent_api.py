"""显式进程内资源接口，复用 Supervisor 与 MessageManager 的唯一运行权威。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from ghrah.protocol.runtime import RuntimeDispatchResponse
from ghrah.protocol.runtime_agents import (
    RuntimeAgentCreate,
    RuntimeAgentResource,
    RuntimeAgentsPage,
    RuntimeAgentSummary,
    RuntimeAssemblyInput,
    RuntimeStateTransitionResult,
)
from ghrah.protocol.runtime_digest import assembly_digest, canonical_json, normalized_assembly
from ghrah.protocol.runtime_digest import runtime_digest as digest
from ghrah.protocol.runtime_resources import (
    RuntimeDeliveryView,
    RuntimeMessageAccepted,
    RuntimeMessageCreate,
    RuntimeStateTransition,
)

from ghrah.ability.source import BuiltinAbilitySource
from ghrah.communication.supervisor import SupervisorActor
from ghrah.core.config.builders import build_model_overrides_from_dict, build_window_from_dict
from ghrah.core.runtime_agent_config import ModelOptions, WindowOptions
from ghrah.core.runtime_agent_store import RuntimeAgentStore, runtime_now
from ghrah.core.runtime_read_api import RuntimeReadAPI
from ghrah.core.runtime_resource_ids import ResourceError, internal_id, wire_id
from ghrah.core.runtime_resources import RuntimeResources
from ghrah.types.config_types import AgentConfig

_JSON_HEADERS = {"Content-Type": "application/json"}


class RuntimeAgentAPI:
    """最小内置 conversation 资源链；其他装配显式拒绝，不隐式降级。"""

    def __init__(
        self,
        *,
        database_path: str,
        scope_ref: str,
        supervisor: SupervisorActor,
        config_quota: int,
        boot_id: str,
    ) -> None:
        """绑定已有 Core 服务，构造时不打开数据库。"""
        self.store = RuntimeAgentStore(database_path, scope_ref)
        self.supervisor = supervisor
        self.manager = supervisor.message_manager
        self.config_quota = config_quota
        self.boot_id = boot_id
        self._cursor_secret = secrets.token_bytes(32)
        self._source = BuiltinAbilitySource()
        self._lock = asyncio.Lock()
        self._changed_at: dict[str, str] = {}
        self._errors: dict[str, dict[str, Any]] = {}
        self._opened_at = runtime_now()
        self._read = RuntimeReadAPI(self.store, boot_id)

    async def open(self) -> None:
        """仅准备资源定义，不恢复运行 Agent。"""
        await self.store.open()

    async def close(self) -> None:
        """资源连接在运行任务退出后关闭。"""
        await self.store.close()

    @staticmethod
    def _key(headers: dict[str, str]) -> str:
        key = headers.get("idempotency-key", "")
        if not re.fullmatch(r"[\x21-\x7e]{1,128}", key):
            raise ResourceError(
                422, "INVALID_ARGUMENT", "A visible ASCII Idempotency-Key is required"
            )
        return key

    @staticmethod
    def _etag() -> str:
        # 本入口尚不提供配置 PATCH，配置 revision 始终为 1；状态变化不改配置 ETag。
        return '"rev-1"'

    def _supported(self, config: RuntimeAssemblyInput) -> None:
        request = config.ability_request
        surface = request.surface
        permissions = config.permission_request
        valid = (
            surface.mode == "auto"
            and not surface.static
            and surface.max_static is None
            and not request.mcp_refs
            and len(request.packages) == 1
            and request.packages[0].package == "ghrah.core"
            and request.packages[0].origin == "builtin"
            and not request.packages[0].whole_container
            and not request.packages[0].skills
            and len(request.packages[0].operations) == 1
            and request.packages[0].operations[0].key == "conversation"
            and request.packages[0].operations[0].visible
            and request.packages[0].operations[0].available
            and config.context_policy.overrides is None
            and not permissions.auto_approve
            and permissions.require_approval_by_default
            and not permissions.safe_extra_commands
            and permissions.safe_extra_sub_commands is None
        )
        if not valid:
            raise ResourceError(
                501,
                "UNSUPPORTED",
                "This capability supports explicit builtin ghrah.core/conversation, auto surface, "
                "supported model/window options, null context overrides and default permissions",
            )

        for options, model in (
            (config.model.model_overrides, ModelOptions),
            (config.context_policy.window, WindowOptions),
        ):
            if options is not None:
                unknown = options.keys() - model.model_fields.keys()
                if unknown:
                    raise ResourceError(
                        501,
                        "UNSUPPORTED",
                        "Unsupported configuration options",
                        fields=sorted(unknown),
                    )
                model.model_validate(options)

    async def _registered(self, agent_id: str) -> bool:
        return any(item["agent_id"] == agent_id for item in await self.supervisor.list_agents())

    async def _view(self, row: Any) -> dict[str, Any]:
        config = RuntimeAssemblyInput.model_validate_json(row["config"])
        running = await self._registered(row["agent_id"])
        manifest = self._source.load_ability("ghrah.core.conversation").model_dump(mode="json")
        tree_digest = digest({"operations": [manifest]})
        current = await (
            await self.store.connection().execute(
                "SELECT delivery_id, state, blocked_reason FROM mm_deliveries "
                "WHERE recipient_id=? AND state IN ('pending', 'processing') ORDER BY seq LIMIT 1",
                (row["agent_id"],),
            )
        ).fetchone()
        delivery = None
        if current is not None:
            observed = await self._delivery(wire_id("dlv", current["delivery_id"]))
            delivery = {"delivery_id": observed["delivery_id"], "state": observed["state"]}
        latest = await (
            await self.store.connection().execute(
                "SELECT MAX(COALESCE(d.updated_at, m.accepted_at)) FROM mm_deliveries d "
                "JOIN mm_messages m ON m.message_id=d.message_id WHERE d.recipient_id=?",
                (row["agent_id"],),
            )
        ).fetchone()
        delivery_time = (
            runtime_now(latest[0]) if latest is not None and latest[0] is not None else ""
        )
        return RuntimeAgentResource.model_validate(
            {
                "agent_id": row["agent_id"],
                "scope_ref": self.store.scope_ref,
                "external_ref": row["external_ref"],
                "config": config.model_dump(mode="json"),
                "state": (
                    "error"
                    if row["agent_id"] in self._errors
                    else "busy"
                    if running and current is not None and current["state"] == "processing"
                    else "idle"
                    if running
                    else "stopped"
                ),
                "incarnation_id": self.supervisor.incarnation_id(row["agent_id"])
                if running
                else None,
                "config_revision": 1,
                "assembly_digest": row["assembly_digest"],
                "resolved_digest": digest(
                    {"assembly_digest": row["assembly_digest"], "operations": [manifest]}
                ),
                "capability": {
                    "tree_revision": 1,
                    "tree_digest": tree_digest,
                    "pending_changes": 0,
                },
                "current_delivery": delivery,
                "last_error": self._errors.get(row["agent_id"]),
                "created_at": row["created_at"],
                "updated_at": max(
                    row["created_at"],
                    self._opened_at,
                    self._changed_at.get(row["agent_id"], ""),
                    delivery_time,
                ),
            }
        ).model_dump(mode="json")

    async def _create(self, body: Any, headers: dict[str, str]) -> RuntimeDispatchResponse:
        key = self._key(headers)
        if not isinstance(body, dict):
            raise ResourceError(422, "INVALID_ARGUMENT", "An AgentCreate object is required")
        # 全对象按 UTF-8 计配额；拒绝发生在持久化或 Agent 启动之前。
        encoded = canonical_json(body.get("config")).encode("utf-8")
        if len(encoded) > self.config_quota:
            raise ResourceError(413, None, "Agent config exceeds the configured byte quota")
        request = RuntimeAgentCreate.model_validate(body)
        self._supported(request.config)
        if request.state != "stopped":
            raise ResourceError(
                501, "UNSUPPORTED", "Create stopped, then explicitly PUT state=running"
            )
        config = normalized_assembly(request.config)
        request_digest = digest(
            {"external_ref": request.external_ref, "config": config, "state": request.state}
        )
        previous = await self.store.receipt(key)
        if previous is not None:
            if previous["request_digest"] != request_digest:
                raise ResourceError(
                    409, "CONFLICT", "Idempotency-Key was used for a different request"
                )
            return RuntimeDispatchResponse(
                201,
                {**json.loads(previous["headers"]), "Idempotency-Replayed": "true"},
                json.loads(previous["body"]),
            )
        if await self.store.by_external_ref(request.external_ref) is not None:
            raise ResourceError(409, "CONFLICT", "external_ref already exists in this scope")
        record = {
            "agent_id": wire_id("agt", uuid4().hex),
            "external_ref": request.external_ref,
            "config": config,
            "assembly_digest": assembly_digest(request.config),
            "created_at": runtime_now(),
        }
        # _view 也用于未提交定义，其配置表示在内存中转换为与存储行同形。
        view = await self._view({**record, "config": json.dumps(config, ensure_ascii=False)})
        result_headers = {
            **_JSON_HEADERS,
            "Location": f"/v1/agents/{record['agent_id']}",
            "ETag": self._etag(),
        }
        await self.store.create(
            record, key=key, request_digest=request_digest, body=view, headers=result_headers
        )
        return RuntimeDispatchResponse(201, result_headers, view)

    async def _state(self, row: Any, body: Any, headers: dict[str, str]) -> dict[str, Any]:
        transition = RuntimeStateTransition.model_validate(body)
        if "if-match" not in headers:
            raise ResourceError(428, None, "If-Match is required")
        if headers["if-match"] != self._etag():
            raise ResourceError(
                412,
                "PRECONDITION_FAILED",
                "Configuration revision differs",
                current_etag=self._etag(),
            )
        agent_id = row["agent_id"]
        running = await self._registered(agent_id)
        recovered = None
        if transition.state == "running" and not running:
            config = RuntimeAssemblyInput.model_validate_json(row["config"])
            self._supported(config)
            task = asyncio.create_task(
                self.supervisor.spawn_agent(
                    AgentConfig(
                        name=agent_id,
                        agent_id=agent_id,
                        endpoint_name=config.model.endpoint_name,
                        model_overrides=(
                            build_model_overrides_from_dict(
                                ModelOptions.model_validate(
                                    config.model.model_overrides
                                ).model_dump()
                            )
                            if config.model.model_overrides is not None
                            else None
                        ),
                        window=(
                            build_window_from_dict(
                                WindowOptions.model_validate(
                                    config.context_policy.window
                                ).model_dump()
                            )
                            if config.context_policy.window is not None
                            else None
                        ),
                        system_prompt=config.prompt.system_prompt,
                        max_iterations=config.context_policy.max_iterations,
                        communication_timeout=config.context_policy.communication_timeout,
                    ),
                    abilities=[self._source.create_operation("ghrah.core.conversation")],
                    persistence_factory=lambda _: RuntimeResources.persistence(self.store.path),
                )
            )
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Supervisor 的构造/恢复阶段不是可取消事务，先等其归还控制再回收。
                await asyncio.gather(task, return_exceptions=True)
                if await self._registered(agent_id):
                    await self.supervisor.terminate_agent(agent_id)
                raise
            except Exception as exc:
                self._errors[agent_id] = {
                    "code": "INTERNAL",
                    "message": "Agent startup failed",
                    "at": runtime_now(),
                }
                self._changed_at[agent_id] = self._errors[agent_id]["at"]
                raise ResourceError(500, "INTERNAL", "Agent startup failed") from exc
            recovered = self.supervisor.recovery_mode(agent_id)
        elif transition.state == "stopped" and running:
            await self.supervisor.terminate_agent(agent_id)
        had_error = self._errors.pop(agent_id, None) is not None
        if running != (transition.state == "running") or had_error:
            self._changed_at[agent_id] = runtime_now()
        return RuntimeStateTransitionResult.model_validate(
            {
                "agent": await self._view(row),
                "recovered": recovered,
            }
        ).model_dump(mode="json")

    async def _message(
        self, row: Any, body: Any, headers: dict[str, str]
    ) -> RuntimeDispatchResponse:
        key = self._key(headers)
        request = RuntimeMessageCreate.model_validate(body)
        if request.kind != "user":
            raise ResourceError(501, "UNSUPPORTED", "Only user messages are implemented")
        if not request.text.strip():
            raise ResourceError(422, "INVALID_ARGUMENT", "Message text must not be blank")
        agent_id = row["agent_id"]
        sender, storage_key = "runtime-subject", f"runtime:{agent_id}:{key}"
        previous = await (
            await self.store.connection().execute(
                "SELECT message_id FROM mm_messages WHERE sender_id=? AND idempotency_key=?",
                (f"human:{sender}", storage_key),
            )
        ).fetchone()
        if previous is None and not await self._registered(agent_id):
            raise ResourceError(409, "INVALID_STATE", "Agent is stopped")
        try:
            # 元数据是 opaque 客户端数据，不得作为 execute_ability/room_ids 等内部控制字段。
            message = await self.manager.send_external(
                sender=sender,
                recipient_ids=[agent_id],
                content=request.text,
                idempotency_key=storage_key,
                metadata={"runtime_message": {"kind": request.kind, "metadata": request.metadata}},
            )
        except ValueError as exc:
            raise ResourceError(409, "CONFLICT", "Message idempotency payload differs") from exc
        delivery = (await self.manager.get_deliveries(message.message_id))[0]
        delivery_id = wire_id("dlv", delivery.delivery_id)
        url = f"/v1/deliveries/{delivery_id}"
        result_headers = {**_JSON_HEADERS, "Location": url}
        if previous is not None:
            result_headers["Idempotency-Replayed"] = "true"
        return RuntimeDispatchResponse(
            202,
            result_headers,
            RuntimeMessageAccepted(
                message_id=wire_id("msg", message.message_id),
                delivery_id=delivery_id,
                resource_url=url,
            ).model_dump(mode="json"),
        )

    async def _delivery(self, delivery_id: str) -> dict[str, Any]:
        raw = internal_id("dlv", delivery_id)
        row = await (
            await self.store.connection().execute(
                "SELECT d.recipient_id, d.updated_at, m.accepted_at FROM mm_deliveries d "
                "JOIN mm_messages m ON m.message_id=d.message_id "
                "JOIN runtime_agents a ON a.agent_id=d.recipient_id "
                "WHERE d.delivery_id=? AND a.scope_ref=?",
                (raw, self.store.scope_ref),
            )
        ).fetchone()
        if row is None:
            raise ResourceError(404, "NOT_FOUND", "Delivery does not exist")
        progress = await self.manager.get_progress(raw)
        item = progress.delivery
        state = "queued" if item.state.value == "pending" else item.state.value
        blocked_reason = "dependency" if item.blocked_reason else None
        if item.state.value == "pending":
            if await self.manager.get_queue_blocker(raw) is not None:
                state, blocked_reason = "blocked", "dependency"
            elif not await self._registered(item.recipient_id):
                state, blocked_reason = "blocked", "paused"
        # progress 的 branch/iteration 来自 Core checkpoint；不复制回复正文。
        return RuntimeDeliveryView(
            delivery_id=delivery_id,
            agent_id=item.recipient_id,
            message_id=wire_id("msg", item.message_id),
            state=state,
            branch_id=progress.branch_id,
            iteration=progress.iteration,
            blocked_reason=blocked_reason,
            last_node_id=wire_id("nod", item.last_node_id) if item.last_node_id else None,
            attempts=item.attempts,
            created_at=runtime_now(row["accepted_at"]),
            updated_at=runtime_now(row["updated_at"] or row["accepted_at"]),
        ).model_dump(mode="json")

    def _cursor(self, after: str) -> str:
        value = f"{self.boot_id}:{after}".encode()
        signature = hashlib.sha256(self._cursor_secret + value).hexdigest().encode()
        return base64.urlsafe_b64encode(value + b":" + signature).decode().rstrip("=")

    def _after(self, cursor: str) -> str:
        try:
            value = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            boot, agent, signature = value.decode().split(":")
            expected = hashlib.sha256(self._cursor_secret + f"{boot}:{agent}".encode()).hexdigest()
            if boot != self.boot_id or not secrets.compare_digest(signature, expected):
                raise ValueError("Expired cursor")
            internal_id("agt", agent)
            return agent
        except (ValueError, ResourceError) as exc:
            raise ResourceError(
                410, "CURSOR_EXPIRED", "Rebuild the collection from its first page"
            ) from exc

    async def _list(self, query: dict[str, list[str]]) -> dict[str, Any]:
        if set(query) - {"limit", "cursor"} or any(len(v) != 1 for v in query.values()):
            raise ResourceError(422, "INVALID_ARGUMENT", "Unknown or repeated page parameter")
        limit = int(query.get("limit", ["50"])[0])
        if not 1 <= limit <= 500:
            raise ResourceError(422, "INVALID_ARGUMENT", "limit must be between 1 and 500")
        after = self._after(query["cursor"][0]) if "cursor" in query else ""
        rows = await self.store.page(after, limit)
        items = []
        for row in rows[:limit]:
            view = await self._view(row)
            prompt = view.pop("config")["prompt"]["system_prompt"].encode("utf-8")
            items.append(
                RuntimeAgentSummary.model_validate(
                    {
                        **view,
                        "etag": self._etag(),
                        "prompt": {
                            "digest": "sha256:" + hashlib.sha256(prompt).hexdigest(),
                            "size": len(prompt),
                        },
                    }
                )
            )
        cursor = self._cursor(rows[limit - 1]["agent_id"]) if len(rows) > limit else None
        return RuntimeAgentsPage(
            items=items, next_cursor=cursor, has_more=cursor is not None
        ).model_dump(mode="json")

    async def dispatch(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None,
        body: Any,
    ) -> RuntimeDispatchResponse:
        """按资源方法派发；错误交给外层统一编码为 Problem。"""
        parts = urlsplit(path)
        route, query = parts.path, parse_qs(parts.query, keep_blank_values=True)
        allowed = None
        match = re.fullmatch(r"/v1/agents/([^/]+)(/state|/messages|/timeline)?", route)
        node_match = re.fullmatch(r"/v1/nodes/([^/]+)", route)
        delivery_match = re.fullmatch(r"/v1/deliveries/([^/]+)", route)
        if route == "/v1/agents":
            allowed = "GET, POST"
        elif match:
            allowed = {None: "GET", "/state": "PUT", "/messages": "POST", "/timeline": "GET"}[
                match[2]
            ]
        elif delivery_match or node_match:
            allowed = "GET"
        if allowed is None:
            raise ResourceError(404, "NOT_FOUND", "Resource is not implemented")
        if method not in allowed.split(", "):
            raise ResourceError(405, None, "Method is not allowed", allow=allowed)
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        # 长轮询不占用资源变更锁，避免阻止 stop 或后续消息受理。
        if delivery_match:
            if set(query) - {"wait"} or any(len(v) != 1 for v in query.values()):
                raise ResourceError(422, "INVALID_ARGUMENT", "Only one wait parameter is supported")
            wait = float(query.get("wait", ["0"])[0])
            if not 0 <= wait <= 30:
                raise ResourceError(422, "INVALID_ARGUMENT", "wait must be between 0 and 30")
            deadline = asyncio.get_running_loop().time() + wait
            while True:
                async with self._lock:
                    view = await self._delivery(delivery_match[1])
                if (
                    view["state"] in {"completed", "failed", "interrupted", "blocked"}
                    or asyncio.get_running_loop().time() >= deadline
                ):
                    return RuntimeDispatchResponse(200, dict(_JSON_HEADERS), view)
                await asyncio.sleep(min(0.05, max(0, deadline - asyncio.get_running_loop().time())))
        async with self._lock:
            if node_match:
                return await self._read.node(node_match[1], query, headers)
            if match and match[2] == "/timeline":
                internal_id("agt", match[1])
                if await self.store.get(match[1]) is None:
                    raise ResourceError(404, "NOT_FOUND", "Agent does not exist")
                return await self._read.timeline(match[1], query)
            if route == "/v1/agents" and method == "GET":
                return RuntimeDispatchResponse(200, dict(_JSON_HEADERS), await self._list(query))
            if query:
                raise ResourceError(
                    422, "INVALID_ARGUMENT", "Query parameters are not supported here"
                )
            if route == "/v1/agents":
                return await self._create(body, headers)
            internal_id("agt", match[1])
            row = await self.store.get(match[1])
            if row is None:
                raise ResourceError(404, "NOT_FOUND", "Agent does not exist")
            if match[2] == "/messages":
                return await self._message(row, body, headers)
            view = (
                await self._state(row, body, headers)
                if match[2] == "/state"
                else await self._view(row)
            )
            return RuntimeDispatchResponse(200, {**_JSON_HEADERS, "ETag": self._etag()}, view)
