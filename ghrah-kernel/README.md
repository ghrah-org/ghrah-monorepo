# ghrah-kernel

`ghrah-kernel` 是 ghrah 的应用微内核实现：一个持有进程、句柄与调用事实的本体，加上按期望状态驱动受管服务的监督者，以及它们共享的结构化消息 SDK。导入名为 `ghrah_kernel`。

本包提供 14 个基础入口、两种 shutdown、策略型监督者，以及受信启动清单、真实命名服务和受限文本 Agent 宿主。连接由宿主绑定稳定主体与解析 Scope（本次会话解释名字时使用的根和挂载集合）；两个会话可以把同名路径解析到不同实例。Agent 宿主使用真实 ActorAgent 与 SQLite Context，目前显式使用离线脚本模型。

## 包里有什么

```
ghrah-kernel/
├── src/ghrah_kernel/
│   ├── abi.py              # 14 个基础入口的唯一权威清单（冻结快照由测试钉住）
│   ├── protocol.py         # 控制信封编解码；业务载荷保持不透明
│   ├── payloads.py         # SDK 业务编解码
│   ├── wire.py             # 标准库消息连接的薄适配，不实现分帧
│   ├── endpoint.py         # 并发端点与收发名额
│   ├── client.py           # 请求／响应客户端
│   ├── kernel_client.py    # 面向内核 ABI 的客户端封装
│   ├── context.py          # 受信连接绑定与独立于业务参数的调用上下文
│   ├── launch.py           # 固定入口、解释器、环境与配置的受信清单
│   ├── provider.py         # 注册、打开、调用、取消与显式效果回执
│   ├── integration/        # 静态身份、命名服务、ActorAgent 宿主与受管入口
│   ├── kernel_main.py      # 内核宿主装配
│   ├── supervisor.py       # 期望状态与重启策略
│   └── kernel/             # 内核本体：进程、句柄、调用、引用、事件、流、收尾
└── tests/
    ├── _mechanism/         # 机制回归夹具：替身服务、固定启动链、场景装配
    ├── integration/        # 身份/Scope、受信启动与上下文的跨进程验收
    └── test_*.py           # 机制回归与 ABI 冻结快照
```

内核本体（`kernel/`、`supervisor.py`）不导入领域模块；替身和探针只在 tests 中。产品命名服务可从独立 wheel 启动，不依赖这些测试模块。

## 边界

- **做**：持有跨进程会话、句柄与调用事实；按显式清单启动、监控、回收受管进程；提供 14 个基础入口的消息语义。
- **不做**：业务动作的领域解释、任意请求指定的模块加载、完整账号系统、动态组成员撤回、真实 LLM 接线。静态服务入口许可与 Ability 成员授权各有归属，命名挂载不产生目标访问权。

## 受管服务怎样启动

宿主在 `LaunchCatalog` 中预先声明模块、解释器、固定参数、环境和连接绑定。空清单拒绝所有服务启动；请求只能选择清单条目及启动实例键，不能提交模块路径或环境覆盖。`entry_key` 选择条目，省略时使用 `kind`；兼容字段 `fixture` 只选择清单中显式声明的 `variant`，Kernel 不再解释故障模式。

`_kernel` 是供宿主注入连接 FD 的低层入口，必须显式传入 `--config <宿主配置文件>`。配置必须包含 `identities`、`launches` 和 `callers`；`identities` 是唯一静态身份来源，`callers` 分别声明 `control`、`observer`、`peer` 三个预建连接的主体、解析 Scope 与诊断许可。所有连接和启动条目的绑定均从该目录核定，服务身份快照由装配层派生。若条目保留了内嵌身份快照，则必须与宿主完全一致；未知主体、非法 Scope 或快照漂移在创建服务子进程前失败。普通客户端的 `hello` 或业务 payload 不能覆盖这些绑定。

用户态的 `StaticIdentityConfig` 记录稳定 Subject、直接 Group 成员、资源 Namespace、解析 Scope 和服务入口许可。配置在一次部署中冻结；组成员、资源 owner 或名字可见均不自动授予调用权。命名服务只改挂载，目标服务独立核验入口许可。Provider 的 handler 接收 `ProviderContext`，业务参数单独传入，并显式返回带效果证据的 `ProviderResult`；取消和异常缺少证据时保持 `UNKNOWN`。

## 受限文本 Agent 宿主

默认安装仅依赖消息编码库。真实 Agent 通过 `agent` 可选依赖引入 Core；本地构建验收须一并提供 Core、Protocol、Plugin 的 wheel，后者尚无发布通道。命名服务和内核本体仍可在未安装 Core 时独立运行。

受信启动条目选择 `module="ghrah_kernel.integration.entry"`，配置使用 `provider="agent"`，并显式给出 `principal`、`administrator`、`namespace`、绝对路径 `database` 与 `model`。`principal` 必须与该启动条目的连接绑定一致，管理主体必须与宿主控制连接一致。模型配置当前形如 `{"kind":"scripted","responses":["回答"],"delay_seconds":0}`；序列耗尽会报错，不会转入真实模型或重复播放。

服务只提供以下消息端口，入口资源名为 `agent`，许可由静态服务规则核定：

