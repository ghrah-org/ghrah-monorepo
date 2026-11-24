# 能力系统代码地图（按层）

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-MAP-006 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `a742fcf`（2026-11-23），工作区干净 |
| 组织方式 | 按「层 → 目录 → 职责 → 关键符号」组织，不逐文件列清单；行号只做少量当前指针 |
| 读者 | 想知道"改这件事该动哪一层"的人、要新增文件不知道放哪的人、排查"授权/工具面/数据从哪来"的人 |
| 取代 | 本文件 1.0（逐文件索引版，主体路径已失效；自相矛盾处已剔除） |
| 相关文档 | `ghrah-01`（契约）、`ghrah-03`（循环）、`ghrah-04`（技能）、`ghrah-05`（构建与启动）、`ghrah-11`（member）、`ghrah-12`（包结构）、`ghrah-13`（数据底座） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。以文件为单位建立索引 |
| 2.0 | 2026-11-23 | 整篇重写为按层能力地图：能力实现全部收敛到 `ghrah-core/src/ghrah/ability/`（access/loading/surface/execution/package/builtin）；重画包级与层内依赖；补数据地图（四库一安装根）与两段调用链；删除旧目录、旧工具清单投影、旧模板/清单服务、单体启动脚本等失效内容 |

## 1. 怎么用这份地图

| 你的目的 | 从这里开始 |
| --- | --- |
| 搞懂"一个能力怎么从模板变成可调用工具" | §8.1 创建/启动段，再回 §3 找对应层 |
| 搞懂"一次 tool_call 走到哪" | §8.2 运行段，再回 §3.4/§3.5 |
| 判断"这份数据谁是权威" | §7 数据地图 |
| 决定"新代码放哪个包" | §10 新增文件放哪 |
| 确认"改了会不会反向依赖" | §9 依赖方向 |

**状态三态**（本文件所有表格通用）：**现行** = 本基线在跑；**计划** = 已设计未实现，文中直接写"未实现"；**历史** = 已删除，仅作考古。

路径缩写：`ability/…` = `ghrah-core/src/ghrah/ability/…`；`core/…` = `ghrah-core/src/ghrah/core/…`；`subject/…` = `ghrah-subject/src/ghrah/subject/…`；`protocol/…` = `ghrah-protocol/src/ghrah/protocol/…`。全文默认相对仓库根。

## 2. 包与体量（实测）

统计口径：各包 `src/` 下的 `.py` 文件数（不含测试、不含 `__pycache__`）。

| 包 / 路径 | py | 角色 | 发布通道 |
| --- | --- | --- | --- |
| `ghrah-core/src/ghrah` | 271 | 认知层：循环、能力（`ability/`）、上下文、协议抽象 | PyPI `ghrah-core` |
| `ghrah-subject/src/ghrah/subject` | 105 | 效果层：HTTP/WS 服务、域存储、unit 装配、Core 端口消费 | PyPI `ghrah-subject` |
| `ghrah-protocol/src/ghrah/protocol` | 37 | wire 契约 + 模块端口契约（唯一权威） | PyPI `ghrah-protocol` |
| `ghrah-kernel/src`（包名 `ghrah_kernel`） | 33 | 应用微内核：跨进程句柄、结构化消息 SDK、期望状态监督者 | 未列入白名单 |
| `ghrah-plugin/src` | 8 | 插件 spec/发现/注册/协商/离线验证 | 未列入白名单 |
| `ghrah-taskstore/src` | 7 | 任务归因内核（tasks/claims/evidence 三表） | 未列入白名单 |
| `ghrah-scheme/src/ghrah/scheme` | 10 | 自用 Scheme 模型与控制原语（`authority/codec/contracts/models/runtime`） | 未列入白名单 |
| `ghrah-observer-webui/packages/*` | — | TS 观察端：`protocol`、`observer-core`、`observer-web`、`plugin-registry`、`mock-server` | npm / vscode 扩展 |

发布白名单（`publish.yml`）：`py/ghrah-core`、`py/ghrah-protocol`、`py/ghrah-subject`、`npm/ghrah-protocol`、`npm/ghrah-observer-core`、vscode 扩展。上表其余包没有发布通道，跨包引用时须自行确认可安装形态。

`ghrah-scheme` 是**独立契约包**（`protocol/scheme.py` 的格式模型 + 进程内控制原语），不是能力实现目录；Core 只在 `scheme` extra 下使用它（见 §3.8）。

## 3. 能力系统七层（`ability/`）

能力实现的唯一位置。旧的文件式能力目录已整体并入此处（历史，见 §12.1）。

```
ability/                        能力域根：来源装配与共享契约
├── assembly.py  source.py      来源装配（内置树 + 安装根，全名冲突即拒绝）
├── versions.py  member.py      版本捕获、成员身份与两轴
├── access/                     关系库：授权状态的唯一权威 + 启动核定
├── loading/                    定义面：扫描、校验、目录、技能
├── surface/                    工具面：投影、命名、搜索、元工具、预算
├── execution/                  执行面：executor、hooks、调用值对象
├── package/                    安装面：build / install / lock / provider
├── builtin/                    6 个内核件（随源码分发的能力实现）
└── permissions/ scripts/ tools/ scheme/     横切：权限、脚本、CLI、Scheme 适配
```

### 3.1 根：来源与共享契约

