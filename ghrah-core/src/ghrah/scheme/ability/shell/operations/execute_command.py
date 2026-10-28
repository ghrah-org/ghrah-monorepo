# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ExecuteCommandAbility：命令执行能力。

功能：
- 执行 shell 命令并返回输出
- 支持 SAFE/DANGEROUS/REQUIRE_HITL 三级命令安全分类
- 双模式执行：单体模式（subprocess）和 Subject 模式（SandboxExecutor）

执行模式：
1. 单体模式（command_runner=None）：asyncio.create_subprocess_shell
2. Subject 模式（command_runner=SandboxExecutor）：委托给 command_runner

安全检查：
- inline 硬拒绝：CommandSafetyChecker.category == DANGEROUS → FAILURE
- Hook 层 HITL：CommandApprovalHook 提供 PRE_EXECUTE 拦截
- Subject 端 PermissionChecker 再检查一次
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel

from ghrah.scheme.execution.base import Ability
from ghrah.scheme.permissions.command_safety import (
    CommandSafetyCategory,
    CommandSafetyChecker,
)
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme.execution.context import AbilityExecutionContext
    from ghrah.scheme.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/execute_command.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {
    "shell_access": True,
    "require_hitl": True,
}
HOOKS = {
    "pre_execute": [
        {
            "type": "builtin",
            "handler": "command_approval",
        },
    ],
}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = (
    "Execute a shell command and return its output. Dangerous commands are blocked. Other "
    "commands require human approval."
)
TAGS = [
    "shell",
    "execution",
]
# --- catalogue 声明结束

logger = logging.getLogger(__name__)

__all__ = [
    "ExecuteCommandAbility",
    "ExecuteCommandInput",
    "CommandResult",
    "CommandRunner",
]

_MAX_OUTPUT_BYTES = 1_000_000

# stdout 折叠参数：超过 fold_stdout_lines 行时保留 head + tail，中间以标记行省略
_FOLD_HEAD_LINES = 40
_FOLD_TAIL_LINES = 40


class ExecuteCommandInput(BaseModel):
    command: str
    working_dir: str | None = None


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    command: str = ""
    working_dir: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@runtime_checkable
class CommandRunner(Protocol):
    async def execute_command(
        self,
        command: list[str],
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        stdin_data: str | None = None,
    ) -> CommandResult: ...


