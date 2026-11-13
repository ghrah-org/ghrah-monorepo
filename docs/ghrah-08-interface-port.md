# 模块接口与端口设计说明

| 文档状态 | 发布 | 保密级别 | 内部 |
| --- | --- | --- | --- |
| 文档编号 | GHRAH-DESIGN-008 | 管理部门 | ghrah 平台 |
| 修订年月 | 2026-11 | 版本号 | 2.0 |

## 变更履历

本仓评审走 PR，不设签字位。

| 序号 | 变更日期 | 版本 | 变更位置 | 变更原因 | 修订人 | 审核人 | 批准人 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-11-12 | 1.0 | 全文 | 建立初稿 | — | — | — |
| 2 | 2026-11-13 | 2.0 | 全文 | 内容修订 |
| 3 | 2026-11-13 | 2.1 | §1.4 | 端口层「能力」统一改称「接口」，新增术语约定 | — | — | — |

校准基线：`ghrah-monorepo` @ `08983d7`（2026-11-13），工作区干净。

# 1 文档介绍

## 1.1 编写目的

定义模块之间唯一的通信形态——**接口端口**——并给出 ghrah-core 的完整落地实例，使新增或修改一条跨模块接口时无需再读实现代码即可判断改哪里、怎么改；为模块边界评审提供可判定的判据（而不是靠约定）；把"跨模块只能走端口"从口头约定变成可执行的强制点。

## 1.2 文档范围

本文覆盖：端口模型的三个抽象、代码布局与依赖方向、Core 的 50 条接口清单、`CorePort` 的方法签名、项目作用域模型、装配与生命周期、错误模型与翻译表、三条强制点、边界闸门的现状、Subject 侧的接入方式、新增接口的步骤、已知偏差。

本文**不替代**以下内容，各自有独立归属：能力的业务语义（一个能力内部做了什么）由能力系统与驱动循环的设计文档负责；存储表结构由持久层代码负责；Agent 运行时控制面（`ghrah.core.runtime` 的 `/v1/status`、`/v1/commands` 等路径式接口，用于进程内桥接与观测，与模块端口是两件事）不在本文范围内；`ghrah-kernel` 的信封式调用协议也不在本文范围内。

## 1.3 读者对象

| 角色 | 从本文取什么 |
| --- | --- |
| 要给模块加一条对外接口的人 | §4 总表确认归属、§5 签名写法、§12 步骤 |
| 要改跨模块调用的人 | §6 作用域、§7 装配、§11 Subject 接入 |
| 做边界评审的人 | §2 模型、§9 强制点、§10 闸门、§13 偏差 |
| 排查"这条链路断在哪"的人 | §8 错误码、§11 接入、§13 偏差 |

## 1.4 术语与缩写解释

| 术语或缩写 | 解释 |
| --- | --- |
| 模块 | 拥有自己的状态与权威的独立包，如 `ghrah-core`、`ghrah-subject` |
| 接口 | 模块愿意让其它模块调用的一个操作，用点分名标识（`nodes.get`）。本项目统一用「接口」，不再称「能力」 |
| 端口 | 模块接口的唯一对外集合，是一个 `Protocol`；消费方只认识它 |
| 收敛点 | 模块内部唯一的接口入口，负责查表、校验、转交、校出参 |
| 门面 | 端口的实现对象，每应用实例一个，由装配根创建 |
| 作用域 | 一条接口所属的项目边界；由门面的 `project(project_id)` 绑定 |
| 出参信封 | 接口层统一返回的 DTO（如 `NodeIdView`），端口层拆壳成裸值 |
| 契约包 | `ghrah-protocol`，存放端口协议、视图 DTO、错误码、接口骨架 |
| 装配根 | 唯一允许同时认识多个模块的位置：创建门面、注入、关闭 |
| 能力包（ability） | **与接口是两件事**：被授权、被调用的实现单元（`ghrah.ability.*`）。接口是端口暴露的调用面，能力包是实现体 |

> **术语约定（必读）**：本项目端口层原先叫「能力 / 能力清单」，自 2026-11 起**统一称为「接口 / 接口清单」**，
> 代码落点为 `ghrah.protocol.interface`、`ghrah.core.api.interfaces`、`CORE_INTERFACES`。
> 「接口」只指端口暴露的调用面；`ability`（能力包、Skill 包）是另一个概念，指被授权调用的实现单元，
> 两者不可混用——历史文档里出现的「端口能力」一律按「接口」理解。
> 错误响应中的 `Problem.capability` 字段名为兼容保留，其值就是接口名。

## 1.5 参考资料

| 序号 | 文档名称 | 文档编号 | 版本 | 发布日期 |
| --- | --- | --- | --- | --- |
| — | 无外部标准或上游文档；本文自闭环，事实依据为 §1.2 所述源码 | — | — | — |

# 2 端口模型

## 2.1 为什么用端口而不是 HTTP 语义

模块之间在**同一进程**内通信，没有网络。HTTP 语义在进程内的成本是三样纯编码开销：路径模板编译与匹配、请求头解析、状态码映射。它的表达力没有一样是 HTTP 独有的：

| HTTP 表达力 | 本方案的等价物 | 处置 |
| --- | --- | --- |
| method + path | 方法名（`nodes.get`） | 不采用 |
| 状态码 | 异常类型 + `Problem.code` | 不采用 |
| 查询参数裁剪字段 | —— | 不采用（进程内无带宽成本） |
| 条件写（`If-Match`） | 冲突异常携带 `details.revision` | 保留语义 |
| 幂等键（`Idempotency-Key`） | `idempotency_key` 参数 | 保留 |
| 分页 | 返回 `Page[T]` | 保留 |
| 长动作 | 返回 `Operation` | 保留 |