| 目录/文件 | 职责 | 关键符号 | 状态 |
| --- | --- | --- | --- |
| `assembly.py` | 能力来源模式；只接受 `builtin`，旧来源值直接拒绝 | `AbilitySourceMode`、`AbilityAssembly.select` | 现行（仅 builtin） |
| `source.py` | 联合登记内置树与安装根；同全名双源时**拒绝静默覆盖** | `BuiltinAbilitySource`、`AbilityOrigin`（`kind` 取 `builtin` / `installed`） | 现行 |
| `versions.py` | 一次装配固定的定义 + 实现摘要 | `AbilityVersion`、`capture_ability_version` | 现行 |
| `member.py` / `paths.py` | 成员身份与两轴；路径参数规格（8 条） | `Member`、`MemberAxes`、`ABILITY_PATH_SPECS`、`extract_paths` | 现行 |
| `content.py` / `contracts.py` | 内容字节捕获、摘要、分块读取；结果 schema 归一 | `capture_content`、`ContentReadRequest`、`operation_result_schema` | 现行 |
| `provider_contracts.py` / `file_*` / `local_content.py` | 后端契约（python/mcp）与文件型成员内容读取 | `contract_for`、`FileBackend`、`LocalGrantedContentProvider` | 现行 |
| `mcp_wiring.py` | MCP 部署接线：包/成员/技能绑定与刷新 | `McpDeployment`、`McpDeploymentConfig`、`AgentMcpService` | 现行 |
| `kinds.py` | 能力类型单一真源 | `FS_ABILITY_TYPES`(7)、`CLUSTER_ABILITY_TYPES`(7) | 现行 |
| `consumer.py` | 经 Scheme 读内容（可选接入，需 `scheme` extra） | `GrantedContentProvider` | 现行（可选） |

### 3.2 `access/` — 授权关系库（唯一写侧权威）

| 文件 | 职责 | 关键符号 | 状态 |
| --- | --- | --- | --- |
| `store.py` | 关系库门面：schema v5、**8 张数据表** | `AbilityAccessStore`、`startup_registration`、`admit`/`admit_tool`、`commit_version`、`grants`（内部 `_SCHEMA:57`） | 现行 |
| `startup.py` | 启动核定器：模板成员 → 提交版本 → 落 grants → 绑定工具 → 产出工具表与工具面 | `StartupGrantMaterializer`、`materialize`、`tool_table_projector`、`ProjectedAbility`、`AuthorizedToolTable` | 现行 |
| `policy.py` | 部署方启动授权策略（包/成员/操作粒度，含撤销） | `GrantPolicy`、`GrantRule`、`GrantMode`、`GrantEffect` | 现行 |
| `facts.py` | 授权事实的四种来源（整包声明 / 技能作用域 / 已存记录 / 本次请求） | `GrantFacts`、`whole_container_facts`、`skill_scope_facts`、`stored_facts`、`request_facts` | 现行 |
| `reconcile.py` | 期望事实与库内事实的收敛；唯一批量写入口 | `apply_grants`、`desired_grants`、`GrantInstance`、`GrantReconcileError` | 现行 |
| `backends.py` | 按后端契约做参数级接纳（fs 路径、shell 命令、mcp、core 参数形状） | `BackendAdmission`、`admission_for`、`contract_of` | 现行 |
| `boundary.py` | 核定器的构造边界（Core 装配层唯一入口） | `build_startup_materializer` | 现行 |
| `migration.py` / `maintenance.py` | 存量 Agent 授权迁移；域检查与重置（备份后重建） | `migrate_agents`、`inspect_ability_domain`、`reset_ability_domain` | 现行 |
| `identity.py` / `errors.py` | 稳定 Agent ID 校验；拒绝码闭集 | `validate_agent_id`、`AccessError`、`TreeErrorCode` | 现行 |

关系库 8 张数据表（另有 `ability_tree_schema` 版本表，`version=5`）：

| 表 | 主键 | 语义 |
| --- | --- | --- |
| `ability_versions` | `(project,digest)` | 一次提交的确定版本：定义 JSON + 实现索引 + 资源清单 |
| `ability_content` | `(project,digest)` | 按内容摘要存字节（技能正文、附件、脚本） |
| `ability_instances` | `(project,id)` | 包级版本池：当前选中版本摘要与安装根 |
| `ability_agents` | `(project,id)` | Agent 的授权修订计数与存活标记 |
| `ability_grants` | `(project,agent,alias)` | 某 Agent 对某包的授予：成员集合、根、内容白名单、两轴、来源 |
| `ability_tools` | `(project,agent,name)` | 已绑定为常驻工具的成员（工具名 → alias/member/修订） |
| `ability_changes` | `seq` | 变更审计：修订、原因、操作者、版本摘要、策略 |
| `ability_registrations` | `(project,agent)` | 启动独占占位：token、状态、拥有修订（防并发重启互相覆盖） |

### 3.3 `loading/` — 定义面（只读扫描，不授予）

