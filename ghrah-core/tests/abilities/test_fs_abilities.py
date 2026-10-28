# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""文件系统内置 Ability 测试：ListDirectoryAbility, WriteFileAbility, EditFileAbility,
MoveFileAbility, DeleteFileAbility。"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from ghrah.scheme.ability.fs.operations.delete_file import DeleteFileAbility
from ghrah.scheme.ability.fs.operations.edit_file import EditFileAbility
from ghrah.scheme.ability.fs.operations.list_directory import ListDirectoryAbility
from ghrah.scheme.ability.fs.operations.move_file import MoveFileAbility
from ghrah.scheme.ability.fs.operations.write_file import WriteFileAbility
from ghrah.scheme.execution.base import ActionOutcome
from ghrah.scheme.execution.context import AbilityExecutionContext
from ghrah.scheme.permissions.fs_permissions import FSPermissionChecker


def _make_context(**overrides: Any) -> AbilityExecutionContext:
    """创建测试用 AbilityExecutionContext。"""
    defaults: dict[str, Any] = {}
    defaults.update(overrides)
    return AbilityExecutionContext(**defaults)


# ── WriteFileAbility 测试 ──


class TestWriteFileAbility:
    """WriteFileAbility 测试。"""

    def test_name(self) -> None:
        ability = WriteFileAbility()
        assert ability.name == "write_file"

    def test_bind_tool_returns_schema(self) -> None:
        """返回 OpenAI function calling 格式的 schema。"""
        ability = WriteFileAbility()
        schema = ability.bind_tool()

        assert schema is not None
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "write_file"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]

    def test_bind_tool_schema_has_required_params(self) -> None:
        """schema 包含 file_path 和 content 参数。"""
        ability = WriteFileAbility()
        schema = ability.bind_tool()

        params = schema["function"]["parameters"]
        props = params.get("properties", {})
        assert "file_path" in props
        assert "content" in props
        assert "file_path" in params.get("required", [])
        assert "content" in params.get("required", [])

    def test_to_prompt_description(self) -> None:
        ability = WriteFileAbility()
        desc = ability.to_prompt_description()
        assert "write_file" in desc

    def test_get_hooks_default_empty(self) -> None:
        ability = WriteFileAbility()
        assert ability.get_hooks() == []

    async def test_execute_write_new_file(self) -> None:
        """成功写入新文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.txt")
            ctx = _make_context(
                tool_args={"file_path": file_path, "content": "Hello, World!"},
            )
            ability = WriteFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert result.data["file_path"] == file_path
            assert result.data["bytes_written"] > 0

            # 验证文件内容
            with open(file_path) as f:
                assert f.read() == "Hello, World!"

    async def test_execute_write_overwrites_existing(self) -> None:
        """覆盖写入已有文件。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("old content")
            temp_path = f.name

        try:
            ctx = _make_context(
                tool_args={"file_path": temp_path, "content": "new content"},
            )
            ability = WriteFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            with open(temp_path) as f:
                assert f.read() == "new content"
        finally:
            os.unlink(temp_path)

    async def test_execute_write_creates_dirs(self) -> None:
        """自动创建父目录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "sub", "dir", "test.txt")
            ctx = _make_context(
                tool_args={"file_path": file_path, "content": "nested"},
            )
            ability = WriteFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            with open(file_path) as f:
                assert f.read() == "nested"

    async def test_execute_write_no_create_dirs(self) -> None:
        """不自动创建目录时，父目录不存在则失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "nonexistent", "test.txt")
            ctx = _make_context(
                tool_args={
                    "file_path": file_path,
                    "content": "test",
                    "create_dirs": False,
                },
            )
            ability = WriteFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.FAILURE
            assert "error" in result.data

    async def test_execute_write_no_file_path(self) -> None:
        """缺少 file_path 返回 FAILURE。"""
        ctx = _make_context(tool_args={"content": "test"})
        ability = WriteFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "file_path is required" in result.data["error"]

    async def test_execute_write_with_permission_checker_allowed(self) -> None:
        """权限检查通过时正常写入。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = FSPermissionChecker(allowed_paths=[tmpdir])
            file_path = os.path.join(tmpdir, "test.txt")
            ctx = _make_context(
                tool_args={"file_path": file_path, "content": "allowed"},
            )
            ability = WriteFileAbility(permission_checker=checker)
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS

    async def test_execute_write_with_permission_checker_denied(self) -> None:
        """权限检查拒绝时返回 FAILURE。"""
        # require_approval=False 时，不在白名单的路径会被直接拒绝
        checker = FSPermissionChecker(allowed_paths=["/tmp/data"], require_approval=False)
        ctx = _make_context(
            tool_args={"file_path": "/etc/config.txt", "content": "denied"},
        )
        ability = WriteFileAbility(permission_checker=checker)
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "Permission denied" in result.data["error"]

    async def test_execute_write_with_encoding(self) -> None:
        """使用指定编码写入文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.txt")
            ctx = _make_context(
                tool_args={
                    "file_path": file_path,
                    "content": "你好世界",
                    "encoding": "utf-8",
                },
            )
            ability = WriteFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            with open(file_path, encoding="utf-8") as f:
                assert f.read() == "你好世界"

    async def test_execute_uses_accumulated_data_fallback(self) -> None:
        """tool_args 为空时从 accumulated_data 获取参数。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.txt")
            ctx = _make_context(
                accumulated_data={"tool_args": {"file_path": file_path, "content": "fallback"}},
            )
            ability = WriteFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS


# ── EditFileAbility 测试 ──


class TestEditFileAbility:
    """EditFileAbility 测试。"""

    def test_name(self) -> None:
        ability = EditFileAbility()
        assert ability.name == "edit_file"

    def test_bind_tool_returns_schema(self) -> None:
        """返回 OpenAI function calling 格式的 schema。"""
        ability = EditFileAbility()
        schema = ability.bind_tool()

        assert schema is not None
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "edit_file"

    def test_bind_tool_schema_has_required_params(self) -> None:
        """schema 包含 file_path, old_str, new_str 参数。"""
        ability = EditFileAbility()
        schema = ability.bind_tool()

        params = schema["function"]["parameters"]
        props = params.get("properties", {})
        assert "file_path" in props
        assert "old_str" in props
        assert "new_str" in props

    def test_to_prompt_description(self) -> None:
        ability = EditFileAbility()
        desc = ability.to_prompt_description()
        assert "edit_file" in desc

    async def test_execute_edit_success(self) -> None:
        """成功替换字符串。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello, World!")
            temp_path = f.name

        try:
            ctx = _make_context(
                tool_args={
                    "file_path": temp_path,
                    "old_str": "World",
                    "new_str": "Python",
                },
            )
            ability = EditFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert result.data["replacements"] == 1

            with open(temp_path) as f:
                assert f.read() == "Hello, Python!"
        finally:
            os.unlink(temp_path)

    async def test_execute_edit_multiline(self) -> None:
        """成功替换多行字符串。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def foo():\n    pass\n")
            temp_path = f.name

        try:
            ctx = _make_context(
                tool_args={
                    "file_path": temp_path,
                    "old_str": "    pass",
                    "new_str": "    return 42",
                },
            )
            ability = EditFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS

            with open(temp_path) as f:
                assert f.read() == "def foo():\n    return 42\n"
        finally:
            os.unlink(temp_path)

    async def test_execute_edit_old_str_not_found(self) -> None:
        """old_str 不在文件中返回 FAILURE。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello, World!")
            temp_path = f.name

        try:
            ctx = _make_context(
                tool_args={
                    "file_path": temp_path,
                    "old_str": "not_present",
                    "new_str": "replacement",
                },
            )
            ability = EditFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.FAILURE
            assert "not found" in result.data["error"]
        finally:
            os.unlink(temp_path)

    async def test_execute_edit_old_str_multiple_matches(self) -> None:
        """old_str 出现多次返回 FAILURE 并提示提供更多上下文。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("foo bar foo baz foo")
            temp_path = f.name

        try:
            ctx = _make_context(
                tool_args={
                    "file_path": temp_path,
                    "old_str": "foo",
                    "new_str": "qux",
                },
            )
            ability = EditFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.FAILURE
            assert "3 times" in result.data["error"]
            assert "more" in result.data["error"].lower()
        finally:
            os.unlink(temp_path)

    async def test_execute_edit_no_file_path(self) -> None:
        """缺少 file_path 返回 FAILURE。"""
        ctx = _make_context(
            tool_args={"old_str": "a", "new_str": "b"},
        )
        ability = EditFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "file_path is required" in result.data["error"]

    async def test_execute_edit_no_old_str(self) -> None:
        """缺少 old_str 返回 FAILURE。"""
        ctx = _make_context(
            tool_args={"file_path": "/tmp/test.txt", "new_str": "b"},
        )
        ability = EditFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "old_str is required" in result.data["error"]

    async def test_execute_edit_file_not_found(self) -> None:
        """文件不存在返回 FAILURE。"""
        ctx = _make_context(
            tool_args={
                "file_path": "/nonexistent/file.txt",
                "old_str": "a",
                "new_str": "b",
            },
        )
        ability = EditFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "not found" in result.data["error"].lower()

    async def test_execute_edit_with_permission_checker_denied(self) -> None:
        """权限检查拒绝时返回 FAILURE。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello")
            temp_path = f.name

        try:
            # require_approval=False 时，不在白名单的路径会被直接拒绝
            checker = FSPermissionChecker(allowed_paths=["/tmp/data"], require_approval=False)
            ctx = _make_context(
                tool_args={
                    "file_path": temp_path,
                    "old_str": "Hello",
                    "new_str": "World",
                },
            )
            ability = EditFileAbility(permission_checker=checker)
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.FAILURE
            assert "Permission denied" in result.data["error"]
        finally:
            os.unlink(temp_path)

    async def test_execute_edit_preserves_file_content(self) -> None:
        """替换只影响 old_str 部分，其余内容不变。"""
        content = "line1\nline2\nline3\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            ctx = _make_context(
                tool_args={
                    "file_path": temp_path,
                    "old_str": "line2",
                    "new_str": "LINE_TWO",
                },
            )
            ability = EditFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            with open(temp_path) as f:
                assert f.read() == "line1\nLINE_TWO\nline3\n"
        finally:
            os.unlink(temp_path)


