# 能力系统代码地图

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-MAP-006 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `f8ff157`（2026-10-28），`ghrah-core` 0.2.0a0 |
| 读者 | 刚接手仓库要"从哪个文件开始读"的人、要做改动前想确认影响面的人、要新增文件不知道放哪的人 |
| 取代 | `能力系统代码地图.md`、`能力协议层调用依赖分析.md`（归档至 `docs/archive/`） |
| 相关文档 | `ghrah-01-ability-design-and-implementation.md`（契约细节）、`ghrah-03-drive-loop.md`（循环）、`ghrah-04-skill-system.md`（技能）、`ghrah-05-build-startup-and-troubleshooting.md`（运行） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。以文件为单位重建索引；补入协议层结构与"一次调用的七步链"；修正三处旧文档事实错误（`load_tool_list` 生产零调用、`ability_protocol` 在 core 侧、事件驱动落 ledger 已废） |

## 1. 怎么用这份地图

三条读法，按目的选：

| 目的 | 从哪里开始 |
| --- | --- |
| 搞懂"一个能力怎么被执行" | §4 的七步链，再跳到 `ghrah-01-ability-design-and-implementation.md` §9 |
| 要改一个文件 | §2 目录树 → §3 文件职责表 → §7 依赖方向（确认不会反向依赖） |
| 要新增文件 | §6 决策树（先判断它属于哪个面） |

路径缩写：`scheme/...` = `ghrah-monorepo/ghrah-core/src/ghrah/scheme/...`；`core/...` = `.../ghrah/core/...`；`subject/...` = `ghrah-monorepo/ghrah-subject/src/ghrah/subject/...`；`protocol/...` = `ghrah-monorepo/ghrah-protocol/src/ghrah/protocol/...`。

## 2. 仓库结构

```
ghrah-monorepo/
├── ghrah-protocol/        19 个 py / 2669 行   wire 契约（唯一权威）
├── ghrah-scheme/           9 个 py / 1891 行   契约格式（local-contract / catalog）
├── ghrah-core/           169 个 py / 27354 行  认知层：循环、能力、上下文、协议抽象
├── ghrah-subject/         82 个 py / 12799 行  效果层：HTTP/WS 服务、store、unit 装配
├── ghrah-plugin/           8 个 py /  802 行   插件发现与校验（**未发布**）
├── ghrah-taskstore/        7 个 py / 1288 行   任务归因存储（**未发布**）
├── ghrah-observer-webui/                       WebUI（vite + TS）
├── scripts/                                    start_all / 文档检查 / 发布干跑
└── docs/                                       仓库自带的模块清单与迁移记录（历史证据）
```

发布面（`publish.yml` 白名单）：`py/ghrah-core`、`py/ghrah-protocol`、`py/ghrah-subject`、`npm/ghrah-protocol`、`npm/ghrah-observer-core`、vscode 扩展。`ghrah-plugin` 与 `ghrah-taskstore` 未列入。

### 2.1 `ghrah-core/src/ghrah/scheme/` 全量（74 个 py）

