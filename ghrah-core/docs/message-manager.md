# MessageManager 技术方案

本文面向方案评审与维护，按需求背景、技术方案、新增库表、影响范围和回归测试说明当前实现。MessageManager（MM）已接入 Subject 默认项目装配；后端自动化回归已完成，真实运行验收仍待完成。

## 一、需求背景

### 1. 原有通信链路与问题

原有链路主要依赖 Router 和 agent 内存队列：

```text
Agent A 调用发送工具
         │
         ↓
Supervisor → Router 查 Registry
                         │
                         ↓
                    B.receive()
                         │
                         ↓
                    B 的内存队列
                         │
                         ↓
                    B 的执行循环
                     ↙         ↘
            保存 ActionChain   返回回复
                                │
                                ├─ 同步：返回 A 的工具调用
                                └─ 异步：再次路由到 A
```

执行历史能够保存，不等于通信过程能够恢复。待处理消息、同步等待关系和回复投递缺少统一的持久记录；执行记录和消息消费状态分开保存，也可能出现部分成功。

### 2. 本期目标与范围

本期保留原有业务能力，将内部通信主链路切换到持久化消息管理。下表“已实现”表示编码与已有自动化验证完成，完整启动和真实模型业务验收仍待完成。

| 功能 | 本期具体实现 | 验收重点 | 当前状态 |
|---|---|---|---|
| 消息持久化 | 保存正文、类型、元数据和受理时间；成功受理前完成落库 | 重启后消息可查询，待收记录保留 | 已实现 |
| 来源与去向追踪 | 记录来源 agent/外部输入、接收者、关联请求、session 和执行节点 | 可追踪谁发送、谁接收、处理进度及回复 | 已实现 |
| 一对一、一对多 | 正文保存一份，每个接收者独立保存投递记录 | 部分成功、失败和离线互不覆盖 | 已实现 |
| 幂等受理 | 按来源与幂等键去重，相同载荷复用，冲突载荷拒绝 | 重复请求不新增消息和投递 | 已实现 |
| 异步发送 | 保留异步工具入口；出站草稿随节点提交，回复进入持久收件记录 | 提交前不投递，提交失败不发送 | 已实现 |
| 同步发送 | 保存等待检查点，等待持久回复，提交真实工具结果与回复消费确认 | 等待可恢复，正常回复不重复注入 | 已实现 |
| 广播与委派 | 保留广播和委派入口；广播排除自身并返回成功结果及失败诊断 | 空目标、部分失败及原有返回能力 | 已实现 |
| 用户输入与能力命令 | receive/chat 持久受理；显式能力命令通过 Runner 原子执行 | 来源和结构化内容保留，能力命令无需调用模型 | 已实现 |
| 固定 session | 受理时冻结来源/目标会话，回复返回原来源会话 | 切换会话不串任务，异常归属阻塞而非改投 | 已实现 |
| 执行调度与离线待收 | 同一 agent 串行、不同 agent 可并行；已知离线目标保留 pending | 上线继续投递，执行顺序正确 | 已实现 |
| 原子执行提交 | ActionChain、输入确认和本轮出站消息使用同一事务 | 写库失败无部分成功，内存与持久 head 一致 | 已实现 |
| 中断恢复与可靠收尾 | 未消费显式重试；已消费显式续跑；已保存结果自动补齐回复 | 不重复注入输入，不为收尾重跑模型或工具 | 已实现 |
| 同步异常处理 | 循环等待诊断；超时保留请求，迟到回复走持久收件记录 | 避免循环等待挂死，超时不丢请求 | 已实现 |
| 项目装配与生命周期 | 每项目一个 MM；同项目多 cluster 共享；动态 agent 绑定与最后关闭释放 | 项目隔离、离线再启动、共享资源正确释放 | 已实现 |
| Room 后端接入 | 消息持久投递；日志引用 MM 正文；回复投影及幂等重放 | 保留成员/targets 语义，重放无重复日志，旧日志可读 | 已实现 |
| 回归与实际验收 | 自动化回归覆盖正常、失败、回滚和进程中断；执行真实环境业务验收 | 原有功能保留，新增功能在实际环境可用 | 自动化完成，实际验收待完成 |

本期部署与能力边界：

