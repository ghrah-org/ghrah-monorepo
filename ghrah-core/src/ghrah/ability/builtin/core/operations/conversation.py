# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ConversationAbility：无 tool call 的最简对话能力。

- 这是「无 tool_call 的最简实例」
- 当 LLM 返回纯文本时，框架自动标记为使用了 conversation 能力
- execute() 方法不再调用 LLM（LLM 调用将在 Phase 1 移到 _drive_loop 的 action 层）
- 从 LOOP 执行服务（``llm_response``）获取 LLM 已返回的纯文本响应

内置 Hook：
    ConversationDoneHook：POST_EXECUTE 触发，执行后终止循环。
    纯对话场景下 ConversationAbility 只需执行一次，无需继续循环。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import LOOP
from ghrah.ability.execution.hooks import Hook, HookPoint, HookResult
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.invocation import OperationCall


# ---------------------------------------------------------------------------
# Operation 声明（v1）：build 从下列常量与 Input/Output 模型生成 ability.yaml 的 members。
# 旧 manifest 的 metadata.description / tags / permissions 分别落在 SUMMARY / TAGS / PERMISSIONS。
# ---------------------------------------------------------------------------
PERMISSIONS = {}

# --- 成员声明（build 生成 members 的唯一来源；改完须重新 build）
TITLE = "Conversation"
SUMMARY = "Direct conversation with the LLM without tool usage"
TAGS = [
    "core",
    "conversation",
]
EFFECTS = []
TIMEOUT_MS = 60_000
# 不进模型工具面：本成员没有工具绑定，只作为一次不带工具的用户轮次入口。
EXPOSE = False
# --- 成员声明结束

logger = logging.getLogger(__name__)

__all__ = ["ConversationAbility", "ConversationDoneHook"]


class Input(BaseModel):
    """conversation 入参契约：无参数（额外字段拒绝）。"""

    model_config = ConfigDict(extra="forbid")


class Output(BaseModel):
    """conversation 成功结果：有模型回复时带 response，否则为空对象。"""

    model_config = ConfigDict(extra="forbid")

    response: str | None = Field(
        default=None,
        description="Assistant reply when the loop produced one",
    )


class ConversationDoneHook(Hook):
    """ConversationAbility 执行完成后终止循环。

    纯对话 Ability 只需执行一次 LLM 调用即可，无需继续循环。
    AFTER_ACTION hook（drive_loop 级），
    这样可以在 _drive_loop 层面终止循环。
    """

    hook_point = HookPoint.AFTER_ACTION

    async def should_trigger(self, call: OperationCall, services: ExecutionServices) -> bool:
        """只在当前 ability 是 conversation 且有 LLM 响应时触发。"""
        return call.member.member == "conversation"

    async def execute(
        self,
        call: OperationCall,
        services: ExecutionServices,
        result: ActionResult | None = None,
    ) -> HookResult:
        """终止循环。"""
        return HookResult.stop()


class ConversationAbility(Ability):
    """对话能力 — 无 tool call，纯文本响应。

    - bind_tool() 返回 None（不注册 function calling schema）
    - execute() 不再调用 LLM，从 LOOP 执行服务（``llm_response``）获取 LLM 响应
    - 内置 ConversationDoneHook：AFTER_ACTION 终止循环（drive_loop 级）

    用法::

        ability = ConversationAbility()
        agent.register_ability(ability)
    """

    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self._hooks: list[Hook] = [ConversationDoneHook()]
        if hooks:
            self._hooks.extend(hooks)

    @property
    def name(self) -> str:
        return "conversation"

    def bind_tool(self) -> dict[str, Any] | None:
        """纯对话能力不需要 tool binding。"""
        return None

    def to_prompt_description(self) -> str:
        return "Direct conversation with the LLM without tool usage"

    def get_hooks(self) -> list[Hook]:
        return list(self._hooks)

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        """执行对话响应处理。

        从 LOOP 执行服务（``llm_response``）获取 LLM 已经返回的纯文本响应，
        包装为 ActionResult 返回。如果不存在这个 key，返回一个空的 SUCCESS result。


        Args:
            call: 本次调用的值对象。
            services: 执行期服务表。

        Returns:
            ActionResult，data 中包含 "response" 字段
        """
        response = services.require(LOOP).llm_response

        if response:
            logger.debug(f"ConversationAbility: got LLM response ({len(response)} chars)")
            return ActionResult(
                outcome=ActionOutcome.SUCCESS,
                data={"response": response},
                next_action_hint=None,  # 对话完成，无需继续循环
            )

        logger.debug("ConversationAbility: no llm_response in loop data, returning empty success")
        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={},
            next_action_hint=None,
        )
