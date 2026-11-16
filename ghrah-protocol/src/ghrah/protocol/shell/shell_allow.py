# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Shell 放行的共用纯函数；调用方提供分类结果与项目记录，不在此读写数据库。"""

from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from bash_classify import Classification
from bash_classify.models import CommandResult, ExpressionResult, InnerCommandResult

from ghrah.protocol.payloads.agent import ShellRememberOptions
from ghrah.protocol.shell.bash_classify_frontend import (
    classify_command_expression,
    iter_classified_segments,
    segment_base_sub,
)

__all__ = [
    "ShellRememberOptions",
    "ShellAllowLookup",
    "normalize_shell_command",
    "irreversible_reason",
    "is_builtin_shell_expression",
    "extract_shell_class",
    "build_shell_remember_options",
    "matches_shell_allow",
    "is_shell_expression_allowed",
]

type ShellAllowLookup = Callable[[], Awaitable[Sequence[Mapping[str, str]]]]

_Segment = CommandResult | InnerCommandResult
_INTERPRETERS = frozenset(
    {"python", "python3", "node", "bash", "sh", "dash", "zsh", "perl", "ruby"}
)
_PACKAGE_FLAGS = frozenset({"-r", "--recursive", "-w", "--workspace-root"})
_PACKAGE_VALUES = frozenset({"--filter", "--dir"})
_UV_FLAGS = frozenset(
    {
        "--offline",
        "--locked",
        "--frozen",
        "--no-sync",
        "--no-project",
        "--isolated",
        "--active",
        "--no-dev",
        "--only-dev",
        "--all-extras",
        "--all-groups",
        "--no-default-groups",
    }
)
_UV_VALUES = frozenset(
    {
        "--directory",
        "--project",
        "--package",
        "--python",
        "-p",
        "--with",
        "-w",
        "--with-editable",
        "--with-requirements",
        "--extra",
        "--no-extra",
        "--group",
        "--no-group",
        "--only-group",
        "--env-file",
        "--config-file",
    }
)
_SCRIPT_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*\Z")
_PACKAGE_COMMANDS = frozenset({"exec", "dlx", "create", "install", "add", "remove", "publish"})


DEFAULT_DANGEROUS_COMMANDS: set[str] = {
    "rm",
    "rmdir",
    "chmod",
    "chown",
    "chgrp",
    "mkfs",
    "dd",
    "format",
    "fdisk",
    "parted",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "kill",
    "killall",
    "pkill",
    "useradd",
    "userdel",
    "usermod",
    "groupadd",
    "groupdel",
    "passwd",
    "su",
    "sudo",
    "doas",
}


DEFAULT_DANGEROUS_SUB_COMMANDS: dict[str, set[str]] = {
    "git": {
        "clean",
    },
    "npm": {
        "publish",
    },
    "docker": {
        "rmi",
    },
    "kubectl": {
        "delete",
    },
}


def normalize_shell_command(command: str) -> str:
    """使用完整原始命令作为 exact 键，保留引号、操作符及所有空白。"""
    if not command.strip():
        raise ValueError("Empty shell command")
    return command


def _skip_options(
    argv: Sequence[str], start: int, flags: frozenset[str], values: frozenset[str]
) -> int | None:
    index = start
    while index < len(argv) and argv[index].startswith("-"):
        token = argv[index]
        option, separator, value = token.partition("=")
        if token == "--":
            return index + 1
        if token in flags:
            index += 1
        elif option in values:
            if separator:
                if not value:
                    return None
                index += 1
            else:
                if index + 1 >= len(argv) or argv[index + 1].startswith("-"):
                    return None
                index += 2
        else:
            return None
    return index


def _uv_inner(argv: Sequence[str]) -> tuple[str, ...] | None:
    if not argv or Path(argv[0]).name != "uv":
        return None
    index = _skip_options(argv, 1, _UV_FLAGS, _UV_VALUES)
    if index is None or index >= len(argv) or argv[index] != "run":
        return None
    index = _skip_options(argv, index + 1, _UV_FLAGS, _UV_VALUES)
    return tuple(argv[index:]) if index is not None and index < len(argv) else None


