# SPDX-License-Identifier: Apache-2.0
"""文件切片明确支持的成员及参数映射，不按未知成员猜测执行入口。"""

from dataclasses import dataclass
from types import MappingProxyType

from ghrah.scheme_ability.ability.fs.operations.list_directory import ListDirectoryAbility
from ghrah.scheme_ability.ability.fs.operations.read_file import ReadFileAbility
from ghrah.scheme_ability.execution.base import Ability


@dataclass(frozen=True)
class FileMember:
    """一个只读后端及其路径参数；新增成员必须显式登记。"""

    operation: type[Ability]
    path_argument: str


FILE_OPERATIONS = MappingProxyType(
    {
        "read_file": FileMember(ReadFileAbility, "file_path"),
        "list_directory": FileMember(ListDirectoryAbility, "dir_path"),
    }
)
