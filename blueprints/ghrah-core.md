## `ghrah` 核心业务代码思维导图

以下按模块层级和依赖关系组织，排除 examples 和 test 代码。

```
ghrah (核心框架)
│
├── 📦 types/  【基础类型层 — 零内部依赖叶子模块】
│   ├── results.py        → ActionOutcome, ActionResult
│   ├── config_types.py   → AgentConfig, ContextConfig, ModelOverrides, WindowConfig
│   │                        (原 core/config.py 中的配置数据类，已提取至此消除循环依赖)
│   └── tokens.py         → TokenUsage
│
├── 📦 core/  【核心基础设施层】
│   ├── events.py         → CoreEvent, CoreEventType, AgentResponseEvent,
│   │                        AgentErrorEvent, ActionChainUpdatedEvent,
│   │                        HITLRequestEvent, SessionCreatedEvent,
│   │                        SessionSwitchedEvent, SessionArchivedEvent,
│   │                        SessionDeletedEvent
│   ├── exceptions.py     → ActorAgentError (根异常) → AgentError,
│   │                        AgentInitializationError, AgentTimeoutError,
│   │                        LLMError, MessageError, ToolError,
│   │                        RegistryError → AgentNotFoundError,
│   │                        RoutingError, CommunicationTimeoutError,
│   │                        AbilityError → AbilityNotFoundError, HookError
│   ├── message.py        → Message, MessageType (Agent 间通信协议)
│   ├── config.py         → 纯 re-export: 从 ghrah.types.config_types 重新导出
│   │                        AgentConfig, ContextConfig, ModelOverrides, WindowConfig
│   ├── event_publisher.py → EventPublisher (ABC), NullEventPublisher,
│   │                         ServerEventPublisher (依赖 ghrah.protocol.types.Message)
│   ├── hitl.py           → HITLFutureStore, HITLResult (人机协同)
│   ├── command_sender.py → CommandSender (Protocol), AbilityResultCallback
│   └── server/           【分布式服务子系统】
│       ├── app.py        → create_app(), main() (FastAPI 应用)
│       ├── server.py     → 服务入口
│       ├── router.py     → HTTP/WS 路由
│       ├── config.py     → 服务配置
│       ├── event_bus.py  → EventBus (服务器事件总线)
│       └── connection_manager.py → WebSocket 连接管理
│
├── 📦 chat/  【消息与格式层】
│   ├── content.py        → TextBlock, ImageBlock, ToolCallBlock,
│   │                        ToolResultBlock, AudioBlock, FileBlock,
│   │                        ReasoningBlock, StreamingBlock, ErrorBlock,
│   │                        ToolCallChunkBlock, ContentBlock
│   ├── message.py        → ChatMessage (依赖 content)
│   ├── serialization.py  → serialize_messages, deserialize_messages
│   ├── response.py       → extract_reasoning_content,
│   │                        extract_response_metadata, extract_token_usage
│   └── format/           【LLM 厂商格式适配器】
│       ├── openai.py     → OpenAIFormat
│       ├── anthropic.py  → AnthropicFormat
│       └── deepseek.py   → DeepSeekFormat
│
├── 📦 llm/  【LLM 集成层】
│   ├── factory.py        → LLMFactory (懒加载 chat/format/*)
│   └── response_utils.py → extract_reasoning_content,
│                            extract_response_metadata, extract_token_usage
│
├── 📦 abilities/  【能力/工具系统】
│   ├── base.py           → Ability (抽象基类), re-export ActionOutcome, ActionResult
│   ├── context.py        → AbilityExecutionContext
│   ├── executor.py       → AbilityExecutor, LocalAbilityExecutor,
│   │                        RemoteAbilityExecutor
│   ├── registry.py       → AbilityRegistry
│   ├── hooks.py          → Hook, HookPoint, HookResult
│   ├── hook_registry.py  → Hook 注册
│   ├── _utils.py         → 内部工具函数
│   └── builtin/          【内置能力实现 (14个)】
│       ├── 文件操作 (6):
│       │   read_file.py, write_file.py, edit_file.py,
│       │   delete_file.py, move_file.py, list_directory.py
│       ├── 权限与安全 (2):
│       │   fs_permissions.py → FSPermissionChecker, AccessApprovalHook,
│       │                        WriteApprovalHook
│       │   command_safety.py → CommandSafetyChecker, CommandSafetyCategory,
│       │                        CommandApprovalHook
│       ├── Shell (1):
│       │   execute_command.py → ExecuteCommandAbility, ExecuteCommandInput
│       ├── 对话与任务 (2):
│       │   conversation.py, end_task.py
│       └── 集群通信 (5):
│           spawn_agent.py, terminate_agent.py, send_message.py,
│           broadcast_message.py, query_agents.py
│           _cluster_common.py (共享工具)
│
├── 📦 context/  【上下文与会话管理层】
│   ├── session.py        → Session (纯数据类, 零内部依赖)
│   ├── node.py           → ContextNode (单个上下文单元)
│   ├── chain.py          → ActionChain (上下文链)
│   ├── message_store.py  → MessageStore (消息存储)
│   ├── state.py          → StateManager (状态追踪)
│   ├── window.py         → WindowManager, WindowStrategy,
│   │                        estimate_tokens, estimate_message_tokens
│   ├── manager.py        → ContextManager (核心门面类, 聚合所有子模块,
│   │                       依赖 types.results)
│   ├── rebase.py         → create_rebased_context (上下文变基)
│   ├── persistence/      【持久化后端 (4种) + 工厂函数】
│   │   ├── backend.py    → PersistenceBackend (抽象基类)
│   │   ├── memory.py     → InMemoryBackend
│   │   ├── json_file.py  → JsonFileBackend
│   │   ├── sqlite_backend.py → SqliteBackend
│   │   ├── remote_backend.py → RemoteBackend
│   │   ├── serialization.py  → serialize_node/deserialize_node,
│   │   │                        serialize_session/deserialize_session,
│   │   │                        serialize_action_result/deserialize_action_result,
│   │   │                        serialize_action_results/deserialize_action_results,
│   │   │                        serialize_messages/deserialize_messages
│   │   └── __init__.py   → create_persistence() 工厂函数 (从 core/config 迁移至此)
│   └── strategies/       【上下文窗口策略 (4种)】
│       ├── sliding_window.py → SlidingWindowStrategy
│       ├── llm_summary.py    → LLMSummaryStrategy
│       ├── tool_call_fold.py → ToolCallFoldStrategy
│       └── truncation.py     → TruncationStrategy
│
├── 📦 agents/  【Agent 核心 — 中央枢纽】
│   └── base.py           → ActorAgent (约1370行, 依赖最多的模块)
│                           直接依赖 types (AgentConfig, WindowConfig, ActionOutcome, ActionResult)
│                           聚合 abilities + chat + context + core + llm
│                           懒加载 context/strategies/*
│                           使用 context.persistence.create_persistence() 创建持久化后端
│
├── 📦 communication/  【Agent 间通信与编排】
│   ├── registry.py       → AgentRegistry, AgentInfo
│   ├── router.py         → MessageRouter (依赖 registry + core)
│   └── supervisor.py     → SupervisorActor (编排多 Agent 生命周期,
│                           依赖 types (AgentConfig) + agents + abilities +
│                           communication + core)
│
└── 📦 manifest/  【声明式清单系统 — 独立子系统】
    ├── types.py          → ToolSchema, ToolParameter, HookDef,
    │                        ImplementationDef, PermissionFlags, SUPPORTED_AGENT_VERSIONS / SUPPORTED_ABILITY_VERSIONS
    ├── ability.py        → AbilityManifest, AbilityMetadata, AbilityHooks
    ├── agent.py          → AgentManifest, AgentMetadata, AgentHooks,
    │                        AbilityRef, ModelConfig, ContextOverrides,
    │                        PersistenceOverrides, WindowOverrides
    ├── parser.py         → YAML/JSON 解析器 (依赖 pydantic)
    ├── resolver.py       → ManifestResolver, ResolvedAbility, ResolvedAgent
    │                       (依赖 types.config_types 中的
    │                        AgentConfig, ContextConfig, ModelOverrides, WindowConfig)
    ├── store.py          → BuiltinManifestStore
    ├── protocols.py      → ManifestStoreProtocol
    ├── responses.py      → AbilityManifestData, AgentManifestData,
    │                        ManifestDeleteResult, ManifestPutResult,
    │                        ResolvedAbilityData, ResolvedAgentData, ValidateResult
    ├── errors.py         → ManifestError, ManifestValidationError,
    │                        ManifestNotFoundError, ManifestVersionError,
    │                        DuplicateManifestError
    └── builtins/         【内置 YAML 清单 (14个)】
        ├── ghrah.fs.read_file.yaml
        ├── ghrah.fs.write_file.yaml
        ├── ghrah.fs.list_directory.yaml
        ├── ghrah.fs.edit_file.yaml
        ├── ghrah.fs.move_file.yaml
        ├── ghrah.fs.delete_file.yaml
        ├── ghrah.shell.execute_command.yaml
        ├── ghrah.cluster.query_agents.yaml
        ├── ghrah.cluster.send_message.yaml
        ├── ghrah.cluster.broadcast_message.yaml
        ├── ghrah.cluster.spawn_agent.yaml
        ├── ghrah.cluster.terminate_agent.yaml
        ├── ghrah.core.conversation.yaml
        └── ghrah.core.end_task.yaml
```

