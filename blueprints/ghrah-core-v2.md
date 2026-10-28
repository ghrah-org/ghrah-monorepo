## `ghrah-core` 蓝图 v2 — 类关系导向

> **设计哲学**: <!-- 由维护者填写 -->

---

### L0 基础类型层

零内部依赖的叶子模块，供所有上层模块引用。

```mermaid
classDiagram
    class ActionOutcome {
        <<enum>>
        SUCCESS
        FAILURE
        NEEDS_INPUT
        DELEGATE
    }
    class ActionResult {
        outcome: ActionOutcome
        data: dict
        next_action_hint: str | None
    }
    class TokenUsage {
        input_tokens: int
        output_tokens: int
        total_tokens: int
        +to_dict() dict
        +from_dict(data) TokenUsage
    }
    class WindowConfig {
        max_tokens: int
        strategies: list
        tool_call_max_length: int
        sliding_window_size: int
    }
    class ModelOverrides {
        temperature: float | None
        max_tokens: int | None
        top_p: float | None
        top_k: int | None
    }
    class ContextConfig {
        snapshot_interval: int
        auto_persist: bool
        persistence_type: str
        persistence_root_dir: str | None
        persistence_compress: bool
        persistence_run_id: str | None
        command_sender: CommandSender | None
        persistence_agent_name: str | None
    }
    class AgentConfig {
        name: str
        endpoint_name: str
        description: str
        system_prompt: str
        max_iterations: int
        communication_timeout: float
        resources: dict
        window: WindowConfig
        context: ContextConfig
        model_overrides: ModelOverrides
        workspace_root: str | None
    }

    AgentConfig --> WindowConfig
    AgentConfig --> ContextConfig
    AgentConfig --> ModelOverrides
    ActionResult --> ActionOutcome
```

**核心类说明**:

- **ActionOutcome** — 能力执行结果枚举，驱动 Agent 循环的分支决策
- **ActionResult** — 能力执行结果容器，`outcome` 决定后续流程，`data` 携带返回值，`next_action_hint` 可引导 Agent 下一轮行为
- **TokenUsage** — LLM 调用 token 消耗追踪，支持 dict 序列化
- **WindowConfig** — 上下文窗口压缩策略配置，定义 token 预算和策略链
- **ModelOverrides** — LLM 模型参数覆盖，用于 Manifest 层面对模型行为的微调
- **ContextConfig** — 上下文管理器配置，控制持久化行为和快照策略；`command_sender` 字段用于远程执行场景
- **AgentConfig** — Agent 框架顶层配置，聚合 `WindowConfig`、`ContextConfig`、`ModelOverrides`，是 Agent 构建的唯一起点

**层间关系**: 被 L1~L6 所有层依赖，是整个框架的类型地基。

---

### L1 核心协议与抽象层

定义框架的核心协议（结构化子类型）、事件系统、异常层次、HITL 机制、进程间消息。

