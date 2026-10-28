# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""recall_context 能力测试：list/messages 双模式、过滤、边界强制与注册面。

覆盖：
- compact 后原始消息可召回（链上 commit 若干轮 → commit_compact_node →
  recall 按 summarized_range 命中原文——召回闭环主用例）
- 全部过滤器（node_id/范围含端点/keyword 大小写/role/since_iteration/limit 取尾）
- max_output_tokens 截断 + truncated 标记
- 查询边界：兄弟分支界外错误、未知 node_id、context_manager 未接线、非法参数
- 双面（定义面扫盘 + builtin manifest + resolver）
"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.chat.factory import ChatMessageFactory
from ghrah.context.manager import ContextManager
from ghrah.scheme.ability.core.operations.recall_context import (
    RecallContextAbility,
    RecallContextInput,
)
from ghrah.scheme.execution.context import AbilityExecutionContext
from ghrah.types.results import ActionOutcome

# ── 辅助 ──

_factory = ChatMessageFactory()


def _make_cm() -> ContextManager:
    return ContextManager(
        agent_name="recaller",
        system_prompt="You are a test agent.",
        message_factory=_factory,
    )


def _seed_round(cm: ContextManager, user_text: str, ai_text: str) -> None:
    """提交一轮普通迭代（user + ai 两条 delta）。"""
    cm.begin_iteration()
    cm.add_messages(
        [
            _factory.create_message("user", text=user_text),
            _factory.create_message("ai", text=ai_text),
        ]
    )
    cm.commit_iteration(ability_names=["conversation"], action_results=[])


def _make_context(cm: ContextManager | None, tool_args: dict[str, Any]) -> AbilityExecutionContext:
    return AbilityExecutionContext(
        context_manager=cm,
        tool_args=tool_args,
        agent_name="recaller",
    )


async def _run(cm: ContextManager | None, **tool_args: Any) -> dict[str, Any]:
    result = await RecallContextAbility().execute(_make_context(cm, tool_args))
    assert result.outcome == ActionOutcome.SUCCESS, result.data
    return result.data


async def _run_fail(cm: ContextManager | None, **tool_args: Any) -> str:
    result = await RecallContextAbility().execute(_make_context(cm, tool_args))
    assert result.outcome == ActionOutcome.FAILURE
    assert isinstance(result.data, dict)
    return str(result.data.get("error", ""))


def _commit_compact(cm: ContextManager) -> Any:
    """提交一个最小 compact 节点（压缩视图 = 简单摘要消息）。"""
    snapshot = [
        _factory.create_message("system", text="You are a test agent."),
        _factory.create_message(
            "system", text="[Context Summary] earlier rounds summarized", source="system:runtime"
        ),
    ]
    return cm.commit_compact_node(
        snapshot_messages=snapshot,
        metadata={
            "trigger_source": "threshold",
            "method": "ghrah.builtin",
            "summarized_range": ["n0", "n1"],
            "kept_node_ids": [],
            "kept_recent_nodes": 2,
        },
    )


# ── list 模式 ──


class TestListMode:
    async def test_list_shape_and_order(self) -> None:
        cm = _make_cm()
        _seed_round(cm, "hello world", "hi there")

        data = await _run(cm, mode="list")
        nodes = data["nodes"]

        assert data["count"] == len(nodes) == 2  # root + 1 轮
        assert [n["node_kind"] for n in nodes] == ["root", "plain"]
        assert nodes[0]["iteration"] == 0
        assert nodes[1]["iteration"] == 1
        assert nodes[1]["ability_names"] == ["conversation"]
        assert all(n["branch_id"] for n in nodes)
        assert data["truncated"] is False

    async def test_list_compact_node_carries_compaction_metadata(self) -> None:
        cm = _make_cm()
        _seed_round(cm, "q1", "a1")
        _commit_compact(cm)

        data = await _run(cm, mode="list")
        compact_entries = [n for n in data["nodes"] if n["node_kind"] == "compact"]

        assert len(compact_entries) == 1
        entry = compact_entries[0]
        assert entry["summarized_range"] == ["n0", "n1"]
        assert entry["trigger_source"] == "threshold"

    async def test_list_keyword_filters_to_matching_nodes(self) -> None:
        cm = _make_cm()
        _seed_round(cm, "alpha secret", "a1")
        _seed_round(cm, "beta public", "a2")

        data = await _run(cm, mode="list", keyword="SECRET")
        assert data["count"] == 1
        assert data["nodes"][0]["iteration"] == 1

    async def test_list_role_filter(self) -> None:
        cm = _make_cm()
        _seed_round(cm, "u1", "a1")

        data = await _run(cm, mode="list", role="tool")
        assert data["count"] == 0

    async def test_list_limit_takes_most_recent(self) -> None:
        cm = _make_cm()
        for i in range(5):
            _seed_round(cm, f"q{i}", f"a{i}")

        data = await _run(cm, mode="list", limit=3)
        nodes = data["nodes"]
        assert data["count"] == 3
        assert [n["iteration"] for n in nodes] == [3, 4, 5]
        assert data["truncated"] is True

    async def test_list_since_iteration(self) -> None:
        cm = _make_cm()
        for i in range(3):
            _seed_round(cm, f"q{i}", f"a{i}")

        data = await _run(cm, mode="list", since_iteration=2)
        assert [n["iteration"] for n in data["nodes"]] == [2, 3]


