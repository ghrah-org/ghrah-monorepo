# 构建、启动、日志与排障

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-OPS-005 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `f8ff157`（2026-10-28） |
| 读者 | 首次搭环境的人、要部署到新机器的运维、排查"起不来/起错端口/日志刷屏/数据在哪"的开发者 |
| 取代 | `构建启动与日志链路说明.md`、`启动命令.txt`（归档至 `docs/archive/`） |
| 相关文档 | `ghrah-01-ability-design-and-implementation.md`（能力装配）、`ghrah-03-drive-loop.md`（循环与压缩）、`ghrah-06-code-map.md` §5（协议与前端下发） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。修正三处旧文档事实错误：不存在 `ghrah-toolchain/`、不存在 `ghrah-core/src/ghrah/logging/`、`full` profile 实际挂 14 个 unit（文档字符串里的 15 与 `ability_bridge` 已过时） |

## 1. 启动链路

唯一真入口是 `scripts/start_all.py`（322 行），`main()` 在 `:234-315`。

| 步 | 行号 | 动作 | 失败行为 |
| --- | --- | --- | --- |
| 1 | `:235` | `parse_args()` | — |
| 2 | `:236-239` | `logging.basicConfig(level=args.log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")`，logger 名 `ghrah.start_all`（`:48`） | — |
| 3 | `:241` | `ensure_tool("uv", …)`——**无条件**先查 uv | `RuntimeError` → exit 1 |
| 4 | `:243-246` | 未 `--no-webui` 时：`ensure_tool("pnpm")` → `ensure_node_version()` → 检查 `WEBUI_PACKAGE_DIR.is_dir()` | 同上 |
| 5 | `:248-250` | `check_port_free(host, subject_port, "Subject")`，再查 WebUI 端口 | 端口被占即失败 |
| 6 | `:258-260` | 起 Subject：`[uv, "run", "ghrah-subject-server"]`，`cwd=MONOREPO_ROOT`，注入 `GHRAH_SUBJECT_SERVER_PORT`，`setdefault GHRAH_SUBJECT_SERVER_HOST=args.host` | — |
| 7 | `:268` | `wait_port_open(..., 20)`——**纯 TCP 探活**，超时只 WARNING、**不失败** | 打印警告继续 |
| 8 | `:276` | `write_webui_env(f"ws://{host}:{subject_port}/ws")`，写 `ghrah-observer-webui/packages/observer-web/.env.local`（`:52-53`、`:213-231`） | — |
| 9 | `:277-280` | 起 WebUI：`[pnpm, "dev", "--port", str(webui_port), "--strictPort"]`，`cwd=WEBUI_PACKAGE_DIR` | — |
| 10 | `:287` | `wait_port_open` 等 WebUI | — |
| 11 | `:294-298` | 打印 `=== ghrah 全栈启动完成 ===`；`--open` 时 `webbrowser.open` | — |
| 12 | `:300` | `await wait_shutdown_signal()`（POSIX 捕获 `SIGINT`/`SIGTERM` `:204-208`；Windows 等空 Event） | — |
| 13 | `:307-312` | `finally`：**逆序** `terminate`（POSIX `killpg` SIGTERM → 5s → SIGKILL `:163-198`；Windows `proc.terminate`/`proc.kill` `:153-160`）→ `cleanup_webui_env` | — |

`RuntimeError` 统一走 `logger.error` + `exit_code = 1`（`:303-305`），最后 `sys.exit(exit_code)`（`:315`）。

### 1.1 端口与默认值

| 项 | 默认 | 覆盖方式 |
| --- | --- | --- |
| Subject 端口 | `4112`（`DEFAULT_SUBJECT_PORT` `:55`） | `--subject-port` 或 `GHRAH_SUBJECT_SERVER_PORT`（`:73`） |
| Subject 宿主 | `127.0.0.1`（`:67`） | `--host` 或 `GHRAH_SUBJECT_SERVER_HOST` |
| WebUI 端口 | `5173`（`DEFAULT_WEBUI_PORT` `:56`） | `--webui-port` 或 `GHRAH_WEBUI_PORT`（`:79`） |
| 日志级别 | `INFO`（`:85-88`） | `--log-level`（**只影响 start_all 自身进程**） |

