# SPDX-License-Identifier: Apache-2.0
"""把成员移出模型上下文（仍可被搜索与调用）。"""

from ghrah.ability.surface.meta import BindInput, BindOutput, MetaToolAbility

PERMISSIONS = {}
TITLE = "Unbind Member"
SUMMARY = (
    "Remove a bound tool from the model context; it stays discoverable through "
    "search_tools and callable through execute_tool."
)
TAGS = ["core", "binding"]
EFFECTS = []
TIMEOUT_MS = 30_000
Input = BindInput
Output = BindOutput


class UnbindMemberAbility(MetaToolAbility):
    """解绑不撤销授权、不影响可调用性，只释放上下文里的 schema。"""

    tool_name = "unbind_member"
    summary = SUMMARY
    input_model = Input