| 文件 | 职责 | 关键符号 | 状态 |
| --- | --- | --- | --- |
| `ability_schema.py` | `ability.yaml` 模型与跨字段校验；支持 `schema_version` `'1'`（两段映射）与 `'2'`（成员记录列表） | `AbilityDefinition`、`OperationDefinition`、`SkillDefinition`、`SUPPORTED_SCHEMA_VERSIONS`、`DEFINITION_FILE_NAME`、`LOCK_FILE_NAME` | 现行 |
| `ability_roots.py` | 扫描根与优先级 | `AbilityRoot`、`builtin_pool`、`default_pool`、`BUILTIN_ROOT_RANK=0`、`USER_ROOT_RANK=100` | 现行 |
| `ability_scan.py` | 目录池全量扫盘、成员装配、技能指纹 | `scan_ability_definitions`、`scan_abilities`、`ensure_locks`、`verify_skill`、`AbilityScanIssue` | 现行 |
| `ability_catalog.py` | 内置树严格投影：handler → 实现类，工具 schema | `ability_class`、`create_ability`、`builtin_handlers`、`build_tool_schema` | 现行 |
| `ability_view.py` / `catalog.py` | 运行时视图与对外合并报告 | `AbilityManifest`、`scan_catalog`、`CatalogReport` | 现行 |
| `skill_context.py` | 技能目录渲染、每轮提示、正文读取 | `SkillCatalog`、`render_skill_catalog`、`next_skill_reminder`、`read_skill_document` | 现行 |
| `skill_provider.py` | 技能扫描/读取/校验/重锁的服务门面 | `SkillAbilityProvider` | 现行 |
| `skill_lock.py` / `skill_fingerprint.py` / `skill_models.py` | `ability.lock.yaml` 读写（schema 2）与整树指纹 | `LOCK_SCHEMA_VERSION=2`、`LockRecord`、`SkillFingerprint` | 现行 |
| `manifest_fields.py` | 旧 manifest 字段兼容与错误类型 | `ManifestValidationError`、`PermissionFlags` | 现行（兼容层） |

### 3.4 `surface/` — 工具面（每轮从关系库单向重建）

| 文件 | 职责 | 关键符号 | 状态 |
| --- | --- | --- | --- |
| `runtime.py` | 工具面运行时：刷新、元工具路由、调用校验、绑定集合、审计 | `ToolSurfaceRuntime`（`refresh:203`、`invoke:405`、`validate_call`、`set_member_bound`）、`RunScriptTool` | 现行 |
| `assembly.py` | 静态集/长尾/元工具与 token 预算的装配 | `assemble_surface`、`assemble_agent_surface`、`SurfaceAssembly`、`SurfaceThresholds` | 现行 |
| `projection.py` | 授权视图 → 表面条目（唯一过滤口径：已授予 + 已选择 + 可暴露 + 未撤销） | `project_grants`、`project_surface` | 现行 |
| `catalog.py` | Agent 目录读面（工具 + 技能） | `SurfaceCatalog`、`SurfaceTool`、`read_agent_catalog` | 现行 |
| `naming.py` | 工具名归一与保留名 | `assign_tool_names`、`META_TOOL_NAMES:39`、`RESERVED_TOOL_NAMES` | 现行 |
| `search.py` / `meta.py` | 工具检索索引；五个元工具的输入/输出模型 | `AgentToolSearch`、`MetaToolAbility` | 现行 |
| `types.py` / `config.py` | 表面条目值对象；表面阈值默认值 | `SurfaceEntry`、`MemberRef`、`SurfaceConfig` | 现行 |

### 3.5 `execution/` — 执行面

| 文件 | 职责 | 关键符号 | 状态 |
| --- | --- | --- | --- |
| `executor.py` | 执行主干：并行 tool_calls、单次执行、HITL 等待与归因 | `LocalAbilityExecutor`（`execute_ability:308`、`execute_routed:452`、`execute_tool_calls:462`、`handle_hitl_hook_result`） | 现行 |
| `hooks.py` / `hook_runner.py` / `hook_store.py` | Hook 契约（10 个 HookPoint）、唯一执行路径、三桶索引 | `HookPoint`、`HookResult`、`HookRunner`、`HookStore` | 现行 |
| `invocation.py` | 调用值对象（身份只来自受信装配，不从参数取） | `OperationCall`、`MemberReference`、`InvocationIdentity` | 现行 |
| `execution_services.py` / `loop_data.py` | 执行期服务表与循环共享数据 | `ExecutionServices`、`ServiceKey`、`LoopData` | 现行 |
| `base.py` | Operation 抽象基类 | `Ability`（对外别名 `Operation`） | 现行 |
| `ability_build.py` | 已解析能力 → 实例（装配点） | `instantiate_resolved_abilities` | 现行 |

### 3.6 `package/` — 安装面（作者侧 → 安装根）

| 文件 | 职责 | 关键符号 | 状态 |
| --- | --- | --- | --- |
| `generate.py` | 由源码树生成并校验 `ability.yaml` 成员索引 | `build`、`check`、`render`、`BuildResult` | 现行 |
| `introspect.py` | 从脚本读出 `Input`/`Output`/summary（不执行入口） | `read_declaration`、`Declaration` | 现行 |
| `intake.py` | 目录/归档归一为可安装源 | `normalize_source`、`IntakeResult` | 现行 |
| `archive.py` | 归档解包与限额（条目数/大小/链接拒绝） | `extract_package`、`ArchiveLimits` | 现行 |
| `installer.py` | 安装/卸载/校验/刷新，写安装记录 | `AbilityInstaller`、`InstallResult`、`VerifyResult`、`InstallOrigin` | 现行 |
| `lock.py` | `<version>.lock.yaml`（安装记录）读写 | `InstallLock`、`render_lock`、`read_lock`、`lock_filename` | 现行 |
| `installed_store.py` | 安装根只读枚举（不写盘、不跟随链接） | `InstalledStore`、`InstalledVersion` | 现行 |
| `provider.py` | 安装包运行期提供器：Python 操作与 MCP 快照 | `InstalledAbilityProvider`、`PythonOperation`、`McpSnapshotOperation` | 现行 |
| `paths.py` / `digest.py` / `schema.py` | 可移植路径校验、整树摘要、schema 元字段剥离 | `check_relative_path`、`tree_digest`、`strip_schema_metadata` | 现行 |
| `sources/mcp.py` | MCP 源探测与快照安装/刷新 | `install_mcp`、`refresh_mcp` | 现行 |

