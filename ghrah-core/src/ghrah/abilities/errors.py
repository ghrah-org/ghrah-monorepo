# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""旧导入路径，执行实现由新版 Ability 提供。"""

from ghrah.ability.execution.errors import AbilityError, AbilityNotFoundError

__all__ = ["AbilityError", "AbilityNotFoundError"]
