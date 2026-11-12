"""Agent 资源作者模型；完整装配输入不依赖 Subject 模板或旧命令 DTO。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from ghrah.protocol.ability_tree import TreeKey
from ghrah.protocol.runtime import RuntimeRequest, RuntimeResponse, RuntimeTimestamp
from ghrah.protocol.runtime_resources import RuntimeAgentId, RuntimeDeliveryId, RuntimePage

_JSON: dict[str, Any] = {"x-runtime-json": True}


class _ModelFields(BaseModel):
    endpoint_name: str = Field(min_length=1, max_length=128)
    model_overrides: dict[str, Any] | None = Field(json_schema_extra=_JSON)


class RuntimeModelInput(RuntimeRequest, _ModelFields):
    """模型端点引用和显式参数覆盖。"""


class RuntimeModelView(RuntimeResponse, _ModelFields):
    """响应中的模型配置，允许将来新增字段。"""


class _PromptFields(BaseModel):
    system_prompt: str
    template_ref: str | None


class RuntimePromptInput(RuntimeRequest, _PromptFields):
    """完整提示词和仅用于审计的来源引用。"""


class RuntimePromptView(RuntimeResponse, _PromptFields):
    """响应中的提示词，不截断正文。"""


class _ContextFields(BaseModel):
    max_iterations: int = Field(ge=1, le=200)
    communication_timeout: float = Field(allow_inf_nan=False)
    window: dict[str, Any] | None = Field(json_schema_extra=_JSON)
    overrides: dict[str, Any] | None = Field(json_schema_extra=_JSON)


class RuntimeContextInput(RuntimeRequest, _ContextFields):
    """上下文策略，所有可空字段也必须显式出现。"""


class RuntimeContextView(RuntimeResponse, _ContextFields):
    """响应中的上下文策略。"""


class _SurfaceFields(BaseModel):
    mode: str
    static: list[str]
    max_static: int | None = Field(ge=1)


class RuntimeSurfaceInput(RuntimeRequest, _SurfaceFields):
    """工具表面偏好，不等于授权事实。"""

    mode: Literal["auto", "static", "progressive"]


class RuntimeSurfaceView(RuntimeResponse, _SurfaceFields):
    """工具表面响应保留未来枚举值。"""

    mode: str


class _OperationFields(BaseModel):
    key: TreeKey
    visible: bool
    available: bool


class RuntimeOperationInput(RuntimeRequest, _OperationFields):
    """操作成员及其显式可见、可用请求。"""


class RuntimeOperationView(RuntimeResponse, _OperationFields):
    """响应中的操作成员。"""


class _ScriptFields(BaseModel):
    key: TreeKey
    visible: bool
    exec: bool


class RuntimeScriptInput(RuntimeRequest, _ScriptFields):
    """脚本可见与执行分开声明。"""


class RuntimeScriptView(RuntimeResponse, _ScriptFields):
    """响应中的脚本成员。"""


class RuntimeSkillInput(RuntimeRequest):
    """技能内容和脚本成员申请。"""

    key: TreeKey
    visible: bool
    scripts: list[RuntimeScriptInput]


class RuntimeSkillView(RuntimeResponse):
    """响应中的技能内容。"""

    key: str
    visible: bool
    scripts: list[RuntimeScriptView]


class RuntimePackageInput(RuntimeRequest):
    """按完整包名和成员表达来源；不使用已废弃的 source_mode。"""

    package: str = Field(min_length=1)
    origin: Literal["builtin", "installed", "mcp"]
    whole_container: bool
    operations: list[RuntimeOperationInput]
    skills: list[RuntimeSkillInput]


class RuntimePackageView(RuntimeResponse):
    """响应中的能力包。"""

    package: str
    origin: str
    whole_container: bool
    operations: list[RuntimeOperationView]
    skills: list[RuntimeSkillView]


class RuntimeAbilityInput(RuntimeRequest):
    """渲染后的完整能力输入，Core 不读取 Subject 模板。"""

    surface: RuntimeSurfaceInput
    packages: list[RuntimePackageInput]
    mcp_refs: list[str]


class RuntimeAbilityView(RuntimeResponse):
    """响应中的完整能力配置。"""

    surface: RuntimeSurfaceView
    packages: list[RuntimePackageView]
    mcp_refs: list[str]


type RuntimeAgentState = (
    Literal["stopped", "starting", "idle", "busy", "stopping", "error"]
    | Annotated[str, Field(min_length=1)]
)


type RuntimeSubcommand = Annotated[tuple[str, list[str]], Field(strict=False)]


class _PermissionFields(BaseModel):
    auto_approve: list[str]
    require_approval_by_default: bool
    safe_extra_commands: list[str]
    safe_extra_sub_commands: list[RuntimeSubcommand] | None


class RuntimePermissionInput(RuntimeRequest, _PermissionFields):
    """完整权限请求，命令与有序子命令规则分开。"""


class RuntimePermissionView(RuntimeResponse, _PermissionFields):
    """响应中的权限请求。"""


class RuntimeAssemblyInput(RuntimeRequest):
    """完整装配对象：不省略字段，不依赖 Core 补默认值。"""

    model: RuntimeModelInput
    prompt: RuntimePromptInput
    context_policy: RuntimeContextInput
    ability_request: RuntimeAbilityInput
    permission_request: RuntimePermissionInput

    @model_validator(mode="after")
    def finite_json(self) -> RuntimeAssemblyInput:
        """拒绝不能作为 JSON 请求传输的参数。"""
        import json

        from pydantic import JsonValue, TypeAdapter

        value = self.model_dump(mode="python")
        # 有序子命令在 JSON 上是数组，先用序列化器还原其 wire 形状。
        value["permission_request"] = self.permission_request.model_dump(mode="json")
        TypeAdapter(JsonValue).validate_python(value, strict=True)
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        return self


class RuntimeAssemblyView(RuntimeResponse):
    """与输入同义的响应配置，逐层允许 additive-only 扩展。"""

    model: RuntimeModelView
    prompt: RuntimePromptView
    context_policy: RuntimeContextView
    ability_request: RuntimeAbilityView
    permission_request: RuntimePermissionView


class RuntimeAgentCreate(RuntimeRequest):
    """创建资源；运行目标缺省为 stopped，scope 由受信主体提供。"""

    external_ref: str = Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")
    config: RuntimeAssemblyInput
    state: Literal["running", "stopped"] = "stopped"


class RuntimeCapabilitySummary(RuntimeResponse):
    """当前能力的摘要；不是独立可写授权。"""

    tree_revision: int = Field(ge=0)
    tree_digest: str
    pending_changes: int = Field(ge=0)


class RuntimeCurrentDelivery(RuntimeResponse):
    """当前投递的引用。"""

    delivery_id: RuntimeDeliveryId
    state: str


class RuntimeAgentError(RuntimeResponse):
    """可展示的运行错误。"""

    code: str
    message: str
    at: RuntimeTimestamp


class RuntimeAgentResource(RuntimeResponse):
    """Agent 定义与即时运行事实的组合读面。"""

    agent_id: RuntimeAgentId
    scope_ref: str
    external_ref: str
    config: RuntimeAssemblyView
    state: RuntimeAgentState
    incarnation_id: str | None
    config_revision: int = Field(ge=1)
    assembly_digest: str
    resolved_digest: str
    capability: RuntimeCapabilitySummary
    current_delivery: RuntimeCurrentDelivery | None
    last_error: RuntimeAgentError | None
    created_at: RuntimeTimestamp
    updated_at: RuntimeTimestamp


class RuntimePromptSummary(RuntimeResponse):
    """只包含提示词 UTF-8 大小和摘要。"""

    digest: str
    size: int = Field(ge=0)


class RuntimeAgentSummary(RuntimeResponse):
    """集合表示不内联配置或提示词正文。"""

    agent_id: RuntimeAgentId
    scope_ref: str
    external_ref: str
    state: RuntimeAgentState
    config_revision: int = Field(ge=1)
    assembly_digest: str
    prompt: RuntimePromptSummary
    capability: RuntimeCapabilitySummary
    current_delivery: RuntimeCurrentDelivery | None
    last_error: RuntimeAgentError | None
    etag: str
    created_at: RuntimeTimestamp
    updated_at: RuntimeTimestamp


class RuntimeAgentsPage(RuntimePage[RuntimeAgentSummary]):
    """Agent 摘要分页。"""


class RuntimeStateTransitionResult(RuntimeResponse):
    """状态转换后返回资源和本次恢复结果；重复目标返回 null。"""

    agent: RuntimeAgentResource
    recovered: Literal["restored", "initialized"] | None
