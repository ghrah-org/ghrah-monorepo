"""直接查询 Core 上下文权威的资源读面，不建立投影或读取时写回。"""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime
from typing import Any

from ghrah.protocol.runtime import RuntimeDispatchResponse
from ghrah.protocol.runtime_digest import canonical_json
from ghrah.protocol.runtime_resources import (
    RuntimeNodePayload,
    RuntimeNodeView,
    RuntimeTimelineEntry,
    RuntimeTimelinePage,
)

from ghrah.core.runtime_agent_store import RuntimeAgentStore, runtime_now
from ghrah.core.runtime_resource_ids import ResourceError, internal_id, wire_id

_JSON_HEADERS = {"Content-Type": "application/json"}


class RuntimeReadAPI:
    """只服务当前作用域已创建的资源 Agent，游标绑定启动实例和筛选条件。"""

    def __init__(self, store: RuntimeAgentStore, boot_id: str) -> None:
        """保存权威连接；随机密钥只用于验证游标，不持有业务事实。"""
        self.store, self.boot_id = store, boot_id
        self._secret = secrets.token_bytes(32)

    @staticmethod
    def _parameters(query: dict[str, list[str]], allowed: set[str]) -> dict[str, str]:
        if set(query) - allowed or any(len(values) != 1 for values in query.values()):
            raise ResourceError(422, "INVALID_ARGUMENT", "Unsupported or repeated query parameter")
        return {key: values[0] for key, values in query.items()}

    def _cursor(self, value: dict[str, Any]) -> str:
        data = canonical_json(value).encode()
        signature = hmac.digest(self._secret, data, "sha256")
        return base64.urlsafe_b64encode(signature + data).decode().rstrip("=")

    def _decode(self, cursor: str, identity: dict[str, Any]) -> dict[str, Any]:
        try:
            if len(cursor) > 4096:
                raise ValueError("Cursor too long")
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            signature, data = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.digest(self._secret, data, "sha256")):
                raise ValueError("Invalid cursor")
            value = json.loads(data)
            if any(value.get(key) != expected for key, expected in identity.items()):
                raise ValueError("Cursor belongs to a different snapshot")
            return value
        except (ValueError, TypeError, AttributeError) as exc:
            raise ResourceError(410, "CURSOR_EXPIRED", "Restart the timeline query") from exc

    @staticmethod
    def _payload(row: Any) -> tuple[RuntimeNodePayload, int]:
        metadata = json.loads(row["metadata"])

        def messages(column: str) -> list[dict[str, Any]] | None:
            value = json.loads(row[column]) if row[column] is not None else None
            if value is None:
                return None
            # v1 公开消息内容和来源，不暴露内部路由/投递元数据。
            return [
                {
                    key: message[key]
                    for key in ("role", "content_blocks", "source")
                    if key in message
                }
                for message in value
            ]

        payload = RuntimeNodePayload(
            messages_delta=messages("messages_delta"),
            messages_snapshot=messages("messages_snapshot"),
            action_results=json.loads(row["action_results"]),
            metadata={
                key: metadata[key]
                for key in ("node_kind", "compaction", "visible_response_retry_count")
                if key in metadata
            },
            usage=metadata.get("token_usage"),
        )
        return payload, len(canonical_json(payload.model_dump(mode="json")).encode("utf-8"))

    @staticmethod
    def _identity(row: Any) -> dict[str, Any]:
        metadata = json.loads(row["metadata"])
        kind = (
            "compaction"
            if metadata.get("node_kind") == "compact"
            else "snapshot"
            if row["is_snapshot"]
            else "iteration"
        )
        return {
            "node_id": wire_id("nod", row["id"]),
            "agent_id": wire_id("agt", row["agent_name"]),
            "session_id": row["session_id"],
            "branch_id": row["created_on_branch_id"],
            "parent_id": wire_id("nod", row["parent_id"]) if row["parent_id"] else None,
            "iteration": row["iteration"],
            "seq": row["runtime_node_seq"],
            "kind": kind,
            "created_at": runtime_now(datetime.fromisoformat(row["timestamp"]).timestamp()),
        }

    def _entry(self, row: Any) -> RuntimeTimelineEntry:
        payload, size = self._payload(row)
        text = ""
        for message in reversed(payload.messages_delta or payload.messages_snapshot or []):
            text = "".join(
                block.get("text", "")
                for block in message.get("content_blocks", [])
                if block.get("type") == "text"
            )
            if text:
                break
        outcomes = {
            item["action_result"]["outcome"]
            for item in payload.action_results or []
            if item.get("action_result")
        }
        outcome = (
            "success"
            if outcomes == {"success"}
            else "failure"
            if outcomes == {"failure"}
            else "partial"
            if outcomes
            else "unknown"
        )
        return RuntimeTimelineEntry(
            **self._identity(row),
            ability_names=json.loads(row["ability_names"]),
            outcome=outcome,
            summary={"text": text[:256], "truncated": len(text) > 256},
            payload_ref={"schema_version": 1, "size": size},
        )

    async def timeline(self, agent_id: str, query: dict[str, list[str]]) -> RuntimeDispatchResponse:
        """按 branch/node_seq 遍历固定提交水位，快照起点可重复读取。"""
        params = self._parameters(query, {"limit", "cursor", "session_id", "branch_id"})
        limit = int(params.get("limit", "100"))
        if not 1 <= limit <= 500:
            raise ResourceError(422, "INVALID_ARGUMENT", "limit must be between 1 and 500")
        identity = {
            "boot": self.boot_id,
            "agent": agent_id,
            "session": params.get("session_id"),
            "branch": params.get("branch_id"),
        }
        state = self._decode(params["cursor"], identity) if "cursor" in params else None
        db = self.store.connection()
        # 同一只读事务固定水位和首批节点。其他 Core 连接仍可提交 WAL。
        await db.execute("BEGIN")
        try:
            high = await (
                await db.execute(
                    "SELECT high_water FROM runtime_node_counters WHERE agent_id=?", (agent_id,)
                )
            ).fetchone()
            watermark = state["watermark"] if state else (high[0] if high else 0)
            after_branch, after_seq = (
                (state["after_branch"], state["after_seq"]) if state else ("", 0)
            )
            if after_seq:
                found = await (
                    await db.execute(
                        "SELECT 1 FROM nodes WHERE agent_name=? AND runtime_node_seq=? "
                        "AND created_on_branch_id=?",
                        (agent_id, after_seq, after_branch),
                    )
                ).fetchone()
                if found is None:
                    raise ResourceError(410, "CURSOR_EXPIRED", "Restart the timeline query")
            clauses = [
                "agent_name=?",
                "runtime_node_seq<=?",
                "runtime_node_seq IS NOT NULL",
                "(created_on_branch_id>? OR (created_on_branch_id=? AND runtime_node_seq>?))",
            ]
            args: list[Any] = [agent_id, watermark, after_branch, after_branch, after_seq]
            for name, column in (
                ("session_id", "session_id"),
                ("branch_id", "created_on_branch_id"),
            ):
                if name in params:
                    clauses.append(f"{column}=?")
                    args.append(params[name])
            rows = await (
                await db.execute(
                    "SELECT * FROM nodes WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY created_on_branch_id, runtime_node_seq LIMIT ?",
                    (*args, limit + 1),
                )
            ).fetchall()
        finally:
            await db.rollback()
        start = {**identity, "watermark": watermark, "after_branch": "", "after_seq": 0}
        cursor = None
        if len(rows) > limit:
            cursor = self._cursor(
                {
                    **start,
                    "after_branch": rows[limit - 1]["created_on_branch_id"],
                    "after_seq": rows[limit - 1]["runtime_node_seq"],
                }
            )
        page = RuntimeTimelinePage(
            items=[self._entry(row) for row in rows[:limit]],
            next_cursor=cursor,
            has_more=cursor is not None,
            snapshot_cursor=self._cursor(start),
            watermark_seq=watermark,
        )
        return RuntimeDispatchResponse(200, dict(_JSON_HEADERS), page.model_dump(mode="json"))

    async def node(
        self, node_id: str, query: dict[str, list[str]], headers: dict[str, str]
    ) -> RuntimeDispatchResponse:
        """读取不可变 payload v1；无法支持的显式版本返回 406。"""
        params = self._parameters(query, {"schema_version"})
        version = int(params.get("schema_version", "1"))
        if version < 1:
            raise ResourceError(422, "INVALID_ARGUMENT", "schema_version must be positive")
        raw_id = internal_id("nod", node_id)
        # 既有 ContextNode 的身份为 12 位十六进制；wire 编码不另建映射表。
        raw_id = raw_id.lstrip("0").zfill(12)
        row = await (
            await self.store.connection().execute(
                "SELECT n.* FROM nodes n JOIN runtime_agents a ON a.agent_id=n.agent_name "
                "WHERE n.id=? AND a.scope_ref=? AND a.deleted_at IS NULL "
                "AND n.runtime_node_seq IS NOT NULL",
                (raw_id, self.store.scope_ref),
            )
        ).fetchone()
        if row is None:
            raise ResourceError(404, "NOT_FOUND", "Node does not exist")
        if version != 1:
            raise ResourceError(406, "PAYLOAD_UNSUPPORTED", "Supported payload schema_version is 1")
        payload, size = self._payload(row)
        view = RuntimeNodeView(**self._identity(row), schema_version=1, payload=payload, size=size)
        body = view.model_dump(mode="json")
        etag = '"' + hashlib.sha256(canonical_json(body).encode()).hexdigest() + '"'
        response_headers = {**_JSON_HEADERS, "ETag": etag}
        if headers.get("if-none-match") == etag:
            return RuntimeDispatchResponse(304, response_headers, None)
        return RuntimeDispatchResponse(200, response_headers, body)
