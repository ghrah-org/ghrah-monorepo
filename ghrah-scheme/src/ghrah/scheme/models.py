# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""进程内的身份、引用和状态快照；不定义跨语言 wire 契约。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

from ghrah.scheme.errors import ErrorCode

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True)
class AgentScope:
    """一个项目内 Agent 的确定运行作用域，由受信装配者核定。"""

    project_id: str
    agent_id: str
    run_id: str

    def __post_init__(self) -> None:
        for value in (self.project_id, self.agent_id, self.run_id):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError("项目、Agent 与运行 ID 必须为非空且无首尾空白的字符串")


@dataclass(frozen=True)
class Principal:
    """稳定身份与运行身份参与核定；显示名和归属 namespace 仅作元数据。"""

    project_id: str
    agent_id: str
    run_id: str
    display_name: str = field(default="", compare=False)
    namespace: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        AgentScope(self.project_id, self.agent_id, self.run_id)
        if not isinstance(self.display_name, str) or not isinstance(self.namespace, str):
            raise ValueError("显示名和归属 namespace 必须为字符串")

    @property
    def scope(self) -> AgentScope:
        """供局部绑定及本 boot 去重使用的稳定运行作用域。"""
        return AgentScope(self.project_id, self.agent_id, self.run_id)


@dataclass(frozen=True)
class Target:
    """固定 Agent 运行作用域、契约、绑定和提供者实例的解析结果。"""

    scope: AgentScope
    kind: str
    alias: str
    resource: tuple[str, ...]
    provider_id: str
    incarnation: str
    contract: str
    contract_version: str
    contract_digest: str
    binding_revision: int


class AccessStage(StrEnum):
    """明确区分取得引用、最终接纳和结果正文读取。"""

    ACQUIRE = "acquire"
    ADMIT = "admit"
    RESULT = "result"


@dataclass(frozen=True)
class AccessRequest:
    """交给领域权威的受限核定输入；参数为隔离副本。"""

    principal: Principal
    target: Target
    actions: frozenset[str]
    stage: AccessStage
    arguments: JsonObject | None = None
    invocation_id: str | None = None


@dataclass(frozen=True)
class Authorization:
    """领域核定结果；允许时必须给出可归因的依据。"""

    allowed: bool
    basis: str = ""

    def __post_init__(self) -> None:
        if self.allowed and not self.basis.strip():
            raise ValueError("允许访问时必须提供核定依据")


@dataclass(frozen=True)
class Admission(Authorization):
    """领域提交的接纳依据；只读调用可以没有持久记录 ID。"""

    record_id: str | None = None


@dataclass(frozen=True)
class ResourceRef:
    """会话持有的运行引用；必须与服务端登记完全匹配。"""

    boot: str
    holder: str
    slot: int
    generation: int
    token: str


@dataclass(frozen=True)
class InvocationRef:
    """调用的观察／控制引用，不能仅凭调用 ID 构造权限。"""

    boot: str
    holder: str
    token: str


class InvocationState(StrEnum):
    """运行调用状态；COMPLETED 表示收到有效结果，不解释业务成功。"""

    QUEUED = "queued"
    ADMITTING = "admitting"
    ADMITTED = "admitted"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AdmissionState(StrEnum):
    """取消核定等待时，不能假称领域一定没有提交。"""

    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class EffectState(StrEnum):
    """效果信息；收到结果仍由领域解释其中的实际业务效果。"""

    NOT_DISPATCHED = "not_dispatched"
    UNKNOWN = "unknown"
    REPORTED = "reported"


class CancellationState(StrEnum):
    """取消确认只表示执行协程响应了取消，不表示外部效果回滚。"""

    NONE = "none"
    REQUESTED = "requested"
    CONFIRMED = "confirmed"


class ProviderState(StrEnum):
    """描述运行导出；WITHDRAWN 不宣称任何 OS 进程已退出。"""

    READY = "ready"
    DRAINING = "draining"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class InvocationSnapshot:
    """隔离的调用快照；修改嵌套 result 不会修改运行记录。"""

    invocation_id: str
    target: Target
    action: str
    state: InvocationState
    admission: AdmissionState
    effect: EffectState
    cancellation: CancellationState
    cancel_reason: ErrorCode | None
    error: ErrorCode | None
    basis: str | None
    admission_record: str | None
    result: JsonObject | None
    result_redacted: bool
    version: int
    pending: bool


class CancelDisposition(StrEnum):
    """取消入口的请求回执，与执行终态分开。"""

    REQUESTED = "requested"
    ALREADY_FINISHED = "already_finished"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ReleaseReport:
    """逻辑引用已释放；正在执行的调用仍由原运行 owner 持有。"""

    released: bool


@dataclass(frozen=True)
class DrainReport:
    """关闭后的残余运行责任，不是物理进程停止确认。"""

    pending: tuple[InvocationSnapshot, ...]
    pending_controls: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """是否已经没有在途调用。"""
        return not self.pending and not self.pending_controls


@dataclass(frozen=True)
class RuntimeLimits:
    """明确的内存、任务、引用和等待预算。"""

    max_sessions: int = 128
    max_providers: int = 64
    max_bindings: int = 256
    max_contracts: int = 64
    max_inflight: int = 64
    max_resource_refs: int = 64
    max_invocation_refs: int = 128
    max_retained_calls: int = 256
    max_request_keys: int = 1024
    max_authorizations: int = 16
    max_observers: int = 16
    max_input_bytes: int = 65536
    max_result_bytes: int = 262144
    default_timeout: float = 30.0
    max_observe_wait: float = 30.0

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"预算必须为正数：{name}")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"预算必须为有限正数：{name}")
            if name not in {"default_timeout", "max_observe_wait"} and not isinstance(value, int):
                raise ValueError(f"数量预算必须为整数：{name}")
