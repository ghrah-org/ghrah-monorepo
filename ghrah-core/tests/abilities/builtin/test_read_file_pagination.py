# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ReadFileAbility 分页与行号化测试（C1b）。"""

from __future__ import annotations

from typing import Any

from ghrah.scheme.ability.fs.operations.read_file import ReadFileAbility
from ghrah.scheme.execution.base import ActionOutcome
from ghrah.scheme.execution.context import AbilityExecutionContext


def _make_context(**overrides: Any) -> AbilityExecutionContext:
    defaults: dict[str, Any] = {}
    defaults.update(overrides)
    return AbilityExecutionContext(**defaults)


def _make_file(tmp_path, lines: list[str]) -> str:
    p = tmp_path / "sample.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


class TestPagination:
    async def test_offset_only(self, tmp_path) -> None:
        path = _make_file(tmp_path, ["l1", "l2", "l3", "l4", "l5"])
        ability = ReadFileAbility()
        result = await ability.execute(_make_context(tool_args={"file_path": path, "offset": 2}))
        assert result.outcome == ActionOutcome.SUCCESS
        assert "3\u2192l3" in result.data["content"]
        assert "1\u2192l1" not in result.data["content"]
        assert result.data["total_lines"] == 5
        assert result.data["offset"] == 2
        assert result.data["limit"] == 0

    async def test_offset_and_limit(self, tmp_path) -> None:
        path = _make_file(tmp_path, [f"line{i}" for i in range(1, 11)])
        ability = ReadFileAbility()
        result = await ability.execute(
            _make_context(tool_args={"file_path": path, "offset": 2, "limit": 3})
        )
        content = result.data["content"]
        assert "3\u2192line3" in content
        assert "5\u2192line5" in content
        assert "6\u2192line6" not in content
        assert "2\u2192line2" not in content

    async def test_limit_only_starts_from_head(self, tmp_path) -> None:
        path = _make_file(tmp_path, ["a", "b", "c"])
        ability = ReadFileAbility()
        result = await ability.execute(_make_context(tool_args={"file_path": path, "limit": 2}))
        content = result.data["content"]
        assert "1\u2192a" in content
        assert "2\u2192b" in content
        assert "3\u2192c" not in content

    async def test_offset_beyond_eof_empty(self, tmp_path) -> None:
        path = _make_file(tmp_path, ["a", "b"])
        ability = ReadFileAbility()
        result = await ability.execute(_make_context(tool_args={"file_path": path, "offset": 100}))
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["content"] == ""
        assert result.data["total_lines"] == 2

    async def test_empty_file(self, tmp_path) -> None:
        p = tmp_path / "empty.txt"
        p.write_text("", encoding="utf-8")
        ability = ReadFileAbility()
        result = await ability.execute(
            _make_context(tool_args={"file_path": str(p), "offset": 0, "limit": 10})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["content"] == ""
        assert result.data["total_lines"] == 0

    async def test_limit_exceeds_tail(self, tmp_path) -> None:
        path = _make_file(tmp_path, ["x", "y"])
        ability = ReadFileAbility()
        result = await ability.execute(
            _make_context(tool_args={"file_path": path, "offset": 0, "limit": 99})
        )
        assert "1\u2192x" in result.data["content"]
        assert "2\u2192y" in result.data["content"]


class TestCompatibility:
    async def test_zero_args_content_byte_identical(self, tmp_path) -> None:
        """零参数路径保持旧语义：全文、无行号前缀。"""
        raw = "Hello, World!"
        path = _make_file(tmp_path, [raw])
        ability = ReadFileAbility()
        result = await ability.execute(_make_context(tool_args={"file_path": path}))
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["content"] == raw + "\n"
        assert "\u2192" not in result.data["content"]
        assert "total_lines" in result.data
        assert "offset" not in result.data
        assert "limit" not in result.data

    async def test_total_lines_present_in_legacy_mode(self, tmp_path) -> None:
        path = _make_file(tmp_path, ["a", "b", "c"])
        ability = ReadFileAbility()
        result = await ability.execute(_make_context(tool_args={"file_path": path}))
        assert result.data["total_lines"] == 3


class TestLineNumberFormat:
    async def test_numbered_lines_contain_arrow(self, tmp_path) -> None:
        path = _make_file(tmp_path, ["first", "second"])
        ability = ReadFileAbility()
        result = await ability.execute(
            _make_context(tool_args={"file_path": path, "offset": 0, "limit": 2})
        )
        lines = result.data["content"].splitlines()
        assert lines[0].strip("\n").rstrip("first").strip() == "1\u2192" or "1\u2192" in lines[0]
        assert "2\u2192second" in result.data["content"]

    async def test_line_numbers_are_1_based_absolute(self, tmp_path) -> None:
        """行号是文件内绝对 1-based 行号（offset 后不归零）。"""
        path = _make_file(tmp_path, [f"line{i}" for i in range(1, 8)])
        ability = ReadFileAbility()
        result = await ability.execute(
            _make_context(tool_args={"file_path": path, "offset": 5, "limit": 2})
        )
        content = result.data["content"]
        assert "6\u2192line6" in content
        assert "7\u2192line7" in content
        assert "1\u2192" not in content


class TestSchema:
    def test_schema_has_offset_limit(self) -> None:
        from ghrah.scheme.ability.fs.operations.read_file import ReadFileInput

        js = ReadFileInput.model_json_schema()
        assert "offset" in js["properties"]
        assert "limit" in js["properties"]

    def test_bind_tool_describes_line_numbers(self) -> None:
        ability = ReadFileAbility()
        desc = ability.bind_tool()["function"]["description"]
        assert "line number" in desc

    async def test_encoding_still_honored_paginated(self, tmp_path) -> None:
        p = tmp_path / "cn.txt"
        p.write_text("你好\n世界\n", encoding="utf-8")
        ability = ReadFileAbility()
        result = await ability.execute(
            _make_context(tool_args={"file_path": str(p), "encoding": "utf-8", "limit": 1})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert "1\u2192你好" in result.data["content"]
        assert "世界" not in result.data["content"]


class TestErrors:
    async def test_file_not_found_paginated(self) -> None:
        ability = ReadFileAbility()
        result = await ability.execute(
            _make_context(tool_args={"file_path": "/nonexistent/f.txt", "offset": 0, "limit": 5})
        )
        assert result.outcome == ActionOutcome.FAILURE
        assert "not found" in result.data["error"].lower()

    async def test_no_tool_args_failure(self) -> None:
        ability = ReadFileAbility()
        result = await ability.execute(_make_context())
        assert result.outcome == ActionOutcome.FAILURE
        assert "file_path" in result.data["error"]


class TestPromptDescription:
    def test_prompt_describes_pagination(self) -> None:
        desc = ReadFileAbility().to_prompt_description()
        assert "offset" in desc
        assert "limit" in desc