| 项目 | 范围说明 |
|---|---|
| 部署范围 | 本地单进程、每个项目一个 MM；支持同项目多个 cluster 的内部 agent 通信 |
| 外部输入 | 支持受信宿主受理用户/外部请求；`send_external()` 不提供外部 agent 注册和网络认证 |
| 外部 agent 双向通信 | 不在本期；不提供跨项目、跨进程、跨机器网络投递和回复地址 |
| Room / Observer 前端 | 不新增前端体验，沿用现有展示契约 |
| 自动策略与运维扩展 | 自动业务重试、TTL、背压、容量归档及灾难恢复不在本期 |
| 外部副作用 | 不承诺 exactly-once；外部工具需要自身幂等，消息事务不能撤销其副作用 |

## 二、技术方案

### 1. 新通信链路

新链路先保存通信事实，再安排执行。agent 在一轮执行中产生的出站消息，必须随该轮执行记录提交，才能投递。

```text
Agent A 执行本轮工具
         │
         ↓
Supervisor 解析目标 → MM 准备出站草稿
         │
         ↓ 同一 SQLite 事务
ActionChain 节点 + 输入消费确认 + 出站消息/投递记录
         │
         ├─ 失败：全部回滚，不投递草稿
         └─ 成功
                │
                ↓
          MM 扫描待投递记录
                │
                ├─ B 离线：保留 pending
                └─ B 在线
                       │
                       ↓
               固定 session → AgentRunner 串行执行
                       │
                       ↓
               原子保存执行进度
                       │
                       ↓
               保存最终结果 → 保存回复并确认完成
                       │
                       ├─ 同步：A 读取持久回复后继续
                       └─ 异步：回复进入 A 的持久收件记录
```

### 2. 组件职责与权威来源

| 组件 | 职责 |
|---|---|
| Supervisor | 管理 agent 生命周期，将目标名称解析成稳定 ID，提供业务发送入口 |
| MessageManager | 消息受理、幂等检查、投递调度、回复保存、进度查询和恢复 |
| AgentRunner | 串行执行同一个 agent 的请求，协调接收、会话选择和压缩 |
| ContextManager | 管理上下文、ActionChain、session 和 branch，准备持久提交 |
| Core SQLite | 执行记录与消息事实的唯一持久权威 |
| Subject 项目工厂/Registry | 装配项目 MM，管理多个 cluster 的共享生命周期 |
| RoomStore | 保存新消息的引用、Room 顺序和审计信封；读取时从 Core 重建正文 |
| Observer | 展示事件及读侧投影，不负责写回消息事实 |

MM 与 ContextManager 使用同一个 Core SQLite 文件；原子提交使用同一连接、同一事务。MM 的身份缓存来自显式白名单或 Core sessions，不另建可独立修改的 agent 身份权威。

Router 已退出已装配 MM 的 agent 通信主路径。独立、未装配 MM 的接收回调仍可用于测试和底层集成，但不能获得生产原子通信的完整保证。

### 3. 消息模型

一条消息保存一份正文，每个接收者有自己的投递记录：

```text
消息 M
来源 A、正文、类型、幂等键、来源 session、关联节点
   │
   ├─ 投递 B：目标 session、状态、消费节点、最后节点、错误
   ├─ 投递 C：目标 session、状态、消费节点、最后节点、错误
   └─ 投递 D：目标 session、状态、消费节点、最后节点、错误
```

B 完成、C 失败、D 离线可以同时存在。一个接收者失败不会覆盖其他接收者的结果。

消息类型：

| 类型 | 行为 |
|---|---|
| `request` | 正常完成后产生一条关联最终回复 |
| `notification` | 消费完成后不自动回复 |
| `response` | 关联原请求，消费后不再自动回复 |

消息保留结构化内容块。外部输入单独标记来源，由受信宿主受理，不需要伪造 agent 身份。

### 4. 业务能力

