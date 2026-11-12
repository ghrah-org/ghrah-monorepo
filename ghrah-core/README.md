# Jiuxia/ghrah

[简体中文](./README_zh.md)

**This is an Alpha version framework that can be used for proof of concept, it is not production-ready and may have numerous breaking updates**

A general-purpose distributed agent cluster framework

Can be used to build a secure, auditable runtime core for various workloads, ranging from single agents to agent cluster systems

## Quick Start

### Install Dependencies

```bash
uv sync
```

### Configure Agentconf (Recommended)

使用 Agentconf 0.4 的 TUI 配置 Provider 与模型端点。ghrah 通过显式 `endpoint_name` 引用端点，请求参数由自身保存。

```bash
uv run agentconf
```

旧配置迁移、接口格式与调用参数见 [模型端点与配置迁移](docs/model-endpoints.md)。当前依赖 `agentconf>=0.4.0,<0.5`（独立 MCP Provider／schema 6），使用显式端点配置；旧库与旧配置须先完成迁移。

### Direct Configuration (Without agentconf)

If you prefer not to use agentconf, you can directly construct a `ChatFormat` and inject it into the Agent:

```python
from ghrah.ability.builtin.core.operations.conversation import ConversationAbility
from ghrah.agents.base import ActorAgent
from ghrah.chat.format.openai import OpenAIFormat
from ghrah.core.config import AgentConfig

config = AgentConfig(name="assistant", system_prompt="You are an AI assistant.")
agent = ActorAgent(config)
agent.register_ability(ConversationAbility())

# Directly construct ChatFormat and inject (bypasses agentconf， This is a workaround, and it will be improved later)
agent._llm = OpenAIFormat(
    model="gpt-4o",
    api_key="sk-your-api-key",
    base_url="https://api.openai.com/v1",
)

response = await agent.chat("Hello!")
```

Available `ChatFormat` subclasses: `OpenAIFormat`, `AnthropicFormat`, `DeepSeekFormat`. See [Configuration Reference](docs/configuration_en.md) for details.

### Run Examples

```bash
uv run python examples/simple_chat.py
```

Or run other examples under the `examples` directory

## Project Structure

```
src/ghrah/
├── core/           # Core abstractions: config, messages, events, exceptions, HITL
├── agents/         # Agent implementations: ActorAgent base class
├── chat/           # LLM interaction layer: ChatMessage, ContentBlock, ChatFormat
│   └── format/     # Format adapters: OpenAIFormat, AnthropicFormat
├── abilities/      # Ability system: Ability interface, hooks, executors, built-in abilities
│   └── builtin/    # Built-in abilities: conversation, file operations, task termination, cluster operations, etc.
├── context/        # Context management: ActionChain, StateManager, MessageStore, window policies
│   └── persistence/# Persistence backends: JSON, SQLite, in-memory, remote
├── llm/            # LLM integration: LLMFactory (agentconf → ChatFormat)
└── communication/  # Communication layer: Router, Registry, Supervisor
```

## Documentation

For full usage documentation, see the [docs/](docs/) directory:

| Document | 中文 | English |
|----------|------|---------|
| Installation & Quick Start | [getting-started.md](docs/getting-started.md) | [getting-started_en.md](docs/getting-started_en.md) |
| Core Concepts | [core-concepts.md](docs/core-concepts.md) | [core-concepts_en.md](docs/core-concepts_en.md) |
| Ability System | [ability-system.md](docs/ability-system.md) | [ability-system_en.md](docs/ability-system_en.md) |
| Hook Mechanism | [hook-mechanism.md](docs/hook-mechanism.md) | [hook-mechanism_en.md](docs/hook-mechanism_en.md) |
| Context Management | [context-management.md](docs/context-management.md) | [context-management_en.md](docs/context-management_en.md) |
| Multi-Agent Communication | [multi-agent.md](docs/multi-agent.md) | [multi-agent_en.md](docs/multi-agent_en.md) |
| Persistence & Window Management | [persistence.md](docs/persistence.md) | [persistence_en.md](docs/persistence_en.md) |
| Built-in Ability Reference | [builtin-abilities.md](docs/builtin-abilities.md) | [builtin-abilities_en.md](docs/builtin-abilities_en.md) |
| Configuration Reference | [configuration.md](docs/configuration.md) | [configuration_en.md](docs/configuration_en.md) |
| Error Handling | [error-handling.md](docs/error-handling.md) | [error-handling_en.md](docs/error-handling_en.md) |
| Architecture & Flow Diagrams | [architecture.md](docs/architecture.md) | [architecture_en.md](docs/architecture_en.md) |
| Chat Interaction Layer | [chat-module.md](docs/chat-module.md) | [chat-module_en.md](docs/chat-module_en.md) |
| Dual-Mode Architecture | [distributed-mode.md](docs/distributed-mode.md) | [distributed-mode_en.md](docs/distributed-mode_en.md) |
| HITL Human-in-the-Loop | [hitl.md](docs/hitl.md) | [hitl_en.md](docs/hitl_en.md) |

## Development

```bash
# Install optional-dependencies
uv sync --extra dev

# Run tests
uv run pytest tests/ -v

# Lint
uv run ruff check src/ tests/
```

## License

Apache 2.0

## 新增显式 Runtime 入口

现有生命周期与 Subject 进程内调用链已完成实现和验收；完整 M0 的资源管理扩展另行交付。

新增的显式 `CoreRuntime` 生命周期、REST 风格进程内派发与 Subject 新客户端入口见
[Runtime 生命周期接入](../ghrah-subject/docs/runtime-lifecycle.md)。显式启用 `enabled_agent_api=True`
可使用 Agent 定义、条件启停、消息/投递及 Timeline/Node 只读资源接口。
目前支持内置 conversation 组合，并可显式传入模型覆盖和窗口策略。
默认不启用资源接口；原有 CoreUnit 用法保持原样。

## 可选 Scheme 资源切片

默认 Core 安装不要求 ghrah-scheme。文件 Ability 的 Scheme 接入属于显式 `scheme` extra，目前使用 workspace 或本地 wheel 组合验证；ghrah-scheme 尚未对外发布。完整示例与边界见[文件 Ability 资源切片](../ghrah-scheme/docs/2026-10-29-ability-resource-slice.md)，现有生产 Agent 通路尚未切换。

## 新版 Ability 的本地获授内容

Subject／CoreUnit 显式选择 `ability_source="builtin"` 后，生产 spawn 会为每个 Agent 创建内容服务，并在注册完成时绑定其运行代次。目录、正文和附件都读取 `AbilityAccessStore` 中的当前授予与已提交快照；运行期间不扫描旧 Skill 根，也不要求启动 Scheme 或新内核。

Agent Manifest 中声明 `ghrah.core.list_abilities` 和 `ghrah.core.load_skill`，部署方再通过 `ability_grant_policy` 显式授予所需 Skill。例如，`GrantRule(package="ghrah.ability-builder", skill="ability-package-development-guide")` 授予内置开发指南；它会与模板申请的工具一起核定。模板本身不产生权限，未配置策略时只保留已有授予。

模型每轮看到 `alias/skill` 形式的名称和摘要。调用 `load_skill(full_name="ability-builder/ability-package-development-guide")` 读取完整正文；传入 `path="references/style.md"` 可读取该 Skill 已登记且获授的相对文本附件。返回的 `path` 是包内路径，`uri` 是逻辑内容地址，不能交给文件工具当作宿主路径。

正文和文本附件单次完整加载最多 1 MiB；二进制附件通过内容服务的分块读取接口返回。来源目录改变或删除不会改变已提交字节。撤销、版本切换、Agent 停止或运行换代后，旧读取引用失效；子 Agent 独立装配内容服务，Context rebase 也须显式提供并核验目标主体的内容服务。读取 Skill 不会自动授予 Operation、绑定工具或启动 MCP。
