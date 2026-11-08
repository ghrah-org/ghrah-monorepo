# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ExecuteCommandAbility 输出折叠测试（C1c）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec

from _helpers import make_call, make_services

from ghrah.ability.builtin.shell.operations.execute_command import (
    CommandResult,
    ExecuteCommandAbility,
    _fold_text,
)
from ghrah.ability.execution.base import ActionOutcome
from ghrah.ability.execution.execution_services import ExecutionServices
from ghrah.ability.execution.invocation import OperationCall
from ghrah.ability.permissions.command_safety import (
    CommandSafetyCategory,
    CommandSafetyChecker,
    CommandSafetyVerdict,
)


def _make_context(**overrides: Any) -> tuple[OperationCall, ExecutionServices]:
    """创建测试用 (OperationCall, ExecutionServices)。"""
    tool_args = overrides.pop("tool_args", None)
    member = overrides.pop("member", "")
    return make_call(member=member, arguments=tool_args), make_services(**overrides)


def _line_text(i: int) -> str:
    return f"line {i:04d}"


def _big_stdout(n: int) -> str:
    return "\n".join(_line_text(i) for i in range(1, n + 1)) + "\n"


class MockBigRunner:
    """固定返回大 stdout 的 runner。"""

    def __init__(self, stdout: str, stderr: str = "", exit_code: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._exit_code = exit_code

    async def execute_command(
        self,
        command: list[str],
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        stdin_data: str | None = None,
    ) -> CommandResult:
        return CommandResult(
            exit_code=self._exit_code,
            stdout=self._stdout,
            stderr=self._stderr,
            timed_out=False,
            command=" ".join(command),
            working_dir=cwd or "",
        )


class TestFoldTextUnit:
    def test_under_threshold_unchanged(self) -> None:
        text = _big_stdout(99)
        assert _fold_text(text, 100) == text

    def test_at_threshold_unchanged(self) -> None:
        text = _big_stdout(100)
        assert _fold_text(text, 100) == text

    def test_over_threshold_folded_head_tail(self) -> None:
        text = _big_stdout(101)
        folded = _fold_text(text, 100)
        assert "line 0001" in folded
        assert "line 0040" in folded
        assert "line 0061" not in folded
        assert "[... 21 lines omitted ...]" in folded
        assert "line 0101" in folded

    def test_marker_counts_omitted(self) -> None:
        folded = _fold_text(_big_stdout(1000), 100)
        assert "[... 920 lines omitted ...]" in folded

    def test_head_and_tail_sizes(self) -> None:
        from ghrah.ability.builtin.shell.operations.execute_command import (
            _FOLD_HEAD_LINES,
            _FOLD_TAIL_LINES,
        )

        folded = _fold_text(_big_stdout(500), 100)
        # head/tail 边界行在，omitted 区间不在（标记行占一行）
        assert f"line {_FOLD_HEAD_LINES:04d}" in folded
        assert f"line {_FOLD_HEAD_LINES + 1:04d}" not in folded
        assert f"line {500 - _FOLD_TAIL_LINES + 1:04d}" in folded
        assert f"line {500 - _FOLD_TAIL_LINES:04d}" not in folded
        marker_lines = [ln for ln in folded.splitlines() if "lines omitted" in ln]
        assert len(marker_lines) == 1
        assert len(folded.splitlines()) == _FOLD_HEAD_LINES + _FOLD_TAIL_LINES + 1


class TestRunnerModeFold:
    async def test_stdout_folded(self) -> None:
        ability = ExecuteCommandAbility(
            command_runner=MockBigRunner(_big_stdout(1000)), fold_stdout_lines=100
        )
        call, services = _make_context(tool_args={"command": "big"})
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.SUCCESS
        assert "[... " in result.data["stdout"]
        assert "lines omitted" in result.data["stdout"]
        assert "line 0001" in result.data["stdout"]

    async def test_stderr_never_folded(self) -> None:
        stderr = _big_stdout(500)
        ability = ExecuteCommandAbility(
            command_runner=MockBigRunner(_big_stdout(1000), stderr=stderr),
            fold_stdout_lines=100,
        )
        call, services = _make_context(tool_args={"command": "loud"})
        result = await ability.execute(call, services)
        assert result.data["stderr"] == stderr
        assert "lines omitted" not in result.data["stderr"]

    async def test_fold_disabled_zero(self) -> None:
        big = _big_stdout(1000)
        ability = ExecuteCommandAbility(command_runner=MockBigRunner(big), fold_stdout_lines=0)
        call, services = _make_context(tool_args={"command": "big"})
        result = await ability.execute(call, services)
        assert result.data["stdout"] == big


class TestSubprocessModeFold:
    async def test_subprocess_folds_only_stdout(self, tmp_path: Path) -> None:
        script = tmp_path / "output.py"
        script.write_text(
            "import sys\n"
            "print('\\n'.join(f'line {i:04d}' for i in range(1, 301)))\n"
            "print('\\n'.join('err' for _ in range(200)), file=sys.stderr)\n",
            encoding="utf-8",
        )
        # 只测试已通过检查的子进程输出。命令准入由独立的安全测试覆盖。
        checker = create_autospec(CommandSafetyChecker, instance=True)
        checker.check_command.return_value = CommandSafetyVerdict(
            category=CommandSafetyCategory.SAFE, base_command="python"
        )
        ability = ExecuteCommandAbility(command_checker=checker, fold_stdout_lines=10, timeout=30.0)
        call, services = _make_context(tool_args={"command": f'"{sys.executable}" "{script}"'})
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.SUCCESS, result
        stdout = result.data["stdout"]
        assert "line 0001" in stdout and "line 0300" in stdout
        assert "lines omitted" in stdout
        assert result.data["stderr"].splitlines() == ["err"] * 200


class TestThresholdBoundaries:
    async def test_99_100_101_lines(self) -> None:
        for n, expect_omit in ((99, False), (100, False), (101, True)):
            ability = ExecuteCommandAbility(
                command_runner=MockBigRunner(_big_stdout(n)), fold_stdout_lines=100
            )
            call, services = _make_context(tool_args={"command": "big"})
            result = await ability.execute(call, services)
            has_marker = "lines omitted" in result.data["stdout"]
            assert has_marker == expect_omit, f"n={n}"