| 能力 | 当前行为 |
|---|---|
| 一对一 | 持久请求、独立投递状态、关联回复 |
| 一对多 | 一个正文对应多个独立投递记录 |
| 异步发送 | 当前 action 准备草稿，提交成功后投递，回复通过持久 mailbox 返回 |
| 同步发送 | 保存等待检查点和请求后等待，真实回复进入工具结果 |
| 广播 | 排除自身，返回成功回复，失败目标保留独立诊断；无目标时返回空结果 |
| 委派 | 保留 Supervisor 同步返回接口，底层通过持久请求与回复实现 |
| 用户输入 | `receive/chat` 先持久受理，固定目标 session，再返回对应回复 |
| 显式能力执行 | 通过 Runner 和原子提交执行，无需为纯能力命令调用模型 |
| 离线投递 | 已知身份和目标 session 的消息保留待投递，绑定后继续 |
| 动态 agent | 生产装配显式从 Core sessions 刷新身份缓存并绑定接收端 |
| Room 后端 | 持久投递、正文引用日志、回复投影和幂等重放 |
| 中断恢复 | 未消费重试、已消费显式续跑、已保存结果自动收尾 |

受理幂等以 `sender + idempotency_key` 为范围：相同有效载荷返回原消息，不同有效载荷报冲突。接收者和冻结的会话归属也参与一致性检查。

### 5. 事务边界

#### 普通执行轮次

```text
准备候选上下文与本轮出站草稿
                 │
                 ↓ 同一事务
       ActionChain 节点及 branch head
       输入 consumed_node_id/session_id
       最后提交节点 last_node_id
       出站消息及各接收者记录
                 │
       ┌─────────┴─────────┐
       ↓                   ↓
提交成功                提交失败
推进内存 head           保留旧 head
发布节点事件            不保存部分消费或出站
允许消息投递            清理本轮草稿
```

`prepare_send()` 返回 `PreparedMessage`，只表示草稿已准备。提交后消息才可查询、投递；`origin_node_id` 指向同事务保存的节点。原子执行作用域内直接调用 `send()` 会被拒绝，避免绕过事务。

压缩也经过持久提交边界，但压缩节点不确认尚未消费的输入、不发布出站草稿、不清空待收消息。压缩失败保留旧 head；摘要语义质量不属于消息持久化保证。

#### 最终结果收尾

最终回复采用两阶段持久收尾：

```text
构建最终结果
      │
      ↓ 事务 1
保存结果、结构化内容和最终节点引用
      │
      ↓ 事务 2
创建关联回复及投递记录 + 原请求 completed
      │
      ↓
返回结果 / 发布观察事件
```

结果保存后进程退出，`recover_completions()` 可以补齐第二个事务，不再调用模型或工具。回复按原投递幂等生成，不会因重复收尾新增第二条回复。

节点提交成功但最终结果尚未保存时退出，不能推断任务已经完成：该任务进入中断状态，需要显式续跑。

### 6. 同步发送

`send_message(..., fire_and_forget=True)` 为异步发送，工具返回 `message_id` 和 `status=prepared`。

`fire_and_forget=False` 时：

```text
A 执行发送工具
      │
      ↓ 同一事务
输入确认 + 等待检查点 + 出站请求
      │
      ↓
B 执行并保存回复
      │
      ↓
A 读取真实回复，执行 POST_EXECUTE 钩子
      │
      ↓ 同一事务
A 工具结果节点 + 回复消费确认
      │
      ↓
A 的下一轮模型调用
```

等待检查点保存已执行工具结果和工具调用 ID。中途退出后，显式续跑先补齐待回复的工具结果，不重新执行已经提交的工具。同步工具的 `POST_EXECUTE` 在回复解析完成后运行；钩子的进程内局部状态不是执行快照。

- 正常同步回复不会再额外注入 A 的普通 mailbox。
- A→B→A 或自身同步等待会返回循环等待诊断。
- 超时结束等待，不删除请求、不取消对端执行；迟到回复转入普通持久 mailbox。
- 等待检查点已经提交，后半段失败不会撤销前半段的执行和发送。
- 模型用量在等待节点记录，工具结果节点不重复累计。

### 7. 固定 session 归属

一个 agent 可以切换任务会话。如果只记目标 agent，旧请求或迟到回复可能进入新任务。

生产装配要求 `session_routing=True`：受理时保存来源 session 和每个接收者的目标 session。默认归属从 Core 持久状态读取并冻结，后续切换 active session 不会改变已有消息。

Runner 临时进入目标 session 执行，结束后恢复原选择，不改写持久 active session。回复回到原请求的来源 session。