保留下来的不是传输细节，而是**资源命名纪律**：每个接口必须落到一个名词分组上，命名可预测（`nodes.get` 而不是 `getNodeById`），否则 50 条接口会退化成无法猜的 RPC 集合。

## 2.2 三个抽象

契约包用三个类型定义"一条接口"，与具体模块无关：

| 类型 | 职责 | 位置 |
| --- | --- | --- |
| `Capability` | 一条能力的声明：`name` + 请求信封模型 + 出参模型 | `ghrah-protocol/src/ghrah/protocol/interface.py:49` |
| `InterfaceRegistry` | 有序、名字唯一、可枚举的清单；重复名在构造时抛 `ValueError` | 同上 `:64` |
| `InterfaceRuntime` | 模块内唯一运行时入口；构造时校验"清单与处理器集合完全一致"，不一致抛 `RuntimeError`（启动即失败） | 同上 `:108` |

命令名用点分两段（`<分组>.<动作>`），分组名即端口上的属性名，动作名即方法名。这条对应关系是**人工约定**，由 §12 的步骤保证；运行时不解析名字。

## 2.3 一次调用的四步

`InterfaceRuntime.invoke(name, payload)` 固定做四件事，顺序不可调换：

1. **查表**：`name` 未登记 → `INTERNAL`（接口名写错是代码缺陷，不是调用方错误）；
2. **校验入参**：按 `Capability.request` 校验 `payload`；失败 → `INVALID_ARGUMENT`，`details.errors` 里给 `loc`/`type`/`msg` 三元组（只投这三个字段，因为 pydantic 的 `ctx` 可能带异常对象，会破坏 JSON 可往返）；
3. **转交**：在 `translator(name)` 上下文里调用处理器，把模块私有异常翻译成统一错误族；
4. **校出参并强制 JSON**：用 `Capability.response` 校验返回值，再走 `check_json_serializable`。

第 4 步是"只说 DTO"这条边界的唯一强制点：整个调用链上只有这里把返回值当成可序列化数据检查。任何返回活对象（`datetime`、ORM 行、内部实体）的处理器都会在第一次调用时失败，而不是在某个下游消费方手里才炸。

`invoke` 的返回值类型是 `dict[str, Any]`（JSON 形状），不是 DTO 实例——这个刻意的降级让门面必须显式 `model_validate` 回来，从而在类型系统里留下转换点。

# 3 代码布局与依赖方向

## 3.1 契约包（`ghrah-protocol`）

| 路径 | 内容 |
| --- | --- |
| `protocol/interface.py` | 能力骨架：`Capability` / `InterfaceRegistry` / `InterfaceRuntime` / `Handler` / `Translator` / `ErrorFactory` |
| `protocol/ports/__init__.py` | 端口再导出：`CorePort` 与 `CORE_PORT` / `SUBJECT_PORT` |
| `protocol/ports/core.py` | `CorePort` + 10 个操作分组协议 + 6 个标识类型别名 |
| `protocol/ports/keys.py` | 注入键：`CORE_PORT = "core.port"`、`SUBJECT_PORT = "subject.port"` |
| `protocol/views/common.py` | `Page[T]`、`Operation` / `OperationState` / `OperationView`、`AckView`、7 个出参信封 |
| `protocol/views/core.py` | Core 的资源视图：`NodeView`、`SessionCatalogView`、`HistoryView` 等 |
| `protocol/problem.py` | `Problem` + `ProblemCode`（13 个码，无状态码、无 type URI） |
| `protocol/errors.py` | `ApiError` 基类：消费方 `except` 的公共父类 |
| `protocol/corpus.py` | 契约回归录制 + `check_json_serializable` |
| `protocol/events.py` | 事件信封（与接口端口分离的独立通道） |

## 3.2 Core 侧（`ghrah-core`）

`ghrah-core/src/ghrah/core/api/__init__.py` 声明了依赖只能向下的分层：

```
read/        → 只认存储与 DTO（无内部依赖）
errors       → read, protocol.problem
capabilities → protocol.capability, protocol.views
runtime      → capabilities, errors, protocol.capability
handlers     → read, capabilities, errors
facade       → runtime, protocol.ports
wiring       → 全部
```

| 路径 | 职责 |
| --- | --- |
| `core/read/chain.py` | 只读链库：`ChainReadStore`、`ChainDatabaseUnavailableError`、`DbPathResolver` |
| `core/read/mailbox.py` | 只读信箱/投递：`MailboxReadStore` 与三类未找到异常、`MailboxUnavailableError`、`MailboxProjectMismatchError` |
| `core/read/ability.py` | 能力关系库读面：`AbilityReadStore`、`AbilityConflictError`、`AbilityUnavailableError` |
| `core/read/ability_catalog.py` | 能力包目录读面：`CatalogError`（带 `not_found`/`invalid_request`/`unavailable` 三值 code） |
| `core/read/ability_admin.py` | 能力管理总览的装配读面 |
| `core/api/interfaces.py` | `CORE_INTERFACES`：50 条接口的声明（名字 + 请求信封 + 出参） |
| `core/api/handlers/` | 10 个处理器模块：`nodes`、`sessions`、`session_write`、`branches`、`mailbox`、`deliveries`、`manifests`、`abilities`、`agents`；`__init__.py` 的 `build_handlers(chain, mailbox, abilities, registry)` 汇总 |
| `core/api/runtime.py` | `CoreRuntime(InterfaceRuntime)` + `build_runtime`：绑定 Core 的清单、翻译表、异常工厂 |
| `core/api/errors.py` | `CoreApiError` + `translate()` 翻译表 |
| `core/api/facade.py` | `CoreFacade` 与 10 个分组实现类；`_call` / `_call_global` / `project` / `aclose` |
| `core/wiring.py` | `build_core_port(...)`：装配根唯一入口 |

