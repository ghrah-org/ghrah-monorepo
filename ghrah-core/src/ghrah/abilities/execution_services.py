# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""向后兼容模块：服务表的唯一实现已迁至 ``ghrah.ability.execution.execution_services``。

新代码请直接 ``from ghrah.ability.execution.execution_services import ...``。保留
本模块只为旧调用方不断链；两个导入路径返回同一批类与键对象。
"""

from __future__ import annotations

from ghrah.ability.execution.execution_services import (
    ABILITY_CONTENT,
    CONTEXT_MANAGER,
    LOOP,
    MANIFEST_STORE,
    MCP_RUNTIME,
    MESSAGE_FACTORY,
    SUPERVISOR,
    ExecutionServices,
    ServiceKey,
    build_execution_services,
)

__all__ = [
    "ABILITY_CONTENT",
    "CONTEXT_MANAGER",
    "LOOP",
    "MANIFEST_STORE",
    "MCP_RUNTIME",
    "MESSAGE_FACTORY",
    "SUPERVISOR",
    "ExecutionServices",
    "ServiceKey",
    "build_execution_services",
]