目标 session 关闭、缺失或接收端不支持固定路由时，投递显示 `blocked` 并保留原因，不猜测新归属、不自动改投。一个 session 的阻塞拦住该 session 后续消息，其他 session 可继续；整个 agent 的执行仍串行。

`session_routing=False` 仅适用于独立回调模式。升级前未保存 session 的历史待收消息，在严格模式下会以 `legacy_session_unassigned` 阻塞，不能自动猜测迁移。

### 8. 状态与恢复

| 状态 | 含义 |
|---|---|
| `pending` | 已持久受理，等待执行或等待上线 |
| `processing` | 已领取，正在执行 |
| `completed` | 正常返回且完成状态已保存，不代表业务答案一定正确 |
| `failed` | 执行失败，错误保留 |
| `interrupted` | 执行因关闭或进程退出中断 |
| `blocked` | 会话等条件阻止投递；由原状态与持久阻塞原因组合展示 |

失败、中断或阻塞不会静默跳过。固定会话模式按 session 保持顺序；普通回调模式按接收者保持顺序。

恢复要根据持久进度选择入口：

| 情况 | 入口 | 原因 |
|---|---|---|
| 仍为 pending，目标上线 | 绑定接收端后自动调度 | 输入尚未执行 |
| failed/interrupted，输入未消费且无最终结果 | `retry()` | 可以重新注入输入，但仍需确认外部副作用风险 |
| 输入已消费，任务未完成且无最终结果 | `get_progress()` → `resume_interrupted()` | 从最后持久节点继续，不再次注入输入 |
| 最终结果已保存，收尾未完成 | `recover_completions()`，启动及扫描也自动执行 | 只补回复和完成状态，不重跑业务 |
| 会话路由阻塞且条件已修复 | `resume_delivery()` | 验证原目标并解除阻塞，不更换归属 |
| Room 引用日志尚未投影 | `recover_room_projections()`，扫描也自动执行 | 重放引用日志，不重跑 agent |

已消费任务的显式续跑：

```python
progress = await manager.get_progress(delivery_id)
if progress.resume_block_reason is None:
    await manager.resume_interrupted(
        delivery_id,
        expected_last_node_id=progress.delivery.last_node_id,
        acknowledge_side_effect_risk=True,
    )
```

申请、领取和 Runner 执行前均校验 session、branch、身份及 head。目标离线时申请仍持久保留。head 漂移、旧版本缺少可信最后检查点等情况会拒绝续跑。

续跑恢复的是持久上下文，不是 Python 调用栈。使用当前能力和模型，以及新一轮迭代预算；未保存的工具结果、钩子局部状态和累计统计不能恢复。框架不主动重放已提交工具，但模型仍可能再次选择同一工具。

### 9. Subject 生产装配

```text
Subject 项目 Registry
         │
         ↓
default_core_unit_factory.prepare
初始化 Core schema → 打开项目 MM → 注入 CoreUnit
         │
         ├─ cluster 1 → Supervisor → agents
         └─ cluster 2 → Supervisor → agents
                       共用一个项目 MM
```

默认项目工厂显式设置固定 session 路由和 `allow_persisted_agents=True`。不同项目使用各自的 Core SQLite；同项目多个 cluster 共享 MM。

spawn 先恢复或保存 Core checkpoint，再注册并原子绑定 agent。默认 agent backend 使用同文件独立连接；不同文件的 backend 会被拒绝。关闭 cluster 时解绑其 agent，最后一个 cluster 关闭才释放 MM 和占用锁。挂载失败清理已获得的资源。

程序化自定义工厂需要显式提供同项目 MM。网络命令的身份与权限仍由宿主入口校验，内部 `sender_id` 不是身份认证机制。

### 10. Room 单向投影

新 Room 消息的正文由 MM 保存。RoomStore 保存 `mm_message_id` 引用、顺序和审计信封；查询和事件展示时从项目 Core SQLite 读取正文，客户端继续使用 `data.message`。原有历史日志保留读取能力。

```text
MM 提交消息 + Room 投影意图
              │
              ├─ 向目标 agent 投递
              │
              ↓ 可重试的单向投影
RoomStore：消息引用、Room 顺序、审计信封
              │
              ↓ 读取/展示
Core 正文 + Room 信封 → 原有客户端字段
```