```
scheme/
├── __init__.py              对外门面（延迟导出，scheme/__init__.py:91 导 load_tool_list）
├── _cluster_common.py
├── paths.py                 ABILITY_PATH_SPECS / extract_paths / is_subpath / resolve_*
├── kinds.py                 FS_ABILITY_TYPES(7) / CLUSTER_ABILITY_TYPES(7)
├── errors.py                AbilityError / AbilityNotFoundError
├── agent_manifest.py        旧 manifest 形态（agent 侧）
├── agent_resolver.py        把 manifest 解析成 ResolvedAbility
├── ability/                 能力包（只有包，不含加载逻辑，scheme/__init__.py:19）
│   ├── cluster/operations/  broadcast_message query_agents query_manifests send send_message spawn_agent terminate_agent
│   ├── core/operations/     conversation end_task list_abilities load_skill recall_context session_info
│   ├── fs/operations/       delete_file edit_file list_directory move_file read_file search_files write_file
│   ├── shell/operations/    execute_command
│   └── mcp/operations/      mcp_call
├── execution/               执行面
│   ├── base.py              Ability 抽象基类（100 行）
│   ├── context.py           AbilityExecutionContext（门面 + 状态 API）
│   ├── execution_data.py    ExecutionData / DataKey
│   ├── execution_services.py ExecutionServices / ServiceKey
│   ├── execution_frame.py   ExecutionFrame（精简上下文，hook/循环共用）
│   ├── invocation.py        AbilityInvocation（frozen）
│   ├── executor.py          AbilityExecutor / LocalAbilityExecutor（802 行，执行主干）
│   ├── hooks.py             HookPoint(10) / HookScope / HookResult / Hook
│   ├── hook_runner.py       HookRunner（唯一执行路径）
│   ├── hook_store.py        HookStore（三桶索引）+ HookListView
│   ├── hook_context.py      HookContext（frame + invocation + result）
│   └── ability_build.py     instantiate_resolved_abilities（装配点 B）
├── loading/                 定义面 + 目录面 + 工具面 + 技能
│   ├── ability_schema.py    ability.yaml 的 pydantic 模型与跨字段校验
│   ├── ability_roots.py     扫描根与优先级
│   ├── ability_scan.py      全池扫盘 + 成员装配 + lock（952 行，最大文件）
│   ├── ability_catalog.py   内置树严格投影 + handler→类 + create_ability
│   ├── ability_view.py      AbilityManifest 视图模型
│   ├── catalog.py           scan_catalog 合并报告
│   ├── tool_list.py         工具面（生产零调用，见 §5.2）
│   ├── manifest_io.py       旧 manifest 文本解析（遗留层）
│   ├── manifest_fields.py   ManifestValidationError / PermissionFlags / merge
│   ├── skill_context.py     SkillCatalog / render_skill_catalog / read_skill_document
│   ├── skill_provider.py    SkillAbilityProvider（scan/get_skill/read_skill/verify/relock）
│   ├── skill_models.py      LockRecord / SkillFingerprint / BundleVerification / SkillDocument
│   ├── skill_lock.py        schema=2 lock 读写
│   ├── skill_fingerprint.py 整树指纹（tree/snapshot/entry 三层）
│   ├── skill_mcp.py         MCP 声明解析
│   ├── skill_errors.py      SkillContextError 等
│   └── skill_*              上述之外无遗漏
└── permissions/             权限与审批
    ├── fs_permissions.py    FSPermissionChecker + AccessApprovalHook
    ├── command_safety.py    CommandSafetyChecker + 4 张名单 + CommandApprovalHook
    └── hitl.py              HITLApprovalHook + HITLRequiredAbility
```

### 2.2 `ghrah-core/src/ghrah/` 其他关键目录

| 目录 | 关键文件 | 作用 |
| --- | --- | --- |
| `agents/` | `base.py`（2123 行，`ActorAgent`）、`builder.py`、`iteration_state.py` | 驱动循环（`ghrah-03-drive-loop.md`） |
| `context/` | `manager.py`（ContextManager，写侧权威）、`compact.py`、`rebase.py`、`persistence/sqlite_backend.py`（678 行） | 上下文与落盘 |
| `core/` | `unit.py`（1662 行，`CoreUnit` + `CoreUnitConfig` + 装配点 A）、`ability_protocol.py`、`events.py`、`hitl.py` | 内核装配 |
| `communication/` | `supervisor.py`、`message_manager.py` | 路由与消息持久化 |
| `skills/` | 11 个文件 | **旧**技能实现（见 `ghrah-04-skill-system.md` §2） |
| `types/` | `config_types.py`、`results.py` | `AgentConfig`/`WindowConfig`/`ActionResult` |
| `chat/` | `format/{__init__,openai,anthropic}.py` | LLM 工具面下发 |

### 2.3 `ghrah-subject/src/ghrah/subject/`

