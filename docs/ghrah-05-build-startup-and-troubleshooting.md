# 构建、启动、日志与排障

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-OPS-005 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `a742fcf`（2026-11-23）；命令面 `ghrah-toolchain` 0.2.0 |
| 读者 | 首次搭环境的人、部署到新机器的运维、排查"起不来／起错端口／日志刷屏／数据在哪"的开发者 |
| 取代 | `构建启动与日志链路说明.md`、`启动命令.txt`（均已归档至 `docs/archive/`） |
| 相关文档 | `ghrah-01-ability-design-and-implementation.md`（能力装配）、`ghrah-03-drive-loop.md`（循环与压缩）、`ghrah-06-code-map.md`（代码地图） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 2.0 | 2026-11-23 | 按 `ghrah-toolchain` 现行命令面整篇重写：build 产物树、distro 安装域、dev 快环、Subject 20 个 unit、实例级数据落盘、启动授权与日志；删除已不存在的仓内全栈脚本及其行号 |

## 1. 前置与命令面

### 1.1 前置

| 依赖 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.15.1 及以上 | 工具链自身与被部署发行版各自管理解释器；源码 `.python-version` 决定后者的版本 |
| uv | 可执行 | Python 构建、venv、依赖安装；源码安装链需要 |
| Node / pnpm | Node ≥ 26、pnpm 可执行 | 前端构建；pnpm 11 依赖 `node:sqlite` |
| git | 可执行 | 从源码仓安装／更新；已装实例的 start/stop/status/logs 不需要 git |
| `ghrah` | 已安装到 PATH | 推荐 `uv tool install --editable <ghrah-toolchain>`；写 `~/.ghrah` 的命令不要用 `uv run ghrah`（受限环境下只能写工作区） |

首检用 `ghrah self doctor`（打印 git/uv/pnpm、引导菜单、各实例的安装代、`current`、落后情况与端口）或 `ghrah check`（只静态校验 `ghrah.toml`，不启动任何组件）。安装域根默认 `~/.ghrah`，可用 `GHRAH_HOME` 重定向（`layout.py:ghrah_root`）。

### 1.2 能力边界

`ghrah` 是独立于 monorepo 的工具链 CLI（兄弟仓 `ghrah-toolchain`，入口 `ghrah_toolchain.cli:main`）。四类命令职责不重叠：

| 命令面 | 职责 | 是否写安装域 | 证据 |
| --- | --- | --- | --- |
| `ghrah build` | 从固定 commit 构建产物树（wheels／tarballs／observer-web／catalog／manifest），只做构建与黑盒验收 | 否，只写 `--out` | `build.py:build_artifacts`（`:184-256`） |
| `ghrah distro install` / `update` | 接安装域：建 `versions/` 基座、物化 `generations/` 组合代、门禁→快照→原子重指 `current` | 是 | `runtime_ref.py:RuntimeRefManager.ensure`（`:315-531`） |
| `ghrah dev run/status/stop/reset` | 工作树快环：从本地工作树增量构建并在 `.dev/` 起服，不动发布代 | 只写 `<实例>/.dev/` | `dev.py:1-27`、`:963-981` |
| `ghrah distro start/stop/restart/status/logs/config/list/default` | 发布代的生命周期、日志与实例配置 | 是（会话记录／配置） | `commands/user.py:110-136`、`instances.py` |

快捷命令（`ghrah install`、`ghrah start`、`ghrah ps` 等）与 `distro` 分组共用处理函数；`create`／`boot`／`ps` 是别名（`commands/user.py:113-127`）。`ghrah ability` 是目标发行版 core 的包入口，工具链只解析解释器／存储根并透传，不在工具链内实现包逻辑（`commands/ability.py`）。

`build` 的产物树结构固定（`build.py:224-256`）：

```text
<out>/                       # 默认 <repo>/dist/toolchain/<commit 前 7 位>/
├─ wheels/*.whl              # 六个 Python 包：kernel/core/protocol/plugin/taskstore/subject
├─ wheels/pins.txt           # 第三方依赖闭包（安装期零解析）
├─ tarballs/*.tgz            # protocol / plugin-registry / observer-core
├─ observer-web/             # dist + public + locales + package.json
├─ catalog.json              # 代内目录（新代；旧源码无此文件）
├─ manifest.json             # 文件清单与 sha256
└─ BOOTSTRAP.toml            # source_commit / built_at / builder
```

验收在全新临时 venv 中装 wheels + pins、import 六包、确认宿主入口存在（`build.py:verify_artifacts` `:497-536`）；`--no-verify` 才跳过。构建目录非空会直接拒绝（"构建结果目录已存在且非空"）。

### 1.3 仓内入口

monorepo 不再持有全栈启动脚本；仓内只剩两条转交 `ghrah` 的入口：

| 入口 | 行为 | 位置 |
| --- | --- | --- |
| `scripts/dev-run.ps1` | 先注入 `GHRAH_SUBJECT_ABILITY_GRANT_POLICY`，再在 `../ghrah-toolchain` 下执行 `ghrah dev run --distro <实例>`；支持 `-Distro`、`-NoBrowser`；找不到工具链直接 `throw` | `scripts/dev-run.ps1:28-62` |
| `ghrah-observer-webui/scripts/dev.sh` | 无 `--mock` 时 `exec ghrah dev run`（默认 `--no-browser`，`--open` 才打开浏览器）；`--mock` 起协议 mock server（`4113`）＋ SPA，不依赖 Subject | `ghrah-observer-webui/scripts/dev.sh:11-84` |

