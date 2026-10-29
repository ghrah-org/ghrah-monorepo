---
title: scheme 模块清点与冗余排查
description: 逐一说明 ghrah/scheme 下每个文件现在的职责与真实消费方，并按死代码、纯别名、可合并、过渡兼容层四类列出冗余模块与处置建议
tags: [清点, 能力系统, Scheme]
keywords: [scheme, 冗余, 死代码, 兼容层, shim]
created: 2026-10-26
updated: 2026-10-26
status: done
---

# scheme 模块清点与冗余排查

> **⚠ 已归档（2026-10-29）**：本文记录 `ghrah/scheme/` 迁移过程中的中间态，已被 2026-10-28 的重构（`1938ffa` 退役 `abilities/`+`manifest/`、`fc032df` subject 改指 `ghrah.scheme`）整体推翻。仍在生效的只有 §7 的双 schema 实测，已并入 [ghrah-01](../ghrah-01-ability-design-and-implementation.md) §8.4。**不要照着本文写代码**；本文内部的相对链接多为失效的历史链接。

> 配套文档：[Ability 与 Manifest 一体化迁移记录](2026-10-26-scheme-ability-migration.md)（讲怎么搬的），本文讲**搬完之后每个文件是干什么的、哪些是冗余的**。
>
> 度量口径：**消费者数 = 仓库内真正 import 该模块的文件数**，按来源分类统计
> （`scheme` = scheme 内部 / `src` = 其他 src 包 / `test` / `example` / `oldpath` = 旧路径 shim 与门面 / `facade` = 旧路径聚合门面）。
> 符号级用"该名字在其它文件中被引用的次数"。数据来自对全仓 AST 的静态扫描，非人工印象。

## 0. 结论摘要

现状规模：`scheme/` 非能力包代码 **37 个模块、8371 行**（`execution/` 12 + 2078、`loading/` 16 + 4789、`permissions/` 3 + 871、根 `6` + 633，统计口径见下注）；`ability/` 下 5 个能力包共 38 个文件、5979 行；旧树兼容层（52 shim + 3 门面）**已全部删除**（见 §3.11）；备份目录 `legacy/manifest-old-format/`。

> 统计口径：只算 `scheme/` 下非 `ability/` 的 `*.py`，不含各层 `__init__.py`（它们只放文档字符串与惰性门面表）。

| 类别 | 对象 | 规模 | 处置建议 |
| --- | --- | --- | --- |
| **已删除** | `manifest_responses.py`（7 个零消费者 DTO）及其 shim | 80 + 15 行 | ✅ 2026-10-26 完成；冻结副本见 `legacy/manifest-old-format/` |
| ~~真死代码~~ | ~~`hook_registry.py`、`_utils.py`~~ | ~~118 行 + 2 个 shim~~ | ✅ **已删除**（2026-10-26，见 §3.1） |
| ~~类型注册表~~ | ~~`execution/registry.py`~~ | ~~202 行 + 22 条 import 期注册~~ | ✅ **已删除**，统一走扫盘解析（见 §3.9） |
| **可合并的薄模块** | `execution_frame`、`hook_context`、`invocation`、`kinds` | 156 行 | 并入更内聚的宿主（见 §3.2） |
| **名义符号** | `CLUSTER_ABILITY_TYPES`、`SUPPORTED_VERSIONS` 等 | — | 保留但可移出 `__all__`（见 §3.3） |
| **过渡兼容层** | 52 shim + 3 门面 + core 内 1 处惰性映射 | 1203 行 | 删旧树时一并消失（见 §3.4） |
| **旧格式解析栈** | `parse_ability_manifest` / `validate_manifest` / `BuiltinManifestStore` | 见 §3.6 | 已备份；**移除卡在 subject 落盘格式的决定上** |
| **目录池（本轮新增）** | `ability_roots` / `ability_scan` / `catalog` | 165 + 545 + 106 行 | ✅ 2026-10-26 完成；内置树 rank 0 + 用户目录 rank 100（见 §3.12），并给出容器视图 `scan_abilities()`（见 §3.13） |
| **模型上下文装载（本轮新增）** | `tool_list` / `skill_context` | 161 + 366 行 | ✅ 2026-10-26 完成；operations → OpenAI 工具列表、skills → 目录文案 + 正文按名字读取（见 §3.14） |
| **技能包管理并入（上一轮）** | `skill_errors` / `skill_models` / `skill_fingerprint` / `skill_lock` / `skill_mcp` / `skill_provider`（另 4 个已在本轮删除） | 6 个模块、约 1500 行 | ✅ 2026-10-26 完成；`ghrah/skills/` 曾退化为 6 个别名壳（见 §3.15 / §3.16），**随后整包回退到 HEAD**（见 §6） |
| ~~重复的加载流程~~ | ~~`skill_scan`、`skill_query`、`skill_init`、`skill_roots`~~ | ~~784 行 + 4 个壳~~ | ✅ **已删除**（2026-10-26），技能遍历/锁/指纹并入 `ability_scan`，技能根并入 `ability_roots`（见 §3.16） |
| **正常在役** | 其余 19 个模块 | — | 保留（见 §1） |

递减收益的动作顺序：**删剩余死代码（-118 行）→ 合并薄模块（-156 行、少 4 个 import 面）→ 定 subject 落盘格式后删旧格式解析栈 → 改指 20 处旧路径 import 后删兼容层（-1203 行）**。

## 1. scheme/ 模块逐个说明

本节按第四轮子包化之后的**现状**写（旧的 `manifest_models.py` / `ability_loader.py` / `manifest_resolver.py` 等已改名归位，映射见 §3.7；兼容层已全部删除，见 §3.11）。行数为实测值，会随改动漂移；"谁在消费"按 import 实测（含测试与 examples），不是推测。

### 1.1 `execution/` — 执行运行时（原 `abilities/*`）

| 模块 | 行 | 做什么 | 谁在消费 | 判定 |
| --- | --- | --- | --- | --- |
| `execution/base.py` | 83 | `Ability` ABC：`name`/`execute`/`get_hooks` 抽象 + `bind_tool`/`to_prompt_description`/`get_default_state` 空默认（纯接口，无业务逻辑） | 21 个 operation 类 | 在役 |
| `execution/context.py` | 215 | `AbilityExecutionContext`：把 invocation + data + services + agent_state 收成一个门面，旧属性名以 property 转发 | 几乎全部 operation | 在役 |
| `execution/execution_data.py` | 86 | 类型化 key + 可变数据总线（`accumulated_data` 的家） | `context`、`executor` | 在役 |
| `execution/execution_services.py` | 83 | 类型化服务注册表：`CONTEXT_MANAGER` / `SUPERVISOR` / `MANIFEST_STORE` / `SKILL_PROVIDER` / `AGENT_NAME` | `context`、`executor`、`core/unit` | 在役 |
| `execution/executor.py` | 718 | 执行器：PRE/POST hook、HITL 等待与超时降级、tool_calls 并行、延迟回复恢复 | `agents/base`、`core/unit` | 在役 |
| `execution/hooks.py` | 200 | Hook ABC + `HookPoint`（drive_loop / action / ability 三层）+ `HookScope` + `HookResult`（含 HITL、route 语义与合并规则） | 33 个文件 | 在役 |
| `execution/hook_store.py` | 128 | 按 (HookPoint, ability) 索引的 hook 存储 + 只读视图 | `hook_runner`、`agents/base` | 在役 |
| `execution/hook_context.py` | 64 | 把 `AbilityExecutionContext` 投影成执行帧给 runner 用 | `hook_runner`、`executor` | 在役（可合并，§3.2） |
| `execution/hook_runner.py` | 59 | hook 的唯一执行路径：从 store 选、跑、合并结果 | `executor` | 在役 |
| `execution/execution_frame.py` | 29 | `ExecutionFrame`：data + services + agent_state + last_action_result + current_node_id | `hook_context`（唯一） | 在役（可合并，§3.2） |
| `execution/invocation.py` | 21 | `AbilityInvocation`：一次调用的身份与参数（frozen dataclass） | `context`、`agents/base` | 在役（可合并，§3.2） |
| `execution/ability_build.py` | 220 | `ResolvedAbility` → `Ability` 实例：FS 权限、命令审批、HITL 包装的接线 | `spawn_agent`、`core/unit` | 在役 |

