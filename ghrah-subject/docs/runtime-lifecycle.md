# 显式初始化与 REST 风格进程内调用

新增 Runtime 入口由 Subject 显式初始化 Core，并在同一进程内通过统一请求/响应契约调用。
旧 `ghrah-subject`、`ghrah-subject-server`、CoreUnit 挂载和 Observer WS 的启动方式保持原样。
本次不提供新增 HTTP 宿主、客户端或转发服务，Observer Mock 与意图收敛也不在本次范围。

本次交付状态：**现有 Core 生命周期与 Subject 进程内调用链已完成交付**（2026-11-12）。
交付范围包括显式 init/ready/stop、失败回收、状态查询、Subject 等待 ready 后的业务调用，
以及已实现的 conversation 资源调用与回复读取。自动化回归和目标环境真实模型验证已通过。
这里的“已交付”表示当前分支实现和验收完成，不表示代码已经提交或版本已经发布。

完整 M0 的配置 PATCH/Operation、资源删除、能力管理、资源事件和断档恢复等属于后续独立扩展，
不作为本次交付的未完成项或验收前置条件；本次交付不宣称完整 M0 已冻结或全量验收通过。
后续需按明确范围单独推进，本次不再自动扩展实现。HTTP 不纳入，Observer Mock 继续暂缓。

## Core 生命周期

`CoreRuntime(config)` 只保存配置。`await init()` 校验 scope、绝对数据库路径和超时，
取得唯一数据库 owner，准备本 scope 的上下文 schema、消息与投递表，恢复未完成投递，
装配 CoreUnit 及能力来源。恢复与关键依赖检查成功后才启动 dispatcher 并发布 ready。
默认不创建资源定义表；显式启用 `enabled_agent_api=True` 后准备 Agent 定义和创建回执表。
两种模式初始化均不自动启动 Agent，也不逐个打开 Agent 上下文连接。

booting/stopping/failed 时已启用的业务命令返回 503；status/meta 保持可查询。stop 请求
立即关闭准入，已排队但未获准的调用也拒绝。初始化期间收到 stop 不再进入 ready。

关闭先等待已受理的业务调用与投递/回复读取，宽限到期后中断剩余执行并保留持久中断状态，
再停止服务、关闭数据库并释放 owner；成功后才进入 stopped。异常进入 failed 并保留诊断。
初始化失败、超时或取消同样回收资源；原始错误不会被清理错误覆盖。失败后须新建实例重试。

`init_timeout` 覆盖初始化协程的可取消等待，不含失败清理，不能抢占同步阻塞代码。
`stop_timeout` 是优雅完成工作的总等待宽限，不是整个资源清理的硬上限。
status.since 是当前阶段起点；日志包含 boot_id、阶段和启动耗时。

ready 只承诺已公布 capability 的可用性。能力授权库迁移、事件水位/system.updated、
HITL 重启通知及收敛控制器尚未实现，不作为本次进程内接入的前置工作。

## Subject 显式启动和调用

显式启动、等待 ready、进程内业务调用和退出回收均已实现，并已通过业务链路和真实模型验证。
新入口由需要它的应用显式选用；原有默认启动流程不自动切换，也无需为使用该入口再新增宿主层。
以下示例展示应用如何按 start → 业务调用 → stop 的顺序使用现有入口。
不需要另起 Core 进程、监听端口或设置网络认证 token。

命令入口沿用当前 main 的模板与授权语义。先在宿主模板库中准备 `runtime.worker`，
其中显式声明 conversation 与 ghrah.core.session_info，并配置实际模型端点。再将模板库注入新 Runtime：

```python
from uuid import uuid4

from ghrah.core.runtime import CoreRuntime, RuntimeConfig
from ghrah.core.unit import CoreUnitConfig
from ghrah.protocol.types import SpawnAgentPayload, SendMessagePayload
from ghrah.subject.manifest_store.store import ManifestStore
from ghrah.subject.runtime_connection import RuntimeConnection, RuntimeConnectionConfig

runtime = CoreRuntime(
    RuntimeConfig(
        scope_ref="demo",
        database_path="/absolute/new-runtime/demo.sqlite3",
        enabled_commands=("spawn_agent", "list_agents", "send_message", "terminate_agent"),
    ),
    unit_config=CoreUnitConfig(manifest_store=ManifestStore("/absolute/templates")),
)
connection = RuntimeConnection(RuntimeConnectionConfig(enabled=True, scope_ref="demo"), runtime)
agent_id = uuid4().hex
try:
    await connection.start()  # Core 已 ready；不会自动创建业务 Agent。
    client = connection.client
    spawned = await client.spawn_agent(
        SpawnAgentPayload(
            project_id="demo",
            config={"name": "worker", "agent_id": agent_id},
            manifest_ref="runtime.worker",
        )
    )
    if not spawned["success"]:
        raise RuntimeError(spawned)
    reply = await client.send_message(
        SendMessagePayload(
            project_id="demo", agent_id=agent_id, target="worker", content="Hello", timeout=30
        )
    )
    await client.terminate_agent(agent_id, "worker")
finally:
    await connection.stop()
```

