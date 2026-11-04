# ghrah-kernel

`ghrah-kernel` 是 ghrah 的应用微内核实现：一个持有进程、句柄与调用事实的本体，加上按期望状态驱动受管服务的监督者，以及它们共享的结构化消息 SDK。导入名为 `ghrah_kernel`。

本包当前承载的是**已验证的机制基线**：14 个基础入口、两种 shutdown 与策略型监督者。它从机制实验仓原样迁入主仓，只做了包名与导入路径改写、内省清单修正这类机械调整；真实 Ability／Agent Provider 与受信启动清单尚未接入。

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
│   ├── kernel_main.py      # 内核宿主装配
│   ├── supervisor.py       # 期望状态与重启策略
│   └── kernel/             # 内核本体：进程、句柄、调用、引用、事件、流、收尾
└── tests/
    ├── _mechanism/         # 机制回归夹具：替身服务、固定启动链、场景装配
    └── test_*.py           # 机制回归与 ABI 冻结快照
```

内核本体（`kernel/`、`supervisor.py`）不导入任何领域模块；受管服务的替身实现只存在于 `tests/_mechanism/`，不是产品代码。

## 边界

- **做**：持有跨进程会话、句柄与调用事实；按显式清单启动、监控、回收受管进程；提供 14 个基础入口的消息语义。
- **不做**：业务动作的领域解释、任意模块加载、账号与权限系统、真实 LLM 接线。受信启动清单与真实 Provider 由后续批次接入。

## 开发

```sh
# 仓库根执行；uv 解析根 workspace
uv run pytest ghrah-kernel -q -W error -o faulthandler_timeout=10
uv run ruff check ghrah-kernel && uv run ruff format --check ghrah-kernel
```

机制回归需要真实子进程与 Unix 本地 Pipe；在限制子进程或本地 IPC 的沙箱里会失败，这不是回归。

## 当前状态

- 已迁入：内核、监督者、消息基座与 97 项机制回归。
- 未迁入：机制实验仓的 demo／verify 场景装配与 mock 受管入口；它们保留为可复现证据，不进入产品线。