| 目录 | 关键文件 | 作用 |
| --- | --- | --- |
| `server/` | `app.py`、`config.py`、`server.py`、`event_bus.py`、`connection_manager.py`、`router.py` | HTTP/WS 服务（只有 3 条路由） |
| `units/` | `__init__.py`（unit 装配序）、`websocket_observer_endpoint.py`、`ledger.py`、`skill.py`、`core_cluster.py`、`project.py`、`room.py`、`recovery.py`、`sandbox.py`、`task*.py`、`workspace.py` | Ouroboros unit |
| `core_cluster/` | `registry.py` | CoreUnit 工厂与生命周期 |
| `ledger/` | `chain.py` | 只读投影（第二 WAL 连接） |
| `logging_setup.py` | — | 唯一日志实现 |

## 3. 文件职责速查

| 我要找… | 看这个文件 | 关键符号 |
| --- | --- | --- |
| 能力的 YAML 字段定义 | `scheme/loading/ability_schema.py` | `AbilityDefinition`、`OperationDefinition`、`ExecutionSpec`、`validate` |
| 扫描根怎么定 | `scheme/loading/ability_roots.py` | `builtin_pool`、`default_pool`、`AbilityRoot` |
| 扫盘为什么漏了我的包 | `scheme/loading/ability_scan.py` | `scan_ability_definitions`、`AbilityScanIssue`（`__str__` 就是错误行） |
| handler 为什么找不到类 | `scheme/loading/ability_catalog.py` | `ability_class`（要求恰好一个 `Ability` 子类且 `__module__` 匹配） |
| 工具描述为什么和 YAML 不一样 | `scheme/loading/ability_catalog.py` | `_project`（`SUMMARY` → `metadata.description`）vs `tool_list.py`（YAML `description` → 工具描述） |
| 一次调用为什么被拦 | `scheme/execution/executor.py` | `execute_ability`（PRE_EXECUTE → HITL → execute → POST_EXECUTE） |
| hook 为什么不生效 | `scheme/execution/hook_store.py` | `select`（三桶并集）、`is_legacy` |
| hook 为什么没短路 | `scheme/execution/hook_runner.py` | `run`（无 `break`，靠 `merge` 的 AND/OR） |
| 权限为什么允许了 | `scheme/permissions/fs_permissions.py` | `check_access`（五级判定） |
| 命令为什么要求审批 | `scheme/permissions/command_safety.py` | `classify_command`（DANGEROUS → SAFE_SUB → REQUIRE_HITL） |
| 审批为什么超时 | `scheme/execution/executor.py` | `handle_hitl_hook_result`（唯一 `wait_for`） |
| 循环为什么回滚 | `agents/base.py` | `_drive_loop` 的 `except` `:906-971` |
| 压缩为什么触发 | `core/context/manager.py` | `check_compact_trigger` `:1255-1273` |
| 技能为什么没注入 | `core/context/manager.py` | `_skill_catalog_message` `:1121-1161` |
| 技能名为什么调不到 | `scheme/loading/skill_provider.py` | `get_skill`（三级匹配） |
| 事件为什么没到前端 | `subject/server/event_bus.py` | `emit_core_event`（未归属事件丢弃 `:184-192`） |

## 4. 一次能力调用的七步链

以"模型决定调 `read_file`"为例，端到端：

| # | 发生什么 | 位置 |
| --- | --- | --- |
| 1 | 模型返回 `tool_calls`，循环取出 | `agents/base.py:1357`（`tool_calls = llm_response.tool_calls`） |
| 2 | 交给执行器 | `agents/base.py:1399-1405` |
| 3 | 并行执行 + 未知能力折 FAILURE + 取消重抛 | `scheme/execution/executor.py:429-534`（未知 `:470-479`、gather `:492-505`、取消 `:507-511`、异常 `:512-522`） |
| 4 | 单次执行：建上下文 → `execute_ability` | `:536-588`（上下文 `:568-584`、`:586`） |
| 5 | 结果转成工具消息入历史 | `agents/base.py:1516-1536`（`ChatMessage.tool` `:1526-1534`、`add_messages` `:1536`） |
| 6 | 提交迭代 → 发 `ActionChainUpdatedEvent` | `agents/base.py:1418-1429`（await_reply 分支）/ 普通路径 `:842-882` |
| 7 | 事件落库（写侧）与投影（读侧） | 写：`core/context/manager.py:875`（`commit_iteration_async`）；读：`subject/ledger/chain.py:42-51` |