`dev.sh` 在 PATH 上没有 `ghrah` 时显式失败并提示安装工具链，不会退回旧的仓内启动方式。

## 2. 实例布局与启动

### 2.1 目录

一个具名实例就是 `~/.ghrah/<实例>/`；以下路径由 `DistroLayout`（`layout.py:238-287`）与安装流程共同定义：

```text
~/.ghrah/
├─ boot.toml                    # 引导菜单：default / fallback / last_good，值为 "<实例>/<版本>"
├─ config.toml                  # CLI 偏好：self.output、self.log_tail
├─ .cache/repositories/<摘要>/repo.git   # 远程来源的裸仓缓存（清理不影响已装代）
└─ <实例>/
   ├─ distro.toml               # 实例声明：repo/ref/plugins/abilities/runtime/data
   ├─ current -> generations/<gen-id>     # 当前引导目标（只指组合代）
   ├─ last-good -> generations/<gen-id>   # 上次成功启动的组合代
   ├─ versions/<版本>/          # 基座产物树（wheels/tarballs/observer-web/catalog/manifest/BOOTSTRAP）
   ├─ generations/<gen-id>/     # GENERATION.toml + catalog.json + env/.venv + env/node
   ├─ data/                     # 数据盘：subject.db / taskstore.sqlite3 / manifests / projects / workspace
   ├─ logs/<id>.log             # 每次后台启动一个日志文件
   ├─ ability/<ns>/<name>/<版本>/  # v1 Ability 包安装根（发行版级共享）
   ├─ snapshots/                # update 前的数据快照（默认保留 3 份）
   ├─ .dev/                     # dev 快环私有命名空间
   └─ .session.json             # 后台会话记录：schema/token/pid/created/state/generation/error
```

`current` 与 `last-good` 都必须是 `generations/` 下的单层目标；指向 `versions/` 会被 fail-closed 拒绝（`runtime_ref.py:_repoint_link` `:152-188`、`_entry_kind` `:726-741`）。`versions/` 只是基座产物库与显示面，不是引导目标。

### 2.2 端口与地址

| 项 | 默认 | 覆盖方式 | 证据 |
| --- | --- | --- | --- |
| Subject 端口 | `4112` | 首装 `--subject-port`；之后 `ghrah distro config set <实例> runtime.subject_port <n>` | `layout.py:126`、`lifecycle.py:41` |
| 前端端口 | `5173` | 首装 `--webui-port`；之后 `runtime.webui_port` | `layout.py:127`、`lifecycle.py:42` |
| 绑定地址 | `127.0.0.1` | `GHRAH_BOOT_HOST`（`dev.py:672`；后台 start 的 `StackConfig` 同源） | `lifecycle.py:43` |
| 管理面开关 | `true` | `GHRAH_SUBJECT_OBSERVER_HTTP_API_ENABLED` | `config.py:825-828` |
| CORS 白名单 | `5173`、`5180` | `GHRAH_SUBJECT_OBSERVER_CORS_ORIGINS`；工具链会把前端端口对应的 origin 追加进去 | `config.py:428-431`、`boot.py:_merge_cors_origins` `:371-387` |

端口配置只在首装时可给参数；已有实例的端口必须走 `config set`，且要求实例已停止（`cli.py:173-176`、`README`「配置实例与 CLI」）。后端与前端端口不能相同（`cli.py:181-182`）。`distro.toml` 的 `[runtime.env]` 只接受 `GHRAH_SUBJECT_` 前缀字符串；与工具链管理的数据路径／端口键冲突会报 "runtime.env 与工具链管理的数据路径或端口配置冲突"（`boot.py:353-359`）。

### 2.3 启动链

首装用 `ghrah distro install <实例> --repo <monorepo> --ref commit:<sha>`（完成后不自动启动）；`ghrah distro start <实例>` 默认立即返回（`instances.py:start_instance` `:173-244`）：

1. `resolve_boot_target` 解析目标：显式实例名 > `boot.toml` 默认项；版本取 `current` 指向（`boot.py:225-290`）。
2. 加实例锁，生成随机 `token`，打开 `logs/<token>.log`（追加模式）。
3. 后台 spawn 监督者 `python -m ghrah_toolchain.supervisor <实例> <token>`；Windows 用 `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` 脱离当前终端，POSIX 用 `start_new_session`。
4. 写 `.session.json`：`state="starting"`、`generation`、`pid`、`created`；Windows 上监督者随后经 `_claim_record` 把记录归属到真正执行监督逻辑的进程。
5. 监督者 `serve_instance` → `run_boot` → `BootStack.start`：按代内 `catalog.host.command[0]` 拉起宿主进程（`current/env/.venv/<entry>`），注入数据面 env 与端口。
6. 轮询 `http://127.0.0.1:<port>/health`，最多 10 秒（`lifecycle.py:325-358`、`health.py:21-47`）。通过后 `on_ready` 把状态改为 `running`，并更新 `last-good`／`boot.toml`。
7. 前端页面：后台 start 默认静态 serve `versions/<base>/observer-web/dist`（stdlib HTTP server），`index.html` 注入 `window.__GHRAH_SUBJECT_WS_URL__` 与 `Cache-Control: no-store`（`lifecycle.py:484-505`）。`--webui-dev` 才起 `pnpm dev --strictPort`，且必须 `--foreground`（`cli.py:397-399`）。
8. `ghrah distro stop` 向监督者发停止请求，等待其收束子进程并复核端口释放；`--timeout` 默认 15 秒。

