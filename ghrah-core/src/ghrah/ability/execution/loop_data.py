# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Agent 循环数据：只通过 ``LOOP`` 服务提供，不进入调用对象。

``llm_response``、``response``、``cot_content``、累计 token 用量与上一次动作
结果都属于「Agent 循环」，不属于「一次能力调用」。把循环数据塞进
``OperationCall`` 会让调用对象随功能膨胀，也会让模型参数与循环状态混在一起；
因此这里单独成形，由执行器以快照方式交给对话、结束这类紧耦合成员。

``LoopData`` 同时承接 Hook 的 ``modified_context`` 合并目标：Hook 改的是循环
数据，不是调用参数。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ghrah.types.results import ActionResult

__all__ = ["LoopData"]


class LoopData:
    """一次执行可见的循环数据快照。

    典型用法::

        loop = services.require(LOOP)
        response = loop.llm_response or loop.response
    """

    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        self._raw: dict[str, Any] = dict(initial or {})

    @property
    def raw(self) -> dict[str, Any]:
        """返回底层数据（供 Hook 合并修改）。"""
        return self._raw

    @property
    def llm_response(self) -> str:
        """本轮模型可见文本；没有时为 ``""``。"""
        value = self._raw.get("llm_response", "")
        return value if isinstance(value, str) else ""

    @property
    def response(self) -> str:
        """历史约定的累积响应；没有时为 ``""``。"""
        value = self._raw.get("response", "")
        return value if isinstance(value, str) else ""

    @property
    def cot_content(self) -> str:
        """思维链文本；没有时为 ``""``。"""
        value = self._raw.get("cot_content", "")
        return value if isinstance(value, str) else ""

    @property
    def last_action_result(self) -> ActionResult | None:
        """上一次动作结果（循环状态，不是本次调用结果）。"""
        value = self._raw.get("last_action_result")
        return value  # type: ignore[no-any-return]

    def get(self, key: str, default: Any = None) -> Any:
        """按原始键读取；仅供尚未类型化的循环字段过渡使用。"""
        return self._raw.get(key, default)

    def update(self, values: Mapping[str, Any] | LoopData | None = None) -> None:
        """合并循环数据（Hook 的 ``modified_context`` 走这里）。"""
        if isinstance(values, LoopData):
            self._raw.update(values.raw)
        elif values is not None:
            self._raw.update(values)

    def snapshot(self) -> dict[str, Any]:
        """返回深拷贝快照，供持久化与跨轮传递。"""
        return copy.deepcopy(self._raw)
