# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""共享测试辅助工厂函数。"""

from __future__ import annotations

from typing import Any

from ghrah.abilities.base import ActionOutcome, ActionResult
from ghrah.ability.execution.execution_services import (
    ExecutionServices,
    build_execution_services,
)
from ghrah.ability.execution.invocation import (
    InvocationIdentity,
    MemberReference,
    OperationCall,
)
from ghrah.ability.execution.loop_data import LoopData
from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.context.node import ContextNode


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


class ToolScriptedLLM(ScriptedLLM):
    """脚本化假 LLM：按轮次返回 tool_call 块，轮次用尽后返回纯文本收束。

    用于驱动真实 Agent 循环的“工具轮 → 回复轮”链路：每轮记录实际输入，
    测试据此断言工具结果与 ``tool_call_id`` 确实进入下一次模型输入。
    """

    def __init__(
        self,
        rounds: list[list[Any]],
        final_text: str = "done",
        model: str = "scripted-model",
    ) -> None:
        super().__init__(replies=[])
        self._rounds = [list(round_) for round_ in rounds]
        self._final_text = final_text

    async def generate(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        from ghrah.chat.content import TextBlock

        self.calls.append(list(messages))
        if self._rounds:
            return LLMResponse(content_blocks=list(self._rounds.pop(0)))
        return LLMResponse(content_blocks=[TextBlock(text=self._final_text)])


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


def make_call(
    *,
    member: str = "",
    arguments: dict[str, Any] | None = None,
    tool_call_id: str = "",
    agent: str = "",
    agent_name: str = "",
    project_id: str = "",
    run: str = "",
    alias: str = "",
    instance_id: str = "",
    version_digest: str = "",
) -> OperationCall:
    """创建测试用 OperationCall（成员、参数、调用标识与受信身份）。"""
    return OperationCall(
        member=MemberReference(
            instance_id=instance_id,
            alias=alias,
            member=member,
            version_digest=version_digest,
        ),
        arguments=dict(arguments or {}),
        tool_call_id=tool_call_id,
        identity=InvocationIdentity(
            project_id=project_id,
            agent=agent,
            run=run,
            agent_name=agent_name,
        ),
    )


def make_services(
    loop: dict[str, Any] | LoopData | None = None, **services: Any
) -> ExecutionServices:
    """创建测试用 ExecutionServices；``loop`` 既可直接给 LoopData，也可给 dict。"""
    loop_data = loop if isinstance(loop, LoopData) else LoopData(loop)
    return build_execution_services(loop=loop_data, **services)
