# 配置参考

ghrah 的配置分为两层：框架层配置（`AgentConfig` 等）和 LLM 配置（通过 agentconf 管理）。

## AgentConfig

[`AgentConfig`](../src/ghrah/core/config.py:102) 是 Agent 的框架层配置：

```python
from ghrah.core.config import AgentConfig

config = AgentConfig(
    name="my-agent",  # Agent 运行时名称（必填，用于消息路由和持久化路径）
    endpoint_name="assistant",  # agentconf 模型端点名称（默认工厂必填）
    description="通用助手",  # Agent 描述（用于发现和路由）
    system_prompt="你是一个助手",  # 系统提示词
    max_iterations=10,  # 最大推理迭代次数
    resources={"CPU": 2, "GPU": 1},  # 资源需求
    window=WindowConfig(...),  # 窗口管理配置
    context=ContextConfig(...),  # 上下文管理配置
)
```

### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | `str` | **必填** | Agent 运行时唯一名称，用于消息路由和持久化路径 |
| `endpoint_name` | `str \| None` | `None` | agentconf 模型端点名称；默认工厂要求显式指定 |
| `description` | `str` | `""` | Agent 能力描述，用于 Agent 发现和路由 |
| `system_prompt` | `str` | `""` | 系统提示词 |
| `max_iterations` | `int` | `10` | 最大推理迭代次数（防止死循环的安全阀） |
| `resources` | `dict[str, Any]` | `{}` | 资源需求，如 `{"CPU": 2, "GPU": 1}` |

| `window` | `WindowConfig \| None` | `None` | 窗口管理配置，`None` 表示不启用 |
| `context` | `ContextConfig \| None` | `None` | 上下文管理配置，`None` 表示使用默认值 |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `num_cpus` | `float \| None` | 需要的 CPU 资源数 |
| `num_gpus` | `float \| None` | 需要的 GPU 资源数 |

### endpoint_name 使用场景

当多个运行时 Agent 共享同一份 LLM 配置时（如 Worker 池场景），可以通过 `endpoint_name` 将运行时名称与 agentconf 配置名称分离：

```python
# Worker 池：多个 worker 共享同一份 agentconf 配置
for i in range(num_workers):
    config = AgentConfig(
        name=f"solve_worker_{i}",  # 运行时唯一标识
        endpoint_name="solve_worker",  # agentconf 中的端点名称
        system_prompt=f"你是解题 Worker #{i}",
    )
    # Agent 初始化时会使用 "solve_worker" 查找 agentconf 配置
    # 而不是查找不存在的 "solve_worker_0", "solve_worker_1" 等
```

默认工厂不再回退到运行时 `name`。旧 Agent 名称必须经导出映射转换；即使端点名称看起来相同，也不能直接把旧字段改名。迁移方法见 [模型端点与配置迁移](model-endpoints.md)。

```python
config = AgentConfig(name="planner", endpoint_name="planning-endpoint")
```

## WindowConfig

[`WindowConfig`](../src/ghrah/core/config.py:20) 控制如何将对话历史压缩到 LLM 的 token 预算内：

```python
from ghrah.core.config import WindowConfig

window_config = WindowConfig(
    max_tokens=4096,  # token 预算
    strategies=["tool_call_fold", "truncation"],  # 策略列表
    tool_call_max_length=500,  # ToolCall 折叠最大长度
    sliding_window_size=20,  # 滑动窗口大小
)
```

### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_tokens` | `int \| None` | `None` | LLM 上下文窗口大小（token 预算）；未声明时优先使用 agentconf 解析后的窗口，缺少能力接口时才查模型窗口表，最终兜底为 1M（1,000,000）tokens |
| `strategies` | `list[str]` | `["tool_call_fold", "truncation"]` | 策略名称列表，按执行顺序排列 |
| `tool_call_max_length` | `int` | `500` | `ToolCallFoldStrategy` 的最大 content 长度 |
| `sliding_window_size` | `int` | `20` | `SlidingWindowStrategy` 的窗口大小 |

agentconf 0.2 将模型绑定显式指向能力档案，未声明的字段按模型类型补默认值。LLM 工厂将完整能力与来源
携带到客户端，Agent 创建首个 LLM 后使用其中的 `max_context_length` 落定预算，
事件中的 `budget_source` 为 `agentconf`。显式填写的 `WindowConfig.max_tokens` 优先。
自定义注入客户端若没有携带能力信息，保留模型窗口表的兼容路径；能力信息中的窗口显式
为 `None` 时使用兜底预算，不再进入前缀查表。参考输出上限仅表示模型能力，不会自动
把它设为每次调用的输出 token 数，也不会因推理能力标记而强制启用厂商推理模式。
厂商返回上下文超限错误时，
运行时仍会按错误中的实际窗口大小修正预算，并执行紧急压缩。