**注意宿主差异**：库层 `ObserverServerConfig.host` 默认 `0.0.0.0`（`subject/server/config.py:23`），但 `start_all.py:259` 用 `setdefault` 把它改成回环——所以走 `start_all.py` 只监听 127.0.0.1，直接跑 `ghrah-subject-server` 才是 0.0.0.0。

健康检查只有端口探活，Subject 自带的 `/health`（`units/websocket_observer_endpoint.py:194-198`）**从未被 start_all 调用**。

## 2. 命令面

没有 `ghrah-toolchain/` 这个目录（旧文档提到的命令面不存在）。真实命令来自 `[project.scripts]` 与 `scripts/*.py`。

### 2.1 console scripts（`ghrah-subject/pyproject.toml`）

| 命令 | 入口 | 行为 |
| --- | --- | --- |
| `subject` | `ghrah.subject.cli:main`（`subject/cli.py:35-52`） | 唯一子命令 `subject plugin verify [--all]`（`:41-52`）；走 `ghrah.plugin.loader.discover_plugins` / `ghrah.plugin.verify.verify_plugins`（`:21-23`、`:96-105`）；exit `0`/`1`/`2`（`:29-31`）；空信任表打印 `no plugins to verify (trust list is empty; use --all to check all discovered)`（`:82`）；只有 spec 时警告 `no unit factory detected (spec-only: negotiable but not mountable)`（`:114`） |
| `ghrah-subject` | `ghrah.subject.app:main`（`subject/app.py:32-50`） | `configure_logging`（`:36-38`）→ 打印 `ghrah-subject starting (ouroboros, profile=full)...`（`:41`）+ `workspace_root`（`:42`）/`db_path`（`:43`）/可选 `log_file`（`:45`）→ `asyncio.run(run_forever())`（`:48`） |
| `ghrah-subject-server` | `ghrah.subject.server.app:main`（`server/app.py:92-106`） | `configure_logging`（`:100-102`）→ `ghrah-subject observer server starting (ouroboros, profile=full)...`（`:103`）→ `asyncio.run(serve())`（`:106`） |

### 2.2 仓库脚本（`scripts/`）

| 脚本 | 参数 | 用途 |
| --- | --- | --- |
| `start_all.py` | `--host` `--subject-port` `--webui-port` `--log-level` `--no-webui` `--open` | 全栈启动（§1） |
| `check_docstrings.py` | `--summary`（`argparse` `:200-213`） | docstring 覆盖率检查，`--summary` 只出目录计数 |
| `check_release_tag.py` | `--target`（取值来自 `TARGETS`）+ `--version`（必填，`:48-50`） | 校验发布 tag 与版本一致 |
| `release_dry_run.py` | `--all`、`--out-dir`（必填且必须为空，`:269-271`） | 干跑发布产物 |

### 2.3 WebUI

`pnpm dev|build|preview|test`（`ghrah-observer-webui/packages/observer-web/package.json`）；`predev`/`prebuild` 先跑 `node ../../scripts/assemble-plugins.mjs`。全栈脚本是 `ghrah-observer-webui/scripts/dev.sh`：末尾 `exec python3 "$MONOREPO_ROOT/scripts/start_all.py" "$@"`（`:51`）；`--mock` 模式起只协议 mock server 于 `4113` + SPA（`dev.sh:10-43`，`SCENARIO` 默认 `demo` `:12`、`PORT` `4113` `:13`）。`build.sh` 依次构建 protocol → plugin-registry → observer-core → observer-web → vscode-extension，最后 `vsce package`。

## 3. 服务端

### 3.1 app 工厂是两层

`subject/server/app.py:27-31` 的 `create_app(config, *, ctx)` 只是门面：从已装配的 Context 取 `observer_endpoint`（`:50`），类型不符即

```
create_app requires an assembled Context with the websocket_observer_endpoint unit (assemble_subject(profile='full')).
```

（`:52-56`），然后返回 `unit.app`（`:57`）。真正的 FastAPI 实例在 `units/websocket_observer_endpoint.py:180-210` 的 `_create_observer_app`：`FastAPI(title="ghrah-subject-observer", …, version="0.1.0")`（`:185-189`）。

### 3.2 路由全表（3 条）

| 方法/协议 | 路径 | 返回 | 行号 |
| --- | --- | --- | --- |
| WebSocket | `config.ws_path`（默认 `/ws`） | 观察端会话（握手/续传见 §3.4） | `:207` |
| GET | `/health` | `{"status": "ok", "active_sessions": …}` | `:208`、`:194-198` |
| GET | `/` | `{name, version, description}` | `:209`、`:200-205` |

