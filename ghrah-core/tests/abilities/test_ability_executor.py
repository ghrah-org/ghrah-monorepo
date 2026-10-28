# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""AbilityExecutor 测试。

测试 AbilityExecutor 接口和 LocalAbilityExecutor 实现：
- execute_ability: 单个 Ability 执行（含 PRE/POST_EXECUTE Hook）
- execute_tool_calls: 多个 tool_calls 并行执行
- HookRunner: Hook 运行机制
- handle_hitl_hook_result: HITL 审批流程
- receive_hitl_response: 外部 HITL 结果接收
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ghrah.chat.content import ToolCallBlock
from ghrah.core.event_publisher import EventPublisher, NullEventPublisher
from ghrah.core.events import HITLRequestEvent, HITLResolvedEvent
from ghrah.core.hitl import HITLPromiseRegistry
from ghrah.scheme.execution.base import Ability, ActionOutcome, ActionResult
from ghrah.scheme.execution.context import AbilityExecutionContext
from ghrah.scheme.execution.executor import AbilityExecutor, LocalAbilityExecutor
from ghrah.scheme.execution.hook_context import HookContext
from ghrah.scheme.execution.hooks import Hook, HookPoint, HookResult

# ─── 测试用 Ability 和 Hook ───


class MockAbility(Ability):
    """测试用 Ability。"""

    def __init__(self, name: str = "mock_ability", action_result: ActionResult | None = None):
        self._name = name
        self._action_result = action_result or ActionResult(
            outcome=ActionOutcome.SUCCESS, data={"result": "ok"}
        )

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        return self._action_result

    def get_hooks(self) -> list[Hook]:
        return []

    def bind_tool(self) -> dict | None:
        return {
            "type": "function",
            "function": {
                "name": self._name,
                "description": f"Mock ability: {self._name}",
                "parameters": {"type": "object", "properties": {}},
            },
        }


class FailingAbility(Ability):
    """执行失败的 Ability。"""

    @property
    def name(self) -> str:
        return "failing_ability"

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "intentional failure"})

    def get_hooks(self) -> list[Hook]:
        return []


class BlockingHook(Hook):
    """返回 should_continue=False 的 Hook（直接拦截，不触发 HITL）。"""

    hook_point = HookPoint.PRE_EXECUTE

    def __init__(self, target_ability: str = "mock_ability", message: str = "Blocked by hook"):
        self._target_ability = target_ability
        self._message = message

    async def should_trigger(self, context: AbilityExecutionContext) -> bool:
        return context.current_ability_name == self._target_ability

    async def execute(
        self, context: AbilityExecutionContext, result: ActionResult | None
    ) -> HookResult:
        return HookResult(should_continue=False, message=self._message)


class HITLBlockingHook(Hook):
    """返回 should_continue=False 且 requires_hitl=True 的 Hook（模拟 HITL 拦截）。"""

    hook_point = HookPoint.PRE_EXECUTE

    def __init__(
        self, target_ability: str = "mock_ability", message: str = "Human approval required"
    ):
        self._target_ability = target_ability
        self._message = message

    async def should_trigger(self, context: AbilityExecutionContext) -> bool:
        return context.current_ability_name == self._target_ability

    async def execute(
        self, context: AbilityExecutionContext, result: ActionResult | None
    ) -> HookResult:
        return HookResult.hitl(message=self._message)


class ModifyingHook(Hook):
    """修改上下文的 Hook。"""

    hook_point = HookPoint.PRE_EXECUTE

    def __init__(self, target_ability: str = "mock_ability"):
        self._target_ability = target_ability

    async def should_trigger(self, context: AbilityExecutionContext) -> bool:
        return context.current_ability_name == self._target_ability

    async def execute(
        self, context: AbilityExecutionContext, result: ActionResult | None
    ) -> HookResult:
        return HookResult(
            should_continue=True,
            modified_context={"hook_modified": True},
        )


class PostExecuteHook(Hook):
    """POST_EXECUTE Hook。"""

    hook_point = HookPoint.POST_EXECUTE

    async def should_trigger(self, context: AbilityExecutionContext) -> bool:
        return True

    async def execute(
        self, context: AbilityExecutionContext, result: ActionResult | None
    ) -> HookResult:
        return HookResult(should_continue=True, message="post_execute hook ran")


# ─── 测试 LocalAbilityExecutor ───


