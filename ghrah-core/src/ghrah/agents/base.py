# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ActorAgent 基类：Actor + Ability 组合 + Hook 驱动循环 + ContextManager 集成。

每个 ActorAgent 是一个 Agent Actor，内部持有：
- LLMProtocol（通过 llm_factory 回调惰性创建）
- 已注册的 Ability 集合（组合模式）
- 绑定的 tool schema（从 Ability.bind_tool() 收集）
- ContextManager（注入，上下文管理：消息历史、状态、链式历史、窗口管理、驱动循环控制状态）
- AbilityExecutor（注入，Ability 执行器，将执行与 Agent 循环解耦）

构造方式：
    推荐使用 AgentBuilder.from_config() 便捷创建（零配置默认注入），
    或直接调用 ActorAgent(...) 注入所有依赖（纯 DI，适合测试）。

Hook:
    三层 Hook 架构：
    drive_loop 级：BEFORE_ACTION → _action → AFTER_ACTION
    action 级：PRE_LLM_CALL → [LLM 调用] → POST_LLM_CALL → [tool 执行] → PRE/POST_EXECUTE
    ability 级：PRE_EXECUTE → [execute] → POST_EXECUTE
    特殊触发：ON_ERROR（异常时）、ON_MAX_ITERATIONS（达到最大迭代时）

AbilityExecutor:
    - AbilityExecutor 接口将 Ability 执行从 Agent 循环中解耦
    - LocalAbilityExecutor：单体模式，在 Core 端本地执行 Ability + HITL

ContextManager:
    - ContextManager 统一管理消息、状态、链式历史和驱动循环控制状态
    - _drive_loop 中每次迭代通过 begin_iteration / commit_iteration / rollback_iteration
    - 驱动循环控制状态（iteration, max_iterations, should_continue 等）由 ContextManager 管理
    - AbilityExecutionContext 只保留 Ability 执行所需的最少信息
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

from ghrah.abilities.context import AbilityExecutionContext
from ghrah.abilities.errors import AbilityNotFoundError
from ghrah.abilities.hook_context import HookContext
from ghrah.abilities.hook_runner import HookRunner
from ghrah.abilities.hook_store import HookListView, HookStore
from ghrah.abilities.hooks import Hook, HookPoint, HookResult
from ghrah.abilities.invocation import AbilityInvocation
from ghrah.agents.runner import AgentRunner
from ghrah.chat.content import ContentBlock, block_to_dict, blocks_from_dicts
from ghrah.chat.message import ChatMessage
from ghrah.context.action_session import ActionSession
from ghrah.context.branch import ActionBranch
from ghrah.context.iteration_commit import (
    DeliveryFinisher,
    IterationCommitter,
    durable_iteration_scope,
    get_delivery_finisher,
    get_iteration_committer,
)
from ghrah.context.iteration_state import IterationState
from ghrah.context.manager import ContextManager
from ghrah.context.window import parse_vendor_window_tokens
from ghrah.core.ability_protocol import AbilityProtocol, ExecutorProtocol
from ghrah.core.event_publisher import (
    EventPublisher,
    NullEventPublisher,
)
from ghrah.core.events import (
    ActionChainUpdatedEvent,
    AgentErrorEvent,
    AgentResponseEvent,
    BranchActivatedEvent,
    BranchArchivedEvent,
    BranchCreatedEvent,
    BranchDeletedEvent,
    ContextUsageUpdatedEvent,
    SessionActivatedEvent,
    SessionArchivedEvent,
    SessionCreatedEvent,
    SessionDeletedEvent,
)
from ghrah.core.exceptions import (
    AgentError,
    AgentInitializationError,
)
from ghrah.core.llm_protocol import LLMProtocol, LLMResponseProtocol
from ghrah.core.message import AgentMessage, MessageType, classify_source
from ghrah.types.config_types import AgentConfig
from ghrah.types.results import ActionOutcome, ActionResult

logger = logging.getLogger(__name__)

_VISIBLE_RESPONSE_RETRY_PROMPT = (
    "The previous generation ended without user-visible text or a tool call. "
    "Answer the user's request now and put the final answer in the normal response "
    "content. Do not return only reasoning content."
)
_EMPTY_VISIBLE_RESPONSE = "模型未返回可展示的正文，请重试。"

# 厂商上下文超限错误保守识别白名单：只认确定类别，不解析数字。
# 类型名匹配 SDK 异常类（如 anthropic.BadRequestError 子类不可枚举，故用
# 消息短语为主、类型名为辅的双通道；误判会吞真实故障，宁缺勿滥）。
_CONTEXT_LIMIT_TYPE_MARKERS = ("contextwindowexceeded", "contextlengthexceeded")
_CONTEXT_LIMIT_MESSAGE_MARKERS = (
    "context_length_exceeded",
    "prompt is too long",
    "maximum context length",
    "exceeds the context window",
    "exceed the context window",
    "exceed context limit",
    "too many input tokens",
)

# 紧急压缩阶梯：最多减半 4 次（budget/16）
_MAX_EMERGENCY_HALVINGS = 4


def _is_context_limit_error(exc: BaseException) -> bool:
    """保守判定异常是否为厂商上下文超限类别（不解析报文数字）。"""
    type_name = type(exc).__name__.lower()
    if any(marker in type_name for marker in _CONTEXT_LIMIT_TYPE_MARKERS):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _CONTEXT_LIMIT_MESSAGE_MARKERS)


