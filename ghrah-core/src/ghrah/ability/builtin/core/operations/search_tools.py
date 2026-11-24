# SPDX-License-Identifier: Apache-2.0
"""扫描当前 Agent 实例的可见未绑定成员。"""

from ghrah.ability.surface.meta import MetaToolAbility, SearchInput, SearchOutput

PERMISSIONS = {}
TITLE = "Search Tools"
SUMMARY = (
    "List granted tools (operations and MCP members) that are visible but not bound in "
    "this Agent instance (axes.visible=true, axes.bind=false). Call bind_member to keep "
    "one in context, or execute_tool to call it directly."
)
TAGS = ["core", "discovery"]
EFFECTS = []
TIMEOUT_MS = 60_000
Input = SearchInput
Output = SearchOutput


class SearchToolsAbility(MetaToolAbility):
    """搜索不加载完整定义，也不授予任何成员。"""

    tool_name = "search_tools"
    summary = SUMMARY
    input_model = Input
