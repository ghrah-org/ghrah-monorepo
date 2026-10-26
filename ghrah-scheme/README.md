---
title: ghrah-scheme
description: ghrah 自用的进程内 Scheme 模型与控制平面原型
created: 2026-10-26
updated: 2026-10-27
status: active
---

# ghrah-scheme

ghrah 的进程内 Scheme 模型与控制平面原型，导入名为 `ghrah.scheme`。当前只面向 ghrah 自身，不承诺通用 OS 框架或仓外独立系统。包已进入 monorepo 开发与测试基线，现有 Core／Subject 运行通路尚未切换，未纳入对外发布白名单。

## 提供什么

- `SchemeSpec`／`ActionSpec` 分别声明资源 kind、契约身份／版本及严格的动作输入／输出模型；同 kind 可以有不同契约。
- 受信 `RuntimeManager` 发布、撤回、绑定、取消绑定并建立访问会话。
- 消费者 `Session` 提供 acquire、submit、observe、cancel、release 与自身关闭；catalog／describe 提供受控只读发现。
- 显式资源 owner 执行准备与关闭，支持共享导出、实例健康报告、关闭残余和显式重试。
- 引用固定 boot、持有者、绑定与提供者代次，排队、接纳、派发、结果与取消分别记录。
- 有界排队、结果保留、引用、核定和观察预算；管理表也有容量上限，关闭报告保留非合作调用及核定任务。

## 在 workspace 中验证

在 monorepo 根运行：

```sh
uv sync --all-packages --group dev --frozen
uv run pytest ghrah-scheme
uv run ruff check ghrah-scheme
MYPYPATH=ghrah-scheme/src uv run mypy --explicit-package-bases -p ghrah.scheme
uv build --package ghrah-scheme
```

测试包含可控提供者、独立测试策略和真实 Core 文件 Ability 接入。测试策略不随包安装，运行包依赖 ghrah-protocol 的公共描述格式与 Pydantic，不依赖 Core、Subject 或测试模块。

## 使用关系

先在 asyncio 运行环境中显式创建 `SchemeRuntime(authority)`。`authority` 由宿主实现 `Authority`：`authorize` 分别核定 DISCOVER（摘要可见）、DESCRIBE（完整 schema 可读）、INVOKE（工具投影可调用）、引用取得与结果读取，`admit` 核定当前许可并提交必要的领域接纳。没有显式端口时使用 `DenyAuthority`，不会自动允许访问。产品授权记录继续由对应领域持有，本包没有 grant 数据库。

装配者保留 `runtime.manager`，调用 publish／bind／create_session；向普通消费者只注入 Session。管理端口接收的是宿主已经核定的 `Principal`，不是认证服务；进程内私有属性和不透明 token 都不能抵御任意恶意 Python 代码。

`Principal` 必须显式提供 `project_id`、稳定 `agent_id` 和本次 `run_id`，其 `scope` 为对应的 `AgentScope`。`display_name` 和组织／定义归属 `namespace` 只作元数据，不用于查绑定、授权主体键或去重。共享项目、namespace 或显示名的两个 Agent 仍拥有各自的局部名字表。Agent 的新 run 需要重新安装绑定，不自动继承旧 run；是否停止旧 run，由生命周期 owner 关闭其会话或撤去其绑定，不由创建新会话暗中决定。

受信装配者可以按以下方式安装一个内部契约；示例假设宿主已提供 `authority` 与符合该动作契约的 `provider`：

```python
from ghrah.scheme import ActionSpec, Principal, SchemeModel, SchemeRuntime, SchemeSpec


class WriteInput(SchemeModel):
    value: str


class WriteOutput(SchemeModel):
    value: str


spec = SchemeSpec(
    kind="ability",
    contract="acme.notes",
    version="1",
    actions=(ActionSpec("write", WriteInput, WriteOutput, mutating=True),),
)
principal = Principal(
    project_id="project-1",
    agent_id="agent-uuid",
    run_id="run-uuid",
    display_name="Alice",
    namespace="acme",
)
runtime = SchemeRuntime(authority)
runtime.manager.publish("notes-1", spec, provider)
runtime.manager.bind(
    principal.scope,
    "ability",
    "notes",
    "notes-1",
    contract=spec.contract,
    contract_version=spec.version,
    contract_digest=spec.digest,
)
session = runtime.manager.create_session(principal)
```