`execution/registry.py`（`AbilityRegistry`，202 行）已于 2026-10-26 删除，改由 `loading/ability_catalog.py` 扫盘解析取代，见 §3.9。

### 1.2 `loading/` — 声明读取、目录池与视图投影

| 模块 | 行 | 做什么 | 谁在消费 | 判定 |
| --- | --- | --- | --- | --- |
| `loading/ability_catalog.py` | 563 | **实现面投影**：扫内置树 → 解析定义 → import 实现脚本 → 读 `PERMISSIONS`/`HOOKS`/`SUMMARY`/`TAGS` → 组装 `AbilityManifest`（工具面开关取定义面的 `expose`）；对外另给 `builtin_handlers` / `operation_containers` / `REGISTRY_ONLY_OPERATIONS` / `builtin_full_names` / `build_tool_schema` | `manifest_io`、`catalog`、`list_abilities`、`core/unit` | 在役 |
| `loading/ability_roots.py` | 208 | **目录池**：`AbilityRoot`（locator + rank + source + required）与 `default_pool` / `builtin_root` / `user_root` / `ensure_roots` / `path_root` / `default_user_root`。内置树 rank 0（必需）、`~/.ghrah/skill-ability` rank 100（可选，不存在不算问题） | `ability_scan`、`ability_catalog`、`catalog`、`skill_roots`（转调，不自己拼路径） | 在役（2026-10-26 新增） |
| `loading/ability_scan.py` | 939 | **按池扫盘 + 技能成员的锁与指纹**：只解析定义、不 import 实现。三个读数同源——扁平 `AbilityEntry` / `AbilitySkill`、容器视图 `scan_abilities()` → `AbilityContainer`（`name`/`namespace`/`version`/`title`/`description`/`operations`/`skills`/`assets`/`issues` + 计数与 `kinds`/`is_skill_only`）、技能成员 `load_ability_skills()`。`AbilitySkill` 自带 `skill_dir` / `lock_path` / `fingerprint` / `lock_issue`；写盘只经 `ensure_locks()` / `relock_skill()`，扫描本身只读。同名容器由 rank 小者胜出，被忽略的一份记 issue 而不是静默覆盖 | `ability_catalog`、`catalog`、`tool_list`、`skill_context`、`skill_provider`、调试脚本 | 在役（2026-10-26 新增；§3.13 容器视图，§3.16 流程合并） |
| `loading/ability_schema.py` | 325 | `ability.yaml` 的类型化契约：`AbilityDefinition` / `AbilityIdentity` / `ProviderRequirement` / `OperationDefinition` / `ExecutionSpec` / `OperationImplementation` / `SkillDefinition` / `SkillBody`，全部 `extra="forbid"`；`OperationDefinition.expose`（默认 True）决定是否进模型工具面；另含 `DEFINITION_FILE_NAME` / `LOCK_FILE_NAME` / `SKILL_BODY_DIR` / `load_yaml`（`CSafeLoader`）/ `skill_body_path` | `ability_catalog`、`ability_scan`、`skill_provider` | 在役 |
| `loading/ability_view.py` | 85 | 运行时视图模型：`AbilityManifest` / `AbilityMetadata` / `AbilityHooks` / `ImplementationDef` 等（**派生投影，可重建**） | 10 个文件（含 subject 的 manifest_store） | 在役 |
| `loading/manifest_fields.py` | 187 | 两侧共用件：格式版本、校验异常、`PermissionFlags` / `ToolSchema` / `HookDef` / `ImplementationDef` | 24 个文件（loading 全部、`agent_*`、subject、examples） | 在役 |
| `loading/manifest_io.py` | 150 | 声明的来源：yaml/json → 模型 + `ManifestStoreProtocol` + `BuiltinManifestStore`（内含旧格式 `parse_ability_manifest` / `validate_manifest`，属 §3.6 那套栈） | `agent_resolver`、subject `manifest_store/store.py`、examples、3 个测试 | 在役（旧格式栈见 §3.6） |
| `loading/catalog.py` | 136 | **对外目录报告**：能力面 + skill 面合流成一份 `CatalogReport`（含目录池读数、扫描问题、两面差异），给外层框架一次调用拿到全部 | 目前只有测试与调试脚本；设计定位是对外 API | 在役 |
| `loading/tool_list.py` | 161 | **模型工具面**：只读 yaml → OpenAI function 列表（工具名 = 成员键），`ToolList` 给 `names` / `tools` / `get()` 反查归属；`expose: false` 的成员不出现；跨容器撞名留先入者并记问题 | 目前只有测试与调试脚本；设计定位是装配模型上下文的入口 | 在役（2026-10-26 新增，见 §3.14） |
| `loading/skill_context.py` | 366 | **模型技能面 + 两条读正文入口**：目录文案（`render_skill_catalog` / `digest_skill_catalog` / `catalog_description`）、正文读取（`read_skill_body` 收定义面 `AbilitySkill`、`read_skill_document` 收技能包面 `SkillSummary`，两者共用 `read_skill_text` 一份读盘）、`SkillCatalog`；`SkillCatalogEntry` Protocol 让 `SkillSummary` 直接复用同一套渲染 | `context/manager.py`（目录注入）、`load_skill`、`skills/catalog.py` 与 `skills/document.py`（转调）、调试脚本 | 在役（2026-10-26 新增，见 §3.14 / §3.15） |
| `loading/skill_errors.py` | 27 | 技能包面的异常：`SkillError` / `SkillLockError` / `SkillNotFoundError` / `SkillParseError` | `list_abilities`、`load_skill`、`skill_*` 全套 | 在役（2026-10-26 自 `ghrah/skills/errors.py` 迁入） |
| `loading/skill_models.py` | 204 | 技能包面读数：`SkillRoot` / `SkillSummary` / `SkillScanResult` / `LockRecord` / `SkillFingerprint` / `BundleVerification` / `SkillInitResult` / `SkillDocument`（+ `SkillPackageDocument` 无歧义别名） | `skill_*` 全套、`skills/__init__` 壳、`catalog` | 在役（迁入见 §3.15；`SkillScanResult` 现已不带扫描逻辑，仅作外部 `scan_skills` 的形状） |
| `loading/skill_fingerprint.py` | 627 | 整树观测与两枚摘要（`content_digest` 跨机可比 / `snapshot_digest` 含 mtime），排除规则与上限；`build_manifest` 产出 lock 的 `manifest.text` | `ability_scan`、`skill_lock`、`skill_provider` | 在役（迁入见 §3.15） |
| `loading/skill_lock.py` | 287 | `ability.lock.yaml` 的读写、schema 迁移与往返；`extract_permission_request` 仍从「正文申请值」取权限（新格式正文无 frontmatter，故恒为默认，见 §3.16） | `ability_scan`、`skill_provider` | 在役（迁入见 §3.15） |
| `loading/skill_mcp.py` | 283 | MCP 规格加载与渲染：`load_mcp_specs` / `render_mcp_catalog` / `render_mcp_schema_block` | `load_skill` | 在役（迁入见 §3.15） |
| `loading/skill_provider.py` | 251 | `SkillAbilityProvider`：外部唯一聚合入口（`scan` / `get_skill` / `read_skill` / `query_fingerprints` / `get_fingerprint` / `ensure_roots` / `initialize` / `verify` / `relock`，全部不缓存）。内部只剩一层转调：扫描与写锁都交给 `ability_scan` | `core/unit.py`、subject `units/skill.py`（经壳）、`list_abilities` | 在役（迁入见 §3.15，瘦身见 §3.16） |

