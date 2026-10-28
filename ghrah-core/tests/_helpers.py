# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""共享测试辅助工厂函数。"""

from __future__ import annotations

from typing import Any

from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.context.node import ContextNode
from ghrah.scheme.execution.base import ActionOutcome, ActionResult


class ScriptedLLM:
    """脚本化假 LLM：按预设字符串顺序返回纯文本回复（满足 LLMProtocol）。

    仅用于功能级测试——不依赖 unittest.mock，完整走 ActorAgent 的
    llm_factory 注入与 _ensure_llm() 初始化路径。
    """

    def __init__(self, replies: list[str], model: str = "scripted-model") -> None:
        self._replies = list(replies)
        self._model = model
        self.tools: list[dict[str, Any]] = []
        self.calls: list[list[Any]] = []

    @property
    def model(self) -> str:
        return self._model

    async def generate(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._replies:
            raise AssertionError("ScriptedLLM exhausted: no preset reply left")
        return LLMResponse(content_blocks=[TextBlock(text=self._replies.pop(0))])

    def configure_tools(self, tools: list[dict[str, Any]]) -> None:
        self.tools = list(tools)

    def apply_model_overrides(self, overrides: Any) -> None:
        return None


def make_node(**overrides) -> ContextNode:
    """创建测试用 ContextNode。"""
    defaults = {
        "parent_id": None,
        "agent_name": "test-agent",
        "iteration": 0,
        "ability_names": ["init"],
        "agent_state": {"key": "value"},
        "messages_delta": [],
        "is_snapshot": True,
        "session_id": "session-test",
        "created_on_branch_id": "branch-main",
    }
    defaults.update(overrides)
    return ContextNode(**defaults)


def make_action_result(
    outcome: ActionOutcome = ActionOutcome.SUCCESS,
    data: dict | None = None,
    hint: str | None = None,
) -> ActionResult:
    """创建测试用 ActionResult。"""
    return ActionResult(outcome=outcome, data=data or {}, next_action_hint=hint)