`units/ledger.py:5-9` 明确：**旧的"事件驱动落库"面已死、已删除**。现在 Subject 只是只读投影，经 `get_chain_history` 重建。

## 5. 协议层与前端下发

### 5.1 `ghrah-protocol` 结构

顶层只有一行转发：`protocol/__init__.py:8` 的 `from ghrah.protocol.types import *`。门面 `types.py:23-27` 再导出 enums/envelope/factories/payloads/routing；`types.py:31` 定义 `Message = Envelope`。

19 个文件：`__init__`、`enums`、`envelope`、`factories`、`routing`、`scheme`、`types` + `payloads/{__init__,agent,entity,manifest,persist,plugin,project,room,session,system,task,workspace}`。

`Envelope`（`protocol/envelope.py:142`）字段：`type: str`（`:161`）、`payload: Any`（`:162`）、`request_id`（`:163`）、`timestamp`（`:164`）、`client_type`（`:165`）、`seq_id`（`:166`）；辅助方法 `known_type`（`:177`）、`as_command_type`（`:181`）；`PAYLOAD_MAP` 在 `:200` 之后按 `type` 注册 payload 模型。

**没有 wire 版本常量**：`WIRE|VERSION|PROTOCOL_` 在 `src` 下 0 命中。最接近"版本"的四处都不是协议版本：`scheme.py:32` 的 `format: Literal["ghrah.scheme.local-contract.v1"]`、`scheme.py:73` 的 `contract_version`、`scheme.py:83` 的 `"ghrah.scheme.catalog.v1"`、`payloads/plugin.py:41` 的 manifest `version`、`payloads/project.py:89` 与 `payloads/room.py:39` 的 `version: int = 1`（行级乐观锁）。

能力相关 payload（`protocol/payloads/agent.py`）：`AbilityDefinitionPayload`（`:88`）、`RegisterAbilityPayload`（`:99`）、`ExecuteAbilityPayload`（`:146`，`tool_args` `:155`）、`ActionChainUpdatedPayload`（`:194`）、`AbilityResultPayload`（`:225`）、`HITLRequestPayload`（`:243`，`tool_args` `:256`）、`HITLResponsePayload`（`:299`，`tool_call_id` `:318`，三元组校验 `:330`）、`HITLResolvedPayload`（`:339`）、`UnregisterAbilityPayload`（`:357`）。

工具契约面在独立模块 `protocol/scheme.py`：`ActionDescriptor`（`:18`，input/output/mutating/cancellable）、`ContractDescriptor`（`:29`）、`fingerprint()`（`:45`）、`DiscoveredAction`（`:55`）、`DiscoveredResource`（`:67`）、`CatalogSnapshot`（`:80`）。

### 5.2 工具面：两套入口，只有一套在生产用

| 入口 | 位置 | 生产使用 |
| --- | --- | --- |
| `load_tool_list()`（从 `ability.yaml` 建 schema） | `scheme/loading/tool_list.py:107` | **零调用**（仅 `tests/scheme/loading/test_tool_list.py` 与 `plans/selfcheck/query_all_abilities.py:78`、`:578`） |
| `Ability.bind_tool()`（各实现自己声明） | 声明 `core/ability_protocol.py:41`；实现如 `scheme/ability/core/operations/list_abilities.py:116`、`conversation.py:106`、`end_task.py:132`、`load_skill.py:112`、`cluster/operations/spawn_agent.py:95` | **生产链路** |

真实链路：`bind_tool()` → `agents/base.py:212` `register_ability` 收进 `_bound_tools` → `base.py:426`、`:454` 的 `self._llm.configure_tools(self._bound_tools)` → `chat/format/__init__.py:84` → `openai.py:93`（`api_kwargs["tools"] = self._format_tools(...)`）、`:95`（`client.chat.completions.create(**api_kwargs)`），`_format_tools` 在 `openai.py:222`；Anthropic 同构于 `anthropic.py:90` → `:97`。

