# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Ability 最小接口协议定义。

通过 Protocol（structural subtyping）定义 Ability、Executor 的接口契约，
使 core 层可以仅依赖这些协议而非 abilities 层的具体实现。
ghrah.ability.execution.base.Ability 等具体类天然满足这些协议，无需显式继承。
方法签名中使用 Any 替代执行层的具体类型（OperationCall / ExecutionServices），
避免 protocol 层反向依赖实现模块。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ghrah.types.results import ActionResult

__all__ = [
    "AbilityProtocol",
    "ExecutorProtocol",
]


@runtime_checkable
class AbilityProtocol(Protocol):
    """Ability 最小接口协议 — core 层仅依赖此协议。

    与 ghrah.ability.execution.base.Ability 不同，Protocol 不要求继承，
    任何实现了 name/execute/bind_tool/get_hooks 的对象都满足此协议。
    """

    @property
    def name(self) -> str: ...

    async def execute(self, call: Any, services: Any) -> ActionResult: ...

    def bind_tool(self) -> dict[str, Any] | None: ...

    def get_hooks(self) -> list[Any]: ...

    def get_default_state(self) -> dict[str, Any]: ...


@runtime_checkable
class ExecutorProtocol(Protocol):
    """Ability 执行器协议 — 供 agents/base.py 和 core/server 依赖。"""

    async def execute_ability(
        self,
        ability: AbilityProtocol,
        call: Any,
        services: Any,
    ) -> ActionResult: ...

    async def execute_tool_calls(
        self,
        tool_calls: list[Any],
        abilities: dict[str, AbilityProtocol],
        accumulated_data: dict[str, Any],
        context_manager: Any,
        last_action_result: Any = None,
        invocation_factory: Any = None,
    ) -> list[dict]: ...

    def update_hooks(self, hooks: list[Any]) -> None: ...

    def update_event_publisher(self, publisher: Any) -> None: ...

    def receive_hitl_response(
        self,
        ability_name: str,
        tool_call_id: str,
        approved: bool,
        result: Any = None,
    ) -> bool: ...
