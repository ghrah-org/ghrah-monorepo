# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""向后兼容模块：Hook 控制流的唯一实现已迁至 ``ghrah.ability.execution.hooks``。

新代码请直接 ``from ghrah.ability.execution.hooks import ...``。保留本模块只为
旧调用方（``ghrah.abilities`` 包内与外部示例）不断链；两个导入路径返回同一批
类对象，因此跨模块的 ``isinstance`` 判断成立，``HookPoint`` 也只有一个枚举类型。
"""

from __future__ import annotations

from ghrah.ability.execution.hooks import (
    Hook,
    HookPoint,
    HookResult,
    HookScope,
)

__all__ = [
    "HookScope",
    "HookPoint",
    "HookResult",
    "Hook",
]