**读面与集群生命周期解耦**：`core/read/*` 只按 `resolve_db_path(project_id)` 打开只读连接，不需要先挂载 `CoreUnit`。因此读取一个已停止 Agent 的历史不会复活项目。带运行时状态的能力（`agents.runtime`、`abilities.tree`、`agents.tools`）是例外，它们经 `RuntimeRegistry` 读活跃单元，离线时按各自视图返回 `offline` 或抛 `NOT_FOUND`。

## 3.3 为什么契约与实现必须分包

如果 `CorePort` 定义在 `ghrah-core` 里，Subject 就必须 `from ghrah.core... import CorePort`——边界当场失效。所以端口协议、视图、错误基类、注入键只能住在第三方包；模块只提供实现。这条是**结构约束，不是风格偏好**。

# 4 Core 接口面（50 项）

口径：以下 50 条是 `CORE_INTERFACES` 的登记项数（`len(CORE_INTERFACES) == 50`），分组数与端口上的属性数一致，均为 10。

## 4.1 总表

"入参"列省略被注入的 `project_id`（由门面注入，调用方从不参与）；"出参"列是**端口层**的返回类型，括号内注明能力层的信封。

### 链节点与会话（`nodes` / `sessions` / `branches`，14 条）

| # | 接口名 | 端口方法 | 入参 | 出参 | 主要失败码 |
| --- | --- | --- | --- | --- | --- |
| 1 | `nodes.get` | `nodes.get` | `agent_id`, `node_id` | `NodeView` | `NOT_FOUND` |
| 2 | `sessions.catalog` | `sessions.catalog` | `agent_id` | `SessionCatalogView` | — |
| 3 | `sessions.history` | `sessions.history` | `agent_id`, `session_id`, `branch_id`, `limit=-1` | `HistoryView` | `NOT_FOUND` |
| 4 | `sessions.head` | `sessions.head` | `agent_id`, `session_id`, `branch_id` | `NodeId`（`NodeIdView`） | `NOT_FOUND` |
| 5 | `sessions.meta` | `sessions.meta` | `agent_id` | `ChainMetaView` | — |
| 6 | `sessions.create` | `sessions.create` | `agent_id`, `name=None`, `system_prompt=None` | `SessionId`（`SessionIdView`） | — |
| 7 | `sessions.activate` | `sessions.activate` | `agent_id`, `session_id` | `None`（`AckView`） | `NOT_FOUND` |
| 8 | `sessions.archive` | `sessions.archive` | `agent_id`, `session_id` | `None` | `NOT_FOUND` |
| 9 | `sessions.delete` | `sessions.delete` | `agent_id`, `session_id` | `None` | `NOT_FOUND` |
| 10 | `branches.list` | `branches.list` | `agent_id`, `session_id` | `list[BranchView]`（`BranchListView`） | `NOT_FOUND` |
| 11 | `branches.create` | `branches.create` | `agent_id`, `session_id`, `name`, `from_node_id=None`, `parent_branch_id=None` | `BranchId`（`BranchIdView`） | — |
| 12 | `branches.activate` | `branches.activate` | `agent_id`, `session_id`, `branch_id` | `None` | `NOT_FOUND` |
| 13 | `branches.archive` | `branches.archive` | `agent_id`, `session_id`, `branch_id` | `None` | `NOT_FOUND` |
| 14 | `branches.delete` | `branches.delete` | `agent_id`, `session_id`, `branch_id` | `None` | `NOT_FOUND` |

### 信箱与投递（`mailbox` / `deliveries`，9 条）

| # | 接口名 | 端口方法 | 入参 | 出参 | 主要失败码 |
| --- | --- | --- | --- | --- | --- |
| 15 | `mailbox.summary` | `mailbox.summary` | `agent_id` | `MailboxSummaryView` | — |
| 16 | `mailbox.list` | `mailbox.list` | `agent_id`, `inbox=True`, `limit=50`, `cursor=None` | `Page[MessageView]` | — |
| 17 | `mailbox.get` | `mailbox.get` | `agent_id`, `message_id` | `MessageView` | `NOT_FOUND` |
| 18 | `mailbox.publish_external` | `mailbox.publish_external` | `sender`, `recipient_ids`, `content`, `idempotency_key`, `metadata=None` | `PublishedMessageView` | — |
| 19 | `mailbox.publish_notification` | `mailbox.publish_notification` | `sender_id`, `content`, `kind`, `idempotency_key`, `metadata=None` | `PublishedMessageView` | — |
| 20 | `mailbox.recover_room_projections` | `mailbox.recover_room_projections` | 无 | `int`（`AckView` 之外的裸计数） | — |
| 21 | `deliveries.get` | `deliveries.get` | `agent_id`, `delivery_id` | `DeliveryView` | `NOT_FOUND` |
| 22 | `deliveries.retry` | `deliveries.retry` | `agent_id`, `delivery_id` | `None` | `NOT_FOUND` |
| 23 | `deliveries.resume` | `deliveries.resume` | `agent_id`, `delivery_id` | `None` | `NOT_FOUND` |

