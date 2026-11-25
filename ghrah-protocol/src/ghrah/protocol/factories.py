# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""消息构造工厂函数。"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel

from ghrah.protocol.enums import EventType, SystemType
from ghrah.protocol.envelope import Envelope
from ghrah.protocol.payloads import CommandResultPayload, ErrorPayload

# ─── 工厂函数（payload 存模型实例，序列化由 model_dump 统一处理）───


def create_command_result(
    request_id: str,
    success: bool,
    data: Any = None,
    error: str | None = None,
    error_detail: str | None = None,
    *,
    details: dict[str, Any] | None = None,
) -> Envelope:
    """创建命令结果消息的便捷函数。"""
    return Envelope(
        type=SystemType.COMMAND_RESULT.value,
        payload=CommandResultPayload(
            request_id=request_id,
            success=success,
            data=data,
            error=error,
            error_detail=error_detail,
            details=details,
        ),
        request_id=request_id,
    )


def create_event(event_type: EventType, payload: BaseModel) -> Envelope:
    """创建事件消息的便捷函数。"""
    return Envelope(
        type=event_type.value,
        payload=payload,
    )


def create_error(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> Envelope:
    """创建错误消息的便捷函数。"""
    return Envelope(
        type=SystemType.ERROR.value,
        payload=ErrorPayload(
            code=code,
            message=message,
            details=details,
        ),
        request_id=request_id,
    )


def create_ping() -> Envelope:
    """创建心跳 ping 消息。"""
    return Envelope(type=SystemType.PING.value)


def create_pong() -> Envelope:
    """创建心跳 pong 消息。"""
    return Envelope(type=SystemType.PONG.value)


def generate_request_id() -> str:
    """生成唯一的请求ID。"""
    return uuid.uuid4().hex[:12]