**没有** `include_router`、**没有** CORS 中间件、**没有** 静态资源挂载——`CORSMiddleware|add_middleware|StaticFiles` 在 `ghrah-subject/src` 下 0 命中。这也是 WebUI 直接连 WS 而不是走 HTTP 代理的原因（§7）。

### 3.3 启动与配置

`serve()`（`server/app.py:60-89`）：`ObserverServerConfig.from_env()`（`:70`）+ `SubjectConfig.from_env()`（`:71`）→ `async with Context()`（`:72`）→ `assemble_subject(ctx, cfg, profile="full")`（`:73`）→ `uvicorn.Server(uvicorn.Config(app, host, port, log_level=…, ws_ping_interval, ws_ping_timeout))`（`:76-85`）→ `await server.serve()`（`:86`）。

| 字段 | 默认 | 行号 |
| --- | --- | --- |
| `host` | `"0.0.0.0"` | `server/config.py:23` |
| `port` | `4112` | `:24` |
| `ws_path` | `"/ws"` | `:25` |
| `ping_interval` | `30.0` | `:26` |
| `ping_timeout` | `10.0` | `:27` |
| `event_replay_capacity` | `1000` | `:28` |
| `log_level` | `"INFO"` | `:29` |

环境变量名 `GHRAH_SUBJECT_SERVER_{HOST,PORT,WS_PATH,PING_INTERVAL,PING_TIMEOUT,EVENT_REPLAY_CAPACITY,LOG_LEVEL}`（`:47-53`）。类型错抛 `"Environment variable GHRAH_SUBJECT_SERVER_{key} must be an integer, got '{val}'"`（`:57-66`）或同形 `must be a number`（`:69-78`）。

### 3.4 WebSocket 握手与续传

`server/server.py:68-129`：`session_id = uuid4().hex[:16]`（`:80`）；query 取 `client_id`（`:82`）与 `last_seq_id`（`:86-89`）；welcome 帧 `request_id="connect"`（`:100-108`）；用 `EventBus.replay_since` 回放断线期间事件（`:110-118`）。消息循环 `:131-180`：`PING` → `pong`（`:162-165`）；命令 `asyncio.create_task(name=f"obs-cmd-{session_id[:8]}-{message.type}")`（`:173-180`）。`router.py:98-110` 只本地处理 `subscribe`/`unsubscribe`，其余转 `engine.dispatch_observer_command`。

### 3.5 Subject unit 装配

`units/__init__.py:21-115` 的 `mount_builtin_units(ctx, config, *, profile="coexistence", core_cluster_unit=None, route_registry=None)`：

- 基础 5 个：`SandboxUnit`、`ManifestStoreUnit`、`SkillUnit`、`LedgerUnit`、`WorkspaceUnit`（`:66-72`）；
- `config.taskstore.enabled` 为真时插 `TaskStoreUnit`（`:79-80`），**且必须在 `TaskUnit` 之前**（`:74-78` 注释：builtin 链挂载序 = serial 序，归因命令先注册）；
- 再加 `TaskUnit`、`DesiredStateUnit`（`:82`）；
- `profile == "full"` 追加 `WebSocketObserverEndpointUnit`、`CoreClusterRegistryUnit`（或注入的替换实例）、`ProjectUnit`、`RoomUnit`、`RecoveryUnit`（`:94-104`）；
- `config.room_filter.enabled` 为真时最后追加 `RoomFilterUnit`（`:107-108`，必须在 `RoomUnit` 之后，因为它 requires `ROOM_MANAGER`）；
- 逐个 `ctx.plugin(mount_unit(...))` 并 `await wait_active(fiber, timeout=10.0)`（`:111-114`）——注释 `:36-38` 说明**并发挂载会让多个 store 同时打开同一 SQLite 文件触发 `database is locked`**。

**数量口径**：默认配置（taskstore 与 room_filter 都开）下 `coexistence = 8`（5 + taskstore + task + desired_state），`full = 14`（8 + 5 + room_filter）。模块 docstring `:31-35` 写的 "full = 15（… + ability_bridge）" 已过时：`units/` 下没有 `ability_bridge.py`，`:94-108` 也从未追加它（文档缺陷 D-OPS-1）。关掉 `taskstore.enabled` 或 `room_filter.enabled` 时对应 unit 不挂载（`:79`、`:107`）。非法 profile 抛 `ValueError(f"Unknown built-in Subject unit profile: {profile}")`（`:53-54`）。

