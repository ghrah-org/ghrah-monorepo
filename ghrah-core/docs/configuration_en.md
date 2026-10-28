# Configuration Reference

ghrah configuration is divided into two layers: framework configuration (`AgentConfig`, etc.) and LLM configuration (managed through agentconf).

## AgentConfig

[`AgentConfig`](../src/ghrah/core/config.py:102) is the Agent's framework-level configuration:

```python
from ghrah.core.config import AgentConfig

config = AgentConfig(
    name="my-agent",  # Agent runtime name (required, for routing and persistence paths)
    endpoint_name="assistant",  # agentconf 模型端点名称（默认工厂必填）
    description="General assistant",  # Agent description (for discovery and routing)
    system_prompt="You are an assistant",  # System prompt
    max_iterations=10,  # Maximum reasoning iterations
    resources={"CPU": 2, "GPU": 1},  # Resource requirements
    window=WindowConfig(...),  # Window management configuration
    context=ContextConfig(...),  # Context management configuration
)
```

### Parameter Descriptions

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | **Required** | Agent runtime unique name, for message routing and persistence paths |
| `endpoint_name` | `str \| None` | `None` | agentconf 模型端点名称；默认工厂要求显式指定 |
| `description` | `str` | `""` | Agent capability description, for discovery and routing |
| `system_prompt` | `str` | `""` | System prompt |
| `max_iterations` | `int` | `10` | Maximum reasoning iterations (safety valve against infinite loops) |
| `resources` | `dict[str, Any]` | `{}` | Resource requirements, e.g., `{"CPU": 2, "GPU": 1}` |

| `window` | `WindowConfig \| None` | `None` | Window management configuration, `None` disables window management |
| `context` | `ContextConfig \| None` | `None` | Context management configuration, `None` uses defaults |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `num_cpus` | `float \| None` | Number of CPUs required |
| `num_gpus` | `float \| None` | Number of GPUs required |

### endpoint_name Usage

When multiple runtime Agents share the same LLM configuration (e.g., Worker pool scenarios), use `endpoint_name` to separate the runtime name from the agentconf configuration name:

```python
# Worker pool: multiple workers share the same agentconf configuration
for i in range(num_workers):
    config = AgentConfig(
        name=f"solve_worker_{i}",  # Runtime unique identifier
        endpoint_name="solve_worker",  # Model endpoint name in agentconf
        system_prompt=f"You are solve Worker #{i}",
    )
    # Agent initialization uses "solve_worker" to look up agentconf configuration
    # instead of looking up non-existent "solve_worker_0", "solve_worker_1", etc.
```

默认工厂不再回退到运行时 `name`。旧 Agent 名称必须经导出映射转换；即使端点名称看起来相同，也不能直接把旧字段改名。迁移方法见 [模型端点与配置迁移](model-endpoints.md)。

```python
config = AgentConfig(name="planner", endpoint_name="planning-endpoint")
```

## WindowConfig

[`WindowConfig`](../src/ghrah/core/config.py:20) controls how conversation history is compressed within the LLM token budget:

```python
from ghrah.core.config import WindowConfig

window_config = WindowConfig(
    max_tokens=4096,  # Token budget
    strategies=["tool_call_fold", "truncation"],  # Strategy list
    tool_call_max_length=500,  # ToolCall fold max length
    sliding_window_size=20,  # Sliding window size
)
```

### Parameter Descriptions

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_tokens` | `int` | `4096` | LLM context window size (token budget) |
| `strategies` | `list[str]` | `["tool_call_fold", "truncation"]` | Strategy name list, in execution order |
| `tool_call_max_length` | `int` | `500` | Max content length for `ToolCallFoldStrategy` |
| `sliding_window_size` | `int` | `20` | Window size for `SlidingWindowStrategy` |

### Available Strategies

| Strategy Name | Class | Description |
|---------------|-------|-------------|
| `"truncation"` | `TruncationStrategy` | Simple truncation from earliest messages |
| `"sliding_window"` | `SlidingWindowStrategy` | Sliding window, keeps most recent N messages |
| `"tool_call_fold"` | `ToolCallFoldStrategy` | Folds long tool_call results |
| `"llm_summary"` | `LLMSummaryStrategy` | Uses LLM to generate summaries (requires LLM injection) |

## ContextConfig

[`ContextConfig`](../src/ghrah/core/config.py:40) controls the ActorAgent's context management behavior:

```python
from ghrah.core.config import ContextConfig