```mermaid
classDiagram
    class EventPublisher {
        <<ABC>>
        +publish(event: CoreEvent)*
    }
    class NullEventPublisher {
        +publish(event: CoreEvent)
    }
    class ServerEventPublisher {
        +publish(event: CoreEvent)
        -_create_event_message(event)
    }
    EventPublisher <|-- NullEventPublisher
    EventPublisher <|-- ServerEventPublisher
    ServerEventPublisher --> EventBus

    class CoreEvent {
        <<dataclass>>
        event_type: CoreEventType
        agent_name: str
        data: dict
    }
    class HITLRequestEvent {
        ability_name: str
        tool_call: dict
        context: dict
    }
    class AgentErrorEvent {
        error: str
    }
    class AgentResponseEvent {
        content: str
        content_blocks: list
        message_type: str
        metadata: dict
    }
    class ActionChainUpdatedEvent {
        node: dict
    }
    CoreEvent <|-- HITLRequestEvent
    CoreEvent <|-- AgentErrorEvent
    CoreEvent <|-- AgentResponseEvent
    CoreEvent <|-- ActionChainUpdatedEvent

    class HITLFutureStore {
        +create_future(agent, ability, tool_call_id) Future
        +resolve_future(agent, ability, tool_call_id, result)
        +get_future(agent, ability, tool_call_id) Future
        +cancel_future(...)
        +cancel_all()
        +list_pending()
    }

    class Message {
        <<dataclass>>
        sender: str
        recipient: str
        content: str
        type: MessageType
        id: str
        metadata: dict
        content_blocks: list
        +to_chat_message() ChatMessage
        +create_reply(content, type) Message
    }
    class MessageType {
        <<enum>>
        CHAT COMMAND TOOL_CALL
        TOOL_RESULT RESULT ERROR BROADCAST
    }
    Message --> MessageType

    class ActorAgentError {
        <<root exception>>
    }
    class AgentError {
        agent_name: str
    }
    class LLMError {
        provider: str
    }
    class ToolError {
        tool_name: str
    }
    class AbilityError {
        ability_name: str
    }
    class RegistryError
    class RoutingError
    class CommunicationTimeoutError {
        sender: str
        recipient: str
        timeout: float
    }
    ActorAgentError <|-- AgentError
    ActorAgentError <|-- LLMError
    ActorAgentError <|-- ToolError
    ActorAgentError <|-- AbilityError
    ActorAgentError <|-- RegistryError
    ActorAgentError <|-- RoutingError
    ActorAgentError <|-- CommunicationTimeoutError

    class AbilityProtocol {
        <<Protocol>>
        +name: str*
        +execute()*
        +bind_tool()*
        +get_hooks()*
        +get_default_state()*
    }
    class ExecutorProtocol {
        <<Protocol>>
        +execute_ability()*
        +execute_tool_calls()*
        +update_hooks()*
        +update_event_publisher()*
        +receive_hitl_response()*
    }
    class LLMProtocol {
        <<Protocol>>
        +model: str*
        +generate()*
        +configure_tools()*
        +apply_model_overrides()*
    }
    class CommandSender {
        <<Protocol>>
        +send_command()*
    }
    class SupervisorProtocol {
        <<Protocol>>
        +list_agents()*
        +terminate_agent()*
        +spawn_agent()*
        +send()*
        +broadcast()*
        +get_agent_handle()*
    }
```

**核心类说明**:

- **EventPublisher / NullEventPublisher / ServerEventPublisher** — 事件发布抽象与两种实现：NullEventPublisher 用于本地/测试模式（静默丢弃），ServerEventPublisher 通过 EventBus 向 Observer 连接推送事件
- **CoreEvent 体系** — 框架统一事件模型，子类型覆盖 HITL 请求、Agent 错误/响应、上下文链更新、会话生命周期等场景
- **HITLFutureStore** — Human-in-the-Loop 核心机制，以 `(agent, ability, tool_call_id)` 三元组为 key 管理 asyncio.Future，支撑能力执行中的审批/拒绝流程
- **Message / MessageType** — Agent 间通信的统一消息协议，支持从 Message 转换为 ChatMessage（LLM 格式）和创建回复
- **ActorAgentError 异常树** — 框架级根异常，所有业务异常均派生自此类，便于上层统一捕获框架错误；关键子类携带上下文信息（如 `agent_name`、`provider`、`sender/recipient`）
- **Protocol 系列** — 核心解耦机制。`AbilityProtocol` 解耦 Agent 与具体 Ability 实现；`ExecutorProtocol` 解耦 Agent 循环与执行策略（本地/远程）；`LLMProtocol` 解耦 Agent 与 LLM 提供商；`CommandSender` 解耦能力执行与服务端通信；`SupervisorProtocol` 解耦能力上下文与 Supervisor 实现

**层间关系**: 依赖 L0（types）；被 L2~L6 依赖，是框架的"契约层"。

---

### L2 消息与 LLM 集成层

将 LLM 厂商差异封装为统一的消息模型和格式适配器。