def _package_script(argv: Sequence[str]) -> tuple[str, str] | None:
    if not argv or Path(argv[0]).name not in {"pnpm", "npm"}:
        return None
    base = Path(argv[0]).name
    index = _skip_options(argv, 1, _PACKAGE_FLAGS, _PACKAGE_VALUES)
    if index is None or index >= len(argv):
        return None
    explicit_run = argv[index] == "run"
    if explicit_run:
        index = _skip_options(argv, index + 1, _PACKAGE_FLAGS, _PACKAGE_VALUES)
        if index is None or index >= len(argv):
            return None
    script = argv[index]
    if not _SCRIPT_NAME.fullmatch(script) or script in _PACKAGE_COMMANDS | _INTERPRETERS:
        return None
    # 只接受递归/包选择选项，不把额外脚本参数扩大成类别。
    end = _skip_options(argv, index + 1, _PACKAGE_FLAGS, _PACKAGE_VALUES)
    if end != len(argv):
        return None
    return base, "test" if script == "t" and not explicit_run else script


def irreversible_reason(
    expression: ExpressionResult,
    *,
    dangerous_commands: set[str] | None = None,
    dangerous_sub_commands: dict[str, set[str]] | None = None,
) -> str | None:
    """返回必须拒绝的原因；复用现有危险名单并检查委托内层和路径裁定。"""
    if dangerous_commands is None:
        dangerous_commands = DEFAULT_DANGEROUS_COMMANDS
    if dangerous_sub_commands is None:
        dangerous_sub_commands = DEFAULT_DANGEROUS_SUB_COMMANDS

    if not expression.commands or expression.parse_warnings:
        return "Failed to parse command structure safely"
    if expression.sensitive_paths:
        return "Command touches sensitive paths"
    segments = iter_classified_segments(expression)
    write_paths = set(expression.write_paths)
    for segment in segments:
        write_paths.update(segment.write_paths or ())
    # 用分类器现有的路径规则独立检查写目标，避免把脚本入口的危险分类当作路径危险。
    for path in write_paths:
        if (
            classify_command_expression(f"echo > {shlex.quote(path)}").classification
            is Classification.DANGEROUS
        ):
            return "Dangerous write or redirection"
    for segment in segments:
        if segment.sensitive_paths:
            return "Command touches sensitive paths"
        base, sub = segment_base_sub(segment.argv)
        if base in DEFAULT_DANGEROUS_COMMANDS or base in dangerous_commands:
            return f"Dangerous command: {base}"
        if sub in DEFAULT_DANGEROUS_SUB_COMMANDS.get(base, ()) or sub in dangerous_sub_commands.get(
            base, ()
        ):
            return f"Dangerous sub-command: {base} {sub}"
        args = segment.argv[1:]
        destructive = (
            (base == "git" and sub == "reset" and "--hard" in args)
            or (
                base == "git"
                and sub in {"branch", "tag"}
                and any(
                    arg == "--delete"
                    or (
                        arg.startswith("-")
                        and not arg.startswith("--")
                        and any(letter in arg[1:] for letter in "dD")
                    )
                    for arg in args
                )
            )
            or (
                base == "git"
                and sub == "push"
                and any(
                    arg.startswith("--force")
                    or (arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:])
                    for arg in args
                )
            )
            or (base == "docker" and sub == "rm")
            or (base == "docker" and sub == "system" and "prune" in args)
        )
        if destructive:
            return f"Destructive command: {base} {sub}"
        # 委托结果的 argv 不保留脚本内重定向，需要重新分类 -c 的完整脚本。
        if base in {"bash", "sh", "dash", "zsh"}:
            for option, script in zip(segment.argv, segment.argv[1:]):
                if option == "-c":
                    reason = irreversible_reason(
                        classify_command_expression(script),
                        dangerous_commands=dangerous_commands,
                        dangerous_sub_commands=dangerous_sub_commands,
                    )
                    if reason is not None:
                        return reason
                    break
        # 库对 uv 的带值选项委托可能偏移，使用同一分类器重查实际内层。
        inner = _uv_inner(segment.argv)
        if base == "uv" and sub == "run" and inner is None:
            return "Cannot resolve uv run command structure"
        if inner is not None:
            reason = irreversible_reason(
                classify_command_expression(shlex.join(inner)),
                dangerous_commands=dangerous_commands,
                dangerous_sub_commands=dangerous_sub_commands,
            )
            if reason is not None:
                return reason
    return None