已被 §3.16 删除的四个模块（`skill_scan` / `skill_query` / `skill_init` / `skill_roots`）不再单独占行：遍历与锁并入 `ability_scan`，技能根并入 `ability_roots`。

### 1.3 根部与 `permissions/`

| 模块 | 行 | 做什么 | 谁在消费 | 判定 |
| --- | --- | --- | --- | --- |
| `__init__.py` | 194 | 包级门面：一张「公开名字 → 归属模块」索引表（`_MODULE_OF`，125 条），`__all__` 由它派生；`__getattr__` 惰性加载并缓存，`__dir__` 供补全。**导入期不拉起任何子模块**（子进程实测 0 个），故不产生初始化环 | 外部框架对接、各包 | 在役 |
| `agent_manifest.py` | 139 | agent 定义格式：`AgentManifest`（`manifest: Literal["agent"]`、metadata / model / system_prompt / max_iterations / abilities…） | `agent_resolver`、`manifest_io`、subject、examples | 在役 |
| `agent_resolver.py` | 175 | `AgentManifest` → `AgentConfig` + `ResolvedAbility[]`（builtin 与 registry 的 join、权限合并、FS 规格展开） | `core/unit`、`ability_build`、`spawn_agent`、subject | 在役 |
| `errors.py` | 26 | `AbilityError` / `AbilityNotFoundError`（继承 core 的 `ActorAgentError`） | `core/exceptions` 惰性映射、`agents/base` | 在役 |
| `paths.py` | 154 | 路径参数规格表 `ABILITY_PATH_SPECS` + `is_subpath` + 模板展开 + 相对路径归一 | 7 个 fs op、`ability_build`、`executor`、`fs_permissions`、subject sandbox | 在役 |
| `kinds.py` | 38 | `FS_ABILITY_TYPES` / `CLUSTER_ABILITY_TYPES`（为断开门面初始化环而单独成模块） | `core/unit`、`ability_build` | 在役（可合并，§3.2） |
| `_cluster_common.py` | 46 | 集群能力共用件：supervisor 未接线时的统一错误、目标解析 | 6 个 cluster operation | 在役 |
| `permissions/fs_permissions.py` | 264 | `FSPermissionChecker` + `AccessApprovalHook` / `WriteApprovalHook` | 7 个 fs op、`ability_build`、`core/unit` | 在役 |
| `permissions/command_safety.py` | 448 | 命令分类（safe / dangerous + 子命令路由）+ `CommandApprovalHook` | `execute_command`、`ability_build` | 在役 |
| `permissions/hitl.py` | 82 | `HITLApprovalHook` + `HITLRequiredAbility`：声明式 `require_hitl` 的内核固定实现（组合非继承） | `ability_build` | 在役 |

## 2. `ability/` 目录

只放能力包，**没有任何加载 / 解析代码**（那两件事在 `loading/ability_catalog.py` 与 `loading/ability_schema.py`）：

| 路径 | 说明 |
| --- | --- |
| `ability/__init__.py` | 包标记。目录扫描（`importlib.resources`）与 `import ghrah.scheme.ability.<容器>.operations.<键>` 都需要它 |
| `ability/<容器>/__init__.py` | 容器包标记 |
| `ability/<容器>/ability.yaml` | 定义面：身份、`provider_requirements`、每个成员的 in/out schema、errors、effects、resources、execution |
| `ability/<容器>/operations/__init__.py` | 实现包标记 |
| `ability/<容器>/operations/<键>.py` | 实现面：实现类 + `PERMISSIONS` / `HOOKS` / `SUMMARY` / `TAGS` / `TOOL` 模块级声明 |

四个容器：`fs`（7 个成员）、`core`（6）、`cluster`（7）、`shell`（1），共 21 个实现脚本。

## 3. 冗余清单

### 3.1 真死代码（建议直接删）

| 对象 | 规模 | 证据 | 状态 |
| --- | --- | --- | --- |
| `scheme/hook_registry.py` + `abilities/builtin/__init__.py` 的 `_register_builtin_hooks()`（3 条注册与调用点） | 101 行 | `BuiltinHookRegistry` 全仓只有写入（`abilities/builtin/__init__.py:133-135`），**没有任何读取方**。真实 hook 接线走类的 `get_hooks()` 与 `HITLRequiredAbility` 包装 | 待删 |
| `scheme/_utils.py` + `abilities/_utils.py` shim | 17 行 | `ghrah.scheme._utils` 的唯一引用者是它自己的 shim；没有任何代码 import `ghrah.abilities._utils`。它只是 `paths.py` 的别名 | 待删 |
| ~~`scheme/manifest_responses.py`~~ | ~~80 行~~ | 7 个 DTO 零引用 | ✅ **已删除**（2026-10-26）：模块 + 别名 shim + `ghrah/manifest/__init__.py` 里的转发与 `__all__` 条目一起移除；`ghrah-core` + `ghrah-subject` 2830 passed 无回归。冻结副本见 `legacy/manifest-old-format/` |

删除注意：删 `hook_registry` 时必须同时删 `abilities/builtin/__init__.py` 里的三行注册与 `_register_builtin_hooks()` 调用；删 `_utils` 时确认 `core/exceptions.py` 的惰性映射表里没有它（当前没有）。

### 3.2 可合并的薄模块（低风险）

| 模块 | 行 | 唯一 / 主要消费者 | 建议并入 |
| --- | --- | --- | --- |
| `execution/execution_frame.py` | 29 | `execution/hook_context.py` | `execution/hook_context.py`（专职服务它）或 `execution/context.py`（它本来就是 context 的字段集去掉 invocation） |
| `execution/hook_context.py` | 64 | `execution/hook_runner.py` + executor/agents 的构造点 | `execution/hook_runner.py`（runner 与它的上下文是同一件事的两半） |
| `execution/invocation.py` | 21 | `execution/context.py`、`agents/base.py` | `execution/context.py`（frozen 身份值，无独立生命周期） |
| `kinds.py` | 42 | `execution/ability_build.py` + abilities 门面 | `execution/registry.py`（能力类型集合与能力注册表同属"能力分类"）。**注意**：`CLUSTER_ABILITY_TYPES` 目前零消费者 |

四者合并后：`scheme/` 少 4 个 import 面、约 -156 行，且 `context ↔ execution_*` 这条"门面 + 三块内脏"的绕行会缩短。

### 3.3 名义符号（不是死代码，但从对外面看多余）

| 符号 | 现状 |
| --- | --- |
| `CLUSTER_ABILITY_TYPES` | 零消费者，只被 abilities 门面转发 |
| `SUPPORTED_VERSIONS` | 仅 `manifest_models.py` 内部（model_validator）使用 |
| `HitlOutcome` / `HITLWaitResult` | 仅 `execution/executor.py` 内部使用（HITL 等待结果） |
| `CommandSafetyVerdict` | 仅 `permissions/command_safety.py` 内部使用（分类裁定结果） |
| `AbilityIdentity` / `ExecutionSpec` / `OperationImplementation` / `ProviderRequirement` | 仅 `loading/ability_schema.py` 内部作为嵌套模型使用 |

处置：可留在 `__all__`（类型完整、便于外部构造），也可移出以收紧对外面。**不建议删定义**。

### 3.4 过渡兼容层（已退役，见 §3.11）

