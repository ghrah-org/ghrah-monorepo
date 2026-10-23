# ghrah-observer-webui

ghrah 观察端 WebUI 与 VS Code 扩展（Vue 3 + Vite + pnpm workspace）。

## 包结构

- `packages/protocol` — `@ghrah/protocol`，TS 协议类型/校验（zod schema + snapshot 测试）
- `packages/observer-core` — `@ghrah/observer-core`，WS 客户端 + Pinia stores + 投影逻辑
- `packages/observer-web` — `@ghrah/observer-web`，Vue 3 Web 前端（私有，不发布）
- `packages/mock-server` — `@ghrah/mock-server`，协议层 mock 服务器（开发/测试用）
- `vscode-extension` — VS Code 扩展（Marketplace 发布）

## 环境要求

- Node.js `^20.19.0 || >=22.12.0`
- pnpm `>=9.0.0`

## 构建与测试（在 monorepo 根目录执行）

```sh
pnpm install        # 安装整个 workspace（单一 pnpm-lock.yaml 在仓库根）

# 构建发布面（observer-web / observer-core 依赖 protocol 的 dist，须先构建）
pnpm --filter @ghrah/protocol build
pnpm --filter @ghrah/observer-core build

# 测试
pnpm --filter @ghrah/protocol test
pnpm --filter @ghrah/observer-core test
pnpm -r test        # 全部包（含 mock-server、observer-web）

# 类型检查（递归所有包）
pnpm type-check
pnpm lint

# 完整构建（含 Web、mock-server 与 VS Code 扩展）
pnpm -r build
```

## 本目录下的 scripts

```sh
pnpm dev            # ./scripts/dev.sh — 起 observer-web vite dev server
pnpm build          # ./scripts/build.sh
pnpm lint           # i18n / ui-tokens 检查 + biome
```

## IDE

VS Code + [Vue (Official)](https://marketplace.visualstudio.com/items?itemName=Vue.volar) 扩展。`.vue` 的类型检查由 `vue-tsc` 承担（`pnpm type-check`）。

## Agent 通信验证

打开项目的 Agent 总览，展开“Agent 通信验证”。源 agent 须已注册 `send_message`
能力；同步验证前须启动目标 cluster。同一项目内可选择不同 cluster 的 agent，
以稳定 agent ID 寻址，实际发送由源 agent 的能力执行和项目 MessageManager 完成。

- **A → B → C**：添加 A → B、B → C，选择同步回复与顺序链路。
- **一对多**：添加 A → B、A → C，选择并行链路；各链路创建独立持久消息。
- **异步**：回执仅证明源 agent 已提交出站消息，接收方后续执行请查看 ActionChain。
- **失败/回执未知**：顺序验证停止后续链路；超时可能仍在服务端执行，再次运行前先检查历史。

顺序验证逐跳发送输入框中的正文，不自动转发上一跳回复。面板展示本次命令的临时回执，
消息与执行历史仍以 Core SQLite 为权威，可在各 agent 的 ActionChain 查看；重新运行会新建请求。

## Agent 能力管理

在项目 Agent 总览中点击对应 Agent 的“能力管理”，或选中右侧 Agent 后从“…”菜单打开。
窗口通过 `get_agent_info` 查询当前实例的已注册能力和后端可注册类型；注册时从下拉框选择能力类型（如
`send_message`）和 JSON 构造参数（无参数时为 `{}`），成功后重新查询实例确认。

运行时注册只更新当前实例，不写入 Agent manifest，不承诺重启后保留，也不自动重试
历史阻塞消息。需要重启后保留的能力须在 Agent manifest 中配置。停止状态下展示的是
配置定义，须显式启动 Agent 后才能进行运行时注册。

本次接入的功能、通信路径、模式语义及已知限制见 [MessageManager 前端接入说明](docs/message-manager-webui.md)。