契约按 `(contract, version)` 登记，同一版本不能改写，也不能改挂到不同 kind。`spec.digest` 从本地模型生成的内容计算：包括 kind、契约身份／版本、动作名、输入／输出 schema、mutating、cancellable 和 description；动作按名称排序、JSON 对象键排序并使用紧凑 UTF-8 编码，再加 `sha256:` 前缀。schema 内数组保持原序。它是当前契约的精确内容指纹，不承诺跨 Pydantic 版本的摘要稳定性或任意 schema 的语义兼容。公共格式由 ghrah.protocol.scheme 的 ContractDescriptor／ActionDescriptor 定义，spec.descriptor() 单向导出隔离副本；TS 消费者与同一 Python 生成样本对齐。

绑定与解析后的 `Target` 固定契约身份、版本和摘要。bind 会核对期望 kind／contract／version，摘要可额外校验；提供者以相同 ID 重启为不同契约时，旧绑定不能自动改指，必须明确重新接线。

消费者可以按下列方式调用已经装配好的会话；动作、输入与目标取决于宿主契约：

```python
resource = await session.acquire(
    "/scheme/ability/notes",
    actions=frozenset({"write"}),
    contract="acme.notes",
    contract_version="1",
)
try:
    invocation = await session.submit(
        resource, action="write", arguments={"value": "hello"}, request_key="request-1"
    )
    try:
        snapshot = await session.observe(invocation, wait=1.0)
        # pending、取消意图和效果确定性需要分别处理。
    finally:
        await session.release(invocation)
finally:
    await session.release(resource)
```

输入／输出继承 `SchemeModel`，拒绝未知字段与隐式类型转换。跨边界值必须可表达为有上限的 JSON 对象；本地调用不经过 IPC，但仍验证和复制数据，不暴露裸 handler。当前类型只服务于进程内 API；公共 wire 协议将继续由 ghrah-protocol 维护。

## 发现与契约披露

```python
catalog = await session.catalog("ability")
resource = await session.describe("ability", "notes")
for action in resource.actions:
    if resource.available and action.invokable and action.input_schema is not None:
        # 由同一 schema 构造工具投影；实际执行仍须 acquire／submit 和最终 admit。
        print(action.name, action.input_schema)
```

catalog 只枚举当前 AgentScope 的绑定；没有可见动作的资源不出现。describe 对隐藏与不存在统一返回 NOT_FOUND。每个动作分别核定摘要、schema 和执行意图；schema 不可读时输入／输出为 None，不能因为能发现摘要就当作可执行工具。准备中、暂时不可用或失败的提供者可保留可见摘要，但 available／invokable 不会宣称可执行。

发现是从绑定、冻结契约与当前核定结果重建的只读投影，不保存第二份目录或许可表。它有独立的 max_discoveries、整次读取期限和结果大小上限，不进入业务接纳、不持提供者执行槽，也不新增通用写权限。异步核定后和整个目录返回前复核绑定／实例；核定异常按拒绝披露处理。没有隐藏项总数或依赖隐藏项的游标；目前返回有界完整目录，未实现分页和订阅。

目录是核定时的快照，各权限读取并非跨领域原子事务，也不是持续授权。调用前仍复核当前许可和确定目标；持续更新的工具面由消费者重新查询或后续订阅协议驱动。

## 资源 owner 与提供者生命周期

不带 owner 的 publish 保持“宿主已准备好对象、资源由宿主负责”的语义，运行时只管理逻辑导出和调用。需要运行时编排 prepare／aclose 时，显式提供 ProviderLifecycle。下面的发布方式替代前面的已就绪对象发布步骤：

```python
owner = runtime.manager.create_owner(lifecycle)
incarnation = runtime.manager.publish("notes-1", spec, provider, owner=owner)
status = await runtime.manager.wait_prepared(owner, timeout=1.0)
# status.state 为 READY 后可用；有限等待结束不表示准备已失败或被取消。
report = await runtime.manager.withdraw("notes-1", timeout=1.0)
# report.complete 同时检查调用、核定和所需资源收尾；残余继续归原 owner。
```