def _builtin_segment(segment: _Segment) -> bool:
    inner = _uv_inner(segment.argv)
    if inner is not None:
        base = Path(inner[0]).name
        if base not in {"pytest", "ruff"}:
            return False
        classified = classify_command_expression(shlex.join(inner))
        return (
            classified.classification not in {Classification.DANGEROUS, Classification.UNKNOWN}
            and not classified.parse_warnings
            and not classified.sensitive_paths
        )
    return _package_script(segment.argv) == ("pnpm", "test")


def is_builtin_shell_expression(expression: ExpressionResult) -> bool:
    """只判断整条表达式是否符合系统预设脚本形态。"""
    return (
        bool(expression.commands)
        and not expression.redirects
        and all(_builtin_segment(segment) for segment in expression.commands)
    )


def _segment_class(segment: _Segment) -> str | None:
    inner = _uv_inner(segment.argv)
    if inner is not None:
        base = Path(inner[0]).name
        if base in _INTERPRETERS or re.fullmatch(r"python\d+(?:\.\d+)*", base):
            return None
        return f"uv:run:{base}" if _SCRIPT_NAME.fullmatch(base) else None
    script = _package_script(segment.argv)
    return f"{script[0]}:script:{script[1]}" if script else None


def extract_shell_class(expression: ExpressionResult) -> str | None:
    """所有顶层调用具有同一封闭形态时返回一个类别键，否则返回 None。"""
    if expression.redirects:
        return None
    keys = {_segment_class(segment) for segment in expression.commands}
    return next(iter(keys)) if len(keys) == 1 and None not in keys else None


def build_shell_remember_options(
    expression: ExpressionResult, *, command: str
) -> ShellRememberOptions:
    """为可审批命令生成整条及可选类别候选；只应在 REQUIRE_HITL 分支调用。"""
    exact = normalize_shell_command(command)
    category = extract_shell_class(expression)
    return ShellRememberOptions(
        exact_key=exact, exact_label=exact, class_key=category, class_label=category
    )


def matches_shell_allow(
    expression: ExpressionResult,
    entries: Sequence[Mapping[str, str]],
    *,
    command: str,
) -> bool:
    """只匹配调用方传入的单项目记录，不判断危险和内置规则。"""
    exact = normalize_shell_command(command)
    if any(entry["kind"] == "exact" and entry["key"] == exact for entry in entries):
        return True
    if expression.redirects:
        return False
    classes = {entry["key"] for entry in entries if entry["kind"] == "class"}
    return bool(expression.commands) and all(
        (key := _segment_class(segment)) is not None and key in classes
        for segment in expression.commands
    )


def is_shell_expression_allowed(
    expression: ExpressionResult,
    entries: Sequence[Mapping[str, str]],
    *,
    command: str,
) -> bool:
    """组合内置与项目放行结果；调用方须先完成危险判断和路径权限检查。"""
    if matches_shell_allow(expression, entries, command=command):
        return True
    if expression.redirects:
        return False
    classes = {entry["key"] for entry in entries if entry["kind"] == "class"}
    return bool(expression.commands) and all(
        _builtin_segment(segment) or _segment_class(segment) in classes
        for segment in expression.commands
    )
