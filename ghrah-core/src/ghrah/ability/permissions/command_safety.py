# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""命令安全检查模块：分类和审批逻辑。

提供：
- CommandSafetyCategory: 命令安全分类枚举（SAFE / DANGEROUS / REQUIRE_HITL）
- CommandSafetyVerdict: 分类裁定结果
- CommandSafetyChecker: 命令安全分类器（bash-classify 前端 + 范围外壳）
- CommandApprovalHook: PRE_EXECUTE Hook，拦截 execute_command Ability

分类模型（判定顺序，2026-11-08 阶段 4）：
1. 空命令 → 硬拒绝
2. ``denied_commands`` 命中任一段 → 硬拒绝
3. 仅 ``--version`` / ``-V`` / ``--help``、无委托无重定向 → 自动批准
4. 无位置参数的 ``git branch`` / ``git tag`` 列表 → 自动批准
5. bash-classify 判 DANGEROUS，或 ghrah 危险叠加 → 硬拒绝
6. ``allowed_commands`` 覆盖全部顶层段 → 自动批准（``UNKNOWN`` 除外）
7. 整式 READONLY+LOW（无 parse_warnings / 敏感路径）→ 自动批准
8. 部署级 SAFE 名单抬升：每段须先过段级门禁（READONLY+LOW 或
   LOCAL_EFFECTS+LOW），再匹配名单；重定向 / 敏感路径 / READONLY+HIGH 禁止抬升
9. ``UNKNOWN``：需审批时 HITL，关闭审批时硬拒绝
10. 工作区写入投影：LOCAL_EFFECTS 且已注入 FS checker 时，路径均在授权
    范围内则自动批准；越界硬拒绝；无法投影则继续
11. 其余：``require_approval`` 为真时审批，否则按 ``deny_out_of_scope`` 决定拒绝或放行
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from bash_classify import Classification, Risk

from ghrah.ability.execution.hooks import Hook, HookPoint, HookResult
from ghrah.ability.permissions.bash_classify_frontend import (
    classify_command_expression,
    expression_is_dangerous,
    expression_is_unknown,
    is_readonly_low,
    iter_classified_segments,
    segment_base_sub,
)
from ghrah.ability.permissions.command_scope import command_entry_covers, parse_command_entry
from ghrah.ability.permissions.shell_write_projection import try_workspace_write_approval

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.invocation import OperationCall
    from ghrah.ability.permissions.fs_permissions import FSPermissionChecker
    from ghrah.types.results import ActionResult

logger = logging.getLogger(__name__)

_VERSION_OR_HELP_FLAGS = frozenset({"--version", "-V", "--help"})
_GIT_BARE_LIST_SUBS = frozenset({"branch", "tag"})


def _is_version_or_help_probe(expression: Any, segments: list[Any]) -> bool:
    """整式每一段都只是版本或帮助探测，且没有委托、重定向、敏感路径。"""
    if getattr(expression, "redirects", None) or getattr(expression, "sensitive_paths", None):
        return False
    if not segments:
        return False
    for segment in segments:
        if getattr(segment, "inner_commands", None):
            return False
        argv = [str(token) for token in (getattr(segment, "argv", ()) or ())]
        if len(argv) != 2 or argv[1] not in _VERSION_OR_HELP_FLAGS:
            return False
    return True


def _is_bare_git_list(expression: Any) -> bool:
    """``git branch`` / ``git tag`` 没有位置参数时只是列出。

    允许 git 全局选项（``-C``、``--no-pager`` 等）。创建、删除和重命名带有
    位置参数或其他标志，不走这条放行。
    """
    if getattr(expression, "redirects", None) or getattr(expression, "sensitive_paths", None):
        return False
    commands = list(getattr(expression, "commands", ()) or ())
    if not commands:
        return False
    for command in commands:
        if getattr(command, "inner_commands", None) or getattr(command, "positionals", None):
            return False
        argv = [str(token) for token in (getattr(command, "argv", ()) or ())]
        parsed = parse_command_entry(shlex.join(argv))
        if (
            parsed.ambiguous
            or parsed.base != "git"
            or parsed.sub not in _GIT_BARE_LIST_SUBS
            or parsed.arguments != (parsed.sub,)
        ):
            return False
    return True


