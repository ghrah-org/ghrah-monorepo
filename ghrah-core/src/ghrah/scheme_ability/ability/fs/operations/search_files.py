# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SearchFilesAbility：内容/文件名搜索能力。

双模式执行：
1. Subject 模式（command_runner=SandboxExecutor）：组装 ``rg`` 命令行委托
   runner 执行（``rg`` 在 DEFAULT_SAFE_COMMANDS 白名单内，无审批摩擦）；
2. 单体模式：``shutil.which("rg")`` 可用则直接子进程执行 rg；否则回退
   Python 纯实现（os.walk + 逐行匹配，跳过 .git/node_modules 与二进制文件）。

安全性：组装的 rg 命令行仅使用固定 flag 集（--line-number/--context/
--glob/--max-count/-i），不透传用户任意 flag（排除 ``--pre`` 等可执行
任意预处理器的入口）。

执行逻辑：
1. 从 context.tool_args 获取 LLM 解析的参数
2. 权限验证（FSPermissionChecker.check_read_path）
3. 执行搜索（rg 或 Python 回退）
4. 返回 ActionResult(data={"matches": [...], "total": N, "truncated": bool})
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.scheme_ability.execution.base import Ability
from ghrah.scheme_ability.permissions.fs_permissions import FSPermissionChecker
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme_ability.execution.context import AbilityExecutionContext
    from ghrah.scheme_ability.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/search_files.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {
    "fs_read_only": True,
}
HOOKS = {}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = (
    "Search file contents for a regex pattern using ripgrep, with results including file "
    "paths and line numbers."
)
TAGS = [
    "filesystem",
    "read",
    "search",
]
# --- catalogue 声明结束

logger = logging.getLogger(__name__)

__all__ = ["SearchFilesAbility", "SearchFilesInput", "DEFAULT_SKIP_DIRS"]

DEFAULT_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "dist"})
_BINARY_SAMPLE_SIZE = 1024

# rg --line-number --no-heading 输出行：path 可含盘符冒号/UNC/多级目录冒号，
# 非贪婪匹配到首个 ``:(digits):`` 边界
_RG_LINE_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<text>.*)$")

# rg 默认不跳过 node_modules 等依赖目录——与 Python 回退的 DEFAULT_SKIP_DIRS
# 语义对齐，经 glob 白名单显式排除
# rg 默认不跳过 node_modules 等依赖目录——与 Python 回退的 DEFAULT_SKIP_DIRS
# 语义对齐，经 glob 显式排除（目录形态需尾斜杠：``!dir/`` 才对 rg 生效）
_RG_SKIP_ARGS: tuple[str, ...] = tuple(
    arg for d in sorted(DEFAULT_SKIP_DIRS) for arg in ("--glob", f"!{d}/")
)


def _build_rg_args(
    pattern: str,
    path: str,
    include: str | None,
    context_lines: int,
    ignore_case: bool,
) -> list[str]:
    """组装固定 flag 集的 rg 命令行（安全边界：不透传任意用户 flag）。

    max_results 不进命令行：``--max-count`` 是 per-file 限制而非全局截断，
    全局截断在 _parse_rg_output 解析层完成。
    """
    args = ["rg", "--line-number", "--color", "never", "--no-heading"]
    if ignore_case:
        args.append("-i")
    if context_lines > 0:
        args += ["--context", str(context_lines)]
    args += _RG_SKIP_ARGS
    if include:
        args += ["--glob", include]
    args += [pattern, path]
    return args


class SearchFilesInput(BaseModel):
    """SearchFiles 工具的输入参数。"""

    pattern: str = Field(description="Regular expression to search for")
    path: str = Field(description="Directory or file to search in")
    include: str | None = Field(
        default=None,
        description="Optional glob filter for file names (e.g. '*.py')",
    )
    context_lines: int = Field(
        default=0,
        ge=0,
        le=5,
        description="Lines of context around each match",
    )
    max_results: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of matching lines to return",
    )
    ignore_case: bool = Field(default=False, description="Case-insensitive search")