业务 payload、模板解析、授权装配、消息持久化和回执沿用当前 CoreUnit。
当前 main 已退役 legacy 来源和 wire 直接传 abilities 的启动方式；新入口也不恢复它们。
Core 内部使用 canonical UUID hex 稳定主体，资源 API 的 agt_ ID 是同一身份的可逆编码。

启用 spawn_agent 命令或 Agent 资源入口时，若宿主未注入授权库，Runtime 在同一数据库中准备
现有 AbilityAccessStore，部署策略只授予 session_info；完整宿主策略和授权库可经
unit_config 显式注入，库的 project_id 必须与 scope_ref 相同。宿主注入连接由宿主关闭；
Runtime 自建连接在 Agent 停止后、数据库 owner 释放前关闭。能力申请仍经授权装配边界，
不直接拼装工具列表，不从模板隐式授予更广权限。main 自带的系统元工具按其既有规则装配。

模型端点须在宿主环境中显式配置。新连接不接管旧 Context、Project、Registry 或旧数据库。
未启用配置时无副作用；未指定 enabled_commands 时不开放命令业务写入。
旧预发布 Runtime 数据库若仍用 agt_ 字符串作内部主体，显式拒绝启动，需另行重建或迁移；
本次不自动重写历史消息和上下文身份。

## REST 风格请求/响应契约

调用链为 `RuntimeClient.request → await CoreRuntime.dispatch → CoreUnit`。
method、path、headers、body 以 Python 参数传递，返回 status、headers、body，作用域由连接固定。
这些是进程内接口语义，不产生 Subject 到 Core 的 HTTP 请求。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/v1/status` | 查询生命周期、ready、boot_id、since 和 error。 |
| GET | `/v1/meta` | 查询契约与实际 capability。 |
| POST | `/v1/commands` | 调用显式启用的既有业务命令。 |

```python
response = await connection.client.request(
    "POST",
    "/v1/commands",
    headers={},
    body={"command": "list_agents", "payload": {"project_id": "demo"}},
)
print(response.status, response.headers, response.body)
```

当前支持 spawn_agent、list_agents、send_message、terminate_agent 四项命令。
payload 按既有 DTO 校验，业务回执保持 success/data/error 语义。status=200 且 success=false
仍代表业务失败；503 表示未就绪或停止中。请求的 project_id 必须与连接的 scope_ref 一致。

`POST /v1/commands` 是保留的过渡命令信封。下述 Agent 资源入口通过独立开关启用；
两类请求有各自的数据模型和响应。OpenAPI/TS 用于描述和校验进程内契约，不代表提供 HTTP 服务。

## 协议模型与实际路由的边界

协议包已包含完整装配输入、Agent 资源/摘要、启停结果、分页、消息受理和 Delivery，
由 Python 单向生成 OpenAPI、TS/Zod 与样例。资源路由需要显式启用，支持范围见下节。
Timeline/Node 已提供只读路由。Operation 与资源引用事件目前只有模型；对应资源执行、
Ability 管理与完整能力装配尚未实现，属于后续独立范围，不因生成了模型而宣称可用。

请求严格拒绝未知字段，响应忽略新增字段并接受未来枚举值；status 的 ready 必须与
phase 一致。时间采用 UTC 毫秒精度。Problem 的 detail、instance、details、trace_id
可省略，但不能显式传 null；error 等明确 nullable 的字段保留 null 语义。
405 使用不带领域 code 的标准 Problem，并返回 Allow；领域错误继续提供 code。
这些改动只作用于新 Runtime 契约，不改变旧 CoreUnit 命令回执和 Observer WS 协议。

## 显式 Agent 资源入口

配置 `enabled_agent_api=True`，保留默认 `ability_source="builtin"`。`enabled_commands`
不需要开启。资源定义由 Core 保存，运行事实从 Supervisor 获取，消息和投递从
MessageManager 的同一 SQLite 权威读取；Subject 不保存第二份运行状态。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/v1/agents` | 持久创建 stopped 定义，返回 201、Location、ETag。必填 Idempotency-Key。 |
| GET | `/v1/agents` | 摘要分页，limit 默认 50、范围 1–500，不返回提示词正文。 |
| GET | `/v1/agents/{id}` | 完整配置和当前运行事实。 |
| PUT | `/v1/agents/{id}/state` | 目标 running/stopped，必填 If-Match；返回 agent 和 recovered。 |
| POST | `/v1/agents/{id}/messages` | 持久受理消息后返回 202 和投递引用，必填 Idempotency-Key。 |
| GET | `/v1/deliveries/{id}` | 当前投递状态，可加 wait=0..30 秒。 |
| GET | `/v1/agents/{id}/timeline` | 轨迹摘要分页，limit 默认 100、范围 1–500，支持 session_id / branch_id 筛选。 |
| GET | `/v1/nodes/{id}` | payload v1 正文、大小和内容 ETag；可用 If-None-Match 条件读取。 |