---

### 模块依赖关系图 (箭头 = "依赖于")

```
                    ┌──────────────────────────────────────────────────┐
                    │              ghrah.types (叶子层)                 │
                    │  results  │  config_types  │  tokens             │
                    │  (ActionOutcome,          │  (TokenUsage)        │
                    │   ActionResult)           │                      │
                    │  (AgentConfig, ContextConfig,                    │
                    │   ModelOverrides, WindowConfig)                  │
                    └──┬────────────┬──────────────┬──────────────────┘
                       │            │              │
          ┌────────────▼──┐  ┌──────▼───────┐  ┌──▼───────────────┐
          │abilities/base │  │ core/events  │  │ context/manager  │
          │(纯re-export)  │  │ core/message │  │ context/session  │
          └───────┬───────┘  │ core/exceptions│  │ context/window  │
                  │          │ core/hitl    │  │ context/persistence│
                  │          │ core/server  │  └────────┬─────────┘
                  │          └──────┬───────┘           │
                  │                 │                   │
    ┌─────────────┼─────────────────┼───────────────────┼──────────────┐
    │             │                 │                   │              │
    ▼             ▼                 ▼                   ▼              ▼
┌──────────┐ ┌──────────┐  ┌──────────────┐  ┌──────────────────┐
│ chat/    │ │ llm/     │  │communication │  │  context/        │
│ content  │ │ factory  │  │  /router     │  │  manager (门面)   │
│ message  │ │          │  │  /registry   │  │  聚合所有子模块   │
│ format   │ │          │  │  /supervisor │  │  + persistence/  │
│ (独立)   │ │          │  │              │  │  + strategies/   │
└────┬─────┘ └────┬─────┘  └──────┬───────┘  └────────┬─────────┘
     │            │               │                    │
     │            │    types ────►│ (supervisor 直接   │
     │            │    .AgentConfig│  从 types 导入)    │
     │            │               │                    │
     └────────────┼───────────────┼────────────────────┘
                  │               │
                  ▼               ▼
    ┌─────────────────────────────────────────────────────┐
    │              agents/base.py (ActorAgent)             │
    │              中央枢纽 — 直接依赖 types, 聚合所有模块  │
    │   types.AgentConfig, types.WindowConfig,             │
    │   types.ActionOutcome, types.ActionResult            │
    │   + context.persistence.create_persistence()         │
    │   + 懒加载 context/strategies/*                      │
    └──────────────────────────┬──────────────────────────┘
                               │
                               ▼
                  communication/supervisor ← 多 Agent 编排
                               │
                               ▼
                  communication/router    ← 消息路由
                  communication/registry  ← Agent 注册


  ┌─────────────────────────────────────────────────────────────┐
  │  manifest/*  (独立子系统, 仅依赖 types + pydantic + yaml)    │
  │  parser → store → resolver                                  │
  │  resolver 直接引用 types.config_types 中的                   │
  │  AgentConfig, ContextConfig, ModelOverrides, WindowConfig    │
  └─────────────────────────────────────────────────────────────┘
```