# ── MoveFileAbility 测试 ──


class TestMoveFileAbility:
    """MoveFileAbility 测试。"""

    def test_name(self) -> None:
        ability = MoveFileAbility()
        assert ability.name == "move_file"

    def test_bind_tool_returns_schema(self) -> None:
        """返回 OpenAI function calling 格式的 schema。"""
        ability = MoveFileAbility()
        schema = ability.bind_tool()

        assert schema is not None
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "move_file"

    def test_bind_tool_schema_has_required_params(self) -> None:
        """schema 包含 source_path 和 destination_path 参数。"""
        ability = MoveFileAbility()
        schema = ability.bind_tool()

        params = schema["function"]["parameters"]
        props = params.get("properties", {})
        assert "source_path" in props
        assert "destination_path" in props

    def test_to_prompt_description(self) -> None:
        ability = MoveFileAbility()
        desc = ability.to_prompt_description()
        assert "move_file" in desc

    async def test_execute_move_success(self) -> None:
        """成功移动文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "source.txt")
            dst = os.path.join(tmpdir, "dest.txt")
            with open(src, "w") as f:
                f.write("content")

            ctx = _make_context(
                tool_args={"source_path": src, "destination_path": dst},
            )
            ability = MoveFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert result.data["source_path"] == src
            assert result.data["destination_path"] == dst
            assert not os.path.exists(src)
            assert os.path.exists(dst)
            with open(dst) as f:
                assert f.read() == "content"

    async def test_execute_move_cross_directory(self) -> None:
        """跨目录移动文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, "src")
            dst_dir = os.path.join(tmpdir, "dst")
            os.makedirs(src_dir)
            os.makedirs(dst_dir)

            src = os.path.join(src_dir, "file.txt")
            dst = os.path.join(dst_dir, "file.txt")
            with open(src, "w") as f:
                f.write("moved")

            ctx = _make_context(
                tool_args={"source_path": src, "destination_path": dst},
            )
            ability = MoveFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert not os.path.exists(src)
            assert os.path.exists(dst)

    async def test_execute_move_creates_dirs(self) -> None:
        """自动创建目标目录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "source.txt")
            dst = os.path.join(tmpdir, "sub", "dir", "dest.txt")
            with open(src, "w") as f:
                f.write("content")

            ctx = _make_context(
                tool_args={"source_path": src, "destination_path": dst},
            )
            ability = MoveFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert os.path.exists(dst)

    async def test_execute_move_no_create_dirs(self) -> None:
        """不创建目录时，目标目录不存在则失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "source.txt")
            dst = os.path.join(tmpdir, "nonexistent", "dest.txt")
            with open(src, "w") as f:
                f.write("content")

            ctx = _make_context(
                tool_args={
                    "source_path": src,
                    "destination_path": dst,
                    "create_dirs": False,
                },
            )
            ability = MoveFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.FAILURE

    async def test_execute_move_overwrite(self) -> None:
        """覆盖已存在的目标文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "source.txt")
            dst = os.path.join(tmpdir, "dest.txt")
            with open(src, "w") as f:
                f.write("new content")
            with open(dst, "w") as f:
                f.write("old content")

            ctx = _make_context(
                tool_args={
                    "source_path": src,
                    "destination_path": dst,
                    "overwrite": True,
                },
            )
            ability = MoveFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            with open(dst) as f:
                assert f.read() == "new content"

    async def test_execute_move_destination_exists_no_overwrite(self) -> None:
        """目标已存在且不覆盖时失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "source.txt")
            dst = os.path.join(tmpdir, "dest.txt")
            with open(src, "w") as f:
                f.write("content")
            with open(dst, "w") as f:
                f.write("existing")

            ctx = _make_context(
                tool_args={"source_path": src, "destination_path": dst},
            )
            ability = MoveFileAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.FAILURE
            assert "already exists" in result.data["error"]

    async def test_execute_move_source_not_found(self) -> None:
        """源文件不存在返回 FAILURE。"""
        ctx = _make_context(
            tool_args={
                "source_path": "/nonexistent/source.txt",
                "destination_path": "/tmp/dest.txt",
            },
        )
        ability = MoveFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "not found" in result.data["error"].lower()

    async def test_execute_move_no_source_path(self) -> None:
        """缺少 source_path 返回 FAILURE。"""
        ctx = _make_context(
            tool_args={"destination_path": "/tmp/dest.txt"},
        )
        ability = MoveFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "source_path is required" in result.data["error"]

    async def test_execute_move_no_destination_path(self) -> None:
        """缺少 destination_path 返回 FAILURE。"""
        ctx = _make_context(
            tool_args={"source_path": "/tmp/source.txt"},
        )
        ability = MoveFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "destination_path is required" in result.data["error"]

    async def test_execute_move_with_permission_checker(self) -> None:
        """权限检查通过时正常移动。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = FSPermissionChecker(allowed_paths=[tmpdir])
            src = os.path.join(tmpdir, "source.txt")
            dst = os.path.join(tmpdir, "dest.txt")
            with open(src, "w") as f:
                f.write("content")

            ctx = _make_context(
                tool_args={"source_path": src, "destination_path": dst},
            )
            ability = MoveFileAbility(permission_checker=checker)
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS

    async def test_execute_move_permission_denied_read(self) -> None:
        """源路径读取权限拒绝时返回 FAILURE。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = FSPermissionChecker(allowed_paths=[tmpdir])
            src = "/etc/passwd"  # 不在白名单中
            dst = os.path.join(tmpdir, "dest.txt")

            ctx = _make_context(
                tool_args={"source_path": src, "destination_path": dst},
            )
            ability = MoveFileAbility(permission_checker=checker)
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.FAILURE


