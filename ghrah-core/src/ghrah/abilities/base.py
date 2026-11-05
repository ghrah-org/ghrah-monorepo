# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""向后兼容模块：Ability 基类的唯一实现已迁至 ``ghrah.ability.execution.base``。

新代码请直接 ``from ghrah.ability.execution.base import Ability``。保留本模块
只为旧调用方不断链；两个导入路径返回同一个类对象。
"""

from __future__ import annotations

from ghrah.ability.execution.base import (
    Ability,
    ActionOutcome,
    ActionResult,
)

__all__ = [
    "ActionOutcome",
    "ActionResult",
    "Ability",
]