### 3.7 `builtin/` — 6 个内核件

目录形态固定：`builtin/<包>/{ability.yaml,operations/,skills/}`。共 **26 个 operation + 1 个 skill**。

| 包 | operation | 成员要点 | 状态 |
| --- | --- | --- | --- |
| `fs` | 7 | `read_file`、`write_file`、`edit_file`、`move_file`、`delete_file`、`list_directory`、`search_files` | 现行 |
| `core` | 10 | `conversation`、`end_task`、`session_info`、`load_skill`、`recall_context`、`search_tools`、`describe_tool`、`execute_tool`、`bind_member`、`unbind_member` | 现行 |
| `cluster` | 7 | `spawn_agent`、`terminate_agent`、`query_agents`、`query_manifests`、`send`、`send_message`、`broadcast_message` | 现行 |
| `shell` | 1 | `execute_command` | 现行 |
| `ability-builder` | 0 | 1 个技能：`ability-package-development-guide`（能力包开发指南） | 现行 |

`core` 包的 5 个元工具是**固定通道**：工具面收窄后，模型仍能发现、描述、执行、绑定/解绑成员（见 §8.3）。`ability-builder` 只有技能成员，是"纯技能包"的样板。

### 3.8 横切层

| 目录 | 职责 | 关键符号 | 状态 |
| --- | --- | --- | --- |
| `permissions/` | 权限判定与 HITL 钩子：fs 五级、shell 分类、命令作用域、写投影 | `FSPermissionChecker`、`CommandSafetyChecker`、`HITLApprovalHook`、`resolve_effective_permission`、`try_workspace_write_approval` | 现行 |
| `scripts/` | `run_script` 的进程外入口（不走 Operation 成员契约） | `resolve`、`runner`、`process`（`Spawner`、`build_envelope`） | 现行 |
| `tools/` | 能力包 CLI：`python -m ghrah.ability.tools.cli <verb>`，7 个 verb + JSON 信封 schema 1 | `build`、`install`、`list`、`show`、`verify`、`refresh`、`uninstall` | 现行 |
| `scheme/` | 资源树 → 领域接口适配（`ghrah.scheme` 可选依赖） | `host.py`（`AccessRequest`/`Admission` 适配）、`consumer.py` | 现行（`scheme` extra） |

## 4. 其它 Core 域

| 目录 | 职责 | 关键符号 / 指针 |
| --- | --- | --- |
| `agents/` | 驱动循环（读 LLM → 执行工具 → 提交迭代） | `base.py`（`ActorAgent._drive_loop:872`、`execute_tool_calls:1537`）、`builder.py`（`AgentBuilder.from_config`、`default_llm_factory`）、`entity.py`、`runner.py` |
| `context/` | 上下文写侧权威（sqlite）、压缩与窗口 | `manager.py`（`ContextManager`）、`iteration_state.py`、`iteration_commit.py`、`window.py`、`compact.py`、`rebase.py`、`persistence/sqlite_backend.py`、`strategies/*` |
| `core/` | 内核装配（单元、端口门面、命令处理） | `unit.py`（`CoreUnit`、`CoreUnitConfig`、`create_core_unit`、`_materialize_builtin_abilities:1471`）、`api/facade.py`（`CoreFacade`、`create_assembly:615`）、`api/interfaces.py`、`api/runtime.py`、`api/catalog.py`、`api/handlers/*`（11 个域）、`wiring.py`（`build_core_port`、`build_core_catalog`）、`units/registry.py`（`RuntimeRegistry`） |
| `core/agents/` | 装配输入落地：`AgentAssemblySpec` → 运行时对象；实例快照 | `assembly.py`、`bindings.py`（`MemberBindingState`）、`instance_store.py`（`agent_instances`）、`members.py` |
| `core/read/` | 只读读面（供 Subject/观测端经端口消费） | `chain.py`、`ability.py`（`AbilityReadStore`）、`ability_admin.py`、`ability_catalog.py`、`ability_mcp_probe.py`、`mailbox.py` |
| `communication/` | 集群消息：路由、投递、邮箱 | `supervisor.py`、`message_manager.py`、`router.py`、`registry.py`、`mailbox_reader.py` |
| `llm/` `chat/` | 模型工厂与 wire 格式 | `llm/factory.py`；`chat/format/{openai,anthropic,deepseek,openai_responses}.py` |
| `mcp/` | MCP 客户端、会话、探测、凭据 | `client.py`、`runtime.py`、`session.py`、`probe.py`、`spec.py`、`identity.py` |
| `types/` | 配置与结果值对象 | `config_types.py`（`DEFAULT_WINDOW_MAX_TOKENS:12`）、`results.py`、`tokens.py` |
| `manifest/` | Agent/能力清单解析的**兼容投影层**（定义真源在 `ability/loading`） | `ability.py`、`agent.py`、`resolver.py`、`parser.py`、`protocols.py` |

## 5. Subject（`ghrah-subject/src/ghrah/subject/`）

### 5.1 `units/` 与 profile

21 个文件，每个 unit 一个文件。`mount_builtin_units` 按 profile 逐个挂载并等 ACTIVE。

