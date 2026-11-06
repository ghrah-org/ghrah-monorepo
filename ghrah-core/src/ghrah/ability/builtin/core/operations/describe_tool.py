# SPDX-License-Identifier: Apache-2.0
"""描述已获授且已选择的工具，并按需加载定义。"""

from ghrah.ability.surface.meta import DescribeInput, DescribeOutput, MetaToolAbility

PERMISSIONS = {}
TITLE = "Describe Tool"
SUMMARY = (
    "Describe a granted tool's schema, effects and availability; "
    "load its definition for the next turn."
)
TAGS = ["core", "discovery"]
EFFECTS = []
TIMEOUT_MS = 60_000
Input = DescribeInput
Output = DescribeOutput


class DescribeToolAbility(MetaToolAbility):
    """描述不可用成员时保留契约事实，不把它变成可执行。"""

    tool_name = "describe_tool"
    summary = SUMMARY
    input_model = Input