诊断用 `ghrah start <实例> --foreground`（占当前终端，日志直出）；`--ref last-good` 与 `--restore-snapshot` 也要求 `--foreground`，后者会丢快照之后的写入（`cli.py:390-401`、`boot.py:_perform_snapshot_restore` `:546-567`）。

### 2.4 状态三态

工具链只承诺三种可判定的运行态，`status` 的语义全部来自 `.session.json` 与进程存活探测（`instances.py:117-137`）：

| 状态 | 含义 | 判据 |
| --- | --- | --- |
| `starting` | **提交**：已提交后台启动，尚未确认健康检查通过 | 记录已写、监督者进程存活；不代表服务可用 |
| `running` | **历史结论**：启动时健康检查曾通过 | 监督者在 `on_ready` 时写入；此后不自动重探 |
| `failed` | **失败**：启动异常或子进程异常退出 | 监督者捕获异常写入 `error`，或子进程退出码非 0 |

`stopped` 是空闲边界（无会话记录或监督者已退出且无端口占用），`legacy` 是旧 `.boot.pid` 会话边界（无 `.session.json` 但存在旧 pidfile）。

`ghrah distro status` 只读状态，不 fetch、不构建、不启动，也不探测服务：输出里的前端／后端地址来自 `distro.toml`，并显式标注 "以上地址来自配置，本次未检查服务是否可访问"（`cli.py:504-539`，字段 `health="not-probed"`）。因此 `running` 不能当作"此刻健康"使用；要确认可用性必须自己请求 `/api/v1/health`。

## 3. dev 快环

### 3.1 前置

`ghrah dev` 从**工作树**（含未提交改动）构建，但不改变发布态身份：

- 实例必须已安装并重指过 `current`（`install` 的产物）；`resolve_boot_target` 是唯一解析实现（`dev.py:320-340`）。
- `distro.toml` 的 `[distro.repo].path` 必须指向有效的 git 工作树；远程 `url` 来源没有本地工作树，dev 直接失败（`dev.py:328-339`）。
- 省略 `--distro` 时按 `boot.toml` 的 `default` → `last_good` → `fallback` 取第一个**确实已安装**的实例，悬空项跳过并告警；全部不可用才报错（`dev.py:_pick_distro` `:276-317`）。
- dev 只借实例的端口／数据盘／能力根与 `.dev/` 命名空间，**不重指 `current`、不写 `last-good`、不改 `boot.toml`**（`dev.py:5-27`）。

### 3.2 命令

| 命令 | 行为 |
| --- | --- |
| `ghrah dev status` | 对比工作树与上次 dev 构建指纹，报"全量／增量（哪些包）"及 dev 会话是否在跑（`dev.py:776-809`） |
| `ghrah dev run` | 构建后**前台**起服并监督；已有 dev 会话先停、等端口释放再起；同一对端口上的发布态会话也会被先停掉（`dev.py:812-910`、`:622-631`） |
| `ghrah dev run --no-restart` | 只构建／升级，不起服 |
| `ghrah dev run --full` | 忽略指纹强制全量重建 |
| `ghrah dev run --no-browser` | 起服但不自动打开浏览器 |
| `ghrah dev stop` | 停止 dev 会话并收束整棵进程树（`:913-919`）；停不掉时抛错，不谎报成功 |
| `ghrah dev reset` | 删除整个 `.dev/`（含产物与 venv），下次必然全量（`:922-928`） |

`run`／`stop` 的别名是 `up`／`down`（`cli.py:783-798`）。`dev run` 是幂等的"确保按当前工作树跑起来"：不会把"已在运行"当成成功空操作。

### 3.3 增量与指纹

指纹的维度是**包**（`dev.py:100-211`）：

- 六个 Python 包各算一个指纹单元：`ghrah-kernel`、`ghrah-core`、`ghrah-protocol`、`ghrah-plugin`、`ghrah-taskstore`、`ghrah-subject`；各自 wheel 单独重编；
- 依赖闭包算一个单元：`.python-version`、`pyproject.toml`、`uv.lock`、`pnpm-lock.yaml`、`pnpm-workspace.yaml`；
- 前端 `ghrah-observer-webui` 算一个单元。

只改某个 Python 包时，只重编该包 wheel，并以 `uv pip install --force-reinstall --no-deps` 覆盖进 dev venv（`dev.py:_upgrade_dev` `:478-490`）；依赖或前端变动走全量重建（`_build_full` `:418-433` + `_materialize_dev` `:459-475`）。首次必然全量（含 pnpm 构建，可能数分钟）。指纹基线在 `.dev/state.json`；缺少 `pins.txt`、缺 dev venv 也会触发全量（`dev.py:825-838`）。

Windows 上运行中的 Python 持有 `.pyd`／`.pyc` 文件锁，所以顺序固定为"停旧 → 装 → 起新"；构建阶段可以让旧服继续跑（`dev.py:20-22`、`:860-862`）。

### 3.4 `.dev/` 布局