## 4. 日志

### 4.1 唯一实现

`ghrah-subject/src/ghrah/subject/logging_setup.py`（92 行）。**不存在** `ghrah-core/src/ghrah/logging/`。

`configure_logging(*, level="INFO", levels="", log_file="")`（`:61-92`）：

- `DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"`（`:28`）；
- `StreamHandler` 恒有（`:81`）；
- `log_file` 非空时加 `FileHandler(resolved, encoding="utf-8")`（`:86`），路径先 `Path(os.path.expanduser(...))`（`:84`）再 `parent.mkdir(parents=True, exist_ok=True)`（`:85`）；
- `logging.basicConfig(..., handlers=handlers, force=True)`（`:89`，`force` 防重复 handler）；
- 返回 resolved `Path | None`（`:92`）。

**没有轮转**：全仓无 `RotatingFileHandler`/`TimedRotatingFileHandler`/`maxBytes`/`backupCount`——单文件 append，无界增长。

### 4.2 logger 名称空间

没有统一的 `ghrah.*` 根：各模块用 `logging.getLogger(__name__)`，所以定向只能按顶层包名 `ghrah` 打，或者精确到具体模块（如 `ghrah.subject.units.ledger`，`units/ledger.py:29`）。

### 4.3 级别与定向

| 项 | 默认 | 位置 |
| --- | --- | --- |
| root 级别 | `INFO` | `subject/config.py:314` |
| 定向表 | `""` | `:315` |
| 日志文件 | `""`（不写文件） | `:316` |

定向语法 `parse_log_levels`（`logging_setup.py:33-58`），条目形如 `logger=LEVEL`，错误消息：

- `ValueError(f"非法定向级别条目: {item!r}（应为 logger=LEVEL）")`（`:54`）
- `ValueError(f"未知日志级别: {level!r}（条目 {item!r}）")`（`:56`）
- root 级别非法：`ValueError(f"未知日志级别: {level!r}")`（`:79`）

### 4.4 三种开 debug 的方式

| 目的 | 环境变量 |
| --- | --- |
| 全局 DEBUG | `GHRAH_SUBJECT_LOG_LEVEL=DEBUG`（`subject/config.py:649`） |
| 只开本仓（**推荐**） | `GHRAH_SUBJECT_LOG_LEVELS="ghrah=DEBUG"`（`:650`） |
| 同时写文件 | `GHRAH_SUBJECT_LOG_FILE=~/ghrah.log`（`:651`，双写） |

`start_all.py --log-level` 只影响 start_all 自己，**不影响子进程**。

### 4.5 噪音来源

| 来源 | 说明 | 位置 |
| --- | --- | --- |
| `aiosqlite` | 逐条 SQL 打 DEBUG——**实测 20 秒 1.35 MB，并且把启动拖过健康检查的 10s hard timeout** | `logging_setup.py:8` |
| `uvicorn` | 自身日志 + access log 走 INFO | `server/app.py:77` |
| 业务每事件 INFO | `handle_event: event_type=%s …` | `router.py:132-137` |
| 会话建立 | `Observer WebSocket session established` | `server/server.py:98` |
| EventBus | 多处 INFO | `server/event_bus.py:67`、`:71`、`:122`、`:188`、`:196` |

排障第一步永远是 `GHRAH_SUBJECT_LOG_LEVELS="ghrah=DEBUG,aiosqlite=WARNING"`，不要直接全局 DEBUG。

## 5. 数据落盘

### 5.1 默认路径

| 数据 | 默认路径 | 位置 |
| --- | --- | --- |
| Subject 库 | `~/.ghrah/subject.db` | `subject/config.py:310`（`PersistenceConfig` 同 `:130`） |
| manifest 根 | `~/.ghrah/manifests` | `:311` |
| TaskStore 库 | `~/.ghrah/taskstore.sqlite3` | `:277` |
| 工作区 | `~/ghrah-workspace`（沙箱同源 `:145`） | `:309` |
| 技能根 | `~/.ghrah/skill-ability` | `:635` |
| Core 库 | `~/.ghrah/data/ghrah.db` | `context/persistence/sqlite_backend.py:170-171` |