# ── messages 模式 ──


class TestMessagesMode:
    async def test_messages_shape_root_snapshot_and_plain_delta(self) -> None:
        cm = _make_cm()
        _seed_round(cm, "hello world", "hi there")

        data = await _run(cm, mode="messages")
        nodes = data["nodes"]

        assert [n["node_kind"] for n in nodes] == ["root", "plain"]
        root_msgs = nodes[0]["messages"]
        assert any(m["role"] == "system" and "test agent" in m["text"] for m in root_msgs)

        plain_msgs = nodes[1]["messages"]
        assert [m["role"] for m in plain_msgs] == ["user", "ai"]
        assert plain_msgs[0]["text"] == "hello world"

    async def test_recall_after_compact_recovers_original(self) -> None:
        """召回闭环主用例：compact 提交后早期原文仍可按节点召回。"""
        cm = _make_cm()
        _seed_round(cm, "the launch code is OMEGA-7", "noted")
        early_node_id = cm.get_history()[-1].id
        _commit_compact(cm)

        # 活跃 store 已是压缩视图，原文不在 get_messages 中
        active_text = " ".join(str(getattr(m, "text", "")) for m in cm.get_messages())
        assert "OMEGA-7" not in active_text

        data = await _run(cm, mode="messages", node_id=early_node_id)
        node = data["nodes"][0]
        assert node["node_id"] == early_node_id
        assert node["node_kind"] == "plain"
        assert any("OMEGA-7" in m["text"] for m in node["messages"])

    async def test_messages_compact_node_returns_compressed_view(self) -> None:
        cm = _make_cm()
        _commit_compact(cm)

        data = await _run(cm, mode="messages", role=None)
        compact_nodes = [n for n in data["nodes"] if n["node_kind"] == "compact"]
        assert len(compact_nodes) == 1
        texts = [m["text"] for m in compact_nodes[0]["messages"]]
        assert any("[Context Summary]" in t for t in texts)

    async def test_messages_keyword_and_role_filters(self) -> None:
        cm = _make_cm()
        _seed_round(cm, "find the needle", "answer about haystack")

        data = await _run(cm, mode="messages", keyword="NEEDLE", role="user")
        assert data["count"] == 1
        msgs = data["nodes"][0]["messages"]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "needle" in msgs[0]["text"]

    async def test_messages_max_output_tokens_truncates(self) -> None:
        cm = _make_cm()
        long_text = "x" * 4000  # ~1000 tokens
        _seed_round(cm, long_text, long_text)

        data = await _run(cm, mode="messages", max_output_tokens=100)
        assert data["truncated"] is True
        total_chars = sum(len(m["text"]) for n in data["nodes"] for m in n["messages"])
        # 截断后输出远小于全量（全量 ~8000 chars）
        assert total_chars < 2000

    async def test_messages_node_id_range_inclusive(self) -> None:
        cm = _make_cm()
        for i in range(4):
            _seed_round(cm, f"q{i}", f"a{i}")
        history = cm.get_history()
        # root + 4 轮 = 5 节点；范围取第 2~4 个（含端点）
        from_node, to_node = history[1], history[3]

        data = await _run(cm, mode="messages", node_id_range=[from_node.id, to_node.id])
        assert [n["node_id"] for n in data["nodes"]] == [
            from_node.id,
            history[2].id,
            to_node.id,
        ]

    async def test_messages_range_reverse_order_normalized(self) -> None:
        cm = _make_cm()
        for i in range(3):
            _seed_round(cm, f"q{i}", f"a{i}")
        history = cm.get_history()

        data = await _run(cm, mode="messages", node_id_range=[history[3].id, history[1].id])
        assert [n["iteration"] for n in data["nodes"]] == [1, 2, 3]

    async def test_messages_root_uses_snapshot_not_delta(self) -> None:
        cm = _make_cm()
        _seed_round(cm, "q", "a")
        root = cm.get_history()[0]
        assert root.messages_delta == []  # 前置：root 无 delta

        data = await _run(cm, mode="messages", node_id=root.id)
        assert data["count"] == 1
        assert len(data["nodes"][0]["messages"]) > 0


# ── 边界强制 ──


