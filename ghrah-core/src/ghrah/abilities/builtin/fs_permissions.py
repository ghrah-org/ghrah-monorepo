# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""向后兼容模块：文件系统权限实现的唯一真相已迁至
``ghrah.ability.permissions.fs_permissions``。

新代码请直接 ``from ghrah.ability.permissions.fs_permissions import ...``。
保留本模块只为旧调用方不中断；两个导入路径返回同一批类对象，跨模块
``isinstance`` 判断成立。
"""

from __future__ import annotations

from ghrah.ability.permissions.fs_permissions import (
    AccessApprovalHook,
    FSPermissionChecker,
    WriteApprovalHook,
)

__all__ = [
    "FSPermissionChecker",
    "AccessApprovalHook",
    "WriteApprovalHook",
]
