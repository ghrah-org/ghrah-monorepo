# ghrah monorepo

多智能体运行时 monorepo。项目定位与快速开始见根 [README](./README.md)；本文是开发/协作规范。

## 仓库结构

- `ghrah-core/` — Python 核心包（PyPI: ghrah-core）；文件 Scheme 切片为显式 `scheme` extra，默认安装不依赖尚未发布的 ghrah-scheme，当前只在 workspace／本地 wheel 组合验证。
- `ghrah-kernel/` — Python 应用微内核包（14 个基础入口、跨进程句柄／调用事实、结构化消息 SDK、期望状态监督者；受信启动、静态身份/Scope、命名服务及受限文本 ActorAgent/Context 宿主已接入；Core 为显式 `agent` extra，生产工具/内容/MCP 待合流；**未纳入 `publish.yml` 白名单，暂无发布通道**）
- `ghrah-plugin/` — Python 插件基础设施包（plugin spec 契约/发现/注册/协商/离线验证；**未纳入 `publish.yml` 白名单，暂无发布通道**）
- `ghrah-protocol/` — Python 协议包（PyPI: ghrah-protocol）
- `ghrah-subject/` — Python subject 包（PyPI: ghrah-subject）
- `ghrah-taskstore/` — Python 任务归因内核包（**可选组件**、首个接入插件系统的插件；tasks/claims/evidence 三表权威 + checker 扩展点宿主；**未纳入 `publish.yml` 白名单，暂无发布通道**）
- `ghrah-scheme/` — Python Scheme 控制平面原型（monorepo 自用；类型化契约、五个控制原语与有界生命周期；既有运行时尚未迁移，未纳入发布白名单）
- `ghrah-observer-webui/` — TS 观察端（pnpm workspace: packages/* + vscode-extension）

## 架构原则（SSOT / CQRS）

分布式下组件不共享内存、只共享消息，因此每一个被多方共享的"事实"必须有**唯一的权威来源**；其余副本只能是**派生**（生成 / 校验 / 读通 / 缓存），且必须**单向、可重建**。

- **单一权威**：写同一份逻辑状态或定义的路径只能有一条。禁止任何形式的第二写路径（尤其"事件驱动落库"式投影写回）。
- **派生副本**：不得独立写；必须能从权威完整重建；同步方向单向且幂等。
- **判断标准**：一份副本若能被独立写、或不能从权威重建，它就不是副本，而是第二权威（违规）。

### 本仓的 CQRS 模型（现状）

- **写侧权威**：Core `ContextManager` 的 sqlite（ActionChain / session / branch / messages 的唯一真相源）。
- **读侧投影**：
  - Subject `ActionChainLedger` 为**只读投影**，经同文件 WAL 双连接直读 Core sqlite，**无写侧**；
  - Observer stores 由 `core:*` 事件驱动实时更新，并在（重）连接时经 `get_chain_history` 从权威**重建**——事件是实时投影，读通量是重建路径。
- **契约平面**：`ghrah-protocol` 是 wire 契约的唯一权威；TS `packages/protocol` 与其对齐（双侧同步 + 一致性测试），不得单侧手改。
- **身份/请求关联**：HITL pending 的权威是 Core `HITLPromiseRegistry`（`promise_id`）；Observer 只持 `promise_id` 引用，不复制真相。

### 新增共享状态/事件时的检查清单

1. 这份事实的唯一权威在哪（谁写）？
2. 其他出现的地方是生成 / 校验 / 读通 / 缓存？能否被独立写？
3. 能否从权威重建？重建指令是什么？
4. 是否引入了第二条写路径？
5. 跨机部署后此方案是否仍成立？（读通式投影依赖共享存储边界；跨机须改为"权威 + 单向复制/CDC + 可重建投影"）

## 工具链

- Python：uv（单一 `uv.lock` 在根；**成员内禁止 `uv lock`**）
- Web：pnpm（单一 `pnpm-workspace.yaml` / `pnpm-lock.yaml` 在仓库根）
- Node 版本：**须 ≥ 26**（pnpm 11 依赖 `node:sqlite`，Node 20 下任何 pnpm 命令直接崩；`nvm use 26`）

```sh
uv sync --all-packages      # Python 全量安装（含 dev 组另加 --group dev）
pnpm install                # TS 全量安装（webui 目录或根均可，workspace 在根）
```

## 代码规范

### Python

- lint/format：`uv run ruff check . && uv run ruff format --check .`（规则 `E,F,I,N,W,UP`，行宽 100，target py315）。提交前必须全绿。
- 类型：现代语法（`str | None`，禁 `Optional`）；公开函数带类型标注与中文 docstring。
- 测试：pytest（`asyncio_mode = "auto"`）；测试文件与被测模块同构放在 `tests/`。
- 命令一律 `uv run <cmd>`（解析到根 workspace 环境），勿用系统 python。

### TypeScript（ghrah-observer-webui）

- lint：`pnpm lint` = **oxlint（先跑高性能通用规则）→ eslint（Vue/TS 专属；经 `eslint-plugin-oxlint` 按 `.oxlintrc.json` 关闭重复规则）→ stylelint（CSS 正确性）→ `check-style-governance`（样式 ratchet 基线）→ prettier --check → i18n/ui-token 门禁**。fix：`pnpm lint:fix`；format：`pnpm format`。
- 工具链职责：oxlint 管通用 JS/TS 规则；ESLint 管 Vue SFC/template 与 typescript-eslint 规则；stylelint 只管 CSS 的“对不对”（排版交给 prettier）；prettier 管格式。规则配置在 `ghrah-observer-webui/`（`eslint.config.js` / `.oxlintrc.json` / `stylelint.config.mjs` / `.prettierrc.json`），依赖装在 monorepo 根。
- i18n：用户可见文案 **en 源 + zh-CN 双侧同步**（`check-i18n` 防中文残留）；新增组件文案先加 en 再补 zh。
- 样式分层：`src/styles/layers.css` 声明 `@layer reset, tokens, base, components, utilities`；`reset/tokens/base` 已拆分，`style.css` 整体位于 `components` 层。新增样式先判断归属：**值** → `tokens.css`；**元素默认** → `base.css`；**组件私有** → `<style scoped>`；其余才进 `style.css`。
- 样式默认用 UnoCSS 原子类；颜色只用语义 token，禁止调色板类（`bg-gray-800`、`text-blue-500`）。
- 禁止裸 hex/rgba、px 字号、任意值工具类（`w-[...]`）、`!important`、组件级 `:root.dark` 与模板 `dark:`；存量按基线只降不升，不得新增。
- 级联顺序由 `@layer` 决定，不要靠 import 顺序或堆特异性取胜；需要覆盖组件样式时用工具类，而不是加 `!important`。
- 样式门禁：`pnpm lint` 内含 `check-style-governance`（ratchet，基线 `style-baseline.json`）。CSS 体积可在本地构建后用 `pnpm lint:css-budget` 检查（基线 `style-budget.json`，gzip 为主），不作为 CI 门禁。清理存量后跑对应 `--update` 下调基线。
- 布局/样式改动须自查 1600 / 1024 两种宽度 × 100% / 110% 字号；涉及侧边栏的还要覆盖“展开 / 收起 / 两侧都收起”。
- 构建：`packages/protocol`、`packages/plugin-registry`、`packages/observer-core`、`packages/mock-server` 的 `exports` 指向 `dist/`；改动后须跑 `pnpm build:deps` 重建（monorepo 根或 webui 目录均可），否则 dist 陈旧会让测试与 type-check 结果分裂。

### 通用

- **零隐式行为**：新功能必须显式声明（配置/Filter/白名单驱动），不做"顺便帮用户做了"的隐式默认。
- 换行符统一 **LF**（根 `.gitattributes` 已置 `* text=auto eol=lf`）；不得提交 CRLF，避免跨平台 diff 噪声与脚本解析问题。
- 不做顺手的无关重构/重命名/重排版；改动范围最小化。
- 新增依赖须入对应 workspace 清单，禁止本地 path 依赖混入锁文件。

## 测试与验收

```sh
# Python 七包（根目录执行；成员目录跑测试须 env -u PYTHONPATH uv run pytest -q）
uv run pytest ghrah-plugin ghrah-protocol ghrah-core ghrah-subject ghrah-taskstore ghrah-scheme ghrah-kernel

# strict 类型门禁（两个包各自执行）
cd ghrah-protocol && MYPYPATH=src uv run mypy --explicit-package-bases -p ghrah.protocol
cd ../ghrah-scheme && MYPYPATH=src uv run mypy --explicit-package-bases -p ghrah.scheme

# TS（monorepo 根目录执行）
pnpm build:deps        # 构建 workspace 库的 dist（测试按 exports 消费 dist）
pnpm test              # 全部包测试（等价 pnpm -r test）
pnpm type-check        # 根级递归；vue-tsc 在 observer-web 包级
pnpm lint

# 观察端浏览器测试（首次需 pnpm --filter @ghrah/observer-web exec playwright install chromium）
pnpm --filter @ghrah/observer-web test:guards   # 断言型护栏：盒模型/层顺序/横向溢出/侧边栏，CI 会跑
pnpm --filter @ghrah/observer-web test:visual   # 像素基线：4 界面 × 2 宽度 × 2 字号 + 侧边栏三态
```

- 视觉基线绑定生成机器的字体渲染：`test:visual` 只在本机或固定镜像内跑，跨发行版会整片误报；更新基线用 `test:visual:update` 并逐张确认差异。**像素基线不入库**（`e2e/visual.spec.ts-snapshots/` 已 gitignore），新克隆先跑一次 `test:visual:update` 生成，CI 只跑 `test:guards` 断言。
- 删除型重构的验证顺序：**先 `rm` 工作树文件 → 再 pytest → 最后 `git add`**（仅 `git add` 前文件仍在磁盘，会被测试收集，造成"已删仍绿"假象）。
- 收编跨包漂移的修复，须跑双侧测试（协议改动 → Python + TS 两侧）。

## 提交约定

- Conventional Commits，中文描述：`feat(subject): …` / `fix(core)!…`（破坏性加 `!`）；范围用包短名（core/plugin/protocol/subject/taskstore/scheme/observer/webui/ci/docs）。

## plans 目录约定

所有设计/计划文档落盘到根 **`plans/`**，该目录被 `.gitignore` 整体忽略——**本地专用，不提交**。

- 分类：`plans/done/`（已完成归档）、`plans/todo/`（待办）、`plans/planning/`（进行中设计）、`plans/shelved/`（搁置）、`plans/<repo>/`（仓专属主题，如 `plans/ghrah-core/`）。
- **不要在项目代码中引用plan文档中的节点，标题，锚点，阶段**

