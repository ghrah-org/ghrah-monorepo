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
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "command_entry_covers",
    "command_in_scope",
    "normalize_command_entry",
]


# 只解析当前支持的命令入口，不猜测未知选项是否带值。
_VALUE_OPTIONS = {
    "git": {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"},
    "docker": {"-H", "--host", "--context", "--config", "-l", "--log-level"},
    "kubectl": {
        "-n",
        "--namespace",
        "--context",
        "--cluster",
        "--user",
        "--kubeconfig",
        "--server",
        "-s",
        "--token",
        "--certificate-authority",
        "--client-certificate",
        "--client-key",
        "--request-timeout",
    },
    "npm": {"--prefix", "--userconfig", "--cache", "--registry", "--workspace", "-w", "--loglevel"},
}
_FLAG_OPTIONS = {
    "git": {
        "--no-pager",
        "--paginate",
        "--bare",
        "--no-optional-locks",
        "--version",
        "--help",
        "-h",
    },
    "docker": {"--debug", "-D", "--tls", "--tlsverify", "--version", "-v", "--help"},
    "kubectl": {"--help", "--warnings-as-errors"},
    "npm": {"--global", "-g", "--silent", "--verbose", "--version", "--help"},
}


@dataclass(frozen=True)
class ParsedCommand:
    """有界命令入口解析结果；不能确认语义时不允许白名单放行。"""

    base: str = ""
    sub: str | None = None
    arguments: tuple[str, ...] = ()
    ambiguous: bool = False


def parse_command_entry(text: str) -> ParsedCommand:
    """跳过已知带值选项；未知前置选项、shell 组合或展开均显式拒绝。"""
    try:
        parts = shlex.split(text)
    except ValueError:
        return ParsedCommand(ambiguous=True)
    if not parts:
        return ParsedCommand()
    base = Path(parts[0]).name.lower()
    quote = None
    escaped = False
    for character in text:
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
        elif quote is not None:
            if character == quote:
                quote = None
            elif quote == '"' and character in "$`":
                return ParsedCommand(base=base, ambiguous=True)
        elif character in "\"'":
            quote = character
        elif character in ";&|<>`$()\n":
            return ParsedCommand(base=base, ambiguous=True)
    index = 1
    while index < len(parts):
        token = parts[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-"):
            break
        option = token.split("=", 1)[0]
        if option in _VALUE_OPTIONS.get(base, set()):
            if "=" not in token:
                index += 1
                if index >= len(parts):
                    return ParsedCommand(base=base, ambiguous=True)
        elif base in _VALUE_OPTIONS and option not in _FLAG_OPTIONS.get(base, set()):
            # 常见短选项的紧接值，如 -Csub、-ndefault。
            if not any(
                token.startswith(opt) and len(token) > len(opt)
                for opt in _VALUE_OPTIONS[base]
                if opt.startswith("-") and len(opt) == 2
            ):
                return ParsedCommand(base=base, ambiguous=True)
        index += 1
    sub = None
    tail = tuple(parts[index:])
    for token in tail:
        if token.startswith("-") or "/" in token or ("." in token and not token.startswith(".")):
            continue
        sub = token.lower()
        break
    return ParsedCommand(base, sub, tail)


def normalize_command_entry(text: str) -> tuple[str, str | None]:
    """将声明与调用归一为相同入口；不确定的声明不能制造匹配。"""
    parsed = parse_command_entry(text)
    return ("", None) if parsed.ambiguous else (parsed.base, parsed.sub)


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
