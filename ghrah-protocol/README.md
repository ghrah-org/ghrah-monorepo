# ghrah-protocol

[简体中文](./README_zh.md)

Shared protocol type definitions for the ghrah distributed agent cluster framework.

Defines all WebSocket message envelope formats, command types, event types, and payload models using Pydantic for type safety and JSON serialization compatibility.

## Project Structure

```
ghrah-protocol/
├── pyproject.toml
├── LICENSES/
│   └── Apache-2.0.txt
└── src/ghrah/protocol/
    ├── __init__.py       # Package re-exports (via the types.py facade)
    ├── types.py          # Compatibility facade: re-exports everything below
    ├── enums.py          # Enums: ClientType / CommandType / EventType / SystemType / domain enums
    ├── routing.py        # Command routing groups (*_COMMANDS) + agent-scoped event contract
    ├── payloads/         # Payload models by domain (agent, persist, workspace, manifest,
    │                     #   session, task, project, room, system)
    ├── envelope.py       # Envelope, type → payload registries, envelope_from_dict
    └── factories.py      # create_command_result / create_event / ... helpers
```

## License

Apache 2.0

## Scheme 契约与目录描述

`ghrah.protocol.scheme` 定义 ContractDescriptor／ActionDescriptor 和按主体裁剪的 CatalogSnapshot／DiscoveredResource／DiscoveredAction。动作 schema 从领域的唯一作者模型单向导出，公共格式不另行维护业务动作定义；指纹用于精确内容匹配，不认证来源或判断任意 schema 的兼容性。TS 的 `@ghrah/protocol` 提供对应 Zod 消费者；Python 生成样本及字段对齐测试覆盖双侧。

目录描述是一次读取的快照，发现、schema 披露与执行许可分开；它不签发 grant，也不替代调用时的最终核定。本批只增加描述载体，没有增加新的消息路由或启动生产服务。
