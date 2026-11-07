# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""旧导入路径，执行实现由新版 Ability 提供。"""

from ghrah.ability.paths import (
    ABILITY_PATH_SPECS,
    AbilityPathSpec,
    extract_paths,
    is_subpath,
    resolve_fs_permission_paths,
    resolve_relative_path,
)

__all__ = [
    "ABILITY_PATH_SPECS",
    "AbilityPathSpec",
    "extract_paths",
    "is_subpath",
    "resolve_fs_permission_paths",
    "resolve_relative_path",
]
