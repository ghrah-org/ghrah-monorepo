# SPDX-License-Identifier: Apache-2.0
"""以已绑定名字路由到目标成员自己的执行入口。"""

from ghrah.ability.surface.meta import ExecuteInput, ExecuteOutput, MetaToolAbility

PERMISSIONS = {}
TITLE = "Execute Tool"
SUMMARY = (
    "Execute a granted tool by exact name and arguments. "
    "Inspect its schema with describe_tool first."
)
TAGS = ["core", "execution"]
EFFECTS = []
TIMEOUT_MS = 60_000
Input = ExecuteInput
Output = ExecuteOutput


class ExecuteToolAbility(MetaToolAbility):
    """包装调用不会扩大目标授权，也不会代替目标 Hook 与 HITL。"""

    tool_name = "execute_tool"
    summary = SUMMARY
    input_model = Input