### 能力关系与能力包（`abilities`，13 条）

| # | 接口名 | 端口方法 | 入参 | 出参 | 主要失败码 |
| --- | --- | --- | --- | --- | --- |
| 24 | `abilities.tree` | `abilities.tree` | `agent_id` | `AbilityTreeView` | — |
| 25 | `abilities.changes` | `abilities.changes` | `after_seq=0`, `limit=100` | `AbilityChangesPage` | — |
| 26 | `abilities.admin` | `abilities.admin` | `agent_id` | `AbilityAdminView` | — |
| 27 | `abilities.packages` | `abilities.packages` | 无（全局，`_call_global`） | `AbilityPackageCatalogView` | `UNAVAILABLE` |
| 28 | `abilities.package_detail` | `abilities.package_detail` | `namespace`, `name`（全局） | `AbilityPackageDetailView` | `NOT_FOUND` |
| 29 | `abilities.package_groups` | `abilities.package_groups` | `agent_id=None`, `scoped=True` | `AbilityPackageGroupsView` | — |
| 30 | `abilities.instances` | `abilities.instances` | 无 | `AbilityInstancesView` | — |
| 31 | `abilities.content` | `abilities.content` | `agent_id`, `alias`, `member`, `offset=0`, `limit=65536` | `ContentReadResult` | `NOT_FOUND` |
| 32 | `abilities.status` | `abilities.status` | 无 | `AbilityApiStatus` | — |
| 33 | `abilities.grant` | `abilities.grant` | `agent_id`, `change: GrantAbilityRequest` | `AbilityTreeChangeResult` | `FAILED_PRECONDITION` |
| 34 | `abilities.revoke` | `abilities.revoke` | `agent_id`, `change: RevokeAbilityRequest` | `AbilityTreeChangeResult` | `FAILED_PRECONDITION` |
| 35 | `abilities.enable_tool` | `abilities.enable_tool` | `agent_id`, `change: BindToolRequest` | `AbilityTreeChangeResult` | `FAILED_PRECONDITION` |
| 36 | `abilities.disable_tool` | `abilities.disable_tool` | `agent_id`, `change: UnbindToolRequest` | `AbilityTreeChangeResult` | `FAILED_PRECONDITION` |

`abilities.grant`/`revoke`/`enable_tool`/`disable_tool` 的冲突以 `FAILED_PRECONDITION` 表达，`details.revision` 给当前修订号、`details.view` 给写后视图（`ghrah-core/src/ghrah/core/api/errors.py:81`）。

### Agent 生命周期（`agents`，9 条）

| # | 接口名 | 端口方法 | 入参 | 出参 | 主要失败码 |
| --- | --- | --- | --- | --- | --- |
| 37 | `agents.create` | `agents.create` | `spec: AgentSpawnSpec` | `AgentId`（`AgentIdView`） | — |
| 38 | `agents.create_from_template` | `agents.create_from_template` | `template: AgentTemplateSpec`, `agent_id=None` | `AgentId` | `UNAVAILABLE` |
| 39 | `agents.get` | `agents.get` | `agent_id` | `AgentView` | `NOT_FOUND`（不在线） |
| 40 | `agents.list` | `agents.list` | 无 | `Page[AgentId]` | — |
| 41 | `agents.delete` | `agents.delete` | `agent_id` | `None` | `NOT_FOUND` |
| 42 | `agents.runtime` | `agents.runtime` | `agent_id` | `AgentRuntimeView`（离线时 `availability="offline"`） | — |
| 43 | `agents.tools` | `agents.tools` | `agent_id` | `list[ToolView]`（`ToolListView`） | `NOT_FOUND` |
| 44 | `agents.compact` | `agents.compact` | `agent_id` | `None` | — |
| 45 | `agents.reset` | `agents.reset` | `agent_id` | `None` | — |

### 消息、Room 与 manifest（`messages` / `rooms` / `manifests`，5 条）

| # | 接口名 | 端口方法 | 入参 | 出参 | 主要失败码 |
| --- | --- | --- | --- | --- | --- |
| 46 | `messages.append` | `messages.append` | `agent_id`, `target`, `content`, `metadata=None` | `MessageId`（`MessageIdView`） | — |
| 47 | `messages.broadcast` | `messages.broadcast` | `content`, `sender=""` | `list[MessageId]`（`MessageIdListView`） | — |
| 48 | `messages.delegate` | `messages.delegate` | `from_agent_id`, `to_agent_id`, `content`, `timeout=None` | `DeliveryId`（`DeliveryIdView`） | — |
| 49 | `rooms.recover` | `rooms.recover` | 无 | `None` | — |
| 50 | `manifests.resolve` | `manifests.resolve` | `manifest_ref` | `AgentSpecView` | `FAILED_PRECONDITION`（解析源未注入） |

## 4.2 分组与请求信封的对应

请求信封是按接口定义的 pydantic 模型，命名规律是 `<分组><动作>Request`（如 `SessionsCatalogRequest`；`NodeGetRequest` 等少数例外不带分组前缀）。除 2 条全局能力外，所有信封都带 `project_id: str`；不带的正是 `abilities.packages` 与 `abilities.package_detail`（这两条确实无项目维度）。

出参一律是 `views/` 里的 DTO，或 `Page[T]`；需要返回裸 id/计数的接口，能力层仍返回信封 DTO，由端口层拆壳（§5.3）。

# 5 CorePort 的方法签名

## 5.1 标识类型