| 对象 | 规模 | 说明 |
| --- | --- | --- |
| 模块别名 shim | 52 个文件 / 780 行 | abilities 侧 42（17 模块 + 4 支持件 + 21 操作）+ manifest 侧 10（9 模块 + `builtins`；`responses` 的 shim 已随 §3.1 删除）。全部是 `sys.modules[__name__] = _target` 的模块对象别名 |
| `abilities/__init__.py` | 207 行 | 对外门面：50+ 符号聚合 + 22 条 `AbilityRegistry.register` |
| `abilities/builtin/__init__.py` | 123 行 | 聚合 + 内置 hook 注册（后者随 §3.1 一起删） |
| `manifest/__init__.py` | 88 行 | 27 个符号的聚合转发（原 34 个，减去已删的 7 个 DTO） |
| `core/exceptions.py` 的 `_RE_EXPORTS` | 2 条映射 | `"AbilityError"` / `"AbilityNotFoundError"` → `ghrah.abilities.errors`，由模块级 `__getattr__` 惰性解析。这是继"模块别名 shim"与"聚合门面"之后的第三种兼容机制 |
| 仍 import 旧路径的源文件 | 20 个 | `ghrah-core/src`、`examples`、`ghrah-subject/src` 中仍走 `ghrah.abilities.*` / `ghrah.manifest.*` 的文件 |

✅ **该顺序已全部执行完毕**（2026-10-26）：先清掉注册表这个障碍，再改指全仓 298 处调用，最后整棵删除旧树。详见 §3.11。

### 3.5 不建议动的

- `errors.py`（26 行）：虽小，但被 `core/exceptions.py` 与 `agents/base.py` 消费，是能力域异常的正式归属。
- `execution/hooks.py` / `execution/hook_store.py` / `execution/hook_runner.py`：hook 三件套职责清晰（定义 / 索引 / 执行），合起来会让"hook 从哪来、怎么被选中"重新变模糊。
- `_cluster_common.py`：6 个消费者，是真实的共用件。
- `execution/execution_data.py` / `execution/execution_services.py`：被 `context` 与 `executor` 共同使用，拆开或合并都会扩大改动面而无收益。
- `agent_resolver.py` / `execution/ability_build.py`：一个只做声明→配置，一个做实例化与权限接线，混在一起会让"哪里决定权限"变模糊（合并依据见迁移文档 §2.2）。

### 3.6 旧格式解析栈：已备份，移除卡在 subject 的落盘格式上

**定义面确认只加载新格式**：`loading/ability_catalog.py` 只扫描 `ability/<容器>/ability.yaml`（`ghrah.ability-definition`），随包 20 份旧格式 yaml 已并入 4 份容器定义并删除。

但"旧格式的能力**加载**"还在一条回路里活着，因此**不能直接删**：

```text
ability/<容器>/ability.yaml（新格式，定义）
   │ ability_loader 投影
   ▼ AbilityManifest（运行时视图）
   │ ghrah-subject .../manifest_store/builtins.py:39-40   序列化成旧格式 YAML
   ▼ {root}/abilities/{ns}/{name}.yaml（旧格式，落盘）
   │ ghrah-subject .../manifest_store/store.py:80         parse_ability_manifest 读回
   ▼ AbilityManifest（再次）
```

也就是说：**旧格式此刻是 subject 的能力落盘格式**，不是定义来源。生产消费者只有 subject（另加 core 的 examples 与测试）：

| 位置 | 用途 |
| --- | --- |
| `ghrah-subject/.../manifest_store/builtins.py:39-40` | `ensure_builtins` 把 20 份投影结果序列化成旧格式 YAML 写入 store |
| `ghrah-subject/.../manifest_store/store.py:80` | `get_ability` → `parse_ability_manifest(content)` |
| `ghrah-subject/.../manifest_store/store.py:203` | `_put_entry` → `validate_manifest(content)` 且强制 `manifest == "ability"` |
| `ghrah-subject/.../manifest_store/service.py:209` | `manifest_validate` 命令 |
| `ghrah-core/examples/agents_manifest/runner.py:39,77` | 示例直接解析旧格式能力声明 |
| `ghrah-core/tests/manifest/test_parser.py`、`ghrah-subject/tests/test_manifest_store.py` | 覆盖旧格式解析与校验 |

**备份位置**：`legacy/manifest-old-format/`（冻结副本 + `README.md` + 由投影结果生成的真实旧格式样本 `sample-ability.yaml`）。该目录不属于任何包、不被导入、不参与测试。

**移除的前置决定**——subject 的能力存储走哪种格式：

- **方案 A（推荐，最小版本）**：内置能力不再落盘，`get_ability` 未命中时回落到 `ability_loader` 的投影；只保留用户自建能力的 CRUD。这样能先删掉旧格式解析的内置路径，且不必改 subject 对外命令形状。
- **方案 B**：subject 保留旧格式落盘 → core 侧解析器**必须保留**（它服务的是"运行时视图的落盘格式"，与"定义的加载"是两件事），备份目录只作历史参考。

**不可删的部分**：`AbilityManifest` / `AbilityMetadata` / `AbilityHooks` / `ToolSchema` / `ToolParameter` / `PermissionFlags` / `ImplementationDef`（在 `manifest_models.py`）是投影目标与运行时视图，被 56 个文件消费，与本条无关。

### 3.7 第三轮重命名（2026-10-26）

能力侧不再有 manifest 概念，模块名随之调整；**agent 侧保留 manifest 命名**（`manifest: agent` 仍是活格式，被 subject 的 `manifest_store`、`ghrah-protocol` 的 manifest payload 消费）。

| 旧名 | 新名 | 理由 |
| --- | --- | --- |
| `ability_loader.py` | `loading/ability_catalog.py` | 扫盘 + 组装对外目录（展示 + 模型工具面） |
| `ability_definition.py` | `loading/ability_schema.py` | 新格式 yaml 的字段契约 |
| `manifest_materialize.py` | `execution/ability_build.py` | 定义 → 可执行实例 |
| `manifest_models.py` | `loading/manifest_fields.py` + `loading/ability_view.py` + `agent_manifest.py` | 共用字段/异常、能力运行时视图、agent 定义格式；**拆三份而非两份**——`PermissionFlags`/`HookDef`/`ToolSchema`/`ImplementationDef`/格式版本/异常被两侧共用，让任一方持有都会造出反向依赖 |
| `manifest_source.py` | `loading/manifest_io.py` | 解析 + store 协议（仍在服务 agent manifest，故保留 manifest 命名） |
| `manifest_resolver.py` | `agent_resolver.py` | 它解的是 AgentManifest → AgentConfig |
| `execution/registry.py` | **暂留** | 见下 |

**`execution/registry.py` 没能一起删**：`AbilityRegistry` 不只是"声明式能力的清单"，它还承担两件扫描替代不了的事——

1. ~~`command_proxy` 是 descriptor 驱动的~~ —— **该项已随插件能力桥退役删除**（§3.8）；
2. 运行时注册自定义类（`tests/abilities/test_ability_registry.py` 的 `MockAbility`/`ParamAbility`、`examples`）。

所以 registry 的删除只剩第 2 项待处理：热插拔落地后，`create(type)` 由扫盘结果解析，自定义类注册要么改为产出定义、要么保留一个只给它用的构造缝。

### 3.8 插件能力桥退役（2026-10-26）

`command_proxy` 原本是"插件能力 → Agent 工具面"的投影桥。ability 规范完整后它不再必要：
插件提供的应当直接就是 ability 包，由扫盘加载。**整条链已退役删除**，两侧同时移除（否则
subject 仍会发 Core 不再认识的 `ability_type: command_proxy`）：