`running` 是请求目标，资源的运行观测值为 `idle` / `busy`，还可返回 `stopped` / `error`。
重复设置目标返回 recovered=null；启动首次上下文返回 initialized，显式重启恢复返回 restored。
Core 重启只加载定义，不自动启动 Agent。配置暂不支持修改，config_revision 与 ETag 为 1；
状态变化不修改配置版本。缺 If-Match 返回 428，版本不符返回 412 和 details.current_etag。

创建回执与定义在同一事务提交；同键同请求返回首次 201 正文并带 Idempotency-Replayed: true，
同键异请求或重复 external_ref 返回 409。消息幂等沿用持久消息权威，跨重启保持相同 message_id / delivery_id。
分页游标在本次 Core 生命周期内有效，重启或无效游标返回 410，调用方从第一页重建。

目前公布能力 `runtime.agents.conversation@1`，仅支持下面显式组合，不隐式补能力：

- builtin 包 ghrah.core，显式包含 conversation 和只读 session_info，visible/available 都为 true，无整包或 skill 申请。
- surface 为 auto、static=[]、max_static=null，mcp_refs=[]。
- model_overrides 可为 null，或声明 temperature、max_tokens、top_p、top_k、extra_params。
- context_policy.window 可为 null，或声明现有窗口策略参数，详见下文；context_policy.overrides 仍须为 null。
- 默认权限策略，auto_approve / safe_extra_commands 为空，safe_extra_sub_commands=null。
- 创建目标为 stopped；创建时 running 返回 501，随后显式 PUT 启动。
- 消息 kind=user；system/command 返回 501。metadata 作为不解释的客户端数据封装保存，不作为内部控制字段。

这些组合之外的合法装配请求返回 501；字段缺失或未知字段返回 422。整个 config 的 UTF-8 配额
默认 8 MiB，可通过 config_quota 修改，超限整体返回 413，提示词不截断。所有装配字段，
包括可空字段，都必须显式出现；template_ref 仅审计，不读取模板或 manifest。

模型覆盖和窗口策略复用现有 Core builder，未声明的参数沿用其默认值。模型的 max_tokens
须为正整数，top_p 范围 0–1、top_k 为非负整数；extra_params 是显式厂商参数对象。
窗口可声明 max_tokens、strategies、tool_call_max_length、sliding_window_size、compact_threshold、
compact_keep_recent、compact_method。strategies 保持调用方声明顺序，支持 tool_call_fold、
sliding_window、truncation、llm_summary；数值和枚举非法返回 422，未支持的参数名返回 501，
不会静默忽略。示例：

```json
{
  "model_overrides": {"temperature": 0, "max_tokens": 512},
  "window": {"max_tokens": 8192, "strategies": ["tool_call_fold", "truncation"]}
}
```

新增读面公布 `runtime.timeline@1`，直接查询 Core 上下文权威。每个 Agent 的 node_seq
持久存储在节点行上，在节点提交的同一事务里分配，不使用 rowid 或时间戳。
显式开启资源入口才进行列、索引和计数器迁移；旧入口创建的非资源 Agent 不参与分配。
旧版资源 Agent 的历史节点在 init 时按 iteration/id 确定性回填新序号，该顺序不代表历史提交顺序。
GET 不做迁移、不写库。

Timeline 按 `(branch_id, node_seq)` 排序，每次遍历固定 watermark_seq，期间新增节点需重新
从第一页读取。snapshot_cursor 可重读该快照，next_cursor 向后翻页；翻页时保持相同筛选条件。
游标绑定 Agent、筛选和当前 Core 启动实例；重启、篡改、筛选变化或翻页锚点已删除返回
410 CURSOR_EXPIRED。列表只返回最多 256 字符摘要及 payload_ref，不返回消息正文。
当前未实现增量追尾、字段裁剪、归档保留策略或读副本。

