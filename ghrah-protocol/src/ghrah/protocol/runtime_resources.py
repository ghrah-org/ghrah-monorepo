"""已明确的资源模型；定义模型不意味着 Core 已开放对应路由。"""

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, JsonValue, TypeAdapter, model_validator
from pydantic.json_schema import SkipJsonSchema

from ghrah.protocol.runtime import (
    RuntimeProblem,
    RuntimeRequest,
    RuntimeResponse,
    RuntimeTimestamp,
    _readonly_schema,
)

_JSON_VALUE = TypeAdapter(JsonValue)

type RuntimeAgentId = Annotated[str, Field(pattern=r"^agt_[0-9A-HJKMNP-TV-Z]{26}$")]
type RuntimeDeliveryId = Annotated[str, Field(pattern=r"^dlv_[0-9A-HJKMNP-TV-Z]{26}$")]
type RuntimeMessageId = Annotated[str, Field(pattern=r"^msg_[0-9A-HJKMNP-TV-Z]{26}$")]
type RuntimeNodeId = Annotated[str, Field(pattern=r"^nod_[0-9A-HJKMNP-TV-Z]{26}$")]


def _page_schema(schema: dict[str, Any]) -> None:
    _readonly_schema(schema)
    schema["allOf"] = [
        {
            "if": {"properties": {"next_cursor": {"const": None}}, "required": ["next_cursor"]},
            "then": {"properties": {"has_more": {"const": False}}, "required": ["has_more"]},
            "else": {"properties": {"has_more": {"const": True}}, "required": ["has_more"]},
        }
    ]


class RuntimePage[T](RuntimeResponse):
    """分页通用形状，游标为空与是否还有下一页必须一致。"""

    model_config = ConfigDict(json_schema_extra=_page_schema)

    items: list[T]
    next_cursor: str | None
    has_more: bool

    @model_validator(mode="after")
    def consistent_cursor(self) -> RuntimePage[T]:
        """has_more 由游标是否为 null 决定，不作为第二个独立事实。"""
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("has_more must equal (next_cursor != null)")
        return self


class RuntimeOperation(RuntimeResponse):
    """长动作的可查询表示，不在此处实现动作执行器。"""

    _omittable = ("resource_url",)
    operation_id: str = Field(pattern=r"^op_[0-9A-HJKMNP-TV-Z]{26}$")
    kind: (
        Literal["apply_config", "delivery_resume", "package_install"]
        | Annotated[str, Field(min_length=1)]
    )
    state: (
        Literal["pending", "running", "succeeded", "failed"] | Annotated[str, Field(min_length=1)]
    )
    created_at: RuntimeTimestamp
    updated_at: RuntimeTimestamp
    resource_url: str | SkipJsonSchema[None] = None
    problem: RuntimeProblem | None = None


class RuntimeRef(RuntimeResponse):
    """资源事件中的引用；URL 的可读性由实际资源验收验证。"""

    kind: (
        Literal["agent", "delivery", "ability", "node", "system"]
        | Annotated[str, Field(min_length=1)]
    )
    id: str = Field(min_length=1)
    url: str = Field(min_length=1)


class RuntimeEvent(RuntimeResponse):
    """新资源事件只携带引用；未知响应字段按 additive-only 忽略。"""

    _omittable = ("resource_etag",)
    event_id: str = Field(pattern=r"^evt_[0-9A-HJKMNP-TV-Z]{26}$")
    seq: int = Field(ge=0)
    type: (
        Literal[
            "system.updated",
            "agent.created",
            "agent.updated",
            "agent.deleted",
            "delivery.updated",
            "ability.updated",
            "context.updated",
        ]
        | Annotated[str, Field(min_length=1)]
    )
    scope_ref: str = Field(min_length=1)
    occurred_at: RuntimeTimestamp
    resource: RuntimeRef
    resource_etag: str | SkipJsonSchema[None] = None


class RuntimeStateTransition(RuntimeRequest):
    """声明式启停请求；不接受任意运行观察态作为目标。"""

    state: Literal["running", "stopped"]