| 侧 | 删除对象 |
| --- | --- |
| Core | `scheme/command_proxy.py`（能力本体）、`core/subject_bridge.py`（`SubjectCommandBridge`，仅服务本回路）、`core/unit.py` 的 `_materialize_command_proxy` 与 `_subject_bridge` 构造、`register_ability` 命令里的 descriptor 分支 |
| Subject | `runtime/ability_bridge.py`（`AbilityBridgeUnit` 装配桥）、`plugin_mount.py` 的 `_notify_ability_bridge` 与两处调用、`service_keys.py` 的 `AbilityDescriptor` / `AbilityBridgeService` / `ABILITY_BRIDGE` / `ABILITY_BRIDGE_UNIT` / `COMMAND_BRIDGE`、`units/__init__.py` 的挂载 |
| 测试 | 整文件删除：`test_command_proxy.py`、`test_command_proxy_materialize.py`、`test_subject_bridge.py`、`test_ability_bridge.py`、`test_plugin_capability_e2e.py`；断言改用剩余项：`test_list_abilities.py`（registry-only 改为 `send`）、`test_ouroboros_assembly.py`（去掉 `ability_bridge` fiber）、`test_supervisor.py`（文档串） |

退役后：注册表 23 → **22** 种类型，声明面仍是 20 份；`send` 成为唯一的 registry-only 项。
插件挂载本身（`plugin_mount`）不受影响，只是不再投影工具面。

### 3.9 AbilityRegistry 退役：统一走扫盘解析（2026-10-26）

注册表（类型名 → 类的进程内映射 + import 期注册）与"扫盘即真相"是两套权威，
因此整体退役。**无需任何注册**：handler 落在哪个容器的 ``operations`` 表里，
实现脚本就在 ``ability/<容器>/operations/<handler>.py``，类就是该脚本里唯一的
``Ability`` 子类（``_operation_class`` 的既有约定）。

| 旧 API | 新 API（`ability_catalog`） |
| --- | --- |
| `AbilityRegistry.create(t, **p)` | `create_ability(handler, **params)` |
| `AbilityRegistry.has(t)` | `has_handler(handler)` |
| `AbilityRegistry.list_types()` | `builtin_handlers()` |
| `AbilityRegistry.get_class(t)` | `ability_class(handler)` |
| `AbilityRegistry.register/unregister/clear` | **删除**（自定义类注册特性退役） |
| `AbilityRegistry.register_from_resolved` | **删除**（其"builtin 校验"并入 `has_handler`） |

**删除对象**：`scheme/execution/registry.py`、`scheme/hook_registry.py`（具名内建 hook
注册表，只写不读）、`scheme/_utils.py`（`paths` 的纯别名）及其 3 个 shim、
`abilities/__init__.py` 的 22 条 `register` 与 `_register_builtin_hooks()`、
`core/ability_protocol.RegistryProtocol`、`tests/abilities/test_ability_registry.py`、
`tests/manifest/test_resolver.py` 的 `register_from_resolved` 测试类。

**`mcp_call` 升格为能力包**：它此前是唯一"只在注册表里、没有定义面"的真实能力
（`abilities/builtin/mcp_call.py`），注册表一删它就没有落点。现已落成
`scheme/ability/mcp/{ability.yaml, operations/mcp_call.py}`，于是：

- handler 面 **21 → 22**（含 `send`；`send` 是设计上的 registry-only 成员，不进工具面但必须可解析）；
- 定义面 **20 → 21 份**（`ghrah.mcp.mcp_call` 加入）。

**验证**：无需任何 import 期副作用即可解析 —— 只 import `ability_catalog` 就能
`create_ability("conversation")` / `create_ability("mcp_call")`；全量 **2976 passed**。
唯一失败是既有的 Windows 路径断言（`/tmp/data` vs `D:\tmp\data`），与本轮无关。

### 3.10 去掉缓存：真正的热插拔（2026-10-26）

此前能力识别是"import 期注册 + 写缓存"：`lru_cache` 定义扫描、`lru_cache` 视图投影、
`cached_property` 内置 store、外加 import 期名单快照 `_BUILTIN_YAML_NAMES`。
现在**查询即扫盘**，外部改动在下一次查询生效。

**先决条件：换 YAML loader。** 这是关键发现——`yaml.safe_load` **恒为纯 Python 实现**，
与 `yaml.__with_libyaml__` 是否为真无关（C loader 是独立的 `yaml.CSafeLoader`）。
实测 36KB 定义：

| loader | 耗时 |
| --- | --- |
| `yaml.safe_load`（纯 Python） | **75 ms** |
| `yaml.CSafeLoader` | **9.7 ms** |

不换 loader 就"每次查询都扫盘"，等于每次查询 75ms、22 个默认能力的装配要多花 1.6s。
现统一走 `ability_catalog.load_yaml`（`CSafeLoader`，无 libyaml 时退回 `SafeLoader`）。

**删除的缓存**：`load_ability_definitions` / `load_all_builtin_manifests` 的 `lru_cache`、
`BuiltinManifestStore._builtins` 的 `cached_property`、`_BUILTIN_YAML_NAMES`（改由
`builtin_full_names()` 现算；4 个测试文件同步改）。

**实测成本**（无缓存，稳态）：

| 操作 | 耗时 |
| --- | --- |
| `load_ability_definitions()` | 5.8 ms |
| `load_all_builtin_manifests()` | 7.1 ms |
| `builtin_handlers()` | 6.5 ms |
| 22 个能力逐个 `create_ability` | ~130 ms |

注：22 个能力首次调用是 689ms，其中绝大部分是 **21 个实现脚本的首次 import**（进程内
一次性，与缓存无关）；第二遍起 ~130ms，即 22 × 单次扫盘。

**对外扫描 API**（外层框架直接可调，不必经模型工具面）：

- `ability_catalog`：`load_ability_definitions` / `load_all_builtin_manifests` /
  `builtin_full_names` / `builtin_handlers` / `has_handler` / `ability_class` /
  `create_ability` / `operation_containers` / `load_definition` / `load_yaml`；
- `scheme/loading/catalog.py` 的 `scan_catalog(skill_provider=...)`：**一次拿到两面**
  （能力定义 view + skill 扫描结果），并显式标出 skill 面是否接线（未接线不假装"没有技能"）。
  它不定义新事实，只是把两处既有权威的读数并成一份报告；
- skill 侧：`ghrah.skills.SkillAbilityProvider`（`scan` / `get_skill` / `read_skill` /
  `query_fingerprints` / `initialize`）。

**热插拔的边界（如实记录）**：热插拔对**定义面**成立——增删改 `ability.yaml` 下一次
查询即反映到定义、handler 名单与 view。但**实现面**按点分包路径 import
（`ghrah.scheme.ability.<容器>.operations.<成员>`），因此新能力的实现脚本必须落在可
import 的包树里；包外的容器会在投影期报"无法装载实现脚本"并指名缺失模块（不静默跳过）。
skill 面没有实现脚本，故不受此限——用户级技能池本来就是完全热插拔的。

### 3.11 兼容层退役完成：外部调用全部适配到 scheme（2026-10-26）

`abilities` / `manifest` 两个兼容包（52 个模块别名 shim + 3 个门面）**已整体删除**，
外部调用全部改指 `ghrah.scheme.*`。做法是先把映射推导出来，再按 ast 精确改写——
**任何无法映射的符号都会中止改写**，不留半成品。

**映射是怎么来的**（不手抄）：

1. 模块级：扫旧树每个 shim 的 `import <新模块> as _target`，得 48 条；
2. 符号级：import 门面包，对 `__all__` 里每个名字取 `__module__`；常量/容器
   （如 `FS_ABILITY_TYPES`）按对象身份在 scheme 各模块里反查——得
   `abilities` 65 / `abilities.builtin` 30 / `manifest` 27 条；
3. 调用点：ast 扫全仓，得 **298 处、88 个文件**，分四种形态分别处理。

**改写处理的四种形态**：