```mermaid
classDiagram
    class ChatMessage {
        <<dataclass>>
        role: str
        content_blocks: list~ContentBlock~
        source: str
        metadata: dict
        +text: str
        +tool_calls: list
        +has_tool_calls: bool
        +reasoning: str
        +images: list
        +is_multimodal: bool
        +tool_results: list
        +find_blocks(type) list
        +to_dict() dict
        +from_dict(data) ChatMessage
        +system(text)$ ChatMessage
        +user(text)$ ChatMessage
        +ai(text)$ ChatMessage
        +tool(result)$ ChatMessage
    }

    class TextBlock { text: str }
    class ReasoningBlock { reasoning: str; incomplete: bool }
    class ImageBlock { url: str; base64: str; mime_type: str }
    class AudioBlock { data: str; mime_type: str }
    class FileBlock { url: str; base64: str; mime_type: str; filename: str }
    class ToolCallBlock { id: str; name: str; arguments: str }
    class ToolResultBlock { tool_call_id: str; name: str; content: str; success: bool; error: str }
    class ErrorBlock { error_type: str; message: str; details: dict }
    class ToolCallChunkBlock { index: int; id: str; name: str; arguments_chunk: str }

    ChatMessage --> TextBlock
    ChatMessage --> ReasoningBlock
    ChatMessage --> ImageBlock
    ChatMessage --> ToolCallBlock
    ChatMessage --> ToolResultBlock
    ChatMessage --> ErrorBlock

    class LLMResponse {
        <<dataclass>>
        content_blocks: list
        token_usage: TokenUsage
        response_metadata: dict
        raw: object
        +text: str
        +tool_calls: list
        +reasoning: str
        +to_chat_message() ChatMessage
    }

    class ChatFormat {
        <<ABC>>
        +model: str*
        +generate(messages, tools)*
        +configure_tools(abilities)
        +apply_model_overrides(overrides)
    }
    class OpenAIFormat {
        +generate(messages, tools)
        +_format_messages(messages)
        +_format_tools(abilities)
        +_parse_response(response)
    }
    class AnthropicFormat {
        +generate(messages, tools)
        +_format_messages(messages)
        +_format_tools(abilities)
        +_parse_response(response)
    }
    class DeepSeekFormat {
        +_format_single_message(msg)
    }
    ChatFormat <|-- OpenAIFormat
    ChatFormat <|-- AnthropicFormat
    OpenAIFormat <|-- DeepSeekFormat

    class LLMFactory {
        +create(config: AgentConfig)$ ChatFormat
    }
    LLMFactory --> ChatFormat : creates
    LLMFactory --> AgentConfig : reads

    class ChatMessageFactory {
        +create_message(role, blocks, source, metadata) ChatMessage
        +create_tool_result_block(tool_call_id, name, content, success) ToolResultBlock
    }
    ChatMessageFactory --> ChatMessage : creates
    ChatMessageFactory --> ToolResultBlock : creates
```

**核心类说明**:

- **ChatMessage** — 框架统一消息模型，以结构化的 `content_blocks` 列表替代纯文本，天然支持多模态和工具调用。提供 `system/user/ai/tool` 工厂方法和 `find_blocks` 类型查询
- **ContentBlock 系列** — 内容块类型联合体，每种块对应一种语义：文本、推理链、图像、音频、文件、工具调用、工具结果、错误、流式块。块通过 `type` 字段实现运行时多态
- **LLMResponse** — LLM 调用的统一响应容器，解耦厂商差异。持有 `content_blocks`、`token_usage`、`response_metadata`，可转换为 `ChatMessage` 继续上下文循环
- **ChatFormat / OpenAIFormat / AnthropicFormat / DeepSeekFormat** — 适配器模式，将 `ChatMessage` 列表转换为厂商 API 格式、将厂商响应解析为 `LLMResponse`。DeepSeek 继承 OpenAI 并覆盖推理内容提取
- **LLMFactory** — 从 `AgentConfig` 创建对应 `ChatFormat` 实例，懒加载厂商 SDK
- **ChatMessageFactory** — 默认 `MessageFactory` 协议实现，供 WindowManager 等模块构造消息和块

**层间关系**: 依赖 L0（TokenUsage、AgentConfig）和 L1（LLMProtocol）；被 L3（能力系统不需要直接依赖）、L4（上下文管理）和 L5（Agent 循环）依赖。

---

### L3 能力系统层

定义 Agent 的工具/能力抽象、执行策略、注册机制和 Hook 拦截。

