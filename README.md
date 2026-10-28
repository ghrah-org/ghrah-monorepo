# ghrah

多智能体运行时（multi-agent runtime）：认知（Core）、效果（Subject）、观察（Observer）三层分离的 agent 基础设施。

## 这是什么

ghrah 为持久化、可观察、受权限治理的多 agent 协作提供运行时：

- **Core**（`ghrah-core`）— agent 认知半区：agent loop、LLM 调用、上下文管理（ActionChain 分支/Session）、能力（Ability）执行、per-agent 注入式装配。
- **Subject**（`ghrah-subject`）— agent 效果半区（effect host）：工作区与沙箱、权限/HITL 审批、Project/Room/Task 管理、manifest store、事件持久化与恢复（reconcile）。运行时基于 [Ouroboros](https://pypi.org/project/cordis-ouroboros/) Unit 内核，**内嵌 Core 单进程运行**。
- **Protocol**（`ghrah-protocol`）— 跨进程协议真相源：Envelope/CommandResult/Event 与各类 payload schema（Python 侧）；TS 侧 `@ghrah/protocol` 对齐演进。
- **Observer WebUI**（`ghrah-observer-webui`）— CQRS 读模型：WebSocket 订阅 Subject 事件流，提供聊天、ActionChain 链视图、HITL 审批、Project/Room 管理界面（Vue 3 + Vite）。

```
Observer WebUI (vite :5173) ──WS──> ghrah-subject (:4112, 内嵌 CoreUnit)
                                       └── Subject 落盘：subject.db / ghrah.db / workspace
```

## 现状

- 版本 `0.2.0a0`（快速成型期，接口可能破坏性变更）；Core Unit 化与单进程化已完成，Room/Project/Task 域已落地，恢复（reconcile）体系在建。
- Python ≥ 3.11 + [uv](https://docs.astral.sh/uv/)；Node.js ≥ 22 + pnpm ≥ 9（pnpm 11 需要 Node ≥ 22 的 `node:sqlite`）。
- 许可 Apache-2.0（REUSE 标注补全进行中）。

模型连接通过 agentconf 0.3 模型端点解析；Agent 配置必须显式声明 `endpoint_name`。旧配置转换及正式依赖使用边界见 [模型端点与配置迁移](ghrah-core/docs/model-endpoints.md)。

## 快速开始

```sh
# 0) 依赖（仓库根执行）
uv sync --all-packages            # Python 全量（单一 uv.lock 在根）
pnpm install                      # TS 全量（单一 pnpm-lock.yaml 在根）

# 1) 一键启动全栈（根目录）
python3 scripts/start_all.py      # Subject :4112 + WebUI dev server :5173
# 常用变体：
#   --no-webui            仅后端
#   --no-webui --open     后端 + 自动开浏览器
#   --webui-port 5273     换端口
```

启动后访问 `http://localhost:5173`（WebUI）。Ctrl+C 一键退出（进程组整组终止，退出时清理注入的 env 文件）。

只跑前端 demo（无需后端，协议级 mock）：

```sh
cd ghrah-observer-webui && pnpm dev -- --mock --scenario demo
```

## 仓库结构

源码组合的草案入口为根目录 `ghrah.toml`，各包的 `component.toml` 描述交付身份与入口。
安装独立工具链后，可在本仓执行 `ghrah check` 或 `ghrah metadata --json` 做离线校验。
当前仅校验静态组合；不会启动组件或授予 Agent 权限，现有开发启动方式仍按上文执行。

| 目录 | 语言 | 说明 | 发布 |
| --- | --- | --- | --- |
| `ghrah-core/` | Python | agent 认知核心（loop/上下文/能力/装配） | PyPI `ghrah-core` |
| `ghrah-plugin/` | Python | 插件基础设施（plugin spec 契约/发现/注册/协商/离线验证） | 未发布（`publish.yml` 白名单未纳入） |
| `ghrah-protocol/` | Python | 协议类型与消息定义 | PyPI `ghrah-protocol` |
| `ghrah-subject/` | Python | 效果宿主（工作区/权限/HITL/Project/Room/恢复） | PyPI `ghrah-subject` |
| `ghrah-taskstore/` | Python | 任务归因内核（**可选组件**、首个接入插件系统的插件；tasks/claims/evidence 三表权威 + checker 扩展点宿主） | 未发布（`publish.yml` 白名单未纳入） |
| `ghrah-scheme/` | Python | 内部 Scheme 控制平面原型：类型化动作、路由引用、接纳与有界生命周期；现有 Core／Subject 尚未迁移 | 未发布（自用，未纳入发布白名单） |
| `ghrah-observer-webui/` | TypeScript | 观察端（`@ghrah/protocol`、`@ghrah/observer-core` 发 npm；`observer-web` 前端、`mock-server` 私有；`vscode-extension` 发 Marketplace） | npm / Marketplace |
| `scripts/` | Python | 全栈启动入口（`start_all.py`） | — |

## 测试与质量

```sh
uv run pytest ghrah-plugin ghrah-protocol ghrah-core ghrah-subject ghrah-taskstore ghrah-scheme  # Python 六包
uv run ruff check . && uv run ruff format --check .     # Python lint/format
pnpm -r test && pnpm type-check                          # TS 测试 + 类型检查（webui 内）
pnpm lint                                                # TS biome + i18n/ui-token 门禁（webui 内）
```

发布前手工运行 GitHub Actions 的 `release-precheck`。它只测试、构建、审计并上传临时
artifact，不创建 tag、GitHub Release，也不向 registry 发布。

## 发布

各发布面使用独立版本和独立 tag：

- `py/ghrah-core@<PEP440>`、`py/ghrah-protocol@<PEP440>`、`py/ghrah-subject@<PEP440>` → PyPI；
- `npm/ghrah-protocol@<SemVer>`、`npm/ghrah-observer-core@<SemVer>` → npm；
- `vscode/ghrah-vscode-extension@<SemVer>` → VS Code Marketplace。

例如同一 alpha 代际在 Python 使用 `0.2.0a0`，在 npm/VSIX 使用 `0.2.0-alpha.0`。
`publish.yml` 会在上传前验证 tag 版本与目标包清单完全一致。GitHub Release 由独立的
`github-release` workflow 针对已经存在的展示 tag 显式创建。

本地完整预演（不会发布）：

```sh
uv run python scripts/release_dry_run.py --all --out-dir /tmp/ghrah-release
```

## 约定

开发/协作规范见 [AGENTS.md](./AGENTS.md)（工具链、代码规范、plans 目录约定）。

## LICENSE

Apache-2.0
