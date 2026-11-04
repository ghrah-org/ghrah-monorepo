# SPDX-License-Identifier: Apache-2.0
"""文件切片明确支持的成员及参数映射，不按未知成员猜测执行入口。"""

from dataclasses import dataclass
from types import MappingProxyType

from ghrah.ability.builtin.fs.operations.list_directory import ListDirectoryAbility
from ghrah.ability.builtin.fs.operations.read_file import ReadFileAbility
from ghrah.ability.execution.base import Ability


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


def supports_file_operation(definition: dict, key: str) -> bool:
    """后端按显式槽契约匹配，不能因成员名相同而替未知后端执行。"""
    operation = definition["operations"].get(key)
    if key not in FILE_OPERATIONS or operation is None or operation.get("script") is not None:
        return False
    route = operation["implementation"]
    requirement = definition["provider_requirements"].get(route["provider_slot"], {})
    return (
        route["operation"] == key
        and requirement.get("contract") == "ghrah.fs.v1"
        and set(requirement.get("required_capabilities", [])) <= {"request"}
    )