| 形态 | 数量 | 处理 |
| --- | --- | --- |
| 具体子模块 import（`from ghrah.abilities.executor import X`） | 275 | 换模块名，符号不动 |
| 字符串路径（`monkeypatch.setattr("ghrah.abilities.builtin.search_files.shutil.which", …)`） | 11 | 前缀替换为新模块 |
| 门面符号 import（`from ghrah.manifest import AbilityManifest`） | 4 | 按符号归属拆成多条新路径 import |
| 注释/文档串 | 8 | 逐个改成新路径（避免指向已删包） |

另有两种要单独认的形态：`from ghrah.manifest import resolver`（导的是**子模块本身**，
改写成 `from ghrah.scheme import agent_resolver as resolver` 以保住本地名），
以及 `import ghrah.abilities  # noqa: F401` 这类**为触发注册而存在的副作用导入**
——注册已删，直接移除。

**结果**：

- 54/60 个文件被改写，其余 6 个只有注释；
- `ghrah-core/src/ghrah/{abilities,manifest}/` 两个目录整棵删除
  （49 个 shim + 3 个门面 = 52 个文件）；
- `core/exceptions.py` 的 `_RE_EXPORTS` 随改写自动指向 `ghrah.scheme.errors`；
- 全仓（`legacy/` 冻结快照除外）对 `ghrah.abilities` / `ghrah.manifest` **零引用**；
  旧包已不可导入（`ModuleNotFoundError`）；
- **2998 passed**，与改写前完全一致；唯一失败是既有的 Windows 路径断言。

**命名保留说明**：`ghrah-subject` 的 `manifest_store`、`ghrah-protocol` 的 manifest
payload **不进本次退役**——agent 定义格式仍是 `manifest: agent`（见 §3.7 的口径）。

**遗留（不影响对接）**：测试目录仍按旧名组织（`tests/abilities/`、`tests/manifest/`）。
AGENTS.md 要求"测试文件与被测模块同构"，按新结构它们应分别落到
`tests/scheme/ability/`、`tests/scheme/loading/`、`tests/scheme/` 等；这是独立的一次
整理，与调用适配无关，未混在本轮里做。

### 3.12 目录池：扫描不再是"一个写死的根"（2026-10-26）

**触发**：用调试脚本 `plans/selfcheck/query_all_abilities.py` 一跑，`~/.ghrah/skill-ability` 下 12 个 skill 包一个都没出现。

**原因**：旧 `ability_catalog` 的扫描根写死成一处——`importlib.resources` 指向随包分发的 `ghrah/scheme/ability/`；skill 面则另有自己的根（`ghrah.skills.roots.default_skill_root()`）。两份 yaml 同格式、同权威，却被两条互不知情的扫描路径读取，等于把"能力包在哪"复制成了两份。

**改法**：把"根"提成一等公民，两面对同一个池取数。

| 件 | 位置 | 作用 |
| --- | --- | --- |
| `AbilityRoot` | `loading/ability_roots.py` | 一个根 = locator + rank + source + required（`required=False` 时目录不存在不算问题） |
| `default_pool()` | 同上 | 内置树（rank 0，required）+ `~/.ghrah/skill-ability`（rank 100，可选） |
| `scan_ability_definitions(pool)` | `loading/ability_scan.py` | 按池扫盘，只解析定义、不 import 实现；同名容器 rank 小者胜出，落败者记 issue |
| `scan_catalog(pool=…)` | `loading/catalog.py` | 能力面 + skill 面合流成一份报告 |
| `ghrah.skills.roots` | `ghrah-core/src/ghrah/skills/roots.py` | 改为**转调** `ability_roots`，不再自己拼路径 |

**为什么内置树必须 rank 0 且 required**：它随包分发，是"系统能力"。若允许同 rank 或可缺省，用户丢一份同名 yaml 就能改掉 `read_file` 的语义；反过来，打包损坏会被误判成"用户没装能力"。

**为什么 rank 落败要记 issue 而不是静默覆盖**："我的包为什么没生效"否则是不可诊断问题。

**能力面与 skill 面的关系**：skill 就是"只有 `skills`、没有 `operations` 的 ability 包"，两面读的是同一份 `ability.yaml`。名字口径不同是自然结果——能力侧成员全名是 `<容器>.<键>`（如 `research.research`），skill 侧 `full_name` 取包身份（如 `research`），目录同名时两者恰好相同。

**边界（有意为之）**：`list_abilities`（模型工具面）仍只列内置实现面的投影 + 装配期注入的 skill provider 行；12 个纯 skill 包不进工具面，它们经 `load_skill` 可达。工具面直接吃"没有实现脚本的声明"会把模型引向不可调用的名字。

**验证**：17 个容器（5 内置 + 12 用户）、22 个 handler、21 个 view、12 个 skill 成员、0 条扫描问题；`pytest ghrah-core` 与 ruff 全绿。

### 3.13 容器视图：一次调用返回「能力包对象列表」（2026-10-26）

**触发**：目录池（§3.12）解决"扫哪些根"，但读数仍是扁平的——`entries` 是"容器 → 定义"的列表、`skills` 是另一条平行列表，调用方想回答"每个包里有几个 operation、几个 skill"就得自己按容器名归并一次。

**口径**：扫描的第一维度是 **ability 容器**（= 一个能力包目录），不是成员。一个包可以只有 `operations`（旧口径的"工具"），也可以只有 `skills`，将来还会加 `mcp`；三类成员都在同一个容器对象里。

| 类型 | 位置 | 承载 |
| --- | --- | --- |
| `AbilityContainer` | `loading/ability_scan.py` | 一个包的全貌：`name` / `namespace` / `version` / `title` / `description` / `root` / `locator` / `definition` + `operations` / `skills` / `assets` / `issues` |
| `AbilityOperation` | 同上 | 一个 operation 成员：`key` / `full_name` / `definition`（权威载荷），派生 `title` / `description` / `provider_slot` / `handler` / `timeout_ms` / `effects` |
| `AbilitySkill` | 同上 | 一个 skill 成员：`key` / `full_name` / `title` / `description` / `media_type` / `body_path` / `body_locator` / `tags` / `source` / `rank`（**只给声明读数，不读正文**） |
| `AbilityAsset` | 同上 | 一个 asset 成员：`key` / `full_name` / `definition`（当前 schema 里 `assets` 是自由结构，没有字段级契约，因此不解释语义） |

对外入口：

```python
from ghrah.scheme.loading.ability_scan import scan_abilities

for container in scan_abilities():  # 池默认取 default_pool()
    print(container.name, container.kinds, container.operation_count, container.skill_count)
    for issue in container.issues:  # 只属于这个包的问题
        print("  !", issue)
```

计数与判定：`operation_count` / `skill_count` / `asset_count` / `member_count`，`kinds` 按 `operations` → `skills` → `assets` 固定顺序列出实际存在的类别，另有 `is_operation_only` / `is_skill_only` 两个便捷判定。

**规则**：

- **顺序确定**：容器按 `name` 升序、成员按 `key` 升序，与文件系统遍历顺序无关，可直接用于快照比对；
- **问题归属**：包内的毛病（定义非法、容器名与目录名不符、正文越界/缺失）挂在 `AbilityContainer.issues` 上，`AbilityScanIssue.container` 同时带容器名；**根级**问题（`required` 的扫描根缺失）`container` 为 `None`，不进任何容器；
- **收集式**：单个包坏了照样出现在列表里（带着问题），不因一份坏 yaml 让整面不可读；
- 容器视图是既有扁平读数（`AbilityScanResult.entries` / `.skills` / `.issues`）的**同源派生**，不是第二个权威：`AbilityScanResult.containers` 就是 `scan_abilities()` 的返回；`catalog.CatalogReport.containers` 再透传一层。

