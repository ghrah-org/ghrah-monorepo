# AgentEntity 使用说明

`AgentEntity` 表示以 `project_id + agent_id` 标识的长期 Agent。一个句柄统一访问规格、Context、Mailbox、Ability 与运行实例；停止 ActorAgent 不删除该实体。

## 数据归属

| 内容 | 权威 | 实体入口 |
|---|---|---|
| 身份、名称、cluster、能力配置、期望启停状态 | Subject ProjectStore 中既有 AgentSpec | `snapshot()` |
| 会话、分支、ActionChain | Core ContextManager / SQLite | `context` |
| 消息、投递状态 | 项目 MessageManager / 同一 SQLite 中的 MM 表 | `mailbox` |
| 当前执行实例及实际装载能力 | Core 运行注册表 / ActorAgent | `runtime`、`abilities.list()` |

实体不建第二套状态表，不缓存可独立写入的规格、历史或消息。Core 的实体类通过 `AgentEntityBackend` 协议访问宿主；Subject 的 `ProjectManager.agents` 提供现有服务的适配实现。Core 不依赖 Subject。

`AgentSnapshot` 包含 `identity`、`specification`、`specification_revision`、`runtime`、`context`、`mailbox`、`abilities` 和 `observed_at`。快照汇集各权威的当前观测，不是跨子系统的原子快照。历史正文及消息正文通过各端口单独查询。

## Python

在已装配的 Subject Context 中，使用既有项目与 cluster 的 ID：

```python
from ghrah.protocol.payloads.entity import AgentEntityCreatePayload

manager = ctx.get("project_manager")
agent = await manager.agents.create(
    AgentEntityCreatePayload(
        project_id=project_id,
        cluster_id=cluster_id,
        name="designer",
        start=False,  # 必须显式选择；此时仅创建持久规格
        ability_types=["send_message"],
    )
)

# 已有 Agent 无需迁移身份或复制上下文。
agent = manager.agents.get(project_id, agent.identity.agent_id)
await agent.start()
snapshot = await agent.snapshot()
sessions = await agent.context.list_sessions()
abilities = await agent.abilities.list()
await agent.stop(expected_incarnation_id=snapshot.runtime.incarnation_id)

# 同一实体句柄在离线时仍可查询持久数据。
inbox = await agent.mailbox.list_inbox(limit=50)
await agent.start()  # 相同 agent_id，新的 incarnation_id
```

`create(start=True)` 要求项目处于 active。启动失败时保留可诊断的实体规格；调用者应检查创建后快照中的实际运行状态与 `runtime_error`。`start()` 启动失败则返回错误。句柄的 `get()` 仅绑定身份；每次操作重新验证项目与实体存在性。

## 前端 SDK

```typescript
const entity = client.agent(projectId, agentId);
const snapshot = await entity.snapshot();
await entity.context.listSessions();
await entity.mailbox.listInbox(50);
await entity.abilities.list();
await entity.stop(undefined, snapshot.runtime.incarnation_id ?? undefined);
await entity.start();
```

`client.createAgentEntity({...})` 和 `client.listAgentEntities(projectId)` 返回现有 `CommandResultPayload` 回执格式。绑定句柄上的方法会在失败回执时抛出 Error。

新增命令由 Subject ProjectUnit 处理，Python 与 TypeScript 契约同步：

- `agent_entity_create`：显式 `project_id/name/cluster_id/start`，可选 `manifest_ref/system_prompt/ability_types`。
- `agent_entity_list`：按项目列出持久实体及运行观测，不加载全部历史。
- `agent_entity`：固定 `project_id/agent_id`，加闭集 `operation`，不接受任意方法名或身份覆盖。

## 前端实体管理

在项目的 **Agent 概览** 中，点击 **创建 Agent 实体**，或在已有 Agent 条目中点击 **管理实体**。管理入口绑定持久身份，Agent 离线时仍可打开。

- **创建**：选择 cluster，填写名称和可选 system prompt；从后端注册表多选能力类型，或填写 manifest 引用。是否立即启动由复选框显式选择，默认仅创建持久规格。
- **概览**：展示 project_id / agent_id、期望状态、实际在线状态、忙碌状态、incarnation_id 和错误原因。启动保留实体身份；停止需要确认，可能中断当前执行。
- **会话与历史**：在线或离线均可浏览持久会话、分支和已提交历史；页面每次最多显示 100 个节点，可展开查看消息与执行结果。选择会话仅用于浏览，不改变活动会话。创建和激活会话要求实体在线、空闲且期望运行。
- **消息**：切换收件箱与发件箱，按每页 20 条加载更早记录，查看发送者、回复关联、投递状态及失败/阻塞原因。符合 MessageManager 恢复条件的收件记录可在确认后重试或续跑；不自动跳过失败消息。
- **能力**：区分持久配置的类型和运行时已装载的名称。实体停止且实际离线后，可多选已有注册类型并保存，下一次启动装载。manifest 管理的能力不能在此覆盖。原有能力管理入口继续服务于运行时能力操作。

