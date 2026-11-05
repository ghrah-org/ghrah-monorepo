# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""只解释控制信封；业务载荷保持不透明。"""

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

import cbor2

PROFILE = "mechanism-e0"


class ProtocolError(ValueError):
    """无法解释为本实验控制信封的消息。"""


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Envelope:
    kind: Literal["request", "response", "event"]
    req_id: int
    meta: dict[str, Any] = field(default_factory=dict)
    opaque_payload: bytes | None = None
    error: ErrorRecord | None = None
    profile: str = PROFILE

    def without_payload(self) -> Envelope:
        """仅裁切载荷；授权状态由内核引用表核定。"""
        return replace(self, opaque_payload=None)


def encode_envelope(message: Envelope) -> bytes:
    return cbor2.dumps(asdict(message))


def decode_envelope(data: bytes) -> Envelope:
    try:
        fields = cbor2.loads(data)
        if not isinstance(fields, dict):
            raise ValueError("信封必须是 map")
        error = fields.get("error")
        if error is not None:
            fields["error"] = ErrorRecord(**error)
        message = Envelope(**fields)
        if message.profile != PROFILE:
            raise ValueError("不支持的实验 profile")
        if message.kind not in ("request", "response", "event"):
            raise ValueError("不支持的消息种类")
        if type(message.req_id) is not int or message.req_id <= 0:
            raise ValueError("req_id 必须为正整数")
        if not isinstance(message.meta, dict):
            raise ValueError("meta 必须是 map")
        if message.opaque_payload is not None and not isinstance(message.opaque_payload, bytes):
            raise ValueError("opaque_payload 必须是 bytes 或 None")
        if message.error is not None and (
            not isinstance(message.error.code, str) or not isinstance(message.error.message, str)
        ):
            raise ValueError("错误信息字段类型不正确")
        return message
    except (ValueError, TypeError, cbor2.CBORDecodeError) as exc:
        raise ProtocolError(str(exc)) from exc