注意两者不是"各管一半"：`bind_tool()` 的返回值直接决定**该工具是否出现在工具面**（返回 `None` 即不可见），并且它的 `description` / `parameters` 才是模型真正读到的那份说明书。定义面的 `tool_list` 投影只是并行存在、仓内零调用的另一条路径。详见 `ghrah-01-ability-design-and-implementation.md` §8.4（含 18/19 漂移实测）；两份都由人写、没有一致性校验（D-14）。

### 5.3 前端取 schema 与事件下行

| 环节 | 位置 |
| --- | --- |
| 前端拿工具 schema | 走命令面：`subject/manifest_store/service.py:180-203` 的 `manifest_resolve_agent` 返回 `abilities[].tool_schema`（`:193-197`）；命令枚举 `protocol/enums.py:115` `MANIFEST_LIST_ABILITIES` |
| 订阅 | `subject/units/websocket_observer_endpoint.py:146-148` 对 `EventType` 全集显式订阅 `core:{type}` 与 `event/{type}` |
| 转发 | `:150-158` 构造 `Message` → `subject/server/event_bus.py:165-201` 的 `emit_core_event`（**未归属 agent 的事件丢弃** `:184-192`） |
| 广播/单发 | `subject/server/connection_manager.py:204` broadcast / `:226` send_json；单发 `:241` → `:253` |
| 挂载 | `websocket_observer_endpoint.py:207` 的 `app.websocket(config.ws_path)` |
| 握手/续传 | `subject/server/server.py:82`（`client_id`）、`:86-97`（`last_seq_id`） |
| 前端消费 | `packages/protocol/src/client.ts:40`、`:195`；`observer-core/src/stores/action-chains.ts:18`（`eventType = "action_chain_updated"`） |

### 5.4 TS 侧 `packages/protocol` 的结构差异

文件：`builders.ts`、`client.ts`、`enums.ts`、`identity.ts`、`index.ts`、`message.ts`、`payloads.ts` + `payloads/*.ts` + `scheme.ts`（各带 `*.spec.ts`，含 `protocol-align.spec.ts`）。

| 差异 | 说明 |
| --- | --- |
| TS 独有 `client.ts` | WS 传输层（`:40` `new WebSocket`、`:195` `last_seq_id` 续传 query）；Python 包无传输层，服务端在 `ghrah-subject/.../server/` |
| Python 独有 `factories.py` / `routing.py` | TS 以 `builders.ts` 承担构造、无 `routing.ts` |
| TS 独有 `identity.ts` | Python 无对应 |
| 一致性保障 | `protocol-align.spec.ts:158-217` 把 Python payload 名映射到 zod schema 强制 |
| 共同缺口 | 两侧都没有 wire 版本常量 |

Python 侧对应 schema：`payloads/agent.ts:17`（`AbilityDefinitionPayloadSchema`）、`:111`（`ExecuteAbilityPayloadSchema`）、`:333`（`AbilityResultPayloadSchema`）；导出 `index.ts:44-47`、`:114-115`。

## 6. Subject 内嵌 CoreUnit

| 环节 | 位置 |
| --- | --- |
| 默认工厂 | `subject/core_cluster/registry.py:336` 的 `default_core_unit_factory(...)`；`:412` 的 `factory(cluster_id, project_id, project_root_locator)` |
| 持久化接线 | `:417-418` `persistence_factory` → `SqliteBackend`（Project 的 action-chain 库） |
| 配置 | `:420-429` 构造 `CoreUnitConfig(hitl_timeout / workspace_root / auto_approve_abilities / …)`；Core 侧定义 `core/unit.py:117` |
| 创建 | `core/unit.py:1653` 的 `create_core_unit(config)` |
| 事件桥 | `core/unit.py:635-638` 把宿主 `ctx.emit` 装进 `CoreEventEmitter`；`:796` 发 `core:{event_type}` |
| 取实例 | `subject/units/core_cluster.py:124`（meta 声明 requires `MANIFEST_STORE`/`WORKSPACE_SERVICE`/`SANDBOX_EXECUTOR`、provides `CORE_CLUSTER_REGISTRY` `:145-146`）→ `registry.ensure_cluster` `:241-294` 返回 `CoreUnitHandle`（`:289`）；已挂载用 `get_handle`（`:296`） |
| 消息管理器复用 | `registry.py:306-313`——同一 Project 共享 `MessageManager`，注释明确"禁止另开第二个管理器" |
| 生命周期 | `:253` 加 per-cluster 锁 → `:272` 调工厂 → `:275` 若工厂有 `prepare` 先调（`:376-399` 打开 Project sqlite + MessageManager，写 `unit._config.message_manager`）→ `:276` `ctx.plugin(mount_unit(unit))` → `:277` `wait_active(10s)` → 失败 `:278-288` 回收 fiber 并 release |
| 拆解 | `:315-328` `shutdown_cluster`（`handle._alive=False` → `fiber.dispose` → release）、`:330-333` `registry.stop()` 全量 |

