# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""向后兼容模块：HITL 包装件的唯一实现已迁至
``ghrah.ability.permissions.hitl``。

新代码请直接 ``from ghrah.ability.permissions.hitl import ...``。保留本模块只为
旧调用方不中断；两个导入路径返回同一批类对象，跨模块 ``isinstance`` 判断成立。
"""

from __future__ import annotations

from ghrah.ability.permissions.hitl import (
    HITLApprovalHook,
    HITLRequiredAbility,
)

__all__ = [
    "HITLApprovalHook",
    "HITLRequiredAbility",
]