| 路径 | 用途 |
| --- | --- |
| `.dev/artifacts/` | 本次 dev 构建的 wheels／pins／tarballs／observer-web dist |
| `.dev/gen/` | dev 专用代：`env/.venv`、`GENERATION.toml`、`catalog.json` |
| `.dev/state.json` | 上次构建的单元指纹、HEAD、构建时间 |
| `.dev/dev.pid` | 前台 dev 会话的进程号（与旧工具链同路径、同格式） |
| `.dev/dev.log` | 监督侧 INFO + 子进程 stdout/stderr（追加写） |
| `.dev/stop-request` | Windows 下 `ghrah dev stop` 的停止请求文件 |

dev 产物落 `.dev/` 而永不重指 `current`，所以 dev 迭代不会污染发布态身份；但 dev 与发布态**共用**同一对端口，一次只能运行一个形态。

### 3.5 授权策略注入是必要前置

`scripts/dev-run.ps1` 在调用前把如下 JSON 写进 `GHRAH_SUBJECT_ABILITY_GRANT_POLICY`（`scripts/dev-run.ps1:28-38`）：

```json
{"mode":"merge","rules":[
  {"package":"ghrah.core","operation":"session_info"},
  {"package":"ghrah.fs","operation":"read_file"},
  {"package":"ghrah.fs","operation":"list_directory"},
  {"package":"ghrah.shell","operation":"execute_command"}
]}
```

规则只列具体成员：整包授予会把 `conversation`／`end_task` 一并授出，而这两个是 Agent 自身提供的循环控制成员，重复注册会让 spawn 失败。不注入时 `SubjectConfig.ability_grant_policy=None`：启动核定只认关系库既有事实，不产生新授予；全新 Agent 会在启动期直接失败，而不是"零工具可用"地跑起来（`config.py:405-408`、`startup.py:655-670`）。

## 4. Subject 单元与 HTTP 面

### 4.1 unit 装配

`mount_builtin_units`（`ghrah-subject/src/ghrah/subject/units/__init__.py:21-141`）按 profile 串行挂载，每个 unit `ctx.plugin` 后 `await wait_active(..., timeout=10.0)`；并发挂载会让多个 store 同时打开同一 SQLite 文件而触发 `database is locked`（同文件 `:36-40`）。

| 分组 | unit | 数量 |
| --- | --- | --- |
| 基础 | `SandboxUnit`、`LedgerUnit`、`WorkspaceUnit` | 3 |
| coexistence 追加 | `TaskStoreUnit`（`config.taskstore.enabled`）、`TaskUnit`、`DesiredStateUnit` | 3 |
| full 追加 | `ShippedAbilityUnit`、`WebSocketObserverEndpointUnit`、`CoreClusterRegistryUnit`、`ProjectUnit`、`ProjectControlUnit`、`AbilityControlUnit`、`AgentTemplateControlUnit`、`AgentControlUnit`、`AgentconfControlUnit`、`MediaControlUnit`、`ArtifactControlUnit`、`RoomUnit`、`RecoveryUnit` | 13 |
| full 条件追加 | `RoomFilterUnit`（`config.room_filter.enabled`，必须在 `RoomUnit` 之后） | 1 |

默认配置（taskstore 与 room_filter 都开）下 **coexistence = 6、full = 20**；关掉可选切片时对应 unit 不挂载，非法 profile 抛 `Unknown built-in Subject unit profile`（`:55-56`）。模块 docstring 写的 7／21 已过时，见 §9。

### 4.2 HTTP 路由

FastAPI 实例由 `_create_observer_app` 创建（`units/websocket_observer_endpoint.py:213-250`），`docs_url`／`redoc_url` 关闭，`openapi_url="/api/v1/openapi.json"`：

| 方法／协议 | 路径 | 返回 | 位置 |
| --- | --- | --- | --- |
| WebSocket | `config.ws_path`（默认 `/ws`） | 观察端会话 | `:244` |
| GET | `/api/v1/health` | `{"status":"ok","active_sessions":N}` | `:231-235`、`:245` |
| GET | `/api/v1/meta` | `{name,version,description}` | `:237-242`、`:246` |
| GET | `/health` | 同上健康体；`deprecated=True`、不进 schema | `:247-249` |

`/health` 是过渡别名：工具链的生命周期探测仍请求它（`lifecycle.py:206-210`），所以暂时不能删。业务／管理路由不在这个文件里，而由各 control unit 用 `APIRouter(prefix="/api/v1")` 构造后 `endpoint.app.include_router(...)` 挂载：

| 前缀 | 归属 unit |
| --- | --- |
| `/api/v1`（projects／agents／abilities／agent-templates） | `ProjectControlUnit`、`AgentControlUnit`、`AbilityControlUnit`、`AgentTemplateControlUnit` |
| `/api/v1/agentconf` | `AgentconfControlUnit` |
| `/api/v1`（media／artifacts） | `MediaControlUnit`、`ArtifactControlUnit` |

`AbilityControlUnit.init` 还会额外挂载 `project.py:build_project_shell_allow_router`（前缀 `/api/v1/projects/{project_id}/shell-allow`）。

挂载顺序有硬约束：`AgentTemplateControlUnit` 依赖 `AbilityControlUnit` 注册的 REST 错误处理器，必须在它之后；`RoomUnit` 在 `ProjectUnit` 之后；`RoomFilterUnit` 在 `RoomUnit` 之后。

### 4.3 鉴权与 CORS