class RuntimeMessageCreate(RuntimeRequest):
    """消息受理请求；体量按 JSON/UTF-8 字节计算，不截断正文。"""

    text: str = Field(json_schema_extra={"x-max-utf8-bytes": 256 * 1024})
    kind: Literal["user", "system", "command"]
    metadata: dict[str, Any] = Field(
        default_factory=dict, json_schema_extra={"x-max-json-bytes": 4 * 1024}
    )

    @model_validator(mode="after")
    def bounded_message(self) -> RuntimeMessageCreate:
        """校验字段配额，JSON 编码采用确定的紧凑 UTF-8 表示。"""
        import json

        if len(self.text.encode("utf-8")) > 256 * 1024:
            raise ValueError("text exceeds 256 KiB UTF-8")
        _JSON_VALUE.validate_python(self.metadata, strict=True)
        try:
            encoded = json.dumps(
                self.metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be finite JSON") from exc
        if len(encoded) > 4 * 1024:
            raise ValueError("metadata exceeds 4 KiB JSON")
        return self


class RuntimeMessageAccepted(RuntimeResponse):
    """202 消息受理回执，只引用已持久受理的消息和投递。"""

    message_id: RuntimeMessageId
    delivery_id: RuntimeDeliveryId
    resource_url: str = Field(min_length=1)


class RuntimeDeliveryView(RuntimeResponse):
    """投递资源，不将内部 pending/request 等枚举直接暴露为 wire。"""

    delivery_id: RuntimeDeliveryId
    agent_id: RuntimeAgentId
    message_id: RuntimeMessageId
    state: (
        Literal["queued", "processing", "blocked", "completed", "failed", "interrupted"]
        | Annotated[str, Field(min_length=1)]
    )
    branch_id: str | None = None
    iteration: int | None = None
    blocked_reason: Literal["approval", "budget", "dependency", "paused"] | str | None = None
    last_node_id: RuntimeNodeId | None = None
    attempts: int = Field(ge=0)
    created_at: RuntimeTimestamp
    updated_at: RuntimeTimestamp


class RuntimeTimelineSummary(RuntimeResponse):
    """轨迹集合中的稳定可读摘要。"""

    text: str
    truncated: bool


class RuntimePayloadRef(RuntimeResponse):
    """详情版本与字节大小，摘要由资源 ETag 表达。"""

    schema_version: int = Field(ge=1)
    size: int = Field(ge=0)


class RuntimeTimelineEntry(RuntimeResponse):
    """轨迹条目；seq 是 Core 分配的每 Agent 序号。"""

    node_id: RuntimeNodeId
    agent_id: RuntimeAgentId
    session_id: str
    branch_id: str
    parent_id: RuntimeNodeId | None
    iteration: int
    seq: int = Field(ge=0)
    kind: Literal["iteration", "snapshot", "rollback", "compaction", "system"] | str
    created_at: RuntimeTimestamp
    ability_names: list[str]
    outcome: Literal["success", "failure", "partial", "unknown"] | str
    summary: RuntimeTimelineSummary
    payload_ref: RuntimePayloadRef | None


class RuntimeTimelinePage(RuntimePage[RuntimeTimelineEntry]):
    """带稳定起点与已提交水位的轨迹分页。"""

    snapshot_cursor: str
    watermark_seq: int = Field(ge=0)


class RuntimeNodePayload(RuntimeResponse):
    """节点 payload v1；元数据不携带内部 delivery_context。"""

    messages_delta: list[Any] | None = None
    messages_snapshot: list[Any] | None = None
    action_results: list[Any] | None = None
    metadata: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None


class RuntimeNodeView(RuntimeResponse):
    """版本化节点详情，不替代 Core 上下文权威。"""

    node_id: RuntimeNodeId
    agent_id: RuntimeAgentId
    session_id: str
    branch_id: str
    parent_id: RuntimeNodeId | None
    iteration: int
    seq: int = Field(ge=0)
    kind: Literal["iteration", "snapshot", "rollback", "compaction", "system"] | str
    created_at: RuntimeTimestamp
    schema_version: Literal[1]
    payload: RuntimeNodePayload
    size: int = Field(ge=0)
