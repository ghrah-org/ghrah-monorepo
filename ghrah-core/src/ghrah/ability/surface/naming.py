# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""工具名算法：成员键 → 模型可见的工具名。

算法是纯函数：输入是授权面内的一组 ``(包名, 成员键)``，输出按工具名
升序排列的命名结果。同一组输入重复计算结果相同，与输入顺序无关；
重名、保留名、非法键都有确定结果，不做静默改名。

命名只决定"模型看到什么名字"，不改变成员语义：``tool_name`` 到成员的
映射是唯一权威，调用时只查表，不拼模块路径、不拼服务器地址。
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass

__all__ = [
    "META_TOOL_NAMES",
    "RESERVED_TOOL_NAMES",
    "RUN_SCRIPT_TOOL_NAME",
    "TOOL_NAME_MAX",
    "TOOL_NAME_PATTERN",
    "NamedMember",
    "ToolNameError",
    "assign_tool_names",
]

TOOL_NAME_MAX: int = 64
"""工具名最大长度（兼容主流提供方的 function name 约束）。"""

TOOL_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
"""工具名合法字符集。"""

META_TOOL_NAMES: frozenset[str] = frozenset({"search_tools", "describe_tool", "execute_tool"})
"""内置 core 包占用的三个元工具名。"""

RUN_SCRIPT_TOOL_NAME: str = "run_script"
"""表面层占用的脚本调用工具名；内置 core 包也不得占用。"""

RESERVED_TOOL_NAMES: frozenset[str] = META_TOOL_NAMES | {RUN_SCRIPT_TOOL_NAME}
"""全部保留名：元工具由 core 包占用，``run_script`` 由表面层占用。"""

_MEMBER_KEY_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PACKAGE_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_-]*$")
_TRUNCATE_PREFIX: int = 54
_HASH_HEX_LEN: int = 8


class ToolNameError(ValueError):
    """工具名算法拒绝：非法键、保留名或无法消解的冲突。"""


@dataclass(frozen=True, slots=True)
class NamedMember:
    """一次命名的结果：工具名与它指向的 ``(包名, 成员键)``。"""

    tool_name: str
    package: str
    member: str


def assign_tool_names(
    members: Iterable[tuple[str, str]],
    *,
    allow_reserved: Collection[str] = (),
) -> list[NamedMember]:
    """为授权面内的成员分配工具名。

    规则（确定性，与输入顺序无关）：

    1. 同一成员键在输入中唯一时，``tool_name = 成员键``；
    2. 同一成员键出现多次时，全部改为 ``<包名>__<成员键>``，两段里的
       ``-`` 换成 ``_``；
    3. 超过 64 字符时，取前 54 字符 + ``__`` + 候选名的 sha256 前 8 位
       十六进制；
    4. 最终名字仍冲突时拒绝，不静默改名；
    5. 保留名只允许显式豁免的 core 元工具名。

    Args:
        members: ``(包名, 成员键)`` 序列；包名取容器名，成员键取索引键。
        allow_reserved: 允许占用保留名的成员键集合。只允许 core 的三个
            元工具名；传 ``run_script`` 或其他名字即拒绝。

    Returns:
        按 ``tool_name`` 升序排列的命名结果。

    Raises:
        ToolNameError: 包名或成员键非法、占用保留名、或最终名字冲突。
    """
    allowed = frozenset(allow_reserved)
    unknown = allowed - META_TOOL_NAMES
    if unknown:
        raise ToolNameError(
            f"allow_reserved 只允许 core 元工具名 {sorted(META_TOOL_NAMES)}，收到 {sorted(unknown)}"
        )

    # 先校验再排序：排序让错误信息与处理顺序稳定，与调用方传入顺序无关。
    pairs: list[tuple[str, str]] = []
    for package, member in members:
        if not isinstance(package, str) or not isinstance(member, str):
            raise ToolNameError(f"包名与成员键必须是字符串：{package!r}, {member!r}")
        if not _PACKAGE_NAME_PATTERN.fullmatch(package):
            raise ToolNameError(f"非法包名：{package!r}")
        if not _MEMBER_KEY_PATTERN.fullmatch(member):
            raise ToolNameError(f"非法成员键：{member!r}")
        pairs.append((member, package))
    pairs.sort()

    duplicate = Counter(member for member, _ in pairs)
    named: dict[str, NamedMember] = {}
    for member, package in pairs:
        if member in RESERVED_TOOL_NAMES and member not in allowed:
            raise ToolNameError(f"成员键 {member!r} 占用了保留的工具名")
        if duplicate[member] == 1:
            candidate = member
        else:
            candidate = f"{package.replace('-', '_')}__{member.replace('-', '_')}"
        if len(candidate) > TOOL_NAME_MAX:
            digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:_HASH_HEX_LEN]
            candidate = f"{candidate[:_TRUNCATE_PREFIX]}__{digest}"
        if not TOOL_NAME_PATTERN.fullmatch(candidate):
            raise ToolNameError(f"工具名 {candidate!r} 不在合法字符集内")
        if candidate in RESERVED_TOOL_NAMES and candidate not in allowed:
            raise ToolNameError(f"工具名 {candidate!r} 占用了保留的工具名")
        existing = named.get(candidate)
        if existing is not None:
            raise ToolNameError(
                f"工具名冲突：{candidate!r} 同时来自 "
                f"{existing.package}/{existing.member} 与 {package}/{member}"
            )
        named[candidate] = NamedMember(tool_name=candidate, package=package, member=member)

    return sorted(named.values(), key=lambda item: item.tool_name)