context_config = ContextConfig(
    snapshot_interval=5,  # Snapshot interval
    auto_persist=False,  # Auto-persist
    persistence_type="json_file",  # Persistence backend type
    persistence_root_dir="/tmp/data",  # Storage root directory
    persistence_compress=True,  # gzip compression
    persistence_run_id="my_session",  # Session ID
)
```

### Parameter Descriptions

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `snapshot_interval` | `int` | `5` | Message snapshot interval (full snapshot every N iterations) |
| `auto_persist` | `bool` | `False` | Whether to auto-persist after each commit/rollback |
| `persistence_type` | `str \| None` | `None` | Persistence backend type: `"json_file"` / `"memory"` / `"sqlite"` / `"remote"` / `None` |
| `persistence_root_dir` | `str \| None` | `None` | Persistence storage root directory path (for json_file/sqlite backends) |
| `persistence_compress` | `bool` | `True` | Whether to enable gzip compression for persistence files |
| `persistence_run_id` | `str \| None` | `None` | Persistence run ID, `None` means auto-generated |


### Persistence Backend Types

| Type | Class | Description |
|------|-------|-------------|
| `"json_file"` | `JsonFileBackend` | JSON file-based, supports gzip compression |
| `"memory"` | `InMemoryBackend` | Pure in-memory, no disk persistence |
| `"sqlite"` | `SqliteBackend` | SQLite database-based, WAL mode for concurrent reads |
| `"remote"` | `RemoteBackend` | Delegates persistence to Subject via CommandSender |
| `None` | — | Persistence disabled |

### create_persistence() Factory Method

`ContextConfig.create_persistence()` creates the corresponding persistence backend instance based on `persistence_type`:

```python
context_config = ContextConfig(persistence_type="json_file", persistence_root_dir="/tmp/data")
backend = context_config.create_persistence()  # Returns JsonFileBackend instance
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

## Direct ChatFormat Construction (Fallback)

When you don't want to use agentconf, you can directly construct a `ChatFormat` subclass and inject it into the Agent via `agent._llm`. This approach bypasses the agentconf Provider → 模型端点解析, giving the caller full control over LLM parameter management.

> **How it works**: `ActorAgent._ensure_llm()` checks `self._llm` on first invocation. If already set, it returns immediately without going through agentconf resolution. Simply assign the ChatFormat before calling `receive()` / `chat()`.


## Environment Variables

Configure via `.env` file or environment variables (see [`.env.example`](../.env.example)):

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENTCONF_DB_PATH` | agentconf database path | `~/.agentconf/config.db` |
| `OPENAI_API_KEY` | OpenAI API Key | — |
| `ANTHROPIC_API_KEY` | Anthropic API Key | — |

## Complete Configuration Examples

### Minimal Configuration

```python
from ghrah.core.config import AgentConfig

config = AgentConfig(
    name="assistant",
    endpoint_name="assistant",
    system_prompt="You are a friendly AI assistant.",
)
```

### Full Configuration

```python
from ghrah.core.config import AgentConfig, WindowConfig, ContextConfig

config = AgentConfig(
    name="coder",
    endpoint_name="coder",
    description="Code writing assistant",
    system_prompt="You are a code writing expert. Write high-quality code based on requirements.",
    max_iterations=15,
    resources={"CPU": 2},
    # Window management
    window=WindowConfig(
        max_tokens=8192,
        strategies=["tool_call_fold", "sliding_window", "truncation"],
        tool_call_max_length=500,
        sliding_window_size=30,
    ),
    # Context management
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

### Multi-Agent Configuration

```python
from ghrah.core.config import AgentConfig, ContextConfig

# Planner — lightweight configuration
planner_config = AgentConfig(
    name="planner",
    endpoint_name="planner",
    description="Task planning expert",
    system_prompt="You are a task planning expert. Break down complex tasks into clear steps.",
    max_iterations=5,
)

# Coder — full configuration
coder_config = AgentConfig(
    name="coder",
    endpoint_name="coder",
    description="Code writing expert",
    system_prompt="You are a code writing expert.",
    max_iterations=15,
    context=ContextConfig(
        persistence_type="json_file",
        persistence_root_dir="/tmp/coder_data",
    ),
)

# Reviewer — medium configuration
reviewer_config = AgentConfig(
    name="reviewer",
    endpoint_name="reviewer",
    description="Code review expert",
    system_prompt="You are a code review expert.",
    max_iterations=10,
)
```

### Worker Pool Configuration (endpoint_name)

When multiple Agents share the same LLM configuration (e.g., Worker pool scenarios), use `endpoint_name` to separate the runtime name from the agentconf configuration name:

```python
from ghrah.core.config import AgentConfig

# Worker pool: multiple workers share the same agentconf configuration
# Only need to create one "solve_worker" configuration in agentconf
worker_configs = [
    AgentConfig(
        name=f"solve_worker_{i}",  # Runtime unique identifier
        endpoint_name="solve_worker",  # Model endpoint name in agentconf
        description=f"Solve Worker #{i}",
        system_prompt=f"You are solve Worker #{i}.",
    )
    for i in range(3)
]

# Role alias: different runtime name, shared LLM configuration
reviewer_config = AgentConfig(
    name="code_reviewer_v2",  # New version runtime identifier
    endpoint_name="reviewer",  # Reuse reviewer's LLM configuration
    description="Code review expert v2",
    system_prompt="You are a code review expert.",
)
```

See [Worker Pool Example](../examples/worker_pool.py) for details.

### Distributed Mode Configuration

In distributed mode, Core communicates with Subject via CommandSender:

```python
from ghrah.core.config import AgentConfig, ContextConfig

config = AgentConfig(
    name="coder",
    endpoint_name="coder",
    system_prompt="You are a code writing expert.",
    context=ContextConfig(
        persistence_type="remote",
    ),
)
```

See [Dual-Mode Architecture](distributed-mode_en.md) for details.

## Next Steps

- [Quick Start](getting-started_en.md) — Create Agents using configuration
- [Persistence & Window Management](persistence_en.md) — Deep dive into persistence and window strategy configuration
- [Built-in Ability Reference](builtin-abilities_en.md) — View FSPermissionChecker configuration