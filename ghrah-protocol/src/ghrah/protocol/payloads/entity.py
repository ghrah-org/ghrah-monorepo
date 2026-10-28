"""Agent 长期实体的身份、快照和显式操作契约。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ghrah.protocol.payloads.project import AgentSpecSchema

Identifier = Annotated[str, Field(min_length=1)]


class EntityModel(BaseModel):
    """实体接口拒绝未声明字段，身份不可被操作参数覆盖。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentIdentity(EntityModel):
    """跨运行实例保持不变的 Agent 身份。"""

    project_id: Identifier
    agent_id: Identifier


class AgentRuntimeSnapshot(EntityModel):
    """当前执行实例的观测状态，不从持久诊断推断在线。"""

    availability: Literal["online", "offline", "unknown"]
    incarnation_id: str | None = None
    busy: bool | None = None
    error: str | None = None


class AgentSnapshot(EntityModel):
    """长期实体的可序列化视图；各子域数据仍来自原权威。"""

    identity: AgentIdentity
    specification: AgentSpecSchema
    specification_revision: int
    runtime: AgentRuntimeSnapshot
    context: dict[str, Any]
    mailbox: dict[str, Any]
    abilities: dict[str, Any]
    observed_at: str


class EntityReadOperation(EntityModel):
    """实体及子域的轻量查询。"""

    type: Literal["snapshot", "context.list_sessions", "abilities.list"]


class EntityLifecycleOperation(EntityModel):
    """显式启停实体的执行实例。停止保留规格与历史。"""

    type: Literal["start", "stop"]
    expected_revision: int | None = Field(default=None, ge=1)
    expected_incarnation_id: str | None = None


class EntityHistoryOperation(EntityModel):
    """查询属于当前实体的指定会话/分支历史。"""

    type: Literal["context.get_history"]
    session_id: Identifier
    branch_id: Identifier
    limit: int = Field(default=100, ge=1, le=1000)


class EntityCreateSessionOperation(EntityModel):
    """为当前运行实例创建会话；不会隐式启动实体。"""

    type: Literal["context.create_session"]
    name: str | None = None
    system_prompt: str | None = None


class EntityActivateSessionOperation(EntityModel):
    """显式切换当前实体的会话。"""

    type: Literal["context.activate_session"]
    session_id: Identifier


class EntityMailboxListOperation(EntityModel):
    """按投递或发送消息序号进行稳定游标分页。"""

    type: Literal["mailbox.list_inbox", "mailbox.list_outbox"]
    limit: int = Field(default=50, ge=1, le=200)
    before_seq: int | None = Field(default=None, ge=1)


class EntityMessageOperation(EntityModel):
    """读取当前实体参与的消息及各投递状态。"""

    type: Literal["mailbox.get_message"]
    message_id: Identifier


class EntityDeliveryOperation(EntityModel):
    """只允许恢复投递给当前实体的记录。"""

    type: Literal["mailbox.retry", "mailbox.resume"]
    delivery_id: Identifier


class EntityAbilityConfigureOperation(EntityModel):
    """在停止状态编辑实例的显式能力列表，启动时使用。"""

    type: Literal["abilities.configure"]
    ability_types: list[Identifier] = Field(min_length=1)
    expected_revision: int = Field(ge=1)


AgentEntityOperation = Annotated[
    EntityReadOperation
    | EntityLifecycleOperation
    | EntityHistoryOperation
    | EntityCreateSessionOperation
    | EntityActivateSessionOperation
    | EntityMailboxListOperation
    | EntityMessageOperation
    | EntityDeliveryOperation
    | EntityAbilityConfigureOperation,
    Field(discriminator="type"),
]


class AgentEntityPayload(AgentIdentity):
    """固定身份加白名单操作；不会接收任意子系统命令。"""

    operation: AgentEntityOperation


class AgentEntityCreatePayload(EntityModel):
    """创建持久实体；调用者显式选择是否立即启动。"""

    project_id: Identifier
    name: Identifier
    cluster_id: Identifier
    start: bool
    manifest_ref: str = ""
    system_prompt: str = ""
    ability_types: list[Identifier] | None = Field(default=None, min_length=1)


class AgentEntityListPayload(EntityModel):
    """列出项目内的持久实体（包括已停止的实体）。"""

    project_id: Identifier