`core_db_path = Path(persistence.db_path).parent / "ghrah.db"`（`subject/config.py:391-399`）。

**每 Project 还有一套**（`project/paths.py`）：`db_dir = root/db`（`:99-100`）、`task_db_path = db/tasks.sqlite3`（`:103`）、`room_db_path = db/rooms.sqlite3`（`:107`）、`action_chain_db_path = db/action-chains.sqlite3`（`:111-112`）。注意 `units/ledger.py:88` 用的是 `ProjectPaths.from_locator(...).action_chain_db_path`，**不是** `config.core_db_path`，尽管 `config.py:391-399` 的 docstring 声称共享该推导（D-OPS-2）。

### 5.2 WAL 与 pragma

`sqlite_backend.connect()`（`sqlite_backend.py:220-277`）：`aiosqlite.connect(str(path), isolation_level=None)`（`:269`）、`row_factory = aiosqlite.Row`（`:271`）、`PRAGMA journal_mode=WAL`（`:273`）、`PRAGMA busy_timeout=5000`（`:275`）、`PRAGMA foreign_keys=ON`（`:276`）。只读分支用 `?mode=ro` + `query_only=ON`（`:229-237`）。

`message_manager.py`：`PRAGMA journal_mode=WAL`（`:299`）+ `synchronous=NORMAL`（`:300`）；owner 锁文件 `sqlite3.connect(str(path) + ".owner")`（`:288`）。

各 Store 都在 `start()` 里 `parent.mkdir` → `connect(isolation_level=None)` → `PRAGMA journal_mode=WAL` → `executescript(DDL)` → `_migrate`。

### 5.3 表清单

**Core 上下文库**（`context_schema` version=3，`sqlite_backend.py:54-135`）：

| 表 | 行号 |
| --- | --- |
| `context_schema` | `:55` |
| `runs` | `:61` |
| `agents` | `:68` |
| `sessions` | `:75` |
| `branches` | `:91` |
| `nodes` | `:103` |
| `chain_meta` | `:121` |

外加 8 个索引（`:127-134`）。

**同文件消息表**（`message_manager.py`）：`mm_meta`（`:146`）、`mm_messages`（`:151`）、`mm_deliveries`（`:167`）、`mm_room_projections`（`:187`）、`mm_inline_waits`（`:192`）、`mm_delivery_results`（`:197`）。

**Subject 库**：`subject_schema_versions` + `subject_projects`（`project/store.py:51-70`，WAL `:162`）、`subject_tasks`（`task/store.py:28`，WAL `:141`）、`subject_rooms` + `subject_room_logs`（`room/store.py:47`、`:60`，WAL `:128`）、`subject_workspaces`（`workspace/store.py:47`，WAL `:96`）、`subject_desired_state`（`recovery/desired_state.py:88`，WAL `:136`）。

**TaskStore 库**：`schema_version`（`:37`）、`tasks`（`:40`）、`evidence`（`:59`）、`claims`（`:69`），WAL `:211`。

### 5.4 没有的东西

- **没有 sqlite 事件表**：事件存在内存 ring——`EventStore` 用 `deque`（`server/event_bus.py:25-41`，`replay_since` `:40-41`，容量 `event_replay_capacity = 1000`）。进程重启即丢，只能靠客户端 `last_seq_id` 续传已缓存部分。
- **没有 sqlite chain 表**：链真相源是每 Project 的 `db/action-chains.sqlite3`（`project/paths.py:111-112`），由 Core `SqliteBackend` 与 `MessageManager` **共写同一文件**（`core_cluster/registry.py:382-391`），读侧 `ActionChainLedger` 开只读第二连接（`ledger/chain.py:49-55`、`:63`）。
- **没有锁文件一致性检查**：`uv.lock`/`pnpm-lock.yaml` 只在 `ghrah.toml` 的 `[workspace] native_locks` 里被声明，代码里没有 `lockfile`/`out of sync` 之类校验（全仓 grep 0 命中）。唯一的"锁"错误是 skill lock（§6.3）。

### 5.5 建表责任人