| profile | 挂载 | 状态 |
| --- | --- | --- |
| `coexistence` | 基础 5 个：`sandbox`、`ledger`、`workspace`、`task`、`recovery(DesiredState)`；`taskstore` 启用时 +1 | 现行 |
| `full` | 再挂 13 个：`shipped_abilities`、`websocket_observer_endpoint`、`core_cluster`、`project`、`project_control`、`ability_control`、`agent_template_control`、`agent_control`、`agentconf_control`、`media_control`、`artifact_control`、`room`、`recovery`；`room_filter` 启用时 +1 | 现行 |

`taskstore` 目前挂在 builtin 链上（`config.taskstore.enabled` 控制），代码注明这属**待纠正偏离**：目标形态是插件（发现 + 信任闸 + Project 装配清单驱动），该目标**未实现**（计划）。

| unit | 职责 |
| --- | --- |
| `ability_control` | 能力管理 REST：树/授权/绑定/包目录/导入/校验/变更 |
| `agent_control` / `agentconf_control` / `agent_template_control` | Agent 生命周期、AgentConf 配置、模板 CRUD 与版本 |
| `artifact_control` / `media_control` / `project_control` | 产物只读面、附件库、项目读面 |
| `shipped_abilities` | 发行版预装能力包（`subject/shipped_abilities/`）的装配 |
| `core_cluster` | CoreUnit 工厂与生命周期（`core_cluster/registry.py`：`ability_store:429`、`build_config`） |
| `ledger` | 只读链投影，经 `CorePort` 取历史（见 5.3） |
| `websocket_observer_endpoint` | 观测端 WS 服务与事件转发 |

### 5.2 域服务

| 目录 | 职责 | 关键符号 |
| --- | --- | --- |
| `template_store/` | 模板域：单一 `agent_templates` 表（一行一版本）、`available_members` | `models.py`、`store.py`、`service.py`（`AgentTemplateService`）、`formats/v1.py` |
| `agents/` | 模板 → 装配输入：`render_assembly:235` 纯函数渲染；`create_agent:144` 走 `AgentAssemblySpec` | `creation.py`、`render.py`（`GrantPlan`、`RenderedAssembly`） |
| `project/` | 项目根、路径派生（`action_chain_db_path`）、实体、附件、隔离 | `paths.py`、`manager.py`、`store.py`、`entities.py`、`artifacts.py` |
| `room/` | 房间状态与投影（Core 回调写，Subject 持有） | `manager.py`、`store.py`、`content_blocks.py` |
| `task/` | 任务图与任务存储（`tasks.sqlite3`） | `graph.py`、`manager.py`、`store.py` |
| `workspace/` `sandbox/` `recovery/` | 工作区定位、执行沙箱、期望状态协调 | `locator.py`、`executor.py`、`reconciler.py` |
| `server/` | HTTP/WS 服务骨架、事件总线、连接管理（WS + 健康/元信息两条 HTTP 路由，另有一条已弃用别名） | `app.py`、`server.py`、`event_bus.py`、`connection_manager.py`、`router.py` |
| `runtime/` | 装配与运行时接缝：端口装配、Ouroboros 桥、插件挂载、路由注册 | `assembly.py`（`assemble_subject`）、`core_ports.py`、`ouroboros_bridge.py`、`plugin_mount.py`、`route_registry.py` |
| `shipped_abilities/` | 发行版预装能力包（`project_agent_manage`：4 个 operation + 共享模块） | `__init__.py` |
| `unit/base.py` | unit 契约（meta、路由、生命周期） | `SubjectUnit`、`UnitMeta`、`RouteSpec`、`CommandContext` |
| 顶层 | 入口、配置、日志 | `app.py`、`cli.py`、`config.py`（`AbilityConfig.install_root`）、`logging_setup.py`（唯一日志实现） |

### 5.3 ledger 现状

`units/ledger.py` 的 `LedgerUnit` 是**只读投影**：`chain_history` 命令经 `CorePort.sessions.history` 取链历史，**不自开数据库、不自派库路径**。事件驱动落库面已删除（历史，见 §12.1）。

## 6. Protocol（`ghrah-protocol/src/ghrah/protocol/`）

### 6.1 `interface.py` — 模块能力三件套

| 符号 | 职责 |
| --- | --- |
| `Interface` | 一条能力声明：名字 + 入参信封 + 出参模型 |
| `InterfaceRegistry` | 有序、唯一、可枚举的能力清单 |
| `InterfaceRuntime` | 模块内唯一运行时入口：校验入参 → 调 handler → 校验出参；清单与 handler 不一致启动即失败 |

### 6.2 `ports/` — 跨模块端口契约

| 文件 | 内容 |
| --- | --- |
| `ports/core.py` | `CorePort`（`517`）：10 个分组、**57 条操作**；`CoreCatalogPort`（`559`）：**8 条全局目录操作** + `aclose` |
| `ports/keys.py` | 注入键：`CORE_PORT`、`CORE_CATALOG`、`SUBJECT_PORT` |
| `ports/__init__.py` | 端口导出面 |

`CorePort` 的 10 个分组（方法数）：`nodes`(1)、`sessions`(8)、`branches`(5)、`mailbox`(6)、`deliveries`(3)、`abilities`(13)、`agents`(16)、`messages`(3)、`rooms`(1)、`manifests`(1)，合计 57。`CoreCatalogPort` 只读安装根与受信配置（包目录/详情/画像/MCP 探测/分组/导入），不打开数据库，因此零作用域时也可用。

