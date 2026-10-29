# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""EndTaskAbility：终止循环并生成最终回复。

这个 Ability 用于：
- 显式终止 Agent 的执行循环
- 作为 Hook 路由的目标（如 MaxIterationHook 强制路由到 end_task）
- 收集累积数据，生成摘要

执行逻辑：
1. 从 AbilityExecutionContext 取本轮模型可见文本作为最终回复
2. 缺省时回退到 toolcall 参数里的 ``summary``
3. 返回 ActionResult(outcome=SUCCESS)，Agent 的 _build_response 会提取回复

- mode 参数支持三种模式：auto / toolcall / verified
- bind_tool() 方法在 mode="toolcall" 时返回 function calling schema
- 当前最简版本只支持 mode="auto"（被 Hook 触发）

内置 Hook：
    EndTaskDoneHook：AFTER_ACTION 触发，执行后终止驱动循环。
    toolcall 模式下 LLM 主动调用 end_task 后，返回值本身不控制循环
    （驱动循环只消费 Hook 的 should_continue），必须由该 Hook 终止；
    Hook 路由路径（_execute_routed_ability）不经 AFTER_ACTION，故不受影响。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ghrah.scheme_ability.execution.base import Ability
from ghrah.scheme_ability.execution.hooks import Hook, HookPoint, HookResult
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme_ability.execution.context import AbilityExecutionContext


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/end_task.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {}
HOOKS = {}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = "End the current task and generate a final response"
TAGS = [
    "core",
    "lifecycle",
]
# --- catalogue 声明结束

logger = logging.getLogger(__name__)

__all__ = ["EndTaskAbility", "EndTaskDoneHook"]


class EndTaskDoneHook(Hook):
    """EndTaskAbility 执行完成后终止驱动循环。

    与 ConversationDoneHook 对称：驱动循环只依据 Hook 的 should_continue
    决定是否继续，Ability 返回的 next_action_hint 不被消费，因此 toolcall
    模式的 end_task 需要一个 AFTER_ACTION Hook 来显式终止。
    """

    hook_point = HookPoint.AFTER_ACTION

    async def should_trigger(self, context: AbilityExecutionContext) -> bool:
        """只在当前 ability 是 end_task 时触发。"""
        return context.current_ability_name == "end_task"

    async def execute(
        self, context: AbilityExecutionContext, result: ActionResult | None
    ) -> HookResult:
        """终止循环。"""
        return HookResult.stop()


class EndTaskAbility(Ability):
    """终止任务能力 — 停止执行循环并生成最终回复。


    - mode: 支持三种模式（auto / toolcall / verified）
      - auto: 被 Hook 触发（默认，当前唯一实现的模式）
      - toolcall: 通过 function call 触发
      - verified: 需要验证器确认后终止
    - bind_tool(): 当 mode="toolcall" 时返回 end_task 的 function calling schema

    特点：
    - execute() 从上下文中收集数据生成最终回复
    - next_action_hint 为 None（明确表示任务结束）

    用法::

        end_task = EndTaskAbility()
        agent.register_ability(end_task)

        # 通过 Hook 强制路由
        MaxIterationHook → route_to="end_task"
    """

    def __init__(
        self,
        hooks: list[Hook] | None = None,
        mode: str = "auto",
    ) -> None:
        if mode not in ("auto", "toolcall", "verified"):
            raise ValueError(f"Invalid mode: {mode!r}, expected 'auto', 'toolcall', or 'verified'")
        self._hooks: list[Hook] = [EndTaskDoneHook()]
        if hooks:
            self._hooks.extend(hooks)
        self._mode = mode

    @property
    def name(self) -> str:
        return "end_task"

    @property
    def mode(self) -> str:
        """当前终止模式。"""
        return self._mode

    def bind_tool(self) -> dict[str, Any] | None:
        """当 mode == 'toolcall' 时返回 end_task 的 function calling schema。"""
        if self._mode == "toolcall":
            # TODO: Phase 1 - 返回 end_task 的 function calling schema
            return {
                "type": "function",
                "function": {
                    "name": "end_task",
                    "description": "End the current task and generate a final response",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {
                                "type": "string",
                                "description": "Optional summary of the task result",
                            },
                        },
                        "required": [],
                    },
                },
            }
        return None

    def to_prompt_description(self) -> str:
        return "End the current task and generate a final response"

    def get_hooks(self) -> list[Hook]:
        return list(self._hooks)

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        """执行任务终止。

        优先使用本轮模型可见文本作为最终回复；没有则回退到 toolcall 参数
        里的 ``summary``。

        Args:
            context: 执行上下文

        Returns:
            ActionResult，data 中包含 "response" 字段
        """
        response = self._collect_response(context)

        logger.debug(f"EndTaskAbility: ending task with response ({len(response)} chars)")

        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={"response": response},
            next_action_hint=None,  # 明确结束
        )

    def _collect_response(self, context: AbilityExecutionContext) -> str:
        """收集最终回复内容。

        优先级：
        1. ``accumulated_data["llm_response"]``：本轮模型可见文本（终答主体）
        2. ``tool_args["summary"]``：toolcall 模式下模型显式给出的总结
        3. ``accumulated_data["response"]``：历史约定的累积响应
        4. 默认的 "Task completed" 消息

        **不读取 ``last_action_result``**：toolcall 模式下它是上一个工具的结果
        （read_file / load_skill 等查询类能力的结果带 content/response 字段），
        把它当最终回复会把"上一轮查询结果"错渲染成模型的最终输出。

        Args:
            context: 执行上下文

        Returns:
            最终回复文本
        """
        text = context.accumulated_data.get("llm_response", "")
        if isinstance(text, str) and text.strip():
            return text

        summary = (context.tool_args or {}).get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary

        response = context.accumulated_data.get("response", "")
        if isinstance(response, str) and response.strip():
            return response

        # 默认回复
        return "Task completed"
