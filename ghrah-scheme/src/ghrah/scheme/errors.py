# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""控制平面的结构化拒绝与运行错误。"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """原型公开错误分类，不是 wire 枚举。"""

    INVALID_ARGUMENT = "invalid_argument"
    INVALID_PATH = "invalid_path"
    INVALID_REFERENCE = "invalid_reference"
    STALE_REFERENCE = "stale_reference"
    STALE_BINDING = "stale_binding"
    STALE_PROVIDER = "stale_provider"
    SESSION_CLOSED = "session_closed"
    RUNTIME_CLOSED = "runtime_closed"
    WRONG_LOOP = "wrong_loop"
    NOT_FOUND = "not_found"
    CONTRACT_MISMATCH = "contract_mismatch"
    UNKNOWN_ACTION = "unknown_action"
    INVALID_INPUT = "invalid_input"
    INVALID_RESULT = "invalid_result"
    ACCESS_DENIED = "access_denied"
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    PROVIDER_EXISTS = "provider_exists"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_FAILED = "provider_failed"
    BUSY = "busy"
    REFERENCE_LIMIT = "reference_limit"
    REQUEST_KEY_REQUIRED = "request_key_required"
    REQUEST_CONFLICT = "request_conflict"
    REQUEST_EXPIRED = "request_expired"
    REQUEST_KEY_LIMIT = "request_key_limit"
    REENTRANT_CALL = "reentrant_call"
    NESTED_BUSY = "nested_busy"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


class SchemeError(Exception):
    """在直接入口返回稳定错误码，不暴露提供者异常内容。"""

    def __init__(self, code: ErrorCode, message: str = "") -> None:
        self.code = code
        super().__init__(message or code.value)