## 7. 依赖方向（改文件前必须确认）

正确方向（箭头 = import）：

```
ghrah-protocol  ←  ghrah-core  ←  ghrah-subject
                        ↑
                   ghrah-scheme（契约格式）

scheme/* 内部：
  loading/ability_schema  ←  loading/ability_scan  ←  loading/ability_catalog
        ↑                        ↑                        ↑
  loading/ability_roots    loading/catalog          scheme/__init__
  execution/* 只依赖 ghrah.types.results、ghrah.core.supervisor_protocol、
  ghrah.core.ability_protocol（execution_data.py 与 invocation.py 是零项目依赖的叶）
```

禁止的方向：

| 禁止 | 原因 |
| --- | --- |
| `scheme/execution/*` import `scheme/loading/*`（除 `ability_build.py`） | 执行面不应知道"能力怎么被发现"；`ability_build.py:36` 是唯一例外（它就属装配层） |
| `scheme/*` import `ghrah.core.unit` | 会形成 core ↔ scheme 环；unit 通过 `create_ability` 反向调用 |
| `ghrah-core` import `ghrah-subject` | 分层倒置（认知层不得依赖效果层） |
| `ghrah-protocol` import 其他仓内包 | 它是 wire 契约的唯一权威，必须零内部依赖 |
| `scheme/ability/<包>/operations/*.py` import `loading/*` | 实现脚本只依赖 `execution/base`、`permissions/*`、`types/*`；装配由外部完成 |

`scheme/ability_build.py:33` 引 `ghrah.core.ability_protocol`，`execution/context.py:17` 引 `ghrah.core.supervisor_protocol`——这是 core 提供"协议（Protocol 类）"、scheme 实现它，不是反向依赖。

## 8. 新增文件放哪

| 你要加的东西 | 放这里 | 不要放 |
| --- | --- | --- |
| 一个新能力（工具） | `scheme/ability/<容器>/operations/<key>.py` + 在 `<容器>/ability.yaml` 加成员 | 不要新建 `abilities/` 目录，不要写 `manifest: ability` |
| 一个新能力容器 | `scheme/ability/<新容器>/{ability.yaml,operations/,skills/}` | 不要在 Python 里注册容器 |
| 一个技能包 | 用户根 `~/.ghrah/skill-ability/<容器>/`（仓库内不打包技能，见 `04` §3.2） | 不要放 `scheme/ability/` 下（会用内置 rank 0 锁死，用户无法覆盖） |
| 一个新 Hook | `scheme/permissions/`（权限类）或调用方所在包（编排类） | 不要放 `execution/hooks.py`（那里只有契约与枚举） |
| 一个新 HookPoint | `scheme/execution/hooks.py` 的 `HookPoint` | 不要在业务代码里用裸字符串 |
| 一个新权限检查器 | `scheme/permissions/<name>.py`，并在 `ability_build.py` + `unit.py` 两个装配点都接上 | 只改一处装配点（见 D-2） |
| 一个新 payload | `protocol/payloads/<域>.py` + `PAYLOAD_MAP` 注册 + TS 侧 `payloads/<域>.ts` + `protocol-align.spec.ts` 映射 | 不要只改一侧 |
| 一个新命令 | `protocol/enums.py` 枚举 + `subject/router.py` 或对应 unit 的 handler | 不要在 server 里加 HTTP 路由（当前只有 3 条，加路由要同时改 WebUI 通信模型） |
| 一个上下文变换 | `core/context/` | 不要在 `agents/base.py` 里堆逻辑 |
| 一次性的排查脚本 | 仓库根 `plans/`（被 `.gitignore` 整体忽略） | 不要放 `docs/` 或包内 |

