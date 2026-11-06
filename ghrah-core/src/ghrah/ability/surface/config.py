# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""表面配置的默认值与绝对上限。

阈值有三个触发条件，取最早发生者；显式 ``static`` 不受硬上限约束，但
受固定绝对上限约束。``SurfaceConfig`` 是 per-Agent 覆盖段，定义在
``ghrah.manifest.types``（manifest 不能反向依赖 ability 包），这里只做
re-export 与默认值常量，避免第二份定义。
"""

from __future__ import annotations

from ghrah.manifest.types import SurfaceConfig

__all__ = [
    "DEFAULT_DEFINITION_TOKEN_RATIO",
    "DEFAULT_HARD_CAP",
    "DEFAULT_LOADED_LIMIT",
    "DEFAULT_MAX_STATIC",
    "DEFAULT_SEARCH_LIMIT",
    "SEARCH_LIMIT_ABS_MAX",
    "STATIC_ABS_LIMIT",
    "SurfaceConfig",
]

DEFAULT_DEFINITION_TOKEN_RATIO: float = 0.02
"""定义 token 占模型上下文窗口的比例上限。"""

DEFAULT_MAX_STATIC: int = 20
"""静态工具数上限（``auto`` 模式下按阈值裁切时使用）。"""

DEFAULT_HARD_CAP: int = 30
"""``auto`` 模式下无论 token 如何都切渐进的总成员数。"""

DEFAULT_SEARCH_LIMIT: int = 8
"""单次搜索结果上限的默认值。"""

SEARCH_LIMIT_ABS_MAX: int = 20
"""单次搜索结果上限的硬上限，不开放配置。"""

DEFAULT_LOADED_LIMIT: int = 8
"""已加载集上限的默认值。"""

STATIC_ABS_LIMIT: int = 64
"""显式 ``static`` 的固定绝对上限；不开放配置，超过即装配期报错。"""