---

### 关键重构变更 (v0.1.1)

| 变更项 | 旧位置 | 新位置 | 原因 |
|--------|--------|--------|------|
| `AgentConfig` 等配置类 | `core/config.py` | `types/config_types.py` | 消除 `core → abilities` 循环依赖 |
| `create_persistence()` 工厂 | `core/config.py` | `context/persistence/__init__.py` | 职责归属 persistence 模块自身 |
| `core/config.py` 角色 | 定义 + 工厂 | 纯 re-export | 向后兼容, 旧导入路径仍可用 |
| 异常基类 | `Exception` (直接继承) | `ActorAgentError` 根类 | 统一异常层次, 便于捕获框架级错误 |
| `abilities/__init__.py` 导入源 | `abilities.base` | `types.results` | ActionOutcome/ActionResult 归属 types |
| `event_publisher.py` | 无外部协议依赖 | 依赖 `ghrah.protocol.types.Message` | 与 ghrah-protocol 对齐事件格式 |
| `context/persistence` 序列化 | `serialize_node/deserialize_node` | + `serialize_session/deserialize_session` | 新增 Session 持久化支持 |

---

### 功能组织总结

| 层级 | 模块 | 职责 |
|------|------|------|
| **L0 基础** | `types` | 纯数据类型 (Result, Config, Token)，零内部依赖, 供所有上层模块引用 |
| **L1 核心设施** | `core` | 事件系统、异常层次 (ActorAgentError 根)、消息协议、HITL、分布式服务 |
| **L1 消息** | `chat` + `llm` | 消息内容块、序列化、LLM 厂商格式适配 (OpenAI/Anthropic/DeepSeek)、工厂 |
| **L2 能力** | `abilities` | 工具抽象基类 (Ability)、执行器、注册表、Hook 机制、14个内置工具 |
| **L2 上下文** | `context` | 会话管理、链式节点、消息存储、窗口管理、4种持久化后端 + 工厂、4种窗口策略 |
| **L3 中枢** | `agents` | ActorAgent 聚合所有模块, 直接依赖 types, 是框架唯一集成点 |
| **L3 编排** | `communication` | Agent 注册、消息路由、Supervisor 多Agent编排, 直接依赖 types.AgentConfig |
| **独立** | `manifest` | YAML 声明式 Agent/Ability 定义, 依赖 types + pydantic + yaml, 完全自包含 |