### 6.3 `views/` — 出参 DTO

`views/core.py` 是装配与管理的形状权威：`AgentAssemblySpec`（`464`）、`AgentTemplateSpec`（`705`）、`AgentAssemblyMember`、`AgentSpawnSpec`、`AgentStartView`、`AgentConfigView`、`AbilityPackage*View` 等；`views/common.py` 提供 `Page`/`Operation`。

### 6.4 wire 面

| 模块 | 内容 |
| --- | --- |
| `ability_tree.py` | 能力树 wire：`AbilityTreeView`、`OperationNode`、`SkillNode`、`MemberAxes`、`BoundToolNode`、`GrantAbilityRequest`、`BindToolRequest` |
| `ability_control.py` | 能力管理面视图与请求：`AbilityAdminView`、`AbilitySurfaceView`、`AbilityGrantPolicyView`、`AbilityPackageCatalogView`、`AbilityMcpProbeView`、`AbilityPackageGroupsView` |
| `agent_control.py` | Agent/模板管理面视图与请求：`AgentSummaryView`、`AgentTemplateDetailView`、`AgentCreateRequest` |
| `agentconf_control.py` / `scheme.py` / `shell_allow.py` | AgentConf 面、`ghrah.scheme.*` 契约格式、shell 放行规则 |
| `envelope.py` / `enums.py` / `factories.py` / `routing.py` / `types.py` | 信封、枚举、构造、路由、转发门面 |
| `payloads/*` | 13 个域的 wire payload（agent/entity/content/manifest/model/persist/plugin/project/room/session/system/task/workspace） |

**判据**：跨模块调用只走 `ports/` 与 `views/`；`payloads/` 只服务观察端下发与历史命令面，不为新跨模块调用新增 payload。

## 7. 数据地图

四库一安装根。一句话前提：**文件系统是"提交来源"，不是运行期权威；运行期只读关系库与已提交字节**。

| # | 权威 | 位置 | 内容 | 写方 | 读方 |
| --- | --- | --- | --- | --- | --- |
| 1 | 链 + 能力关系库 | `<project_root>/db/action-chains.sqlite3` | 上下文（session/branch/messages/node）+ `ability_*` 8 表 | `context/manager.py`（链）；`AbilityAccessStore`（能力） | `LedgerUnit`（经 `CorePort`）、`AbilityReadStore`、`Subject` 管理面 |
| 2 | 实例快照 | `<project_root>/db/agents.sqlite3` | `agent_instances`：创建时冻结的配置、成员定义、成员轴（含 `bind`） | `core/agents/instance_store.py` | Core 恢复装配、工具面绑定镜像 |
| 3 | 模板库 | `~/.ghrah/subject.db` 的 `agent_templates` | 模板版本、`available_members`、模型/提示/工作区/权限申请 | `template_store/store.py` | `template_store/service.py`、创建线 |
| 4 | 安装根 | `<install_root>/<ns>/<name>/<version>/` + `<version>.lock.yaml` | 已安装能力件与**安装记录**（来源、文件摘要、mcp 快照） | `package/installer.py` | `package/installed_store.py`、`package/provider.py` |
| 5 | 归因库（可选） | `~/.ghrah/taskstore.sqlite3` | tasks / claims / evidence 三表 | `ghrah-taskstore` | Subject `taskstore` unit |
| 6 | 内容字节 | `ability_content`（按 digest） | 技能正文、附件、脚本；单次读取上限 65536 | 启动提交时捕获 | `read_chunk` / 内容读面 |

安装根的典型部署形态是 `~/.ghrah/<实例>/ability/<ns>/<name>/<version>/`；`install_root` 由部署方显式给出（`GHRAH_SUBJECT_ABILITY_INSTALL_ROOT`），领域层不拼默认值。内置树（`builtin/`）随源码分发，没有版本层与安装记录。

## 8. 一次调用的现行链路（两段）

### 8.1 创建/启动段

| # | 发生什么 | 位置 |
| --- | --- | --- |
| 1 | 模板渲染成装配输入（只含已授予/可授予成员） | `subject/agents/render.py:235` → `subject/agents/creation.py:144` |
| 2 | 装配输入过端口进 Core | `protocol/views/core.py:464` `AgentAssemblySpec` → `core/api/facade.py:615` `create_assembly` → `api/handlers/agents.py` |
| 3 | 解析成运行时对象并落实例快照 | `core/agents/assembly.py`、`core/agents/instance_store.py` |
| 4 | 启动核定：提交版本 → 落 grants → 绑定工具 → 产出工具表 | `core/unit.py:1471` → `ability/access/startup.py:601` `materialize`（`commit_version` + `reconcile.apply_grants`） |
| 5 | 工具面投影：本 run 的可见面由关系库重建，授权对象带 `_admission` | `startup.py:764` `tool_table_projector`、`startup.py:1106` `_admission`、`ability/surface/runtime.py:203` `refresh` |
| 6 | Agent 起跑，LLM 工具表装配完成 | `agents/builder.py` `AgentBuilder.from_config` |

### 8.2 运行段