### 可用策略

| 策略名称 | 类 | 说明 |
|----------|-----|------|
| `"truncation"` | `TruncationStrategy` | 简单截断，从最早的消息开始 |
| `"sliding_window"` | `SlidingWindowStrategy` | 滑动窗口，保留最近 N 条消息 |
| `"tool_call_fold"` | `ToolCallFoldStrategy` | 折叠长的 tool_call 结果 |
| `"llm_summary"` | `LLMSummaryStrategy` | 使用 LLM 生成摘要（需注入 LLM） |

## ContextConfig

[`ContextConfig`](../src/ghrah/core/config.py:40) 控制 ActorAgent 的上下文管理行为：

```python
from ghrah.core.config import ContextConfig

context_config = ContextConfig(
    snapshot_interval=5,  # 快照间隔
    auto_persist=False,  # 自动持久化
    persistence_type="json_file",  # 持久化后端类型
    persistence_root_dir="/tmp/data",  # 存储根目录
    persistence_compress=True,  # gzip 压缩
    persistence_run_id="my_session",  # 会话 ID
)
```

### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `snapshot_interval` | `int` | `5` | 消息快照间隔（每 N 次迭代存储一次完整快照） |
| `auto_persist` | `bool` | `False` | 是否在每次 commit/rollback 后自动持久化节点 |
| `persistence_type` | `str \| None` | `None` | 持久化后端类型：`"json_file"` / `"memory"` / `"sqlite"` / `"remote"` / `None` |
| `persistence_root_dir` | `str \| None` | `None` | 持久化存储根目录路径（json_file/sqlite 后端使用） |
| `persistence_compress` | `bool` | `True` | 是否启用 gzip 压缩持久化文件 |
| `persistence_run_id` | `str \| None` | `None` | 持久化运行 ID，`None` 表示自动生成 |


### 持久化后端类型

| 类型 | 类 | 说明 |
|------|-----|------|
| `"json_file"` | `JsonFileBackend` | 基于 JSON 文件，支持 gzip 压缩 |
| `"memory"` | `InMemoryBackend` | 纯内存，不持久化到磁盘 |
| `"sqlite"` | `SqliteBackend` | 基于 SQLite 数据库，WAL 模式支持并发读 |
| `"remote"` | `RemoteBackend` | 通过 CommandSender 将持久化操作委托给 Subject |
| `None` | — | 不启用持久化 |

### create_persistence() 工厂方法

`ContextConfig.create_persistence()` 根据 `persistence_type` 创建对应的持久化后端实例：

```python
context_config = ContextConfig(persistence_type="json_file", persistence_root_dir="/tmp/data")
backend = context_config.create_persistence()  # 返回 JsonFileBackend 实例
```

## agentconf 配置

agentconf 0.3 管理 Provider 连接、模型端点、能力档案，以及独立的 MCP 连接。模型端点保存实际请求模型名、用途、接口格式和可选能力引用；请求参数留在 ghrah。

无能力引用的 LLM 默认上下文为 1M，支持工具与推理。`resolved.capabilities` 是有效能力，`capability_sources` 表示逐字段来源，`metadata` 保存档案来源与版本，也可以为空。最大输出能力是模型上限，`ModelOverrides.max_tokens` 才是本次请求预算。

执行 `uv run agentconf` 使用 TUI；CLI 的 Provider/端点创建使用 JSON 文件和显式 apply：

```sh
agentconf provider create --file provider.json
agentconf provider create --file provider.json --apply
agentconf endpoint create --file endpoint.json
agentconf endpoint create --file endpoint.json --apply
```

字段样例、格式分派及迁移方法见 [模型端点与配置迁移](model-endpoints.md)。

### SDK 使用

```python
from agentconf import AgentsConfig
from ghrah.llm.factory import LLMFactory
from ghrah.types.config_types import ModelOverrides

with AgentsConfig() as config:
    resolved = config.resolve_model_endpoint("assistant")
llm = LLMFactory.create(resolved, ModelOverrides(temperature=0, max_tokens=2048))
# resolved.model.model_name 是实际请求模型；resolved.connection 是连接信息。
# resolved.capabilities、capability_sources 和 metadata 保留有效能力及来源。
```

本消费契约要求 agentconf>=0.3.0,<0.4。根依赖与锁文件统一解析正式包，不使用本机 SDK path。运行 `uv sync --all-packages --group dev` 同步实际环境。

## 直接构造 ChatFormat（Fallback）

