# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Ability 关系库的可映射拒绝；单独成模块避免接纳层与存储层循环导入。"""

from __future__ import annotations

from ghrah.protocol.ability_tree import TreeErrorCode

__all__ = ["AccessError"]


class AccessError(ValueError):
    """可映射为领域结果的明确拒绝。"""

    def __init__(self, code: TreeErrorCode) -> None:
        self.code = code
        super().__init__(code)
