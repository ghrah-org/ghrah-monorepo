# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""系统载荷模型（command_result / error）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

# ─── 系统载荷模型 ───


class CommandResultPayload(BaseModel):
    """command_result 系统载荷。"""

    request_id: str
    success: bool
    data: Any = None
    error: str | None = None
    error_detail: str | None = None
    details: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.details is None:
            data.pop("details", None)
        return data


class ErrorPayload(BaseModel):
    """error 系统载荷。"""

    code: str
    message: str
    details: dict[str, Any] | None = None