- **回环即受信**：`_is_loopback` 命中 `127.0.0.1`／`::1`／`localhost`／`testclient` 直接放行；非回环请求必须带 `Authorization: Bearer <token>`，否则 `access_denied`（403）（`ability_control.py:126-141`、`:187-198`）。
- **token**：`GHRAH_SUBJECT_OBSERVER_HTTP_API_TOKEN` 默认空；空 token 时非回环请求一律拒绝。CORS 只是浏览器策略，不是安全边界；公网／跨机部署应同时关闭管理面或配置 token 并使用 TLS／同源反代（`config.py:412-427`）。
- **管理面开关**：`GHRAH_SUBJECT_OBSERVER_HTTP_API_ENABLED=false` 时端点返回 `503 capability_unavailable`（`ability_control.py:170-176`、`agent_control.py:125-131`）。
- **CORS**：`AbilityControlUnit.init` 添加 `CORSMiddleware`，`allow_origins=list(config.observer_cors_origins)`、`allow_credentials=False`，方法含 `GET/POST/PUT/PATCH/DELETE/OPTIONS`，请求头含 `Authorization`、`Content-Type`、`Accept`、`X-Filename`、`Range`（`ability_control.py:712-721`）。前端页面用 `http://localhost:5173` 打开时，工具链会把该 origin 合并进白名单；否则预检会被拒。

### 4.4 WebSocket

`ObserverServer.handle_connection`（`server/server.py:68-129`）：`session_id=uuid4().hex[:16]`；query 可取 `client_id` 与 `last_seq_id`；welcome 帧后按 `EventBus.replay_since` 回放断线期间事件；`ping_interval=30`、`ping_timeout=10`（`server/config.py:25-27`）。消息循环内 `PING` 回 `pong`，其余命令转 `engine.dispatch_observer_command`。前端 WS 地址优先 `VITE_GHRAH_SUBJECT_WS_URL`，其次页面注入的 `window.__GHRAH_SUBJECT_WS_URL__`，兜底 `ws://localhost:4112/ws`（`observer-core/src/stores/connection.ts:14-18`）。

## 5. 数据落盘

### 5.1 实例级数据盘

工具链在启动时把五项数据面 env 全部指向 `<实例>/data/`（`layout.py:build_data_plane_env` `:330-338`、`ensure_data_dirs` `:351-354`）：

| 路径 | 内容 | env |
| --- | --- | --- |
| `data/subject.db` | Subject 自身库：`subject_schema_versions`、`subject_projects`、`subject_tasks`、`subject_rooms`、`subject_room_logs`、`subject_workspaces`、`subject_desired_state`、`agent_templates` | `GHRAH_SUBJECT_DB_PATH` |
| `data/taskstore.sqlite3` | 任务归因内核：`schema_version`、`tasks`、`evidence`、`claims` | `GHRAH_SUBJECT_TASKSTORE_DB` |
| `data/manifests/` | 实例级 manifest 根 | `GHRAH_SUBJECT_MANIFEST_ROOT` |
| `data/projects/{project_id}/` | 每个 Project 的 Root（`.ghrah-project.json`、`db/`、`manifests/`、`artifacts/`、`memory/`） | `GHRAH_SUBJECT_PROJECT_DEFAULT_ROOT_LOCATOR_TEMPLATE` |
| `data/workspace/` | 默认工作区 | `GHRAH_SUBJECT_WORKSPACE_ROOT` |

`agent_templates` 是模板的唯一权威表，一行一个版本（`template_store/store.py:57-85`）；它和 `subject_projects` 等表同库，不是独立的模板库。

### 5.2 Project 级数据库

每个 Project 的 `db/` 由 `ProjectPaths` 固定（`project/paths.py:99-112`）：

| 文件 | 内容 | 写入方 |
| --- | --- | --- |
| `db/action-chains.sqlite3` | Core 的 ActionChain／session／branch／messages 真相源；**同时**承载能力关系表 | Core `SqliteBackend`、`MessageManager`、`AbilityAccessStore` |
| `db/agents.sqlite3` | Agent 实例快照 `agent_instances`（成员绑定的权威） | Core `AgentInstanceStore`（`core/agents/instance_store.py:47`） |
| `db/tasks.sqlite3` | Project 作用域任务表 | Subject `ProjectScopedTaskStore` |
| `db/rooms.sqlite3` | Project 作用域房间表 | Subject `ProjectScopedRoomStore` |

链历史的读侧由 `LedgerUnit` 经 `CorePort.sessions.history` 取，**不直连** 该 sqlite（`units/ledger.py:7`）；Subject 只在能力管理面经 `AbilityAccessStore` 打开同一文件（`core_cluster/registry.py:439-447`）。`action-chains.sqlite3` 是单文件多写者：Core 链表、消息表、能力关系表都在这一个文件里。

### 5.3 能力关系表

`AbilityAccessStore` 的 DDL 定义 `ability_tree_schema` 版本 **5**，并建八张关系表（`ghrah-core/src/ghrah/ability/access/store.py:57-92`）：

| 表 | 作用 |
| --- | --- |
| `ability_tree_schema` | schema 版本（`CHECK(version=5)`）；旧主体语义或缺版本标记时打开即拒绝，要求显式重建（`store.py:312-345`） |
| `ability_versions` | 已提交版本定义／实现／manifest（按 `project,digest`） |
| `ability_content` | 内容成员字节（按 `project,digest`） |
| `ability_instances` | 包实例（`project,id,root,revision`） |
| `ability_agents` | Agent 关系修订 |
| `ability_grants` | 授权事实：`agent,alias,instance,members,roots,revision,content,axes,source` |
| `ability_tools` | 本次选定的工具名与成员映射 |
| `ability_changes` | 变更历史（含 `policy` 摘要与 `source`） |
| `ability_registrations` | 启动注册占位（`token,state,owned_revision`） |