| 库 | 责任人 | 位置 |
| --- | --- | --- |
| Core 上下文库 | `SqliteBackend._initialize_schema`（版本 ≠ 3 或发现旧表即 fail-closed） | `sqlite_backend.py:286-…` |
| Subject 各库 | 各 Store 的 `start()` + `_migrate` | 如 `project/store.py:155-166`、`:168-…` |
| Project 目录结构 | `ProjectPaths.initialize()` | `project/paths.py:130-177` |

## 6. 启动期硬失败与原文

### 6.1 start_all.py

| 场景 | 消息原文 | 行号 |
| --- | --- | --- |
| 端口占用 | `` f"{name} 端口 {port} 已被占用——多半是上一次的进程还活着（可用 `ss -ltnp | grep {port}` 找到后停止，或换 --subject-port/--webui-port）。" `` | `:109-115` |
| 缺工具 | `` f"`{name}` 不在 PATH 上；{hint}" ``（hint：uv `"Python 侧经 uv 运行（https://docs.astral.sh/uv/）。"` `:241`；pnpm `"webui 需要 pnpm >= 9（https://pnpm.io/installation）。"` `:243`） | `:118-123` |
| Node 过旧 | `` f"node {out} 过旧：pnpm 11 需要 node >= 22（node:sqlite），请 `nvm use 24` 后重试" `` | `:126-139` |
| Node 版本不可解析 | `f"无法解析 node 版本号：{out!r}"` | `:126-139` |
| 前端包缺失 | `f"observer-web 包不存在：{WEBUI_PACKAGE_DIR}"` | `:245-246` |
| Subject 早退 | `f"Subject 进程提前退出（code={subject.returncode}），请查看上方日志。"` | `:269-272` |
| WebUI 早退 | `f"WebUI dev server 提前退出（code={webui.returncode}）；常见原因：依赖未装（在 ghrah-observer-webui/ 下 `pnpm install`）或端口被占。"` | `:288-292` |

### 6.2 服务端与 schema

| 场景 | 消息原文 | 行号 |
| --- | --- | --- |
| 装配不完整 | `"create_app requires an assembled Context with the websocket_observer_endpoint unit (assemble_subject(profile='full'))."` | `server/app.py:52-56` |
| profile 名错 | `f"Unknown built-in Subject unit profile: {profile}"` | `units/__init__.py:53-54` |
| 旧 schema | `"Legacy context database schema detected; delete it and rebuild explicitly"` | `sqlite_backend.py:295-297` |
| 不支持 schema | `"Unsupported context database schema; rebuild explicitly"` / `"Unsupported context database schema"` | `:303` / `:253`、`:259-260` |
| 重复连接 | `"Database already connected."` | `:226-227` |
| 未连接 | `"Database not connected. Use 'async with backend:' or call connect() first."` | `:210-212` |
| Store schema 过新 | `f"ProjectStore schema {current} is newer than supported {_SCHEMA_VERSION}"` | `project/store.py:176-179` |
| Project Root 嵌套 | `"Project Roots overlap: … Choose an exclusive, non-nested Project Root."` | `project/paths.py:58-61` |
| Project Root 与工作区重叠 | `"Project Root overlaps a writable Workspace: …"` | `:68-70` |
| Root marker 归属不符 | `f"Project Root marker owner mismatch: {self.marker_path}"` | `:206` |
| 消息库 owner 冲突 | `"Project message database owner conflict"` | `core_cluster/registry.py:380` |

### 6.3 skill lock

| 场景 | 消息原文 | 行号 |
| --- | --- | --- |
| schema 不符 | `f"lock schema={schema!r} 与当前 {LOCK_SCHEMA_VERSION} 不符（需显式重建，不自动改写）: {lock_path}"` | `scheme/loading/skill_lock.py:189-193` |
| 缺 provenance | `"lock 缺少 provenance 映射: {lock_path}"` | `:201`（同 `ghrah/skills/lock.py:69`） |
| YAML 坏 | `"lock YAML 解析失败: {exc}"` | `:165` |
| 顶层非映射 | `"lock 顶层不是 YAML 映射"` | `:167` |
| 降级却写摘要 | `"lock 标记降级却写了摘要（拒绝半可信值）: {lock_path}"` / `"lock 未标记降级但摘要缺失: {lock_path}"` | `:221`、`:223` |

### 6.4 未初始化