```mermaid
classDiagram
    class Ability {
        <<ABC>>
        +name: str*
        +execute(context: AbilityExecutionContext)*
        +get_hooks()*
        +bind_tool(ability)
        +to_prompt_description() str
        +get_default_state() dict
    }

    class AbilityExecutionContext {
        <<dataclass>>
        current_ability_name: str
        tool_args: dict
        agent_state: dict
        context_manager: ContextManagerProtocol
        current_node_id: str
        supervisor: SupervisorProtocol
        agent_name: str
        accumulated_data: dict
        last_action_result: ActionResult | None
        +get_ability_state() dict
        +update_state(state)
        +update_global_state(state)
    }

    class AbilityExecutor {
        <<ABC>>
        +execute_ability(name, context)*
        +execute_tool_calls(tool_calls, context)*
        +run_hooks(hook_point, context)*
        +handle_hitl_hook_result(result)*
        +update_hooks(abilities)*
        +update_event_publisher(publisher)*
        +receive_hitl_response(agent, ability, tool_call_id, result)*
    }
    class LocalAbilityExecutor {
        -_abilities: dict
        -_hitl_store: HITLFutureStore
        -_event_publisher: EventPublisher
        +execute_ability(name, context)
        +execute_tool_calls(tool_calls, context)
        +run_hooks(hook_point, context)
    }
    class RemoteAbilityExecutor {
        -_command_sender: CommandSender
        +execute_ability(name, context)
        +execute_tool_calls(tool_calls, context)
        +resolve_ability_result(data) ActionResult
    }
    AbilityExecutor <|-- LocalAbilityExecutor
    AbilityExecutor <|-- RemoteAbilityExecutor
    LocalAbilityExecutor --> HITLFutureStore
    LocalAbilityExecutor --> EventPublisher
    RemoteAbilityExecutor --> CommandSender
    AbilityExecutor --> AbilityExecutionContext : creates
    AbilityExecutionContext --> ActionResult
    AbilityExecutionContext --> ContextManagerProtocol
    AbilityExecutionContext --> SupervisorProtocol

    class AbilityRegistry {
        <<class-level>>
        +register(name, cls)$
        +create(name, **kwargs)$
        +has(name)$
        +list_types()$
        +get_class(name)$
        +clear()$
    }

    class Hook {
        <<ABC>>
        +hook_point: HookPoint
        +should_trigger(context)*$
        +execute(context)*$
    }
    class HookPoint {
        <<enum>>
        BEFORE_ACTION AFTER_ACTION ON_ERROR
        ON_MAX_ITERATIONS PRE_LLM_CALL POST_LLM_CALL
        PRE_TOOL_EXECUTE POST_TOOL_EXECUTE
        PRE_EXECUTE POST_EXECUTE
    }
    class HookResult {
        should_continue: bool
        route_to: str | None
        modified_context: dict | None
        message: str | None
        requires_hitl: bool
        +continue_() HookResult
        +stop() HookResult
        +hitl() HookResult
        +route(agent) HookResult
        +merge(other) HookResult
    }
    Hook --> HookPoint
    Hook --> HookResult
    HookResult --> ActionResult : influences

    class BuiltinHookRegistry {
        <<class-level>>
        +register(name, cls)$
        +create(name, **kwargs)$
        +has(name)$
        +list_handlers()$
    }
```

**核心类说明**:

- **Ability** — 能力抽象基类，所有 Agent 工具必须实现 `execute`、`get_hooks` 接口。`bind_tool` 支持自引用以暴露 LLM tool schema；`get_default_state` 提供能力的初始状态
- **AbilityExecutionContext** — 能力执行的最小上下文，通过 Protocol 解耦：`ContextManagerProtocol` 提供状态管理，`SupervisorProtocol` 提供集群操作，不直接依赖具体实现
- **AbilityExecutor / LocalAbilityExecutor / RemoteAbilityExecutor** — 策略模式：`LocalAbilityExecutor` 在进程内执行能力并支持 HITL 审批流；`RemoteAbilityExecutor` 通过 `CommandSender` 将能力执行委托给 Subject 端
- **AbilityRegistry** — 进程级工厂注册表，将能力类型名映射到类，支持 `AgentManifest` 驱动的动态能力加载
- **Hook / HookPoint / HookResult** — 拦截器机制，`HookPoint` 定义 10 个触发点覆盖 Agent 循环的三层（action/LLM/tool），`HookResult` 通过 `continue_/stop/hitl/route` 控制执行流。这是框架扩展性的核心入口
- **BuiltinHookRegistry** — Hook 的类级注册表，与 AbilityRegistry 对称设计

**层间关系**: 依赖 L0（ActionResult）、L1（Protocol、HITL、Event、异常）；被 L5（Agent）直接依赖，L6（Manifest）通过 AbilityRegistry 间接使用。

---

### L4 上下文管理层

管理 Agent 的对话历史、状态、会话分支、窗口压缩和持久化——是 Agent 循环的"记忆系统"。