class TestLocalAbilityExecutor:
    """LocalAbilityExecutor 测试。"""

    def test_init_default(self):
        """测试默认初始化。"""
        executor = LocalAbilityExecutor(agent_name="test-agent")
        assert executor._agent_name == "test-agent"
        assert executor._hook_store.list_all() == []
        assert isinstance(executor._event_publisher, NullEventPublisher)
        assert executor._hitl_timeout == 300.0

    def test_init_with_hooks(self):
        """测试带 hooks 初始化。"""
        hook = BlockingHook()
        executor = LocalAbilityExecutor(agent_name="test-agent", hooks=[hook])
        assert len(executor._hook_store.list_all()) == 1

    def test_update_hooks(self):
        """测试 update_hooks 方法。"""
        executor = LocalAbilityExecutor(agent_name="test-agent")
        assert executor._hook_store.list_all() == []

        hook = BlockingHook()
        executor.update_hooks([hook])
        assert executor._hook_store.list_all() == [hook]

    def test_update_event_publisher(self):
        """测试 update_event_publisher 方法。"""
        executor = LocalAbilityExecutor(agent_name="test-agent")
        assert isinstance(executor._event_publisher, NullEventPublisher)

        mock_publisher = MagicMock(spec=EventPublisher)
        executor.update_event_publisher(mock_publisher)
        assert executor._event_publisher is mock_publisher

    @pytest.mark.asyncio
    async def test_execute_ability_simple(self):
        """测试简单 Ability 执行。"""
        ability = MockAbility()
        executor = LocalAbilityExecutor(agent_name="test-agent")

        context = AbilityExecutionContext(
            current_ability_name="mock_ability",
            tool_args={},
            agent_state={},
        )

        result = await executor.execute_ability(ability, context)
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_execute_ability_with_pre_execute_hook(self):
        """测试 PRE_EXECUTE Hook 修改上下文。"""
        ability = MockAbility()
        hook = ModifyingHook()
        executor = LocalAbilityExecutor(agent_name="test-agent", hooks=[hook])

        context = AbilityExecutionContext(
            current_ability_name="mock_ability",
            tool_args={},
            agent_state={},
            accumulated_data={},
        )

        result = await executor.execute_ability(ability, context)
        assert result.outcome == ActionOutcome.SUCCESS
        # Hook 修改的上下文应该被合并
        assert context.accumulated_data.get("hook_modified") is True

    @pytest.mark.asyncio
    async def test_execute_ability_with_blocking_hook(self):
        """测试 PRE_EXECUTE Hook 直接拦截执行（非 HITL 场景，不等待审批）。"""
        ability = MockAbility()
        hook = BlockingHook(message="Permission denied")
        executor = LocalAbilityExecutor(agent_name="test-agent", hooks=[hook])

        context = AbilityExecutionContext(
            current_ability_name="mock_ability",
            tool_args={},
            agent_state={},
            accumulated_data={},
        )

        # BlockingHook 返回 should_continue=False 且 requires_hitl=False
        # 应直接返回 FAILURE，不进入 HITL 等待流程
        result = await executor.execute_ability(ability, context)
        assert result.outcome == ActionOutcome.FAILURE
        assert result.data.get("error") == "Permission denied"
        assert "hitl_rejected" not in result.data  # 非 HITL 拦截不应包含 hitl_rejected

    @pytest.mark.asyncio
    async def test_execute_ability_with_hitl_blocking_hook_rejected(self):
        """测试 HITL Hook 拦截且审批被拒绝的场景。"""
        ability = MockAbility()
        hook = HITLBlockingHook(message="Human approval required")
        executor = LocalAbilityExecutor(
            agent_name="test-agent",
            hooks=[hook],
            hitl_timeout=5.0,
        )

        context = AbilityExecutionContext(
            current_ability_name="mock_ability",
            tool_args={"call_id": "call_hitl_reject"},
            agent_state={},
            accumulated_data={},
        )

        # 模拟审批拒绝
        async def reject():
            await asyncio.sleep(0.1)
            executor.receive_hitl_response(
                ability_name="mock_ability",
                tool_call_id="call_hitl_reject",
                approved=False,
            )

        asyncio.create_task(reject())

        result = await executor.execute_ability(ability, context)
        # HITL 拒绝后应返回 FAILURE 且包含 hitl_rejected 标记与归因字段
        assert result.outcome == ActionOutcome.FAILURE
        assert result.data.get("hitl_rejected") is True
        assert result.data.get("promise_id")  # 归因字段随终态回执下发
        assert result.data.get("tool_call_id") == "call_hitl_reject"

    @pytest.mark.asyncio
    async def test_execute_ability_hitl_rejected_reason_passthrough(self):
        """审批人拒绝理由（HITLResult.result）须透传进错误信息，不被丢弃。"""
        ability = MockAbility()
        hook = HITLBlockingHook(
            target_ability="execute_command", message="Command requires approval"
        )
        executor = LocalAbilityExecutor(
            agent_name="test-agent",
            hooks=[hook],
            hitl_timeout=5.0,
        )

        context = AbilityExecutionContext(
            current_ability_name="execute_command",
            tool_args={"command": "pnpm type-check", "call_id": "call_reason"},
            agent_state={},
            accumulated_data={},
        )

        async def reject_with_reason():
            await asyncio.sleep(0.1)
            executor.receive_hitl_response(
                ability_name="execute_command",
                tool_call_id="call_reason",
                approved=False,
                # unit._handle_hitl_response 组装的形状：{"reason": <UI 填写的理由>}
                result={"reason": "too risky for this workspace"},
            )

        asyncio.create_task(reject_with_reason())

        result = await executor.execute_ability(ability, context)
        assert result.outcome == ActionOutcome.FAILURE
        assert result.data.get("hitl_rejected") is True
        # 拒绝理由到达 LLM 上下文（bug A 修复点）
        assert "too risky for this workspace" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_execute_ability_hitl_timeout_attribution(self):
        """超时错误须携带命令摘要与归因 id（并行双超时可归因到具体调用）。"""
        ability = MockAbility()
        hook = HITLBlockingHook(
            target_ability="execute_command", message="Command requires approval"
        )
        executor = LocalAbilityExecutor(
            agent_name="test-agent",
            hooks=[hook],
            hitl_timeout=0.05,
        )

        context = AbilityExecutionContext(
            current_ability_name="execute_command",
            tool_args={"command": "pnpm build", "call_id": "call_t1"},
            agent_state={},
            accumulated_data={},
        )

        result = await executor.execute_ability(ability, context)
        assert result.outcome == ActionOutcome.FAILURE
        assert result.data.get("hitl_timeout") is True
        assert result.data.get("promise_id")
        assert result.data.get("tool_call_id") == "call_t1"
        # 命令摘要进错误文本，并行超时可凭命令归因
        assert "pnpm build" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_execute_ability_with_post_execute_hook(self):
        """测试 POST_EXECUTE Hook。"""
        ability = MockAbility()
        hook = PostExecuteHook()
        executor = LocalAbilityExecutor(agent_name="test-agent", hooks=[hook])

        context = AbilityExecutionContext(
            current_ability_name="mock_ability",
            tool_args={},
            agent_state={},
            accumulated_data={},
        )

        result = await executor.execute_ability(ability, context)
        assert result.outcome == ActionOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_execute_ability_failing(self):
        """测试执行失败的 Ability。"""
        ability = FailingAbility()
        executor = LocalAbilityExecutor(agent_name="test-agent")

        context = AbilityExecutionContext(
            current_ability_name="failing_ability",
            tool_args={},
            agent_state={},
        )

        result = await executor.execute_ability(ability, context)
        assert result.outcome == ActionOutcome.FAILURE
        assert result.data == {"error": "intentional failure"}

    @pytest.mark.asyncio
    async def test_execute_tool_calls_simple(self):
        """测试简单 tool_calls 执行。"""
        ability = MockAbility()
        executor = LocalAbilityExecutor(agent_name="test-agent")

        # 创建 mock ContextManager
        mock_cm = MagicMock()
        mock_cm.get_current_state.return_value = {}

        tool_calls = [
            ToolCallBlock(name="mock_ability", arguments={"key": "value"}, id="call_123"),
        ]

        results = await executor.execute_tool_calls(
            tool_calls=tool_calls,
            abilities={"mock_ability": ability},
            accumulated_data={},
            context_manager=mock_cm,
            last_action_result=None,
        )

        assert len(results) == 1
        assert results[0]["ability_name"] == "mock_ability"
        assert results[0]["action_result"].outcome == ActionOutcome.SUCCESS
        assert results[0]["tool_call_id"] == "call_123"

    @pytest.mark.asyncio
    async def test_execute_tool_calls_injects_supervisor(self):
        """断线回归：tool-call 路径须注入 supervisor/manifest_store。

        此前 _execute_single_with_context 只注入 agent_name/context_manager，
        LLM 工具调用集群类 ability（query_agents 等）拿到 supervisor=None，
        误报 standalone。
        """

        class SupervisorProbeAbility(Ability):
            @property
            def name(self) -> str:
                return "supervisor_probe"

            async def execute(self, context: AbilityExecutionContext) -> ActionResult:
                return ActionResult(
                    outcome=ActionOutcome.SUCCESS,
                    data={
                        "has_supervisor": context.supervisor is not None,
                        "has_manifest_store": context.manifest_store is not None,
                    },
                )

            def get_hooks(self) -> list[Hook]:
                return []

            def bind_tool(self) -> dict | None:
                return None

        supervisor = MagicMock()
        supervisor.manifest_store = object()
        executor = LocalAbilityExecutor(agent_name="test-agent", supervisor=supervisor)

        mock_cm = MagicMock()
        mock_cm.get_current_state.return_value = {}

        results = await executor.execute_tool_calls(
            tool_calls=[ToolCallBlock(name="supervisor_probe", arguments={}, id="call_sup")],
            abilities={"supervisor_probe": SupervisorProbeAbility()},
            accumulated_data={},
            context_manager=mock_cm,
            last_action_result=None,
        )

        data = results[0]["action_result"].data
        assert data["has_supervisor"] is True
        assert data["has_manifest_store"] is True

    @pytest.mark.asyncio
    async def test_execute_tool_calls_unknown_ability(self):
        """测试未知 ability 的 tool_call。"""
        executor = LocalAbilityExecutor(agent_name="test-agent")

        mock_cm = MagicMock()
        mock_cm.get_current_state.return_value = {}

        tool_calls = [
            ToolCallBlock(name="unknown_ability", arguments={}, id="call_456"),
        ]

        results = await executor.execute_tool_calls(
            tool_calls=tool_calls,
            abilities={},
            accumulated_data={},
            context_manager=mock_cm,
            last_action_result=None,
        )

        assert len(results) == 1
        assert results[0]["ability_name"] == "unknown_ability"
        assert results[0]["action_result"].outcome == ActionOutcome.FAILURE
        assert "Unknown ability" in results[0]["action_result"].data["error"]

    @pytest.mark.asyncio
    async def test_execute_tool_calls_multiple(self):
        """测试多个 tool_calls 并行执行。"""
        ability1 = MockAbility(name="ability_a")
        ability2 = MockAbility(name="ability_b")
        executor = LocalAbilityExecutor(agent_name="test-agent")

        mock_cm = MagicMock()
        mock_cm.get_current_state.return_value = {}

        tool_calls = [
            ToolCallBlock(name="ability_a", arguments={}, id="call_1"),
            ToolCallBlock(name="ability_b", arguments={}, id="call_2"),
        ]

        results = await executor.execute_tool_calls(
            tool_calls=tool_calls,
            abilities={"ability_a": ability1, "ability_b": ability2},
            accumulated_data={},
            context_manager=mock_cm,
        )

        assert len(results) == 2
        ability_names = {r["ability_name"] for r in results}
        assert ability_names == {"ability_a", "ability_b"}

    @pytest.mark.asyncio
    async def test_execute_tool_calls_child_cancelled_propagates(self):
        """并行子任务在途等待被外部取消 → CancelledError 原样传播。

        场景：teardown/超时取消 ability 在途 await 的 future（子任务自身
        未被 cancel），gather(return_exceptions=True) 会把 CancelledError
        收集为结果——不得折算成伪结果（r["tool_call_id"] 抛 TypeError）
        或伪 FAILURE，取消必须以 CancelledError 形状逃逸。
        """

        class CancelledAwaitAbility(Ability):
            """等待一个被外部任务取消的 future（模拟在途 receive 被取消）。"""

            def __init__(self) -> None:
                self._canceller: asyncio.Task | None = None

            @property
            def name(self) -> str:
                return "cancelled_await"

            async def execute(self, context: AbilityExecutionContext) -> ActionResult:
                fut = asyncio.get_running_loop().create_future()

                async def cancel_after_suspend() -> None:
                    await asyncio.sleep(0)
                    fut.cancel()

                # 取消来自独立任务：子任务自身未被 cancel，
                # CancelledError 经 gather 结果面收集上来
                self._canceller = asyncio.create_task(cancel_after_suspend())
                await fut
                return ActionResult(outcome=ActionOutcome.SUCCESS, data={})

            def get_hooks(self) -> list[Hook]:
                return []

        executor = LocalAbilityExecutor(agent_name="test-agent")

        mock_cm = MagicMock()
        mock_cm.get_current_state.return_value = {}

        with pytest.raises(asyncio.CancelledError):
            await executor.execute_tool_calls(
                tool_calls=[
                    ToolCallBlock(name="cancelled_await", arguments={}, id="call_cancel"),
                    ToolCallBlock(name="mock_ability", arguments={}, id="call_ok"),
                ],
                abilities={
                    "cancelled_await": CancelledAwaitAbility(),
                    "mock_ability": MockAbility(),
                },
                accumulated_data={},
                context_manager=mock_cm,
            )

    @pytest.mark.asyncio
    async def test_execute_tool_calls_child_exception_becomes_failure(self):
        """普通 Exception 子任务仍折算为 FAILURE 结果（取消传播改动的回归护栏）。"""

        class RaisingAbility(Ability):
            @property
            def name(self) -> str:
                return "raising_ability"

            async def execute(self, context: AbilityExecutionContext) -> ActionResult:
                raise RuntimeError("boom in ability")

            def get_hooks(self) -> list[Hook]:
                return []

        executor = LocalAbilityExecutor(agent_name="test-agent")

        mock_cm = MagicMock()
        mock_cm.get_current_state.return_value = {}

        results = await executor.execute_tool_calls(
            tool_calls=[ToolCallBlock(name="raising_ability", arguments={}, id="call_raise")],
            abilities={"raising_ability": RaisingAbility()},
            accumulated_data={},
            context_manager=mock_cm,
        )

        assert len(results) == 1
        assert results[0]["ability_name"] == "raising_ability"
        assert results[0]["action_result"].outcome == ActionOutcome.FAILURE
        assert "boom in ability" in results[0]["action_result"].data["error"]
        assert results[0]["tool_call_id"] == "call_raise"

    @pytest.mark.asyncio
    async def test_hook_runner_no_hooks(self):
        """测试没有 Hook 时的 HookRunner。"""
        executor = LocalAbilityExecutor(agent_name="test-agent")

        context = AbilityExecutionContext(
            current_ability_name="test",
            tool_args={},
            agent_state={},
        )

        result = await executor._hook_runner.run(
            HookContext.from_ability_context(HookPoint.PRE_EXECUTE, context)
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_hook_runner_with_matching_hook(self):
        """测试匹配触发点的 Hook。"""
        hook = ModifyingHook()
        executor = LocalAbilityExecutor(agent_name="test-agent", hooks=[hook])

        context = AbilityExecutionContext(
            current_ability_name="mock_ability",
            tool_args={},
            agent_state={},
            accumulated_data={},
        )

        result = await executor._hook_runner.run(
            HookContext.from_ability_context(HookPoint.PRE_EXECUTE, context)
        )
        assert result is not None
        assert result.modified_context == {"hook_modified": True}

    @pytest.mark.asyncio
    async def test_hook_runner_with_non_matching_point(self):
        """测试不匹配触发点的 Hook 不执行。"""
        hook = ModifyingHook()  # PRE_EXECUTE
        executor = LocalAbilityExecutor(agent_name="test-agent", hooks=[hook])

        context = AbilityExecutionContext(
            current_ability_name="mock_ability",
            tool_args={},
            agent_state={},
            accumulated_data={},
        )

        result = await executor._hook_runner.run(
            HookContext.from_ability_context(HookPoint.POST_EXECUTE, context)
        )
        assert result is None  # PRE_EXECUTE hook 不应在 POST_EXECUTE 触发

    @pytest.mark.asyncio
    async def test_hook_runner_merge_multiple(self):
        """测试多个 Hook 结果合并。"""
        hook1 = ModifyingHook()
        hook2 = PostExecuteHook()
        executor = LocalAbilityExecutor(agent_name="test-agent", hooks=[hook1, hook2])

        context = AbilityExecutionContext(
            current_ability_name="mock_ability",
            tool_args={},
            agent_state={},
            accumulated_data={},
        )

        # 两个 PRE_EXECUTE Hook 应该合并结果
        # 但 PostExecuteHook 的 hook_point 是 POST_EXECUTE，不会触发
        result = await executor._hook_runner.run(
            HookContext.from_ability_context(HookPoint.PRE_EXECUTE, context)
        )
        assert result is not None
        assert result.modified_context == {"hook_modified": True}

    @pytest.mark.asyncio
    async def test_handle_hitl_hook_result_with_approval(self):
        """测试 HITL 审批通过流程。"""
        executor = LocalAbilityExecutor(agent_name="test-agent")

        context = AbilityExecutionContext(
            current_ability_name="write_file",
            tool_args={"file_path": "/tmp/test.txt", "call_id": "call_789"},
            agent_state={},
            accumulated_data={},
        )

        hook_result = HookResult.hitl(
            message="Human approval required for write operation on: /tmp/test.txt",
        )

        # 在另一个任务中模拟外部审批
        async def approve_after_delay():
            await asyncio.sleep(0.1)
            executor.receive_hitl_response(
                ability_name="write_file",
                tool_call_id="call_789",
                approved=True,
            )

        asyncio.create_task(approve_after_delay())

        result = await executor.handle_hitl_hook_result(hook_result, context)
        assert result.outcome == "approved"  # 审批通过
        assert result.promise_id  # 归因字段随回执下发
        assert result.tool_call_id == "call_789"

        # promise 已随审批终结移除（无注册表时跳过）
        assert executor.hitl_promise_registry is None

    @pytest.mark.asyncio
    async def test_handle_hitl_hook_result_with_rejection(self):
        """测试 HITL 审批拒绝流程。"""
        executor = LocalAbilityExecutor(agent_name="test-agent")

        context = AbilityExecutionContext(
            current_ability_name="write_file",
            tool_args={"file_path": "/tmp/test.txt", "call_id": "call_101"},
            agent_state={},
            accumulated_data={},
        )

        hook_result = HookResult.hitl(
            message="Human approval required for write operation on: /tmp/test.txt",
        )

        # 模拟拒绝
        async def reject_after_delay():
            await asyncio.sleep(0.1)
            executor.receive_hitl_response(
                ability_name="write_file",
                tool_call_id="call_101",
                approved=False,
            )

        asyncio.create_task(reject_after_delay())

        result = await executor.handle_hitl_hook_result(hook_result, context)
        assert result.outcome == "rejected"  # 审批拒绝
        assert result.promise_id
        assert result.tool_call_id == "call_101"

    @pytest.mark.asyncio
    async def test_handle_hitl_hook_result_timeout(self):
        """测试 HITL 审批超时。"""
        executor = LocalAbilityExecutor(agent_name="test-agent", hitl_timeout=0.1)

        context = AbilityExecutionContext(
            current_ability_name="write_file",
            tool_args={"file_path": "/tmp/test.txt"},
            agent_state={},
            accumulated_data={},
        )

        hook_result = HookResult.hitl(
            message="Human approval required for write operation on: /tmp/test.txt",
        )

        # 不 resolve Future，等待超时
        result = await executor.handle_hitl_hook_result(hook_result, context)
        assert result.outcome == "timeout"  # 超时与拒绝语义分离
        assert result.promise_id  # 超时也带归因字段（并行超时可归因）
        assert result.tool_call_id  # 无 call_id 时兜底生成

    def test_receive_hitl_response_no_pending_future(self):
        """测试没有等待中的 Future 时接收 HITL 响应。"""
        executor = LocalAbilityExecutor(agent_name="test-agent")

        # 没有创建 Future，直接调用 receive_hitl_response
        resolved = executor.receive_hitl_response(
            ability_name="write_file",
            tool_call_id="nonexistent",
            approved=True,
        )
        assert resolved is False  # 没有 pending Future

    @pytest.mark.asyncio
    async def test_hitl_request_event_published(self):
        """测试 HITL 请求事件被发布。"""
        mock_publisher = AsyncMock(spec=EventPublisher)
        executor = LocalAbilityExecutor(
            agent_name="test-agent",
            event_publisher=mock_publisher,
            hitl_timeout=0.1,
        )

        context = AbilityExecutionContext(
            current_ability_name="write_file",
            tool_args={"file_path": "/tmp/test.txt"},
            agent_state={},
            accumulated_data={},
        )

        hook_result = HookResult.hitl(
            message="Human approval required for write operation on: /tmp/test.txt",
        )

        # 触发 HITL 流程（会超时）
        await executor.handle_hitl_hook_result(hook_result, context)

        # 验证两类事件均被发布：请求 + 超时终结
        assert mock_publisher.publish.await_count == 2
        request_event = mock_publisher.publish.await_args_list[0][0][0]
        assert isinstance(request_event, HITLRequestEvent)
        assert request_event.agent_name == "test-agent"
        assert request_event.ability_name == "write_file"
        assert request_event.promise_id  # promise 随请求下发
        # 触发原因（Hook 拦截说明）随请求下发，审批人可见"为何要审"
        assert "Human approval required" in request_event.reason
        resolved_event = mock_publisher.publish.await_args_list[1][0][0]
        assert isinstance(resolved_event, HITLResolvedEvent)
        assert resolved_event.status == "timeout"
        assert resolved_event.promise_id == request_event.promise_id

    @pytest.mark.asyncio
    async def test_hitl_promise_registry_lifecycle(self):
        """promise 注册表：请求时登记 → 审批后移除；超时按三元组惰性清理。"""
        registry = HITLPromiseRegistry()
        mock_publisher = AsyncMock(spec=EventPublisher)
        executor = LocalAbilityExecutor(
            agent_name="test-agent",
            event_publisher=mock_publisher,
            hitl_timeout=0.1,
            hitl_promise_registry=registry,
        )

        context = AbilityExecutionContext(
            current_ability_name="write_file",
            tool_args={"file_path": "/tmp/test.txt", "call_id": "call_p1"},
            agent_state={},
            accumulated_data={},
        )
        hook_result = HookResult.hitl(message="Human approval required")

        # 超时路径：promise 登记后按三元组惰性清除
        outcome = await executor.handle_hitl_hook_result(hook_result, context)
        assert outcome.outcome == "timeout"
        request_event = mock_publisher.publish.await_args_list[0][0][0]
        assert registry.resolve(request_event.promise_id) is None

        # 批准路径：promise 登记后随 resolve 移除
        async def approve_after_delay():
            await asyncio.sleep(0.05)
            executor.receive_hitl_response(
                ability_name="write_file",
                tool_call_id="call_p1",
                approved=True,
            )

        asyncio.create_task(approve_after_delay())
        outcome = await executor.handle_hitl_hook_result(hook_result, context)
        assert outcome.outcome == "approved"
        request_event2 = mock_publisher.publish.await_args_list[-2][0][0]
        assert isinstance(request_event2, HITLRequestEvent)
        assert registry.resolve(request_event2.promise_id) is None  # 已消费

    @pytest.mark.asyncio
    async def test_execute_ability_with_hitl_approval(self):
        """测试完整的 HITL 审批流程：Hook 拦截 → 等待审批 → 继续执行。"""
        ability = MockAbility()
        # 创建一个 HITL 拦截 Hook（requires_hitl=True）
        hitl_hook = HITLBlockingHook(message="Human approval required for write operation")
        executor = LocalAbilityExecutor(
            agent_name="test-agent",
            hooks=[hitl_hook],
            hitl_timeout=5.0,
        )

        context = AbilityExecutionContext(
            current_ability_name="mock_ability",
            tool_args={"call_id": "call_hitl_1"},
            agent_state={},
            accumulated_data={},
        )

        # 模拟外部审批通过
        async def approve():
            await asyncio.sleep(0.1)
            executor.receive_hitl_response(
                ability_name="mock_ability",
                tool_call_id="call_hitl_1",
                approved=True,
            )

        asyncio.create_task(approve())

        result = await executor.execute_ability(ability, context)
        # HITL 审批通过后，Ability 应该正常执行
        assert result.outcome == ActionOutcome.SUCCESS


# ─── 测试 AbilityExecutor 接口 ───


class TestAbilityExecutorInterface:
    """AbilityExecutor 抽象接口测试。"""

    def test_cannot_instantiate_abstract(self):
        """测试不能直接实例化抽象类。"""
        with pytest.raises(TypeError):
            AbilityExecutor()

    def test_local_ability_executor_is_ability_executor(self):
        """测试 LocalAbilityExecutor 是 AbilityExecutor 的子类。"""
        executor = LocalAbilityExecutor(agent_name="test-agent")
        assert isinstance(executor, AbilityExecutor)


class TestHitlConsecutiveTimeoutCircuit:
    """连续超时熔断：降级等待 + 人工响应复位（默认禁用零隐式）。"""

    @pytest.mark.asyncio
    async def test_disabled_by_default_full_wait(self):
        """limit=0（默认）时永不降级——保持旧行为。"""
        executor = LocalAbilityExecutor(
            agent_name="test-agent", hitl_timeout=0.1, hitl_consecutive_timeout_limit=0
        )
        context = AbilityExecutionContext(
            current_ability_name="execute_command",
            tool_args={"command": "pnpm build"},
            agent_state={},
            accumulated_data={},
        )
        hook_result = HookResult.hitl(message="requires approval")

        first = await executor.handle_hitl_hook_result(hook_result, context)
        second = await executor.handle_hitl_hook_result(hook_result, context)
        assert first.outcome == "timeout" and first.degraded is False
        assert second.outcome == "timeout" and second.degraded is False

    @pytest.mark.asyncio
    async def test_degrades_after_consecutive_timeouts(self):
        """连续 N 次超时后：后续等待降级为 degraded_timeout。"""
        executor = LocalAbilityExecutor(
            agent_name="test-agent",
            hitl_timeout=0.1,
            hitl_consecutive_timeout_limit=2,
            hitl_degraded_timeout=0.02,
        )
        context = AbilityExecutionContext(
            current_ability_name="execute_command",
            tool_args={"command": "pnpm build"},
            agent_state={},
            accumulated_data={},
        )
        hook_result = HookResult.hitl(message="requires approval")

        first = await executor.handle_hitl_hook_result(hook_result, context)
        second = await executor.handle_hitl_hook_result(hook_result, context)
        assert first.degraded is False and second.degraded is False

        # 第三次起降级（等待更短）
        third = await executor.handle_hitl_hook_result(hook_result, context)
        assert third.outcome == "timeout"
        assert third.degraded is True
        assert third.degraded_timeout == 0.02

    @pytest.mark.asyncio
    async def test_human_response_resets_counter(self):
        """任何 approve/reject 都复位计数——审批人回来批一张卡即解除。"""
        executor = LocalAbilityExecutor(
            agent_name="test-agent",
            hitl_timeout=0.1,
            hitl_consecutive_timeout_limit=1,
            hitl_degraded_timeout=0.02,
        )
        context = AbilityExecutionContext(
            current_ability_name="execute_command",
            tool_args={"command": "pnpm build", "call_id": "call_c1"},
            agent_state={},
            accumulated_data={},
        )
        hook_result = HookResult.hitl(message="requires approval")

        # 第一次超时 → 计数 1（已达阈值 1）
        first = await executor.handle_hitl_hook_result(hook_result, context)
        assert first.degraded is False

        # 第二次本应降级（等待缩短为 0.02s），approve 立即 resolve 复位计数
        async def approve_after_delay():
            await asyncio.sleep(0)
            executor.receive_hitl_response(
                ability_name="execute_command",
                tool_call_id="call_c1",
                approved=True,
            )

        asyncio.create_task(approve_after_delay())
        second = await executor.handle_hitl_hook_result(hook_result, context)
        assert second.outcome == "approved"

        # 计数已复位：再超时也不立即降级
        third = await executor.handle_hitl_hook_result(hook_result, context)
        assert third.degraded is False

    @pytest.mark.asyncio
    async def test_degraded_error_guides_end_task(self):
        """降级超时的错误信息声明通道疑似离线并引导 end_task。"""
        ability = MockAbility()
        hook = HITLBlockingHook(
            target_ability="execute_command", message="Command requires approval"
        )
        executor = LocalAbilityExecutor(
            agent_name="test-agent",
            hooks=[hook],
            hitl_timeout=0.05,
            hitl_consecutive_timeout_limit=1,
            hitl_degraded_timeout=0.02,
        )
        context = AbilityExecutionContext(
            current_ability_name="execute_command",
            tool_args={"command": "pnpm build"},
            agent_state={},
            accumulated_data={},
        )

        await executor.execute_ability(ability, context)  # 第一次：未降级
        result = await executor.execute_ability(ability, context)  # 第二次：降级

        assert result.outcome == ActionOutcome.FAILURE
        assert result.data.get("hitl_degraded") is True
        error = result.data.get("error", "")
        assert "likely offline" in error
        assert "end_task" in error