同一 owner 可以发布多个逻辑导出，prepare 只执行一次；最后一个导出停止接纳且在途调用实际结束后，才执行一次 aclose。关闭消费会话或撤回其中一个共享导出，不关闭其他导出仍在使用的资源。同一个生命周期对象不能登记为两个 owner。

逻辑导出状态为 PREPARING／READY／UNAVAILABLE／FAILED／DRAINING／WITHDRAWN；资源 owner 另有准备、健康、排空、关闭、关闭失败与已关闭状态。`manager.report_health(provider_id, incarnation, state)` 只接受确定实例的报告，共享执行体的活跃导出一起更新；旧实例通知不能影响新实例。单次业务异常不会自动将整个 provider 判为崩溃；READY 不能绕过 prepare 或复活 FAILED／已退出实例。

prepare 有执行预算；wait_prepared 只有观察预算。停止发生在准备期间，或准备超过执行期限后迟到成功，都不会重新发布 READY。aclose 失败、超时或非合作时，DrainReport.pending_owners 保留责任；失败关闭的同名提供者不能被替换。`owner_status` 返回隔离快照；只有先前关闭任务确已结束且失败时，才允许显式 `retry_close(owner)`。aclose 应处理已部分释放的资源并支持安全重试；仍在执行的非合作关闭不能并发重试。

WITHDRAWN 表示运行导出退出。受管理 owner 的 CLOSED 表示生命周期适配器报告完成，物理进程／连接的实际释放仍由该唯一适配器负责；本包不会额外发送信号、强杀或回滚外部效果。

## 语义和支持范围

submit 返回的是运行调用引用，不是业务接纳成功。`AdmissionState` 与运行状态、`EffectState` 分开；`COMPLETED` 表示提供者返回了有效结果，结果中的业务成败由领域模型解释。接纳端口的提交／撤销顺序由宿主负责，原型不宣称与外部效果共享原子事务。

observe 的 `wait` 只限制状态变化等待，读取正文另有核定等待预算（不超过 `default_timeout`），核定失败时返回正文被遮蔽的最小状态。观察超时不取消执行；撤销后仍可查询自有调用的最小收尾状态、取消及释放。release 不撤销权限，也不取消已经接纳的请求。关闭会话则明确请求取消自有调用，剩余责任归运行 owner。

cancel 是协作式请求。确认取消表示执行协程响应了取消，不表示外部写入被回滚或子进程已退出。withdraw 的 `WITHDRAWN` 只表示运行导出已退出；非合作任务阻止同名实例替换，保留在 `DrainReport` 中。

`cancellation` 和 `cancel_reason` 保留停止意图；`error` 记录执行终态错误。提供者在请求取消后仍返回有效结果时，状态可为 `COMPLETED`、取消仍为 `REQUESTED`，不能由取消意图覆盖实际结果。父调用取消会传递给已登记子调用，取消后不能继续发起嵌套工作。

去重只限当前 boot：同 project_id、agent_id、run_id 和 request_key 的重复调用关联同一目标与输入；显示名或归属 namespace 变化不改变该范围，内容或绑定变化拒绝。完成记录有保留上限，去重键保留为有界索引，记录过期返回 `REQUEST_EXPIRED`，不静默重做；索引满时拒绝新增键。跨 boot 的持久去重和业务对账归领域 owner。变更动作要求显式 request_key。

同提供者重入明确拒绝；跨提供者嵌套只在目标没有排队风险的容量内允许，繁忙时返回 `NESTED_BUSY`，不形成互相等待的执行槽环。没有公平排队或硬实时保证；阻塞同步代码仍可阻塞 asyncio loop。

`RuntimeLimits` 配置当前 boot 的管理表、在途调用、会话引用、正文大小与去重容量。会话关闭后，没有在途责任与观察者的会话可在下一次创建会话时回收。提供者标识、契约及去重索引在本 boot 内保留，容量满时显式拒绝新增；需要长期高频动态发布的宿主应另行设计安全回收政策。该运行时仅在创建它的 asyncio loop 上使用。

尚未实现发现分页／订阅、进程外调用传输、OS 沙箱、自由句柄委托、通用持久 Job／流框架、Core 通用 grant 事务及正式 Subject 迁移。资源引用目前是运行访问关系，不自动替提供者打开／关闭 OS 文件或管理远端租约；具体领域资源仍由领域提供者负责。
