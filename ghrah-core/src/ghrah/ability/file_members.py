# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""文件切片明确支持的成员及参数映射，不按未知成员猜测执行入口。"""

from dataclasses import dataclass
from types import MappingProxyType

from ghrah.ability.builtin.fs.operations.list_directory import ListDirectoryAbility
from ghrah.ability.builtin.fs.operations.read_file import ReadFileAbility
from ghrah.ability.execution.base import Ability
from ghrah.ability.provider_contracts import FS_CONTRACT, contract_for


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
    """成员是否由文件切片后端执行。

    v1 索引不写 ``provider_requirements`` / ``provider_slot``：后端由包来源与
    身份决定（见 :func:`ghrah.ability.provider_contracts.contract_for`），只有
    内置 ``ghrah.fs`` 的已登记成员才允许文件切片执行，不能因成员名相同而替
    未知后端执行。
    """
    operation = definition.get("operations", {}).get(key)
    if key not in FILE_OPERATIONS or not isinstance(operation, dict):
        return False
    entry = operation.get("entry")
    if not isinstance(entry, dict) or entry.get("path") != f"operations/{key}.py":
        return False
    identity = definition.get("identity") or {}
    return (
        contract_for(
            definition.get("origin") or "source",
            identity.get("namespace", ""),
            identity.get("name"),
        )
        == FS_CONTRACT
    )
