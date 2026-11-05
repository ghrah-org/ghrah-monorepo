# SPDX-License-Identifier: Apache-2.0
"""命令白名单的纯字符串匹配：基础命令 + 可选子命令（两段式）。

白名单条目与命令调用都归一成 ``(基础命令, 子命令或 None)``：

- ``git`` 覆盖任意 ``git ...``；
- ``git status`` 只覆盖 ``git status ...``；
- 第三个及之后的参数不参与匹配（声明只表达“命令 + 子命令”粒度）。

本模块不依赖运行时对象，供有效权限范围解析与命令检查器共用，避免两处
各自实现一套“两段式”解释。
"""

from __future__ import annotations

import shlex
from pathlib import Path

__all__ = [
    "command_entry_covers",
    "command_in_scope",
    "normalize_command_entry",
]


def normalize_command_entry(text: str) -> tuple[str, str | None]:
    """把命令白名单条目归一为 ``(基础命令, 子命令或 None)``。

    空条目返回 ``("", None)``，由调用方忽略。
    """
    stripped = text.strip()
    if not stripped:
        return "", None
    try:
        parts = shlex.split(stripped)
    except ValueError:
        parts = stripped.split()
    if not parts:
        return "", None
    base = Path(parts[0]).name.lower()
    sub: str | None = None
    for part in parts[1:]:
        if part.startswith("-"):
            continue
        if "/" in part or ("." in part and not part.startswith(".")):
            continue
        sub = part.lower()
        break
    return base, sub


def command_entry_covers(entry: str, base: str, sub: str | None) -> bool:
    """白名单条目是否覆盖一次命令调用（基础命令相同且子命令匹配）。"""
    entry_base, entry_sub = normalize_command_entry(entry)
    if not entry_base or entry_base != base:
        return False
    if entry_sub is None:
        return True
    return entry_sub == sub


def command_in_scope(entries: tuple[str, ...] | list[str], command: str) -> bool:
    """命令是否落在给出的白名单范围内。"""
    base, sub = normalize_command_entry(command)
    if not base:
        return False
    return any(command_entry_covers(entry, base, sub) for entry in entries)