## 9. 关键常量索引

| 常量 | 值 | 位置 |
| --- | --- | --- |
| `SCHEMA_VERSION`（定义面） | `"0.1-draft"` | `scheme/loading/ability_schema.py:54` |
| `FORMAT` | `"ghrah.ability-definition"` | `:53` |
| `DEFINITION_FILE_NAME` | `ability.yaml` | `:57` |
| `SKILL_BODY_DIR` / `SKILL_FILE_NAME` | `skills` / `SKILL.md` | `:60`、`:63` |
| `LOCK_FILE_NAME` | `ability.lock.yaml` | `:66` |
| `BUILTIN_ROOT_RANK` / `USER_ROOT_RANK` | `0` / `100` | `scheme/loading/ability_roots.py:52`、`:63` |
| `REGISTRY_ONLY_OPERATIONS` | `frozenset({"send"})` | `scheme/loading/ability_catalog.py:81` |
| `LEGACY_MANIFEST_VERSION` | `"1"` | `:78` |
| `FS_ABILITY_TYPES` | 7 个 fs handler | `scheme/kinds.py:18-28` |
| `CLUSTER_ABILITY_TYPES` | 7 个 cluster handler（**无消费者**） | `:31-41` |
| `ABILITY_PATH_SPECS` | 8 条路径规格 | `scheme/paths.py:42-53` |
| `AGENT_OWNER` | `"__agent_hooks__"` | `scheme/execution/hook_store.py:61` |
| `LOCK_SCHEMA_VERSION` | `1`（旧）/ `2`（新） | `skills/lock.py:43` / `scheme/loading/skill_lock.py:59` |
| `MANIFEST_MAGIC` | `"ghrah-skill-tree-v1"` | `scheme/loading/skill_fingerprint.py:79` |
| `CATALOG_DESCRIPTION_MAX_CHARS` | `500` | `scheme/loading/skill_context.py:60` |
| `DEFAULT_WINDOW_MAX_TOKENS` | `200_000` | `core/types/config_types.py:12` |
| `_MAX_EMERGENCY_HALVINGS` | `4` | `agents/base.py:120` |
| `DEFAULT_SUBJECT_PORT` / `DEFAULT_WEBUI_PORT` | `4112` / `5173` | `scripts/start_all.py:55`、`:56` |

## 10. 公认的文档陷阱

这些是旧文档里写错、但看起来很像真的说法，改文档时不要复述：

| 旧说法 | 事实 |
| --- | --- |
| "有 `ghrah-toolchain/` 提供命令行" | 该目录不存在；命令面来自 `[project.scripts]` + `scripts/*.py` |
| "日志实现在 `ghrah-core/src/ghrah/logging/`" | 该目录不存在；唯一实现在 `subject/logging_setup.py` |
| "装配链统一调 `load_tool_list()`" | 生产零调用；实际走 `bind_tool()`（§5.2） |
| "`ability_protocol` 在 `ghrah-protocol` 包里" | 在 `ghrah-core/src/ghrah/core/ability_protocol.py` |
| "`action_chain_updated` 事件驱动落 ledger" | 已废（`subject/units/ledger.py:5-9`、`subject/ledger/chain.py:12`）；现在只是只读投影 |
| "full profile 挂 15 个 unit，含 `ability_bridge`" | 无该文件，实际 14（`05` §3.5） |
| "存在全局能力注册表 / 挂载表" | 只有 manifest 解析 + `RegistryProtocol` 抽象（`core/ability_protocol.py:49`） |
| "协议有 wire 版本常量" | 不存在（§5.1） |