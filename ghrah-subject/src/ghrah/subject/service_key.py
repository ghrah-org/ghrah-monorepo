# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Subject 单元在 Ouroboros 容器中声明的类型化服务键。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SubjectServiceKey[T]:
    """以名称标识容器服务，并可附上服务的运行时类型。"""

    name: str
    service_type: type[Any] | tuple[type[Any], ...] = object