| # | 发生什么 | 位置 |
| --- | --- | --- |
| 1 | 每轮先从关系库重建工具面（撤销/版本变更即时生效） | `agents/base.py:1379` → `ToolSurfaceRuntime.refresh` |
| 2 | LLM 返回 `tool_calls`，交给执行器并行执行 | `agents/base.py:1537` → `execution/executor.py:462` |
| 3 | 调用对象由装配者构造：成员引用与受信身份来自装配，不从参数或消息里取 | `agents/base.py` `_build_ability_call`（`OperationCall.identity`） |
| 4 | 接纳：在同一读事务里核对成员、版本、当前 run 与参数作用域；拒绝即返回拒绝码（不执行实现） | `startup.py:1106` `admit` → `access/store.py:1659` `admit_tool` |
| 5 | 执行：prepare → check → PRE_EXECUTE hook →（HITL）→ `execute` → POST_EXECUTE hook | `execution/executor.py:308` `execute_ability` |
| 6 | 结果转工具消息入历史；提交迭代并发布链更新事件 | `agents/base.py` `_add_tool_results`、`commit_iteration_async:1557`、`ActionChainUpdatedEvent` |

要点：没有"先执行再补授权"的路径——接纳发生在实现调用之前；HITL 是 Hook 结果的一个分支，不是独立执行阶段；撤权与版本变更在下一轮 `refresh` 即从可见面消失。

### 8.3 元工具是固定通道

`core` 包占 5 个保留名（`ability/surface/naming.py:39`）：`search_tools`、`describe_tool`、`execute_tool`、`bind_member`、`unbind_member`。它们只消费**当前目录**，目标调用仍走第 8.2 段的校验/接纳/审批：

| 元工具 | 作用 |
| --- | --- |
| `search_tools` | 在已授予集合里检索（不加载完整定义、不扩权） |
| `describe_tool` | 给出 schema/副作用/可用性，并按需把定义载入常驻集 |
| `execute_tool` | 按精确名与参数转调目标成员（路径解析后再走原执行器） |
| `bind_member` / `unbind_member` | 切换成员绑定轴；绑定集合的权威在实例快照，运行期只镜像 |

## 9. 依赖方向

### 9.1 包级

箭头 = "依赖"（右侧被依赖）：

```
ghrah-core            ──▶ ghrah-protocol
ghrah-subject         ──▶ ghrah-core, ghrah-protocol, ghrah-plugin, ghrah-taskstore
ghrah-scheme          ──▶ ghrah-protocol
ghrah-core[scheme]    ──▶ ghrah-scheme          （可选 extra，默认安装不含）
ghrah-taskstore       ──▶ ghrah-plugin          （插件基础设施）
ghrah-kernel          ──▶ 无仓内依赖；Core 只在 [agent] extra 下接入
ghrah-protocol        ──▶ ghrah-plugin          （唯一例外，见 9.4）
```

### 9.2 `ability/*` 层内节点

```
loading/ ──▶ package/            （扫描定义、捕获版本、安装记录）
execution/ ──▶ access/errors      （只借错误类型，不碰关系库）
surface/ ──▶ access/ + execution/ （读授权视图；转调执行器）
access/startup ──▶ loading + package + surface   （装配边界，唯一允许跨层）
builtin/* ──▶ execution/base + permissions + types （叶实现，不感知加载）
```

数据流单向：`安装根/内置树 →（启动提交）→ 关系库 →（每轮投影）→ 工具面 →（执行）→ 结果`。反向不存在第二条写路径。

### 9.3 禁止的方向

| 禁止 | 原因 |
| --- | --- |
| `execution/*` import `loading/*`（除 `ability_build.py`） | 执行面不应知道"能力怎么被发现" |
| `builtin/<包>/operations/*` import `loading/*`、`surface/*` | 操作只依赖执行契约与权限；装配由外部完成 |
| `access/` 之外再开一条写 grants/versions 的路径 | 关系库是授权唯一权威；管理端写也只经 store 的写方法 |
| Core 直接读 Subject 的库，或 Subject 直读 Core 的链库 | 只经 `CorePort` / 能力关系库服务面 |
| `ghrah-core` import `ghrah-subject` | 分层倒置（认知层不得依赖效果层） |
| `ghrah-protocol` import 其他仓内包 | 它是契约唯一权威，应保持叶子（例外见 9.4） |

### 9.4 既有例外：`protocol → plugin`

`protocol/payloads/plugin.py:15` 从 `ghrah.plugin.negotiator` 取 `NegotiationResult`，`payloads/plugin.py:33` 的 `PluginNegotiateResultPayload` 直接继承它。代价是三个可发布 PyPI 包全部传递依赖一个没有发布通道的包，TS 侧只能手抄同名字段。新增反向依赖不得再开第二条。

## 10. 新增文件放哪

| 要加的东西 | 放这里 | 不要放 |
| --- | --- | --- |
| 一个内核件 operation | `ghrah-core/src/ghrah/ability/builtin/<包>/operations/<key>.py`，并在同包 `ability.yaml` 加成员 | 不要新建 `abilities/` 目录，不要手改成员索引（用 `python -m ghrah.ability.tools.cli build`） |
| 一个新的内核件包 | `ghrah-core/src/ghrah/ability/builtin/<新包>/{ability.yaml,operations/,skills/}` | 不要在 Python 里注册容器 |
| 一个技能包 | 同上 `builtin/<包>/skills/<key>/SKILL.md`；用户可覆盖技能放用户根 | 不要只改锁文件；锁由扫描器生成 |
| 一个跨模块契约 | `ghrah-protocol`（能力名 `Interface` + `views/` 出参 + `ports/` 若需新端口） | 不要只改一侧（TS 侧同步 `packages/protocol` + 一致性测试） |
| 一个 Subject 业务域 | `ghrah-subject/src/ghrah/subject/<域>/`（manager/store/models 三件套）+ `units/` 里加 unit | 不要在 `server/` 里加 HTTP 路由 |
| 一个权限检查器 | `ability/permissions/`，并在装配点 `access/boundary.py` / `startup.py` 接上 | 只改一处装配点 |
| 一个 HookPoint | `ability/execution/hooks.py` 的 `HookPoint` | 不要在业务里用裸字符串 |
| WebUI 功能 | `ghrah-observer-webui/packages/*`（协议改动先对齐 `packages/protocol`） | 不要绕过 `observer-core` 直接连 WS |
| 一次性排查脚本 | 仓库根 `plans/`（被 `.gitignore` 整体忽略） | 不要放 `docs/` 或包内 |

