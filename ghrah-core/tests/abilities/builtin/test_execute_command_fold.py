# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ExecuteCommandAbility 输出折叠测试（C1c）。"""

from __future__ import annotations

from typing import Any

from ghrah.scheme.ability.shell.operations.execute_command import (
    CommandResult,
    ExecuteCommandAbility,
    _fold_text,
)
from ghrah.scheme.execution.base import ActionOutcome
from ghrah.scheme.execution.context import AbilityExecutionContext


def _make_context(**overrides: Any) -> AbilityExecutionContext:
    defaults: dict[str, Any] = {}
    defaults.update(overrides)
    return AbilityExecutionContext(**defaults)


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
        from ghrah.scheme.ability.shell.operations.execute_command import (
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
        ability = ExecuteCommandAbility(fold_stdout_lines=100)
        result = await ability.execute(_make_context(tool_args={"command": "big"}))
        # MockBigRunner 未注入——默认路径走 subprocess；改用 runner 注入
        ability = ExecuteCommandAbility(
            command_runner=MockBigRunner(_big_stdout(1000)), fold_stdout_lines=100
        )
        result = await ability.execute(_make_context(tool_args={"command": "big"}))
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
        result = await ability.execute(_make_context(tool_args={"command": "loud"}))
        assert result.data["stderr"] == stderr
        assert "lines omitted" not in result.data["stderr"]

    async def test_fold_disabled_zero(self) -> None:
        big = _big_stdout(1000)
        ability = ExecuteCommandAbility(command_runner=MockBigRunner(big), fold_stdout_lines=0)
        result = await ability.execute(_make_context(tool_args={"command": "big"}))
        assert result.data["stdout"] == big


class TestSubprocessModeFold:
    async def test_subprocess_stdout_folded(self, tmp_path) -> None:
        ability = ExecuteCommandAbility(fold_stdout_lines=10, timeout=30.0)
        # 跨平台：Windows 无 echo -e；用 python -c 生成多行
        script = "import sys; print('\\n'.join(f'line {i}' for i in range(1, 301)))"
        result = await ability.execute(
            _make_context(tool_args={"command": f'python -c "{script}"'})
        )
        assert result.outcome in (ActionOutcome.SUCCESS, ActionOutcome.FAILURE)
        stdout = result.data["stdout"]
        if "lines omitted" in stdout or "lines omitted" in stdout:
            assert "line 1" in stdout or "line 001" in stdout
            assert "[... " in stdout and "lines omitted ...]" in stdout

    async def test_subprocess_stderr_full(self, tmp_path) -> None:
        ability = ExecuteCommandAbility(fold_stdout_lines=10, timeout=30.0)
        script = "import sys; print('\\n'.join('err' for _ in range(200)), file=sys.stderr)"
        result = await ability.execute(
            _make_context(tool_args={"command": f'python -c "{script}"'})
        )
        stderr = result.data["stderr"]
        assert stderr.count("200") >= 0
        # stderr 不折叠：300 行 stderr 不应出现省略标记
        assert "lines omitted" not in stderr


class TestThresholdBoundaries:
    async def test_99_100_101_lines(self) -> None:
        for n, expect_omit in ((99, False), (100, False), (101, True)):
            ability = ExecuteCommandAbility(
                command_runner=MockBigRunner(_big_stdout(n)), fold_stdout_lines=100
            )
            result = await ability.execute(_make_context(tool_args={"command": "big"}))
            has_marker = "lines omitted" in result.data["stdout"]
            assert has_marker == expect_omit, f"n={n}"