```mermaid
classDiagram
    class ContextNode {
        <<frozen dataclass>>
        id: str
        parent_id: str | None
        agent_name: str
        timestamp: float
        iteration: int
        ability_names: list
        agent_state: dict
        messages_delta: list | None
        messages_snapshot: list | None
        is_snapshot: bool
        action_results: list
        metadata: dict
        branch_name: str
        session_id: str | None
        +create_root(agent_name, system_prompt)$ ContextNode
    }

    class Session {
        <<dataclass>>
        session_id: str
        agent_name: str
        branch_name: str
        created_at: float
        parent_node_id: str | None
        parent_session_id: str | None
        system_prompt: str
        metadata: dict
        +is_root: bool
        +is_rebase: bool
    }

    class ActionChain {
        agent_name: str
        head: ContextNode | None
        active_branch: str
        branches: dict
        +init_chain(system_prompt)
        +commit_node(node)
        +fork(branch_name, system_prompt)
        +checkout(branch_name)
        +get_history() list
        +rebuild_from_nodes(nodes)$ ActionChain
    }
    ActionChain --> ContextNode : manages

    class StateManager {
        -_state: dict
        -_transaction_stack: list
        +current: dict
        +in_transaction: bool
        +begin_transaction()
        +apply_changes(changes)
        +commit()
        +rollback()
        +savepoint()
        +rollback_to_savepoint()
        +reset()
    }

    class MessageStore {
        -_messages: list
        -_snapshot_interval: int
        +current_messages: list
        +count: int
        +append(message)
        +extend(messages)
        +compute_delta_since_last_snapshot() list
        +compute_delta_since(iteration) list
        +should_snapshot() bool
        +take_snapshot()
        +rebuild_from(messages)
    }

    class WindowStrategy {
        <<ABC>>
        +name: str*
        +apply(messages, max_tokens)*
    }
    class TruncationStrategy {
        +apply(messages, max_tokens)
    }
    class SlidingWindowStrategy {
        window_size: int
        +apply(messages, max_tokens)
    }
    class ToolCallFoldStrategy {
        max_content_length: int
        +apply(messages, max_tokens)
    }
    class LLMSummaryStrategy {
        summary_prompt: str
        +apply(messages, max_tokens)
        +set_llm(llm)
    }
    WindowStrategy <|-- TruncationStrategy
    WindowStrategy <|-- SlidingWindowStrategy
    WindowStrategy <|-- ToolCallFoldStrategy
    WindowStrategy <|-- LLMSummaryStrategy

    class WindowManager {
        max_tokens: int
        strategies: list~WindowStrategy~
        message_factory: MessageFactory
        +add_strategy(strategy)
        +apply(messages, max_tokens) list
        +estimate_tokens(messages) int
    }
    WindowManager --> WindowStrategy : composes

    class PersistenceBackend {
        <<ABC>>
        +save_node(node)*
        +load_node(id)*
        +load_chain(agent_name)*
        +save_messages(agent_name, messages)*
        +load_messages(agent_name)*
        +save_session(session)*
        +load_session(session_id)*
        +delete_chain(agent_name)*
    }
    class InMemoryBackend
    class JsonFileBackend
    class SqliteBackend
    class RemoteBackend
    PersistenceBackend <|-- InMemoryBackend
    PersistenceBackend <|-- JsonFileBackend
    PersistenceBackend <|-- SqliteBackend
    PersistenceBackend <|-- RemoteBackend

    class ContextManager {
        agent_name: str
        chain: ActionChain
        state_manager: StateManager
        message_store: MessageStore
        window_manager: WindowManager
        persistence: PersistenceBackend | None
        auto_persist: bool
        +begin_iteration()
        +apply_state_changes(changes)
        +add_messages(messages)
        +commit_iteration()
        +rollback_iteration()
        +build_execution_context(ability, tool_args) AbilityExecutionContext
        +get_llm_messages() list
        +get_cumulative_token_usage() TokenUsage
        +fork_for_sub_agent(agent_config) ContextManager
        +create_session(system_prompt, parent_session_id) Session
        +switch_session(session_id) Session
        +list_sessions() list
        +persist()
        +restore()
    }
    ContextManager --> ActionChain
    ContextManager --> StateManager
    ContextManager --> MessageStore
    ContextManager --> WindowManager
    ContextManager --> PersistenceBackend
    ContextManager --> Session : manages
```

**核心类说明**:

