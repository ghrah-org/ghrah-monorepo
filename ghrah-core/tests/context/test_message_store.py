# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""MessageStore 单元测试。

MessageStore 现在只负责运行时完整消息列表；周期性快照机制已删除，
链节点的 delta 由 ContextManager 直接写入，因此这里只测试容器行为。
"""

from __future__ import annotations

from ghrah.context import MessageStore


class TestMessageStore:
    """MessageStore 消息存储测试。"""

    def test_initial_state(self) -> None:
        """初始状态：空消息。"""
        store = MessageStore()
        assert store.count == 0
        assert store.current_messages == []
        assert len(store) == 0

    def test_append_single(self) -> None:
        """追加单条消息。"""
        store = MessageStore()
        store.append("msg1")
        assert store.count == 1
        assert store.current_messages == ["msg1"]

    def test_extend_multiple(self) -> None:
        """追加多条消息。"""
        store = MessageStore()
        store.extend(["msg1", "msg2", "msg3"])
        assert store.count == 3
        assert store.current_messages == ["msg1", "msg2", "msg3"]

    def test_append_and_extend(self) -> None:
        """混合使用 append 和 extend。"""
        store = MessageStore()
        store.append("msg1")
        store.extend(["msg2", "msg3"])
        store.append("msg4")
        assert store.count == 4
        assert store.current_messages == ["msg1", "msg2", "msg3", "msg4"]

    def test_current_messages_returns_copy(self) -> None:
        """返回的是副本，修改不影响内部。"""
        store = MessageStore()
        store.append("msg1")

        messages = store.current_messages
        messages.append("hacked")

        assert store.count == 1
        assert store.current_messages == ["msg1"]

    def test_replace_messages(self) -> None:
        """替换完整消息列表。"""
        store = MessageStore()
        store.extend(["old1", "old2"])

        store.replace_messages(["new1"])

        assert store.current_messages == ["new1"]
        assert store.count == 1

    def test_clear(self) -> None:
        """清空全部消息。"""
        store = MessageStore()
        store.extend(["msg1", "msg2"])

        store.clear()

        assert store.count == 0
        assert store.current_messages == []
