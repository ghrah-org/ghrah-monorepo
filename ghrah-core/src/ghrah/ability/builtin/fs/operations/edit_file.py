# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""EditFileAbility：精确字符串替换能力。

功能：
- 在文件中查找唯一匹配的 old_str 并替换为 new_str
- old_str 必须在文件中恰好出现 1 次，否则返回 FAILURE
- 集成 FSPermissionChecker 权限检查

执行逻辑：
1. 从 call.arguments 获取参数
2. 权限验证
3. 读取文件内容
4. 检查 old_str 匹配次数（0 / 1 / N）
5. 执行替换并写回
6. 返回 ActionResult
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ghrah.ability.execution.base import Ability
from ghrah.ability.permissions.fs_permissions import FSPermissionChecker
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.hooks import Hook
    from ghrah.ability.execution.invocation import OperationCall


# ---------------------------------------------------------------------------
# Operation 声明（v1）：build 从下列常量与 Input/Output 模型生成 ability.yaml 的 members。
# 旧 manifest 的 metadata.description / tags / permissions 分别落在 SUMMARY / TAGS / PERMISSIONS。
# ---------------------------------------------------------------------------
PERMISSIONS = {
    "fs_write": True,
    "require_hitl": True,
}

# --- 成员声明（build 生成 members 的唯一来源；改完须重新 build）
TITLE = "Edit File"
SUMMARY = (
    "Edit a file by replacing a unique string. The old_str must appear exactly once in the file."
)
TAGS = [
    "filesystem",
    "write",
    "edit",
]
EFFECTS = ["filesystem:write"]
TIMEOUT_MS = 60_000
# --- 成员声明结束

logger = logging.getLogger(__name__)

__all__ = ["EditFileAbility", "EditFileInput"]


class EditFileInput(BaseModel):
    """EditFile 工具的输入参数。"""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="Path to the file to edit")
    old_str: str = Field(
        description="The exact string to find and replace (must be unique in the file)"
    )
    new_str: str = Field(description="The replacement string")
    encoding: str = Field(default="utf-8", description="Text encoding for the file")


Input = EditFileInput  # v1 生成器入口别名（生成器只读 module.Input）


class EditFileOutput(BaseModel):
    """EditFile 工具的成功结果契约。"""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="Path of the file that was edited")
    replacements: int = Field(description="Number of replacements applied")


Output = EditFileOutput  # v1 生成器入口别名（生成器只读 module.Output）


class EditFileAbility(Ability):
    """文件编辑能力 — 精确字符串替换。

    核心设计：LLM 提供一个在文件中唯一出现的 old_str，将其替换为 new_str。
    如果 old_str 在文件中出现多次，返回 FAILURE 并提示提供更多上下文。

    特点：
    - bind_tool() 返回 OpenAI function calling 格式的 schema
    - old_str 必须精确匹配（包括空白字符和缩进）
    - 替换是原子操作：先读取、验证、替换、再写回
    - 集成 FSPermissionChecker 权限检查

    用法::

        # 无权限限制
        ability = EditFileAbility()

        # 限制写入目录
        checker = FSPermissionChecker(allowed_paths=["/home/user/project"])
        ability = EditFileAbility(permission_checker=checker)
    """

    def __init__(
        self,
        hooks: list[Hook] | None = None,
        permission_checker: FSPermissionChecker | None = None,
    ) -> None:
        self._hooks = hooks or []
        self._checker = permission_checker

    @property
    def name(self) -> str:
        return "edit_file"

    def bind_tool(self) -> dict[str, Any]:
        """返回 OpenAI function calling 格式的 tool schema。"""
        return {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": (
                    "Edit a file by replacing a unique string. "
                    "The old_str must appear exactly once in the file. "
                    "If it appears multiple times, the operation fails and you should "
                    "provide more surrounding context to make it unique."
                ),
                "parameters": EditFileInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "edit_file(file_path: str, old_str: str, new_str: str, "
            "encoding: str = 'utf-8'): Edit a file by replacing old_str with new_str. "
            "old_str must be unique in the file."
        )

    def get_hooks(self) -> list[Hook]:
        return list(self._hooks)

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        """执行文件编辑（精确字符串替换）。

        Args:
            call: 本次调用的值对象。
            services: 执行期服务表。

        Returns:
            ActionResult：
            - SUCCESS: data 包含 "file_path" 和 "replacements"
            - FAILURE: data 包含 "error"
        """
        tool_args = call.arguments
        file_path = tool_args.get("file_path", "")
        old_str = tool_args.get("old_str", "")
        new_str = tool_args.get("new_str", "")
        encoding = tool_args.get("encoding", "utf-8")

        if not file_path:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "file_path is required"},
            )

        if not old_str:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "old_str is required"},
            )

        # 权限验证
        if self._checker is not None:
            allowed, reason = self._checker.check_write_path(file_path)
            if not allowed:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={"error": reason},
                )

        logger.debug(f"EditFileAbility: editing {file_path}")

        try:
            # 读取文件
            with Path(file_path).open(encoding=encoding) as f:
                content = f.read()

            # 检查 old_str 匹配次数
            count = content.count(old_str)

            if count == 0:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={
                        "error": (
                            f"old_str not found in file: {file_path}. "
                            "Please verify the exact string content including "
                            "whitespace and indentation."
                        ),
                    },
                )

            if count > 1:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={
                        "error": (
                            f"old_str appears {count} times in file: {file_path}, "
                            f"expected exactly 1. Provide more surrounding context "
                            f"to make it unique."
                        ),
                    },
                )

            # 执行替换
            new_content = content.replace(old_str, new_str, 1)

            # 写回文件
            with Path(file_path).open("w", encoding=encoding) as f:
                f.write(new_content)

            logger.debug(f"EditFileAbility: replaced 1 occurrence in {file_path}")

            return ActionResult(
                outcome=ActionOutcome.SUCCESS,
                data={
                    "file_path": file_path,
                    "replacements": 1,
                },
            )
        except FileNotFoundError:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"File not found: {file_path}"},
            )
        except PermissionError:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Permission denied: {file_path}"},
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": str(e)},
            )
