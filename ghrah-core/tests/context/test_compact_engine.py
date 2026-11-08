# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""链上 compact 引擎（ghrah.builtin）纯函数与专用提交路径测试。

覆盖：
- 窗口分割与消息重组边界（吸收不堆叠、保留窗不含 compact 节点展开）
- 折叠/摘要输入截断/快照拼接布局/提交前自检
- commit_compact_node 专用路径（回归：快照即压缩视图而非活 store
  旧全量；提交后 store 重建；分支切回后原始上下文完整回来）
"""

from __future__ import annotations

import pytest

from ghrah.chat.factory import ChatMessageFactory
from ghrah.chat.message import ChatMessage
from ghrah.context.compact import (
    COMPACT_DEGRADED_PREAMBLE,
    COMPACT_PREAMBLE_TEMPLATE,
    SUMMARY_MESSAGE_PREFIX,
    assemble_snapshot,
    collect_window_messages,
    fold_long_tool_outputs,
    post_check_snapshot,
    split_compact_windows,
    truncate_summary_input,
)
from ghrah.context.manager import ContextManager
from ghrah.context.node import ContextNode
from ghrah.context.window import estimate_tokens


def _make_cm(**overrides) -> ContextManager:
    """创建测试用 ContextManager（带 system_prompt）。"""
    defaults = {
        "agent_name": "test-agent",
        "system_prompt": "You are a test agent.",
        "message_factory": ChatMessageFactory(),
    }
    defaults.update(overrides)
    return ContextManager(**defaults)


def _commit_round(cm: ContextManager, label: str, messages: list[ChatMessage]) -> None:
    cm.begin_iteration()
    cm.add_messages(messages)
    cm.commit_iteration(ability_names=[label])


def _seed_rounds(cm: ContextManager, rounds: int = 4) -> None:
    """写入 rounds 轮普通节点。"""
    for i in range(rounds):
        _commit_round(cm, f"round-{i}", [ChatMessage.user(text_or_blocks=f"user-{i}")])


class TestSplitCompactWindows:
    """窗口分割边界。"""

    def test_returns_none_when_window_empty(self) -> None:
        """非 root 节点数 <= keep_recent 时窗外为空，返回 None（振荡防护）。"""
        cm = _make_cm()
        windows = split_compact_windows(cm.get_history(), keep_recent=2)

        assert windows is None

    def test_returns_none_exactly_keep_recent(self) -> None:
        """恰好 keep_recent 个非 root 节点时仍为空窗。"""
        cm = _make_cm()
        _seed_rounds(cm, rounds=2)
        windows = split_compact_windows(cm.get_history(), keep_recent=2)

        assert windows is None

    def test_splits_by_node_boundary(self) -> None:
        """普通分割：摘要窗 = 头部，保留窗 = 尾部 keep_recent 个。"""
        cm = _make_cm()
        _seed_rounds(cm, rounds=5)
        history = cm.get_history()

        summary_nodes, kept_nodes = split_compact_windows(history, keep_recent=2)

        assert [n.ability_names for n in summary_nodes] == [
            ["round-0"],
            ["round-1"],
            ["round-2"],
        ]
        assert [n.ability_names for n in kept_nodes] == [["round-3"], ["round-4"]]
        # root 恒不参与
        assert all(n.ability_names != ["init"] for n in summary_nodes + kept_nodes)


class TestCollectWindowMessages:
    """消息重组（吸收不堆叠）。"""

    def test_plain_nodes_collect_deltas_system_filtered(self) -> None:
        """普通节点 delta 全收；system 消息被过滤（由 session prompt 重建）。"""
        cm = _make_cm()
        _commit_round(cm, "r0", [ChatMessage.user(text_or_blocks="hello")])
        _commit_round(cm, "r1", [ChatMessage.user(text_or_blocks="world")])
        history = cm.get_history()

        summary_messages, kept_messages = collect_window_messages([history[1]], [history[2]])

        assert [m.text for m in summary_messages] == ["hello"]
        assert [m.text for m in kept_messages] == ["world"]

    def test_compact_node_summary_absorbed_into_summary_side(self) -> None:
        """链上 compact 节点：摘要消息归摘要侧，前置提示丢弃，不展开 snapshot。"""
        cm = _make_cm()
        _seed_rounds(cm, rounds=3)
        snapshot = [
            ChatMessage.system("You are a test agent.", source="system:config"),
            ChatMessage.system("Note: earlier history compacted.", source="system:runtime"),
            ChatMessage.system(
                SUMMARY_MESSAGE_PREFIX + "Earlier summary.", source="system:runtime"
            ),
            ChatMessage.user(text_or_blocks="kept-user"),
        ]
        compact_node = cm.commit_compact_node(snapshot, {"trigger_source": "threshold"})
        _seed_rounds(cm, rounds=3)

        summary_nodes, kept_nodes = split_compact_windows(cm.get_history(), keep_recent=2)
        assert compact_node in summary_nodes

        summary_messages, kept_messages = collect_window_messages(summary_nodes, kept_nodes)

        # 摘要侧吸收上一份摘要消息（唯一一条 system），前置提示被丢弃
        summary_texts = [m.text for m in summary_messages]
        assert SUMMARY_MESSAGE_PREFIX + "Earlier summary." in summary_texts
        assert all("Note: earlier history compacted." != t for t in summary_texts)
        # compact 节点的保留窗消息不进保留侧（snapshot 不展开）
        assert all(m.text != "kept-user" for m in kept_messages)

    def test_no_summary_stacking_on_second_compact(self) -> None:
        """二次压缩：摘要输入只含一份摘要消息（吸收不堆叠）。"""
        cm = _make_cm()
        _seed_rounds(cm, rounds=3)
        first_snapshot = [
            ChatMessage.system("sys", source="system:config"),
            ChatMessage.system("preamble", source="system:runtime"),
            ChatMessage.system(SUMMARY_MESSAGE_PREFIX + "first summary", source="system:runtime"),
            ChatMessage.user(text_or_blocks="kept-1"),
            ChatMessage.user(text_or_blocks="kept-2"),
        ]
        cm.commit_compact_node(first_snapshot, {"trigger_source": "threshold"})
        _seed_rounds(cm, rounds=3)

        summary_nodes, kept_nodes = split_compact_windows(cm.get_history(), keep_recent=2)
        summary_messages, _ = collect_window_messages(summary_nodes, kept_nodes)

        summary_msgs = [m for m in summary_messages if m.text.startswith(SUMMARY_MESSAGE_PREFIX)]
        assert len(summary_msgs) == 1
        assert summary_msgs[0].text == SUMMARY_MESSAGE_PREFIX + "first summary"


class TestFoldAndTruncate:
    """折叠与摘要输入截断。"""

    def test_fold_long_tool_outputs(self) -> None:
        """超长工具输出被截断加标记，短的不动。"""
        factory = ChatMessageFactory()
        long_msg = ChatMessage.tool(
            tool_call_id="c1", content="x" * 1000, name="search", source="agent:t"
        )
        short_msg = ChatMessage.user(text_or_blocks="hi")

        folded = fold_long_tool_outputs([long_msg, short_msg], 100, factory)

        assert len(folded) == 2
        assert len(folded[0].tool_results[0].content) < 1000
        assert "truncated" in folded[0].tool_results[0].content
        assert "1000 chars" in folded[0].tool_results[0].content
        assert folded[1] is short_msg

    def test_fold_without_factory_returns_originals(self) -> None:
        """factory 为 None 时原样返回（与策略行为一致）。"""
        long_msg = ChatMessage.tool(tool_call_id="c1", content="x" * 1000, name="search")
        folded = fold_long_tool_outputs([long_msg], 100, None)

        assert folded[0] is long_msg

    def test_truncate_summary_input_under_limit_noop(self) -> None:
        """预算内不截断。"""
        msgs = [ChatMessage.user(text_or_blocks="m")]
        kept, truncated = truncate_summary_input(msgs, 10000)

        assert kept == msgs
        assert truncated is False

    def test_truncate_summary_input_drops_oldest(self) -> None:
        """超 budget×2 渐进丢最旧并加标记。"""
        msgs = [ChatMessage.user(text_or_blocks=f"msg-{i} " * 50) for i in range(10)]
        kept, truncated = truncate_summary_input(msgs, 200)

        assert truncated is True
        assert len(kept) < 10
        assert kept[-1] is msgs[-1]  # 最新的保留


class TestAssembleAndPostCheck:
    """快照拼接与提交前自检。"""

    def test_assemble_layout(self) -> None:
        """布局：[system(config), preamble(system:runtime), summary(system:runtime), kept…]。"""
        factory = ChatMessageFactory()
        kept = [ChatMessage.user(text_or_blocks="k1"), ChatMessage.user(text_or_blocks="k2")]
        preamble = COMPACT_PREAMBLE_TEMPLATE.format(summarized_range="a..b")

        snapshot = assemble_snapshot(
            "You are a test agent.", preamble, "summary text", kept, factory
        )

        assert [m.role for m in snapshot] == ["system", "system", "system", "user", "user"]
        assert snapshot[0].source == "system:config"
        assert snapshot[0].text == "You are a test agent."
        assert snapshot[1].source == "system:runtime"
        assert snapshot[1].text == preamble
        assert snapshot[2].text == SUMMARY_MESSAGE_PREFIX + "summary text"
        assert snapshot[3:] == kept

    def test_assemble_empty_system_prompt_omitted(self) -> None:
        """system_prompt 为空时省略首条。"""
        factory = ChatMessageFactory()
        snapshot = assemble_snapshot("", "preamble", "s", [ChatMessage.user("k")], factory)

        assert snapshot[0].text == "preamble"

    def test_assemble_degraded_no_summary(self) -> None:
        """降级路径：summary_text=None 时无摘要消息，kept 直接跟前置提示。"""
        factory = ChatMessageFactory()
        folded = [ChatMessage.user(text_or_blocks="folded-1")]
        snapshot = assemble_snapshot("sys", COMPACT_DEGRADED_PREAMBLE, None, folded, factory)

        assert [m.role for m in snapshot] == ["system", "system", "user"]
        assert snapshot[1].text == COMPACT_DEGRADED_PREAMBLE

    def test_post_check_ok_within_budget(self) -> None:
        """预算内原样通过。"""
        factory = ChatMessageFactory()
        snapshot = assemble_snapshot("sys", "preamble", "s", [ChatMessage.user("k")], factory)

        result, status = post_check_snapshot(snapshot, 10000, factory)

        assert result == snapshot
        assert status == "ok"

    def test_post_check_truncates_summary_keeps_floor(self) -> None:
        """超预算时截断摘要文本；保留窗消息不动（地板）。"""
        factory = ChatMessageFactory()
        kept = [ChatMessage.user(text_or_blocks="kept-message")]
        big_summary = "s" * 4000
        snapshot = assemble_snapshot("sys", "preamble", big_summary, kept, factory)

        result, status = post_check_snapshot(snapshot, 500, factory)

        assert status == "ok"
        assert estimate_tokens(result) <= 500
        assert len(result) == len(snapshot)
        # 保留窗是地板：kept 消息原样保留
        assert result[-1].text == "kept-message"
        # 摘要被截断
        assert len(result[2].text) < len(big_summary)

    def test_post_check_over_budget_pathological(self) -> None:
        """病理配置（保留窗本身超预算）：over_budget 提交不缩减保留窗。"""
        factory = ChatMessageFactory()
        kept = [ChatMessage.user(text_or_blocks="k" * 5000) for _ in range(3)]
        snapshot = assemble_snapshot("sys", "preamble", "s", kept, factory)

        result, status = post_check_snapshot(snapshot, 100, factory)

        assert status == "over_budget"
        assert [m.text for m in result[3:]] == [m.text for m in kept]

    def test_post_check_uses_injected_estimator(self) -> None:
        """注入估算器：estimate 与 chars 截断上界均走注入口径。"""

        class CharCountEstimator:
            """len(text) 即 token、chars=tokens 的测试口径。"""

            def __init__(self) -> None:
                self.chars_budget_calls = 0

            def estimate_message(self, message: Any) -> int:
                return max(1, len(getattr(message, "text", "") or ""))

            def estimate_messages(self, messages: list[Any]) -> int:
                return sum(self.estimate_message(m) for m in messages)

            def chars_budget_for(self, tokens: int) -> int:
                self.chars_budget_calls += 1
                return max(tokens, 0)

        from typing import Any

        factory = ChatMessageFactory()
        kept = [ChatMessage.user(text_or_blocks="kept-message")]  # 12 tokens
        big_summary = "s" * 4000
        snapshot = assemble_snapshot("sys", "preamble", big_summary, kept, factory)

        estimator = CharCountEstimator()
        result, status = post_check_snapshot(snapshot, 200, factory, estimator)

        # 摘要被截到注入口径的预算余量内；chars_budget_for 真实被调用
        assert status == "ok"
        assert estimator.chars_budget_calls >= 1
        assert len(result[2].text) < len(big_summary)
        assert estimator.estimate_messages(result) <= 200

    def test_truncate_summary_input_uses_injected_estimator(self) -> None:
        """注入估算器：截断门按注入口径判定。"""

        class ConstantEstimator:
            """恒返回 5 的测试估算器。"""

            def estimate_message(self, message: Any) -> int:
                return 5

            def estimate_messages(self, messages: list[Any]) -> int:
                return 5 * len(messages)

            def chars_budget_for(self, tokens: int) -> int:
                return max(tokens, 0)

        from typing import Any

        msgs = [ChatMessage.user(text_or_blocks=f"m{i}") for i in range(10)]

        # 注入口径 5×10=50 ≤ 200（budget=100，limit=200）→ 不截断
        kept, truncated = truncate_summary_input(msgs, 100, ConstantEstimator())
        assert kept == msgs
        assert truncated is False

        # 注入口径 5×10=50 > 8（budget=4，limit=8）→ 截到 1 条
        kept2, truncated2 = truncate_summary_input(msgs, 4, ConstantEstimator())
        assert truncated2 is True
        assert len(kept2) == 1
        assert kept2[0] is msgs[-1]


class TestCommitCompactNode:
    """专用提交路径。"""

    def test_snapshot_is_compressed_view_not_live_store(self) -> None:
        """compact 节点快照 == 压缩视图 ≠ 活 store 旧全量（核心回归）。"""
        cm = _make_cm()
        _seed_rounds(cm, rounds=4)

        compressed_view = [
            ChatMessage.system("You are a test agent.", source="system:config"),
            ChatMessage.system("preamble", source="system:runtime"),
            ChatMessage.system(SUMMARY_MESSAGE_PREFIX + "s", source="system:runtime"),
        ]
        node = cm.commit_compact_node(compressed_view, {"trigger_source": "threshold"})

        assert node.is_snapshot is True
        assert node.messages_delta == []
        assert node.ability_names == ["compact"]
        assert node.metadata["node_kind"] == "compact"
        assert node.metadata["trigger_source"] == "threshold"
        assert [m.text for m in node.messages_snapshot] == [m.text for m in compressed_view]
        # 提交后活 store 即压缩视图（短 store）
        assert [m.text for m in cm.message_store.current_messages] == [
            m.text for m in compressed_view
        ]
        assert cm.message_store.count == 3  # 而非种子 4 轮的旧全量

    def test_store_rebuilt_after_commit(self) -> None:
        """提交后 get_messages() == 快照（回放与活 store 一致）。"""
        cm = _make_cm()
        _seed_rounds(cm, rounds=3)
        view = [
            ChatMessage.system("You are a test agent.", source="system:config"),
            ChatMessage.system(SUMMARY_MESSAGE_PREFIX + "s", source="system:runtime"),
        ]
        cm.commit_compact_node(view, {"trigger_source": "threshold"})

        assert [m.text for m in cm.get_messages()] == [m.text for m in view]

    def test_anchor_invalidated_after_compact(self) -> None:
        """提交后锚点失效，后续 commit 正常推进。"""
        cm = _make_cm()
        _seed_rounds(cm, rounds=3)
        cm.record_window_occupied(999)
        view = [ChatMessage.system("You are a test agent.", source="system:config")]
        node = cm.commit_compact_node(view, {"trigger_source": "threshold"})

        assert cm._window_occupied is None
        # compact 节点之后照常 commit，iteration 自然推进
        _commit_round(cm, "after-1", [ChatMessage.user("a1")])
        history = cm.get_history()
        assert history[-1].ability_names == ["after-1"]
        assert node.iteration == history[-2].iteration + 1 or node is history[-2]

    def test_rejects_during_iteration(self) -> None:
        """迭代进行中拒绝 compact 提交。"""
        cm = _make_cm()
        cm.begin_iteration()

        with pytest.raises(RuntimeError, match="during an iteration"):
            cm.commit_compact_node([ChatMessage.system("s")], None)

    def test_branch_switch_back_restores_original_context(self) -> None:
        """分支切回 compact 节点之前 → 原始上下文完整回来（回滚可逆）。"""
        cm = _make_cm()
        session_id = cm.active_session_id
        main_branch_id = cm.get_active_session().active_branch_id
        _seed_rounds(cm, rounds=4)
        pre_compact_head = cm.active_head

        view = [
            ChatMessage.system("You are a test agent.", source="system:config"),
            ChatMessage.system(SUMMARY_MESSAGE_PREFIX + "s", source="system:runtime"),
        ]
        cm.commit_compact_node(view, {"trigger_source": "threshold"})

        # compact 后视图变短
        assert cm.message_store.count == 2

        branch = cm.create_branch(
            session_id=session_id,
            name="pre-compact",
            from_node_id=pre_compact_head.id,
            parent_branch_id=main_branch_id,
        )
        cm.activate_branch(session_id, branch.branch_id)

        # 原始上下文完整回来（system + 4 轮 user）
        texts = [m.text for m in cm.message_store.current_messages]
        assert texts == ["You are a test agent.", "user-0", "user-1", "user-2", "user-3"]

    def test_auto_persist_schedules_node(self) -> None:
        """auto_persist 开启时 compact 节点走后台持久化。"""
        import asyncio

        from ghrah.context.persistence import InMemoryBackend

        backend = InMemoryBackend()

        async def _run() -> ContextNode:
            cm = _make_cm(persistence=backend, auto_persist=True, compact_threshold=0.8)
            _seed_rounds(cm, rounds=3)
            node = cm.commit_compact_node(
                [ChatMessage.system("You are a test agent.", source="system:config")],
                {"trigger_source": "threshold"},
            )
            await cm.wait_for_persist()
            return node

        node = asyncio.run(_run())
        assert node.metadata["node_kind"] == "compact"
        checkpoint = asyncio.run(backend.load_checkpoint("test-agent"))
        assert checkpoint is not None
        persisted = next((n for n in checkpoint.nodes if n.id == node.id), None)
        assert persisted is not None
        assert persisted.metadata["node_kind"] == "compact"
