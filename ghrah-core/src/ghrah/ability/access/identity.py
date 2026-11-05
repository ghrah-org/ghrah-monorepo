# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""新版授权边界的稳定主体校验；名称不能被当成恢复凭据。"""

from __future__ import annotations

from uuid import UUID


def validate_agent_id(value: str) -> str:
    """只接受规范的 32 位小写 UUID，不转换名称或猜测主体。"""
    try:
        if UUID(value).hex == value:
            return value
    except ValueError, AttributeError, TypeError:
        pass
    raise ValueError("stable Agent ID must be a canonical 32-character lowercase UUID")