class TestBoundary:
    async def test_sibling_branch_node_rejected_with_boundary_error(self) -> None:
        cm = _make_cm()
        session_id = cm.active_session_id
        main_branch_id = cm.get_active_session().active_branch_id
        _seed_round(cm, "q1", "a1")
        branch = cm.create_branch(session_id=session_id, name="alt", from_node_id=cm.active_head.id)
        cm.activate_branch(session_id, branch.branch_id)
        _seed_round(cm, "on branch", "b1")
        sibling_node_id = cm.get_history()[-1].id
        cm.activate_branch(session_id, main_branch_id)

        error = await _run_fail(cm, mode="messages", node_id=sibling_node_id)
        assert "outside the current branch" in error

    async def test_unknown_node_id_not_found(self) -> None:
        cm = _make_cm()
        _seed_round(cm, "q", "a")

        error = await _run_fail(cm, mode="messages", node_id="nonexistent00")
        assert "not found" in error

    async def test_range_endpoint_outside_boundary_rejected(self) -> None:
        cm = _make_cm()
        session_id = cm.active_session_id
        main_branch_id = cm.get_active_session().active_branch_id
        _seed_round(cm, "q1", "a1")
        branch = cm.create_branch(session_id=session_id, name="alt", from_node_id=cm.active_head.id)
        cm.activate_branch(session_id, branch.branch_id)
        _seed_round(cm, "on branch", "b1")
        sibling_node_id = cm.get_history()[-1].id
        cm.activate_branch(session_id, main_branch_id)
        main_last = cm.active_head.id

        error = await _run_fail(cm, mode="messages", node_id_range=[main_last, sibling_node_id])
        assert "outside the current branch" in error

    async def test_fork_ancestors_recallable_from_branch(self) -> None:
        cm = _make_cm()
        _seed_round(cm, "ancestor round", "a1")
        ancestor_node_id = cm.get_history()[-1].id
        branch = cm.create_branch(
            session_id=cm.active_session_id, name="child", from_node_id=cm.active_head.id
        )
        cm.activate_branch(cm.active_session_id, branch.branch_id)
        _seed_round(cm, "branch round", "b1")

        data = await _run(cm, mode="messages", node_id=ancestor_node_id)
        assert data["count"] == 1
        assert any("ancestor round" in m["text"] for m in data["nodes"][0]["messages"])

    async def test_context_manager_not_wired(self) -> None:
        error = await _run_fail(None, mode="list")
        assert "not wired" in error

    async def test_history_read_failure(self) -> None:
        class _BrokenCM:
            def get_history(self) -> list[Any]:
                raise RuntimeError("boom")

        result = await RecallContextAbility().execute(
            _make_context(_BrokenCM(), {"mode": "list"})  # type: ignore[arg-type]
        )
        assert result.outcome == ActionOutcome.FAILURE
        assert "Failed to read" in str(result.data.get("error"))

    async def test_invalid_args(self) -> None:
        cm = _make_cm()
        # 坏 mode
        error = await _run_fail(cm, mode="bad")
        assert "invalid recall_context args" in error
        # 范围长度错
        error = await _run_fail(cm, node_id_range=["a", "b", "c"])
        assert "exactly 2" in error
        # node_id 与 range 同给
        error = await _run_fail(cm, node_id="x", node_id_range=["a", "b"])
        assert "mutually exclusive" in error

    async def test_unknown_params_ignored(self) -> None:
        """未知参数按 send 先例过滤忽略（不报错、不参与查询）。"""
        cm = _make_cm()
        _seed_round(cm, "q", "a")

        data = await _run(cm, unknown_param=1)
        assert data["count"] == 2


# ── 注册面 ──


class TestRegistration:
    def test_registry_has_recall_context(self) -> None:
        from ghrah.scheme.loading.ability_catalog import has_handler

        assert has_handler("recall_context")

    def test_builtin_manifest_loads(self) -> None:
        from ghrah.scheme.loading.ability_catalog import load_builtin_manifest

        manifest = load_builtin_manifest("ghrah.core.recall_context")
        assert manifest.implementation.type == "builtin"
        assert manifest.implementation.handler == "recall_context"
        assert manifest.metadata.permissions.require_hitl is False

    def test_resolver_from_builtin(self) -> None:
        from ghrah.scheme.agent_resolver import ResolvedAbility

        resolved = ResolvedAbility.from_builtin("recall_context")
        assert resolved.ability_name == "ghrah.core.recall_context"

    def test_manifest_tool_parameters_match_input_model(self) -> None:
        from ghrah.scheme.loading.ability_catalog import load_builtin_manifest

        manifest = load_builtin_manifest("ghrah.core.recall_context")
        assert manifest.tool is not None
        params = manifest.tool.parameters
        assert set(params) == set(RecallContextInput.model_fields)


# ── 输入模型直测 ──


class TestRecallContextInput:
    def test_defaults(self) -> None:
        args = RecallContextInput()
        assert args.mode == "list"
        assert args.limit == 20
        assert args.max_output_tokens == 2000

    def test_bad_role_rejected(self) -> None:
        with pytest.raises(ValueError):
            RecallContextInput(role="assistant")

    def test_negative_since_iteration_rejected(self) -> None:
        with pytest.raises(ValueError):
            RecallContextInput(since_iteration=-1)


# ── tool 描述面 ──


class TestToolSurface:
    def test_bind_tool_schema(self) -> None:
        tool = RecallContextAbility().bind_tool()
        assert tool["function"]["name"] == "recall_context"
        props: dict[str, Any] = tool["function"]["parameters"]["properties"]
        assert "node_id_range" in props
        assert "max_output_tokens" in props

    def test_to_prompt_description(self) -> None:
        desc = RecallContextAbility().to_prompt_description()
        assert "recall_context" in desc
