# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 的收敛点：共享骨架 + Core 的异常族与翻译表。"""

from __future__ import annotations

from collections.abc import Mapping

from ghrah.protocol.interface import Handler, InterfaceRuntime

from ghrah.core.api.errors import CoreApiError, translate
from ghrah.core.api.interfaces import CORE_INTERFACES

__all__ = ["CoreRuntime"]


class CoreRuntime(InterfaceRuntime):
    """Core 内部能力的唯一入口。

    Args:
        handlers: 能力名 → 处理器
    """

    def __init__(self, handlers: Mapping[str, Handler]) -> None:
        super().__init__(
            registry=CORE_INTERFACES,
            handlers=handlers,
            translator=translate,
            error_factory=CoreApiError,
        )


def build_runtime(handlers: Mapping[str, Handler]) -> CoreRuntime:
    """构造 Core 收敛点（装配层的统一入口）。

    Args:
        handlers: 能力名 → 处理器

    Returns:
        已校验能力齐全性的 CoreRuntime
    """
    return CoreRuntime(handlers)
