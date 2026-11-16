# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""跨模块错误载荷。

只有语义，没有传输：不含 HTTP 状态码，也不含 `type` URI。
`code` 是唯一的机器判据——调用方只靠它就能分支。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

__all__ = ["Problem", "ProblemCode"]


class ProblemCode(StrEnum):
    """机器可读的错误分类。"""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    BUSY = "BUSY"
    FAILED_PRECONDITION = "FAILED_PRECONDITION"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    PRECONDITION_REQUIRED = "PRECONDITION_REQUIRED"
    CURSOR_EXPIRED = "CURSOR_EXPIRED"
    RESOURCE_ARCHIVED = "RESOURCE_ARCHIVED"
    PROMISE_NOT_HELD = "PROMISE_NOT_HELD"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL = "INTERNAL"


#: 默认可重试的错误码。刻意保持极小：其余错误码重试只会重复失败。
_RETRYABLE: frozenset[ProblemCode] = frozenset(
    {
        ProblemCode.BUSY,
        ProblemCode.UNAVAILABLE,
    }
)


class Problem(BaseModel):
    """跨模块错误载荷。

    Attributes:
        code: 机器可读分类（唯一判据）
        capability: 发生位置，如 ``"nodes.get"``
        detail: 人类可读说明
        details: 结构化补充（必须 JSON 可往返）
        retryable: 调用方可否原样重试
        trace_id: 贯穿日志的关联 ID
    """

    code: ProblemCode
    capability: str = ""
    detail: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    trace_id: str = ""

    @classmethod
    def make(
        cls,
        code: ProblemCode,
        *,
        capability: str = "",
        detail: str = "",
        details: dict[str, Any] | None = None,
        trace_id: str = "",
        retryable: bool | None = None,
    ) -> Problem:
        """构造问题载荷，``retryable`` 默认按错误码推导。

        Args:
            code: 错误码
            capability: 发生位置
            detail: 人类可读说明
            details: 结构化补充
            trace_id: 关联 ID
            retryable: 显式覆盖可重试性；``None`` 表示按错误码推导

        Returns:
            构造好的问题载荷
        """
        return cls(
            code=code,
            capability=capability,
            detail=detail,
            details=dict(details or {}),
            retryable=code in _RETRYABLE if retryable is None else retryable,
            trace_id=trace_id,
        )
