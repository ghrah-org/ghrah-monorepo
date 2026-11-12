"""Runtime 进程内请求与资源契约；Python 模型是生成产物的作者来源。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema


def _utc_timestamp(value: str) -> str:
    datetime.fromisoformat(value)
    return value


type RuntimeTimestamp = Annotated[
    str,
    Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$",
        json_schema_extra={"format": "date-time"},
    ),
    AfterValidator(_utc_timestamp),
]
type RuntimeUri = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9+.-]*:")]

type RuntimeErrorCode = Literal[
    "ACCESS_DENIED",
    "NOT_FOUND",
    "CONFLICT",
    "INVALID_STATE",
    "BUSY",
    "PRECONDITION_FAILED",
    "INVALID_ARGUMENT",
    "BUDGET_EXCEEDED",
    "UNAVAILABLE",
    "UNSUPPORTED",
    "CURSOR_EXPIRED",
    "PAYLOAD_UNSUPPORTED",
    "INTERNAL",
]


def _readonly_schema(schema: dict[str, Any]) -> None:
    schema["readOnly"] = True
    for field in schema.get("properties", {}).values():
        field["readOnly"] = True
        if field.get("type") in {"string", "object"} and field.get("default", ...) is None:
            field.pop("default")


def _status_schema(schema: dict[str, Any]) -> None:
    _readonly_schema(schema)
    schema["allOf"] = [
        {
            "if": {"properties": {"phase": {"const": "ready"}}, "required": ["phase"]},
            "then": {"properties": {"ready": {"const": True}}, "required": ["ready"]},
            "else": {"properties": {"ready": {"const": False}}, "required": ["ready"]},
        },
        {
            "if": {"properties": {"error": {"type": "object"}}, "required": ["error"]},
            "then": {"properties": {"phase": {"const": "failed"}}, "required": ["phase"]},
        },
    ]


class RuntimeRequest(BaseModel):
    """请求严格校验，不接受未知字段或隐式类型转换。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class RuntimeResponse(BaseModel):
    """资源响应忽略新增字段；可省略字段不因此接受显式 null。"""

    model_config = ConfigDict(
        extra="ignore", strict=True, frozen=True, json_schema_extra=_readonly_schema
    )
    _omittable: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: Any) -> Any:
        """仅允许契约明确标注 nullable 的字段使用 null。"""
        if isinstance(value, dict):
            for name in cls._omittable:
                if name in value and value[name] is None:
                    raise ValueError(f"{name} may be omitted but cannot be null")
        return value

    @model_serializer(mode="wrap")
    def omit_missing(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """未提供的非 nullable 字段不输出 null 占位。"""
        value: dict[str, Any] = handler(self)
        for name in self._omittable:
            if value.get(name) is None:
                value.pop(name, None)
        return value


@dataclass(frozen=True)
class RuntimeDispatchResponse:
    """进程内派发响应，保留资源契约的状态码、头和正文。"""

    status: int
    headers: dict[str, str]
    body: dict[str, Any] | None


class RuntimePrincipal(RuntimeRequest):
    """由受信宿主提供的调用主体，不从请求体提取。"""

    kind: Literal["subject"] = "subject"
    scope_ref: str = Field(min_length=1, max_length=128)


RuntimeCommand = Literal["spawn_agent", "list_agents", "send_message", "terminate_agent"]


class RuntimeCommandRequest(RuntimeRequest):
    """过渡命令信封；payload 沿用既有命令 DTO，不另立资源事实。"""

    command: RuntimeCommand
    payload: dict[str, Any]


class RuntimeCapability(RuntimeResponse):
    """已实际实现的契约能力，客户端不按实现名称分支。"""

    name: str = Field(min_length=1)
    version: int = Field(ge=1)


class RuntimeMeta(RuntimeResponse):
    """Runtime 契约和能力协商结果。"""

    contract: Literal["v1"] = "v1"
    capabilities: list[RuntimeCapability]
    server_time: RuntimeTimestamp


class RuntimeStandardProblem(RuntimeResponse):
    """405/413/428 的标准错误表示，不生成领域 code。"""

    _omittable = ("detail", "instance", "details", "trace_id")
    type: RuntimeUri
    title: str = Field(min_length=1)
    status: Literal[405, 413, 428]
    detail: str | SkipJsonSchema[None] = None
    instance: str | SkipJsonSchema[None] = None
    retryable: bool
    details: dict[str, Any] | SkipJsonSchema[None] = None
    trace_id: str | SkipJsonSchema[None] = None


class RuntimeProblem(RuntimeResponse):
    """领域错误；响应保留未知 code，调用方仍可按状态码处理。"""

    _omittable = RuntimeStandardProblem._omittable
    type: RuntimeUri
    title: str = Field(min_length=1)
    status: int = Field(ge=400, le=599)
    detail: str | SkipJsonSchema[None] = None
    instance: str | SkipJsonSchema[None] = None
    code: RuntimeErrorCode | Annotated[str, Field(min_length=1)]
    retryable: bool
    details: dict[str, Any] | SkipJsonSchema[None] = None
    trace_id: str | SkipJsonSchema[None] = None


class RuntimeSystemStatus(RuntimeResponse):
    """权威生命周期快照；新增阶段可读，但不能冒充 ready。"""

    model_config = ConfigDict(json_schema_extra=_status_schema)

    phase: (
        Literal["booting", "ready", "stopping", "stopped", "failed"]
        | Annotated[str, Field(min_length=1)]
    )
    ready: bool
    boot_id: str = Field(min_length=1)
    since: RuntimeTimestamp
    contract: Literal["v1"] = "v1"
    error: RuntimeProblem | None = None

    @model_validator(mode="after")
    def consistent_phase(self) -> RuntimeSystemStatus:
        """ready 与阶段一致，诊断错误只属于 failed 阶段。"""
        if self.ready != (self.phase == "ready"):
            raise ValueError("ready must equal (phase == ready)")
        if self.error is not None and self.phase != "failed":
            raise ValueError("error is only valid in failed phase")
        return self