运行期读取只认这张关系库，不重新读模板或策略；模板与策略只在启动核定时使用（`reconcile.py:1-16`）。

### 5.4 目录与已退役项

- `ProjectPaths.initialize` 负责建 `db/`、`manifests/`、`artifacts/`、`memory/` 等目录并写项目 marker（`project/paths.py:139-189`）；Root 嵌套、Root 与工作区重叠会 fail-closed（`:49-71`）。
- 技能根 `GHRAH_SUBJECT_SKILL_ROOT` 已退役：设置它只会得到警告 `GHRAH_SUBJECT_SKILL_ROOT has been retired; install Skills as v1 Ability packages`（`config.py:778-781`），不再解析该目录。Skills 按 v1 Ability 包安装到 `GHRAH_SUBJECT_ABILITY_INSTALL_ROOT`，工具链默认指向 `<实例>/ability`（`layout.py:build_ability_env` `:373-382`），核心侧对应 `AbilityConfig.install_root`（`config.py:171-188`）。
- `~/.ghrah` 顶层的旧 `subject.db`／`taskstore.sqlite3`／旧目录不再被实例读取；需要迁移时用 `ghrah distro install ... --adopt-legacy-data`（`cli.py:200-213`），普通启动只打提示。

## 6. 启动授权

### 6.1 链路

```text
GHRAH_SUBJECT_ABILITY_GRANT_POLICY (JSON 文本)
  → SubjectConfig.from_env()            config.py:783-789
  → GrantPolicy.from_json()              policy.py:134-151
  → CoreUnitConfig.ability_grant_policy  core_cluster/registry.py:664-666
  → build_startup_materializer()         ability/access/boundary.py:32-91
  → StartupGrantMaterializer.materialize(agent=..., resolved=...)
                                         ability/access/startup.py:601-742
  → apply_grants()（CAS 单写方）          ability/access/reconcile.py
  → ability_grants（+ ability_changes）   action-chains.sqlite3
```

`StartupGrantMaterializer` 是实际类名（`startup.py:443`）；`agents/creation.py:6-8` 的 docstring 用简写 `StartupMaterializer`，以类名为准。

### 6.2 语义

- `GrantPolicy.mode`：`replace` 用策略集合替换模板结果；`merge` 在模板结果上先移除后授予（`policy.py:32-36`、`:108-132`）。
- 规则目标逐级细化：包 → Operation → skill → script；包级规则不带成员轴；skill 只有"可见"，Operation 与 script 才有"可见＋可用"两轴（`policy.py:42-105`）。
- **模板只申请**：模板决定本次启动涉及哪些包与成员；没有策略时 `policy=None`，只认关系库里已有的管理员／迁移事实，不扩权也不撤销；没有事实则不授予、启动失败（`startup.py:655-663`）。
- 授权写入的唯一入口是 `AbilityAccessStore`（`startup.py:683-704`）；修订以 CAS 重试，失败不回退到"模板自动授予"（`reconcile.py:10-16`）。
- 循环控制成员 `conversation`／`end_task` 由 Agent 原控制机制提供，不进关系库；`ghrah.core` 的系统元工具在普通授权成立后才加入，不能让空策略靠元工具启动（`startup.py:76-77`、`:671-693`）。策略摘要 `GrantPolicy.digest` 写入 `ability_changes.policy`（`policy.py:182-196`、`startup.py:701`）。

### 6.3 失败

策略 JSON 非法时 `GrantPolicy.from_json` 抛 `授权策略无效：…`；`SubjectConfig.from_env` 在 `configure_logging` 之前执行，所以进程会在日志装配前退出，traceback 落在实例日志里（`server/app.py:167-181`）。策略引用了本次启动未解析的包、模板没有任何能力包成员、策略与模板没有授予任何成员，都会由 `StartupGrantError` 直接失败（原文见 §8.4）。

## 7. 日志

### 7.1 Subject 日志装配

唯一实现是 `ghrah-subject/src/ghrah/subject/logging_setup.py`：

- `DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"`（`:28`）；
- `StreamHandler` 恒有；`log_file` 非空时再加 `FileHandler(resolved, encoding="utf-8")`，目录自动 `mkdir`（`:81-86`）；
- `logging.basicConfig(..., handlers=handlers, force=True)`（`:89`），重复调用不会叠加 handler；
- 返回实际写入的文件路径（`:92`）。

入口在 `ghrah-subject`／`ghrah-subject-server` 的 `main()` 里调用（`subject/app.py:36-45`、`server/app.py:167-181`）。**没有轮转**：全仓没有 `RotatingFileHandler`／`maxBytes`／`backupCount`，`GHRAH_SUBJECT_LOG_FILE` 是单文件追加。

| env | 默认 | 作用 |
| --- | --- | --- |
| `GHRAH_SUBJECT_LOG_LEVEL` | `INFO` | 根级别 |
| `GHRAH_SUBJECT_LOG_LEVELS` | 空 | 定向级别，如 `ghrah=DEBUG,aiosqlite=WARNING` |
| `GHRAH_SUBJECT_LOG_FILE` | 空 | 同时写文件；空 = 只写 stdout/stderr |