# ── DeleteFileAbility 测试 ──


class TestDeleteFileAbility:
    """DeleteFileAbility 测试。"""

    def test_name(self) -> None:
        ability = DeleteFileAbility()
        assert ability.name == "delete_file"

    def test_bind_tool_returns_schema(self) -> None:
        """返回 OpenAI function calling 格式的 schema。"""
        ability = DeleteFileAbility()
        schema = ability.bind_tool()

        assert schema is not None
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "delete_file"

    def test_bind_tool_schema_has_file_path(self) -> None:
        """schema 包含 file_path 参数。"""
        ability = DeleteFileAbility()
        schema = ability.bind_tool()

        params = schema["function"]["parameters"]
        assert "file_path" in params.get("properties", {})

    def test_to_prompt_description(self) -> None:
        ability = DeleteFileAbility()
        desc = ability.to_prompt_description()
        assert "delete_file" in desc

    async def test_execute_delete_success(self) -> None:
        """成功删除文件。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("to be deleted")
            temp_path = f.name

        assert os.path.exists(temp_path)

        ctx = _make_context(tool_args={"file_path": temp_path})
        ability = DeleteFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["file_path"] == temp_path
        assert result.data["deleted"] is True
        assert not os.path.exists(temp_path)

    async def test_execute_delete_file_not_found(self) -> None:
        """文件不存在返回 FAILURE。"""
        ctx = _make_context(tool_args={"file_path": "/nonexistent/file.txt"})
        ability = DeleteFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "not found" in result.data["error"].lower()

    async def test_execute_delete_no_file_path(self) -> None:
        """缺少 file_path 返回 FAILURE。"""
        ctx = _make_context(tool_args={})
        ability = DeleteFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "file_path is required" in result.data["error"]

    async def test_execute_delete_with_permission_checker_allowed(self) -> None:
        """权限检查通过时正常删除。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = FSPermissionChecker(allowed_paths=[tmpdir])
            file_path = os.path.join(tmpdir, "test.txt")
            with open(file_path, "w") as f:
                f.write("content")

            ctx = _make_context(tool_args={"file_path": file_path})
            ability = DeleteFileAbility(permission_checker=checker)
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert not os.path.exists(file_path)

    async def test_execute_delete_with_permission_checker_denied(self) -> None:
        """权限检查拒绝时返回 FAILURE。"""
        # require_approval=False 时，不在白名单的路径会被直接拒绝
        checker = FSPermissionChecker(allowed_paths=["/tmp/data"], require_approval=False)
        ctx = _make_context(tool_args={"file_path": "/etc/config.txt"})
        ability = DeleteFileAbility(permission_checker=checker)
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "Permission denied" in result.data["error"]

    async def test_execute_delete_uses_accumulated_data_fallback(self) -> None:
        """tool_args 为空时从 accumulated_data 获取参数。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("content")
            temp_path = f.name

        ctx = _make_context(
            accumulated_data={"tool_args": {"file_path": temp_path}},
        )
        ability = DeleteFileAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.SUCCESS
        assert not os.path.exists(temp_path)


# ── ListDirectoryAbility 测试 ──


class TestListDirectoryAbility:
    """ListDirectoryAbility 测试。"""

    def test_name(self) -> None:
        ability = ListDirectoryAbility()
        assert ability.name == "list_directory"

    def test_bind_tool_returns_schema(self) -> None:
        """返回 OpenAI function calling 格式的 schema。"""
        ability = ListDirectoryAbility()
        schema = ability.bind_tool()

        assert schema is not None
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "list_directory"

    def test_bind_tool_schema_has_dir_path(self) -> None:
        """schema 包含 dir_path 参数。"""
        ability = ListDirectoryAbility()
        schema = ability.bind_tool()

        params = schema["function"]["parameters"]
        assert "dir_path" in params.get("properties", {})

    def test_to_prompt_description(self) -> None:
        ability = ListDirectoryAbility()
        desc = ability.to_prompt_description()
        assert "list_directory" in desc

    def test_get_hooks_default_empty(self) -> None:
        ability = ListDirectoryAbility()
        assert ability.get_hooks() == []

    async def test_execute_list_flat(self) -> None:
        """非递归列出目录内容。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建一些文件和目录
            os.makedirs(os.path.join(tmpdir, "subdir"))
            with open(os.path.join(tmpdir, "file1.txt"), "w") as f:
                f.write("hello")
            with open(os.path.join(tmpdir, "file2.txt"), "w") as f:
                f.write("world")

            ctx = _make_context(tool_args={"dir_path": tmpdir})
            ability = ListDirectoryAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert result.data["dir_path"] == tmpdir
            entries = result.data["entries"]
            assert len(entries) == 3  # subdir, file1.txt, file2.txt

            names = {e["name"] for e in entries}
            assert names == {"subdir", "file1.txt", "file2.txt"}

            # 检查类型
            subdir_entry = next(e for e in entries if e["name"] == "subdir")
            assert subdir_entry["type"] == "directory"

            file_entry = next(e for e in entries if e["name"] == "file1.txt")
            assert file_entry["type"] == "file"
            assert file_entry["size"] == 5  # "hello"

    async def test_execute_list_recursive(self) -> None:
        """递归列出目录内容。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建嵌套结构
            os.makedirs(os.path.join(tmpdir, "sub", "nested"))
            with open(os.path.join(tmpdir, "root.txt"), "w") as f:
                f.write("root")
            with open(os.path.join(tmpdir, "sub", "sub.txt"), "w") as f:
                f.write("sub")
            with open(os.path.join(tmpdir, "sub", "nested", "deep.txt"), "w") as f:
                f.write("deep")

            ctx = _make_context(
                tool_args={"dir_path": tmpdir, "recursive": True},
            )
            ability = ListDirectoryAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            entries = result.data["entries"]
            names = {e["name"] for e in entries}
            assert names == {"root.txt", "sub", "sub.txt", "nested", "deep.txt"}

            # 检查递归条目有 path 字段
            deep_entry = next(e for e in entries if e["name"] == "deep.txt")
            assert "path" in deep_entry
            assert "nested" in deep_entry["path"]

    async def test_execute_list_empty_directory(self) -> None:
        """空目录返回空列表。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(tool_args={"dir_path": tmpdir})
            ability = ListDirectoryAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert result.data["entries"] == []

    async def test_execute_list_directory_not_found(self) -> None:
        """目录不存在返回 FAILURE。"""
        ctx = _make_context(tool_args={"dir_path": "/nonexistent/dir"})
        ability = ListDirectoryAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "not found" in result.data["error"].lower()

    async def test_execute_list_not_a_directory(self) -> None:
        """路径是文件而非目录返回 FAILURE。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("not a dir")
            temp_path = f.name

        try:
            ctx = _make_context(tool_args={"dir_path": temp_path})
            ability = ListDirectoryAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.FAILURE
            assert "not a directory" in result.data["error"].lower()
        finally:
            os.unlink(temp_path)

    async def test_execute_list_no_dir_path(self) -> None:
        """缺少 dir_path 返回 FAILURE。"""
        ctx = _make_context(tool_args={})
        ability = ListDirectoryAbility()
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "dir_path is required" in result.data["error"]

    async def test_execute_list_with_permission_checker_allowed(self) -> None:
        """权限检查通过时正常列出。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = FSPermissionChecker(allowed_paths=[tmpdir])
            with open(os.path.join(tmpdir, "file.txt"), "w") as f:
                f.write("content")

            ctx = _make_context(tool_args={"dir_path": tmpdir})
            ability = ListDirectoryAbility(permission_checker=checker)
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert len(result.data["entries"]) == 1

    async def test_execute_list_with_permission_checker_denied(self) -> None:
        """权限检查拒绝时返回 FAILURE。"""
        checker = FSPermissionChecker(allowed_paths=["/tmp/data"], require_approval=False)
        ctx = _make_context(tool_args={"dir_path": "/etc"})
        ability = ListDirectoryAbility(permission_checker=checker)
        result = await ability.execute(ctx)

        assert result.outcome == ActionOutcome.FAILURE
        assert "Permission denied" in result.data["error"]

    async def test_execute_uses_accumulated_data_fallback(self) -> None:
        """tool_args 为空时从 accumulated_data 获取参数。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(
                accumulated_data={"tool_args": {"dir_path": tmpdir}},
            )
            ability = ListDirectoryAbility()
            result = await ability.execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS

    async def test_pagination_legacy_mode_without_offset_limit(self) -> None:
        """offset=0/limit=0（默认）→ 旧行为：全量返回，无分页元数据。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(5):
                with open(os.path.join(tmpdir, f"f{i}.txt"), "w") as f:
                    f.write("x")

            ctx = _make_context(tool_args={"dir_path": tmpdir})
            result = await ListDirectoryAbility().execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert len(result.data["entries"]) == 5
            assert result.data["total_entries"] == 5
            # 旧行为：无 offset/limit/truncated/next_offset 键
            assert "truncated" not in result.data
            assert "offset" not in result.data
            assert "next_offset" not in result.data

    async def test_pagination_limit_only(self) -> None:
        """仅 limit → 返回前 N 条 + truncated/next_offset。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(5):
                with open(os.path.join(tmpdir, f"f{i}.txt"), "w") as f:
                    f.write("x")

            ctx = _make_context(tool_args={"dir_path": tmpdir, "limit": 2})
            result = await ListDirectoryAbility().execute(ctx)

            assert result.outcome == ActionOutcome.SUCCESS
            assert [e["name"] for e in result.data["entries"]] == ["f0.txt", "f1.txt"]
            assert result.data["total_entries"] == 5
            assert result.data["offset"] == 0
            assert result.data["limit"] == 2
            assert result.data["truncated"] is True
            assert result.data["next_offset"] == 2

    async def test_pagination_offset_and_limit(self) -> None:
        """offset+limit → 窗口切片；末页 next_offset=None。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(5):
                with open(os.path.join(tmpdir, f"f{i}.txt"), "w") as f:
                    f.write("x")

            ability = ListDirectoryAbility()
            ctx = _make_context(tool_args={"dir_path": tmpdir, "offset": 1, "limit": 2})
            result = await ability.execute(ctx)
            assert [e["name"] for e in result.data["entries"]] == ["f1.txt", "f2.txt"]
            assert result.data["truncated"] is True
            assert result.data["next_offset"] == 3

            # 末页：正好取完剩余条目
            ctx = _make_context(tool_args={"dir_path": tmpdir, "offset": 3, "limit": 2})
            result = await ability.execute(ctx)
            assert [e["name"] for e in result.data["entries"]] == ["f3.txt", "f4.txt"]
            assert result.data["truncated"] is True  # offset>0 即视为分页窗口
            assert result.data["next_offset"] is None

    async def test_pagination_offset_only_tail(self) -> None:
        """仅 offset → 从 offset 到末尾。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(4):
                with open(os.path.join(tmpdir, f"f{i}.txt"), "w") as f:
                    f.write("x")

            ctx = _make_context(tool_args={"dir_path": tmpdir, "offset": 2})
            result = await ListDirectoryAbility().execute(ctx)
            assert [e["name"] for e in result.data["entries"]] == ["f2.txt", "f3.txt"]
            assert result.data["truncated"] is True
            assert result.data["next_offset"] is None

    async def test_pagination_recursive_truncates_walk(self) -> None:
        """recursive + limit → 递归遍历结果同样截断。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "sub"))
            for i in range(3):
                with open(os.path.join(tmpdir, f"f{i}.txt"), "w") as f:
                    f.write("x")
            with open(os.path.join(tmpdir, "sub", "deep.txt"), "w") as f:
                f.write("d")

            ctx = _make_context(tool_args={"dir_path": tmpdir, "recursive": True, "limit": 2})
            result = await ListDirectoryAbility().execute(ctx)
            assert len(result.data["entries"]) == 2
            assert result.data["total_entries"] == 5  # sub + 3 files + deep.txt
            assert result.data["truncated"] is True

    async def test_pagination_offset_beyond_end_returns_empty(self) -> None:
        """offset 越界 → 空列表（不报错）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "f.txt"), "w") as f:
                f.write("x")

            ctx = _make_context(tool_args={"dir_path": tmpdir, "offset": 10, "limit": 5})
            result = await ListDirectoryAbility().execute(ctx)
            assert result.outcome == ActionOutcome.SUCCESS
            assert result.data["entries"] == []
            assert result.data["total_entries"] == 1