- **ContextNode** — 不可变快照节点，类似 Git commit。持有 `messages_delta`（增量）或 `messages_snapshot`（全量），以及 `action_results`、`agent_state`、`branch_name`、`session_id`
- **Session** — 会话分支，支持 fork/checkout 语义。`is_rebase` 属性标识从其他 Agent 上下文变基而来的子代理会话
- **ActionChain** — 类 Git 的链式管理器，支持分支、fork、checkout、重建。`commit_node` 追加节点，`fork` 创建新分支，`checkout` 切换分支
- **StateManager** — 事务性状态管理器，支持嵌套 savepoint 和深拷贝隔离。`begin_transaction` / `commit` / `rollback` 确保能力执行失败时状态回滚
- **MessageStore** — 运行时消息存储，通过 `snapshot_interval` 控制快照频率，`compute_delta_since` 实现增量持久化
- **WindowStrategy / 策略系列** — 策略模式，4 种窗口压缩策略可组合：`TruncationStrategy`（截断最老消息）、`SlidingWindowStrategy`（滑动窗口）、`ToolCallFoldStrategy`（折叠长工具结果）、`LLMSummaryStrategy`（LLM 摘要）。由 `WindowManager` 按链式顺序组合
- **WindowManager** — 策略组合器，按配置顺序依次应用 `WindowStrategy`，实现 pipeline 式的窗口压缩
- **PersistenceBackend / 实现系列** — 持久化抽象与 4 种实现：InMemory（测试）、JsonFile（文件）、Sqlite（WAL 模式）、Remote（委托 Subject）。由 `create_persistence()` 工厂函数从 `ContextConfig` 创建
- **ContextManager** — 门面类，聚合 `ActionChain`、`StateManager`、`MessageStore`、`WindowManager`、`PersistenceBackend`，是 Agent 驱动循环操作上下文的唯一入口。`build_execution_context` 方法将自身转换为 `AbilityExecutionContext`（通过 Protocol），实现与能力系统的解耦

**层间关系**: 依赖 L0（types）、L1（Protocol）、L2（ChatMessage）；被 L5（ActorAgent）直接依赖。

---

### L5 Agent 与编排层

Agent 的核心驱动循环和多 Agent 编排机制。

```mermaid
classDiagram
    class ActorAgent {
        config: AgentConfig
        -_context: ContextManager
        -_abilities: dict
        -_executor: AbilityExecutor
        -_llm: LLMProtocol
        -_event_publisher: EventPublisher
        -_supervisor: SupervisorProtocol | None
        +register_ability(ability)
        +unregister_ability(name)
        +get_abilities() list
        +set_event_publisher(publisher)
        +receive_hitl_response(agent, ability, tool_call_id, result)
        +inject_message(message)
        +receive(message: Message)
        +chat(text: str) str
        +get_history() list
        +get_state() dict
        +set_state(state)
        +reset()
        +send(recipient, content, message_type) Message
        +create_session(system_prompt, parent_session_id) Session
        +switch_session(session_id) Session
        +list_sessions() list
        +archive_session(session_id)
        +delete_session(session_id)
        -_drive_loop() str
        -_action(tool_calls) ActionResult
        -_run_hooks(hook_point, context) HookResult
    }
    ActorAgent --> AgentConfig
    ActorAgent --> ContextManager
    ActorAgent --> AbilityExecutor
    ActorAgent --> LLMProtocol
    ActorAgent --> EventPublisher
    ActorAgent --> SupervisorProtocol

    class AgentBuilder {
        +from_config(config: AgentConfig)$ ActorAgent
    }
    AgentBuilder --> ActorAgent : builds

    class AgentInfo {
        <<dataclass>>
        name: str
        config: AgentConfig
        actor_handle: ActorAgent
        created_at: float
        +to_dict() dict
    }
    AgentInfo --> AgentConfig

    class AgentRegistry {
        +register(name, config, handle)
        +unregister(name)
        +get_handle(name) ActorAgent
        +get_info(name) AgentInfo
        +list_agents() list
        +list_names() list
        +exists(name) bool
    }
    AgentRegistry --> AgentInfo

    class MessageRouter {
        -_registry: AgentRegistry
        +route(message: Message) Message
        +broadcast(message: Message) dict
        +send_and_wait(sender, recipient, content, timeout) Message
    }
    MessageRouter --> AgentRegistry

    class SupervisorActor {
        -_registry: AgentRegistry
        -_router: MessageRouter
        -_command_sender: CommandSender | None
        -_event_bus: EventBus | None
        +spawn_agent(config: AgentConfig) ActorAgent
        +terminate_agent(name)
        +list_agents() list
        +route_message(message) Message
        +send(sender, recipient, content, message_type) Message
        +broadcast(sender, content) dict
        +delegate(from_agent, to_agent, message) Message
        +register_ability_for_agent(agent_name, ability)
        +get_agent_handle(name) ActorAgent
        +create_session(agent_name, system_prompt, parent_session_id) Session
        +switch_session(agent_name, session_id) Session
        +health_check() dict
    }
    SupervisorActor --> AgentRegistry
    SupervisorActor --> MessageRouter
    SupervisorActor --> AgentBuilder
    SupervisorActor --> CommandSender
    SupervisorActor --> EventBus
```