投影引用 ID 固定，至少一次重放通过幂等追加避免重复日志。投影失败保留错误，后续恢复不调用 agent。RoomFilter 跳过 MM 管理的执行节点，避免事件形成第二条消息写路径。

人类 Room 输入、agent Room 工具、定向 targets、成员校验、自身排除和自由 data 保留。纯日志发布也通过 MM 保存正文。项目级投影器由 Registry 持有，不依赖某一个 cluster 存活。

### 11. 内部 API 与装配示例

| API | 用途 |
|---|---|
| `send()` | 在原子执行作用域外受理 agent 消息 |
| `send_external()` | 受信宿主受理外部输入 |
| `prepare_send()` | 原子执行中准备出站草稿 |
| `bind_atomic()` / `unbind()` | 绑定生产原子接收端 / 解绑 |
| `bind()` | 绑定普通回调；不保证 ActionChain 与消息同事务 |
| `get_message()` / `get_deliveries()` / `get_replies()` | 查询消息、各目标状态和关联回复 |
| `wait_replies()` | 等待持久回复，超时不取消目标请求 |
| `dispatch_pending()` | 等待当前可处理投递收敛，不等待离线目标上线 |

独立回调演示见 [persistent_messages.py](../examples/persistent_messages.py)：

```sh
uv run python ghrah-core/examples/persistent_messages.py --db /tmp/ghrah-message-demo.sqlite
```

该演示使用专用消息文件，展示在线、离线和重启后查询。它不是 Subject 生产装配示例，也不提供 ActionChain 原子保证。

生产原子装配必须先初始化/恢复 Core，再打开同文件 MM，并绑定已连接到该文件的 agent：

```python
from ghrah.communication import MessageKind, MessageManager

async with MessageManager(
    backend.db_path,
    project_id="project-1",
    agent_ids=("a", "b"),
    session_routing=True,
) as manager:
    await manager.bind_atomic(a)
    await manager.bind_atomic(b)
    request = await manager.send(
        sender_id="a",
        recipient_ids=("b",),
        content="检查设计",
        kind=MessageKind.REQUEST,
        idempotency_key="inspect-design-1",
    )
    deliveries = await manager.get_deliveries(request.message_id)
```

示例中的 `backend`、agent 与 Core checkpoint 应提前完成装配；业务使用默认 Subject 工厂时无需手工创建第二个 MM。

### 12. 保证与限制

| 可以保证的范围 | 不提供的保证 |
|---|---|
| 成功受理后，消息及全部投递记录持久保存 | 磁盘损坏、数据库丢失后的灾难恢复 |
| ActionChain、输入确认和本轮出站原子提交 | 外部 HTTP、文件写入等副作用随事务回滚 |
| 同一 agent 串行、离线待收保留 | 跨进程/跨机消费者协调与分布式投递 |
| 持久最终结果可以恢复唯一回复 | 外部工具 exactly-once |
| 固定 session，异常条件阻塞而非改投 | 任意 Python 位置的执行快照 |
| 显式重试/续跑与可查询错误 | 自动业务重试、TTL、业务取消和背压 |

MM 连接使用 WAL、foreign keys 和 `synchronous=NORMAL`；此次同步级别调整仅作用于 MM，不修改原有 Core SqliteBackend 的 PRAGMA 配置。NORMAL 保留事务原子性和应用进程崩溃恢复能力，但断电或操作系统崩溃可能丢失近期已提交事务，不承诺此类故障下消息绝不丢失。同目录 `.owner` 占用锁保证一个数据库只有一个活跃 MM；不要运行时删除或替换数据库/锁文件，不使用网络文件系统。

当前 MM schema 为 v6，可从 v1–v5 原位升级并保留消息 ID。缺少会话或最后检查点的旧记录不会猜测补齐。运行期间不要绕过 Runner 修改执行中的 session/branch。

当前候选上下文准备会复制 session 历史，长历史存在开销；消息增长和容量管理也需要后续评估。持久化成功不等于模型理解正确或业务任务成功。

## 三、新增库表

新增六张 `mm_*` 表，与现有 ActionChain、session、branch 共存于项目 Core SQLite，不新增独立的生产消息数据库。以下字段以当前 schema v6 为准。

