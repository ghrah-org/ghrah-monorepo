# Observer mock server

协议层开发服务，独立运行 WS 与 HTTP，不需要启动 Core / Subject，也不会调用真实模型。
数据只保存在进程内，重启即清空。WS 的实体与 HTTP 的能力管理共享项目、Agent 身份及运行状态。

## 启动

在 monorepo 根目录，使用 Node >= 26：

```sh
pnpm dev --mock
```

入口会先构建所有 workspace 库，再启动 SPA、WS `4113` 和 HTTP `4114`。
HTTP `/health` 可探活。页面通过两个显式环境变量连接这两个服务，避免把 HTTP 请求发到 WS 端口。

也可以单独启动：

```sh
pnpm build:deps
pnpm --filter @ghrah/mock-server start --port 4113 --http-port 4114 --scenario demo
```

`demo` 提供四个 Agent 和四个 Room，约 13 秒出现 HITL 审批；`plugins` 提供插件协商及任务归因种子。
四个演示 Agent 显式带有 `send_message`，可直接在 Agent overview 验证彼此通信和启停。
`--help` 查看模拟链、投递推进、重连回放与包目录开关。

## 可交互流程

- 项目与 Room：创建、成员管理、定向消息、日志、归档、恢复、级联删除。
- 稳定 Agent：在 Agent overview 新建实体，manifest 填 `mock.agent`；显式选择立即启动或稍后启动。
  这个固定模板提供 `send_message`；未知模板返回 `manifest_not_found`。
- Agent 管理 HTTP：列表 / 详情、草稿创建、规格 PATCH、期望状态 PUT、带版本校验的 DELETE。
  与 WS 实体共用写入路径和项目版本；保存运行中规格只影响下一次启动。
  HTTP 创建契约没有 manifest 字段，因此可创建停止草稿；缺少模板的启动请求按真实后端返回 `agent_manifest_required`。
- 实体启停：身份和历史保留；重新启动分配新的 incarnation；旧 revision / incarnation 会拒绝写入。
- Session / Branch：创建、激活、历史读取与生命周期；停止后仍可读取已提交历史。
- 人工消息及 Agent 通信验证：回复为 `[mock:<name>] <content>`；消息收件箱、发件箱、投递与 ActionChain 节点关联。
  同步和异步回执遵循页面的不同格式，模拟执行均在内存中立即完成。
- 能力管理：HTTP 读树、授予、撤销、绑定、解绑、CAS 冲突及审计；写入后经 WS 发送失效通知，观察端重新读取权威视图。
  运行时工具快照按 incarnation 重建，Agent 重启后清除待生效标记。
- 模型选择：只读端点目录提供 `mock-model`；固定模板管理模型绑定。
- 重连：事件 seq 增量回放，项目、Room、Agent、Session / Branch 历史重新同步。

## 验证

```sh
pnpm --filter @ghrah/observer-web exec playwright install chromium
pnpm test:mock
```

根命令依次构建依赖、执行真实 ObserverClient + Pinia stores 的 WS / HTTP smoke，
再启动生产构建跑浏览器创建实体、启停、Session、消息、离线历史和 Agent 通信验证。
CI 同时执行该 smoke 与浏览器流程，并保留现有布局护栏。

## 边界

这是确定性联调替身；通过 mock 不能证明真实模型、工具或 Core 持久化可用。
当前实体邮箱只收录直接消息，Room 投递继续通过 Room 日志读取。
当前实体弹窗的系统提示词编辑仍是预览；持久规格写入由 Agent 管理 HTTP 接口验证。
模拟投递立即完成，因此 retry / resume 对已完成投递返回 `invalid_delivery_state`。
完整 agentconf 配置 CRUD、manifest 编辑、工作区 / 持久化管理及任务写入工作流尚未模拟；
未实现的命令或路由会明确报错。`versions_read` 等未实现的能力标志为 false。
旧运行时能力注册返回与 Subject 一致的 `legacy_ability_registration_disabled`，不会伪装成注册成功。

通过库创建 HTTP server 时可传 `state` 与 WS 复用身份和运行状态；不传时使用独立能力 fixture，供局部契约测试。
