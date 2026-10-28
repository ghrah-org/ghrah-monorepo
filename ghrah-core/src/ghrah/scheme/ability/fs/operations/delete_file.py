# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""DeleteFileAbility：文件删除能力。

功能：
- 删除指定文件（不可逆操作）
- 集成 FSPermissionChecker 权限检查
- 删除是破坏性操作，需要写入权限

执行逻辑：
1. 从 context.tool_args 获取参数
2. 权限验证（写入权限，因为删除是破坏性操作）
3. 验证文件存在
4. 删除文件
5. 返回 ActionResult
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ghrah.scheme.execution.base import Ability
from ghrah.scheme.permissions.fs_permissions import FSPermissionChecker
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme.execution.context import AbilityExecutionContext
    from ghrah.scheme.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/delete_file.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {
    "fs_write": True,
    "require_hitl": True,
}
HOOKS = {
    "pre_execute": [
        {
            "type": "builtin",
            "handler": "write_approval",
            "params": {
                "check_write_path": True,
            },
        },
    ],
}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = "Delete a file at the specified path. This operation is irreversible."
TAGS = [
    "filesystem",
    "write",
    "delete",
]
# --- catalogue 声明结束

logger = logging.getLogger(__name__)

__all__ = ["DeleteFileAbility", "DeleteFileInput"]


class DeleteFileInput(BaseModel):
    """DeleteFile 工具的输入参数。"""

    file_path: str


class DeleteFileAbility(Ability):
    """文件删除能力 — 不可逆操作。

    特点：
    - 删除是破坏性操作，需要写入权限（而非读取权限）
    - bind_tool() 返回 OpenAI function calling 格式的 schema
    - 集成 FSPermissionChecker 权限检查

    用法::

        # 无权限限制
        ability = DeleteFileAbility()

        # 限制删除目录
        checker = FSPermissionChecker(allowed_paths=["/tmp/data"])
        ability = DeleteFileAbility(permission_checker=checker)
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
        return "delete_file"

    def bind_tool(self) -> dict[str, Any]:
        """返回 OpenAI function calling 格式的 tool schema。"""
        return {
            "type": "function",
            "function": {
                "name": "delete_file",
                "description": (
                    "Delete a file at the specified path. This operation is irreversible."
                ),
                "parameters": DeleteFileInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return "delete_file(file_path: str): Delete a file. This operation is irreversible."

    def get_hooks(self) -> list[Hook]:
        return list(self._hooks)

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        """执行文件删除。

        Args:
            context: 执行上下文

        Returns:
            ActionResult：
            - SUCCESS: data 包含 "file_path" 和 "deleted"
            - FAILURE: data 包含 "error"
        """
        tool_args = context.tool_args or context.accumulated_data.get("tool_args", {})
        file_path = tool_args.get("file_path", "")

        if not file_path:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "file_path is required"},
            )

        # 权限验证（删除是破坏性操作，需要写入权限）
        if self._checker is not None:
            allowed, reason = self._checker.check_write_path(file_path)
            if not allowed:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={"error": reason},
                )

        logger.debug(f"DeleteFileAbility: deleting {file_path}")

        try:
            if not Path(file_path).exists():
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={"error": f"File not found: {file_path}"},
                )

            Path(file_path).unlink()

            logger.debug(f"DeleteFileAbility: deleted {file_path}")

            return ActionResult(
                outcome=ActionOutcome.SUCCESS,
                data={
                    "file_path": file_path,
                    "deleted": True,
                },
            )
        except PermissionError:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Permission denied: {file_path}"},
            )
        except OSError as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to delete file: {e}"},
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": str(e)},
            )