页面显示后端错误码及详细原因。请求等待期间可以点击 **停止等待**，它仅解除本地等待，不取消后端操作，也不等同于停止 Agent。写操作结果不确定时，页面禁止继续写入，要求先刷新并核对实际状态；创建结果不确定时，应先到概览核对是否已创建，再重新打开创建入口。关闭页面、断线或停止等待后的迟到回执不会覆盖当前页面。

运行旧版后端时，新入口会显示 `Unknown command: agent_entity` 并提示更新后端。需要重启加载本次代码；仅刷新浏览器不能更新 Python 服务。重启前应安排好现有 Agent 的运行任务。

## 可用操作与约束

| 操作 | 行为 |
|---|---|
| `snapshot`、`abilities.list` | 查询期望配置和实际运行状态，离线时装载能力为 null |
| `context.list_sessions` | 会话、分支目录及 active_session_id；隐藏删除墓碑 |
| `context.get_history` | 必须指定属于该实体的 session_id/branch_id；limit 1–1000 |
| `context.create_session`、`context.activate_session` | 仅在线、空闲且期望运行的实体可修改上下文 |
| `mailbox.list_inbox/list_outbox` | limit 1–200，按递减序号使用 before_seq 分页 |
| `mailbox.get_message` | 仅当前实体参与的消息；接收者只看自己的投递记录 |
| `mailbox.retry/resume` | 仅在线实体可显式恢复投递给自己的消息，遵循 MM 原有可重试/可续跑规则 |
| `abilities.configure` | 实体已停止且实际离线时更新 AgentSpec.abilities，必须提供 expected_revision |
| `start/stop` | 显式改变持久期望状态，并操作对应的 ActorAgent |

`specification_revision` 使用现有 ProjectRecord.version，是**项目级**乐观锁版本；其他 Agent 的规格或诊断变化也可能导致冲突，应重新查询后由调用者决定是否重试。`stop` 可额外携带 `expected_incarnation_id`，避免过期操作停止新实例。

能力配置仍使用现有注册类型列表，不创建独立 Ability 数据实体。列表必须非空且类型已注册；manifest 管理的实体拒绝通过此入口覆盖能力。配置在下一次启动时装载，不热替换正在执行的能力。

## 生命周期与兼容

- 旧 AgentSpec 缺少 `desired_state` 时按既有恢复行为读取为 `running`，不重写旧数据。
- 停止先保存 `desired_state=stopped`，再终止执行实例；即使终止失败，停止意图仍保留，调用者可再次停止。
- 协调恢复与显式启停共用项目生命周期锁，恢复前重读 ProjectStore，避免用旧快照启动已停止实体。
- `stop()` 保存已有的已提交上下文；`start()` 复用稳定身份及 checkpoint，生成新的执行实例 ID。此接口不提供任意执行指令位置的暂停/续跑。
- 消息失败/中断仍遵循 MessageManager 原有恢复规则。启动实体不会自动跳过失败消息或重放副作用。新建但从未启动的实体不隐式创建会话或消息执行器，消息受理继续遵循现有 MM 身份及会话检查。
- 离线查询使用 SQLite 只读事务，不建库、不迁移、不登记新 run、不启动 Agent 或消息投递。历史查询目前复用完整 checkpoint 加载；返回条数受 limit 限制，但加载量并非分页。
- 目前 Subject 适配面向已有的进程内 Core 集群和本地项目存储。跨主机部署需增加远程实体后端及权威查询协议，不能把本地文件读取当作跨机共享能力。

## 验收位置

- `ghrah-subject/tests/test_agent_entity.py`：实体创建、停止/重启、Subject 重启、上下文和消息保留、身份隔离、能力配置及冲突检查；另覆盖启动失败重试、终止失败保留停止意图、并发恢复等待与重新检查、非法操作回执。
- `ghrah-core/tests/communication/test_mailbox_reader.py`：离线消息查询、身份过滤与分页。
- `ghrah-core/tests/context/test_entity_readonly.py`：只读查询不创建或迁移数据库。
- Python/TS 共用 `agent-entity.json` 样本，校验全部操作与拒绝规则；前端 SDK 测试校验身份绑定及错误传播。
- `ghrah-observer-webui/packages/observer-web/src/components/agent-entity-dialog.spec.ts`：离线历史、能力配置版本、停止确认、消息分页与重试、断线及迟到回执、创建成功与失败。

## 验收记录（2026-10-28）

- Python 五包全量：3,101 passed，8 skipped。测试数据使用临时目录；修正了 TaskStore 端到端测试直接构造配置时落入用户目录的问题。
- 前端五个测试包：1,101 passed，4 skipped，其中实体页面新增 9 项测试。WebSocket 测试在本机临时端口运行。
- `pnpm build`、`pnpm type-check`、`pnpm lint` 通过；lint 保留原有 20 条 warning，无 error。
- `uv run ruff check .`、`uv run ruff format --check .`、`git diff --check` 通过。
- 浏览器使用隔离项目和独立服务验证：离线会话/历史可见、启动后实际在线、确认停止后实际离线、修改并保存能力配置。未重启原有运行服务；原服务使用新命令前需加载新版后端。

本次交付覆盖实体接口、Subject 适配、生命周期、协议、SDK 和前端实体管理页面。执行中任意位置的暂停/续跑、跨主机实体后端不在本次实现内。