```python
type AgentId = str
type BranchId = str
type DeliveryId = str
type MessageId = str
type NodeId = str
type SessionId = str
```

六个都是 `str` 的别名，没有包装。这样做的收益是调用点不需要拆包、日志不需要 `str()`；代价是**混用不会被类型系统拦住**（把 `agent_id` 传给 `node_id` 仍然能编译）。签名里因此一律使用关键字参数，让误传在字面上就可读。

## 5.2 十个分组

`CorePort` 只暴露 10 个只读属性 + 2 个方法（`project`、`aclose`）；分组协议各自独立，互不引用。

```python
class CorePort(Protocol):
    @property
    def nodes(self) -> NodeOperations: ...
    @property
    def sessions(self) -> SessionOperations: ...
    @property
    def branches(self) -> BranchOperations: ...
    @property
    def mailbox(self) -> MailboxOperations: ...
    @property
    def deliveries(self) -> DeliveriesOperations: ...
    @property
    def abilities(self) -> AbilityOperations: ...
    @property
    def agents(self) -> AgentOperations: ...
    @property
    def messages(self) -> MessageOperations: ...
    @property
    def rooms(self) -> RoomOperations: ...
    @property
    def manifests(self) -> ManifestOperations: ...

    def project(self, project_id: str) -> CorePort: ...
```

代表性签名（其余见 §4 总表）：

```python
class SessionOperations(Protocol):
    async def catalog(self, *, agent_id: AgentId) -> SessionCatalogView: ...
    async def history(
        self, *, agent_id: AgentId, session_id: SessionId, branch_id: BranchId, limit: int = -1
    ) -> HistoryView: ...
    async def head(
        self, *, agent_id: AgentId, session_id: SessionId, branch_id: BranchId
    ) -> NodeId: ...
    async def create(
        self, *, agent_id: AgentId, name: str | None = None, system_prompt: str | None = None
    ) -> SessionId: ...


class MailboxOperations(Protocol):
    async def list(
        self, *, agent_id: AgentId, inbox: bool = True, limit: int = 50, cursor: str | None = None
    ) -> Page[MessageView]: ...
    async def publish_external(
        self,
        *,
        sender: str,
        recipient_ids: Sequence[str],
        content: str,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishedMessageView: ...
```

签名约定：

- **全部关键字参数**（`*` 之后），除 `project(project_id)` 与无参方法；
- 入参只用标量、`list`/`dict`、契约包 DTO、`Sequence`；
- 出参只用 DTO、`Page[T]`、`Operation`、裸 id、`int`、`None`；
- 每个方法一行 docstring 写明"什么时候抛什么码"。

## 5.3 拆壳：接口层统一，端口层拆分

接口层的出参必须是 DTO（`invoke` 第 4 步强制），但端口层希望返回裸值。两层的桥是"出参信封"：

| 端口返回 | 接口层信封 | 拆壳位置 |
| --- | --- | --- |
| `NodeId` | `NodeIdView.node_id` | `ghrah-core/src/ghrah/core/api/facade.py:270` |
| `SessionId` | `SessionIdView.session_id` | 同文件 `:291` |
| `BranchId` | `BranchIdView.branch_id` | 同文件 `:340` |
| `MessageId` | `MessageIdView.message_id` | 同文件 `:538` |
| `DeliveryId` | `DeliveryIdView.delivery_id` | 同文件 `:565` |
| `AgentId` | `AgentIdView.agent_id` | 同文件 `:590` |
| `list[MessageId]` | `MessageIdListView.message_ids` | 同文件 `:556` |
| `None` | `AckView.ok` | 各返回 `None` 的方法 |

`AckView` 的存在理由就是这条约束：`invoke` 不接受 `None` 作为出参，而端口层有一批"没有返回值"的写操作，因此需要最小信封承接后丢弃（`ghrah-protocol/src/ghrah/protocol/views/common.py:85`）。

# 6 项目作用域模型

## 6.1 未绑定端口是唯一实例，绑定视图按需生成

装配根只构造**一个未绑定**的 `CoreFacade`；消费方按项目取绑定视图：

```python
port = ctx.get(CORE_PORT)  # 未绑定
scoped = port.project(project_id)  # 绑定视图（轻量，共享收敛点与连接池）
catalog = await scoped.sessions.catalog(agent_id=agent_id)
```

绑定视图是 `CoreFacade(runtime=..., project_id=..., closer=...)`——共享同一个 `CoreRuntime` 与同一组只读连接，只多记一个字符串（`ghrah-core/src/ghrah/core/api/facade.py:183`）。因此"一个进程只能有一个项目"这个隐含约束不存在：一个端口实例可以服务任意多个项目。

## 6.2 `project_id` 由门面注入，不出现在签名里

`CorePort` 的 50 个方法签名中都没有 `project_id`。注入点在唯一的地方：

```python
async def _call(self, capability: str, **kwargs: Any) -> dict[str, Any]:
    if self._project_id is None:
        raise CoreApiError(
            Problem.make(
                ProblemCode.FAILED_PRECONDITION,
                capability=capability,
                detail="capability requires a project-scoped port: call project(id) first",
            )
        )
    return await self._runtime.invoke(capability, {"project_id": self._project_id, **kwargs})
```

这样做的判据：

- **漏传不可能**：作用域不是参数，调用方没有机会忘；
- **越权参数化不可能**：不存在"路径说 A、主体说 B"的分歧，只有绑定视图那一个来源；
- **错误可读**：忘了 `project()` 得到的是 `FAILED_PRECONDITION` 且 detail 直接告诉怎么修。

