"""受信连接绑定与 Provider 请求上下文；不解释主体种类或领域权限。"""

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class CallerBinding:
    """由宿主核定的主体、解析根与诊断许可，客户端不能覆盖。"""

    principal: str
    resolution_scope: str | None = None
    diagnostics: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.principal, str) or not self.principal:
            raise ValueError("连接必须绑定非空受信主体")
        if self.resolution_scope is not None and (
            not isinstance(self.resolution_scope, str) or not self.resolution_scope
        ):
            raise ValueError("解析 Scope 必须为空或非空引用")
        if type(self.diagnostics) is not bool:
            raise ValueError("诊断许可必须为布尔值")


@dataclass(frozen=True, slots=True)
class ProviderContext:
    """Kernel 交付的实际调用者及精确目标，独立于业务载荷。"""

    principal: str
    session_id: str
    caller_domain_id: str | None
    resolution_scope: str | None
    scheme_id: str
    provider_object_id: str
    invocation_id: str | None = None
    deadline: float | None = None

    def __post_init__(self) -> None:
        for value in (self.principal, self.session_id, self.scheme_id, self.provider_object_id):
            if not isinstance(value, str) or not value:
                raise ValueError("可信上下文的主体、会话与目标必须非空")
        for value in (self.caller_domain_id, self.resolution_scope, self.invocation_id):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError("上下文引用必须为空或非空字符串")
        if self.deadline is not None and (
            type(self.deadline) not in (int, float) or not math.isfinite(self.deadline)
        ):
            raise ValueError("上下文截止时间必须为有限单调时钟值")

    def to_dict(self) -> dict:
        """导出消息元数据；业务参数不参与上下文生成。"""
        return asdict(self)
