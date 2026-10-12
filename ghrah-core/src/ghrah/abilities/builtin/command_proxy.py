# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""CommandProxyAbility：插件能力的工具面投影（command_proxy 实质工厂）。

插件能力不新增 Ability 类型面，而是把差异外置为可序列化 descriptor（工具 schema、目标命令路由），
由本工厂按 descriptor 构造。执行时经 ``SubjectCommandBridge`` 把工具调用
派发为宿主 unit 的协议命令（``command/<command>``），由插件方处理。

管辖权纪律：
- 构造参数面封闭（无 hooks 参数），hook 实质不得经本工厂注入；
- descriptor 模型 ``extra="forbid"``，未知字段一律拒绝；
- HITL 闸门不在此文件——由 Core 物化路径
  （``ghrah.core.unit._materialize_command_proxy``）按强制闸门包装：
  ``require_hitl`` 默认 True，仅 ``auto_approve_abilities`` 白名单可关闭，
  descriptor 的声明不具自批效力。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ghrah.abilities.base import Ability
from ghrah.core.subject_bridge import (
    COMMAND_BRIDGE_UNAVAILABLE_ERROR,
    SubjectCommandBridge,
)
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.abilities.context import AbilityExecutionContext
    from ghrah.abilities.hooks import Hook

logger = logging.getLogger(__name__)

__all__ = [
    "CommandProxyDescriptor",
    "CommandProxyAbility",
]

_DEFAULT_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}}


class CommandProxyDescriptor(BaseModel):
    """command_proxy descriptor 的 Core 侧契约（进程内形态）。

    过渡期为进程内 dict（经 REGISTER_ABILITY params 通道到达）；
    D2 落地时升格 ghrah-protocol（字段前瞻对齐 02 §五 AbilityMount，
    升格时形状冻结）。

    Args:
        tool_name: 工具名（Agent 工具面 / LLM function name，同时是
            注册后的 ability 名）
        description: 工具描述（LLM 判断何时调用的依据）
        parameters: 工具参数 JSON schema（None 时用空 object schema 兜底）
        command: 目标协议命令名（不含 ``command/`` 前缀；宿主 unit 的
            RouteSpec.commands 处理器承接）
        require_hitl: 声明式安全语义——**仅信息性**：强制闸门下仅
            ``auto_approve_abilities`` 白名单可关闭 HITL，本字段不具
            自批效力（挂载面声明不得削弱部署方信任闸）
        timeout: 派发超时秒数（None 不限时）
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, Any] | None = None
    command: str = Field(min_length=1)
    require_hitl: bool = True
    timeout: float | None = Field(default=None, gt=0)


class CommandProxyAbility(Ability):
    """command_proxy 工具：LLM 工具调用 → 宿主协议命令的桥接投影。

    构造参数面封闭（无 hooks 参数——管辖权层 1）。
    """

    def __init__(
        self,
        tool_name: str,
        description: str = "",
        parameters: dict[str, Any] | None = None,
        bridge: SubjectCommandBridge | None = None,
        command: str = "",
        timeout: float | None = None,
    ) -> None:
        self._tool_name = tool_name
        self._description = description
        self._parameters = parameters or _DEFAULT_PARAMETERS
        self._bridge = bridge
        self._command = command
        self._timeout = timeout

    @property
    def name(self) -> str:
        return self._tool_name

    def bind_tool(self) -> dict[str, Any]:
        """descriptor 参数直出 OpenAI function schema。"""
        return {
            "type": "function",
            "function": {
                "name": self._tool_name,
                "description": self._description,
                "parameters": self._parameters,
            },
        }

    def to_prompt_description(self) -> str:
        return f"{self._tool_name}(...) -> dict: proxy to command '{self._command}'" + (
            f" ({self._description})" if self._description else ""
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        if self._bridge is None:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": COMMAND_BRIDGE_UNAVAILABLE_ERROR},
            )

        tool_args = dict(context.tool_args or {})
        dispatch = self._bridge.dispatch(self._command, tool_args)
        try:
            if self._timeout is not None:
                result = await asyncio.wait_for(dispatch, timeout=self._timeout)
            else:
                result = await dispatch
        except TimeoutError:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"command '{self._command}' timed out after {self._timeout}s"},
            )

        if result.get("success"):
            return ActionResult(
                outcome=ActionOutcome.SUCCESS,
                data=result.get("data") or {},
            )
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={
                "error": result.get("error") or f"command '{self._command}' failed",
            },
        )