**核心类说明**:

- **ActorAgent** — 框架中央枢纽，聚合 `ContextManager`、`AbilityExecutor`、`LLMProtocol`、`EventPublisher`，通过 `_drive_loop` 驱动"LLM 生成 → 工具调用 → 能力执行 → Hook 拦截 → 结果提交"循环。对外暴露 `chat`（同步入口）和 `receive`（消息入口），以及会话管理（create/switch/list/archive/delete）和 HITL 响应注入接口
- **AgentBuilder** — 零配置工厂，从 `AgentConfig` 构建完整 `ActorAgent`（含 ContextManager、WindowManager、LLM 实例化）
- **AgentRegistry** — 进程级 Agent 注册表，管理 `name → AgentInfo` 映射，供路由和查找
- **MessageRouter** — 消息路由器，支持点对点 `route` 和 `broadcast`，以及同步 `send_and_wait`（带超时）
- **SupervisorActor** — 多 Agent 生命期编排器，管理 Agent 的 spawn/terminate、消息路由、能力注入。实现 `SupervisorProtocol` 接口，是分布式场景下的中央协调节点

**层间关系**: 依赖 L0~L4 所有层；`communication` 模块被 L6 的 Manifest 系统间接使用（通过 AgentConfig 构建）。

---

### L6 声明式清单层

独立子系统，通过 YAML/JSON 声明式定义 Agent 和 Ability，解析后生成 `AgentConfig` 和能力注册表。

```mermaid
classDiagram
    class AbilityManifest {
        manifest: str
        version: str
        metadata: AbilityMetadata
        tool: ToolSchema
        implementation: ImplementationDef
        hooks: AbilityHooks | None
        +full_name: str
    }
    class AgentManifest {
        manifest: str
        version: str
        metadata: AgentMetadata
        model: ModelConfig | None
        system_prompt: str | None
        max_iterations: int
        communication_timeout: float
        description: str | None
        abilities: list~AbilityRef~
        context: ContextOverrides | None
        hooks: AgentHooks | None
        +full_name: str
    }
    class AbilityMetadata {
        namespace: str
        name: str
        title: str
        description: str
        tags: list
        permissions: PermissionFlags | None
    }
    class AgentMetadata {
        namespace: str
        name: str
        title: str
        description: str
    }
    class ToolSchema {
        name: str
        description: str
        parameters: list~ToolParameter~
        +to_openai_schema() dict
        +to_openai_tool() dict
    }
    class PermissionFlags {
        require_hitl: bool
        fs_read_only: bool
        fs_write: bool
        net_access: bool
        shell_access: bool
        allowed_paths: list
        denied_paths: list
        allowed_commands: list
        denied_commands: list
        +merge(other) PermissionFlags
    }
    class ImplementationDef {
        type: str
        handler: str | None
        entrypoint: str | None
        source: str | None
    }
    class AbilityRef {
        ref: str | None
        type: str | None
        permissions: PermissionFlags | None
    }
    class AbilityHooks {
        pre_execute: list~HookDef~
        post_execute: list~HookDef~
        on_error: list~HookDef~
    }
    class ModelConfig {
        endpoint_name: str | None
        temperature: float | None
        max_tokens: int | None
        top_p: float | None
        top_k: int | None
    }
    class ContextOverrides {
        window: WindowOverrides | None
        persistence: PersistenceOverrides | None
    }

    AbilityManifest --> AbilityMetadata
    AbilityManifest --> ToolSchema
    AbilityManifest --> ImplementationDef
    AbilityManifest --> AbilityHooks
    AgentManifest --> AgentMetadata
    AgentManifest --> ModelConfig
    AgentManifest --> AbilityRef
    AgentManifest --> ContextOverrides
    AgentManifest --> AgentHooks
    AbilityRef --> PermissionFlags
    ContextOverrides --> WindowOverrides
    ContextOverrides --> PersistenceOverrides

    class ManifestResolver {
        -_store: ManifestStoreProtocol
        +resolve(agent_manifest: AgentManifest) ResolvedAgent
        -_build_config(resolved, manifest) AgentConfig
        -_resolve_abilities(manifest) list~ResolvedAbility~
    }
    ManifestResolver --> ManifestStoreProtocol

    class ResolvedAbility {
        <<dataclass>>
        ability_name: str
        tool_schema: ToolSchema
        permissions: PermissionFlags
        implementation: ImplementationDef
        hooks: AbilityHooks | None
        +from_builtin(name)$ ResolvedAbility
    }
    class ResolvedAgent {
        <<dataclass>>
        config: AgentConfig
        abilities: list~ResolvedAbility~
    }
    ManifestResolver --> ResolvedAgent : produces
    ResolvedAgent --> AgentConfig
    ResolvedAgent --> ResolvedAbility

    class BuiltinManifestStore {
        +load_ability(name) AbilityManifest
        +load_agent(name) AgentManifest
        +list_abilities() list
        +list_agents() list
    }
    class ManifestStoreProtocol {
        <<Protocol>>
        +load_ability(name)*
        +load_agent(name)*
        +list_abilities()*
        +list_agents()*
    }
    BuiltinManifestStore ..|> ManifestStoreProtocol
```

