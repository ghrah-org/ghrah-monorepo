# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""MessageStore：运行时完整消息列表。

设计要点：
- 运行时内存中始终维护完整的当前消息列表，O(1) 查询
- 只负责内存消息集合；链节点的 delta 由 ContextManager 从本轮新增消息直接得出
- 持久化快照只出现在语义边界（root 与 compact），不再有周期性快照机制
"""

from __future__ import annotations

from typing import Any

__all__ = ["MessageStore"]


class MessageStore:
    """运行时消息存储 — 完整消息列表。

    该类是一个轻量容器：持有当前上下文的全部消息，供窗口管理、LLM
    注入与子 Agent 继承读取。链节点的 `messages_delta` 由调用方
    （`ContextManager.commit_iteration`）按本轮新增消息直接写入，本类
    不再维护「上次快照」基准，也不产生周期性快照。
    """

    def __init__(self) -> None:
        self._messages: list[Any] = []

    @property
    def current_messages(self) -> list[Any]:
        """返回当前完整消息列表的副本。"""
        return list(self._messages)

    @property
    def count(self) -> int:
        """当前消息数。"""
        return len(self._messages)

    def __len__(self) -> int:
        return len(self._messages)

    def append(self, message: Any) -> None:
        """追加单条消息。

        Args:
            message: 消息对象
        """
        self._messages.append(message)

    def extend(self, messages: list[Any]) -> None:
        """追加多条消息。

        Args:
            messages: 消息列表
        """
        self._messages.extend(messages)

    def replace_messages(self, messages: list[Any]) -> None:
        """替换当前完整消息列表。

        用于向后兼容场景（如 property setter 代理）。

        Args:
            messages: 新的消息列表（会被拷贝）
        """
        self._messages = list(messages)

    def clear(self) -> None:
        """清空所有消息。"""
        self._messages.clear()
