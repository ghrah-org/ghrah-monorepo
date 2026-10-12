# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ContextManager 门面类集成测试。

测试 ContextManager 整合 ActionChain + StateManager + MessageStore 的完整功能，
覆盖迭代生命周期、状态管理、消息管理、快照机制、fork 继承等场景。
"""

from __future__ import annotations

import pytest
from _helpers import make_action_result

from ghrah.abilities.base import ActionOutcome, ActionResult
from ghrah.abilities.context import AbilityExecutionContext
from ghrah.chat.factory import ChatMessageFactory
from ghrah.chat.message import ChatMessage
from ghrah.context.manager import ContextManager
from ghrah.context.node import ContextNode
from ghrah.context.token_estimator import WeightedTokenEstimator
from ghrah.core.config import AgentConfig
from ghrah.core.message import AgentMessage as Message

# ----------------------------------------------------------------
# 辅助工厂
# ----------------------------------------------------------------


def _make_cm(**overrides) -> ContextManager:
    """创建测试用 ContextManager。"""
    defaults = {
        "agent_name": "test-agent",
        "initial_state": None,
        "snapshot_interval": 5,
        "system_prompt": "",
        "message_factory": ChatMessageFactory(),
    }
    defaults.update(overrides)
    return ContextManager(**defaults)


def _make_window_manager(max_tokens: int = 1000):
    """创建无策略 WindowManager（仅提供预算权威）。"""
    from ghrah.context.window import WindowManager

    return WindowManager(strategies=[], max_tokens=max_tokens)


def _make_message(content: str = "hello") -> Message:
    """创建测试用 Message。"""
    return Message(sender="user", recipient="test-agent", content=content)


# ----------------------------------------------------------------
# TestContextManagerLifecycle — 初始化和属性
# ----------------------------------------------------------------


class TestContextManagerLifecycle:
    """ContextManager 初始化和基本属性测试。"""

    def test_init_creates_root_node(self) -> None:
        """初始化后 chain 有根节点。"""
        cm = _make_cm()
        assert cm.active_head.ability_names == ["init"]
        assert cm.active_head.iteration == 0
        assert cm.active_head.is_snapshot is True

    def test_init_with_initial_state(self) -> None:
        """初始状态正确传入 StateManager 和 root node。"""
        state = {"key": "value", "nested": {"a": 1}}
        cm = _make_cm(initial_state=state)
        assert cm.get_current_state() == state
        assert cm.active_head.agent_state == state

    def test_init_with_system_prompt(self) -> None:
        """system_prompt 正确保存。"""
        cm = _make_cm(system_prompt="You are a helpful assistant.")
        assert cm._system_prompt == "You are a helpful assistant."

    def test_agent_name_property(self) -> None:
        """agent_name 属性正确。"""
        cm = _make_cm(agent_name="my-agent")
        assert cm.agent_name == "my-agent"

    def test_in_iteration_initially_false(self) -> None:
        """初始不在迭代中。"""
        cm = _make_cm()
        assert cm.in_iteration is False

    def test_chain_has_one_node_after_init(self) -> None:
        """初始化后 chain 只有根节点。"""
        cm = _make_cm()
        assert cm.chain.node_count == 1


# ----------------------------------------------------------------
# TestContextManagerIteration — 迭代生命周期
# ----------------------------------------------------------------


class TestContextManagerIteration:
    """ContextManager 迭代生命周期测试。"""

    def test_begin_iteration(self) -> None:
        """开启迭代，in_iteration=True。"""
        cm = _make_cm()
        cm.begin_iteration()
        assert cm.in_iteration is True

    def test_begin_iteration_twice_raises(self) -> None:
        """重复开启抛 RuntimeError。"""
        cm = _make_cm()
        cm.begin_iteration()
        with pytest.raises(RuntimeError, match="Iteration already in progress"):
            cm.begin_iteration()

    def test_commit_without_begin_raises(self) -> None:
        """未 begin 就 commit 抛 RuntimeError。"""
        cm = _make_cm()
        with pytest.raises(RuntimeError, match="No iteration in progress"):
            cm.commit_iteration(ability_names=["test"])

    def test_rollback_without_begin_raises(self) -> None:
        """未 begin 就 rollback 抛 RuntimeError。"""
        cm = _make_cm()
        with pytest.raises(RuntimeError, match="No iteration in progress"):
            cm.rollback_iteration(ValueError("test"))

    def test_commit_iteration_basic(self) -> None:
        """基本 commit：创建节点，消息写入 store。"""
        cm = _make_cm()
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="hello")])
        result = make_action_result()
        node = cm.commit_iteration(
            ability_names=["conversation"],
            action_results=[{"ability_name": "conversation", "action_result": result}],
        )

        assert node.ability_names == ["conversation"]
        assert node.iteration == 1
        assert node.action_results == [{"ability_name": "conversation", "action_result": result}]
        assert not cm.in_iteration
        assert cm.message_store.count == 1

    def test_commit_iteration_with_state_changes(self) -> None:
        """commit 时传入 state_changes。"""
        cm = _make_cm(initial_state={"count": 0})
        cm.begin_iteration()
        node = cm.commit_iteration(
            ability_names=["increment"],
            state_changes={"count": 1},
        )

        assert node.agent_state == {"count": 1}
        assert cm.get_current_state() == {"count": 1}

    def test_commit_iteration_increments(self) -> None:
        """多次 commit，iteration 递增。"""
        cm = _make_cm()
        for i in range(3):
            cm.begin_iteration()
            node = cm.commit_iteration(ability_names=[f"ability_{i}"])

        assert node.iteration == 3
        assert cm.chain.node_count == 4

    def test_commit_iteration_auto_snapshot(self) -> None:
        """snapshot_interval=5 时，第 5 轮自动创建 snapshot。"""
        cm = _make_cm(snapshot_interval=5)
        for i in range(5):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"msg_{i}")])
            node = cm.commit_iteration(ability_names=[f"ability_{i}"])

        assert node.is_snapshot is True
        assert node.messages_snapshot is not None

    def test_commit_iteration_delta_calculation(self) -> None:
        """非 snapshot 轮只存 delta。"""
        cm = _make_cm(snapshot_interval=10)
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="msg_1")])
        node = cm.commit_iteration(ability_names=["ability_1"])

        assert node.is_snapshot is False
        assert node.messages_snapshot is None
        assert len(node.messages_delta) == 1

    def test_rollback_iteration(self) -> None:
        """rollback 创建新 branch（从回滚目标节点 fork）。"""
        cm = _make_cm()
        # 先 commit 一次，确保有父节点可以回滚
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="first")])
        cm.commit_iteration(ability_names=["step1"])

        # 再开始一次迭代，然后回滚
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="will be discarded")])
        previous_head = cm.active_head
        session_id = cm.active_session_id
        branch = cm.rollback_iteration(ValueError("something went wrong"))

        assert branch.metadata.get("is_rollback") is True
        assert "something went wrong" in branch.metadata.get("error", "")
        assert branch.name.startswith("retry-")
        assert branch.session_id == session_id
        assert branch.head_node_id == previous_head.id

    def test_rollback_preserves_state(self) -> None:
        """rollback 后状态恢复到迭代前。"""
        cm = _make_cm(initial_state={"key": "original"})
        # 先 commit 一次
        cm.begin_iteration()
        cm.commit_iteration(ability_names=["step1"])

        # 再尝试修改并回滚
        cm.begin_iteration()
        cm.apply_state_changes({"key": "modified"})
        cm.rollback_iteration(RuntimeError("fail"))

        assert cm.get_current_state() == {"key": "original"}

    def test_rollback_discards_messages(self) -> None:
        """rollback 后 pending_messages 不写入 store。"""
        cm = _make_cm()
        # 先 commit 一次
        cm.begin_iteration()
        cm.commit_iteration(ability_names=["step1"])

        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="discarded")])
        cm.rollback_iteration(RuntimeError("fail"))

        assert cm.message_store.count == 0

    def test_rollback_branch_metadata(self) -> None:
        """回滚 Branch metadata 含 is_rollback=True 和 error 信息。"""
        cm = _make_cm()
        # 先 commit 一次
        cm.begin_iteration()
        cm.commit_iteration(ability_names=["step1"])

        cm.begin_iteration()
        branch = cm.rollback_iteration(TypeError("bad type"))

        assert branch.metadata["is_rollback"] is True
        assert "bad type" in branch.metadata["error"]
        assert "rollback_from_branch_id" in branch.metadata
        assert "rollback_from_node_id" in branch.metadata

    def test_add_messages_during_iteration(self) -> None:
        """迭代中添加消息。"""
        cm = _make_cm()
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="msg1")])
        cm.add_messages([ChatMessage.ai(text="msg2")])
        assert len(cm._pending_messages) == 2

        cm.commit_iteration(ability_names=["test"])
        assert cm.message_store.count == 2

    def test_add_messages_without_iteration(self) -> None:
        """非迭代中添加消息直接写入 MessageStore。"""
        cm = _make_cm()
        cm.add_messages([ChatMessage.user(text_or_blocks="msg")])
        assert cm.message_store.count == 1

    def test_apply_state_changes_without_iteration(self) -> None:
        """非迭代中 apply_state_changes 抛 RuntimeError。"""
        cm = _make_cm()
        with pytest.raises(RuntimeError, match="No iteration in progress"):
            cm.apply_state_changes({"key": "value"})


# ----------------------------------------------------------------
# TestContextManagerState — 状态管理
# ----------------------------------------------------------------


class TestContextManagerState:
    """ContextManager 状态管理测试。"""

    def test_get_current_state(self) -> None:
        """返回当前状态深拷贝。"""
        cm = _make_cm(initial_state={"a": 1})
        state = cm.get_current_state()
        assert state == {"a": 1}
        state["a"] = 999
        assert cm.get_current_state() == {"a": 1}

    def test_apply_state_changes(self) -> None:
        """在事务中应用变更。"""
        cm = _make_cm(initial_state={"count": 0})
        cm.begin_iteration()
        preview = cm.apply_state_changes({"count": 5})
        assert preview == {"count": 5}
        assert cm.get_current_state() == {"count": 0}

    def test_state_isolation_after_rollback(self) -> None:
        """rollback 后状态完全恢复。"""
        cm = _make_cm(initial_state={"x": 1, "y": 2})
        cm.begin_iteration()
        cm.apply_state_changes({"x": 100, "y": 200, "z": 300})
        cm.rollback_iteration(RuntimeError("fail"))

        assert cm.get_current_state() == {"x": 1, "y": 2}

    def test_remove_state_deletes_key(self) -> None:
        """remove_state 删除指定键，其余键不受影响。"""
        cm = _make_cm(initial_state={"a": 1, "b": 2})
        cm.set_state("a", {"counter": 3})
        cm.remove_state("a")

        assert cm.get_current_state() == {"b": 2}

    def test_remove_state_missing_key_is_idempotent(self) -> None:
        """remove_state 对缺席键幂等静默。"""
        cm = _make_cm(initial_state={"a": 1})
        cm.remove_state("nonexistent")

        assert cm.get_current_state() == {"a": 1}

    def test_remove_state_during_iteration_raises(self) -> None:
        """迭代中调用 remove_state 抛 RuntimeError。"""
        cm = _make_cm(initial_state={"a": 1})
        cm.begin_iteration()

        with pytest.raises(RuntimeError, match="Cannot call remove_state"):
            cm.remove_state("a")

        cm.rollback_iteration(RuntimeError("fail"))
        assert cm.get_current_state() == {"a": 1}


# ----------------------------------------------------------------
# TestContextManagerQuery — 查询 API
# ----------------------------------------------------------------


class TestContextManagerQuery:
    """ContextManager 查询 API 测试。"""

    def test_get_history(self) -> None:
        """返回完整历史。"""
        cm = _make_cm()
        for i in range(3):
            cm.begin_iteration()
            cm.commit_iteration(ability_names=[f"ability_{i}"])

        history = cm.get_history()
        assert len(history) == 4
        assert history[0].ability_names == ["init"]
        assert history[-1].ability_names == ["ability_2"]

    def test_get_history_with_limit(self) -> None:
        """限制历史数量。"""
        cm = _make_cm()
        for i in range(5):
            cm.begin_iteration()
            cm.commit_iteration(ability_names=[f"ability_{i}"])

        history = cm.get_history(limit=2)
        assert len(history) == 2
        assert history[-1].ability_names == ["ability_4"]

    def test_get_branch_heads(self) -> None:
        """返回分支头节点。"""
        cm = _make_cm()
        cm.begin_iteration()
        cm.commit_iteration(ability_names=["ability_1"])

        heads = cm.get_branch_heads()
        main_branch_id = cm.get_active_session().active_branch_id
        assert main_branch_id in heads
        assert heads[main_branch_id].ability_names == ["ability_1"]

    def test_get_chain_node(self) -> None:
        """按 ID 获取节点。"""
        cm = _make_cm()
        root_id = cm.active_head.id
        node = cm.get_chain_node(root_id)
        assert node is not None
        assert node.ability_names == ["init"]

    def test_get_chain_node_nonexistent(self) -> None:
        """不存在的节点返回 None。"""
        cm = _make_cm()
        assert cm.get_chain_node("nonexistent") is None

    async def test_get_llm_messages(self) -> None:
        """返回当前消息列表。"""
        cm = _make_cm()
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="hello"), ChatMessage.ai(text="hi")])
        cm.commit_iteration(ability_names=["conversation"])

        messages = await cm.get_llm_messages()
        assert len(messages) == 2
        assert messages[0].text == "hello"
        assert messages[1].text == "hi"

    async def test_get_llm_messages_with_filter(self) -> None:
        """使用 filter_fn 过滤消息。"""
        cm = _make_cm()
        cm.begin_iteration()
        cm.add_messages(
            [
                ChatMessage.user(text_or_blocks="hello"),
                ChatMessage.ai(text="hi"),
                ChatMessage.user(text_or_blocks="world"),
            ]
        )
        cm.commit_iteration(ability_names=["conversation"])

        messages = await cm.get_llm_messages(filter_fn=lambda m: m.role == "user")
        assert len(messages) == 2
        assert all(m.role == "user" for m in messages)

    async def test_get_llm_messages_empty(self) -> None:
        """空 store 返回空列表。"""
        cm = _make_cm()
        assert await cm.get_llm_messages() == []

    async def test_get_llm_messages_includes_pending_during_iteration(self) -> None:
        """迭代中 get_llm_messages 应包含尚未 commit 的 pending_messages。

        回归测试：修复 get_llm_messages() 在迭代中不包含 pending_messages
        导致 LLM 收到不完整消息列表（缺少 ChatMessage.user）的问题。
        """
        cm = _make_cm(system_prompt="You are a helpful assistant.")

        assert cm.message_store.count == 1

        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="Hello!")])

        messages = await cm.get_llm_messages()
        assert len(messages) == 2
        assert messages[0].text == "You are a helpful assistant."
        assert messages[1].text == "Hello!"

        cm.commit_iteration(
            ability_names=["conversation"],
            action_results=[
                {
                    "ability_name": "conversation",
                    "action_result": ActionResult(
                        outcome=ActionOutcome.SUCCESS,
                        data={"response": "Hi there!"},
                    ),
                }
            ],
        )
        assert cm.message_store.count == 2

        messages = await cm.get_llm_messages()
        assert len(messages) == 2

    async def test_get_llm_messages_no_pending_outside_iteration(self) -> None:
        """迭代外 get_llm_messages 直接从 store 读取。"""
        cm = _make_cm()

        cm.add_messages([ChatMessage.user(text_or_blocks="hello")])
        assert cm.message_store.count == 1

        messages = await cm.get_llm_messages()
        assert len(messages) == 1
        assert messages[0].text == "hello"


# ----------------------------------------------------------------
# TestContextManagerFork — 子 Agent 继承
# ----------------------------------------------------------------


class TestContextManagerFork:
    """ContextManager fork 子 Agent 测试。"""

    def test_fork_creates_independent_cm(self) -> None:
        """fork 创建独立 ContextManager。"""
        cm = _make_cm()
        child = cm.fork_for_sub_agent("child-agent")
        assert child.agent_name == "child-agent"
        assert child is not cm

    def test_fork_inherits_state(self) -> None:
        """子 CM 继承父 CM 状态。"""
        cm = _make_cm(initial_state={"key": "value"})
        child = cm.fork_for_sub_agent("child-agent")
        assert child.get_current_state() == {"key": "value"}

    async def test_fork_inherits_messages(self) -> None:
        """子 CM 继承父 CM 消息。"""
        cm = _make_cm()
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="hello")])
        cm.commit_iteration(ability_names=["conversation"])

        child = cm.fork_for_sub_agent("child-agent")
        assert child.message_store.count == 1
        assert (await child.get_llm_messages())[0].text == "hello"

    def test_fork_with_state_filter(self) -> None:
        """使用 state_filter 过滤继承的状态。"""
        cm = _make_cm(initial_state={"public": "yes", "secret": "no"})
        child = cm.fork_for_sub_agent(
            "child-agent",
            state_filter=lambda s: {k: v for k, v in s.items() if k != "secret"},
        )
        state = child.get_current_state()
        assert state == {"public": "yes"}
        assert "secret" not in state

    def test_fork_with_custom_system_prompt(self) -> None:
        """覆盖 system_prompt。"""
        cm = _make_cm(system_prompt="Parent prompt")
        child = cm.fork_for_sub_agent("child-agent", system_prompt="Child prompt")
        assert child._system_prompt == "Child prompt"

    def test_fork_independent_iterations(self) -> None:
        """子 CM 独立迭代不影响父 CM。"""
        cm = _make_cm()
        parent_history_len = len(cm.get_history())

        child = cm.fork_for_sub_agent("child-agent")
        child.begin_iteration()
        child.commit_iteration(ability_names=["child_ability"])

        assert len(cm.get_history()) == parent_history_len
        assert len(child.get_history()) == 2

    def test_fork_independent_state(self) -> None:
        """子 CM 状态变更不影响父 CM。"""
        cm = _make_cm(initial_state={"count": 0})
        child = cm.fork_for_sub_agent("child-agent")

        child.begin_iteration()
        child.commit_iteration(ability_names=["increment"], state_changes={"count": 100})

        assert cm.get_current_state() == {"count": 0}
        assert child.get_current_state() == {"count": 100}

    def test_fork_independent_messages(self) -> None:
        """子 CM 消息变更不影响父 CM。"""
        cm = _make_cm()
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="parent_msg")])
        cm.commit_iteration(ability_names=["conversation"])

        parent_msg_count = cm.message_store.count

        child = cm.fork_for_sub_agent("child-agent")
        child.begin_iteration()
        child.add_messages([ChatMessage.user(text_or_blocks="child_msg")])
        child.commit_iteration(ability_names=["conversation"])

        assert cm.message_store.count == parent_msg_count
        assert child.message_store.count == parent_msg_count + 1

    def test_fork_preserves_snapshot_interval(self) -> None:
        """fork 时继承父 CM 的 snapshot_interval。"""
        cm = _make_cm(snapshot_interval=3)
        child = cm.fork_for_sub_agent("child-agent")
        assert child.message_store.snapshot_interval == 3

    def test_fork_with_custom_snapshot_interval(self) -> None:
        """fork 时指定自定义 snapshot_interval。"""
        cm = _make_cm(snapshot_interval=5)
        child = cm.fork_for_sub_agent("child-agent", snapshot_interval=10)
        assert child.message_store.snapshot_interval == 10

    def test_fork_default_no_window_manager(self) -> None:
        """默认不继承 window_manager（子 CM 无 wm，行为与现状一致）。"""
        cm = _make_cm(window_manager=_make_window_manager())
        child = cm.fork_for_sub_agent("child-agent")
        assert child.window_manager is None

    def test_fork_with_independent_window_manager(self) -> None:
        """显式传入独立实例时子 CM 使用该实例。"""
        cm = _make_cm(window_manager=_make_window_manager())
        child_wm = _make_window_manager(max_tokens=500)
        child = cm.fork_for_sub_agent("child-agent", window_manager=child_wm)
        assert child.window_manager is child_wm
        assert child.window_manager is not cm.window_manager

    def test_fork_propagates_compact_config(self) -> None:
        """compact 配置随 fork 传播（threshold/keep_recent/method）。"""
        cm = _make_cm(
            window_manager=_make_window_manager(),
            compact_threshold=0.75,
            compact_keep_recent=3,
            compact_method="ghrah.builtin",
        )
        child = cm.fork_for_sub_agent("child-agent")
        assert child.compact_threshold == 0.75
        assert child.compact_keep_recent == 3
        assert child.compact_method == "ghrah.builtin"

    def test_fork_propagated_threshold_without_wm_not_armed(self) -> None:
        """无 wm 时 threshold 传播不武装决策门（get_window_status 口径不变）。"""
        cm = _make_cm(compact_threshold=0.8)
        child = cm.fork_for_sub_agent("child-agent")
        status = child.get_window_status()
        assert child.window_manager is None
        assert status["budget_tokens"] == 0
        assert status["compact_threshold"] is None


# ----------------------------------------------------------------
# TestContextManagerBuildContext — 上下文构建
# ----------------------------------------------------------------


class TestContextManagerBuildContext:
    """ContextManager build_execution_context 测试。"""

    def test_build_execution_context_basic(self) -> None:
        """基本构建，字段正确。"""
        cm = _make_cm(initial_state={"key": "value"})
        config = AgentConfig(name="test-agent")

        ctx = cm.build_execution_context(config=config)

        assert isinstance(ctx, AbilityExecutionContext)
        assert ctx.agent_state == {"key": "value"}
        assert ctx.context_manager is cm


# ----------------------------------------------------------------
# TestContextManagerIntegration — 集成场景
# ----------------------------------------------------------------


class TestContextManagerIntegration:
    """ContextManager 集成场景测试。"""

    def test_full_iteration_lifecycle(self) -> None:
        """完整生命周期：init → begin → add → commit。"""
        cm = _make_cm(initial_state={"step": 0})

        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="hello")])
        cm.apply_state_changes({"step": 1})
        node = cm.commit_iteration(
            ability_names=["conversation"],
            action_results=[
                {"ability_name": "conversation", "action_result": make_action_result()}
            ],
            state_changes={"extra": "data"},
        )

        assert node.ability_names == ["conversation"]
        assert node.agent_state == {"step": 1, "extra": "data"}
        assert len(node.messages_delta) == 1
        assert cm.message_store.count == 1
        assert not cm.in_iteration

    def test_multiple_iterations(self) -> None:
        """多次迭代完整流程。"""
        cm = _make_cm(initial_state={"count": 0})

        for i in range(1, 4):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"msg_{i}")])
            cm.apply_state_changes({"count": i})
            node = cm.commit_iteration(ability_names=[f"step_{i}"])

        assert node.iteration == 3
        assert cm.get_current_state() == {"count": 3}
        assert cm.message_store.count == 3
        assert cm.chain.node_count == 4

    def test_iteration_with_rollback_and_retry(self) -> None:
        """迭代失败回滚后重试。"""
        cm = _make_cm(initial_state={"retries": 0})

        # 先 commit 一次
        cm.begin_iteration()
        cm.commit_iteration(ability_names=["step1"])

        # 回滚
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="attempt 1")])
        cm.apply_state_changes({"retries": 1})
        retry_branch = cm.rollback_iteration(RuntimeError("timeout"))

        assert retry_branch.metadata["is_rollback"] is True
        assert cm.get_current_state() == {"retries": 0}

        # 在回滚分支上继续
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="attempt 2")])
        cm.apply_state_changes({"retries": 1})
        success_node = cm.commit_iteration(ability_names=["retry_ability"])

        assert success_node.ability_names == ["retry_ability"]
        assert success_node.agent_state == {"retries": 1}

    def test_snapshot_interval_configuration(self) -> None:
        """快照间隔配置生效。"""
        cm = _make_cm(snapshot_interval=2)

        nodes: list[ContextNode] = []
        for i in range(1, 7):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"msg_{i}")])
            node = cm.commit_iteration(ability_names=[f"ability_{i}"])
            nodes.append(node)

        snapshot_iterations = [n.iteration for n in nodes if n.is_snapshot]
        assert snapshot_iterations == [2, 4, 6]

        delta_iterations = [n.iteration for n in nodes if not n.is_snapshot]
        assert delta_iterations == [1, 3, 5]

    def test_full_snapshot_delta_cycle(self) -> None:
        """多轮 commit 验证 snapshot/delta 交替。"""
        cm = _make_cm(snapshot_interval=3)

        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="m1")])
        n1 = cm.commit_iteration(ability_names=["a1"])
        assert not n1.is_snapshot
        assert len(n1.messages_delta) == 1

        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="m2")])
        n2 = cm.commit_iteration(ability_names=["a2"])
        assert not n2.is_snapshot
        assert len(n2.messages_delta) == 1

        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="m3")])
        n3 = cm.commit_iteration(ability_names=["a3"])
        assert n3.is_snapshot
        assert n3.messages_snapshot is not None
        assert len(n3.messages_snapshot) == 3

        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="m4")])
        n4 = cm.commit_iteration(ability_names=["a4"])
        assert not n4.is_snapshot
        assert len(n4.messages_delta) == 1

    async def test_rollback_does_not_affect_message_store(self) -> None:
        """回滚不影响已提交的消息。"""
        cm = _make_cm()

        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="committed")])
        cm.commit_iteration(ability_names=["ability_1"])
        assert cm.message_store.count == 1

        # 回滚前需要再 commit 一次，因为回滚需要一个有前驱的节点
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="second")])
        cm.commit_iteration(ability_names=["ability_2"])

        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="will be discarded")])
        cm.rollback_iteration(RuntimeError("fail"))

        # 已提交的消息保留在 main 分支，回滚不影响
        # 但活跃分支现在是 rollback 分支，history 只包含 rollback 节点
        assert cm.message_store.count == 2

    def test_chain_history_reflects_all_operations(self) -> None:
        """链历史反映所有操作（包括回滚）。"""
        cm = _make_cm()

        cm.begin_iteration()
        cm.commit_iteration(ability_names=["ability_1"])

        cm.begin_iteration()
        cm.rollback_iteration(RuntimeError("fail"))

        # 回滚后，新 Branch 与原 Branch 共享当前 Head。
        cm.begin_iteration()
        cm.commit_iteration(ability_names=["ability_2"])

        # 未提交的失败迭代不制造节点。
        history = cm.get_history()
        assert len(history) == 3
        assert history[0].ability_names == ["init"]
        assert history[1].ability_names == ["ability_1"]
        assert history[2].ability_names == ["ability_2"]


# ----------------------------------------------------------------
# TestWindowOccupancyAnchor — 窗口占用锚点与 compact 决策
# ----------------------------------------------------------------


class TestWindowOccupancyAnchor:
    """占用锚点记录、失效合约与决策/触发辅助。"""

    def test_token_estimator_prefers_window_manager_instance(self) -> None:
        """token_estimator 出口：WindowManager 实例优先于 CM 级参数。"""
        from ghrah.context.token_estimator import DEFAULT_TOKEN_ESTIMATOR

        wm = _make_window_manager(max_tokens=1000)
        cm = _make_cm(window_manager=wm)

        # WM 未注入 estimator → 两侧同为默认实例
        assert cm.token_estimator is wm.estimator
        assert cm.token_estimator is DEFAULT_TOKEN_ESTIMATOR

        # WM 注入专属实例 → 出口随 WM 实例
        custom = WeightedTokenEstimator()
        wm_custom = _make_window_manager(max_tokens=1000)
        wm_custom._estimator = custom
        cm_custom = _make_cm(window_manager=wm_custom)
        assert cm_custom.token_estimator is custom

    def test_token_estimator_falls_back_to_cm_param_without_wm(self) -> None:
        """无 WindowManager 时 token_estimator 回落 CM 级参数/默认实例。"""
        from ghrah.context.token_estimator import DEFAULT_TOKEN_ESTIMATOR

        cm = _make_cm()
        assert cm.token_estimator is DEFAULT_TOKEN_ESTIMATOR

        custom = WeightedTokenEstimator()
        cm_custom = _make_cm(estimator=custom)
        assert cm_custom.token_estimator is custom

    def test_record_window_occupied_sets_anchor_and_last_real(self) -> None:
        """record 同时更新锚点与 last_real_input_tokens。"""
        cm = _make_cm()

        cm.record_window_occupied(1234)

        status = cm.get_window_status()
        assert status["occupied_tokens"] == 1234
        assert status["last_real_input_tokens"] == 1234

    def test_record_real_usage_retains_breakdown_and_accumulates(self) -> None:
        """record_real_usage 留存 output/cache 拆分并累计三值。"""
        from ghrah.types.tokens import TokenUsage

        cm = _make_cm()
        cm.record_real_usage(
            TokenUsage(
                input_tokens=100, output_tokens=10, cache_read_tokens=60, cache_write_tokens=5
            )
        )
        cm.record_real_usage(
            TokenUsage(
                input_tokens=200, output_tokens=20, cache_read_tokens=120, cache_write_tokens=7
            )
        )

        status = cm.get_window_status()
        assert status["occupied_tokens"] == 200
        assert status["last_real_input_tokens"] == 200
        assert status["last_real_output_tokens"] == 20
        assert status["last_real_cache_read_tokens"] == 120
        assert status["last_real_cache_write_tokens"] == 7
        assert status["cumulative_input_tokens"] == 300
        assert status["cumulative_output_tokens"] == 30
        assert status["cumulative_cache_read_tokens"] == 180

    def test_rollback_invalidates_anchor_but_keeps_cumulative(self) -> None:
        """迭代回滚只失效锚点，留存与累计（成本口径）不受影响。"""
        from ghrah.types.tokens import TokenUsage

        cm = _make_cm()
        cm.record_real_usage(
            TokenUsage(
                input_tokens=500, output_tokens=50, cache_read_tokens=0, cache_write_tokens=0
            )
        )
        cm.begin_iteration()
        cm.rollback_iteration(RuntimeError("test rollback"))

        status = cm.get_window_status()
        assert status["occupied_tokens"] is None
        assert status["last_real_input_tokens"] == 500
        assert status["cumulative_input_tokens"] == 500

    def test_evaluate_decision_returns_none_when_threshold_unset(self) -> None:
        """threshold=None（默认禁用）不参与决策，返回 None。"""
        cm = _make_cm()
        cm.record_window_occupied(99999)

        assert cm.evaluate_compaction_decision() is None

    def test_evaluate_decision_returns_none_without_window_manager(self) -> None:
        """无 WindowManager（无预算权威）不参与决策。"""
        cm = _make_cm(compact_threshold=0.8)
        cm.record_window_occupied(99999)

        assert cm.evaluate_compaction_decision() is None

    def test_evaluate_decision_below_threshold(self) -> None:
        """锚点低于阈值：决策产出但 needs_compaction=False。"""
        cm = _make_cm(
            window_manager=_make_window_manager(max_tokens=1000),
            compact_threshold=0.8,
        )
        cm.record_window_occupied(500)

        decision = cm.evaluate_compaction_decision()

        assert decision == {"occupied": 500, "threshold": 0.8, "needs_compaction": False}

    def test_evaluate_decision_at_threshold_triggers(self) -> None:
        """锚点达到阈值（>=）即置 needs_compaction=True。"""
        cm = _make_cm(
            window_manager=_make_window_manager(max_tokens=1000),
            compact_threshold=0.8,
        )
        cm.record_window_occupied(800)

        decision = cm.evaluate_compaction_decision()

        assert decision == {"occupied": 800, "threshold": 0.8, "needs_compaction": True}

    def test_evaluate_decision_without_anchor_needs_false(self) -> None:
        """无锚点（从未记录或已失效）needs_compaction=False，交安全阀兜底。"""
        cm = _make_cm(
            window_manager=_make_window_manager(max_tokens=1000),
            compact_threshold=0.8,
        )

        decision = cm.evaluate_compaction_decision()

        assert decision == {"occupied": None, "threshold": 0.8, "needs_compaction": False}

    def test_check_compact_trigger_requires_threshold_and_anchor(self) -> None:
        """触发门：threshold=None 或无锚点时恒 False。"""
        plain = _make_cm()
        plain.record_window_occupied(99999)
        assert plain.check_compact_trigger() is False

        configured = _make_cm(
            window_manager=_make_window_manager(max_tokens=1000),
            compact_threshold=0.8,
        )
        assert configured.check_compact_trigger() is False

        configured.record_window_occupied(800)
        assert configured.check_compact_trigger() is True

    def test_check_compact_trigger_manual_flag_overrides_anchor(self) -> None:
        """手动标志是显式意图：无锚点/未配阈值也不受活体否决。"""
        cm = _make_cm()

        cm.request_compact()
        assert cm.check_compact_trigger() is True
        assert cm.consume_compact_request() is True
        assert cm.check_compact_trigger() is False

    def test_request_compact_idempotent(self) -> None:
        """重复置标志幂等，一次消费即清。"""
        cm = _make_cm()

        cm.request_compact()
        cm.request_compact()
        assert cm.consume_compact_request() is True
        assert cm.consume_compact_request() is False

    def test_rollback_invalidates_anchor_but_keeps_store(self) -> None:
        """回归：rollback 后锚点失效（None），store 不动、消息不丢。"""
        cm = _make_cm()
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="first")])
        cm.commit_iteration(ability_names=["step1"])
        cm.record_window_occupied(600)
        store_before = cm.message_store

        cm.begin_iteration()
        cm.rollback_iteration(RuntimeError("boom"))

        assert cm._window_occupied is None
        assert cm.message_store is store_before
        assert cm.message_store.count == 1

    def test_activate_branch_invalidates_anchor(self) -> None:
        """activate_branch（head 变更）后锚点失效，store 按新 head 重建。"""
        cm = _make_cm()
        session_id = cm.active_session_id
        main_branch_id = cm.get_active_session().active_branch_id
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="shared")])
        cm.commit_iteration(ability_names=["shared"])
        fork_point = cm.active_head
        retry = cm.create_branch(
            session_id=session_id,
            name="retry-1",
            from_node_id=fork_point.id,
            parent_branch_id=main_branch_id,
        )
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="main-only")])
        cm.commit_iteration(ability_names=["main-only"])
        cm.record_window_occupied(600)

        cm.activate_branch(session_id, retry.branch_id)

        assert cm._window_occupied is None
        assert [m.text for m in cm.message_store.current_messages] == ["shared"]

    def test_activate_session_invalidates_anchor(self) -> None:
        """activate_session（head 变更）后锚点失效。"""
        cm = _make_cm()
        main_session_id = cm.active_session_id
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="main")])
        cm.commit_iteration(ability_names=["main"])
        created = cm.create_session(
            initial_messages=[ChatMessage.user(text_or_blocks="other")],
        )
        cm.record_window_occupied(600)

        cm.activate_session(created.session_id)

        assert cm._window_occupied is None
        assert [m.text for m in cm.message_store.current_messages] == ["other"]

        # 切回原 session 后锚点同样失效（restore 路径公共汇点）
        cm.record_window_occupied(700)
        cm.activate_session(main_session_id)
        assert cm._window_occupied is None

    def test_get_window_status_shape(self) -> None:
        """观测出口字段齐全；无 wm 时 budget=0、budget_source=None、threshold=None。"""
        bare = _make_cm()
        status = bare.get_window_status()

        assert status["occupied_tokens"] is None
        assert status["basis"] == "anchor"
        assert status["budget_tokens"] == 0
        assert status["budget_source"] is None
        assert status["compact_threshold"] is None
        assert status["compact_keep_recent"] == 2
        assert status["compact_method"] == "ghrah.builtin"
        assert status["last_real_input_tokens"] is None

        configured = _make_cm(
            window_manager=_make_window_manager(max_tokens=2000),
            compact_threshold=0.75,
            compact_keep_recent=3,
        )
        configured.record_window_occupied(1500)
        status = configured.get_window_status()

        assert status["occupied_tokens"] == 1500
        assert status["budget_tokens"] == 2000
        assert status["budget_source"] == "declared"
        assert status["compact_threshold"] == 0.75
        assert status["compact_keep_recent"] == 3

    def test_peek_pending_compaction_readonly(self) -> None:
        """peek 只读窥视，不排水；排水仍由 commit_iteration 负责。"""
        cm = _make_cm()
        cm._pending_compaction.append({"strategy": "llm_summary"})

        assert cm.peek_pending_compaction() == [{"strategy": "llm_summary"}]
        assert cm.peek_pending_compaction() == [{"strategy": "llm_summary"}]
        assert len(cm._pending_compaction) == 1
