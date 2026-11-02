# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""向后兼容模块：命令安全分类与审批的唯一实现已迁至
``ghrah.ability.permissions.command_safety``。

新代码请直接 ``from ghrah.ability.permissions.command_safety import ...``。
保留本模块只为旧调用方不中断；两个导入路径返回同一批类对象，跨模块
``isinstance`` 判断成立。
"""

from __future__ import annotations

from ghrah.ability.permissions.command_safety import (
    DEFAULT_DANGEROUS_COMMANDS,
    DEFAULT_DANGEROUS_SUB_COMMANDS,
    DEFAULT_SAFE_COMMANDS,
    DEFAULT_SAFE_SUB_COMMANDS,
    CommandApprovalHook,
    CommandSafetyCategory,
    CommandSafetyChecker,
    CommandSafetyVerdict,
)

__all__ = [
    "CommandSafetyCategory",
    "CommandSafetyVerdict",
    "CommandSafetyChecker",
    "CommandApprovalHook",
    "DEFAULT_SAFE_COMMANDS",
    "DEFAULT_DANGEROUS_COMMANDS",
    "DEFAULT_SAFE_SUB_COMMANDS",
    "DEFAULT_DANGEROUS_SUB_COMMANDS",
]