## 6.3 全局接口走 `_call_global`

`abilities.packages`、`abilities.package_detail` 没有项目维度，走 `_call_global(capability, **kwargs)`，不注入 `project_id`，也不要求先 `project()`（`ghrah-core/src/ghrah/core/api/facade.py:224`）。判断标准是请求信封里**没有** `project_id` 字段——这两处的真相以 §4 总表的入参列为准。

# 7 装配与生命周期

## 7.1 装配根唯一入口：`build_core_port`

```python
def build_core_port(
    *,
    resolve_db_path: DbPathResolver,
    config_factory: ConfigFactory | None = None,
    unit_factory: UnitFactory | None = None,
    manager_factory: ManagerFactory | None = None,
    manifest_resolver: ManifestResolver | None = None,
    install_root: str | None = None,
    config_hook: ConfigHook | None = None,
) -> CoreFacade: ...
```

内部组装顺序（`ghrah-core/src/ghrah/core/wiring.py:111`）：

1. `ChainReadStore(resolve_db_path)` —— 链库只读；
2. `MailboxReadStore(resolve_db_path)` —— 信箱/投递只读；
3. `RuntimeRegistry(config_factory=…, manager_factory=…, manifest_resolver=…, config_hook=…)` —— 活跃单元注册表，也是 `agents.*`/`abilities.tree` 的运行时真相；
4. `AbilityReadStore(resolve_db_path, resolve_run=registry.run_id_for, resolve_tools=registry.tools_for, install_root=…)` —— 能力关系库读面；
5. `registry.set_ability_store_factory(...)` —— 授权关系库由 Core 自持，`reclaim_stale=True` 回收上一进程异常退出留下的 pending 占位；
6. `CoreRuntime(build_handlers(chain, mailbox, abilities, registry))` —— 收敛点（此处校验 50 条能力与处理器齐全）；
7. `CoreFacade(runtime=…, closer=lambda: _close_all(chain.aclose, abilities.aclose, registry.aclose))`。

**部署设置不在 Core 里读**：HITL 策略、工作区根、能力安装目录由 `config_factory` / `config_hook` / `install_root` 注入。Core 不读环境变量、不猜配置。

**依赖方向**：`wiring.py` 是 Core 内唯一可以同时认识读面、注册表、收敛点、门面的文件；其余文件只依赖自己下一层。

## 7.2 注入与关闭

```python
# 装配根（不属于任何模块）
port = build_core_port(resolve_db_path=..., config_factory=..., install_root=...)
ctx.provide(CORE_PORT, port)  # 键来自 ghrah.protocol.ports.keys

# 关闭（与创建逆序）
await port.aclose()
```

- `CORE_PORT` 是字符串常量 `"core.port"`，由契约包定义，两侧共用同一个字面量，不各自硬编码；
- `CoreFacade.aclose()` 幂等；关闭任一绑定视图等价于关闭共享连接池（`closer` 是同一个），连接池可在下次调用时懒重开；
- 端口对象**不做进程级全局单例**：由装配根创建、注入、关闭。这样测试可替换、多应用实例不串味、连接有明确负责人。

# 8 错误模型

## 8.1 载荷

```python
class Problem(BaseModel):
    code: ProblemCode  # 唯一机器判据
    capability: str = ""  # 发生位置，如 "nodes.get"
    detail: str = ""  # 人类可读
    details: dict[str, Any] = {}
    retryable: bool = False
    trace_id: str = ""
```

`ProblemCode` 有 13 个值：`INVALID_ARGUMENT` / `PERMISSION_DENIED` / `NOT_FOUND` / `CONFLICT` / `BUSY` / `FAILED_PRECONDITION` / `PRECONDITION_FAILED` / `PRECONDITION_REQUIRED` / `CURSOR_EXPIRED` / `RESOURCE_ARCHIVED` / `PROMISE_NOT_HELD` / `UNAVAILABLE` / `INTERNAL`。

只有 `BUSY` 与 `UNAVAILABLE` 默认 `retryable=True`，其余默认 `False`——重试只会重复失败。`Problem.make(...)` 允许显式覆盖 `retryable`。

载荷里**没有** HTTP 状态码，也**没有** `type` URI：`code` 已经是唯一标识，URI 只是它的冗余投影，多存一份就会不一致。

## 8.2 异常族

```python
class ApiError(Exception):  # ghrah.protocol.errors
    problem: Problem

    @property
    def code(self) -> str: ...


class CoreApiError(ApiError): ...  # ghrah.core.api.errors
```

消费方 `except ApiError` 一处即可捕获所有模块的端口失败，读 `exc.problem.code` 分支；需要精确定位 Core 时再 `except CoreApiError`。基类必须住在契约包——否则消费方要 `except` 一个它不许 import 的类型。

## 8.3 翻译表

`translate(capability)` 是 `contextmanager`，在 `invoke` 第 3 步包住处理器。映射（`ghrah-core/src/ghrah/core/api/errors.py:57`）：