__all__ = [
    "CommandSafetyCategory",
    "CommandSafetyVerdict",
    "CommandSafetyChecker",
    "CommandApprovalHook",
    "DEFAULT_SAFE_COMMANDS",
    "DEFAULT_DANGEROUS_COMMANDS",
    "DEFAULT_SAFE_SUB_COMMANDS",
    "DEFAULT_DANGEROUS_SUB_COMMANDS",
]


class CommandSafetyCategory(StrEnum):
    SAFE = "safe"
    DANGEROUS = "dangerous"
    REQUIRE_HITL = "require_hitl"


@dataclass
class CommandSafetyVerdict:
    category: CommandSafetyCategory
    base_command: str
    sub_command: str | None = None
    reason: str = ""


DEFAULT_SAFE_COMMANDS: set[str] = {
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "file",
    "stat",
    "wc",
    "ls",
    "tree",
    "pwd",
    "du",
    "df",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "sort",
    "uniq",
    "cut",
    "paste",
    "tr",
    "diff",
    "comm",
    "uname",
    "hostname",
    "whoami",
    "id",
    "date",
    "uptime",
    "which",
    "whereis",
    "type",
    "ps",
    "jobs",
    # pytest 在 bash-classify 为 LOCAL_EFFECTS/MEDIUM，受段级门禁约束，不靠名单抬升。
    "ruff",
    "mypy",
    "eslint",
    "tsc",
    "echo",
    "true",
    "false",
    "test",
    "printenv",
    "seq",
    "dirname",
    "basename",
    "realpath",
    "readlink",
}

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

