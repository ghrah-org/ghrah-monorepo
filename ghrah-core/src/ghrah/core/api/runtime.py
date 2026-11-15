# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 的收敛点：共享骨架 + Core 的异常族与翻译表。

两个收敛点，按作用域边界区分：

- :class:`CoreRuntime`：作用域端口的能力入口（清单 = ``CORE_INTERFACES``）；
- :class:`CoreCatalogRuntime`：全局目录端口的能力入口（清单 =
  ``CORE_CATALOG_INTERFACES``，无作用域、不打开数据库）。
"""

from __future__ import annotations

from collections.abc import Mapping

from ghrah.protocol.interface import Handler, InterfaceRuntime

from ghrah.core.api.errors import CoreApiError, translate
from ghrah.core.api.interfaces import CORE_CATALOG_INTERFACES, CORE_INTERFACES

__all__ = ["CoreCatalogRuntime", "CoreRuntime", "build_catalog_runtime", "build_runtime"]


class CoreRuntime(InterfaceRuntime):
    """作用域端口的能力入口。

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


class CoreCatalogRuntime(InterfaceRuntime):
    """全局目录端口的能力入口（无作用域）。

    Args:
        handlers: 能力名 → 处理器
    """

    def __init__(self, handlers: Mapping[str, Handler]) -> None:
        super().__init__(
            registry=CORE_CATALOG_INTERFACES,
            handlers=handlers,
            translator=translate,
            error_factory=CoreApiError,
        )


def build_runtime(handlers: Mapping[str, Handler]) -> CoreRuntime:
    """构造 Core 作用域收敛点（装配层的统一入口）。

    Args:
        handlers: 能力名 → 处理器

    Returns:
        已校验能力齐全性的 CoreRuntime
    """
    return CoreRuntime(handlers)


def build_catalog_runtime(handlers: Mapping[str, Handler]) -> CoreCatalogRuntime:
    """构造 Core 目录收敛点。

    Args:
        handlers: 能力名 → 处理器

    Returns:
        已校验能力齐全性的 CoreCatalogRuntime
    """
    return CoreCatalogRuntime(handlers)