class ExecuteCommandAbility(Ability):
    """命令执行能力 — 支持单体和 Subject 双模式。

    用法::

        # 单体模式（本地执行）
        ability = ExecuteCommandAbility()

        # 带命令安全检查
        checker = CommandSafetyChecker()
        hook = CommandApprovalHook(checker)
        ability = ExecuteCommandAbility(command_checker=checker, hooks=[hook])

        # Subject 模式（委托 SandboxExecutor）
        ability = ExecuteCommandAbility(command_runner=sandbox_executor)
    """

    def __init__(
        self,
        hooks: list[Hook] | None = None,
        command_checker: CommandSafetyChecker | None = None,
        command_runner: CommandRunner | None = None,
        timeout: float = 300.0,
        fold_stdout_lines: int = 100,
    ) -> None:
        self._hooks = hooks or []
        self._checker = command_checker or CommandSafetyChecker()
        self._command_runner = command_runner
        self._timeout = timeout
        # 0 = 关闭折叠（保留旧行为：仅字节截断）；stderr 永不折叠
        self._fold_stdout_lines = fold_stdout_lines

    @property
    def name(self) -> str:
        return "execute_command"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": (
                    "Execute a shell command and return its output. "
                    "Use for running tests, linters, build commands, and other development tasks. "
                    "Read-only commands are auto-approved. Dangerous commands are blocked. "
                    "Other commands require human approval."
                ),
                "parameters": ExecuteCommandInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "execute_command(command: str, working_dir: str | None = None) -> dict: "
            "Execute a shell command and return exit_code, stdout, stderr"
        )

    def get_hooks(self) -> list[Hook]:
        return list(self._hooks)

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        tool_args = context.tool_args or context.accumulated_data.get("tool_args", {})
        command = tool_args.get("command", "")
        working_dir = tool_args.get("working_dir")

        if not command:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "command is required"},
            )

        verdict = self._checker.check_command(command)
        if verdict.category == CommandSafetyCategory.DANGEROUS:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": verdict.reason, "command": command},
            )

        if self._command_runner is not None:
            return await self._execute_via_runner(command, working_dir)
        return await self._execute_subprocess(command, working_dir)

    async def _execute_via_runner(self, command: str, working_dir: str | None) -> ActionResult:
        try:
            args = shlex.split(command)
        except ValueError as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to parse command: {e}", "command": command},
            )

        try:
            runner = self._command_runner
            assert runner is not None
            result = await runner.execute_command(
                command=args,
                cwd=working_dir,
                timeout=self._timeout,
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": str(e), "command": command},
            )

        return ActionResult(
            outcome=ActionOutcome.SUCCESS if result.success else ActionOutcome.FAILURE,
            data={
                "exit_code": result.exit_code,
                "stdout": self._maybe_fold(result.stdout),
                "stderr": result.stderr,
                "timed_out": result.timed_out,
                "command": command,
                "working_dir": working_dir or "",
            },
        )

    async def _execute_subprocess(self, command: str, working_dir: str | None) -> ActionResult:
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout,
            )
            timed_out = False
        except TimeoutError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            timed_out = True
            stdout_bytes = b""
            stderr_bytes = f"Command timed out after {self._timeout}s".encode()
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": str(e), "command": command},
            )

        stdout = self._truncate_output(self._maybe_fold_bytes(stdout_bytes))
        stderr = self._truncate_output(stderr_bytes)

        exit_code = process.returncode if process.returncode is not None else -1

        data: dict[str, Any] = {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "command": command,
            "working_dir": working_dir or "",
        }

        success = exit_code == 0 and not timed_out
        return ActionResult(
            outcome=ActionOutcome.SUCCESS if success else ActionOutcome.FAILURE,
            data=data,
        )

    def _maybe_fold(self, stdout: str) -> str:
        """对 stdout 文本应用行数折叠（stderr 永不折叠）。"""
        if self._fold_stdout_lines <= 0:
            return stdout
        return _fold_text(stdout, self._fold_stdout_lines)

    def _maybe_fold_bytes(self, data: bytes) -> bytes:
        """字节版折叠：subprocess 模式先解码折叠再回到字节（截断仍按字节兜底）。"""
        if self._fold_stdout_lines <= 0:
            return data
        text = data.decode("utf-8", errors="replace")
        folded = _fold_text(text, self._fold_stdout_lines)
        return folded.encode("utf-8", errors="replace")

    @staticmethod
    def _truncate_output(data: bytes) -> str:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        if len(data) > _MAX_OUTPUT_BYTES:
            return text[:_MAX_OUTPUT_BYTES] + f"\n[truncated at {_MAX_OUTPUT_BYTES} bytes]"
        return text


def _fold_text(text: str, threshold: int) -> str:
    """超 threshold 行时保留 head+tail，中间行以标记行省略。

    stderr 永不折叠（错误现场完整是排障刚需），本函数仅用于 stdout。
    """
    lines = text.splitlines(keepends=True)
    if len(lines) <= threshold:
        return text
    omitted = len(lines) - _FOLD_HEAD_LINES - _FOLD_TAIL_LINES
    if omitted <= 0:
        return text
    head = lines[:_FOLD_HEAD_LINES]
    tail = lines[-_FOLD_TAIL_LINES:]
    eol = "\r\n" if head and head[-1].endswith("\r\n") else "\n"
    marker = f"[... {omitted} lines omitted ...]{eol}"
    return "".join(head) + marker + "".join(tail)