大量 unit 在 `initialize` 前调用会抛 `"<X>Unit has not been initialized."`：`core_cluster.py:157`、`manifest_store.py:96/102/143`、`project.py:81`、`recovery.py:57/116`、`room.py:57`、`sandbox.py:38`、`task.py:54`、`taskstore.py:84`、`websocket_observer_endpoint.py:99/105/111/117`、`workspace.py:112/118`。

各 Store 未 `start()` 时抛 `"… not started. Call start() first."`：`project/store.py:219`、`task/store.py:158`、`room/store.py:145`、`workspace/store.py:118`、`recovery/desired_state.py:152`、`sandbox/executor.py:145`。

## 7. 前端

| 项 | 值 | 位置 |
| --- | --- | --- |
| dev 端口 | `5173` | `packages/observer-web/vite.config.ts:14-16` |
| 端口占用 | 硬失败（start_all 传 `--strictPort`，`start_all.py:279`） | — |
| 构建产物 | 默认 `dist/`（`vite.config.ts` 全文无 `build.outDir`；当前仓库内 `packages/observer-web/dist` 不存在，即未构建） | — |
| dev proxy | **无**（无 `server.proxy`） | — |
| WS 目标 | `import.meta.env.VITE_GHRAH_SUBJECT_WS_URL ?? __GHRAH_SUBJECT_WS_URL__ ?? "ws://localhost:4112/ws"` | `packages/observer-core/src/stores/connection.ts:9-11` |

start_all 通过写 `.env.local` 注入 `VITE_GHRAH_SUBJECT_WS_URL`，退出时删除（`start_all.py:52-53`、`:213-231`）。

## 8. 排障手册

| 现象 | 首查 | 处置 |
| --- | --- | --- |
| `端口 4112 已被占用` | 上一次进程未退出 | 按消息里的 `ss -ltnp \| grep 4112` 找到并停止；或 `--subject-port` 换端口 |
| `node … 过旧` | Node < 22 | `nvm use 24`；pnpm 11 依赖 `node:sqlite` |
| `observer-web 包不存在` | 仓库结构损坏/在错误目录运行 | 确认 `ghrah-observer-webui/packages/observer-web` 存在 |
| `Subject 进程提前退出（code=1）` | 看 Subject 自身日志（start_all 只转发） | 常见根因：schema 不兼容（§6.2）、端口竞争、`.ghrah` 权限 |
| 启动卡住 ~10s 后失败 | `aiosqlite` DEBUG 洪水 | `GHRAH_SUBJECT_LOG_LEVELS="ghrah=DEBUG,aiosqlite=WARNING"` |
| `Legacy context database schema detected` | `~/.ghrah/data/ghrah.db` 版本旧 | 按消息要求**显式删除**后重建（代码故意不自动迁移） |
| 技能不生效 / `lock schema='1' 与当前 2 不符` | skill lock 版本 | 用 `relock` 重建；不要手改 lock（§6.3） |
| 观察端断线后丢事件 | 事件是内存 ring（容量 1000） | 提高 `GHRAH_SUBJECT_SERVER_EVENT_REPLAY_CAPACITY`；或接受重连重建 |
| 工作区文件被拒 | `CoreUnitConfig.workspace_root` 未设 | 见 `ghrah-01-ability-design-and-implementation.md` §11.1 判定链 |

## 9. 已知偏差

| 编号 | 偏差 | 证据 |
| --- | --- | --- |
| D-OPS-1 | `units/__init__.py:31-35` docstring 称 full=15 且含 `ability_bridge`；实际无该文件、实际 full=14 | `units/__init__.py:94-108` |
| D-OPS-2 | `ledger` 用 `ProjectPaths.action_chain_db_path`，与 `config.core_db_path` docstring 声称共享推导不符 | `units/ledger.py:88` vs `subject/config.py:391-399` |
| D-OPS-3 | 日志无轮转，`FileHandler` 无界增长 | `logging_setup.py:86` |
| D-OPS-4 | 健康检查仅 TCP 探活，`/health` 从未被调用 | `start_all.py:142-150` vs `websocket_observer_endpoint.py:208` |
| D-OPS-5 | 库默认监听 `0.0.0.0`，被 start_all 覆盖为回环；直接跑 server 命令会暴露到全网 | `server/config.py:23` vs `start_all.py:259` |
| D-OPS-6 | 无 lockfile 一致性校验，`native_locks` 只是声明 | `ghrah.toml [workspace]` |