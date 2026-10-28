# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SearchFilesAbility 测试：内容搜索能力（双模式 + 权限 + 白名单）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ghrah.scheme.ability.fs.operations.search_files import SearchFilesAbility
from ghrah.scheme.ability.shell.operations.execute_command import CommandResult
from ghrah.scheme.execution.base import ActionOutcome
from ghrah.scheme.execution.context import AbilityExecutionContext
from ghrah.scheme.permissions.command_safety import CommandSafetyCategory, CommandSafetyChecker
from ghrah.scheme.permissions.fs_permissions import FSPermissionChecker


def _make_context(**overrides: Any) -> AbilityExecutionContext:
    defaults: dict[str, Any] = {}
    defaults.update(overrides)
    return AbilityExecutionContext(**defaults)


def _make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "node_modules").mkdir()
    (root / "pkg" / "app.py").write_text(
        "def alpha():\n    return 1\n\ndef beta():\n    return ALPHA_CASE 2\n",
        encoding="utf-8",
    )
    (root / "pkg" / "util.py").write_text("value = alpha\n", encoding="utf-8")
    (root / "README.md").write_text("alpha in readme\n", encoding="utf-8")
    (root / ".git" / "hidden.py").write_text("alpha in git dir\n", encoding="utf-8")
    (root / "node_modules" / "dep.py").write_text("alpha in node_modules\n", encoding="utf-8")
    (root / "binary.py").write_bytes(b"alpha\x00binary")
    return root


@dataclass
class MockRunnerResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class MockCommandRunner:
    stdout: str = ""
    exit_code: int = 0
    calls: list[list[str]] = field(default_factory=list)

    async def execute_command(
        self,
        command: list[str],
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        stdin_data: str | None = None,
    ) -> CommandResult:
        self.calls.append(command)
        return CommandResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr="",
            timed_out=False,
            command=" ".join(command),
            working_dir=cwd or "",
        )


# ── schema / description ──


class TestSchema:
    def test_bind_tool_returns_valid_schema(self) -> None:
        ability = SearchFilesAbility()
        schema = ability.bind_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "search_files"
        assert "parameters" in schema["function"]

    def test_input_schema_required_fields(self) -> None:
        from ghrah.scheme.ability.fs.operations.search_files import SearchFilesInput

        js = SearchFilesInput.model_json_schema()
        assert "pattern" in js["properties"]
        assert "path" in js["properties"]
        assert "pattern" in js.get("required", [])
        assert "path" in js.get("required", [])

    def test_name_and_prompt_description(self) -> None:
        ability = SearchFilesAbility()
        assert ability.name == "search_files"
        assert "search_files" in ability.to_prompt_description()


# ── 参数校验 ──


