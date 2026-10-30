# SPDX-License-Identifier: Apache-2.0
"""文件切片的真实后端，只依赖领域调用，不识别 Scheme 路径。"""

from pathlib import Path
from typing import Any

from ghrah.ability.access import AccessIdentity
from ghrah.ability.file_members import FILE_OPERATIONS
from ghrah.scheme_ability.execution.context import AbilityExecutionContext
from ghrah.scheme_ability.execution.invocation import AbilityInvocation
from ghrah.scheme_ability.permissions.fs_permissions import FSPermissionChecker
from ghrah.types.results import ActionOutcome, ActionResult


class FileBackend:
    """复用已加载的两个内置 Operation 和文件检查器，不复制文件算法。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        checker = FSPermissionChecker(allowed_paths=[str(self.root)], require_approval=False)
        self._operations = {
            name: member.operation(permission_checker=checker)
            for name, member in FILE_OPERATIONS.items()
        }

    async def execute(
        self, actor: AccessIdentity, member: str, arguments: dict[str, Any], invocation_id: str
    ) -> ActionResult:
        """执行已接纳的只读成员，文件范围在实际后端再次检查。"""
        arguments = dict(arguments)
        key = FILE_OPERATIONS[member].path_argument
        path = Path(arguments[key])
        arguments[key] = str((path if path.is_absolute() else self.root / path).resolve())
        context = AbilityExecutionContext(
            agent_name=actor.subject_id,
            invocation=AbilityInvocation(member, arguments, invocation_id),
        )
        result = await self._operations[member].execute(context)
        if member == "list_directory" and result.outcome is ActionOutcome.SUCCESS:
            # 旧非递归后端只返回 name；容器定义的 path 是相对目录的路径。
            result.data["entries"] = [
                {"path": item["name"], **item} for item in result.data["entries"]
            ]
        return result
