# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""向后兼容模块：调用值对象已迁至 ``ghrah.ability.execution.invocation``。

新代码请直接 ``from ghrah.ability.execution.invocation import ...``。旧名
``AbilityInvocation``（能力名 + 参数 + ``tool_call_id``）已被
``OperationCall`` 取代，不再保留。
"""

from __future__ import annotations

from ghrah.ability.execution.invocation import (
    InvocationIdentity,
    MemberReference,
    OperationCall,
)

__all__ = [
    "InvocationIdentity",
    "MemberReference",
    "OperationCall",
]