| 内部异常 | 错误码 | 附加信息 |
| --- | --- | --- |
| `ChainDatabaseUnavailableError` | `NOT_FOUND` | — |
| `MailboxUnavailableError` | `NOT_FOUND` | — |
| `MessageNotFoundError` | `NOT_FOUND` | — |
| `DeliveryNotFoundError` | `NOT_FOUND` | — |
| `MailboxProjectMismatchError` | `FAILED_PRECONDITION` | — |
| `AbilityConflictError` | `FAILED_PRECONDITION` | `details.revision`、`details.view` |
| `CatalogError` | 按 `exc.code`：`not_found`→`NOT_FOUND`、`invalid_request`→`INVALID_ARGUMENT`、`unavailable`→`UNAVAILABLE`，其余 `INTERNAL` | `detail=exc.message` |
| `AbilityUnavailableError` | `UNAVAILABLE` | — |
| `AccessError` | reason 为 `unavailable`/`not_found` → `NOT_FOUND`，否则 `FAILED_PRECONDITION` | — |
| `ValueError` / `ValidationError` | `INVALID_ARGUMENT` | — |
| `CoreApiError` | 原样透传 | — |
| 其它 `Exception` | `INTERNAL` | `detail = "<类型名>: <消息>"`，同时 `logger.exception` |

两条规则：**先匹配具体异常，最后兜底 `Exception`**；**绝不把陌生异常穿出边界**。兜底分支会记日志再包装，所以根因仍可追。

# 9 三条强制点

| 强制点 | 位置 | 违反时的表现 |
| --- | --- | --- |
| 入参按声明校验 | `InterfaceRuntime._validate_request` | `INVALID_ARGUMENT` + `details.errors` |
| 出参是 DTO 且 JSON 可往返 | `InterfaceRuntime._validate_response` | `check_json_serializable` 抛 `JsonShapeError`（不是静默转换） |
| 内部异常不穿出模块 | `translate()` 的 `except Exception` 兜底 | 统一成 `INTERNAL`，并留日志 |

另有一条**启动期**强制：`InterfaceRuntime.__init__` 要求"清单名字集合 == 处理器名字集合"，不一致直接 `RuntimeError`，消息里给出 `missing` 与 `orphan` 两个差集。这条把"忘了注册处理器"从运行时偶发变成启动即失败。

# 10 边界闸门

`ghrah-subject` 的测试里有一份 AST ratchet：`ghrah-subject/tests/test_module_boundary.py` + 同目录 `module_boundary_baseline.json`。

- 禁止前缀：`ghrah.core` / `ghrah.context` / `ghrah.communication` / `ghrah.ability` / `ghrah.agents` / `ghrah.manifest` / `ghrah.types`；
- 检查 `ghrah-subject/src/ghrah/subject/**` 下所有 `.py` 的**绝对** import（相对 import 属于包内引用，跳过）；
- 另检查 `ghrah-subject/pyproject.toml` 是否仍声明 `ghrah-core` 依赖；
- 只降不升：新增违规失败；违规减少但基线没下调也失败（强制把基线往下推）；
- 更新方式：`$env:MODULE_BOUNDARY_UPDATE='1'` 后跑该测试。

**当前基线 35 条**，分布：

| 类别 | 条数 | 说明 |
| --- | --- | --- |
| `pyproject.toml::ghrah-core` | 1 | Subject 仍声明对 `ghrah-core` 的依赖 |
| `ghrah.core.*` | 3 | `runtime_connection.py::ghrah.core.runtime`；`core_cluster/registry.py::ghrah.core.unit`；`units/core_cluster.py::ghrah.core.wiring` |
| `ghrah.context.*` | 4 | `core_cluster/registry.py` 的 `sqlite_backend`；`project/entities` 三处（`context.persistence`、`sqlite_backend`、`session_runtime`） |
| `ghrah.communication.*` | 3 | `core_cluster/registry.py`、`project/entities` 的 `mailbox_reader`、`units/room` |
| `ghrah.ability.*` | 14 | `config` 三处、`ability_catalog` 四处、`core_cluster/registry`、`manifest_store/service`、`project/manager`、`sandbox/executor`、`units/shipped_abilities` 两处 |
| `ghrah.agents.entity` | 1 | `project/entities` |
| `ghrah.manifest.*` | 4 | `manifest_store/service` 一处、`manifest_store/store` 三处 |
| `ghrah.types.results` | 5 | `shipped_abilities/project_agent_manage` 四个操作 + `_shared` |

口径：35 = 上表 1+3+4+3+14+1+4+5。

口径说明：这条闸门只覆盖**静态 import**。`importlib.import_module` / `__import__` / 拼接模块名抓不到——这是刻意取舍（AST 可解释、零误报），动态导入靠 code review。

# 11 Subject 侧如何接入

## 11.1 从容器取端口

Subject 不 import `ghrah.core`，只 import 契约包：

```python
from ghrah.protocol.ports.keys import CORE_PORT

port = ctx.get(CORE_PORT, strict=False)
```

`ghrah-subject/src/ghrah/subject/units/ledger.py:83` 就是这样取的（`strict=False` 允许未注入时降级）。另有一条服务键别名 `CORE_PORT_SERVICE = SubjectServiceKey[Any]("core.port")`（`ghrah-subject/src/ghrah/subject/runtime/service_keys.py:278`），值与 `CORE_PORT` 相同，供 `ctx.provide(ctx.get(...))` 风格的单元使用。

## 11.2 谁提供端口

`CoreClusterUnit` 在 mount 时构造并注入：

```python
from ghrah.protocol.ports.keys import CORE_PORT

core_port = build_core_port(...)  # ghrah.core.wiring
ctx.provide(CORE_PORT, core_port)
ctx.provide(CORE_CLUSTER_REGISTRY.name, self._transport)
```

因此端口生命周期跟随 CoreClusterUnit：单元在则端口在，单元走则端口随之关闭。这也是 Subject 不再自己创建/挂载 `CoreUnit` 实例的落点。

## 11.3 端口传输层