class SearchFilesAbility(Ability):
    """内容搜索能力 — Subject 模式走 rg 命令，单体回退 Python 纯实现。

    用法::

        # Subject 模式（委托 SandboxExecutor）
        ability = SearchFilesAbility(command_runner=sandbox_executor)

        # 单体模式（本地 rg，无 rg 时 Python 回退）
        ability = SearchFilesAbility()

        # 限制搜索目录
        ability = SearchFilesAbility(permission_checker=checker)
    """

    def __init__(
        self,
        hooks: list[Hook] | None = None,
        permission_checker: FSPermissionChecker | None = None,
        command_runner: Any | None = None,
    ) -> None:
        self._hooks = hooks or []
        self._checker = permission_checker
        self._command_runner = command_runner

    @property
    def name(self) -> str:
        return "search_files"

    def bind_tool(self) -> dict[str, Any]:
        """返回 OpenAI function calling 格式的 tool schema。"""
        return {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": (
                    "Search file contents for a regex pattern. Returns matching "
                    "lines with file paths and line numbers. Much more efficient "
                    "than list_directory + read_file for locating code in large "
                    "repositories. Use include to filter by file glob (e.g. '*.py')."
                ),
                "parameters": SearchFilesInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        """用于不支持 function calling 的模型。"""
        return (
            "search_files(pattern: str, path: str, include: str | None = None, "
            "context_lines: int = 0, max_results: int = 50, "
            "ignore_case: bool = False) -> dict: "
            "Search file contents for a regex pattern"
        )

    def get_hooks(self) -> list[Hook]:
        return list(self._hooks)

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        """执行内容搜索。

        Returns:
            ActionResult：
            - SUCCESS: data 包含 "matches"（[{file, line, text}]）、
              "total" 与 "truncated" 标志
            - FAILURE: data 包含 "error"
        """
        tool_args = context.tool_args or context.accumulated_data.get("tool_args", {})
        pattern = tool_args.get("pattern", "")
        path = tool_args.get("path", "")
        include = tool_args.get("include")
        context_lines = int(tool_args.get("context_lines", 0) or 0)
        max_results = int(tool_args.get("max_results", 50))
        ignore_case = bool(tool_args.get("ignore_case", False))

        if not pattern:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "pattern is required"},
            )
        if not path:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "path is required"},
            )
        if not os.path.exists(path):
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Path does not exist: {path}"},
            )

        if self._checker is not None:
            allowed, reason = self._checker.check_read_path(path)
            if not allowed:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={"error": reason},
                )

        logger.debug(f"SearchFilesAbility: searching {pattern!r} in {path}")

        try:
            if self._command_runner is not None:
                return await self._search_via_runner(
                    pattern, path, include, context_lines, max_results, ignore_case
                )
            if shutil.which("rg") is not None:
                return await self._search_via_local_rg(
                    pattern, path, include, context_lines, max_results, ignore_case
                )
            return await asyncio.to_thread(
                _python_search, pattern, path, include, context_lines, max_results, ignore_case
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Search failed: {e}", "pattern": pattern},
            )

    async def _search_via_runner(
        self,
        pattern: str,
        path: str,
        include: str | None,
        context_lines: int,
        max_results: int,
        ignore_case: bool,
    ) -> ActionResult:
        """Subject 模式：组装固定 flag 集的 rg 命令，委托 command_runner。"""
        args = _build_rg_args(pattern, path, include, context_lines, ignore_case)

        runner = self._command_runner
        try:
            result = await runner.execute_command(command=args, cwd=None, timeout=60.0)
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Search failed: {e}", "pattern": pattern},
            )

        matches = _parse_rg_output(result.stdout, max_results)
        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={
                "matches": matches["items"],
                "total": matches["count"],
                "truncated": matches["truncated"],
                "pattern": pattern,
                "path": path,
                "exit_code": result.exit_code,
            },
        )

    async def _search_via_local_rg(
        self,
        pattern: str,
        path: str,
        include: str | None,
        context_lines: int,
        max_results: int,
        ignore_case: bool,
    ) -> ActionResult:
        """单体模式 rg 可用：直接子进程执行同一命令行。"""
        args = _build_rg_args(pattern, path, include, context_lines, ignore_case)

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _stderr_bytes = await process.communicate()
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Search failed: {e}", "pattern": pattern},
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        matches = _parse_rg_output(stdout, max_results)
        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={
                "matches": matches["items"],
                "total": matches["count"],
                "truncated": matches["truncated"],
                "pattern": pattern,
                "path": path,
            },
        )


def _looks_binary(sample: bytes) -> bool:
    """启发式二进制探测：采样块含 NUL 字节判为二进制。"""
    return b"\x00" in sample


def _parse_rg_output(stdout: str, max_results: int) -> dict[str, Any]:
    """解析 rg --line-number --no-heading 输出为结构化 matches。

    rg 退出码 1 表示无匹配（正常）。行格式 ``path:line:text``：path 可含
    盘符冒号（Windows）与 POSIX 全路径冒号，text 为首个 ``数字:`` 之后
    的剩余部分——用非贪婪正则从行首锚定 ``(path):(digits):(text)``。
    """
    items: list[dict[str, str | int]] = []
    count = 0
    for raw in stdout.splitlines():
        if not raw or raw.startswith("--") or raw.endswith("--"):
            continue
        m = _RG_LINE_RE.match(raw)
        if m is None:
            continue
        count += 1
        if len(items) < max_results:
            items.append(
                {
                    "file": m.group("path"),
                    "line": int(m.group("line")),
                    "text": m.group("text"),
                }
            )
    truncated = count > len(items)
    return {"items": items, "count": count, "truncated": truncated}


def _python_search(
    pattern: str,
    path: str,
    include: str | None,
    context_lines: int,
    max_results: int,
    ignore_case: bool,
) -> ActionResult:
    """Python 纯实现回退（rg 不可用时的单体模式保底）。"""
    import fnmatch
    import re as _re

    flags = _re.IGNORECASE if ignore_case else 0
    try:
        regex = _re.compile(pattern, flags)
    except _re.error as e:
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={"error": f"Invalid regex pattern: {e}", "pattern": pattern},
        )

    roots = [path] if os.path.isfile(path) else []
    if not roots:
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in DEFAULT_SKIP_DIRS]
            for filename in filenames:
                if include and not fnmatch.fnmatch(filename, include):
                    continue
                roots.append(os.path.join(dirpath, filename))

    items: list[dict[str, str | int]] = []
    count = 0
    for file_path in roots:
        try:
            with open(file_path, "rb") as fb:
                if _looks_binary(fb.read(_BINARY_SAMPLE_SIZE)):
                    continue
            with open(file_path, encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, start=1):
                    if regex.search(line):
                        count += 1
                        if len(items) < max_results:
                            items.append(
                                {
                                    "file": file_path,
                                    "line": line_no,
                                    "text": line.rstrip("\n"),
                                }
                            )
        except (OSError, PermissionError):
            continue

    return ActionResult(
        outcome=ActionOutcome.SUCCESS,
        data={
            "matches": items,
            "total": count,
            "truncated": count > len(items),
            "pattern": pattern,
            "path": path,
        },
    )