**核心类说明**:

- **AbilityManifest / AgentManifest** — 声明式定义的核心数据模型（Pydantic BaseModel），包含元数据、工具 schema、实现引用、权限和 Hook 配置。`full_name` 属性返回 `namespace.name` 格式
- **PermissionFlags** — 能力权限声明，支持 `merge` 语义用于 Agent 级别覆盖 Ability 级别的权限
- **ToolSchema** — 工具的 JSON Schema 定义，可直接转换为 OpenAI function calling 格式
- **ManifestResolver** — 将 `AgentManifest` 解析为 `ResolvedAgent`（含 `AgentConfig` + `ResolvedAbility` 列表），是从声明式定义到运行时配置的桥梁
- **ResolvedAgent / ResolvedAbility** — 解析后的完整定义，可直接用于构建 `ActorAgent` 和注册能力
- **BuiltinManifestStore** — 内置 YAML 清单的加载器，实现 `ManifestStoreProtocol`

**层间关系**: 仅依赖 L0（AgentConfig 等配置类型）和 Pydantic/YAML；通过 `ManifestResolver._build_config` 将 Manifest 转换为 L0 的 `AgentConfig`，连接声明式与运行时。

---

### 层间依赖总图

```mermaid
graph TD
    L0["L0 基础类型<br/>ActionOutcome ActionResult<br/>AgentConfig WindowConfig<br/>ContextConfig ModelOverrides<br/>TokenUsage"]
    L1["L1 核心协议与抽象<br/>Protocol 系列<br/>EventPublisher / CoreEvent<br/>HITLFutureStore / Message<br/>ActorAgentError 异常树"]
    L2["L2 消息与 LLM 集成<br/>ChatMessage / ContentBlock<br/>ChatFormat / LLMResponse<br/>LLMFactory"]
    L3["L3 能力系统<br/>Ability / AbilityExecutor<br/>AbilityRegistry / Hook<br/>HookResult"]
    L4["L4 上下文管理<br/>ContextManager / ActionChain<br/>ContextNode / Session<br/>StateManager / MessageStore<br/>WindowManager / PersistenceBackend"]
    L5["L5 Agent 与编排<br/>ActorAgent / AgentBuilder<br/>SupervisorActor<br/>AgentRegistry / MessageRouter"]
    L6["L6 声明式清单<br/>AgentManifest / AbilityManifest<br/>ManifestResolver<br/>BuiltinManifestStore"]

    L1 --> L0
    L2 --> L0
    L2 --> L1
    L3 --> L0
    L3 --> L1
    L4 --> L0
    L4 --> L1
    L4 --> L2
    L5 --> L0
    L5 --> L1
    L5 --> L2
    L5 --> L3
    L5 --> L4
    L6 --> L0
    L6 --> L1
```

**关键依赖路径**:
- L0 被所有层依赖，是框架的纯数据地基
- L1 的 Protocol 机制是跨层解耦的核心（`AbilityProtocol`、`ExecutorProtocol`、`LLMProtocol`、`CommandSender`、`SupervisorProtocol`）
- L5 `ActorAgent` 是唯一聚合所有层的"中枢"，但其对 L3/L4 的依赖通过 Protocol 实现可替换
- L6 完全自包含，仅通过 `ManifestResolver._build_config` 连接到 L0 的 `AgentConfig`