`ghrah-subject/src/ghrah/subject/runtime/port_transport.py` 把历史上的 cluster 命令面适配到端口：

| 类 | 职责 |
| --- | --- |
| `PortClusterTransport` | 实现 `CoreClusterRegistryService`；把命令转成端口调用；维护 `cluster_id → project_id` 映射 |
| `PortMailbox` | 按项目访问投递的端口替身，只实现 Subject 实际用到的成员（不给旁路留口子） |
| `_Progress` / `_Published` | `MessageManager.get_progress` / `send*` 的最小替身，只暴露 `delivery` / `message_id` |
| `build_port_transport(ctx)` | 从 ctx 取 `CORE_PORT` 构造传输层（`port_transport.py:514`） |

`entity_context` 这类历史遗留的"操作字典"入口在传输层被拆成具体的端口调用，返回值统一是协议视图的 `model_dump`——与观察端/WS 契约同源，不再有第二套形状。

命令到端口的转换在 `PortClusterTransport.dispatch(command, payload)`（`port_transport.py:305`）内完成；历史上 `ctx.serial` 的"返回 `None` 表示不归我管、传给下一个处理器"这条语义在端口侧没有等价物，多项目场景必须由调用方按 id 路由。

# 12 新增一条接口的步骤

1. **定分组与动作**：分组名要已是 `CorePort` 上的属性（新增分组要同时改 `CorePort`、`ports/__init__.py` 的再导出、门面的属性），动作名用动词。
2. **定义请求信封**：在 `core/api/interfaces.py` 里加 `<分组><动作>Request`，除全局能力外必须含 `project_id: str`。
3. **定出参**：返回 DTO 时直接用 `views/` 里的模型；返回裸 id/计数时用 `views/common.py` 的信封，端口层再拆壳。
4. **登记接口**：往 `CORE_INTERFACES` 加 `Capability(name=..., request=..., response=...)`；顺序即枚举顺序。
5. **写处理器**：在 `core/api/handlers/` 对应模块里加 `async def ...(request) -> ...`；处理器只认请求信封，不认作用域（已被注入）。
6. **接进 `build_handlers`**：漏接会在启动时被"清单与处理器集合不一致"拦下，消息里给出差集。
7. **加端口方法**：分组协议加签名 + 门面分组类里 `raw = await self._facade._call(name, **kwargs)` 然后 `Model.model_validate(raw)`（或拆壳）。
8. **补翻译**：处理器可能抛的新异常加到 `translate()` 里，放在兜底 `Exception` 之前。
9. **录契约样本**：在 corpus 里录一条成功 + 一条失败，`check_json_serializable` 会同时校验形状。
10. **更新本表**：§4 总表加一行；若是新分组，§5.2 也要加。

判据：**新增接口不应该修改 `ghrah-protocol` 里除 `ports/`、`views/` 之外的任何文件**；如果动了 `interface.py`，说明骨架不成立，应先改骨架设计。

# 13 已知偏差

以下问题**已确认存在且未修复**，读代码时按此打折：

| 编号 | 偏差 | 影响 |
| --- | --- | --- |
| P-1 | `CorePort` 的 docstring 仍写"当前落地项目读面（nodes/sessions/branches）；其余分组随各自阶段追加"，而实际 10 个分组、50 条能力全部落地 | 读 docstring 会低估能力面 |
| P-2 | `AbilityOperations` 的 docstring 仍写"`abilities.tree` 与 `agents.runtime` 一起留到在线批次；本组当前只有纯读的变更流"，两者实际都已实现 | 同上 |
| P-3 | `SUBJECT_PORT = "subject.port"` 已定义但**无任何实现与提供方**，也没有 `SubjectPort` 协议 | 键是空声明；Subject 侧能力面尚未端口化 |
| P-4 | 边界基线仍含 `pyproject.toml::ghrah-core`，`ghrah-subject` 仍依赖 `ghrah-core` 包 | 边界目标是"零 import"，依赖本身还在 |
| P-5 | 基线文件 `note` 引用 `plans/planning/module-port-contract.md §10`，且 `test_module_boundary.py:169-174` 的重建提示又硬编码了另一个 `plans/` 路径 | 违反"项目代码不引用 plans 文档"的仓规；且两处路径不一致，提示语与实际文件对不上 |
| P-6 | 六个标识类型都是 `str` 别名，混用不可被类型检查发现 | 只能靠全部关键字参数与评审 |
| P-7 | 请求信封命名不完全统一（`NodeGetRequest` vs `SessionsCatalogRequest`） | 按名字检索会有偏差，以 §4 总表为准 |

# 14 维护对照表

| 你改了 | 必须同步 |
| --- | --- |
| `protocol/interface.py`（骨架） | 本文 §2、§9；并评估所有模块的收敛点 |
| `protocol/ports/core.py`（端口签名/分组） | 本文 §4、§5；`ports/__init__.py` 的再导出 |
| `protocol/views/*`（DTO 字段） | 本文 §4 出参列；消费方 `model_validate` 的字段访问 |
| `protocol/problem.py`（错误码） | 本文 §8.1、§8.3 的映射表 |
| `core/api/interfaces.py`（增删能力） | 本文 §4 总表；第 12 节步骤 6 的 `build_handlers` |
| `core/api/errors.py`（翻译表） | 本文 §8.3 |
| `core/wiring.py`（依赖注入项） | 本文 §7.1 |
| `core/read/*`（读面） | 本文 §3.2 的职责表与"解耦"说明 |
| Subject 侧接入方式 | 本文 §11；基线 JSON 的违规条目 |