### 7.2 实例与会话日志

后台 `start` 每次会话生成新 `token`，日志固定为 `<实例>/logs/<token>.log`，以追加模式打开，监督者与子进程输出都写进去；`ghrah distro logs` 读当前会话的该文件（`instances.py:193-222`、`:379-392`）。新启动不会覆盖旧文件，**也不会自动轮转**；`--tail` 默认 100 行（受 `self.log_tail` 偏好影响），`--follow` 从同一文件句柄持续读，重指到新会话时自动切换（`:394-432`）。

dev 会话的日志是 `<实例>/.dev/dev.log`：监督侧 INFO 记录与后端／前端子进程 stdout/stderr 追加进同一文件；打不开只告警，不阻塞起服（`dev.py:649-663`、`:931-960`）。

### 7.3 WS 事件日志与 Core 日志

- **WS 事件不是日志文件**：`EventBus` 的 `EventStore` 是 `deque` 环形缓冲，容量 `event_replay_capacity` 默认 1000（`server/event_bus.py:29-41`、`server/config.py:32`）；`replay_since` 只能回放进程内还留着的事件，进程重启即丢。可调 `GHRAH_SUBJECT_SERVER_EVENT_REPLAY_CAPACITY`，但不要把它当持久事件审计。
- **Core 日志与 Subject 同进程**：Core 在宿主进程内组装，按 `logging.getLogger(__name__)` 打 `ghrah.core.*`，与 Subject 共用 handler 与实例日志文件；链的持久事实在 `action-chains.sqlite3`，日志只用于事后追执行轨迹。

### 7.4 噪音与定向

| 来源 | 说明 |
| --- | --- |
| `aiosqlite` | 逐条 SQL 打 DEBUG，可把启动拖过 10 秒健康检查窗口（`logging_setup.py:7-10`） |
| `uvicorn` | 自身日志与 access log 走 INFO（`server/app.py:143-151`） |
| `ObserverRouter.handle_event`／`EventBus`／WS 会话 | 每事件 INFO、建立／断开／回放／清理均为 INFO（`server/router.py:136-137`、`server/event_bus.py:84-236`、`server/server.py:102-133`） |

排障首选 `GHRAH_SUBJECT_LOG_LEVELS="ghrah=DEBUG,aiosqlite=WARNING"`，不要直接全局 DEBUG。工具链实例在 `distro.toml` 的 `[runtime.env]` 里配置该键，重启后生效：

```toml
[runtime.env]
GHRAH_SUBJECT_LOG_LEVELS = "ghrah=DEBUG,aiosqlite=WARNING"
```

## 8. 排障

### 8.1 按状态机处置

| 现象 | 首查 | 处置 |
| --- | --- | --- |
| `starting` 停留过久 | `ghrah distro logs <实例> --tail 200`；端口是否被占 | 看后端 spawn／健康检查失败原因；端口冲突按 §8.2 让位；必要时 `ghrah distro stop` 后重试 |
| `failed` | 日志末尾的 traceback 与 `.session.json` 的 `error` | 启动期硬失败见 §8.4；修好后 `ghrah distro start` |
| `running` 但页面无响应 | 自行 `curl http://127.0.0.1:<port>/api/v1/health` | `status` 不重探；无响应按 failed 处理，取日志 |
| `stopped` 但端口仍监听 | `Get-NetTCPConnection -LocalPort 4112`（Windows）／`ss -ltnp` | 残留子进程：先 `ghrah distro stop`；仍占用则人工按端口找进程，不要直接删记录 |
| `legacy` | `boot.toml`／`.boot.pid` 存在旧会话 | 先停旧会话；`ghrah distro start` 会拒绝"实例仍通过旧版方式运行" |
| `无法确认后台管理进程的身份，但端口仍在监听` | 进程身份核对失败（PID 命名空间／监督者已退出但有孤儿） | 不自动强杀；人工核查端口占用后处理（`instances.py:339-351`） |

### 8.2 端口让位与幽灵实例

- **端口被占**：`ghrah distro status` 的地址来自配置，不代表端口属于本实例。先 `ghrah distro stop <实例>`；`dev run` 会自动停掉同端口的发布态会话并等端口真正释放（最多 10 秒），仍占用则报 `端口仍被占用: …`（`dev.py:602-619`）。
- **幽灵实例**：`.session.json` 记录为 `starting`／`running`，但监督者进程已死。`instance_status` 会把它判为 `failed`；`ghrah distro start` 只要确认旧进程不在就会新起会话（`instances.py:181-183`），因此 start 是最简修复。`ghrah distro stop` 在"进程不可见但端口仍监听"时故意报错，避免把仍在服务的实例报告成已停止（`:339-352`）。
- **停止超时**：`ghrah distro stop` 默认等 15 秒；超时报 `停止超时；未强杀，查看实例日志`，CLI 不强杀监督者、交人工核查。子进程组由监督者的收束逻辑处理：Windows `taskkill /T`，POSIX `SIGTERM → 5s → SIGKILL`（`lifecycle.py:128-194`）。

### 8.3 dev 全量重建

指纹判断不符合预期时，按代价从低到高：

1. `ghrah dev status` 看它认为哪些单元变了；
2. `ghrah dev run --full` 强制全量重建；
3. `ghrah dev reset` 删除 `.dev/`（产物、venv、指纹一次性清空），再 `ghrah dev run`；
4. 仍失败时检查 `distro.toml` 的 `[distro.repo].path` 是否仍是有效工作树，以及 `.dev/dev.log` 里的 pnpm／uv 错误。

