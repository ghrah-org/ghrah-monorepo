---
title: ghrah-scheme
description: ghrah 自用的进程内 Scheme 模型与控制平面原型
created: 2026-10-26
updated: 2026-10-26
status: active
---

# ghrah-scheme

ghrah 的进程内 Scheme 模型与控制平面原型，导入名为 `ghrah.scheme`。当前只面向 ghrah 自身，不承诺通用 OS 框架或仓外独立系统。包已进入 monorepo 开发与测试基线，现有 Core／Subject 运行通路尚未切换，未纳入对外发布白名单。

## 提供什么

- `SchemeSpec`／`ActionSpec` 声明域、版本和严格的输入／输出模型。
- 受信 `RuntimeManager` 发布、撤回、绑定、取消绑定并建立访问会话。
- 消费者 `Session` 提供 acquire、submit、observe、cancel、release 与自身关闭。
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

测试包含可控提供者、独立测试策略和真实 Core 文件 Ability 接入。测试策略不随包安装，运行包不依赖 Core、Subject 或测试模块。

## 使用关系

先在 asyncio 运行环境中显式创建 `SchemeRuntime(authority)`。`authority` 由宿主实现 `Authority`：`authorize` 核定引用取得与结果读取，`admit` 核定当前许可并提交必要的领域接纳。没有显式端口时使用 `DenyAuthority`，不会自动允许访问。产品授权记录继续由对应领域持有，本包没有 grant 数据库。

装配者保留 `runtime.manager`，调用 publish／bind／create_session；向普通消费者只注入 Session。管理端口接收的是宿主已经核定的 `Principal`，不是认证服务；进程内私有属性和不透明 token 都不能抵御任意恶意 Python 代码。

消费者可以按下列方式调用已经装配好的会话；动作、输入与目标取决于宿主契约：

```python
resource = await session.acquire(
    "/scheme/ability/notes", actions=frozenset({"write"}), contract_version="1"
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

## 语义和支持范围

submit 返回的是运行调用引用，不是业务接纳成功。`AdmissionState` 与运行状态、`EffectState` 分开；`COMPLETED` 表示提供者返回了有效结果，结果中的业务成败由领域模型解释。接纳端口的提交／撤销顺序由宿主负责，原型不宣称与外部效果共享原子事务。

observe 的 `wait` 只限制状态变化等待，读取正文另有核定等待预算（不超过 `default_timeout`），核定失败时返回正文被遮蔽的最小状态。观察超时不取消执行；撤销后仍可查询自有调用的最小收尾状态、取消及释放。release 不撤销权限，也不取消已经接纳的请求。关闭会话则明确请求取消自有调用，剩余责任归运行 owner。

cancel 是协作式请求。确认取消表示执行协程响应了取消，不表示外部写入被回滚或子进程已退出。withdraw 的 `WITHDRAWN` 只表示运行导出已退出；非合作任务阻止同名实例替换，保留在 `DrainReport` 中。

`cancellation` 和 `cancel_reason` 保留停止意图；`error` 记录执行终态错误。提供者在请求取消后仍返回有效结果时，状态可为 `COMPLETED`、取消仍为 `REQUESTED`，不能由取消意图覆盖实际结果。父调用取消会传递给已登记子调用，取消后不能继续发起嵌套工作。

去重只限当前 boot：同主体、namespace、run 和 request_key 的重复调用关联同一目标与输入；内容或绑定变化拒绝。完成记录有保留上限，去重键保留为有界索引，记录过期返回 `REQUEST_EXPIRED`，不静默重做；索引满时拒绝新增键。跨 boot 的持久去重和业务对账归领域 owner。变更动作要求显式 request_key。

同提供者重入明确拒绝；跨提供者嵌套只在目标没有排队风险的容量内允许，繁忙时返回 `NESTED_BUSY`，不形成互相等待的执行槽环。没有公平排队或硬实时保证；阻塞同步代码仍可阻塞 asyncio loop。

`RuntimeLimits` 配置当前 boot 的管理表、在途调用、会话引用、正文大小与去重容量。会话关闭后，没有在途责任与观察者的会话可在下一次创建会话时回收。提供者标识、契约及去重索引在本 boot 内保留，容量满时显式拒绝新增；需要长期高频动态发布的宿主应另行设计安全回收政策。该运行时仅在创建它的 asyncio loop 上使用。

尚未实现进程外传输、OS 沙箱、自由句柄委托、通用持久 Job／流框架、Core 通用 grant 事务及正式 Subject 迁移。资源引用目前是运行访问关系，不自动替提供者打开／关闭 OS 文件或管理远端租约；具体领域资源仍由领域提供者负责。
