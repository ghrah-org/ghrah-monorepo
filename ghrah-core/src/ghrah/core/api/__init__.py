# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 的 API 层：端口实现、收敛点、能力清单。

分层（依赖只能向下）：

```
adapters → 无内部依赖（只认存储与 DTO）
errors   → adapters, protocol.problem
interfaces → protocol.interface, protocol.views
runtime  → interfaces, errors, protocol.interface
handlers → adapters, interfaces, errors
facade   → runtime, protocol.ports
wiring   → 全部
```

消费方只 import ``ghrah.protocol.ports``；本包对它不可见。
"""

from __future__ import annotations

from ghrah.core.api.errors import CoreApiError, translate
from ghrah.core.api.facade import CoreFacade
from ghrah.core.api.handlers import build_handlers
from ghrah.core.api.interfaces import CORE_INTERFACES
from ghrah.core.api.runtime import CoreRuntime, build_runtime
from ghrah.core.read.chain import (
    ChainDatabaseUnavailableError,
    ChainReadStore,
)

__all__ = [
    "CORE_INTERFACES",
    "ChainDatabaseUnavailableError",
    "ChainReadStore",
    "CoreApiError",
    "CoreFacade",
    "CoreRuntime",
    "build_handlers",
    "build_runtime",
    "translate",
]