class TestValidation:
    async def test_missing_pattern_rejected(self) -> None:
        ability = SearchFilesAbility()
        result = await ability.execute(_make_context(tool_args={"path": "/tmp"}))
        assert result.outcome == ActionOutcome.FAILURE
        assert "pattern is required" in result.data["error"]

    async def test_missing_path_rejected(self) -> None:
        ability = SearchFilesAbility()
        result = await ability.execute(_make_context(tool_args={"pattern": "x"}))
        assert result.outcome == ActionOutcome.FAILURE
        assert "path is required" in result.data["error"]

    async def test_nonexistent_path_rejected(self, tmp_path: Path) -> None:
        ability = SearchFilesAbility()
        result = await ability.execute(
            _make_context(tool_args={"pattern": "x", "path": str(tmp_path / "nope")})
        )
        assert result.outcome == ActionOutcome.FAILURE
        assert "does not exist" in result.data["error"]

    async def test_permission_denied(self, tmp_path: Path) -> None:
        root = _make_tree(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        ability = SearchFilesAbility(
            permission_checker=FSPermissionChecker(
                allowed_paths=[str(other)], require_approval=False
            )
        )
        result = await ability.execute(
            _make_context(tool_args={"pattern": "alpha", "path": str(root)})
        )
        assert result.outcome == ActionOutcome.FAILURE
        assert "Permission denied" in result.data["error"]


# ── Python 回退模式（不依赖 rg 是否安装，monkeypatch which）──


class TestPythonFallback:
    async def test_basic_search(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "ghrah.scheme.ability.fs.operations.search_files.shutil.which", lambda _: None
        )
        root = _make_tree(tmp_path)
        ability = SearchFilesAbility()
        result = await ability.execute(
            _make_context(tool_args={"pattern": "alpha", "path": str(root)})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        files = {m["file"] for m in result.data["matches"]}
        assert any(m.endswith("app.py") for m in files)
        assert not any(".git" in m for m in files)
        assert not any("node_modules" in m for m in files)
        assert not any("binary.py" in m for m in files)
        assert result.data["truncated"] is False

    async def test_include_glob(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "ghrah.scheme.ability.fs.operations.search_files.shutil.which", lambda _: None
        )
        root = _make_tree(tmp_path)
        ability = SearchFilesAbility()
        result = await ability.execute(
            _make_context(tool_args={"pattern": "alpha", "path": str(root), "include": "*.py"})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        files = {m["file"] for m in result.data["matches"]}
        assert all(f.endswith(".py") for f in files)
        assert not any("README" in f for f in files)

    async def test_ignore_case(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "ghrah.scheme.ability.fs.operations.search_files.shutil.which", lambda _: None
        )
        root = _make_tree(tmp_path)
        ability = SearchFilesAbility()
        result = await ability.execute(
            _make_context(
                tool_args={"pattern": "alpha_case", "path": str(root), "ignore_case": True}
            )
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["total"] >= 1

    async def test_max_results_truncation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ghrah.scheme.ability.fs.operations.search_files.shutil.which", lambda _: None
        )
        root = _make_tree(tmp_path)
        big = root / "big.txt"
        big.write_text("\n".join(f"alpha line {i}" for i in range(10)), encoding="utf-8")
        ability = SearchFilesAbility()
        result = await ability.execute(
            _make_context(tool_args={"pattern": "alpha", "path": str(root), "max_results": 3})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert len(result.data["matches"]) == 3
        assert result.data["total"] >= 3
        assert result.data["truncated"] is True

    async def test_invalid_regex_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ghrah.scheme.ability.fs.operations.search_files.shutil.which", lambda _: None
        )
        root = _make_tree(tmp_path)
        ability = SearchFilesAbility()
        result = await ability.execute(
            _make_context(tool_args={"pattern": "(unclosed", "path": str(root)})
        )
        assert result.outcome == ActionOutcome.FAILURE
        assert "Invalid regex" in result.data["error"]

    async def test_line_numbers_are_int(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ghrah.scheme.ability.fs.operations.search_files.shutil.which", lambda _: None
        )
        root = _make_tree(tmp_path)
        ability = SearchFilesAbility()
        result = await ability.execute(
            _make_context(tool_args={"pattern": "def alpha", "path": str(root), "include": "*.py"})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        match = result.data["matches"][0]
        assert isinstance(match["line"], int)
        assert match["line"] == 1


# ── Subject 模式（MockCommandRunner）──


class TestRunnerMode:
    async def test_runner_receives_fixed_flag_set(self, tmp_path: Path) -> None:
        root = _make_tree(tmp_path)
        runner = MockCommandRunner(stdout="")
        ability = SearchFilesAbility(command_runner=runner)
        await ability.execute(
            _make_context(
                tool_args={
                    "pattern": "alpha",
                    "path": str(root),
                    "include": "*.py",
                    "context_lines": 2,
                    "max_results": 10,
                    "ignore_case": True,
                }
            )
        )
        assert len(runner.calls) == 1
        cmd = runner.calls[0]
        assert cmd[0] == "rg"
        assert "--line-number" in cmd
        assert "--color" in cmd and "never" in cmd
        assert "--no-heading" in cmd
        assert "-i" in cmd
        assert cmd[cmd.index("--context") + 1] == "2"
        glob_values = [cmd[i + 1] for i, v in enumerate(cmd) if v == "--glob"]
        assert "*.py" in glob_values
        assert "!node_modules/" in glob_values
        assert "!.git/" in glob_values
        assert "alpha" in cmd
        assert str(root) in cmd
        # 安全边界：固定 flag 集，不含可执行任意预处理器的 flag
        assert "--pre" not in cmd
        assert "--pre-glob" not in cmd

    async def test_runner_output_parsed(self, tmp_path: Path) -> None:
        root = _make_tree(tmp_path)
        rg_out = f"{root}\\pkg\\app.py:1:def alpha():\n{root}\\pkg\\app.py:3:def beta():\n--\n"
        runner = MockCommandRunner(stdout=rg_out)
        ability = SearchFilesAbility(command_runner=runner)
        result = await ability.execute(
            _make_context(tool_args={"pattern": "alpha", "path": str(root)})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["total"] == 2
        assert result.data["matches"][0]["line"] == 1
        assert result.data["matches"][0]["text"] == "def alpha():"
        assert result.data["truncated"] is False

    async def test_runner_max_count_truncation_flag(self, tmp_path: Path) -> None:
        root = _make_tree(tmp_path)
        lines = "\n".join(f"{root}\\f.py:{i}:match {i}" for i in range(1, 4))
        runner = MockCommandRunner(stdout=lines)
        ability = SearchFilesAbility(command_runner=runner)
        result = await ability.execute(
            _make_context(tool_args={"pattern": "match", "path": str(root), "max_results": 2})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert len(result.data["matches"]) == 2
        assert result.data["total"] == 3
        assert result.data["truncated"] is True

    async def test_runner_crash_is_failure(self, tmp_path: Path) -> None:
        root = _make_tree(tmp_path)

        class BoomRunner:
            async def execute_command(self, **kwargs: Any) -> CommandResult:
                raise RuntimeError("runner crashed")

        ability = SearchFilesAbility(command_runner=BoomRunner())
        result = await ability.execute(
            _make_context(tool_args={"pattern": "alpha", "path": str(root)})
        )
        assert result.outcome == ActionOutcome.FAILURE
        assert "Search failed" in result.data["error"]

    async def test_runner_no_match_exit_code_1(self, tmp_path: Path) -> None:
        root = _make_tree(tmp_path)
        runner = MockCommandRunner(stdout="", exit_code=1)
        ability = SearchFilesAbility(command_runner=runner)
        result = await ability.execute(
            _make_context(tool_args={"pattern": "nomatch", "path": str(root)})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["total"] == 0
        assert result.data["matches"] == []


# ── 本地 rg 真实执行（rg 可用时）──

requires_rg = pytest.mark.skipif(
    __import__("shutil").which("rg") is None, reason="rg not available"
)


class TestLocalRg:
    @requires_rg
    async def test_local_rg_matches_python_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_tree(tmp_path)
        rg_ability = SearchFilesAbility()
        rg_result = await rg_ability.execute(
            _make_context(tool_args={"pattern": "alpha", "path": str(root), "include": "*.py"})
        )

        monkeypatch.setattr(
            "ghrah.scheme.ability.fs.operations.search_files.shutil.which", lambda _: None
        )
        py_ability = SearchFilesAbility()
        py_result = await py_ability.execute(
            _make_context(tool_args={"pattern": "alpha", "path": str(root), "include": "*.py"})
        )

        assert rg_result.outcome == ActionOutcome.SUCCESS
        assert py_result.outcome == ActionOutcome.SUCCESS
        rg_names = sorted(Path(m["file"]).name for m in rg_result.data["matches"])
        py_names = sorted(Path(m["file"]).name for m in py_result.data["matches"])
        assert rg_names == py_names

    @requires_rg
    async def test_local_rg_respects_skip_dirs(self, tmp_path: Path) -> None:
        root = _make_tree(tmp_path)
        ability = SearchFilesAbility()
        result = await ability.execute(
            _make_context(tool_args={"pattern": "alpha", "path": str(root)})
        )
        files = " ".join(str(m["file"]) for m in result.data["matches"])
        assert ".git" not in files
        assert "node_modules" not in files


# ── rg 白名单分类 ──


class TestRgWhitelist:
    def test_rg_is_safe(self) -> None:
        checker = CommandSafetyChecker()
        verdict = checker.check_command("rg pattern ./src")
        assert verdict.category == CommandSafetyCategory.SAFE

    def test_rg_with_flags_safe(self) -> None:
        checker = CommandSafetyChecker()
        verdict = checker.check_command("rg --line-number --glob '*.py' def /repo")
        assert verdict.category == CommandSafetyCategory.SAFE


# ── parser 单元 ──


class TestParseRgOutput:
    def test_parse_windows_paths_with_drive_colon(self) -> None:
        from ghrah.scheme.ability.fs.operations.search_files import _parse_rg_output

        out = "C:\\repo\\pkg\\a.py:12:found here\nC:\\repo\\b.py:3:also\n"
        parsed = _parse_rg_output(out, max_results=10)
        assert parsed["count"] == 2
        assert parsed["items"][0]["file"] == "C:\\repo\\pkg\\a.py"
        assert parsed["items"][0]["line"] == 12
        assert parsed["items"][0]["text"] == "found here"
        assert parsed["items"][1]["file"] == "C:\\repo\\b.py"

    def test_parse_context_separator_lines(self) -> None:
        from ghrah.scheme.ability.fs.operations.search_files import _parse_rg_output

        out = "a.py:1:hit\n--\nb.py:2:hit2\n--\n"
        parsed = _parse_rg_output(out, max_results=10)
        assert parsed["count"] == 2
        assert all(m["file"] for m in parsed["items"])

    def test_truncation_flag(self) -> None:
        from ghrah.scheme.ability.fs.operations.search_files import _parse_rg_output

        out = "\n".join(f"a.py:{i}:m" for i in range(1, 6))
        parsed = _parse_rg_output(out, max_results=3)
        assert len(parsed["items"]) == 3
        assert parsed["count"] == 5
        assert parsed["truncated"] is True