`dev reset` 只删 `.dev/`，不动 `data/`、`current`、`logs/` 与发布代。

### 8.4 启动期硬失败原文

| 稳定码／原文 | 触发条件 | 证据 |
| --- | --- | --- |
| `agent_manifest_required` | 启动请求没有可用的模板 manifest；Subject 兼容映射为 `invalid_change`（400），`detail` 带 `kept` | `units/agent_control.py:232-237` |
| `invalid_binding` | 初始绑定名不在"本次真正装上的成员"里，fail-closed | `agents/render.py:367-372` |
| `template_store_unavailable` | Agent 模板存储未挂载，按模板创建不可完成 | `project/manager.py:867-871` |
| `capability_unavailable` | 管理面关闭（`observer_http_api_enabled=false`）或能力来源不是 builtin，HTTP 503 | `units/ability_control.py:170-185` |
| `no_granted_members` | 渲染后没有任何已授予成员，创建在进 Core 前失败 | `agents/creation.py:187-193` |
| `spawn '<agent>' 未配置启动授权策略且没有存量授权事实——不授予任何能力；请配置启动策略，或对已部署 Agent 执行一次性迁移` | `policy=None` 且关系库没有既有事实 | `ability/access/startup.py:655-663` |
| `spawn '<agent>' 的启动授权为空——策略与模板没有授予任何成员；请检查授权策略或 manifest 模板` | 策略存在但算出的目标集合为空 | `startup.py:664-670` |
| `策略引用了未解析的包：<package>` | 策略里的包不在本次启动的能力目录中 | `startup.py:621-625` |
| `spawn '<agent>' 的模板没有任何能力包成员——builtin 模式不能只靠循环控制成员启动；请在 manifest 中声明能力包 ref` | 模板只声明控制成员 | `startup.py:636-640` |
| `manifest_store not available (standalone)` | Core 未注入模板解析源；当前 Core 侧的对应文案 | `ghrah-core/src/ghrah/core/unit.py:992-997` |

说明：`agent_manifest_required` 仍保留在 Subject 的兼容映射里，但当前 Core 不再直接产生该字符串，实际失败文案可能是上表最后一行或 `manifest resolve failed for '<ref>': …`。排障以实例日志里的完整原文为准。

### 8.5 其他常见项

- **鉴权失败**：非回环请求返回 `access_denied`／`非回环请求需要有效 Bearer token`；确认 `GHRAH_SUBJECT_OBSERVER_HTTP_API_TOKEN` 与请求头一致。
- **CORS 预检 400**：前端端口不是 5173／5180 且未合并进白名单；用工具链启动会自动合并，直接跑 server 需自己配 `GHRAH_SUBJECT_OBSERVER_CORS_ORIGINS`。
- **策略警告**：设置 `GHRAH_SUBJECT_SKILL_ROOT` 只有一行退役警告，不生效；按 §5.4 改用 Ability 包安装根。

## 9. 已知运维偏差

以下按当前代码与命令面实测，不沿用历史编号：

| 偏差 | 证据 | 影响 |
| --- | --- | --- |
| `units/__init__.py` 模块 docstring 写 coexistence=7／full=21，实际挂载为 6／20 | `units/__init__.py:31-37` vs `:66-134` | 按代码计数，不要按 docstring 判断 unit 数量 |
| `/health` 已标 deprecated，但工具链生命周期仍以它为健康探测端点 | `websocket_observer_endpoint.py:247-249`、`lifecycle.py:206-210`、`health.py:21-47` | 别名暂时不能删；迁移到 `/api/v1/health` 需要工具链同步改 |
| `status` 的 `running` 是历史结论，命令不重探健康 | `instances.py:117-137`、`cli.py:524-536` | 不能把 running 当"此刻可用"；排障要自己请求健康端点 |
| 实例日志每次启动一个新文件且无轮转；dev 日志同样只追加 | `instances.py:193-222`、`dev.py:649-663` | 长期运行需要外部清理／归档 |
| `agent_manifest_required` 只剩 Subject 兼容映射，当前 Core 已不再产生该字符串 | `agent_control.py:232-237` vs `core/unit.py:992-997` | 前端按该码分支可能永远不命中；以日志原文为准 |
| 直接运行 `ghrah-subject-server` 会绑 `0.0.0.0` 并使用顶层 `~/.ghrah` 默认路径 | `server/config.py:27`、`:51`、`subject/config.py:140`／`:167`／`:325` | 只有工具链启动才注入回环地址与实例 `data/` 路径；手工直跑会暴露端口并写错库 |
| `ghrah distro list` 把 `~/.ghrah` 下任意顶层目录都当实例列出 | `layout.py:290-302` | 遗留目录（如 `abilities/`、`projects/`）会显示成"已停止实例"；以 `distro.toml` 是否存在判断真实例 |
| `stop` 不判断 Agent 是否仍在工作 | `ghrah-toolchain/README.md`「已知的限制」、`instances.py:322-376` | 在 Agent 循环中停止可能造成中断；运维需自行确认空闲窗口 |
| 顶层旧数据不自动迁移，只在命令里提示 | `cli.py:46-54`、`:186`、`adopt.py` | 升级后可能误以为数据丢失；需要显式 `--adopt-legacy-data` |