Node 的 size 与 payload_ref.size 都是公开 payload 的规范 JSON UTF-8 字节数。
`schema_version` 查询参数是当前入口的显式版本选择，默认 1；其他正整数返回
406 PAYLOAD_UNSUPPORTED。该查询参数是当前实现约定，需在正式契约评审确认。
内容 ETag 与 Agent 配置 ETag 相互独立；匹配 If-None-Match 返回 304。
payload v1 消息公开 role、content_blocks、source；不公开内部消息元数据、agent_state、
delivery_context 或模型连接信息。节点 metadata 只公开 node_kind、compaction、
visible_response_retry_count，token_usage 映射为 usage。

```python
from ghrah.subject.runtime_connection import RuntimeConnection, RuntimeConnectionConfig

connection = RuntimeConnection(
    RuntimeConnectionConfig(
        enabled=True,
        enabled_agent_api=True,
        scope_ref="demo",
        database_path="/absolute/new-runtime/demo.sqlite3",
    )
)
try:
    await connection.start()
    client = connection.client
    created = await client.create_agent(
        {
            "external_ref": "demo:worker",
            "config": {
                "model": {"endpoint_name": "your-endpoint", "model_overrides": None},
                "prompt": {"system_prompt": "You are a helpful assistant.", "template_ref": None},
                "context_policy": {
                    "max_iterations": 10,
                    "communication_timeout": 300,
                    "window": None,
                    "overrides": None,
                },
                "ability_request": {
                    "surface": {"mode": "auto", "static": [], "max_static": None},
                    "packages": [
                        {
                            "package": "ghrah.core",
                            "origin": "builtin",
                            "whole_container": False,
                            "operations": [
                                {"key": "conversation", "visible": True, "available": True},
                                {"key": "session_info", "visible": True, "available": True},
                            ],
                            "skills": [],
                        }
                    ],
                    "mcp_refs": [],
                },
                "permission_request": {
                    "auto_approve": [],
                    "require_approval_by_default": True,
                    "safe_extra_commands": [],
                    "safe_extra_sub_commands": None,
                },
            },
        },
        idempotency_key="create-worker-1",
    )
    assert created.status == 201, created.body
    agent_id, etag = created.body["agent_id"], created.headers["ETag"]
    started = await client.set_agent_state(agent_id, "running", etag=etag)
    assert started.status == 200, started.body
    accepted = await client.submit_message(agent_id, "Hello", idempotency_key="message-1")
    assert accepted.status == 202, accepted.body
    while True:
        delivery = await client.get_delivery(accepted.body["delivery_id"], wait=30)
        assert delivery.status == 200, delivery.body
        if delivery.body["state"] in {"completed", "failed", "interrupted", "blocked"}:
            break
        # 30 秒内未结束时继续 GET；不要更换幂等键重复提交。
    assert delivery.body["state"] == "completed", delivery.body
    node = await client.get_node(delivery.body["last_node_id"])
    assert node.status == 200, node.body
    replies = [
        block["text"]
        for message in node.body["payload"]["messages_delta"] or []
        if message.get("role") == "ai"
        for block in message.get("content_blocks", [])
        if block.get("type") == "text"
    ]
    print("".join(replies))
    stopped = await client.set_agent_state(agent_id, "stopped", etag=etag)
    assert stopped.status == 200, stopped.body
finally:
    await connection.stop()
```

Delivery 提供状态和节点引用；完成后用 last_node_id 读取 Node 正文，或经 Timeline 分页浏览历史。
能力管理、动态配置 PATCH/Operation、删除、资源事件与断档重建尚未交付；
本入口不代表完整 M0 已验收。

## 验证

自动化测试覆盖显式启用、就绪门禁、失败回收、业务调用、停止及旧入口兼容。
真实模型验收须显式指定已有 agentconf 端点：

```sh
uv sync --locked --all-packages --group dev
uv run --locked python scripts/verify_runtime_model.py --endpoint your-endpoint
uv run --locked python scripts/verify_runtime_model.py --api resources --endpoint your-endpoint
```

脚本不注入测试 LLM，通过 Subject 新入口在进程内完成 ready、启动 Agent、发送消息、
校验回复标记、终止 Agent 和停止 Core。临时数据库退出时删除，不修改模型配置，
不输出凭据或完整模型正文。资源模式经 Delivery API 验证 completed，再通过 Node API 核对实际 AI 回复中的标记，
同时验证 Timeline 可读到完成节点，全程不直读 Core SQLite。普通 pytest 不会触发真实模型请求。

协议单一来源为 ghrah.protocol.runtime、runtime_agents、runtime_resources 和既有命令 payload，生成一致性检查：

```sh
uv run python scripts/export_runtime_contracts.py --check
```