- `receive`：只接收 `{"type":"chat","text":"你好"}`，文本为 1–8192 UTF-8 字节。不接收 COMMAND、sender、recipient、metadata、run_id 或工具选择。发送者从可信调用上下文产生，接收者固定为此 Agent；回复含输入编号、回复编号、关联编号、双方主体及真实回复文本。
- `status`：参数为 `{}`，通过 Actor 的公开接口读取会话目录，并返回进程、运行代次、忙闲与脚本模型调用次数。
- `history`：参数为 `{"limit":16}`，limit 范围 1–16；通过 Actor 的公开接口读取当前活动会话和分支的已提交节点。大结果仍受消息端点上限约束。
- `close_resources`：只允许控制主体持管理根调用。普通打开的资源句柄不授予此动作。

宿主显式构造 ActorAgent、ContextManager 和 SqliteBackend，只注册内置 ConversationAbility 及其正常结束 hook，不装入业务工具。ContextManager 是唯一写侧；每次真实回复返回前等待显式 `persist()`，落库失败不会返回成功。初始化时先恢复已有检查点，重启保留主体和历史，运行代次与旧句柄资格仍按 Kernel 规则变化。这里不暴露 reset/rebase，也没有第二份事件写库。

Supervisor 使用 `ServiceBinding(path, scheme, scope, namespace)` 完成真实命名挂载，使用显式 `entry_key` 选择启动记录。旧二元组绑定仅保留替身协议兼容。为 Agent 声明 `close_action=("agent", "close_resources")` 后，正常 stop/restart、aclose 和 drain shutdown 先请求应用关闭，再让 Kernel 回收进程。关闭会停止并等待 Runner、持久化检查点、关闭 SQLite；失败会保留错误并继续回收。`now` shutdown、崩溃和强杀不保证运行应用关闭逻辑。

## 许可期限必须显式配置

`StaticIdentityConfig.grant_lifetime` 是必填的受信部署配置，服务入口许可与命名视图共用同一签发函数。没有固定的 300 秒期限，也不为缺字段的旧配置补默认值：

- `{"mode": "instance"}`：不另设计时上限，许可的 wire `expiry` 为正无穷。句柄仍受会话关闭、精确实例退出、撤权和父句柄上限约束。当前静态装配及独立安装示例显式选择此模式。
- `{"mode": "ttl", "ttl_seconds": 60}`：显式选择有限正秒数，从每次签发时刻计算单调时钟截止时间；不是调用 deadline。到期后旧句柄返回 `EXPIRED`，没有自动续期或透明重放。消费者须读取 `open` 回执的 `expiry` 并显式处理到期；重新打开也不会改变旧引用。

有更短期限的父句柄时，`open` 回执返回收窄后的实际期限；`instance` 不能绕过它。NaN、负无穷和布尔值均不是合法的许可期限。未来 Agent 选择有限 TTL 时，需要同时提供显式的到期处理，不能只缩短期限而假定消费者会续期。

## Unix 管道流与额度

流数据仍经过真实 OS pipe，FD 不交给客户端。Linux 使用 pipe2；macOS 等缺少该接口的
Unix 使用不可继承的 pipe，并显式开启非阻塞模式。当前修复不代表 Windows 流后端已支持。

通道的 effective_quota 是内核允许的“已写入但尚未读取”的字节上限，范围不超过提供者
协商值。Kernel 按实际成功读写的字节维护额度；写满后等待读取释放额度，部分读取仅释放
对应字节。额度等待、OS 就绪等待均受相同 deadline、单方向 BUSY 和撤权/关闭规则约束。

Linux 继续尝试调整并查询物理 pipe 容量，额度不超过已知物理容量；没有容量控制接口时，
内核仍严格执行协商额度。该值不保证 OS 分配同样大小的物理缓冲，也不保证单次 write
完整接收所有数据；调用方须继续处理部分进度。额度满时不监听可写 FD，避免宿主缓冲仍有
余量时出现空转。关闭读端会丢弃剩余数据，关闭写端仍允许读取已缓冲数据直至 EOF。

## 开发

```sh
# 仓库根执行；uv 解析根 workspace
uv run pytest ghrah-kernel -q -W error -o faulthandler_timeout=10
uv run ruff check ghrah-kernel
uv run ruff format --check ghrah-kernel

# 独立安装验收；只安装 kernel 和它的依赖，再真实启动命名服务
uv build --package ghrah-kernel --out-dir /tmp/ghrah-kernel-check
uv run --isolated --no-project \
  --with /tmp/ghrah-kernel-check/ghrah_kernel-0.1.0a0-py3-none-any.whl \
  python -I scripts/verify_kernel_install.py
```

机制回归需要真实子进程与 Unix 本地 Pipe；在限制子进程或本地 IPC 的沙箱里会失败，这不是回归。

## 当前状态

- 已迁入：内核、监督者、消息基座与 97 项机制回归。
- 已接入：受信启动清单、静态主体/Scope、真实命名服务、可信调用上下文与显式效果端口；CI 包含 wheel 独立启动验收。
- 已接入：受限文本 ActorAgent、Context 落库/恢复、真实 Supervisor 挂载与正常资源关闭；CI 配置包含可选 Agent wheel 隔离验收，本地执行入口为 `scripts/verify_agent_install.py`。
- 未接入：真实模型、生产工具/内容/MCP 消费、独立 Ability Provider 与发行版默认启动入口。引用祖先记录及实例表的长期有界回收仍须完成。
- 未迁入：机制实验仓的 demo／verify 场景装配与 mock 受管入口；它们保留为可复现证据，不进入产品线。