当不想使用 agentconf 时，可以直接构造 `ChatFormat` 子类并通过 `agent._llm` 注入到 Agent 中。这种方式跳过了 agentconf 的 Provider → 模型端点解析，将 LLM 参数的管控权完全交给调用方。

> **原理**：`ActorAgent._ensure_llm()` 在首次调用时检查 `self._llm`，若已设置则直接返回，不再走 agentconf 解析路径。因此在 `receive()` / `chat()` 之前赋值即可。


## 环境变量

通过 `.env` 文件或环境变量配置（参考 [`.env.example`](../.env.example)）：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AGENTCONF_DB_PATH` | agentconf 数据库路径 | `~/.agentconf/config.db` |
| `OPENAI_API_KEY` | OpenAI API Key | — |
| `ANTHROPIC_API_KEY` | Anthropic API Key | — |

## 完整配置示例

### 最小配置

```python
from ghrah.core.config import AgentConfig

config = AgentConfig(
    name="assistant",
    endpoint_name="assistant",
    system_prompt="你是一个友好的 AI 助手。",
)
```

### 完整配置

```python
from ghrah.core.config import AgentConfig, WindowConfig, ContextConfig

config = AgentConfig(
    name="coder",
    endpoint_name="coder",
    description="代码编写助手",
    system_prompt="你是一个代码编写专家。请根据需求编写高质量的代码。",
    max_iterations=15,
    resources={"CPU": 2},
    # 窗口管理
    window=WindowConfig(
        max_tokens=8192,
        strategies=["tool_call_fold", "sliding_window", "truncation"],
        tool_call_max_length=500,
        sliding_window_size=30,
    ),
    # 上下文管理
    context=ContextConfig(
        snapshot_interval=10,
        auto_persist=True,
        persistence_type="json_file",
        persistence_root_dir="/tmp/agent_data",
        persistence_compress=True,
        persistence_run_id="session_20260422",
    ),
)
```

### 多 Agent 配置

```python
from ghrah.core.config import AgentConfig, ContextConfig

# 规划者 — 轻量配置
planner_config = AgentConfig(
    name="planner",
    endpoint_name="planner",
    description="任务规划专家",
    system_prompt="你是一个任务规划专家。请将复杂任务分解为清晰的步骤。",
    max_iterations=5,
)

# 编码者 — 完整配置
coder_config = AgentConfig(
    name="coder",
    endpoint_name="coder",
    description="代码编写专家",
    system_prompt="你是一个代码编写专家。",
    max_iterations=15,
    context=ContextConfig(
        persistence_type="json_file",
        persistence_root_dir="/tmp/coder_data",
    ),
)

# 审查者 — 中等配置
reviewer_config = AgentConfig(
    name="reviewer",
    endpoint_name="reviewer",
    description="代码审查专家",
    system_prompt="你是一个代码审查专家。",
    max_iterations=10,
)
```

### Worker 池配置（endpoint_name）

当多个 Agent 共享同一份 LLM 配置时（如 Worker 池场景），使用 `endpoint_name` 将运行时名称与 agentconf 配置名称分离：

```python
from ghrah.core.config import AgentConfig

# Worker 池：多个 worker 共享同一份 agentconf 配置
# agentconf 中只需创建一个 "solve_worker" 配置
worker_configs = [
    AgentConfig(
        name=f"solve_worker_{i}",  # 运行时唯一标识
        endpoint_name="solve_worker",  # agentconf 中的端点名称
        description=f"解题 Worker #{i}",
        system_prompt=f"你是解题 Worker #{i}。",
    )
    for i in range(3)
]

# 角色别名：不同运行时名称，共享同一份 LLM 配置
reviewer_config = AgentConfig(
    name="code_reviewer_v2",  # 新版本运行时标识
    endpoint_name="reviewer",  # 复用 reviewer 的 LLM 配置
    description="代码审查专家 v2",
    system_prompt="你是一个代码审查专家。",
)
```

详见 [Worker 池示例](../examples/worker_pool.py)。

### 分布式模式配置

分布式模式下 Core 通过 CommandSender 与 Subject 通信：

```python
from ghrah.core.config import AgentConfig, ContextConfig

config = AgentConfig(
    name="coder",
    endpoint_name="coder",
    system_prompt="你是一个代码编写专家。",
    context=ContextConfig(
        persistence_type="remote",
    ),
)
```

详见 [双模式架构](distributed-mode.md)。

## 下一步

- [快速开始](getting-started.md) — 使用配置创建 Agent
- [持久化与窗口管理](persistence.md) — 深入了解持久化和窗口策略配置
- [内置 Ability 参考](builtin-abilities.md) — 查看 FSPermissionChecker 配置