class ActorAgent:
    """基于 Ability 组合的 Agent Actor。

    生命周期:
        1. __init__ 接收注入的依赖（ContextManager, AbilityExecutor 等）
        2. 首次调用 _ensure_llm() 时，通过 llm_factory 回调创建 LLM
        3. 通过 register_ability() 注册能力（含 bind_tool 收集）
        4. receive() 经 Runner 排队：ability 选择 → hook 时序 → 执行 → 条件转移

        - 所有消息和状态通过 ContextManager 管理
        - 驱动循环控制状态（iteration, max_iterations 等）由 ContextManager 管理
        - AbilityExecutionContext 只保留 Ability 执行所需的最少信息

    用法:
        # 推荐使用 AgentBuilder 便捷创建
        agent = AgentBuilder.from_config(config, abilities=[ConversationAbility()])

        # 或直接依赖注入（适合测试）
        agent = ActorAgent(
            config=config,
            context_manager=context_manager,
            ability_executor=executor,
            context_manager_factory=lambda: context_manager,
        )
    """

    def __init__(
        self,
        config: AgentConfig,
        context_manager: ContextManager,
        ability_executor: ExecutorProtocol,
        context_manager_factory: Callable[[], ContextManager],
        supervisor: Any = None,
        event_publisher: EventPublisher | None = None,
        llm_factory: Callable[[AgentConfig], LLMProtocol] | None = None,
    ) -> None:
        self.config = config
        self._supervisor = supervisor
        self._context_manager = context_manager
        self._context_manager_factory = context_manager_factory
        self._ability_executor = ability_executor
        self._event_publisher = event_publisher or NullEventPublisher()
        self._llm: LLMProtocol | None = None
        self._llm_factory = llm_factory
        self._initialized = False
        self._abilities: dict[str, AbilityProtocol] = {}
        self._bound_tools: list[dict[str, Any]] = []
        self._hook_store = HookStore()
        self._hook_runner = HookRunner(self._hook_store)
        self._hook_view = self._hook_store.view()
        if hasattr(self._ability_executor, "update_hook_runner"):
            self._ability_executor.update_hook_runner(self._hook_runner)
        self._message_queue: asyncio.Queue[ChatMessage] = asyncio.Queue()
        self._runner = AgentRunner(config.name)
        self._iteration_state = IterationState()
        # 驱动循环活跃指示：receive() 期间置位，供 request_compact() 区分
        # "驱动中置标志"与"空闲直接执行"双态
        self._drive_active: bool = False

        # 框架级消息历史（AgentMessage 对象，使用自有 ChatMessage 格式）
        # ContextManager 管理 ChatMessage 消息，这里保留框架 AgentMessage 对象的记录
        self._message_history: list[AgentMessage] = []

        logger.info(f"ActorAgent[{config.name}] created")

    @property
    def _all_hooks(self) -> HookListView:
        """Compatibility view backed by HookStore."""

        return self._hook_view

    @_all_hooks.setter
    def _all_hooks(self, hooks: list[Hook]) -> None:
        self._hook_store.remove_owner(HookStore.AGENT_OWNER)
        self._hook_store.add_hooks(HookStore.AGENT_OWNER, hooks)

    # ----------------------------------------------------------------
    # Ability 注册
    # ----------------------------------------------------------------

    def register_ability(self, ability: AbilityProtocol) -> str:
        """注册一个能力到 Agent。

        相当于 tool 的绑定，但比单纯的 tool bind 有更多的控制与自由度。
        内部会调用 ability.bind_tool() 获取原生 Function Calling schema。
        注册后会更新 ContextManager 中的状态作用域。

        Args:
            ability: 要注册的能力实例

        Returns:
            注册的 ability 名称（调用方可用于确认注册成功）

        Raises:
            AgentError: 如果同名能力已注册
        """
        if ability.name in self._abilities:
            raise AgentError(
                self.config.name,
                f"Ability '{ability.name}' already registered",
            )

        # 收集 bind_tool 的 schema，后续绑定到 LLM
        tool_schema = ability.bind_tool()
        if tool_schema is not None:
            self._bound_tools.append(tool_schema)

        # 收集 hooks
        ability_hooks = ability.get_hooks()
        self._hook_store.add_hooks(ability.name, ability_hooks)

        self._abilities[ability.name] = ability

        # 写入 ability 默认状态到 ContextManager 的状态作用域
        default_state = ability.get_default_state()
        if default_state:
            self._context_manager.set_state(ability.name, copy.deepcopy(default_state))

        # 运行期注册立即对已初始化的 LLM 生效（工具面刷新）
        self._refresh_bound_tools()

        logger.info(
            f"ActorAgent[{self.config.name}] registered ability: "
            f"{ability.name} (tool_bound={tool_schema is not None}, "
            f"hooks={len(ability_hooks)})"
        )

        return ability.name

    def unregister_ability(self, name: str) -> None:
        """移除已注册的能力。

        Args:
            name: 能力名称

        Raises:
            AbilityNotFoundError: 如果能力不存在
        """
        if name not in self._abilities:
            raise AbilityNotFoundError(name)

        # 清理 ContextManager 中的状态作用域（迭代内 raise 由
        # remove_state 的 guard 承担；命令来自外部迭代边界，天然满足。
        # 置于本地变更之前：guard 抛出时注册面保持原状，不留半撤销）
        self._context_manager.remove_state(name)

        ability = self._abilities.pop(name)

        # 移除对应的 tool schema
        tool_schema = ability.bind_tool()
        if tool_schema is not None:
            self._bound_tools = [
                t for t in self._bound_tools if t.get("function", {}).get("name") != name
            ]

        # 移除对应的 hooks
        self._hook_store.remove_owner(ability.name)

        # 运行期注销立即对已初始化的 LLM 生效（工具面刷新）
        self._refresh_bound_tools()

        logger.info(f"ActorAgent[{self.config.name}] unregistered ability: {name}")

    def get_abilities(self) -> list[str]:
        """获取所有已注册的能力名称列表。"""
        return list(self._abilities.keys())

    @property
    def busy(self) -> bool:
        """公开运行忙闲状态，供实体管理端口在安全边界执行操作。"""
        return self._runner.busy or self._context_manager.in_iteration

    def get_entity_context(self) -> dict[str, Any]:
        """读取此执行实例的会话目录，不暴露可写 ContextManager。"""
        from ghrah.context.persistence.serialization import (
            serialize_action_session,
            serialize_branch,
        )

        sessions = self._context_manager.list_sessions()
        return {
            "active_session_id": self._context_manager.active_session_id,
            "sessions": [serialize_action_session(session) for session in sessions],
            "branches": [
                serialize_branch(branch)
                for session in sessions
                for branch in self._context_manager.list_branches(session.session_id)
            ],
        }

    def get_entity_history(self, session_id: str, branch_id: str, limit: int) -> dict[str, Any]:
        """读取实体内指定会话分支的已提交历史。"""
        from ghrah.context.persistence.serialization import serialize_node

        nodes = self._context_manager.get_history(
            session_id=session_id, branch_id=branch_id, limit=limit
        )
        return {
            "session_id": session_id,
            "branch_id": branch_id,
            "nodes": [serialize_node(node) for node in nodes],
        }

    def set_event_publisher(self, publisher: EventPublisher) -> None:
        """注入事件发布器（由 Supervisor 在创建时注入）。
        本地模式使用 NullEventPublisher（默认），Unit 挂载模式由宿主注入实现。
        同时更新 AbilityExecutor 的事件发布器。

        Args:
            publisher: EventPublisher 实例
        """
        self._event_publisher = publisher
        # 同步更新 executor 的事件发布器
        self._ability_executor.update_event_publisher(publisher)
        logger.info(
            f"ActorAgent[{self.config.name}] event publisher set: {type(publisher).__name__}"
        )

    async def receive_hitl_response(
        self,
        ability_name: str,
        tool_call_id: str,
        approved: bool,
        result: Any = None,
    ) -> None:
        """接收 HITL 审批结果（由 Supervisor 通过 Ray.remote 调用）。

        当 Observer 审批 HITL 请求后，宿主将结果路由到
        Supervisor，Supervisor 调用此方法将结果传递给 Agent。

        此方法委托给 AbilityExecutor.receive_hitl_response()。

        流程：Observer → Subject → Core → Supervisor → Agent

        Args:
            ability_name: Ability 名称
            tool_call_id: 工具调用 ID
            approved: 是否批准
            result: 审批附加结果
        """
        self._ability_executor.receive_hitl_response(
            ability_name=ability_name,
            tool_call_id=tool_call_id,
            approved=approved,
            result=result,
        )

    async def inject_message(self, message: ChatMessage) -> None:
        """向 Agent 注入消息，在下一轮迭代中被消费。

        线程安全，可从外部（WebSocket、HITL 回调等）异步调用。
        消息会在 _drive_loop 的下一次迭代开始时注入到 ContextManager。

        Args:
            message: 要注入的 ChatMessage
        """
        await self._message_queue.put(message)
        logger.info(
            f"ActorAgent[{self.config.name}] message injected: "
            f"role={message.role}, queue_size={self._message_queue.qsize()}"
        )

    def _drain_message_queue(self) -> list[ChatMessage]:
        """非阻塞排空消息队列，返回所有待处理消息。

        使用 get_nowait() 非阻塞获取，不会等待新消息到达。
        适用于 _drive_loop 每轮迭代开始时调用。

        Returns:
            队列中所有待处理的 ChatMessage 列表，可能为空
        """
        messages: list[ChatMessage] = []
        while not self._message_queue.empty():
            try:
                msg = self._message_queue.get_nowait()
                messages.append(msg)
            except asyncio.QueueEmpty:
                break
        return messages

    # ----------------------------------------------------------------
    # LLM 初始化
    # ----------------------------------------------------------------

    def _refresh_bound_tools(self) -> None:
        """运行期刷新 LLM 工具面。

        register/unregister_ability 后调用：LLM 已初始化则以当前
        _bound_tools 重新 configure_tools，使运行期注册/注销立即
        生效；LLM 未初始化则跳过——_ensure_llm 首建时本就携带
        当时的全量 tools。
        """
        if self._llm is None:
            return
        self._llm.configure_tools(self._bound_tools)

    async def _ensure_llm(self) -> LLMProtocol:
        """惰性初始化 LLM 客户端。

        通过 llm_factory 回调创建 LLM 实例。
        采用惰性初始化避免进程间序列化问题。

        使用 config.effective_agent_config_name 查找 agentconf 配置，
        支持运行时名称与 LLM 配置名称分离（如 worker 池场景）。

        LLM 创建后自动 configure_tools，使 LLM 能在响应中返回 tool_calls。
        system_prompt 已在 ContextManager.__init__ 中注入，
        此方法仅负责 LLM 客户端创建。
        """
        if self._llm is not None:
            return self._llm

        if self._llm_factory is None:
            raise AgentInitializationError(
                agent_name=self.config.name,
                message="No llm_factory provided; cannot initialize LLM",
            )

        try:
            self._llm = self._llm_factory(self.config)

            if self._bound_tools:
                self._llm.configure_tools(self._bound_tools)

            self._inject_llm_into_summary_strategy(self._llm)

            # agentconf 已完成参考数据与模型覆盖的合并；缺少能力接口的
            # 注入客户端保留模型表兼容路径。declared/vendor 不受影响。
            wm = self._context_manager.window_manager
            if wm is not None:
                capabilities = getattr(self._llm, "model_capabilities", None)
                if isinstance(capabilities, dict):
                    wm.resolve_budget_from_capabilities(capabilities)
                else:
                    wm.resolve_budget_from_model(self._llm.model)

            self._initialized = True

            logger.info(f"ActorAgent[{self.config.name}] LLM initialized: {self._llm.model}")
            return self._llm

        except Exception as e:
            raise AgentInitializationError(
                agent_name=self.config.name,
                message=f"Failed to initialize LLM: {e}",
            ) from e

    def _inject_llm_into_summary_strategy(self, llm: LLMProtocol) -> None:
        self._context_manager.inject_llm_into_summary(llm)

    # ----------------------------------------------------------------
    # 核心驱动循环
    # ----------------------------------------------------------------

    async def receive(self, message: AgentMessage) -> AgentMessage:
        """经唯一 Runner 排队处理消息，返回本次请求的回复。"""
        if getattr(self._supervisor, "message_manager", None) is not None:
            message.recipient = self.config.name
            return await self._supervisor.route_message(message)
        return await self._runner.run(lambda: self._receive_once(message))

    async def stop_receiving(self) -> None:
        """停止当前接收执行并取消排队请求，供终止 agent 时使用。"""
        await self._runner.close()

    async def receive_atomic(
        self,
        message: AgentMessage,
        committer: IterationCommitter,
        rollback: Callable[[], None] | None = None,
        finish: DeliveryFinisher | None = None,
        target_session_id: str | None = None,
    ) -> AgentMessage:
        """经 Runner 执行显式原子接收，每轮节点由调用方提交器保存。"""

        async def operation() -> AgentMessage:
            cm = self._context_manager
            if target_session_id and not self._message_queue.empty():
                raise RuntimeError("Session-routed receive cannot consume unscoped queued messages")
            selection = cm.use_session(target_session_id) if target_session_id else nullcontext()
            with selection, durable_iteration_scope(cm, committer, rollback, finish):
                try:
                    return await self._receive_once(message)
                finally:
                    # 压缩失败可能发生在排空队列之前；持久 mailbox 是该输入的恢复来源。
                    for queued in self._drain_message_queue():
                        if (
                            message.metadata.get("delivery_id") is None
                            or queued.metadata.get("delivery_id") != message.metadata["delivery_id"]
                        ):
                            self._message_queue.put_nowait(queued)

        return await self._runner.run(operation)

    async def resume_atomic(
        self,
        message: AgentMessage,
        committer: IterationCommitter,
        rollback: Callable[[], None],
        finish: DeliveryFinisher,
        *,
        session_id: str,
        branch_id: str,
        last_node_id: str,
    ) -> AgentMessage:
        """从指定持久 Head 的下一轮继续，不重注入输入，不重放已提交工具。"""

        async def operation() -> AgentMessage:
            cm = self._context_manager
            if not self._message_queue.empty():
                raise RuntimeError("Cannot resume with unscoped queued messages")
            with (
                cm.use_session(session_id),
                durable_iteration_scope(cm, committer, rollback, finish),
            ):
                if (
                    cm.active_head.id != last_node_id
                    or cm.get_active_session_branch().branch_id != branch_id
                ):
                    raise RuntimeError("In-memory session head changed before resume")
                if message.metadata.get("execute_ability"):
                    await self._restore_pending_tools()
                    return await self._finish_command_result(
                        message, cm.active_head.action_results[-1]["action_result"]
                    )
                if self.config.max_iterations == 0:
                    raise RuntimeError("Resume requires a non-zero iteration budget")
                await self._ensure_llm()
                await self._restore_pending_tools()
                self._iteration_state.max_iterations = self.config.max_iterations
                self._iteration_state.reset()
                if cm.active_head.action_results:
                    self._iteration_state.last_action_result = cm.active_head.action_results[
                        -1
                    ].get("action_result")
                self._drive_active = True
                try:
                    await self._drive_loop(
                        delivery_context={
                            **{
                                key: message.metadata[key]
                                for key in ("room_id", "message_manager")
                                if message.metadata.get(key)
                            },
                            "project_id": message.metadata["project_id"],
                            "agent_id": self.config.effective_agent_id,
                        }
                    )
                finally:
                    self._drive_active = False
                return await self._finish_response(message)

        return await self._runner.run(operation)

    async def _receive_once(self, message: AgentMessage) -> AgentMessage:
        """处理 Runner 领取的消息并驱动一次执行循环。

        LLM 调用移入 _action 层，receive 不再直接传递 llm。
        用户消息通过 ContextManager 管理。

        Args:
            message: 输入消息

        Returns:
            回复消息
        """
        logger.info(
            f"ActorAgent[{self.config.name}] received: {message.type.value} from {message.sender}"
        )
        self._message_history.append(message)
        if message.metadata.get("execute_ability"):
            return await self._receive_command_once(message)

        if not self._abilities:
            raise AgentError(
                self.config.name,
                "No abilities registered. Register at least one ability "
                "(e.g., ConversationAbility) before calling receive(). "
                "Agent behavior is determined by ability composition.",
            )

        try:
            # 确保LLM已初始化（但不再直接传递给 context）
            await self._ensure_llm()

            # 将用户消息入队，供 _drive_loop 在迭代中消费
            # AgentMessage.metadata（如 Room 投递的 room_id）合入 ChatMessage
            # metadata——随 commit_iteration 落入链节点 messages_delta，
            # 供回复归属推导（Room Filter）消费
            if message.content_blocks:
                text_or_blocks: str | list[ContentBlock] = blocks_from_dicts(message.content_blocks)
            else:
                text_or_blocks = message.content
            await self._message_queue.put(
                ChatMessage.user(
                    text_or_blocks=text_or_blocks,
                    source=(
                        message.sender
                        if message.metadata.get("external_sender")
                        else classify_source(message.sender)
                    ),
                    metadata=dict(message.metadata) if message.metadata else {},
                )
            )

            # 将 max_iterations 从 config 设置到 IterationState
            self._iteration_state.max_iterations = self.config.max_iterations
            self._iteration_state.reset()

            # 仅发布本轮新提交的节点。旧实现会在每次 receive 前重发当前链头；
            # Subject 重启后 RoomFilter 的内存去重为空，旧回复因此会先于新回复
            # 再次落入 RoomLog。历史同步由 get_chain_history 显式承担。
            delivery_context = {
                key: message.metadata[key]
                for key in ("room_id", "project_id", "agent_id", "message_manager")
                if message.metadata and message.metadata.get(key)
            }
            self._drive_active = True
            try:
                await self._drive_loop(delivery_context=delivery_context or None)
            finally:
                self._drive_active = False

            return await self._finish_response(message)

        except AgentError:
            raise
        except Exception as e:
            logger.error(f"ActorAgent[{self.config.name}] error in drive loop: {e}")
            error_reply = AgentMessage(
                sender=self.config.name,
                recipient=message.sender,
                content=f"Error: {e}",
                type=MessageType.ERROR,
                reply_to=message.id,
            )
            return error_reply

    async def _finish_command_result(
        self, message: AgentMessage, result: ActionResult
    ) -> AgentMessage:
        from ghrah.context.persistence.serialization import serialize_action_result

        response = AgentMessage.create_reply(
            message, json.dumps(serialize_action_result(result), ensure_ascii=False)
        )
        finisher = get_delivery_finisher(self._context_manager)
        if finisher:
            await finisher(response)
        return response

    async def _receive_command_once(self, message: AgentMessage) -> AgentMessage:
        """显式能力命令经同一 Runner 和持久提交作用域执行，无需模型推理。"""
        cm = self._context_manager
        invocation = message.metadata["execute_ability"]
        name, call_id = invocation["name"], invocation["call_id"]
        cm.begin_iteration()
        try:
            cm.add_messages(
                [
                    ChatMessage.user(
                        message.content, source=message.sender, metadata=dict(message.metadata)
                    )
                ]
            )
            from ghrah.chat.content import ToolCallBlock

            cm.add_messages(
                [
                    ChatMessage.ai(
                        [ToolCallBlock(id=call_id, name=name, arguments=invocation["args"])]
                    )
                ]
            )
            context = self._build_ability_context(name, invocation["args"], {})
            context.invocation = AbilityInvocation(name, invocation["args"], call_id)
            result = await self._ability_executor.execute_ability(self._abilities[name], context)
            results = [{"ability_name": name, "tool_call_id": call_id, "action_result": result}]
            if result.data.get("await_reply"):
                await cm.commit_iteration_async(
                    ability_names=[name],
                    action_results=results,
                    llm_metadata={"pending_tool_replies": True, "pending_tool_call_ids": [call_id]},
                )
                cm.begin_iteration()
                await self._await_tool_results(results)
            self._add_tool_results(results)
            node = await cm.commit_iteration_async(ability_names=[name], action_results=results)
            from ghrah.context.persistence.serialization import serialize_branch, serialize_node

            await self._event_publisher.publish(
                ActionChainUpdatedEvent(
                    agent_name=self.config.name,
                    node=serialize_node(node),
                    branch=serialize_branch(cm.get_active_session_branch()),
                )
            )
            return await self._finish_command_result(message, results[0]["action_result"])
        finally:
            cm.ensure_iteration_closed()

    async def _finish_response(self, message: AgentMessage) -> AgentMessage:
        """正常接收和中断续跑共用最终结果持久收尾与事件发布。"""
        # 构建最终回复
        response = self._build_response(message)

        finisher = get_delivery_finisher(self._context_manager)
        if finisher is not None:
            # 先保存最终结果并可靠收尾，再通知观察端；事件失败不丢回复。
            await finisher(response)

        # ────── 发布 AgentResponse 事件 ──────
        await self._event_publisher.publish(
            AgentResponseEvent(
                agent_name=self.config.name,
                content=response.content,
                content_blocks=response.content_blocks,
                message_type="result",
                metadata={
                    "iteration": self._iteration_state.iteration,
                },
            )
        )
        # ────── 结束 ──────
        return response

    async def request_compact(self) -> dict[str, Any]:
        """手动触发链上 compact 回合（命令入口，双态）。

        驱动循环活跃（receive 期间）：置手动标志，由下一轮触发门消费，
        回 ``{"scheduled": True}``；标志已置位时回 ``{"scheduled": True,
        "merged": True}``（连续请求合并，幂等）。

        空闲：直接执行一轮 compact（同步等待摘要 LLM 完成），回执即
        结果——``{"executed": True, "node_id": ...}`` 或窗外为空时
        ``{"executed": False, "reason": "empty_window"}``。

        Returns:
            回执 dict（见上）
        """
        cm = self._context_manager
        if self._runner.busy or self._drive_active:
            merged = cm.compact_requested
            cm.request_compact()
            return {"scheduled": True, "merged": merged}
        return await self._runner.run(lambda: self._run_compact_round("manual"))

    async def _drive_loop(
        self,
        delivery_context: dict[str, Any] | None = None,
    ) -> None:
        """核心驱动循环 — 每次迭代执行一个 action。

        驱动循环控制状态（iteration, max_iterations, should_continue 等）
        由 ContextManager 管理，不再通过 AbilityExecutionContext 传递。

        三层 Hook 架构：
            drive_loop 级：BEFORE_ACTION → _action → AFTER_ACTION
            特殊触发：ON_ERROR、ON_MAX_ITERATIONS

        每次迭代通过 ContextManager 的 begin/commit/rollback 管理事务。
        每轮迭代开始时从 _message_queue 排空新消息并注入到 ContextManager，
        支持迭代中注入新消息（如 HITL 回复、WebSocket 追加提示等）。
        迭代失败回滚时，排空的消息会重新注入队列，防止消息丢失。
        """
        cm = self._context_manager
        accumulated_data: dict[str, Any] = {}
        # 紧急压缩阶梯：驱动循环级状态，跨 rollback 存活
        emergency_halvings = 0

        while self._iteration_state.should_continue:
            # 0. compact 触发——位于BEFORE_ACTION hook 之前：
            # compact 回合不执行任何 agent 级HookPoint。
            # 命中后执行 compact 例程替代本轮 _action：
            # 不排空消息队列、不计 max_iterations、不 advance。
            if cm.check_compact_trigger():
                trigger_source = "manual" if cm.compact_requested else "threshold"
                await self._run_compact_round(trigger_source)
                continue

            # 1. BEFORE_ACTION hook（drive_loop 级）
            before_ctx = self._build_hook_context(accumulated_data)
            hook_result = await self._run_hooks(HookPoint.BEFORE_ACTION, before_ctx)
            if hook_result is not None:
                if hook_result.modified_context:
                    accumulated_data.update(hook_result.modified_context)
                if not hook_result.should_continue:
                    if hook_result.route_to:
                        # 路由到指定 ability（如 end_task）
                        await self._execute_routed_ability(
                            accumulated_data,
                            hook_result.route_to,
                            delivery_context=delivery_context,
                        )
                    break

            # 2. 开始事务
            cm.begin_iteration()

            # 在迭代内注入新消息（从队列排空）
            # 保存本迭代排空的消息，用于回滚时重新入队，防止消息丢失
            iteration_drained_messages: list[ChatMessage] = []
            new_messages = self._drain_message_queue()
            if new_messages:
                iteration_drained_messages.extend(new_messages)
                cm.add_messages(new_messages)

            try:
                # 3. 执行 _action（包含 LLM 调用 + tool 执行）
                action_output = await self._action(accumulated_data)
                action_results = action_output["results"]
                llm_meta_for_node = action_output.get("llm_metadata", {})

                # 4. 提交事务
                ability_names = (
                    [r.get("ability_name", "unknown") for r in action_results]
                    if action_results
                    else ["no_action"]
                )
                node_metadata = dict(llm_meta_for_node)
                if delivery_context:
                    # 一次 receive 可能跨多个 tool-call 迭代；只有首节点含
                    # 用户 ChatMessage。把投递归属写入每个节点，确保最终
                    # conversation 节点仍可稳定投影回原 Room。
                    node_metadata["delivery_context"] = dict(delivery_context)
                # commit 前压缩决策 seam：仅在配置 compact_threshold 时产出
                # 记录（None 时保持节点 metadata 与现状 bit 级一致）。
                # 决策随节点提交落链；rollback 无节点，决策自然消失。
                # needs_compaction 仅作审计与事件/UI 记录，触发以驱动循环
                # 开头的锚点活体复评（check_compact_trigger）为准。
                compaction_decision = cm.evaluate_compaction_decision()
                if compaction_decision is not None:
                    node_metadata["compaction_decision"] = compaction_decision
                node = await cm.commit_iteration_async(
                    ability_names=ability_names,
                    action_results=action_results,
                    llm_metadata=node_metadata or None,
                )

                # ────── 发布 ActionChainUpdated 事件 ──────
                # NOTE: 性能优化点 — 当前传输完整序列化 node，
                # 后续可改为仅传输 delta 数据以减少序列化/反序列化开销。
                from ghrah.context.persistence.serialization import (
                    serialize_branch,
                    serialize_node,
                )

                active_branch = cm.get_active_session_branch()
                await self._event_publisher.publish(
                    ActionChainUpdatedEvent(
                        agent_name=self.config.name,
                        node=serialize_node(node),
                        branch=serialize_branch(active_branch),
                    )
                )
                # ────── 结束 ──────

                # 4.5 累加 token 用量到 accumulated_data（读取侧 .get(k, 0) 兼容旧节点）
                current_usage = node.metadata.get("token_usage", {})
                if current_usage:
                    cumulative = dict(
                        accumulated_data.get(
                            "cumulative_token_usage",
                            {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                                "cache_read_tokens": 0,
                                "cache_write_tokens": 0,
                            },
                        )
                    )
                    cumulative["input_tokens"] += current_usage.get("input_tokens", 0)
                    cumulative["output_tokens"] += current_usage.get("output_tokens", 0)
                    cumulative["total_tokens"] += current_usage.get("total_tokens", 0)
                    cumulative["cache_read_tokens"] += current_usage.get("cache_read_tokens", 0)
                    cumulative["cache_write_tokens"] += current_usage.get("cache_write_tokens", 0)
                    accumulated_data["cumulative_token_usage"] = cumulative
            except Exception as e:
                # ────── 发布 AgentError 事件 ──────
                await self._event_publisher.publish(
                    AgentErrorEvent(
                        agent_name=self.config.name,
                        error=str(e),
                    )
                )
                # ────── 结束 ──────

                if cm.in_iteration:
                    cm.rollback_iteration(e)
                    # 未提交输入重新排队；已持久提交后事件失败不能重新注入。
                    for msg in iteration_drained_messages:
                        if get_iteration_committer(cm) is None or not msg.metadata.get(
                            "delivery_id"
                        ):
                            await self._message_queue.put(msg)

                # 紧急压缩阶梯：厂商上下文超限（保守类别识别）且仍有减半
                # 余量时，不重抛——进紧急 compact 回合（trigger_source=
                # "emergency"），下一迭代自然重试。每次触发都是显式信号
                # （warning + emergency 标记）。
                # 窗口回填优先：报文含真实窗口数字时以厂商口径为真值
                # 校正预算；校正后仍超限（或解析不到数字）回落减半阶梯。
                if (
                    _is_context_limit_error(e)
                    and cm.window_manager is not None
                    and emergency_halvings < _MAX_EMERGENCY_HALVINGS
                ):
                    vendor_window = parse_vendor_window_tokens(str(e))
                    if vendor_window is not None and vendor_window != cm.window_manager.max_tokens:
                        cm.window_manager.correct_budget_from_vendor(vendor_window)
                        budget_override = vendor_window
                    elif vendor_window is not None:
                        # 预算已与厂商窗口一致仍超限 → 输出挤压所致，减半
                        emergency_halvings += 1
                        budget_override = cm.window_manager.max_tokens // 2**emergency_halvings
                        logger.warning(
                            "ActorAgent[%s]: vendor context limit exceeded at corrected "
                            "budget — emergency compact (halving %d/4, budget=%d)",
                            self.config.name,
                            emergency_halvings,
                            budget_override,
                        )
                    else:
                        emergency_halvings += 1
                        budget_override = cm.window_manager.max_tokens // 2**emergency_halvings
                        logger.warning(
                            "ActorAgent[%s]: vendor context limit exceeded — emergency compact "
                            "(halving %d/4, budget=%d)",
                            self.config.name,
                            emergency_halvings,
                            budget_override,
                        )
                    if get_iteration_committer(cm) is not None:
                        # 内部压缩重试仍属于同一次投递；回滚后的持久输入需重新入队。
                        for msg in iteration_drained_messages:
                            if msg.metadata.get("delivery_id"):
                                await self._message_queue.put(msg)
                    await self._run_compact_round("emergency", budget_override=budget_override)
                    continue

                error_ctx = self._build_hook_context(accumulated_data)
                await self._run_hooks(HookPoint.ON_ERROR, error_ctx)
                raise AgentError(self.config.name, f"Action failed: {e}") from e
            finally:
                # 兜底：CancelledError 等 BaseException 不进上方 except Exception，
                # 逃逸前强制收尾迭代事务，防止 _in_iteration 残留导致
                # 下一次 begin_iteration 永久失败（ensure_iteration_closed 幂等，
                # 正常 commit/rollback 路径为 no-op）。
                cm.ensure_iteration_closed()

            # 5. AFTER_ACTION hook（drive_loop 级）
            # 从 action_results 提取最后一个 ActionResult 作为 last_action_result
            last_ability_name = ""
            if action_results:
                last_entry = action_results[-1]
                self._iteration_state.last_action_result = last_entry.get("action_result")
                last_ability_name = last_entry.get("ability_name", "")
            else:
                self._iteration_state.last_action_result = None

            after_ctx = self._build_hook_context(accumulated_data, ability_name=last_ability_name)
            hook_result = await self._run_hooks(HookPoint.AFTER_ACTION, after_ctx)
            if hook_result is not None:
                if hook_result.modified_context:
                    accumulated_data.update(hook_result.modified_context)
                if hook_result.route_to:
                    self._iteration_state.pending_route = hook_result.route_to
                    self._iteration_state.advance()
                    continue
                if not hook_result.should_continue:
                    break

            # 6. 检查最大迭代
            iter_state = self._iteration_state
            if (
                not iter_state.is_unlimited
                and iter_state.iteration + 1 >= iter_state.max_iterations
            ):
                max_ctx = self._build_hook_context(accumulated_data)
                max_hook_result = await self._run_hooks(HookPoint.ON_MAX_ITERATIONS, max_ctx)
                if max_hook_result is not None and max_hook_result.route_to:
                    await self._execute_routed_ability(
                        accumulated_data,
                        max_hook_result.route_to,
                        delivery_context=delivery_context,
                    )
                break

            self._iteration_state.advance()

    async def _run_compact_round(
        self, trigger_source: str, budget_override: int | None = None
    ) -> dict[str, Any]:
        """执行一轮链上 compact 回合（ghrah.builtin 综合方法）。

        阈值触发（驱动循环触发门）、手动（request_compact 空闲路径）、
        紧急（厂商超限阶梯）三入口共用本例程：分割 → 折叠 → LLM 摘要 →
        快照拼接 → 自检 → 专用路径提交 → 发布事件。

        本回合不排空消息队列、不计 max_iterations、不执行 agent 级
        HookPoint；摘要 LLM 用量单记节点 metadata.compaction.usage，
        不污染主循环锚点。LLM 摘要失败降级为确定性折叠视图提交
        ——compact 回合必须总能产出节点。

        Args:
            trigger_source: 触发来源 "threshold" | "manual" | "emergency"
            budget_override: 紧急阶梯的减半预算（None 用声明预算）

        Returns:
            回执 dict：executed=False 时含 reason（"empty_window" 振荡
            防护——窗外无节点，标志已清）；executed=True 时含 node_id、
            degraded、post_check
        """
        from ghrah.context.compact import (
            COMPACT_DEGRADED_PREAMBLE,
            COMPACT_PREAMBLE_TEMPLATE,
            COMPACT_SUMMARY_PROMPT,
            assemble_snapshot,
            collect_window_messages,
            fold_long_tool_outputs,
            post_check_snapshot,
            split_compact_windows,
            truncate_summary_input,
        )
        from ghrah.context.persistence.serialization import serialize_branch, serialize_node
        from ghrah.context.strategies.llm_summary import format_messages_for_summary

        cm = self._context_manager
        status = cm.get_window_status()
        keep_recent = status["compact_keep_recent"]
        estimator = cm.token_estimator

        # 手动标志在此消费（含连续请求合并语义）；窗外为空时同样视为
        # 已消费——否则标志残留会导致每轮进回合再退出，振荡
        manual_requested = cm.compact_requested

        windows = split_compact_windows(cm.get_history(), keep_recent)
        if windows is None:
            cm.consume_compact_request()
            logger.info(
                "ActorAgent[%s]: compact round skipped — nothing outside keep window",
                self.config.name,
            )
            if trigger_source == "threshold":
                # 振荡防护：锚点仍 >= trigger 但窗外已空，抑制阈值触发
                # 直到下一个真实锚点重新武装（手动/紧急不受否决）
                cm.skip_compact_until_reanchor()
            return {"executed": False, "reason": "empty_window", "merged": not manual_requested}
        summary_nodes, kept_nodes = windows

        summary_messages, kept_messages = collect_window_messages(summary_nodes, kept_nodes)

        message_factory = cm.message_factory
        if message_factory is None:
            from ghrah.chat.factory import ChatMessageFactory

            message_factory = ChatMessageFactory()

        window_config = self.config.window
        fold_max_length = window_config.tool_call_max_length if window_config is not None else 500
        summary_messages = fold_long_tool_outputs(
            summary_messages, fold_max_length, message_factory
        )
        kept_messages = fold_long_tool_outputs(kept_messages, fold_max_length, message_factory)

        budget = budget_override
        if budget is None:
            budget = status["budget_tokens"]

        # 摘要输入体积安全：折叠后估算超 budget×2 → 渐进丢最旧并标记
        summary_messages, truncated_input = truncate_summary_input(
            summary_messages, budget, estimator
        )

        # LLM 摘要：agent 自有 LLM；失败降级为确定性折叠视图
        summary_text: str | None = None
        summary_usage: dict[str, int] | None = None
        degraded = False
        try:
            llm = await self._ensure_llm()
            summary_prompt_messages = [
                message_factory.create_message(role="system", text=COMPACT_SUMMARY_PROMPT),
                message_factory.create_message(
                    role="user", text=format_messages_for_summary(summary_messages)
                ),
            ]
            response = await llm.generate(summary_prompt_messages)
            text = (getattr(response, "text", "") or "").strip()
            if text:
                summary_text = text
                usage = getattr(response, "token_usage", None)
                if usage is not None:
                    summary_usage = {
                        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                    }
            else:
                degraded = True
        except Exception:
            logger.exception(
                "ActorAgent[%s]: compact summary LLM failed — committing folded view",
                self.config.name,
            )
            degraded = True

        summarized_range = [summary_nodes[0].id, summary_nodes[-1].id]

        if summary_text is not None:
            preamble_text = COMPACT_PREAMBLE_TEMPLATE.format(
                summarized_range=f"{summarized_range[0]}..{summarized_range[1]}"
            )
            snapshot = assemble_snapshot(
                cm.get_active_session().system_prompt,
                preamble_text,
                summary_text,
                kept_messages,
                message_factory,
            )
        else:
            # 降级路径：折叠视图原样进快照（保留窗照旧），必须产出节点
            snapshot = assemble_snapshot(
                cm.get_active_session().system_prompt,
                COMPACT_DEGRADED_PREAMBLE,
                None,
                [*summary_messages, *kept_messages],
                message_factory,
            )

        snapshot, post_check = post_check_snapshot(snapshot, budget, message_factory, estimator)
        if post_check == "over_budget":
            logger.warning(
                "ActorAgent[%s]: compact snapshot still over budget after self-check "
                "(pathological config: system + kept rounds > budget)",
                self.config.name,
            )

        metadata: dict[str, Any] = {
            "trigger_source": trigger_source,
            "method": "ghrah.builtin",
            "summarized_range": summarized_range,
            "kept_node_ids": [node.id for node in kept_nodes],
            "kept_recent_nodes": keep_recent,
            "tokens_before": status["occupied_tokens"],
            "tokens_after": estimator.estimate_messages(snapshot),
            "post_check": post_check,
            "degraded": degraded,
            "truncated_summary_input": truncated_input,
        }
        if summary_usage is not None:
            metadata["usage"] = summary_usage

        node = await cm.commit_compact_node_async(snapshot, metadata)
        cm.consume_compact_request()
        active_branch = cm.get_active_session_branch()
        await self._event_publisher.publish(
            ActionChainUpdatedEvent(
                agent_name=self.config.name,
                node=serialize_node(node),
                branch=serialize_branch(active_branch),
            )
        )

        return {
            "executed": True,
            "node_id": node.id,
            "degraded": degraded,
            "post_check": post_check,
        }

    async def _publish_context_usage(self, phase: str, real_usage: Any | None = None) -> None:
        """发布上下文占用状态事件（fire-and-forget，失败不阻断主循环）。

        pre_call：占用锚点口径（可能为 None），仅配置 window 的 agent 发布；
        post_call：真实 usage 唯一权威口径（occupied_tokens = 本次真实
        input_tokens），所有 agent 发布。

        Args:
            phase: "pre_call" | "post_call"
            real_usage: 本次调用首响应的 TokenUsage（仅 post_call 提供）
        """
        cm = self._context_manager
        try:
            status = cm.get_window_status()
            if phase == "post_call":
                occupied = real_usage.input_tokens if real_usage is not None else None
                basis = "real"
                real_input = real_usage.input_tokens if real_usage is not None else None
                real_output = real_usage.output_tokens if real_usage is not None else None
                real_cache_read = real_usage.cache_read_tokens if real_usage is not None else None
                real_cache_write = real_usage.cache_write_tokens if real_usage is not None else None
            else:
                occupied = status["occupied_tokens"]
                basis = status["basis"]
                real_input = None
                real_output = None
                real_cache_read = None
                real_cache_write = None
            await self._event_publisher.publish(
                ContextUsageUpdatedEvent(
                    agent_name=self.config.name,
                    phase=phase,
                    occupied_tokens=occupied,
                    basis=basis,
                    budget_tokens=status["budget_tokens"],
                    budget_source=status["budget_source"],
                    compact_threshold=status["compact_threshold"],
                    real_input_tokens=real_input,
                    real_output_tokens=real_output,
                    real_cache_read_tokens=real_cache_read,
                    real_cache_write_tokens=real_cache_write,
                    compaction=cm.evaluate_compaction_decision(),
                    iteration=self._iteration_state.iteration,
                )
            )
        except Exception as e:
            logger.warning(
                "ActorAgent[%s]: failed to publish context usage event (%s): %s",
                self.config.name,
                phase,
                e,
            )

    async def _action(self, accumulated_data: dict[str, Any]) -> dict[str, Any]:
        """执行一次 action：调用 LLM → 解析响应 → 执行 abilities。

        多个 tool_calls 使用 asyncio.gather 并行执行，

        Args:
            accumulated_data: 累积数据（跨迭代传递的中间结果）

        Returns:
            dict 包含:
                - "results": list[dict] — 每个 dict 包含 "ability_name" 和 "action_result"
                - "llm_metadata": dict — LLM 响应元数据（token_usage、response_metadata 等）
        """
        cm = self._context_manager
        llm = await self._ensure_llm()

        # 1. PRE_LLM_CALL hook（action 级）
        pre_ctx = self._build_hook_context(accumulated_data)
        hook_result = await self._run_hooks(HookPoint.PRE_LLM_CALL, pre_ctx)
        if hook_result is not None:
            if hook_result.modified_context:
                accumulated_data.update(hook_result.modified_context)

        # 2. 调用 LLM
        if self.config.window is not None:
            await self._publish_context_usage("pre_call")
        messages = await cm.get_llm_messages()
        llm_response: LLMResponseProtocol = await llm.generate(messages)
        llm_responses: list[LLMResponseProtocol] = [llm_response]

        # 某些 thinking 模型偶发以 finish_reason=stop 结束，但只返回
        # reasoning_content，没有正文或 tool call。reasoning 不能直接投影给用户，
        # 因此用仅对本次请求生效的协议提示安全补全一次；首次空响应不写入消息
        # 历史，避免形成无法展示的幽灵 AI 消息。
        if not llm_response.tool_calls and not llm_response.text.strip():
            logger.warning(
                "ActorAgent[%s]: LLM returned no visible content; retrying once",
                self.config.name,
            )
            retry_messages = list(messages)
            insert_at = 0
            while insert_at < len(retry_messages) and retry_messages[insert_at].role == "system":
                insert_at += 1
            retry_messages.insert(
                insert_at,
                ChatMessage.system(
                    _VISIBLE_RESPONSE_RETRY_PROMPT,
                    source="system:runtime",
                ),
            )
            llm_response = await llm.generate(retry_messages)
            llm_responses.append(llm_response)

        # 2.5 提取 LLM 响应 metadata
        from ghrah.chat.response import (
            extract_reasoning_content,
            extract_response_metadata,
            extract_token_usage,
        )

        token_usages = [extract_token_usage(response) for response in llm_responses]
        cot_content = extract_reasoning_content(llm_response)
        resp_meta = extract_response_metadata(llm_response)

        # CoT 写入 accumulated_data，供 Ability/Hook 使用
        if cot_content:
            accumulated_data["cot_content"] = cot_content

        # 构建 llm_metadata dict，供 commit_iteration 使用
        llm_meta: dict[str, Any] = {}
        used = [usage for usage in token_usages if usage is not None]
        if used:
            llm_meta["token_usage"] = {
                "input_tokens": sum(usage.input_tokens for usage in used),
                "output_tokens": sum(usage.output_tokens for usage in used),
                "total_tokens": sum(usage.total_tokens for usage in used),
                "cache_read_tokens": sum(usage.cache_read_tokens for usage in used),
                "cache_write_tokens": sum(usage.cache_write_tokens for usage in used),
            }
        if resp_meta:
            llm_meta["response_metadata"] = resp_meta
        if len(llm_responses) > 1:
            llm_meta["visible_response_retry_count"] = len(llm_responses) - 1

        # 链上 compact 决策锚点：取首响应 input_tokens（重试响应含注入的
        # 协议提示，不再计量纯上下文占用）。无 usage 时沿用上一锚点，
        # 从未有过锚点则不触发决策，交发送侧安全阀兜底。
        first_usage = token_usages[0] if token_usages else None
        if first_usage is not None:
            cm.record_real_usage(first_usage)

        # 上下文占用 post_call 事件（所有 agent，真实值口径）
        await self._publish_context_usage("post_call", real_usage=first_usage)

        # 3. 将 AI 响应添加到消息历史
        cm.add_messages([llm_response.to_chat_message(source=f"agent:{self.config.name}")])

        # 4. POST_LLM_CALL hook（action 级）
        post_ctx = self._build_hook_context(accumulated_data)
        hook_result = await self._run_hooks(HookPoint.POST_LLM_CALL, post_ctx)
        if hook_result is not None:
            if hook_result.modified_context:
                accumulated_data.update(hook_result.modified_context)

        # 5. 解析 LLM 响应
        tool_calls = llm_response.tool_calls
        response_content = llm_response.text or ""
        if not tool_calls and not response_content.strip():
            # 两次调用都没有正文时返回明确、无思考泄露的可展示结果，保证
            # ConversationAbility 与 RoomFilter 不会把该轮误记为空成功。
            response_content = _EMPTY_VISIBLE_RESPONSE

        results: list[dict] = []

        # 本轮 LLM 可见文本 → accumulated_data["llm_response"]（含 tool_call 轮）。
        # 语义恒为"当前迭代的正文"，供 conversation / end_task 这类回合收尾能力
        # 取用：toolcall 模式下 end_task 必须拿到模型本轮写的终答，而不是上一个
        # 工具结果（查询类能力的结果带 content/response，会被误当最终输出）。
        accumulated_data["llm_response"] = response_content

        if not tool_calls:
            # 纯文本响应 → conversation ability
            conversation_ability = self._abilities.get("conversation")
            if conversation_ability:
                ability_ctx = self._build_ability_context("conversation", {}, accumulated_data)
                # 委托给 AbilityExecutor 执行（包含 PRE/POST_EXECUTE Hook 和 HITL 处理）
                action_result = await self._ability_executor.execute_ability(
                    conversation_ability, ability_ctx
                )
                results.append({"ability_name": "conversation", "action_result": action_result})
            else:
                # 缺 conversation 时纯文本轮不产生任何 action 结果：节点记 no_action，
                # 且 ConversationDoneHook 不存在 → 驱动循环不会因纯文本而收尾。
                logger.debug(
                    "ActorAgent[%s] text-only response (%d chars) but no 'conversation' ability "
                    "registered: this iteration records no_action and the loop keeps going",
                    self.config.name,
                    len(response_content),
                )
        else:
            # 有 tool_calls → 委托给 AbilityExecutor 执行
            logger.debug(
                "ActorAgent[%s] %d tool_call(s): %s",
                self.config.name,
                len(tool_calls),
                ", ".join(getattr(tc, "name", "?") for tc in tool_calls),
            )
            results = await self._ability_executor.execute_tool_calls(
                tool_calls=tool_calls,
                abilities=self._abilities,
                accumulated_data=accumulated_data,
                context_manager=cm,
                last_action_result=self._iteration_state.last_action_result,
            )

            if any(entry["action_result"].data.get("await_reply") for entry in results):
                # 同步等待必须在准备消息与执行状态同事务提交后发生。
                from ghrah.context.persistence.serialization import serialize_branch, serialize_node

                delivery_context = {
                    k: accumulated_data[k]
                    for k in ("room_id", "project_id", "agent_id")
                    if k in accumulated_data
                }
                if get_iteration_committer(cm) is not None:
                    delivery_context["message_manager"] = True
                node = await cm.commit_iteration_async(
                    ability_names=[entry["ability_name"] for entry in results],
                    action_results=results,
                    llm_metadata={
                        **llm_meta,
                        "pending_tool_replies": True,
                        "pending_tool_call_ids": [
                            entry.get("tool_call_id", "") for entry in results
                        ],
                        "delivery_context": delivery_context,
                    },
                )
                await self._event_publisher.publish(
                    ActionChainUpdatedEvent(
                        agent_name=self.config.name,
                        node=serialize_node(node),
                        branch=serialize_branch(cm.get_active_session_branch()),
                    )
                )
                usage = llm_meta.get("token_usage", {})
                if usage:
                    cumulative = dict(accumulated_data.get("cumulative_token_usage", {}))
                    for key, value in usage.items():
                        cumulative[key] = cumulative.get(key, 0) + value
                    accumulated_data["cumulative_token_usage"] = cumulative
                # 模型费用已由等待节点持久记录；结果节点不得再次记账。
                llm_meta = {}
                cm.begin_iteration()
                await self._await_tool_results(results)
            self._add_tool_results(results)

        outcomes: list[str] = []
        for entry in results:
            outcome = getattr(entry.get("action_result"), "outcome", None)
            outcomes.append(f"{entry.get('ability_name', '?')}={getattr(outcome, 'value', '?')}")
        logger.debug(
            "ActorAgent[%s] action produced %d result(s): %s",
            self.config.name,
            len(results),
            ", ".join(outcomes) or "none",
        )
        return {"results": results, "llm_metadata": llm_meta}

    async def _await_tool_results(self, results: list[dict[str, Any]]) -> None:
        """从持久回复补齐同步工具结果，不重新执行此前已提交的工具。"""
        manager = self._supervisor.message_manager
        for entry in results:
            result = entry["action_result"]
            pending = result.data.get("await_reply")
            if pending is None:
                continue
            try:
                replies = await manager.wait_inline(
                    pending["message_id"],
                    timeout=pending["timeout"],
                    tolerate_errors=pending["mode"] == "broadcast",
                )
                data = {k: v for k, v in result.data.items() if k != "await_reply"}
                if pending["mode"] == "broadcast":
                    data["responses"] = [
                        {
                            "responder": data.get("recipient_names", {}).get(
                                reply.sender_id, reply.sender_id
                            ),
                            "content": reply.content,
                        }
                        for reply in replies
                    ]
                    data["recipient_ids"] = data.get("recipients", [])
                    data["recipients"] = [item["responder"] for item in data["responses"]]
                    data["agent_count"] = len(data["responses"])
                    data["errors"] = [
                        {"recipient_id": d.recipient_id, "error": d.error or d.blocked_reason}
                        for d in await manager.get_deliveries(pending["message_id"])
                        if d.state.value != "completed"
                    ]
                else:
                    data["response"] = replies[0].content
                data.update(status="completed", mode=pending["mode"])
                entry["action_result"] = ActionResult(outcome=ActionOutcome.SUCCESS, data=data)
            except Exception as exc:
                entry["action_result"] = ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={
                        "error": str(exc) or type(exc).__name__,
                        "message_id": pending["message_id"],
                    },
                )
            finish = getattr(self._ability_executor, "finish_deferred", None)
            if callable(finish):
                context = self._build_ability_context(
                    entry["ability_name"], pending.get("tool_args", {}), {}
                )
                context.invocation = AbilityInvocation(
                    entry["ability_name"], context.tool_args, entry.get("tool_call_id", "")
                )
                await finish(pending["message_id"], context, entry["action_result"])

    def _add_tool_results(self, results: list[dict[str, Any]]) -> None:
        """将已完成工具结果加入本轮上下文。"""
        messages = []
        for entry in results:
            call_id = entry.get("tool_call_id")
            if not call_id:
                continue
            result = entry["action_result"]
            success = result.outcome == ActionOutcome.SUCCESS
            error = None if success else str(result.data.get("error", "Unknown error"))
            messages.append(
                ChatMessage.tool(
                    tool_call_id=call_id,
                    content=str(result.data) if success else f"Error: {error}",
                    success=success,
                    error=error,
                    source=f"agent:{self.config.name}",
                )
            )
        if messages:
            self._context_manager.add_messages(messages)

    async def _restore_pending_tools(self) -> None:
        """恢复等待回复的检查点，补工具结果节点后再开始下一次模型调用。"""
        cm = self._context_manager
        head = cm.active_head
        if not head.metadata.get("pending_tool_replies"):
            return
        results = copy.deepcopy(head.action_results)
        call_ids = head.metadata.get("pending_tool_call_ids", [])
        for entry, call_id in zip(results, call_ids, strict=True):
            entry["tool_call_id"] = call_id
        cm.begin_iteration()
        try:
            await self._await_tool_results(results)
            self._add_tool_results(results)
            node = await cm.commit_iteration_async(
                ability_names=[entry["ability_name"] for entry in results],
                action_results=results,
                llm_metadata={"delivery_context": head.metadata.get("delivery_context", {})},
            )
            from ghrah.context.persistence.serialization import serialize_branch, serialize_node

            await self._event_publisher.publish(
                ActionChainUpdatedEvent(
                    agent_name=self.config.name,
                    node=serialize_node(node),
                    branch=serialize_branch(cm.get_active_session_branch()),
                )
            )
        finally:
            cm.ensure_iteration_closed()

    async def _execute_routed_ability(
        self,
        accumulated_data: dict[str, Any],
        ability_name: str,
        *,
        delivery_context: dict[str, Any] | None = None,
    ) -> None:
        """执行路由目标的 ability（用于 BEFORE_ACTION 和 ON_MAX_ITERATIONS 的强制路由）。"""
        cm = self._context_manager
        ability = self._abilities.get(ability_name)
        if ability is None:
            logger.warning(f"Route target ability '{ability_name}' not found")
            return

        cm.begin_iteration()
        try:
            logger.debug(
                "ActorAgent[%s] executing routed ability '%s'",
                self.config.name,
                ability_name,
            )
            ctx = self._build_ability_context(ability_name, {}, accumulated_data)
            action_result = await ability.execute(ctx)
            logger.debug(
                "ActorAgent[%s] routed ability '%s' finished with %s",
                self.config.name,
                ability_name,
                getattr(action_result.outcome, "value", action_result.outcome),
            )
            self._iteration_state.last_action_result = action_result
            await cm.commit_iteration_async(
                ability_names=[ability_name],
                action_results=[{"ability_name": ability_name, "action_result": action_result}],
                llm_metadata=(
                    {"delivery_context": dict(delivery_context)} if delivery_context else None
                ),
            )
        except Exception as e:
            if cm.in_iteration:
                cm.rollback_iteration(e)
            raise
        finally:
            # 兜底：BaseException 逃逸时强制收尾迭代事务（同 _drive_loop）
            cm.ensure_iteration_closed()

    # ----------------------------------------------------------------
    # Hook 运行机制
    # ----------------------------------------------------------------

    async def _run_hooks(
        self,
        point: HookPoint,
        context: AbilityExecutionContext,
        result: ActionResult | None = None,
    ) -> HookResult | None:
        """运行所有注册的指定触发点的 hooks。

        Args:
            point: Hook 触发点
            context: 当前执行上下文
            result: ActionResult（POST_EXECUTE 时传入）

        Returns:
            合并后的 HookResult，如果没有 hook 触发则返回 None

        Raises:
            HookError: hook 执行出错时
        """
        hook_context = HookContext.from_ability_context(point, context, result)
        return await self._hook_runner.run(hook_context)

    # ----------------------------------------------------------------
    # 上下文构建和回复
    # ----------------------------------------------------------------

    def _build_hook_context(
        self,
        accumulated_data: dict[str, Any],
        ability_name: str = "",
    ) -> AbilityExecutionContext:
        """构建用于 Hook 的 AbilityExecutionContext。

        Args:
            accumulated_data: 累积数据
            ability_name: 最后执行的 ability 名称（供 AFTER_ACTION 等 hook 判断）

        Returns:
            初始化后的 AbilityExecutionContext
        """
        cm = self._context_manager
        return AbilityExecutionContext(
            current_ability_name=ability_name,
            agent_state=cm.get_current_state(),
            context_manager=cm,
            accumulated_data=copy.deepcopy(accumulated_data),
            last_action_result=self._iteration_state.last_action_result,
            supervisor=self._supervisor,
            agent_name=self.config.name,
            manifest_store=getattr(self._supervisor, "manifest_store", None),
            skill_provider=getattr(self._supervisor, "skill_provider", None),
        )

    def _build_ability_context(
        self,
        ability_name: str,
        tool_args: dict[str, Any],
        accumulated_data: dict[str, Any],
    ) -> AbilityExecutionContext:
        """构建用于 Ability 执行的 AbilityExecutionContext。

        Args:
            ability_name: ability 名称
            tool_args: 工具调用参数
            accumulated_data: 累积数据

        Returns:
            初始化后的 AbilityExecutionContext
        """
        cm = self._context_manager
        return AbilityExecutionContext(
            current_ability_name=ability_name,
            tool_args=tool_args,
            agent_state=cm.get_current_state(),
            context_manager=cm,
            accumulated_data={
                **copy.deepcopy(accumulated_data),
                "tool_args": tool_args,
            },
            last_action_result=self._iteration_state.last_action_result,
            supervisor=self._supervisor,
            agent_name=self.config.name,
            manifest_store=getattr(self._supervisor, "manifest_store", None),
            skill_provider=getattr(self._supervisor, "skill_provider", None),
        )

    def _build_response(self, original: AgentMessage) -> AgentMessage:
        """根据循环结果构建最终回复消息。

        Args:
            original: 原始输入消息

        Returns:
            回复消息
        """
        cm = self._context_manager

        # 从 last_action_result 中提取回复内容
        if self._iteration_state.last_action_result is not None:
            ar = self._iteration_state.last_action_result
            content = ar.data.get("response", ar.data.get("content", ""))
            if not content:
                # 尝试将整个 data 转为字符串
                content = str(ar.data) if ar.data else "Task completed"
        else:
            content = "No action executed"

        # 从 ContextManager 的最后一条 AI 消息中提取结构化 content_blocks
        # 以保留 reasoning/text 等类型区分
        content_blocks: list[dict] | None = None
        messages = cm.message_store.current_messages
        for msg in reversed(messages):
            if msg.role == "ai" and msg.content_blocks:
                content_blocks = [block_to_dict(b) for b in msg.content_blocks]
                break

        reply = AgentMessage.create_reply(
            original=original,
            content=content,
            msg_type=MessageType.RESULT,
            content_blocks=content_blocks,
        )
        self._message_history.append(reply)

        # LLM 响应已在 _action 中作为 AI ChatMessage 纳入本轮事务并随节点
        # commit。这里再追加一次会生成不属于任何 node delta 的幽灵消息，
        # 持久化/恢复后表现为上下文里每条最终回复重复两遍。

        logger.info(f"ActorAgent[{self.config.name}] replied: {content[:100]}...")
        return reply

    # ----------------------------------------------------------------
    # Session 管理
    # ----------------------------------------------------------------

    async def create_session(
        self,
        name: str | None = None,
        origin_session_id: str | None = None,
        origin_node_id: str | None = None,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActionSession:
        """创建独立 Root Session 并发布事件。"""
        session = self._context_manager.create_session(
            name=name,
            origin_session_id=origin_session_id,
            origin_node_id=origin_node_id,
            system_prompt=system_prompt,
            metadata=metadata,
        )
        await self._event_publisher.publish(
            SessionCreatedEvent(
                agent_name=self.config.name,
                session=self._session_info(session),
            )
        )
        logger.info("ActorAgent[%s] created session id=%s", self.config.name, session.session_id)
        return session

    async def activate_session(self, session_id: str) -> dict[str, Any]:
        """激活独立 Root Session 并完整恢复运行上下文。"""
        if self._runner.busy:
            raise RuntimeError("Cannot activate session while agent runner is busy")
        self._context_manager.activate_session(session_id)
        session = self._context_manager.get_active_session()
        info = self._session_info(session)
        await self._event_publisher.publish(
            SessionActivatedEvent(
                agent_name=self.config.name,
                session=info,
            )
        )
        logger.info("ActorAgent[%s] activated session id=%s", self.config.name, session_id)
        return info

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出独立 Root Session。"""
        return [self._session_info(session) for session in self._context_manager.list_sessions()]

    def list_sessions_result(self) -> dict[str, Any]:
        """列出 Session 并携带权威 active_session_id。"""
        return {
            "active_session_id": self._context_manager.active_session_id,
            "sessions": self.list_sessions(),
        }

    def _session_info(self, session: ActionSession) -> dict[str, Any]:
        """将 Session 领域对象转换为稳定 wire 结构。"""
        history = self._context_manager.get_history(session_id=session.session_id)
        return {
            "session_id": session.session_id,
            "agent_name": session.agent_name,
            "name": session.name,
            "root_node_id": session.root_node_id,
            "active_branch_id": session.active_branch_id,
            "lifecycle": session.lifecycle,
            "system_prompt": session.system_prompt,
            "origin_agent_name": session.origin_agent_name,
            "origin_session_id": session.origin_session_id,
            "origin_node_id": session.origin_node_id,
            "created_at": session.created_at.isoformat(),
            "metadata": session.metadata,
            "message_count": len(self._context_manager.get_messages(session_id=session.session_id)),
            "iteration_count": max((node.iteration for node in history), default=0),
        }

    @staticmethod
    def _branch_info(branch: ActionBranch) -> dict[str, Any]:
        """将 Branch 领域对象转换为稳定 wire 结构。"""
        return {
            "branch_id": branch.branch_id,
            "session_id": branch.session_id,
            "name": branch.name,
            "head_node_id": branch.head_node_id,
            "lifecycle": branch.lifecycle,
            "parent_branch_id": branch.parent_branch_id,
            "fork_point_node_id": branch.fork_point_node_id,
            "created_at": branch.created_at.isoformat(),
            "metadata": branch.metadata,
        }

    async def create_branch(
        self,
        session_id: str,
        name: str,
        from_node_id: str | None = None,
        parent_branch_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建同 Session Branch，不隐式激活。"""
        branch = self._context_manager.create_branch(
            session_id=session_id,
            name=name,
            from_node_id=from_node_id,
            parent_branch_id=parent_branch_id,
            metadata=metadata,
        )
        info = self._branch_info(branch)
        await self._event_publisher.publish(
            BranchCreatedEvent(agent_name=self.config.name, branch=info)
        )
        return info

    async def activate_branch(self, session_id: str, branch_id: str) -> dict[str, Any]:
        """激活当前 Session 的 Branch。"""
        self._context_manager.activate_branch(session_id, branch_id)
        branch = next(
            item
            for item in self._context_manager.list_branches(session_id)
            if item.branch_id == branch_id
        )
        info = self._branch_info(branch)
        await self._event_publisher.publish(
            BranchActivatedEvent(agent_name=self.config.name, branch=info)
        )
        return info

    def list_branches(self, session_id: str) -> list[dict[str, Any]]:
        """列出 Session 内 Branch。"""
        return [
            self._branch_info(branch) for branch in self._context_manager.list_branches(session_id)
        ]

    def list_branches_result(self, session_id: str) -> dict[str, Any]:
        """列出 Branch 并携带权威 active_branch_id。

        ``active_branch_id`` 是 Session 域必填不变量（每个 Session 必有 active
        Branch），与运行态 active_session 指针无关：非活跃 Session 也直读其
        自身的 ``active_branch_id``，不置空。
        """
        return {
            "session_id": session_id,
            "active_branch_id": self._context_manager.get_session(session_id).active_branch_id,
            "branches": self.list_branches(session_id),
        }

    async def archive_branch(self, session_id: str, branch_id: str) -> None:
        """归档非运行 Branch。"""
        self._context_manager.archive_branch(session_id, branch_id)
        await self._event_publisher.publish(
            BranchArchivedEvent(
                agent_name=self.config.name, session_id=session_id, branch_id=branch_id
            )
        )

    async def delete_branch(self, session_id: str, branch_id: str) -> None:
        """删除非运行 Branch（保留 provenance 墓碑）。"""
        self._context_manager.delete_branch(session_id, branch_id)
        await self._event_publisher.publish(
            BranchDeletedEvent(
                agent_name=self.config.name, session_id=session_id, branch_id=branch_id
            )
        )

    async def archive_session(self, session_id: str) -> None:
        """归档指定 session 并发布事件。

        将 session 状态标记为 archived。

        Args:
            session_id: 要归档的 session ID

        Raises:
            KeyError: session 不存在
        """
        self._context_manager.archive_session(session_id)

        await self._event_publisher.publish(
            SessionArchivedEvent(
                agent_name=self.config.name,
                session_id=session_id,
            )
        )

        logger.info(f"ActorAgent[{self.config.name}] archived session id={session_id}")

    async def delete_session(self, session_id: str) -> None:
        """删除指定 session 并发布事件。

        不能删除当前活跃的 session。

        Args:
            session_id: 要删除的 session ID

        Raises:
            KeyError: session 不存在
            ValueError: 试图删除当前活跃 session
        """
        self._context_manager.delete_session(session_id)

        await self._event_publisher.publish(
            SessionDeletedEvent(
                agent_name=self.config.name,
                session_id=session_id,
            )
        )

        logger.info(f"ActorAgent[{self.config.name}] deleted session id={session_id}")

    # ----------------------------------------------------------------
    # 消息 API
    # ----------------------------------------------------------------

    async def chat(self, content: str, sender: str = "user") -> str:
        """简化的对话接口。

        Args:
            content: 用户输入文本
            sender: 发送者标识

        Returns:
            AI 回复文本
        """
        message = AgentMessage(
            sender=sender,
            recipient=self.config.name,
            content=content,
            type=MessageType.CHAT,
        )
        reply = await self.receive(message)
        return reply.content

    def get_history(self) -> list[dict[str, Any]]:
        """获取消息历史。"""
        return [
            {
                "id": msg.id,
                "sender": msg.sender,
                "type": msg.type.value,
                "content": msg.content[:200],
                "timestamp": msg.timestamp,
            }
            for msg in self._message_history
        ]

    def get_state(self) -> dict[str, Any]:
        """获取 Agent 内部状态。"""
        return {
            "name": self.config.name,
            "initialized": self._initialized,
            "abilities": list(self._abilities.keys()),
            "bound_tools": len(self._bound_tools),
            "message_count": len(self._message_history),
            "state": self._context_manager.get_current_state(),
            "event_publisher_type": type(self._event_publisher).__name__,
            "ability_executor_type": type(self._ability_executor).__name__,
        }

    def get_context_usage_snapshot(self) -> dict[str, Any]:
        """组装上下文用量恢复快照（get_agent_info 回执数据源）。

        与 ContextUsageUpdatedPayload 同构（phase 恒为 post_call、basis
        为 anchor——同步快照即锚点口径），另附 cumulative 三值（成本口径
        权威累计，前端恢复时直置）。

        Returns:
            dict：context_usage 子对象；无任何真实调用记录时
            real_* 全为 None（前端跳过覆盖）
        """
        status = self._context_manager.get_window_status()
        return {
            "phase": "post_call",
            "occupied_tokens": status["occupied_tokens"],
            "basis": status["basis"],
            "budget_tokens": status["budget_tokens"],
            "budget_source": status["budget_source"],
            "compact_threshold": status["compact_threshold"],
            "real_input_tokens": status["last_real_input_tokens"],
            "real_output_tokens": status["last_real_output_tokens"],
            "real_cache_read_tokens": status["last_real_cache_read_tokens"],
            "real_cache_write_tokens": status["last_real_cache_write_tokens"],
            "cumulative_input_tokens": status["cumulative_input_tokens"],
            "cumulative_output_tokens": status["cumulative_output_tokens"],
            "cumulative_cache_read_tokens": status["cumulative_cache_read_tokens"],
        }

    def set_state(self, key: str, value: Any) -> None:
        """设置 Agent 内部状态。

        委托给 ContextManager.set_state()，适用于迭代外的状态设置。
        """
        self._context_manager.set_state(key, value)

    async def reset(self) -> dict[str, Any]:
        """重置 Agent 状态（清空对话历史，保留 LLM 客户端/abilities/持久化后端）。

        就地重置 ContextManager（新起默认 Session/main Branch 并激活），
        而非经 factory 重建：旧 Session 全部保留（可继续查看/激活）、
        sqlite 后端与连接复用、增量持久化在返回前完成。新 Session 的
        实体广播经既有 session_created/session_activated 事件路径。

        Returns:
            新激活 Session 的稳定 wire 信息（``_session_info`` 形态）。

        Raises:
            RuntimeError: 驱动循环进行中（迭代事务未提交前禁止切换运行 Head）。
        """
        if self._runner.busy or self._drive_active:
            raise RuntimeError(
                f"Cannot reset agent '{self.config.name}' while its drive loop is active"
            )

        self._message_history.clear()

        # 清空消息队列
        self._message_queue = asyncio.Queue()

        # 重置驱动循环状态（iteration/last_action_result/pending_route 归零，
        # max_iterations 从 config 重新同步——与 _drive_loop 初始化一致）
        self._iteration_state.max_iterations = self.config.max_iterations
        self._iteration_state.reset()

        # 就地重置 ContextManager：新默认 Session 激活 + 立即增量落库
        session = await self._context_manager.reset()

        # 重新写入所有 ability 的默认状态（一次性收集，避免多次 update_state）
        default_states: dict[str, dict[str, Any]] = {}
        for ability in self._abilities.values():
            default_state = ability.get_default_state()
            if default_state:
                default_states[ability.name] = copy.deepcopy(default_state)

        if default_states:
            self._context_manager.update_state(default_states)

        info = self._session_info(session)
        await self._event_publisher.publish(
            SessionCreatedEvent(agent_name=self.config.name, session=info)
        )
        await self._event_publisher.publish(
            SessionActivatedEvent(agent_name=self.config.name, session=info)
        )
        logger.info(f"ActorAgent[{self.config.name}] reset")
        return info

    async def send(self, target: str, content: str) -> AgentMessage:
        """向其他 Agent 发送消息。

        通过构造函数注入的 Supervisor handle 路由消息，
        无需每次调用传入 supervisor。

        Args:
            target: 目标 Agent 名称
            content: 消息内容

        Returns:
            目标 Agent 的回复消息

        Raises:
            AgentError: 如果未配置 Supervisor
        """
        if self._supervisor is None:
            raise AgentError(
                self.config.name,
                "No supervisor configured, cannot send message to other agents",
            )

        message = AgentMessage(
            sender=self.config.name,
            recipient=target,
            content=content,
            type=MessageType.COMMAND,
        )
        logger.info(f"ActorAgent[{self.config.name}] sending to {target}: {content[:100]}...")
        return await self._supervisor.route_message(message)
