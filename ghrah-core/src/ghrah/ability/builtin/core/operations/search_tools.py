# SPDX-License-Identifier: Apache-2.0
"""只搜索当前 Agent 的授权目录。"""

from ghrah.ability.surface.meta import MetaToolAbility, SearchInput, SearchOutput

PERMISSIONS = {}
TITLE = "Search Tools"
SUMMARY = (
    "Search granted tools when the visible tools are insufficient. Use English keywords if needed."
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