**测试**：`ghrah-core/tests/scheme/loading/test_ability_containers.py`（9 例）钉住容器形状、成员排序与字段、`skill_only` 分支、包内/根级问题归属、同名容器落败记录，以及"容器视图与扁平读数同源"。

## 3.15 技能包管理并入 scheme（2026-10-26）

技能能力的实现此前分散在两处：**渲染与读盘**已在 scheme（§3.14），但**扫描、注册指纹、lock、初始化、查询、MCP 渲染、provider** 还在 `ghrah/skills/`。本轮把后者整批搬进 `loading/skill_*`，`ghrah.skills` 只剩别名壳。

| 原模块 | 新模块 | 行数 |
| --- | --- | --- |
| `skills/errors.py` | `loading/skill_errors.py` | 27 |
| `skills/models.py` | `loading/skill_models.py` | 291 |
| `skills/scan.py` | `loading/skill_scan.py` | 360 |
| `skills/fingerprint.py` | `loading/skill_fingerprint.py` | 627 |
| `skills/lock.py` | `loading/skill_lock.py` | 287 |
| `skills/query.py` | `loading/skill_query.py` | 197 |
| `skills/initialize.py` | `loading/skill_init.py` | 137 |
| `skills/mcp_config.py` | `loading/skill_mcp.py` | 283 |
| `skills/provider.py` | `loading/skill_provider.py` | 181 |
| `skills/roots.py` | `loading/skill_roots.py` | 90 |

> 现状更新（§3.16）：表中 `skill_scan` / `skill_query` / `skill_init` / `skill_roots` 四行已随「加载流程合并」删除，能力分别并入 `ability_scan` 与 `ability_roots`；本节保留原表是为了记录搬家时的对应关系。

**手法：模块对象别名，零复制。** 每个壳 18 行，实质只有一句 `sys.modules[__name__] = _target`，于是 `ghrah.skills.scan` 与 `ghrah.scheme.loading.skill_scan` 是同一个模块对象——属性读写、私有助手与 monkeypatch 都落在新实现上。用 star-import 或显式再导出会丢私有名字、让 patch 打到空壳，这条路在旧树兼容层（§3.11）已经踩过。

**消费点全部改指 scheme**：`context/manager.py`（目录注入）、`core/unit.py`（provider 构造）、`list_abilities.py`、`load_skill.py` 四处直接 import `ghrah.scheme.loading.skill_*`，不再经过壳。核心源码里仅剩 `skills/__init__.py` 自身在引用包内模块（聚合导出的职责）。

**门面**：`scheme/__init__.py` 新增 30 个名字（148 → 178）。两处 `SkillDocument` 同名不同形——定义面那个（带容器 / 成员键 / 媒体类型）占用 `ghrah.scheme.SkillDocument`，技能包面那个以 `SkillPackageDocument` 别名导出，避免遮蔽。

**测试**：`ghrah-core/tests/scheme/loading/test_skill_home.py`（9 例）钉住归属——实现模块的 `__module__` 都在 `ghrah.scheme.loading.*`、10 个壳模块 `__name__` 等于 scheme 模块名（同一对象）、门面名字都解析到 scheme、两个 `SkillDocument` 不互相遮蔽。`pytest ghrah-core` → 2379 passed / 4 skipped / 1 failed（Windows 既有路径失败）。

**删除 `ghrah.skills` 的前置条件**（未做，属后续项）：

1. `ghrah-subject/src/ghrah/subject/units/skill.py:21` 是仅剩的外部引用，需改指 `ghrah.scheme.loading.skill_provider`；
2. `skills/catalog.py` / `skills/document.py` 两个转调文件的调用点改指 scheme，`skills/__init__.py` 的聚合导出随之删除；
3. 两个 `SkillDocument` 是否合并（要动 `load_skill` 返回结构与 subject 用法）。

## 3.16 加载流程合并：技能遍历并入 ability 扫盘（2026-10-26）

§3.15 把技能包管理搬进了 scheme，但搬过来的是**一套平行的流程**：`skill_scan` 自己遍历包目录、`skill_query` 自己比对指纹、`skill_init` 自己写锁、`skill_roots` 自己拼根路径。于是同一件事有两份实现——`ability_scan` 遍历一次定义，`skill_scan` 再遍历一次；容器定义里的 `skills:` 成员与磁盘上的技能包还被当成两类对象。

本轮按「加载流程只有一条」合并，**删掉 4 个模块、784 行**：

| 已删除 | 行数 | 能力去向 |
| --- | --- | --- |
| `loading/skill_scan.py` | 360 | → `loading/ability_scan.py`：池扫描的产物里已有 `AbilitySkill`，不再单独遍历技能包 |
| `loading/skill_query.py` | 197 | → `loading/ability_scan.py` 的 `verify_skill()`，读数落在 `AbilitySkill.fingerprint` / `AbilitySkill.lock_issue` |
| `loading/skill_init.py` | 137 | → `loading/ability_scan.py` 的 `ensure_locks()`；外层入口仍是 `SkillAbilityProvider.initialize()` |
| `loading/skill_roots.py` | 90 | → `loading/ability_roots.py`：技能根就是池里的用户根，不再有第二份路径表 |

合并后的读数就是成员本身：

- `AbilitySkill` 增加 `skill_dir` / `lock_path` / `fingerprint` / `lock_issue` 四个字段，`AbilityScanResult` 增加 `lock_issues`——原来分散在 `SkillSummary` 与 `SkillQuery` 两处的信息收进一处；
- `load_ability_skills()` 是唯一的技能成员读数，`catalog`、`skill_context`、`list_abilities` 都从它取；
- **扫描只读盘**：`scan_ability_definitions` / `scan_abilities` / `load_ability_skills` 不写任何文件；写锁只有 `ensure_locks()` 与 `relock_skill()` 两个显式入口。这条是本轮的架构约束——读路径带副作用会把「扫一眼」变成「改状态」。

**技能成员全名收敛**：成员键与容器同名时，`full_name` 取容器名（`research`，不是 `research.research`）。理由：多数技能包是「一个包一个技能」，双写既不自然，也与装配侧一直使用的包名不一致。`operations` 不套用这条规则：能力成员与容器同名是常态，且工具名必须与 `handler` 一致。

**顺带收窄**：

- `ghrah/skills/` 的别名壳从 10 个降到 6 个（`errors` / `fingerprint` / `lock` / `mcp_config` / `models` / `provider`），`__init__.py` 的聚合导出同步缩小；
- `list_abilities` 的成员详情改为读定义面字段（`title` / `tags` / `body_path` / `media_type`），不再解析正文的 frontmatter 头。

**验证**：`pytest ghrah-core` → 2305 passed / 4 skipped / 1 failed（Windows 既有路径失败）；`ruff check .` 与 `ruff format --check .` 全绿（565 文件）；CRLF 0。

**已知副作用**：新格式的正文没有 frontmatter，于是「正文申请 permission」这条声明通道随旧格式一起消失——新包一律从保守默认（`approve=hitl`、`net=deny`）起步，只有 `ability.lock.yaml` 里的核定值是权威。`ghrah-subject/tests/test_units_skill.py` 有 4 例仍在断言旧通道（正文写 `permission: {approve: auto}` 后 lock 出现 `auto`），当前为失败状态，处置见 §8.8 的同一小节。

## 4. 一个已经消除的冗余（供参照）

第一轮搬迁前，"哪些内置能力存在"这件事有两个来源：随包 20 份 yaml 的**硬编码名单** `_BUILTIN_YAML_NAMES` 与磁盘上的 yaml 文件。两者在历史上漂移过一次（名单 18 项、盘上 19 份，`session_info` 永远不被加载且不报错）。

