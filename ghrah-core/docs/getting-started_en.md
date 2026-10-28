# Installation & Quick Start

## Prerequisites

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) package manager
- [agentconf](https://github.com/ghrah/agentconf) CLI (for LLM configuration management)

## Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/ghrah/python-actor.git
cd python-actor
uv sync
```

## LLM Configuration

ghrah 使用 agentconf 管理 Provider、模型端点和静态能力。运行时通过显式 `endpoint_name` 选择端点，请求参数由 ghrah 的 `ModelOverrides` 持有。

使用 agentconf 0.3 的 TUI 配置 Provider 与端点：

```bash
uv run agentconf
```

端点必须声明实际请求模型名和接口格式：`openai.chat_completions`、`openai.responses` 或 `anthropic.messages`。DeepSeek 使用 `provider_type=deepseek` 与 `openai.chat_completions`，以保留推理内容回传。

也可以用 SDK 显式创建配置（需要先安装兼容的 agentconf 0.3）：

```python
import os
from agentconf import AgentsConfig

with AgentsConfig() as config:
    config.create_provider(
        "openai", "openai", "https://api.openai.com/v1", os.environ["OPENAI_API_KEY"]
    )
    config.create_model_endpoint("openai", "assistant", "your-model-name", "openai.responses")
```

ghrah 配置使用 `AgentConfig(name="runtime-name", endpoint_name="assistant")`。运行时名称不参与端点查找。旧库与旧配置先按 [模型端点与配置迁移](model-endpoints.md) 转换，不能直接改名旧 Agent 引用。

### Environment Variables

Configure environment variables via `.env` file (see [`.env.example`](../.env.example)):

```bash
# agentconf database path (default: ~/.agentconf/config.db)
# AGENTCONF_DB_PATH=~/.agentconf/config.db

# OpenAI API Key (can also be configured via agentconf CLI)
# OPENAI_API_KEY=sk-your-key

# Anthropic API Key
# ANTHROPIC_API_KEY=your-key
```

### Option 2: Direct ChatFormat Construction (Without agentconf)

If you prefer not to use agentconf for configuration management, you can directly construct a `ChatFormat` subclass and inject it into the Agent. This approach is suitable for quick prototyping, CI environments, or scenarios where you have custom configuration management needs.

> **How it works**: `ActorAgent._ensure_llm()` checks whether `self._llm` is already set on first invocation. If it has been set, it is used directly and agentconf resolution is skipped. Simply inject the ChatFormat before calling `receive()` / `chat()`.

#### Using OpenAI-Compatible APIs

```python
import os
from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.agents.base import ActorAgent
from ghrah.chat.format.openai import OpenAIFormat
from ghrah.core.config import AgentConfig


async def main():
    config = AgentConfig(
        name="assistant",
        endpoint_name="assistant",
        description="General chat assistant",
        system_prompt="You are a friendly AI assistant.",
    )

    agent = ActorAgent(config)
    agent.register_ability(ConversationAbility())

    # Directly construct ChatFormat and inject (bypasses agentconf)
    agent._llm = OpenAIFormat(
        model="gpt-4o",
        api_key="sk-your-api-key",
        base_url="https://api.openai.com/v1",
    )

    response = await agent.chat("Hello, please introduce yourself.")
    print(f"AI: {response}")
```

## Quick Start

### Single Agent Chat

The simplest usage — create an Agent, register ConversationAbility, and chat with it:

```python
import asyncio
from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.agents.base import ActorAgent
from ghrah.core.config import AgentConfig


async def main():
    # Create Agent config (name corresponds to agentconf agent name)
    config = AgentConfig(
        name="assistant1",
        endpoint_name="assistant1",
        description="General chat assistant",
        system_prompt="You are a friendly AI assistant.",
    )

    # Create Agent and register at least one Ability
    agent = ActorAgent(config)
    agent.register_ability(ConversationAbility())

    # Chat using the simplified interface
    response = await agent.chat("Hello, please introduce yourself.")
    print(f"AI: {response}")


asyncio.run(main())
```

Run:

```bash
uv run python examples/simple_chat.py
```

### Agent with Abilities

Register Abilities to give the Agent specific capabilities:

```python
from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.abilities.builtin.end_task import EndTaskAbility

# Create Agent and register Abilities
agent = ActorAgent(config)
agent.register_ability(ConversationAbility())
agent.register_ability(EndTaskAbility())
```

Run:

```bash
uv run python examples/ability_agent.py
```

### Multi-Agent Collaboration

Use `SupervisorActor` to manage communication between multiple Agents:

```python
from ghrah.communication import SupervisorActor
from ghrah.core.config import AgentConfig

supervisor = SupervisorActor()

# Register multiple Agents
configs = [
    AgentConfig(name="planner", endpoint_name="planner", description="Task planning expert"),
    AgentConfig(name="coder", endpoint_name="coder", description="Code writing expert"),
    AgentConfig(name="reviewer", endpoint_name="reviewer", description="Code review expert"),
]
for config in configs:
    await supervisor.spawn_agent(config)

# Send message to a specific Agent
response = await supervisor.send("planner", "Design a web server architecture")
```

Run:

```bash
uv run python examples/multi_agent.py
```

### Agent with File Operations

Register file system Abilities to enable the Agent to read and write files:

```python
from ghrah.abilities.builtin import (
    ConversationAbility,
    EndTaskAbility,
    ReadFileAbility,
    WriteFileAbility,
    FSPermissionChecker,
)

# Create Agent with file permissions
config = AgentConfig(name="coder", endpoint_name="coder", description="Code writing assistant")
agent = ActorAgent(config)

# Register Abilities
agent.register_ability(ConversationAbility())
agent.register_ability(EndTaskAbility())
agent.register_ability(
    ReadFileAbility(permission_checker=FSPermissionChecker(allowed_dirs=["/tmp/workspace"]))
)
agent.register_ability(
    WriteFileAbility(permission_checker=FSPermissionChecker(allowed_dirs=["/tmp/workspace"]))
)
```

Run:

```bash
uv run python examples/multi_agent_parallel.py
```

## Verify Installation

Run the test suite to confirm installation:

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific module tests
uv run pytest tests/abilities/ -v
uv run pytest tests/agents/ -v

# Lint check
uv run ruff check src/ tests/
```

## Next Steps

- [Core Concepts](core-concepts_en.md) — Understand ActorAgent, Ability, Hook, and other core concepts
- [Ability System](ability-system_en.md) — Learn how to develop and register Abilities
- [Multi-Agent Communication](multi-agent_en.md) — Build multi-agent collaboration systems
- [Configuration Reference](configuration_en.md) — View all configuration options