### 1. 表结构与用途

| 表 | 关键字段 | 用途与约束 |
|---|---|---|
| `mm_meta` | `singleton`、`project_id`、`version` | 单行项目归属与 MM schema 版本；`singleton=1` |
| `mm_messages` | `seq`、`message_id`、`sender_id`、`content`、`kind`、`reply_to`、`idempotency_key`、`payload`、`accepted_at`、`origin_node_id`、`sender_session_id`、`metadata`、`external_sender` | 保存正文和来源；`message_id` 唯一，`(sender_id, idempotency_key)` 唯一；`reply_to` 引用原消息 |
| `mm_deliveries` | `seq`、`delivery_id`、`message_id`、`recipient_id`、`state`、`attempts`、`error`、`consumed_node_id`、`consumed_session_id`、`target_session_id`、`blocked_reason`、`last_node_id`、`resume_requested` | 保存每个目标的投递和执行进度；`delivery_id` 唯一，`(message_id, recipient_id)` 唯一 |
| `mm_delivery_results` | `delivery_id`、`content`、`result_node_id`、`recorded_at`、`content_blocks` | 每个投递最多一条最终结果，支持中断后补齐回复；`delivery_id` 为主键 |
| `mm_inline_waits` | `message_id`、`parent_delivery_id`、`state` | 记录同步请求属于哪次发送方执行；状态为 `waiting/consumed/released`；`message_id` 为主键 |
| `mm_room_projections` | `message_id`、`completed`、`error` | 记录消息到 Room 引用日志的投影进度；`message_id` 为主键 |

`payload` 用于幂等有效载荷比较，`metadata` 保存路由和结构化内容等扩展信息。接收者位于 `mm_deliveries`，不是在正文表中另存一份成员权威。

### 2. 关联与索引

```text
mm_meta：项目与版本

mm_messages（请求）
      ├─ 1:N → mm_deliveries
      │            ├─ 1:0..1 → mm_delivery_results
      │            └─ 1:N → mm_inline_waits.parent_delivery_id
      ├─ 1:0..1 → mm_inline_waits.message_id
      ├─ 1:0..1 → mm_room_projections
      └─ 1:N → mm_messages（回复，reply_to 指向请求）
```

- `mm_inbox(recipient_id, seq)`：支持按接收者顺序读取待收记录。
- `mm_replies(reply_to, seq)`：支持读取请求的关联回复。
- 消息、投递、结果、同步等待和投影之间使用外键约束。
- `blocked` 是查询展示状态，实际 `state` 只保存 `pending/processing/completed/failed/interrupted`，阻塞原因单独保存。
- Core 节点/session 引用在受理、提交和恢复逻辑中校验，不把所有逻辑引用都声明为数据库外键。

### 3. 迁移与数据边界

v1–v5 可原位升级至 v6，保留消息 ID。旧记录缺失的 session 或最后检查点不会猜测补齐，可能阻塞或拒绝续跑。

MM 表是消息领域的写侧事实；Room 引用日志为单向派生。`.owner` 是运行占用锁，不是消息表，也不保存第二份消息事实。当前没有自动清理、归档和容量上限策略。

## 四、影响范围

这次改动已覆盖通信主链路，属于核心执行与持久化改造，不能按单纯新增工具评估。

| 模块 | 影响程度 | 改动内容 | 重点回归风险 |
|---|---|---|---|
| MessageManager | 新增核心模块 | 消息受理、投递、回复、恢复与表结构 | 幂等冲突、状态推进、离线与中断行为 |
| ActorAgent / AgentRunner | 大 | 串行入口、原子迭代、同步等待检查点、恢复和压缩协调 | 工具结果、取消、钩子时机、迭代预算与用量统计 |
| ContextManager / SqliteBackend | 大 | 候选上下文和可等待提交，与消息共用事务 | 提交失败时内存与数据库一致、branch/session head |
| Supervisor / 通信工具 | 大 | 新发送主路径、目标解析、同步/异步、广播和委派 | 旧业务能力及返回形状、超时、循环等待 |
| CoreUnit | 中 | 持久用户输入、显式能力命令及回复关联 | 用户来源、结构化内容、命令幂等 |
| Subject 工厂 / Registry | 中 | 项目 MM 装配、共享与释放、动态 agent 绑定 | 多 cluster 关闭顺序、挂载失败清理、项目隔离 |
| Room 后端 / RoomFilter | 中 | MM 正文引用、单向投影、跳过重复事件写路径 | 成员与 targets、日志展示、重放去重、历史读取 |
| Protocol / Observer 前端 | 本轮未改 wire/TS | 沿用现有契约和展示字段 | 仍需实际客户端业务验收 |
| Plugin / TaskStore | 小 | 未主动重构，通过全量测试检查关联影响 | 核心执行改动带来的间接回归 |

