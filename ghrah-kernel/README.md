# ghrah-kernel

`ghrah-kernel` 是 ghrah 的应用微内核实现：一个持有进程、句柄与调用事实的本体，加上按期望状态驱动受管服务的监督者，以及它们共享的结构化消息 SDK。导入名为 `ghrah_kernel`。

本包提供 14 个基础入口、两种 shutdown、策略型监督者，以及受信启动清单和真实命名服务。连接由宿主绑定稳定主体与解析 Scope（本次会话解释名字时使用的根和挂载集合）；两个会话可以把同名路径解析到不同实例。真实 ActorAgent 与 Ability Provider 尚未接入。

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
│   ├── integration/        # 静态身份配置、真实命名服务与受管入口
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

`_kernel` 是供宿主注入连接 FD 的低层入口，必须显式传入 `--config <宿主配置文件>`。配置含 `launches` 和 `callers`，后者分别声明 `control`、`observer`、`peer` 三个预建连接的主体、解析 Scope 与诊断许可。普通客户端的 `hello` 或业务 payload 不能覆盖这些绑定。

用户态的 `StaticIdentityConfig` 记录稳定 Subject、直接 Group 成员、资源 Namespace、解析 Scope 和服务入口许可。配置在一次部署中冻结；组成员、资源 owner 或名字可见均不自动授予调用权。命名服务只改挂载，目标服务独立核验入口许可。Provider 的 handler 接收 `ProviderContext`，业务参数单独传入，并显式返回带效果证据的 `ProviderResult`；取消和异常缺少证据时保持 `UNKNOWN`。

## 许可期限必须显式配置

`StaticIdentityConfig.grant_lifetime` 是必填的受信部署配置，服务入口许可与命名视图共用同一签发函数。没有固定的 300 秒期限，也不为缺字段的旧配置补默认值：

- `{"mode": "instance"}`：不另设计时上限，许可的 wire `expiry` 为正无穷。句柄仍受会话关闭、精确实例退出、撤权和父句柄上限约束。当前静态装配及独立安装示例显式选择此模式。
- `{"mode": "ttl", "ttl_seconds": 60}`：显式选择有限正秒数，从每次签发时刻计算单调时钟截止时间；不是调用 deadline。到期后旧句柄返回 `EXPIRED`，没有自动续期或透明重放。消费者须读取 `open` 回执的 `expiry` 并显式处理到期；重新打开也不会改变旧引用。

有更短期限的父句柄时，`open` 回执返回收窄后的实际期限；`instance` 不能绕过它。NaN、负无穷和布尔值均不是合法的许可期限。未来 Agent 选择有限 TTL 时，需要同时提供显式的到期处理，不能只缩短期限而假定消费者会续期。

## 开发

```sh
# 仓库根执行；uv 解析根 workspace
uv run pytest ghrah-kernel -q -W error -o faulthandler_timeout=10
uv run ruff check ghrah-kernel && uv run ruff format --check ghrah-kernel

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
- 未接入：真实 ActorAgent、生产工具/内容/MCP 消费、独立 Ability Provider 与发行版默认启动入口。
- 未迁入：机制实验仓的 demo／verify 场景装配与 mock 受管入口；它们保留为可复现证据，不进入产品线。