## 11. 关键常量与当前指针

| 常量 / 指针 | 值 / 位置 |
| --- | --- |
| `DEFAULT_WINDOW_MAX_TOKENS` | `1_000_000`（`ghrah-core/src/ghrah/types/config_types.py:12`，运营者显式声明） |
| `LOCK_SCHEMA_VERSION` | `2`（`ability/loading/skill_lock.py:59`；不符即报错，不自动改写） |
| `V1_SCHEMA_VERSION` / `V2_SCHEMA_VERSION` | `'1'` / `'2'`（`ability/loading/ability_schema.py`） |
| `DEFINITION_FILE_NAME` / `LOCK_FILE_NAME` | `ability.yaml` / `ability.lock.yaml` |
| `BUILTIN_ROOT_RANK` / `USER_ROOT_RANK` | `0` / `100`（`ability/loading/ability_roots.py`） |
| `META_TOOL_NAMES` | 5 个元工具（`ability/surface/naming.py:39`） |
| `TOOL_NAME_MAX` | `64`（合法字符 `[A-Za-z0-9_-]`） |
| `DEFAULT_SEARCH_LIMIT` / `DEFAULT_LOADED_LIMIT` | `8` / `8`（`ability/surface/config.py`） |
| `CATALOG_DESCRIPTION_MAX_CHARS` | `500`（`ability/loading/skill_context.py:61`） |
| `DEFAULT_SURFACE_CONTEXT_LENGTH` | `8192`（`ability/surface/assembly.py`） |
| `FS_ABILITY_TYPES` / `CLUSTER_ABILITY_TYPES` | 7 / 7（`ability/kinds.py`） |
| `ABILITY_PATH_SPECS` | 8 条路径规格（`ability/paths.py`） |
| 关系库 schema | `ability_tree_schema.version=5`，8 张数据表（`ability/access/store.py:57`） |
| 内容单次读取上限 | `65536` 字节（`AbilityAccessStore.open(read_limit=…)`） |
| 服务端口默认 | Subject `4112`，WebUI `5173`（`docs/ghrah-05`） |

## 12. 历史与常见误读

### 12.1 已删除（历史，勿再作为检索起点）

| 旧概念（历史） | 现行替代 | 备注 |
| --- | --- | --- |
| 按文件组织的旧能力目录（`ghrah-core` 下的 `scheme/` 树） | `ghrah-core/src/ghrah/ability/` 分层 | 整目录已并入 `ability/`，旧路径不要用 |
| 定义面的工具清单投影模块 | `ability/surface/runtime.py` 的 `ToolSurfaceRuntime` | 该模块与符号全仓已无 |
| 旧的模板/清单服务单元 | `subject/template_store/` + `core/api/handlers/manifests.py` | 服务名仍出现在少量服务键中，但没有对应实现目录 |
| 旧的单体启动脚本 | Subject unit profile（`mount_builtin_units`）+ `scripts/dev-run.ps1` | 启动入口不再是单一脚本 |
| 旧的技能单元类型与"事件驱动落 ledger" | 技能是成员类型；ledger 只读投影 | 两者都已在关系库/端口模型下重做 |

`ghrah-scheme` 包**仍存在**（`ghrah-scheme/src/ghrah/scheme`，10 个 py），它是契约原语包，与上面"已删除的旧能力目录"无关；不要把两者混为一谈。

### 12.2 常见误读

| 说法 | 事实 |
| --- | --- |
| "扫描到定义就等于授权" | 不。扫描只产出定义与成员；授权唯一来源是关系库（`access/`） |
| "工具面由各能力自己声明" | 不。工具面每轮从关系库重建（`ToolSurfaceRuntime.refresh`），成员绑定只是镜像 |
| "模板成员可以直接生效" | 不。模板只表达申请，启动核定器提交版本与 grants 后才生效 |
| "Subject 还持有链库的写连接" | 不。写侧只在 Core `ContextManager`；Subject 经 `CorePort` 读 |
| "同一全名在内置树和安装根可以并存" | 不。来源登记遇同全名双源直接拒绝（`ability/source.py`） |
| "本仓自己定义了全部命令行入口" | 不。本仓内命令面是 `[project.scripts]`（`ghrah-subject`/`ghrah-subject-server`/`subject`）+ `python -m ghrah.ability.tools.cli` + `scripts/*.py`；发行版生命周期命令（`ghrah distro …` 等）由外部工具链提供，行为见 `docs/ghrah-05` |
| "日志实现在 `ghrah-core` 里" | 不。唯一实现在 `subject/logging_setup.py` |
| "存在全局能力注册表 / 挂载表" | 不。只有关系库 + 清单解析 + 端口抽象 |