### 1. 保留的能力与行为变化

同步/异步选择、广播、委派、用户输入、能力命令和 Room 后端继续提供。内部实现切换到持久链路，以下变化需要评审关注：

- 异步工具返回 `prepared`，明确消息随当前节点提交后才生效。
- 同步发送增加等待检查点，形成两个提交阶段。
- 已消费的中断任务不能用普通 retry 重注入，必须显式续跑。
- 会话异常时阻塞，不自动投递到当前 active session。
- 新 Room 日志保存正文引用，读取依赖项目 Core SQLite；旧日志仍可读取。

### 2. 尚未纳入本期

外部 agent 网络接入、跨机部署、自动重试策略、背压、容量归档、灾难恢复，以及 Room/Observer 新前端体验均未实现。候选上下文复制和 SQLite 写入竞争的生产负载表现需要后续评估。

## 五、回归测试

当前后端实现已完成。2026-10-14 整体复查修正后，全量 Python 回归结果为 **2908 passed、8 skipped**，Ruff 与格式检查通过。回归时将 Subject 默认数据库和工作区隔离到临时目录。

关键覆盖包括原子提交失败、回滚时清理暂存回复确认、同步等待时真实进程退出与恢复、循环等待、超时迟到回复、广播部分失败、结构化结果、POST_EXECUTE 时机、多 cluster 生命周期、项目隔离及 Room 投影重放。动态创建 agent 后发送消息的全链路用例同时覆盖同步和异步模式。

```sh
uv run pytest -q ghrah-core/tests/communication
uv run pytest -q ghrah-subject/tests/test_project_message_manager.py
uv run pytest -q ghrah-subject/tests/test_room_e2e_real.py
uv run pytest ghrah-plugin ghrah-protocol ghrah-core ghrah-subject ghrah-taskstore
uv run ruff check .
uv run ruff format --check .
```

仍待完整启动、真实模型和人工业务验收，重点验证用户→A→B、同步/异步发送、广播、Room 和进程重启恢复。本轮未修改 TS 或 wire 契约；当前环境缺少 vitest，不能将 Python 回归结果当作前端验收结论。

### 1. 重点场景与验收依据

| 场景 | 预期结果 |
|---|---|
| 相同幂等键重复发送 / 不同载荷复用 | 返回原消息 / 报冲突，不新增重复投递 |
| 一对多、部分离线或失败 | 每个目标独立记录，成功结果不被覆盖 |
| 当前节点提交失败 | 输入确认、执行节点、出站消息无部分成功 |
| 固定 session 后切换 active session | 请求和回复保持原归属 |
| 同步等待、循环等待、超时迟到回复 | 正确工具结果、明确诊断、请求保留且迟到回复可消费 |
| 同步检查点处进程退出 | 显式恢复等待，不重跑已提交工具 |
| 结果已保存但回复未保存 | 自动补齐唯一回复，不调用模型或工具 |
| 已消费未完成任务 | 校验最后 head 后续跑，不再次注入原输入 |
| POST_EXECUTE 与用量统计 | 钩子观察最终回复，模型用量不重复累计 |
| 多 cluster 共享与项目隔离 | 最后关闭才释放 MM，不同项目消息不串扰 |
| Room 投影失败及重放 | 可恢复、无重复日志、正文由 MM 读取 |
| 原有用户输入、委派、广播、能力命令 | 保留可用业务入口及对应结果 |

### 2. 尚待验收

下一步只做实际业务验收：完整启动项目，使用真实模型验证用户→A→B、同步/异步、广播、Room 与处理中重启。自动化测试通过能够证明已覆盖场景，但不能代替真实模型、实际配置、长历史性能和客户端体验验收。
