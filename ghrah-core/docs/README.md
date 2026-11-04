# ghrah 文档

通用分布式智能体集群开发平台，集成 agentconf 配置管理。

## 文档索引

| 文档 | 中文 | English |
|------|------|---------|
| 安装与快速开始 | [getting-started.md](getting-started.md) | [getting-started_en.md](getting-started_en.md) |
| 核心概念 | [core-concepts.md](core-concepts.md) | [core-concepts_en.md](core-concepts_en.md) |
| Agent 实体接口 | [agent-entity.md](agent-entity.md) | — |
| Ability 系统 | [ability-system.md](ability-system.md) | [ability-system_en.md](ability-system_en.md) |
| Ability 包开发手册 | [ghrah-ability-包开发手册.md](ghrah-ability-包开发手册.md) | — |
| ability 调用说明 | [ghrah-ability-调用说明文档.md](ghrah-ability-调用说明文档.md) | — |
| Hook 机制 | [hook-mechanism.md](hook-mechanism.md) | [hook-mechanism_en.md](hook-mechanism_en.md) |
| 上下文管理 | [context-management.md](context-management.md) | [context-management_en.md](context-management_en.md) |
| 多 Agent 通信 | [multi-agent.md](multi-agent.md) | [multi-agent_en.md](multi-agent_en.md) |
| 持久化与窗口管理 | [persistence.md](persistence.md) | [persistence_en.md](persistence_en.md) |
| 内置 Ability 参考 | [builtin-abilities.md](builtin-abilities.md) | [builtin-abilities_en.md](builtin-abilities_en.md) |
| 配置参考 | [configuration.md](configuration.md) | [configuration_en.md](configuration_en.md) |
| 异常处理 | [error-handling.md](error-handling.md) | [error-handling_en.md](error-handling_en.md) |
| 架构图与流程图 | [architecture.md](architecture.md) | [architecture_en.md](architecture_en.md) |
| Chat 交互层 | [chat-module.md](chat-module.md) | [chat-module_en.md](chat-module_en.md) |
| 双模式架构 | [distributed-mode.md](distributed-mode.md) | [distributed-mode_en.md](distributed-mode_en.md) |
| HITL 人机协作 | [hitl.md](hitl.md) | [hitl_en.md](hitl_en.md) |

## 快速导航

- **新手入门**：从 [安装与快速开始](getting-started.md) 开始
- **理解架构**：阅读 [核心概念](core-concepts.md) 和 [架构图](architecture.md)
- **开发 Ability**：参考 [Ability 包开发手册](ghrah-ability-包开发手册.md)（能力包格式与多技能）和 [ability 调用说明](ghrah-ability-调用说明文档.md)（扫描/捕获/授权入口）；旧类接口见 [Ability 系统](ability-system.md)
- **内置能力包**：参考 [内置 Ability 参考](builtin-abilities.md)；内置包树见 `ghrah-core/src/ghrah/ability/builtin/`（含 `ability-builder` 开发指南包）
- **多 Agent 协作**：查看 [多 Agent 通信](multi-agent.md)
- **配置调优**：查阅 [配置参考](configuration.md) 和 [持久化与窗口管理](persistence.md)
- **Chat 交互层**：了解 [ChatMessage 与 ContentBlock](chat-module.md)
- **分布式部署**：阅读 [双模式架构](distributed-mode.md)
- **HITL 审批**：了解 [人机协作流程](hitl.md)