DEFAULT_SAFE_SUB_COMMANDS: dict[str, set[str]] = {
    "git": {
        "status",
        "log",
        "diff",
        "show",
        "blame",
        "rev-parse",
        "describe",
        "name-rev",
        "shortlog",
        "whatchanged",
        "remote",
        "ls-remote",
        "ls-files",
        "tag",
        "var",
    },
    "npm": {
        # run/test 在 bash-classify ≥0.14 为 DANGEROUS（任意 package 脚本），不得列入 SAFE。
        "list",
        "ls",
        "view",
        "info",
        "outdated",
        "audit",
        "pack",
    },
    "pip": {
        "list",
        "show",
        "freeze",
        "search",
    },
    "uv": {
        # run 可执行任意入口，不得无条件 SAFE；list/tree 仍受段级门禁。
        "list",
        "tree",
    },
    "docker": {
        "ps",
        "images",
        "logs",
        "inspect",
        "version",
        "info",
        "diff",
        "top",
    },
    "kubectl": {
        "get",
        "describe",
        "logs",
        "top",
        "explain",
        "api-resources",
        "api-versions",
    },
    "cargo": {
        "check",
        "test",
        "doc",
        "search",
        "metadata",
        "tree",
        "clippy",
    },
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


class CommandSafetyChecker:
    """命令安全分类器：bash-classify 前端 + 声明范围与危险叠加。"""

    def __init__(
        self,
        safe_commands: set[str] | None = None,
        dangerous_commands: set[str] | None = None,
        safe_sub_commands: dict[str, set[str]] | None = None,
        dangerous_sub_commands: dict[str, set[str]] | None = None,
        require_approval: bool = True,
        allowed_commands: tuple[str, ...] | list[str] | None = None,
        denied_commands: tuple[str, ...] | list[str] | None = None,
        deny_out_of_scope: bool = False,
        fs_checker: FSPermissionChecker | None = None,
        workspace_root: str | None = None,
    ) -> None:
        self._safe_commands = safe_commands if safe_commands is not None else DEFAULT_SAFE_COMMANDS
        self._dangerous_commands = (
            dangerous_commands if dangerous_commands is not None else DEFAULT_DANGEROUS_COMMANDS
        )
        self._safe_sub_commands = (
            safe_sub_commands if safe_sub_commands is not None else DEFAULT_SAFE_SUB_COMMANDS
        )
        self._dangerous_sub_commands = (
            dangerous_sub_commands
            if dangerous_sub_commands is not None
            else DEFAULT_DANGEROUS_SUB_COMMANDS
        )
        self._require_approval = require_approval
        # None = 未声明范围（不限制）；空元组 = 声明了范围但什么都不自动批准。
        self._allowed_commands = None if allowed_commands is None else tuple(allowed_commands)
        self._denied_commands = tuple(denied_commands or ())
        self._deny_out_of_scope = deny_out_of_scope
        self._fs_checker = fs_checker
        self._workspace_root = workspace_root

    def extend_safe_lists(
        self,
        extra_commands: set[str] | tuple[str, ...] | list[str] | None = None,
        extra_sub_commands: dict[str, set[str]] | dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        """部署方追加 safe 白名单（与内置名单并集，只增不减）。

        仅在 bash-classify 段级门禁允许的档位内生效（READONLY+LOW 或
        LOCAL_EFFECTS+LOW）；不能抬升 UNKNOWN / MEDIUM 写入 / DANGEROUS。

        Args:
            extra_commands: 追加的基础命令名（小写归一）
            extra_sub_commands: 追加的子命令白名单（base → subs）
        """
        if extra_commands:
            self._safe_commands = self._safe_commands | {c.lower() for c in extra_commands}
        if extra_sub_commands:
            merged = {base: set(subs) for base, subs in self._safe_sub_commands.items()}
            for base, subs in extra_sub_commands.items():
                merged[base.lower()] = merged.get(base.lower(), set()) | {s.lower() for s in subs}
            self._safe_sub_commands = merged

    def check_command(
        self, command: str, *, working_dir: str | None = None
    ) -> CommandSafetyVerdict:
        """检查命令安全性。

        ``working_dir`` 是相对写路径的解析起点；缺省时用注入的 ``workspace_root``。
        """
        if not command or not command.strip():
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.DANGEROUS,
                base_command="",
                reason="Empty command",
            )

        expression = classify_command_expression(command)
        segments = iter_classified_segments(expression)
        if not segments:
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.DANGEROUS,
                base_command="",
                reason="Failed to parse command structure safely",
            )

        primary_base, primary_sub = segment_base_sub(list(segments[0].argv))

        if self._denied_commands and self._any_segment_in_scope(self._denied_commands, segments):
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.DANGEROUS,
                base_command=primary_base,
                sub_command=primary_sub,
                reason=f"Command denied by declared scope: {command}",
            )

        if _is_version_or_help_probe(expression, segments):
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.SAFE,
                base_command=primary_base,
                sub_command=primary_sub,
                reason=f"Version or help probe: {command}",
            )

        if _is_bare_git_list(expression):
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.SAFE,
                base_command=primary_base,
                sub_command=primary_sub,
                reason=f"Read-only git listing: {command}",
            )

        overlay = self._dangerous_overlay(segments, command)
        if overlay is not None:
            return overlay

        if expression_is_dangerous(expression):
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.DANGEROUS,
                base_command=primary_base,
                sub_command=primary_sub,
                reason=f"Dangerous command: {command}",
            )

        if self._allowed_commands is not None and not expression_is_unknown(expression):
            if self._all_segments_in_scope(self._allowed_commands, segments):
                return CommandSafetyVerdict(
                    category=CommandSafetyCategory.SAFE,
                    base_command=primary_base,
                    sub_command=primary_sub,
                    reason=f"Command in declared scope: {command}",
                )

        if is_readonly_low(expression):
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.SAFE,
                base_command=primary_base,
                sub_command=primary_sub,
                reason=f"Safe readonly expression: {command}",
            )

        # 部署级 SAFE 名单：先过段级门禁，再匹配名单（含 extend_safe_lists）。
        if not self._blocks_legacy_safe_promote(expression) and all(
            self._segment_allows_legacy_promote(segment) and self._legacy_segment_safe(segment)
            for segment in segments
        ):
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.SAFE,
                base_command=primary_base,
                sub_command=primary_sub,
                reason=f"Safe command: {primary_base}",
            )

        # 看不懂的结构：宽 allowed 已在上方跳过；关闭审批时硬拒绝。
        if expression_is_unknown(expression):
            if self._require_approval:
                return CommandSafetyVerdict(
                    category=CommandSafetyCategory.REQUIRE_HITL,
                    base_command=primary_base,
                    sub_command=primary_sub,
                    reason=f"Command requires approval: {command}",
                )
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.DANGEROUS,
                base_command=primary_base,
                sub_command=primary_sub,
                reason=f"Unknown command structure cannot auto-execute: {command}",
            )

        projected = self._project_workspace_write(
            expression,
            working_dir=working_dir,
            base_command=primary_base,
            sub_command=primary_sub,
        )
        if projected is not None:
            return projected

        if self._require_approval:
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.REQUIRE_HITL,
                base_command=primary_base,
                sub_command=primary_sub,
                reason=f"Command requires approval: {command}",
            )

        if self._deny_out_of_scope and self._allowed_commands is not None:
            return CommandSafetyVerdict(
                category=CommandSafetyCategory.DANGEROUS,
                base_command=primary_base,
                sub_command=primary_sub,
                reason=(f"Command outside declared scope and approval is disabled: {command}"),
            )

        return CommandSafetyVerdict(
            category=CommandSafetyCategory.SAFE,
            base_command=primary_base,
            sub_command=primary_sub,
            reason=f"Approval not required: {primary_base}",
        )

    def _project_workspace_write(
        self,
        expression: Any,
        *,
        working_dir: str | None,
        base_command: str,
        sub_command: str | None,
    ) -> CommandSafetyVerdict | None:
        projected = try_workspace_write_approval(
            expression,
            fs_checker=self._fs_checker,
            workspace_root=self._workspace_root,
            working_dir=working_dir,
        )
        if projected is None:
            return None
        if projected.kind == "safe":
            category = CommandSafetyCategory.SAFE
        elif projected.kind == "dangerous":
            category = CommandSafetyCategory.DANGEROUS
        else:
            category = CommandSafetyCategory.REQUIRE_HITL
        return CommandSafetyVerdict(
            category=category,
            base_command=base_command,
            sub_command=sub_command,
            reason=projected.reason,
        )

    def _any_segment_in_scope(self, entries: tuple[str, ...], segments: list[Any]) -> bool:
        for segment in self._scope_segments(segments):
            base, sub = segment_base_sub(list(segment.argv))
            if base and any(command_entry_covers(entry, base, sub) for entry in entries):
                return True
        return False

    def _all_segments_in_scope(self, entries: tuple[str, ...], segments: list[Any]) -> bool:
        checked = self._scope_segments(segments)
        if not checked:
            return False
        for segment in checked:
            base, sub = segment_base_sub(list(segment.argv))
            if not base or not any(command_entry_covers(entry, base, sub) for entry in entries):
                return False
        return True

    @staticmethod
    def _scope_segments(segments: list[Any]) -> list[Any]:
        """范围检查只看顶层调用，跳过委托展开的内层（避免 rm 被当成独立入口）。"""
        checked: list[Any] = []
        seen: set[tuple[str, ...]] = set()
        for segment in segments:
            if getattr(segment, "delegation_mode", None):
                continue
            key = tuple(segment.argv)
            if key in seen:
                continue
            seen.add(key)
            checked.append(segment)
        return checked or list(segments)

    def _dangerous_overlay(self, segments: list[Any], command: str) -> CommandSafetyVerdict | None:
        for segment in segments:
            argv = list(segment.argv)
            base, sub = segment_base_sub(argv)
            base_lower = base.lower()
            sub_lower = sub.lower() if sub else None
            arguments = tuple(argv[1:]) if len(argv) > 1 else ()

            if base_lower in self._dangerous_commands:
                return CommandSafetyVerdict(
                    category=CommandSafetyCategory.DANGEROUS,
                    base_command=base_lower,
                    sub_command=sub_lower,
                    reason=f"Dangerous command: {base_lower}",
                )

            destructive = (
                (base_lower == "git" and sub_lower == "reset" and "--hard" in arguments)
                or (
                    base_lower == "git"
                    and sub_lower == "push"
                    and any(
                        arg.startswith("--force")
                        or (arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:])
                        for arg in arguments
                    )
                )
                or (base_lower == "docker" and sub_lower == "rm")
                or (base_lower == "docker" and sub_lower == "system" and "prune" in arguments)
            )
            if destructive:
                return CommandSafetyVerdict(
                    category=CommandSafetyCategory.DANGEROUS,
                    base_command=base_lower,
                    sub_command=sub_lower,
                    reason=f"Dangerous command: {command}",
                )

            dangerous_subs = self._dangerous_sub_commands.get(base_lower, set())
            if sub_lower and sub_lower in dangerous_subs:
                return CommandSafetyVerdict(
                    category=CommandSafetyCategory.DANGEROUS,
                    base_command=base_lower,
                    sub_command=sub_lower,
                    reason=f"Dangerous sub-command: {base_lower} {sub_lower}",
                )
        return None

    @staticmethod
    def _blocks_legacy_safe_promote(expression: Any) -> bool:
        if getattr(expression, "redirects", None):
            return True
        if getattr(expression, "sensitive_paths", None):
            return True
        return (
            getattr(expression, "classification", None) is Classification.READONLY
            and getattr(expression, "risk", None) is Risk.HIGH
        )

    @staticmethod
    def _segment_allows_legacy_promote(segment: Any) -> bool:
        """段级门禁：仅 READONLY+LOW 或 LOCAL_EFFECTS+LOW 可被名单抬升。"""
        classification = getattr(segment, "classification", None)
        risk = getattr(segment, "risk", None)
        if risk is not Risk.LOW:
            return False
        return classification in {Classification.READONLY, Classification.LOCAL_EFFECTS}

    def _legacy_segment_safe(self, segment: Any) -> bool:
        """部署级 SAFE 名单匹配（须已通过段级门禁）。"""
        base, sub = segment_base_sub(list(segment.argv))
        if not base:
            return False
        base_lower = base.lower()
        if base_lower in self._dangerous_commands:
            return False
        sub_lower = sub.lower() if sub else None
        if sub_lower and sub_lower in self._dangerous_sub_commands.get(base_lower, set()):
            return False

        has_sub_routing = (
            base_lower in self._safe_sub_commands or base_lower in self._dangerous_sub_commands
        )
        if has_sub_routing and sub_lower:
            return sub_lower in self._safe_sub_commands.get(base_lower, set())
        if has_sub_routing and not sub_lower:
            return False
        return base_lower in self._safe_commands

    @staticmethod
    def parse_command(command: str) -> tuple[str, str | None]:
        """解析命令字符串，提取 (基础命令, 子命令)。

        处理：
        - "rm -rf /" → ("rm", None)
        - "git status" → ("git", "status")
        - "git log --oneline" → ("git", "log")
        - "npm run build" → ("npm", "run")
        - "/usr/bin/python script.py" → ("python", None)
        - "" → ("", None)

        Args:
            command: 命令字符串

        Returns:
            (基础命令, 子命令或None) 元组
        """
        parsed = parse_command_entry(command)
        return parsed.base, parsed.sub


class CommandApprovalHook(Hook):
    """命令执行审批 Hook。

    在 PRE_EXECUTE 触发点拦截 execute_command Ability，
    通过 CommandSafetyChecker 检查命令安全性（含子命令路由）：
    - SAFE → HookResult.continue_()
    - DANGEROUS → HookResult.stop(message=...)
    - REQUIRE_HITL → HookResult.hitl(message=...)
    """

    hook_point = HookPoint.PRE_EXECUTE

    def __init__(self, checker: CommandSafetyChecker) -> None:
        self._checker = checker

    async def should_trigger(self, call: OperationCall, services: ExecutionServices) -> bool:
        return call.member.member == "execute_command"

    async def execute(
        self,
        call: OperationCall,
        services: ExecutionServices,
        result: ActionResult | None = None,
    ) -> HookResult:
        tool_args = call.arguments
        command = tool_args.get("command", "")
        working_dir = tool_args.get("working_dir")
        verdict = self._checker.check_command(command, working_dir=working_dir)

        if verdict.category == CommandSafetyCategory.SAFE:
            return HookResult.continue_()

        if verdict.category == CommandSafetyCategory.DANGEROUS:
            return HookResult.stop(message=verdict.reason)

        return HookResult.hitl(message=verdict.reason)
