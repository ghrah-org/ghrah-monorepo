# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""命令安全检查模块：分类和审批逻辑。

提供：
- CommandSafetyCategory: 命令安全分类枚举（SAFE / DANGEROUS / REQUIRE_HITL）
- CommandSafetyVerdict: 分类裁定结果
- CommandSafetyChecker: 命令安全分类器（bash-classify 前端 + 范围外壳）
- CommandApprovalHook: PRE_EXECUTE Hook，拦截 execute_command Ability

分类模型（判定顺序，2026-11-07 阶段 1）：
1. 空命令 → 硬拒绝
2. ``denied_commands`` 命中任一段 → 硬拒绝
3. bash-classify（仓内 YAML 覆盖）判 DANGEROUS，或 ghrah 危险叠加 → 硬拒绝
4. ``allowed_commands`` 覆盖全部顶层段 → 自动批准（``UNKNOWN`` 除外）
5. 整式 READONLY+LOW（无 parse_warnings）→ 自动批准
6. 各段均落在部署级 SAFE 名单（含 ``extend_safe_lists``；无重定向）→ 自动批准
7. ``UNKNOWN``：需审批时 HITL，关闭审批时硬拒绝
8. 其余：``require_approval`` 为真时审批，否则按 ``deny_out_of_scope`` 决定拒绝或放行
"""

from __future__ import annotations

import logging
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

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.invocation import OperationCall
    from ghrah.types.results import ActionResult

logger = logging.getLogger(__name__)

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
    "find",
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
    "tee",
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
    "pytest",
    "ruff",
    "mypy",
    "eslint",
    "tsc",
    "echo",
    "true",
    "false",
    "test",
    "env",
    "printenv",
    "seq",
    "dirname",
    "basename",
    "realpath",
    "readlink",
    "xargs",
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
        "branch",
        "tag",
        "stash",
        "config",
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
        "run",
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
        "build",
        "run",
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

    def extend_safe_lists(
        self,
        extra_commands: set[str] | tuple[str, ...] | list[str] | None = None,
        extra_sub_commands: dict[str, set[str]] | dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        """部署方追加 safe 白名单（与内置名单并集，只增不减）。

        配置驱动通道（Subject env → CoreUnitConfig → 装配点）：追加基础
        命令与子命令白名单，供 type-check / lint / test 类语义只读命令
        免审批放行。不收窄内置名单——DANGEROUS 名单不受此影响。

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

    def check_command(self, command: str) -> CommandSafetyVerdict:
        """检查命令安全性。"""
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

        # 部署级 SAFE 名单可抬升 LOCAL_EFFECTS / 库外 UNKNOWN；
        # 重定向、敏感路径、或「只读但 HIGH」（如 cat ~/.ssh）时不抬升。
        if not self._blocks_legacy_safe_promote(expression) and all(
            self._legacy_segment_safe(segment) for segment in segments
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

    def _legacy_segment_safe(self, segment: Any) -> bool:
        """部署级 SAFE 名单兼容：仅在 bash-classify 未判危险时提升为 SAFE。"""
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
        verdict = self._checker.check_command(command)

        if verdict.category == CommandSafetyCategory.SAFE:
            return HookResult.continue_()

        if verdict.category == CommandSafetyCategory.DANGEROUS:
            return HookResult.stop(message=verdict.reason)

        return HookResult.hitl(message=verdict.reason)
