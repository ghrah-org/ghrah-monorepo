# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""MCP 标识的校验与纠错建议。

本模块**不生成**标识，只校验它们。工具不是被注册成独立函数，而是作为
``mcp_call`` 的调用参数传入，因此不存在「名字太长要截断」「撞名要加哈希」
「反解回原值」这一整类问题。剩下三件事：

1. **配置期**约束服务器名（它会作为调用参数出现在模型面前，必须无歧义）；
2. **调用期**原样保留工具名（它是发给服务器的线上名，改一个字符就连不上）；
3. **出错时**给出候选建议，否则模型只能盲试。

第 3 点的措辞很重要：建议只用于**提示**，绝不用于**自动纠正**。自动纠正会把
「服务器上确实存在的另一个工具」悄悄执行掉——那是一次静默的行为变更。
"""

from __future__ import annotations

import difflib
import re

from ghrah.mcp.errors import McpConfigError

__all__ = [
    "MAX_SERVER_NAME_LEN",
    "MAX_TOOL_DESCRIPTION_CHARS",
    "MAX_TOOL_NAME_LEN",
    "SERVER_NAME_RE",
    "format_unknown_tool_message",
    "suggest_tool_names",
    "truncate_description",
    "validate_server_name",
    "validate_tool_description",
    "validate_tool_name",
]

SERVER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
"""服务器名：小写字母开头，只含小写字母/数字/下划线。

与 ability handler 的 snake_case 规则一致，降低心智负担；同时也保证它能安全地
出现在给模型看的目录文本里，不需要转义。
"""

MAX_SERVER_NAME_LEN = 32
MAX_TOOL_NAME_LEN = 128
MAX_TOOL_DESCRIPTION_CHARS = 500
"""工具用途描述的长度上限。

与技能清单的摘要截断保持同一个数量级：目录每轮随上下文重发，单条描述过长会
在多技能会话里线性膨胀。
"""

_WHITESPACE_RE = re.compile(r"\s")


def validate_server_name(name: str) -> str:
    """校验服务器名，返回原值；非法时抛 :class:`McpConfigError`。"""
    if not isinstance(name, str):  # pragma: no cover - 类型层已拦，运行期兜底
        raise McpConfigError(f"server name must be a string, got {type(name).__name__}")
    if not name:
        raise McpConfigError("server name must not be empty")
    if len(name) > MAX_SERVER_NAME_LEN:
        raise McpConfigError(
            f"server name {name!r} is too long ({len(name)} > {MAX_SERVER_NAME_LEN})"
        )
    if not SERVER_NAME_RE.match(name):
        raise McpConfigError(
            f"server name {name!r} must match {SERVER_NAME_RE.pattern} "
            "(lowercase letters, digits and underscores, starting with a letter)"
        )
    return name


def validate_tool_name(name: str) -> str:
    """校验工具名，返回原值。

    规则刻意宽松：工具名由服务器定义，常见形式包含点号、连字符、大写字母。
    这里只拒绝会让文本通道产生歧义的字符（空白与控制字符），**不做任何
    大小写或分隔符归一化**。
    """
    if not isinstance(name, str):  # pragma: no cover
        raise McpConfigError(f"tool name must be a string, got {type(name).__name__}")
    if not name:
        raise McpConfigError("tool name must not be empty")
    if len(name) > MAX_TOOL_NAME_LEN:
        raise McpConfigError(f"tool name {name!r} is too long ({len(name)} > {MAX_TOOL_NAME_LEN})")
    if _WHITESPACE_RE.search(name):
        raise McpConfigError(f"tool name {name!r} must not contain whitespace")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
        raise McpConfigError(f"tool name {name!r} must not contain control characters")
    return name


def validate_tool_description(text: str) -> str:
    """校验工具用途描述非空，返回原值。"""
    if not isinstance(text, str):  # pragma: no cover
        raise McpConfigError(f"tool description must be a string, got {type(text).__name__}")
    if not text.strip():
        raise McpConfigError("tool description must not be empty")
    return text


def truncate_description(text: str, limit: int = MAX_TOOL_DESCRIPTION_CHARS) -> str:
    """把描述压到单行并截断到 ``limit`` 字符。

    换行会被折叠成单个空格：目录是逐行列举的，描述里带换行会把一条记录拆成
    多条，模型会把后半段当成另一个工具名。
    """
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    if limit <= 1:
        return flat[:limit]
    return flat[: limit - 1] + "…"


def _normalize_for_match(name: str) -> str:
    """生成用于**比较**的宽松形式（仅建议用，不用于调用）。"""
    return name.strip().lower().replace("-", "_").replace(".", "_")


def suggest_tool_names(
    requested: str,
    available: tuple[str, ...],
    *,
    limit: int = 3,
) -> tuple[str, ...]:
    """从 ``available`` 中挑出与 ``requested`` 最接近的若干工具名。

    两级匹配：先按归一化形式（忽略大小写、把 ``-`` 与 ``.`` 视同 ``_``）比对，
    再退回模糊匹配。归一化**只影响建议**——真正的调用始终用服务器上的原名。
    """
    if not available:
        return ()
    wanted = _normalize_for_match(requested)
    normalized = {_normalize_for_match(item): item for item in available}

    exact = normalized.get(wanted)
    if exact is not None:
        hits: list[str] = [exact]
    else:
        hits = []
        for key, original in normalized.items():
            if wanted and (wanted in key or key in wanted):
                hits.append(original)

    if len(hits) < limit:
        close = difflib.get_close_matches(wanted, list(normalized.keys()), n=limit, cutoff=0.6)
        for key in close:
            original = normalized[key]
            if original not in hits:
                hits.append(original)

    return tuple(hits[:limit])


def format_unknown_tool_message(server: str, tool: str, available: tuple[str, ...]) -> str:
    """构造给**模型看**的「工具不存在」提示。

    这是纠错通道，与异常消息面向不同读者：异常消息进日志，这条进对话。三个要素
    缺一不可——错在哪、有什么可选、是不是想写另一个。少了中间一项，模型只能
    凭记忆盲试。
    """
    lines = [f"Unknown tool {tool!r} on server {server!r}."]
    if available:
        lines.append(f"Available on {server!r}: " + ", ".join(available) + ".")
    suggestions = suggest_tool_names(tool, available)
    if suggestions:
        lines.append(f"Did you mean {suggestions[0]!r}?")
    return "\n".join(lines)
