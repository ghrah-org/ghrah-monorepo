# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""DeleteFileAbility：文件删除能力。

功能：
- 删除指定文件（不可逆操作）
- 集成 FSPermissionChecker 权限检查
- 删除是破坏性操作，需要写入权限

执行逻辑：
1. 从 call.arguments 获取参数
2. 权限验证（写入权限，因为删除是破坏性操作）
3. 验证文件存在
4. 删除文件
5. 返回 ActionResult
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
TITLE = "Delete File"
SUMMARY = "Delete a file at the specified path. This operation is irreversible."
TAGS = [
    "filesystem",
    "write",
    "delete",
]
EFFECTS = ["filesystem:write"]
TIMEOUT_MS = 60_000
# --- 成员声明结束

logger = logging.getLogger(__name__)

__all__ = ["DeleteFileAbility", "DeleteFileInput"]


class DeleteFileInput(BaseModel):
    """DeleteFile 工具的输入参数。"""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="Path to the file to delete")


Input = DeleteFileInput  # v1 生成器入口别名（生成器只读 module.Input）


class DeleteFileOutput(BaseModel):
    """DeleteFile 工具的成功结果契约。"""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="Path of the file that was deleted")
    deleted: bool = Field(description="Whether the file was deleted")


Output = DeleteFileOutput  # v1 生成器入口别名（生成器只读 module.Output）


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

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        """执行文件删除。

        Args:
            call: 本次调用的值对象。
            services: 执行期服务表。

        Returns:
            ActionResult：
            - SUCCESS: data 包含 "file_path" 和 "deleted"
            - FAILURE: data 包含 "error"
        """
        tool_args = call.arguments
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