现在名单由定义目录派生（`builtin_full_names()`），yaml 清单已并入 4 份容器定义，`ghrah.manifest.builtins._BUILTIN_YAML_NAMES` 只是派生结果的别名。这条"两层清单靠人工对齐"的成本已经归零。

## 5. 第四轮：按主题分子包（2026-10-26）

此前压平到单层是为了一次性看清全貌；现在按主题分组，只搬**明确落在主题里**的模块，跨主题或属 agent 域的先留最外层。

| 子包 | 模块 | 主题边界 |
| --- | --- | --- |
| `permissions/` | `command_safety.py`、`fs_permissions.py`、`hitl.py` | 只判定"是否允许 / 是否需人工批准"，不做执行 |
| `loading/` | `ability_schema.py`、`ability_view.py`、`ability_roots.py`、`ability_scan.py`、`ability_catalog.py`、`catalog.py`、`manifest_io.py`、`manifest_fields.py`、`tool_list.py`、`skill_context.py`，以及 `skill_errors` / `skill_models` / `skill_scan` / `skill_fingerprint` / `skill_lock` / `skill_query` / `skill_init` / `skill_mcp` / `skill_provider` / `skill_roots` | 声明文件的读取、解析、**目录池扫描**、视图投影，以及给模型装配上下文的两面（工具列表 / 技能目录与正文）；对外只产出可展示目录、可注入工具面与可读取正文 |
| `execution/` | `base.py`、`context.py`、`execution_data.py`、`execution_services.py`、`execution_frame.py`、`executor.py`、`invocation.py`、`hooks.py`、`hook_context.py`、`hook_runner.py`、`hook_store.py`、`registry.py`、`ability_build.py` | 执行上下文、执行器、Hook 控制流与实例化 |

**留在最外层（未分类）**：

| 模块 | 为什么没归类 |
| --- | --- |
| `__init__.py` | 包级门面：只登记「公开名字 → 归属模块」索引表，`__getattr__` 惰性加载（见 §1.2） |
| `errors.py` | 能力域异常，被加载与执行两侧共用 |
| `kinds.py` | 能力类型集合，属"分类事实"，不属任一主题 |
| `paths.py` | 通用路径工具（subject 的 sandbox 也在用），只有 `ABILITY_PATH_SPECS` 服务权限 |
| `_cluster_common.py` | 集群能力小件，更像应随容器走 |
| `agent_manifest.py` / `agent_resolver.py` | 属 agent 域，不是 ability 的三个主题 |

依赖方向（无环）：`loading` → 无内部依赖；`permissions` → 无内部依赖；`execution` → `loading`（`ability_build` 用 `PermissionFlags`）。三个子包的 `__init__.py` 都只有文档字符串，不聚合；顶层 `__init__.py` 也只在**属性访问时**才 `import_module`，因此父子两侧都不会形成初始化环。

## 6. 回退：`ghrah/skills/` 整包恢复到 HEAD（2026-10-26）

上一轮把技能包管理的实现搬进 `scheme/loading/skill_*`，`ghrah/skills/` 只剩薄壳。随后决定**不再改动这个包**，整包回退到 HEAD 原状，scheme 侧的新实现保持独立存在。

**执行**：`git checkout HEAD -- ghrah-core/src/ghrah/skills`（11 个文件）；删除 HEAD 中不存在、由过渡期新增的 `fingerprint.py` 与 `mcp_config.py`；`git diff --stat HEAD -- ghrah-core/src/ghrah/skills` 输出为空。

**回退后的现状**：

| 项 | 值 |
| --- | --- |
| 模块数 | 11 个（`__init__` / `catalog` / `document` / `errors` / `initialize` / `lock` / `models` / `provider` / `query` / `roots` / `scan`） |
| 格式支持 | **只认旧格式**：包根 `SKILL.md` + frontmatter，锁 schema 1（`provenance.fingerprint` 单文件摘要） |
| 生产消费者 | 仅 `ghrah-subject/src/ghrah/subject/units/skill.py`（`from ghrah.skills import SkillAbilityProvider`）；`ghrah-core/src` 内无消费者 |
| 与 scheme 的关系 | 无 import 关系。`scheme/loading/` 的 `skill_*`（定义面扫描、schema 2 树指纹、锁）独立可用，能力面走这一套 |

**测试随之改指 scheme**（那不是"包里的内容"，而是调用方）：

- `ghrah-core/tests/skills/` **整目录也回退到 HEAD**（`test_catalog` / `test_document` / `test_initialize` / `test_provider` / `test_query` / `test_scan`，91 例全绿）——它测的是被回退的包，壳时代的改法不再适用；
- 仅为壳而新增的 `ghrah-core/tests/skills/test_fingerprint.py`、`test_mcp_config.py` 删除（对应覆盖已在 `ghrah-core/tests/scheme/loading/test_skill_lifecycle.py`、`test_ability_scan.py`）；
- 调用方测试改指 scheme：`ghrah-core/tests/context/test_skill_catalog_injection.py`、`ghrah-core/tests/abilities/builtin/test_list_abilities.py`、`ghrah-core/tests/abilities/builtin/test_load_skill.py`；
- 删除 `ghrah-core/tests/scheme/loading/test_skill_home.py`（断言"旧路径是 scheme 的别名"，前提已不存在）；
- `ghrah-subject/tests/test_units_skill.py` 回退到 HEAD（重新断言 schema 1）——subject 的 skill unit 用的就是旧格式锁。

**验证**：`uv run pytest ghrah-core -q` → 2313 passed / 4 skipped / 1 failed（既有 Windows 路径断言 `test_fs_permissions`）；`uv run pytest ghrah-subject -q` → 717 passed / 7 skipped；`ruff check .` 与 `ruff format --check .` 全绿。

**遗留（需要决策）**：能力面与 subject 的技能面现在跑两套实现——scheme 只认新格式定义面，subject 的 skill unit 只认旧格式包。要么把 `ghrah-subject/src/ghrah/subject/units/skill.py` 改成调 scheme（会同时改写 subject 的落盘格式），要么接受两套并存。这一步超出"只改 `ghrah-core/`"的边界，等确认。

## 7. 遗留：声明面与实现面是两份 schema（2026-10-28 记账，暂不修）

**现象**：同一个 operation 的参数形状被描述了两遍——`ability.yaml` 的 `operations.<键>.input_schema`（声明面，进 `load_tool_list()` / `list_abilities` 的读数）与实现脚本 `bind_tool()` 里 `model_json_schema()` 的结果（实现面，模型实际调用的工具面）。

**实测**（内置树 22 个成员，19 个可比）：**18 个存在差异**。多数是 pydantic 自动补的装饰（`title` / `additionalProperties: false` / `strict`），语义差异 5 处：

| 能力 | 差异 |
| --- | --- |
| `load_skill` | 声明面缺 `server`、`tool`（实现接受） |
| `search_files` | 声明面缺 `context_lines` 的 min/max、`max_results` 边界、`include` 可空 |
| `recall_context` | 声明面 `mode` 是自由字符串，实现是枚举 |
| `execute_command` | `working_dir` 声明非空、实现可空 |
| `list_directory` | `limit` / `offset` 声明有 `minimum: 0`、实现没有 |

**当前口径（已定）**：以实现面为准。模型看到的工具面来自 `bind_tool()`；声明面的 `input_schema` 只是文档性载荷，不参与校验。

**为什么现在不修**：两条收敛路径都要动全部 21 个实现脚本或全部 5 份 `ability.yaml`，且会把「定义面自足」这条性质削弱——留着记账，等确认哪一侧该让位（候选：①`bind_tool()` 从声明投影；②删 `input_schema` 只留实现面；③保留两份但加一致性测试钉住）。

**风险**：新增能力时若只改一侧，模型看到的能力说明书与实际校验会不一致；模型按声明面传参可能被实现面拒绝。
