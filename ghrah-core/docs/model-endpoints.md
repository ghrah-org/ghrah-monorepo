---
title: 模型端点与配置迁移
description: 说明 ghrah 如何消费 agentconf 模型端点、保存请求参数，以及离线转换旧 Agent 配置。
tags: [配置, 迁移, LLM]
created: 2026-10-28
updated: 2026-10-28
---

# 模型端点与配置迁移

> 面向配置维护者和适配器调用者。端点决定连接到哪里，请求参数决定某个 Agent 怎样调用它。当前消费代码依赖 agentconf `>=0.3.0,<0.4`。运行 `uv sync --all-packages --group dev` 同步实际环境；不能用旧 0.1.0 客户端打开新版配置库。

## 配置由谁保存

agentconf 的 Provider 保存服务地址、密钥及连接选项。模型端点保存实际请求模型名、用途、接口格式和可选能力引用。ghrah 保存 Agent 的运行时名称、提示词、能力列表，以及 `temperature`、`top_p`、`max_tokens`、`top_k` 和 `extra_params` 等请求参数。

多个 Agent 可以引用同一个端点，并使用各自的请求参数。默认工厂要求显式 `endpoint_name`；运行时 `name` 不参与端点查找。注入自有 LLM 或自有工厂的路径仍可不使用 agentconf。

```python
from ghrah.types.config_types import AgentConfig, ModelOverrides

worker = AgentConfig(
    name="worker_0",
    endpoint_name="shared-chat",
    model_overrides=ModelOverrides(
        temperature=0,
        max_tokens=2048,
        extra_params={"stop": ["END"]},
    ),
)
```

`None` 表示未指定，`0` 与 `False` 是显式值。工厂不会从旧 Agent 表读取默认参数。调用者未指定 temperature 时省略该字段；Anthropic 的必需输出预算未指定时使用适配器默认值 4096。

## 端点格式与能力

格式决定调用哪个接口，厂商只在格式内部提供必要的适配。

| api_format | 实际接口 | 行为 |
| --- | --- | --- |
| `openai.chat_completions` | `client.chat.completions.create` | OpenAI 兼容消息；DeepSeek 厂商使用专用适配器保留 `reasoning_content` |
| `openai.responses` | `client.responses.create` | 原生输入项、工具调用与回传、推理状态、完整响应及原生流 |
| `anthropic.messages` | `client.messages.create` | Anthropic 系统消息、工具链与用量归一化 |

未知格式抛出 `UnsupportedFormatError`，非 `llm` 用途抛出用途错误。工厂不会根据厂商猜测另一个接口。有效能力、逐字段来源和可空档案元数据分别保存在适配器的 `model_capabilities`、`capability_sources` 和 `model_metadata` 中；上下文预算继续消费这些有效能力，显式窗口声明优先。

Provider 的 `connection_options` 当前接受 `timeout`、`max_retries`、`default_headers` 和 `default_query`，其他字段明确拒绝。请求扩展参数中，SDK 已知字段直接传入，厂商扩展通过 SDK 的 `extra_body` 发送。显式类型字段覆盖同名扩展字段；扩展参数不能改写模型、密钥、连接身份、消息、工具列表或 SDK 的 `extra_body` 包装层。

Responses 将 `max_tokens` 映射为 `max_output_tokens`，明确拒绝 `top_k`。Chat Completions 和 Anthropic 当前只提供完整生成，收到 `stream=True` 时明确诊断。所有未知扩展会保留，厂商不接受的扩展由请求错误报告，不会被适配器静默丢弃。

## Agent Manifest 使用 major 2

Agent Manifest 的模型引用发生破坏性变化，因此只接受 major 2；Ability Manifest 仍接受 major 1。两个版本范围分别维护。旧 Agent Manifest 即使缺少其他字段，也先报告迁移要求。

```yaml
manifest: agent
version: "2"
metadata:
  namespace: example
  name: worker
model:
  endpoint_name: shared-chat
  temperature: 0
  max_tokens: 2048
  extra_params:
    stop: [END]
system_prompt: 你负责完成分配的任务。
abilities:
  - type: conversation
```

## 旧配置如何离线转换

旧 `agent_config_name` 指向旧 Agent。它与端点名称不一定相同，所以不能直接改名字段，也不能把运行时名称当成新端点名称。转换器读取 agentconf 迁移生成的 `export_version=1` Agent 导出，根据映射和请求参数生成新配置。

在 monorepo 根目录运行，默认只输出差异和逐字段来源报告：

```sh
uv run python scripts/migrate_agentconf_consumer.py old.yaml \
  --agent-export agent-export.json --output new.yaml
```

审阅后使用同一命令增加 `--apply`。它只创建新文件，文件权限为 0600；目标已存在、目标等于输入或指向输入的链接时均拒绝覆盖。转换器不打开 agentconf 数据库、不调用模型、不修改原文件。

转换支持 Agent Manifest、原始 AgentConfig、spawn 的 `config` 和 Project 的 `agents` 规格列表。引用 Manifest 的规格需另行转换对应 Manifest 文件。转换先承接导出参数，再以 ghrah 中非 None 的显式参数覆盖；扩展字段按键合并并保留 False、None 和未知结构。每个最终参数都有来源记录。缺映射、校验和不符、重复映射、损坏或冲突的模型绑定会停止转换。

可复核的旧配置、导出、新配置和来源报告在 [迁移样例](../examples/agentconf_migration/)。样例中的两个 Agent 共用端点，但 temperature 与 stream 不同；`stream=True` 在当前 Chat Completions 适配器会明确拒绝，运营者需审阅并显式调整请求策略，不能让转换器擅自删除旧参数。转换结果从此成为运行期参数来源，运行时不会继续读取导出文件。

## Responses 完整生成与原生流

`OpenAIResponsesFormat.generate()` 返回既有 `LLMResponse`。设置 `extra_params.stream=True` 时，它聚合原生流并返回完整终态。显式消费流时使用 `stream()`，每个 `LLMStreamEvent` 包含一个文本、推理、工具参数增量或错误块；终态事件携带完整响应和用量。增量只用于展示，工具执行应使用终态解析后的完整参数。

流失败或截断会报告 `LLMError`，厂商失败和不完整终态映射为 `ErrorBlock`。任务取消继续传播 `CancelledError`；正常结束、异常、取消和消费者显式关闭生成器都会关闭 SDK 流。提前退出时调用者应使用 `contextlib.aclosing`。

Responses 输出项通过 `response_metadata.responses_output` 随内部消息保存，保留工具 `call_id`、输出项 ID、不透明推理状态和消息阶段。回传时按原顺序重放这些输出项，工具结果使用 `function_call_output`；不会把它们重新编造成 Chat Completions 消息。

## 相关文档

- [配置参考](configuration.md) — Agent、窗口与持久化配置。
- [安装与快速开始](getting-started.md) — 新端点配置入口。
- [长期 Agent 实体](agent-entity.md) — 端点引用与请求参数的持久规格。
