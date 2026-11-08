# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Ability 关系库的可映射拒绝；单独成模块避免接纳层与存储层循环导入。"""

from __future__ import annotations

from ghrah.protocol.ability_tree import TreeErrorCode

__all__ = ["AccessError"]


class AccessError(ValueError):
    """可映射为领域结果的明确拒绝。

    Args:
        code: 协议闭集里的错误码；对外只暴露它。
        reason: 进程内诊断原因（如 ``tool_revoked``、``arguments_out_of_scope``），
            **不进协议**；只供执行入口翻译成更具体的模型可读文案，不泄露修订号。
    """

    def __init__(self, code: TreeErrorCode, reason: str | None = None) -> None:
        self.code = code
        self.reason = reason
        super().__init__(code)
