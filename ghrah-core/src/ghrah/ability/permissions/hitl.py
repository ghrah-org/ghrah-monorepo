# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""内核 HITL 包装件：``require_hitl`` 声明的统一物化实现。

挂载面不接纳任何外部 hook
只携带声明式安全语义（``require_hitl`` 等数据）；本模块是内核侧对这些
声明的**固定实现**——声明（数据）来自 descriptor，物化（代码）只此一处。

两段职责：
- ``HITLApprovalHook``：PRE_EXECUTE 审批 hook，拦截宿主 ability 的每次
  执行并发起 HITL 等待（复用 executor 的 ``handle_hitl_hook_result`` →
  HITLFutureStore → receive_hitl_response 全链路）。
- ``HITLRequiredAbility``：组合包装（非继承），委托宿主 ability 并追加
  上面的 PRE_EXECUTE hook。

消费方：
- manifest 物化器（``ghrah.ability.execution.ability_build``）：通用分支的
  ``require_hitl`` 接线；
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ghrah.ability.execution.hooks import Hook, HookPoint, HookResult
from ghrah.core.ability_protocol import AbilityProtocol
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.invocation import OperationCall
    from ghrah.types.results import ActionResult

__all__ = [
    "HITLApprovalHook",
    "HITLRequiredAbility",
    "PermissionDeniedAbility",
]


class HITLApprovalHook(Hook):
    """通用 PRE_EXECUTE HITL 审批 Hook。

    拦截宿主 ability 的每次执行，要求人工审批。审批消息携带 ability 名
    与 tool 参数摘要，供人审判断。

    管辖权纪律：本 hook 仅触发于 ``call.member.member == 宿主名``
    （自身执行域），不参与任何全局拦截。
    """

    hook_point = HookPoint.PRE_EXECUTE

    def __init__(self, ability_name: str, reason: str) -> None:
        self._ability_name = ability_name
        self._reason = reason

    async def should_trigger(self, call: OperationCall, services: ExecutionServices) -> bool:
        return call.member.member == self._ability_name

    async def execute(
        self,
        call: OperationCall,
        services: ExecutionServices,
        result: ActionResult | None = None,
    ) -> HookResult:
        tool_args = call.arguments
        summary = str(tool_args)[:200]
        return HookResult.hitl(
            message=f"{self._reason} — ability='{self._ability_name}' args={summary}"
        )


class _DelegatingAbility(AbilityProtocol):
    """require_hitl 包装：委托宿主 ability，追加 PRE_EXECUTE HITL hook。

    组合而非继承——不破坏宿主 ability 的属性访问（checker/runner 等
    测试断言直接穿透）。
    """

    def __init__(self, inner: AbilityProtocol) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        return self._inner.name

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        return await self._inner.execute(call, services)

    def get_hooks(self) -> list[Hook]:
        return self._inner.get_hooks()

    def bind_tool(self) -> dict[str, Any] | None:
        return self._inner.bind_tool()

    def get_default_state(self) -> dict[str, Any]:
        return self._inner.get_default_state()

    def to_prompt_description(self) -> str:
        return self._inner.to_prompt_description()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


class HITLRequiredAbility(_DelegatingAbility):
    """委托宿主能力并追加审批 Hook。"""

    def __init__(self, inner: AbilityProtocol, ability_name: str, reason: str) -> None:
        super().__init__(inner)
        self._hitl_hook = HITLApprovalHook(ability_name, reason)

    def get_hooks(self) -> list[Hook]:
        return [*self._inner.get_hooks(), self._hitl_hook]


class PermissionDeniedAbility(_DelegatingAbility):
    """在审批和实际效果之前执行不可被批准覆盖的权限拒绝。"""

    def __init__(self, inner: AbilityProtocol, reason: str) -> None:
        super().__init__(inner)
        self._reason = reason

    def check_call(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        """返回硬拒绝，不进入人工审批。"""
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": self._reason})

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        return self.check_call(call, services)

    def get_hooks(self) -> list[Hook]:
        return self._inner.get_hooks()
