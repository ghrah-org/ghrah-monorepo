# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""LLM 领域异常。"""

from __future__ import annotations

from ghrah.core._base_error import ActorAgentError

__all__ = ["LLMError", "UnsupportedFormatError", "UnsupportedFormat"]


class LLMError(ActorAgentError):
    """LLM 调用相关错误"""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"LLM[{provider}]: {message}")


class UnsupportedFormatError(LLMError):
    """端点声明了当前运行时尚不支持的接口格式。"""


UnsupportedFormat = UnsupportedFormatError
