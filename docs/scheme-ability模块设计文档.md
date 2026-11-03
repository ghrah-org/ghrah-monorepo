# 能力模块（`ghrah.ability`）设计

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-DESIGN-008 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `77f7267`（2026-11-03），分支 `andrew-dev-new`，`ghrah-core` 0.2.0a0 |
| 读者 | 要改本模块任一文件的人；新增或删除能力成员的人；写能力包的人；评审能力契约、授权边界与装配顺序的人 |
| 自闭环 | 本文**不依赖任何其它文档**。包结构、定义面逐字段、目录池、扫描、运行时投影、工具面、技能成员与 lock/漂移、内容与授权面、执行面、Hook、权限与 HITL、装配现状、不变式与已知偏差，全部在本文件内描述；每条论断后跟 `文件路径:行号`（行号以校准基线为准，符号名不会漂移） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-30 | 首版（针对 `scheme_ability/` 包与 `0.1-draft` 定义格式） |
| 2.0 | 2026-11-03 | 按 `77f7267` 全量重写：包名改为 `ghrah.ability`；定义面改为 v1 两段式（手写区 + 生成区 `members`）；工具面唯一来源改为实现自己的 `bind_tool()`；技能发现改为以磁盘 `SKILL.md` 为准；新增内容/授权面（版本捕获、授予、受控读取）；新增分层 `loading/member_index`；计数口径与不变式 INV-01~INV-12、已知偏差 D-1~D-10 全部重写 |

## 1. 范围

### 1.1 本文描述什么

`ghrah-core/src/ghrah/ability/` 这一个包：一个能力包如何被定义、如何被发现、如何投影成模型工具、一次工具调用如何被执行、内容如何被冻结与授权、以及装配期由谁把这些对象接起来。

**权威顺序：代码 > 本文。** 冲突时以代码为准，并把冲突当作文档缺陷来修。

本包对外有三个面，外加一层支撑：

| 面 | 回答的问题 | 主入口 |
| --- | --- | --- |
| 目录面 | 现在盘上有哪些能力包、哪些技能、技能正文是什么 | `scan_abilities()` / `load_skill_catalog()` |
| 实现面 | 某个成员怎么变成模型工具、怎么造出实例并执行 | `load_all_builtin_manifests()` / `create_ability()` |
| 内容/授权面 | 这份内容是哪一份、被谁、以什么权限读过 | `capture_ability_version()` / `AbilityAccessStore` / `AbilitySchemeHost` |
| 执行支撑 | 调用落地时的上下文、Hook、权限与 HITL | `execution/`、`permissions/` |

### 1.2 路径缩写

| 缩写 | 展开（相对仓库根） |
| --- | --- |
| `A/...` | `ghrah-core/src/ghrah/ability/...` |
| `A/loading/...` | `ghrah-core/src/ghrah/ability/loading/...` |
| `unit.py` | `ghrah-core/src/ghrah/core/unit.py` |
| `proto/...` | `ghrah-protocol/src/ghrah/protocol/...` |

### 1.3 计数口径

「有多少个能力」有四个互不相同的数字，本文每次引用都写明是哪一个，差额原因见 §10 与 §11：

| 口径 | 数字 | 取数 API |
| --- | --- | --- |
| handler 全集（内置） | **22** | `builtin_handlers()`（`A/loading/ability_catalog.py:466`） |
| 运行时视图 | **21** | `load_all_builtin_manifests()`（`A/loading/ability_catalog.py:395`） |
| 模型工具面 | **19** | 上述视图里 `manifest.tool is not None` 的条数 |
| 顶层导出名字 | **27** | `len(ghrah.ability.__all__)` |

差额来源固定两条：`send` 是 registry-only（`REGISTRY_ONLY_OPERATIONS`，`A/loading/ability_catalog.py:79`）不进运行时视图；`conversation` 与 `end_task` 的实现 `bind_tool()` 返回 `None`（后者依赖构造模式），不进工具面。

容器数（内置 5 个：`fs` `core` `cluster` `mcp` `shell`）与用户池里的包数属于**环境相关**读数，不在上表统计。

### 1.4 不在本文范围

- **驱动循环**：一次 LLM 调用如何触发工具批、上下如何压缩——本文只写到本包交给执行器的接口为止。
- **Subject 侧的配置解析**：只给默认值与注入锚点。
- **`ghrah.mcp` 包内部**：只描述能力侧如何通过 `provider_slot` 与它对接。
- **`ghrah.scheme` 控制平面**：本文只描述能力侧如何被装进一个 Scheme Provider。

## 2. 术语

| 术语 | 含义 |
| --- | --- |
| 能力包（ability package） | 一个含 `ability.yaml` 的目录；也可以只有 `skill/SKILL.md` 或 `operations/*.py` |
| 容器（container） | 能力包的运行时身份，名字 = 目录名 = `identity.namespace`（或 `identity.name`） |
| 成员（member） | 容器里的一个 operation 或 skill |
| 定义面（definition） | `ability.yaml`：手写区 + 生成区 |
| 生成区（`members`） | 由工具按磁盘扫描写出的成员索引 |
| 目录池（pool） | 若干扫描根，按 `(rank, label)` 排序 |
| 合成视图（synthesized） | 定义面缺失或不受支持时，按目录与正文推出的只读定义 |
| 运行时视图（manifest） | `AbilityManifest`：容器 + 成员 + 工具 schema + 权限元数据 |
| 内容版本（version） | 一次内容快照：定义 + 清单 + 字节，按摘要寻址 |
| 授予（grant） | 某个 Agent 对某个实例某批成员的访问权 |

## 3. 总体结构

### 3.1 三个面 + 一层支撑

```
         定义面（作者写什么）
   ability.yaml 手写区 ──┐
   skill/SKILL.md        ├──► 目录面 ──► 模型上下文（技能清单 / 正文）
   operations/*.py ──────┘        └────► 实现面 ──► 工具面 ──► 执行
                                        └────► 内容/授权面 ──► 版本 / 授予 / 受控读取
```

三个面共用**同一次扫描**（§9），但投影出不同的视图：

| 视图类 | 产出 | 消费者 |
| --- | --- | --- |
| `AbilityContainer` | 成员 + 问题，按容器聚合 | 面板、`list_abilities`、调试 |
| `AbilitySkill` | 技能成员（含正文路径与指纹） | 技能目录注入、`load_skill` |
| `AbilityManifest` | 工具 schema + 权限元数据 | 工具面注入、执行器 |

### 3.2 零缓存

所有查询每次重新扫盘（`A/loading/ability_scan.py:806` 的 docstring 明确写死）。热插拔因此自然生效：删掉一个包，下一次调用就看不到它；改一个 `SKILL.md`，下一次目录渲染就变。禁止在调用方加 `lru_cache`——那会引入第二权威。

### 3.3 数据流（一次工具调用）

```
ability.yaml ──parse──► AbilityDefinition ──project──► AbilityManifest{tool}
                                                            │
模型 tool_calls ──► 执行器 ──► create_ability(handler) ──────┘
                                   │
                                   ▼
                         Ability.execute(context)
```

## 4. 依赖形状

### 4.1 分层

```
ability_schema / skill_frontmatter      模型与解析
        ▲
loading/member_index                    纯扫描原语：目录 → members（只读）
        ▲                    ▲
loading/ability_scan        build       目录面（读） | 生成区与安装期写入（写）
        ▲                    ▲
skill_provider  ──惰性──►  build.ensure_members
```

依赖只朝下：

- `member_index` 只依赖 `ability_schema` / `manifest_fields`，**不写盘、不 import 包内代码**；
- `ability_scan` 依赖 `member_index`，**不依赖 `build`**（有测试守着：`import ability_scan` 不会把 `build` 拉进 `sys.modules`）；
- `build` 依赖 `member_index` 与 `ability_schema`，是唯一写 `ability.yaml` 的模块；
- `skill_provider.initialize()` 惰性 import `build.ensure_members`——写入只发生在明确的安装期调用点。

### 4.2 禁止出现的方向

- 扫描层 → 构建层（会把"只读发现"变成"装配期写盘"）。
- 定义层 → 投影层（schema 不得 import catalog / execution）。
- `loading/` → `access/` 或 `scheme/`（内容面在自己的包里，反向依赖会让目录面拖上 sqlite 与 Scheme）。

### 4.3 对包外的依赖

| 依赖 | 用途 |
| --- | --- |
| `ghrah.protocol.ability_tree` | 授权视图的 wire 契约（`AbilityTreeView` / `GrantAbilityRequest`…） |
| `ghrah.manifest.types` | `PermissionFlags` / `ToolSchema` / `AbilityMetadata`（运行时视图形态） |
| `ghrah.scheme` | `ProviderCall` / `SchemeModel` / `Requester`（内容面挂到 Scheme） |
| `pydantic` / `yaml` | 定义面模型与 YAML 解析 |
| `ghrah.core._base_error` | `ActorAgentError` 基类（`skill_context`） |

## 5. 包结构

### 5.1 顶层

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `__init__.py` | 69 | 27 个公开名字的门面 |
| `build.py` | 518 | 生成区读写、`bootstrap` / `migrate`、安装期 `ensure_members`、CLI |
| `versions.py` | 224 | 内容捕获（`capture_ability_version` / `capture_file_version`） |
| `content.py` | 265 | 逐成员字节捕获与路径/链接/上限校验 |
| `file_members.py` | 115 | 文件切片的两个成员契约（`FILE_OPERATIONS`） |
| `file_backend.py` | 44 | `FileBackend` 契约（切片执行侧） |
| `contracts.py` | 29 | 成功/失败结果 schema 导出 |
| `source.py` | 88 | `BuiltinAbilitySource`：声明、实例、读取三合一的来源门面 |
| `assembly.py` | 264 | `AbilityAssembly`：选择 legacy / builtin 两种来源 |
| `kinds.py` / `paths.py` / `_cluster_common.py` | 42 / 76 / 50 | 成员分类、路径工具、集群成员共用件 |
| `consumer.py` | 94 | `GrantedContentProvider`：按成员地址受控读取 |

### 5.2 `loading/`（19 个文件）

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `ability_schema.py` | 724 | v1 定义模型、`parse_definition`、归一化 |
| `ability_roots.py` | 220 | 扫描根、`default_pool` / `path_root` / `builtin_root` / `ensure_roots` |
| `ability_scan.py` | 1233 | 目录面：发现、合成、对齐、问题、lock 校验 |
| `member_index.py` | 208 | 纯扫描原语：目录 → `members` |
| `ability_catalog.py` | 560 | 实现面：handler → 实现类 → `AbilityManifest` |
| `catalog.py` | 114 | 目录面 + 实现面的合并报告（`CatalogReport`） |
| `skill_frontmatter.py` | 108 | `SKILL.md` frontmatter 解析（技能名字/说明的唯一作者） |
| `skill_context.py` | 403 | 技能清单、目录渲染、正文读取（`SkillCatalog`） |
| `skill_provider.py` | 264 | `SkillAbilityProvider`：扫描、读取、初始化、重登 |
| `skill_models.py` / `skill_fingerprint.py` / `skill_lock.py` / `skill_errors.py` | 218 / 657 / 297 / 27 | 技能成员模型、整树指纹、lock 读写、错误类型 |
| `ability_view.py` / `manifest_fields.py` | 6 / 40 | 视图开关、manifest 字段校验 |

### 5.3 其余子包

| 包 | 文件 | 职责 |
| --- | --- | --- |
| `access/` | `store.py`（948）、`__init__.py` | sqlite 权威：版本、实例、授予、工具绑定、变更日志 |
| `scheme/` | `host.py`（694） | 把授权视图装成 Scheme Provider，提供 `session()` 与四个控制原语 |
| `execution/` | `base.py` `context.py` `hooks.py` `ability_build.py` `invocation.py` `execution_data.py` `execution_services.py` | 执行契约、上下文、Hook、装配期实例化 |
| `permissions/` | `fs_permissions.py` `command_safety.py` `hitl.py` | 文件路径判定、命令分级、HITL 包装 |
| `builtin/` | 5 个容器、22 个成员脚本 | 内置能力实现（`operations/*.py` 未随本次重构改动） |

## 6. 导出契约

`ghrah.ability.__all__` 恰好 27 个名字，分四组：

| 组 | 名字 |
| --- | --- |
| 目录面 | `scan_abilities` `scan_catalog` `default_pool` `AbilityRoot` `AbilityContainer` `AbilityOperation` `AbilityAsset` `AbilitySkill` `AbilityScanResult` `CatalogReport` |
| 技能 | `load_skill_catalog` `SkillCatalog` `SkillAbilityProvider` |
| 定义/投影 | `parse` 层 `AbilityDefinition` `AbilityIdentity` `OperationDefinition` `ProviderRequirement` `ExecutionSpec` `SkillDefinition`；投影层 `load_all_builtin_manifests` `ability_class` `create_ability` `Operation` |
| 装配 | `AbilityAssembly` `AbilityAssemblyError` `AbilitySourceMode` `BuiltinAbilitySource` |

子包各自带 `__all__`：`access`（4）、`scheme`（2）；`execution` / `permissions` 不导出符号，只按模块路径使用（`from ghrah.ability.execution.hooks import HookPoint`）。

**不做**惰性导出门面：名字少、分层清楚，直接 import 比 163 条 `__getattr__` 更容易看清依赖方向。

## 7. 定义面：`ability.yaml`

### 7.1 位置与两段式

`<扫描根>/<容器>/ability.yaml`（`A/loading/ability_schema.py:38`）。文件分两段：

```yaml
# ── 作者手写，工具不改 ──
format: ghrah.ability-definition
schema_version: '1'
identity: {namespace: acme, name: reporting, version: '1.4.0'}
title: Reporting
description: 生成周报与月报，按模板渲染。
provider_requirements: {...}          # 可选

# ── 以下由 `ghrah ability build` 生成，勿手改 ──
members:
  operations: {...}
  skill: {...}
  references: [...]
  assets: [...]
```

手写区逐字节保留；生成区从标记行（或裸 `members:` 行）到文件尾整体替换（`A/build.py` 的 `_authored_part` / `_compose`）。重建幂等：第二次运行 `changed == False`。

### 7.2 手写区字段

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `format` | 是 | 固定 `ghrah.ability-definition` |
| `schema_version` | 是 | 固定 `'1'`（未加引号的整数 1 也接受） |
| `identity` | 是 | 见 §7.3 |
| `title` | 是 | 非空字符串 |
| `description` | 是 | 非空字符串 |
| `provider_requirements` | 否 | 只有 `provider` 型成员才需要 |

模型 `extra="forbid"`：写错字段名直接报错，不静默忽略。

### 7.3 `identity`

| 字段 | 约束 |
| --- | --- |
| `namespace` | `^[a-z][a-z0-9_-]*$` |
| `name` | 可选；省略时取 `namespace`。**必须等于目录名**，否则扫描记问题并跳过 |
| `version` | 非空字符串（包版本，不参与寻址） |

`full_name` = `namespace.name`（`name` 省略时 `full_name == namespace`）。

### 7.4 `provider_requirements`

```yaml
provider_requirements:
  fs_backend: {contract: ghrah.fs.v1, required_capabilities: [request]}
```

`provider` 型成员必须在这里声明同名槽，否则我们不知道它要什么契约。当前只有文件后端 `fs_backend`（契约 `ghrah.fs.v1`）被运行时消费；`script` 槽只作声明（见 D-1）。

### 7.5 `members.operations.<key>`

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `title` | 是 | 成员标题（写入时取实现的 `TITLE`，缺席由键生成） |
| `description` | 是 | 一行说明（取实现的 `SUMMARY`） |
| `entry` | 二选一 | 包内脚本路径（`operations/<key>.py` 或自定义，如 `scripts/render.py`） |
| `provider` | 二选一 | 运行期后端槽名 |
| `effects` | 否 | 副作用标签列表（取实现的 `EFFECTS`） |
| `expose` | 否 | 见 §7.7，缺省 `true` |

校验：`entry` 与 `provider` 至少一个；一个包内**成员键唯一**；`entry` 路径必须是包内相对 POSIX 路径。

### 7.6 `members.skill`

```yaml
skill: {body: skill/SKILL.md, media_type: text/markdown, key: guide}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `body` | 是 | 正文相对路径（本版约定 `skill/SKILL.md`） |
| `media_type` | 是 | 目前恒为 `text/markdown` |
| `key` | 否 | 成员键，缺省等于容器名；只有历史上用过别的键时才写（改键会破坏既有授权地址） |

**`members.skill` 不承载标题与说明**：那两个字段的作者是正文 frontmatter（§12.1），索引里不抄第二份。

### 7.7 `expose`：授权面可见性

`expose: false` 表示该成员**不可授予 / 不发布 / 不可被选为工具**（离线可判定）。它**不决定模型工具面**——工具面由实现 `bind_tool()` 是否为 `None` 决定（§11.1）。两者刻意分开：

- `conversation` 没有工具绑定，不需要 `expose` 就已经不在工具面；
- 控制面成员可以"有工具绑定但不许授予"。

消费点：`A/access/store.py:437`、`:575`、`:657`、`:783`（视图与授予校验）与 `A/scheme/host.py:337`（文件动作导出）。`build` 重建时保留盘上的 `expose: false`（扫不出来，属于作者意图）。

### 7.8 `references` / `assets`

```yaml
references: [{path: skill/references/style.md, media_type: text/markdown, digest: 'sha256:...'}]
assets:     [{path: skill/assets/template.docx, media_type: application/vnd...}]
```

- 位置：**技能目录内部**（`skill/references/`、`skill/assets/`），不跨到包根；
- 成员键 = 文件名主干，重名直接报错；
- 没有正文就没有附件归属——此时目录存在也只当普通子目录，不报错也不写进生成区。

### 7.9 生成规则

`scan_members`（`A/loading/member_index.py:40`）只读目录与实现脚本的模块级字面量（AST），不 import、不执行。写回时做两件事（`A/build.py:93`）：

1. **重算扫得出来的**：`operations` 取 `operations/*.py`，`skill` 取正文位置，附件取技能目录；
2. **带回扫不出来的作者意图**（`_keep_declared`）：`provider` 型成员、`entry` 不在 `operations/` 下的成员、`expose: false`、非容器名的技能键、别名正文路径。

写盘前把"手写区 + 新生成区"整体重新解析一次；读不回来的结果不会被写出去。

### 7.10 补定义：`bootstrap` 与 `migrate`

| 情况 | 入口 | 行为 |
| --- | --- | --- |
| 没有 `ability.yaml` | `write_members(pkg, bootstrap=True)` 或 `initialize()` | 按目录与正文生成最小 v1 定义（容器名取目录名、说明取 frontmatter 或旧格式残留） |
| 旧格式（`0.1-draft` / `0.2`） | `write_members(pkg, migrate=True)` / `--migrate` | 身份、标题、说明从旧文件搬运，`members` 按磁盘重建 |

CLI：`python -m ghrah.ability.build <包...> [--dry-run|--check|--migrate]`。

### 7.11 校验与错误

| 触发 | 错误 |
| --- | --- |
| `schema_version` 不是 `'1'` | `不支持的 schema_version ... 本实现只认 '1'` |
| 未知字段 | pydantic `extra_forbidden` |
| 成员既无 `entry` 也无 `provider` | `成员必须声明 entry 或 provider 至少一项` |
| `provider` 未在 `provider_requirements` 声明 | `provider 'x' 未在 provider_requirements 声明` |
| 容器名 ≠ 目录名 | 扫描期记问题并跳过 |
| 重复键（YAML 层） | `重复键`（自定义加载器检测） |

## 8. 目录池

`AbilityRoot`（`A/loading/ability_roots.py:71`）描述一个扫描根：`locator`（`Path` 或包资源）、`rank`（越小越优先）、`source`（来源标签）、`required`（缺失算不算问题），派生属性 `label` / `path` / `writable`。

| rank | 标签 | 位置 |
| --- | --- | --- |
| 0 | `builtin` | `A/builtin`（包内资源，只读） |
| 100 | `skill-ability` | `~/.ghrah/skill-ability`（用户目录） |

冲突裁决：**同名容器先出现者胜出**（`(rank, label)` 升序），落败副本在胜出者的 `issues` 里留一条——可见，不静默覆盖。`ensure_roots` 只创建缺失的用户目录；`path_root(dir)` 用来造单根池（测试与调试脚本大量使用）。

## 9. 扫描（目录面）

`scan_ability_definitions(pool)`（`A/loading/ability_scan.py:806`）是唯一扫描实现，`scan_abilities` / `load_skill_catalog` / `SkillAbilityProvider.scan` 都是它的薄包装。

### 9.1 发现口径：磁盘

一个目录算不算能力包，看**盘上有什么**（`_looks_like_package`，`A/loading/ability_scan.py`）：

- 有 `skill/SKILL.md`（或 `skills/SKILL.md`、根 `SKILL.md`）；
- 或有 `operations/*.py`。

`ability.yaml` 缺失或解析失败**不再导致整包丢弃**——这是与 1.0 最大的行为差异。

### 9.2 合成视图

定义面不可用时（缺失、旧格式、字段非法），扫描按目录与正文合成一份只读定义（`_synthesized_definition`，`A/loading/ability_scan.py`）：

- 容器名取目录名，版本 `'0'`；
- 标题/说明：正文 frontmatter → 旧 YAML 里的 `title` / `description` → 目录名；
- 成员由 `scan_members` 扫出；
- 容器标 `synthesized=True`，并在 `issues` 里记一条带修复命令的问题。

合成视图只用于**读**：它不写 `ability.yaml`，补定义要显式跑 `build`（§7.10）。

### 9.3 技能成员对齐（磁盘优先）

定义面合法但"没跟上磁盘"时（`_align_skill_with_disk`，`A/loading/ability_scan.py:570`）：

| 磁盘 | 定义面 | 处理 |
| --- | --- | --- |
| 有正文 | 没声明 `members.skill` | 补出成员（键 = 容器名），记一条问题 |
| 有正文 | 声明的正文路径不存在 | 改用盘上那份，记一条问题 |
| 有正文 | 声明一致 | 信定义面，不记问题 |

### 9.4 问题分类（`AbilityScanIssue`）

| 类别 | 例子 |
| --- | --- |
| 根级 | `required` 根不存在、根不可列举 |
| 定义面 | 解析失败、容器名与目录名不符、`provider` 未声明 |
| 成员面 | 正文不存在、`SKILL.md` 链接指向未登记文件 |
| 合成/对齐 | `缺少 ability.yaml；已按目录与 SKILL.md 合成只读视图` |
| 登记面（`lock_issues`） | lock 读不出来、登记时处于降级状态 |

`issues` 与 `lock_issues` 分开：前者意味着"成员坏了"，后者只是"漂移判定不可用"。`SkillCatalog.from_scan` 把两者合并后暴露给目录面。

### 9.5 返回结构

`AbilityScanResult`：`roots` / `entries` / `skills` / `issues` / `lock_issues`，并提供 `.containers`、`.definitions` 两个聚合视图。`AbilityContainer` 带 `name` `namespace` `version` `title` `description` `operations` `skills` `assets` `issues` `synthesized` 与 `kinds` / `full_name` 两个派生属性。

## 10. 运行时投影（实现面）

只扫**内置树**，遇错直接抛（内置定义坏了是发布事故，不降级成问题条目）。

```
handler ─► _operation_module ─► import ghrah.ability.builtin.<容器>.operations.<key>
        ─► _operation_class  ─► type[Ability]（恰好一个实现类）
        ─► 读 PERMISSIONS / SUMMARY / TAGS ─► _project ─► AbilityManifest
```

| 常量 | 类型 | 作用 |
| --- | --- | --- |
| `PERMISSIONS` | dict | → `PermissionFlags`（安全声明的唯一来源） |
| `SUMMARY` | str | 目录里的一行说明 |
| `TAGS` | list[str] | 标签 |
| `TITLE` / `EFFECTS` | 可选 | 供生成区使用（投影不读） |

`AbilityManifest` 携带 `metadata`（标题/说明/标签/权限）与 `tool`（`ToolSchema | None`）。挂载面**不填** `hooks`——安全语义由 `PERMISSIONS` 声明、内核固定实现。

## 11. 工具面

### 11.1 唯一来源：`bind_tool()`

`_project` 实例化实现类并调用 `bind_tool()`（`A/loading/ability_catalog.py:286`），拿 `{"type": "function", "function": {name, description, parameters}}`；返回 `None` 即该成员不进工具面。定义面**不写** `input_schema`，因此工具面不可能与实现对不上。

`Ability.bind_tool()` 是抽象方法（`A/execution/base.py:70`），实现必须显式表态，避免"忘了写工具"被当成"有默认工具"。

### 11.2 形状与可空参数

工具 schema 经 `ToolSchema`（`ghrah.manifest.types`）校验后转 OpenAI 形状。pydantic 生成的 `{"anyOf": [T, null]}` 由 `_unwrap_nullable` 收成单一类型——工具面表达不了 `anyOf`，不收敛会直接校验失败。

### 11.3 模式相关工具（已知偏差 D-2）

`end_task` 的 `bind_tool()` 依赖构造参数（`mode="toolcall"` 才绑定），投影面只会零参构造，因此 `list_abilities` 显示它没有工具；运行时装配走 `create_ability("end_task", mode="toolcall")`，执行不受影响。

## 12. 技能成员与 lock

### 12.1 正文与 frontmatter

技能正文放在**能力包内的 `skill/` 目录**，该目录本身就是一个标准 skill 包：

```
reporting/skill/
├── SKILL.md
├── references/style-guide.md
└── assets/template.docx
```

`SKILL.md` 头部是 YAML frontmatter，**技能名字与说明的唯一作者**：

```markdown
---
name: reporting-guide
description: 使用获准模板和写作指南整理周报与月报。
---
```

解析见 `parse_skill_frontmatter`（`A/loading/skill_frontmatter.py:54`）：两个字段都必填非空；其余键容忍但不解释；缺字段或块未闭合抛 `SkillParseError`。正文没有 frontmatter 是**合法状态**（历史包把说明写在定义面），扫描会回退到定义面，捕获期对 v1 包则要求 frontmatter。

### 12.2 两段式注入

- **目录常驻**：`SkillCatalog.render()` 产出 `<system-reminder>` 包裹的清单（名字 + 截断到 500 字符的一行说明），摘要 `digest` 只覆盖名字与说明——正文改动不触发重新注入。
- **正文按需**：`catalog.read(name)` 逐字返回含 frontmatter 的原文，不裁剪。

### 12.3 指纹与 lock

整树指纹 `ghrah-skill-tree-v1`（`MANIFEST_MAGIC`，`A/loading/skill_fingerprint.py:64`），成员级 `ghrah-skill-entry-v1`。lock 文件固定在**容器根**：`<容器>/ability.lock.yaml`（`LOCK_FILE_NAME`，`A/loading/ability_schema.py:39`）。

lock 记录每个成员的登记摘要；扫描时对每个成员算当前摘要并给出漂移判定（`drifted`）。登记面问题进 `lock_issues`，不影响成员可读。

| 动作 | 入口 | 时机 |
| --- | --- | --- |
| 补缺失 lock | `ensure_locks(scan)` | 安装期 |
| 强制重登 | `relock_skill(skill)` / `provider.relock(name)` | 作者确认内容变更后 |
| 校验 | `verify_skill(skill)` / `provider.verify(name)` | 体检、CI |

### 12.4 `SkillAbilityProvider`

`A/loading/skill_provider.py:50`。构造只固定目录池，不碰文件系统；`scan()` / `read_skill()` / `initialize()` / `verify()` / `relock()` 各自是一次独立读数。

`initialize()`（`:188`）的顺序是：确保目录 → 扫描 → **补生成区**（`build.ensure_members`）→ 有写入则重扫 → 补缺失 lock。生成区与 lock 在同一生命周期、同一落点，且都不覆盖既有内容。

## 13. 内容 / 授权面

### 13.1 捕获

`capture_ability_version(raw, *, root, limits)`（`A/versions.py:169`）：

```
(raw, root) ─► parse_definition ─► 逐成员 capture_bytes ─► AbilityVersion
                                     │
                                     ├─ definition_json（归一化定义）
                                     ├─ manifest_json（成员地址 → {digest,size,media_type}）
                                     └─ blobs（原始字节）
```

- 只捕获**定义面声明过**的成员：技能正文、`references` / `assets`、`operations/<key>/script`；
- 路径校验：绝对路径、`..`、反斜杠、符号链接一律拒绝；
- 声明里给了 `digest` 就校验，不符即失败；
- `AbilityVersion.digest` 由「规范定义 + 实现摘要」派生，`content_digest` 覆盖内容清单。

`capture_bytes`（`A/content.py:67`）按平台分两个实现：有 `os.O_DIRECTORY` 时用目录描述符逐级 `NOFOLLOW` 打开（路径替换与符号链接被内核挡在包外）；否则（Windows）解析后校验仍在包根内、`lstat` 拒绝链接，再按真实路径读。两者判定口径一致：必须是包内普通文件、不在捕获期间变化、不超上限。

### 13.2 文件切片

`capture_file_version(raw=None)`（`A/versions.py:77`）只保留两个只读成员（`list_directory` / `read_file`），把成员重新绑到 `fs_backend` 契约并盖章 schema：输入取实现 `bind_tool()`，成功数据取 `FileMember`（`A/file_members.py:30`）。文件切片不执行快照脚本，因此这条路径不读 `script`。

### 13.3 存储

`AbilityAccessStore`（`A/access/store.py:112`）是唯一写侧，单 sqlite 文件，schema 版本 3：

| 表 | 内容 |
| --- | --- |
| `ability_versions` | 已提交版本（digest、definition、manifest、implementation 摘要） |
| `ability_content` | 版本内每个成员的字节与摘要 |
| `ability_instances` | 实例 → 版本 digest（+ 可选文件根） |
| `ability_agents` | 登记过的 Agent |
| `ability_grants` | 授予（别名、整包或成员子集、允许根、读范围） |
| `ability_tools` | Agent 的工具名绑定 |
| `ability_changes` | 变更日志（revision 来源） |

写路径只有一条：`commit_version(admin, version)`（`:290`）→ `register_agent` → `select_instance` → `grant` / `revoke` / `bind_tool` / `unbind_tool`。每次写都要 `expected_revision`，冲突报 `conflict` 而不是覆盖。`commit_version` 会核对清单键集与定义推导出的成员集完全一致，并复核每个字节的摘要。

### 13.4 视图

`view(actor, agent)`（`A/access/store.py:711`）每次从 sqlite 重建 `AbilityTreeView`（`proto/ability_tree.py:111`）：`AbilityNode`（别名、实例、成员、工具绑定）→ `SkillNode` / `OperationNode` → `ContentResourceNode`。视图是投影，不是第二权威。

### 13.5 Scheme 装配与受控读取

`AbilitySchemeHost`（`A/scheme/host.py:228`）：

| 方法 | 作用 |
| --- | --- |
| `install(instance)`（`:305`） | 把一个实例装成 Provider：导出树、文件动作与内容读取三类调用 |
| `session(principal)`（`:374`） | 为一次请求开 session（`AbilityTreeSession`） |
| `authorize` / `admit` | 执行前复核授权与准入 |
| `restore(actor)` | 冷启动后按库里已授予关系重建投影 |
| `close()` | 收尾（幂等） |

受控读取只经过 HTTP 形状的成员地址：`skill/<键>`、`skill/<键>/assets/<名>`、`skill/<键>/references/<名>`、`operations/<键>`。每次读都复核授权，撤销后旧引用立即失效。

### 13.6 消费者

`GrantedContentProvider`（`A/consumer.py:17`）：`catalog_text()` / `read(alias, member)` / `read_relative(alias, skill_key, path)` / `load_into_context(...)`。正文只有 `text/*` 且不超 `max_bytes` 才允许注入 Context；二进制返回 base64 由调用方处理。

## 14. 执行支撑

### 14.1 `Ability` 契约

`A/execution/base.py:32`：抽象成员 `name`（成员键）、`execute(context) -> ActionResult`、`bind_tool() -> dict | None`。一个脚本恰好一个实现类，类名不限。

### 14.2 上下文

`AbilityExecutionContext`（`A/execution/context.py`）承载会话、权限 checker、hook 运行器、数据总线（`execution_data`）与服务总线（`execution_services`）。成员只依赖这两个总线，不直接拿 Core 内部对象。

### 14.3 Hook

`HookPoint`（`A/execution/hooks.py:41`）共十点：drive_loop 级 4 个（before/after action、on_error、on_max_iterations）、action 级 4 个（pre/post LLM、pre/post tool）、ability 级 2 个（pre/post execute）。`HookRunner` 是唯一执行路径；Hook 的注册与内建实现都在 Core，不在能力包内声明。

### 14.4 权限与 HITL

| 模块 | 职责 |
| --- | --- |
| `permissions/fs_permissions.py` | 路径判定：允许/拒绝根、`fs_read_only`、越界即拒 |
| `permissions/command_safety.py` | 命令分级：安全列表、危险模式、需审批 |
| `permissions/hitl.py` | `HITLRequiredAbility` 包装：执行前发审批请求，批准才继续 |

判定的权威是成员的 `PERMISSIONS` 声明 + 内核固定实现；能力包不能自定义判定逻辑。

## 15. 装配

`AbilityAssembly.select(mode, *, agent_store=None)`（`A/assembly.py:117`）：

| 模式 | 含义 |
| --- | --- |
| `legacy` | 旧注册表 + 旧技能读取器（过渡期兼容） |
| `builtin` | 新声明、新工具实例、新读取服务（三者同源） |

装配点：`unit.py:749` 选择模式；`unit.py:1051` 用 `manifest_resolver()` 解析成员；`unit.py:1059` 用 `assembly.instantiate(...)` 造实例；`A/execution/ability_build.py:58` 是具体的实例化实现（按权限字段挂 FS checker / HITL 包装）。

`builtin` 模式**不混用**旧注册表：解析失败直接报错，不回退。选择在构造时固定一次，不会中途改接。

## 16. 不变式

| 编号 | 不变式 | 证据 |
| --- | --- | --- |
| INV-01 | 定义面只有一个解析入口（v1），没有第二分支 | `A/loading/ability_schema.py:180` |
| INV-02 | 工具参数只有一个作者：实现的 `bind_tool()` | `A/loading/ability_catalog.py:286` |
| INV-03 | 技能名字与说明只有一个作者：正文 frontmatter | `A/loading/skill_frontmatter.py:54` |
| INV-04 | 容器名 = 目录名 = `identity.namespace/name` | `A/loading/ability_scan.py:722` |
| INV-05 | 扫描只读：不写盘、不 import 包内代码 | `A/loading/member_index.py:40`、`A/loading/ability_scan.py:806` |
| INV-06 | 写入只发生在 `build`（CLI 或 `ensure_members`） | `A/build.py:93`、`A/build.py:312` |
| INV-07 | 生成区幂等，且不覆盖手写区 | `build.py` 的 `_authored_part` / `_compose` |
| INV-08 | 内容版本按摘要寻址，提交前复核每个字节 | `A/access/store.py:290` |
| INV-09 | 写侧唯一：版本 → 实例 → 授予，每次写要 `expected_revision` | `A/access/store.py:419` |
| INV-10 | 视图是投影，可从 sqlite 重建 | `A/access/store.py:711`、`A/scheme/host.py:300` |
| INV-11 | 扫描层不依赖构建层（`import ability_scan` 不拉 `build`） | 测试 `test_scan_layer_does_not_import_build` |
| INV-12 | 零缓存：每次调用重新扫盘 | `A/loading/ability_scan.py:806` |

## 17. 已知偏差

| 编号 | 偏差 | 影响 | 现状 |
| --- | --- | --- | --- |
| D-1 | `script` 槽只声明、无执行器：`provider: script` 的成员能被捕获与授权，但没有通用脚本运行后端 | 这类成员执行时报不可用 | 契约已留位（`provider_requirements`），执行器未实现 |
| D-2 | `end_task` 的工具绑定依赖构造模式，投影只零参构造 | `list_abilities` 显示它没有工具 | 运行时装配传 `mode="toolcall"`，执行正常 |
| D-3 | `expose` 不再决定工具面，只影响授权面 | 读者若按旧文档理解会误判 | 语义已在 §7.7 重写，两个开关刻意分开 |
| D-4 | `provider` 型成员不参与本模块的运行时投影（投影只扫内置树与 `entry` 脚本） | 内容包里的 `read_file` 没有 `AbilityManifest` | 由文件切片的后端路径（`FileBackend`）执行 |
| D-5 | Windows 无法创建符号链接时，两条"路径替换/链接"测试无法运行 | 本地验证覆盖缺口 | 代码路径在 POSIX 上覆盖；Windows 需开发者模式 |
| D-6 | 目录面扫描在用户池有大量合成包时变慢（每包一次 AST + 正文读取） | 内置树 ~6ms；12 个合成包约 +65ms | 已知，未做缓存（INV-12 优先） |
| D-7 | `capture_ability_version` 会给已知后端（`fs_backend`）盖章 schema，未知后端成员保持空 schema | 未知后端成员的内容包无法直接执行 | 与 D-1 同源 |
| D-8 | `AbilitySourceMode` 仍保留 `legacy` 分支 | 两套来源并存 | 过渡期显式选择，不默认混用 |
| D-9 | 权限判定的"允许根"由授予提供，包自身不能扩大 | 包作者可能期望"包里写死根" | 安全边界选择，非缺陷 |
| D-10 | `SkillCatalog.digest` 不覆盖正文 | 只改正文不会触发重新注入 | 刻意设计（避免正文编辑抖动上下文） |

## 18. 测试形态

`ghrah-core/tests/ability/`：

| 文件 | 覆盖 |
| --- | --- |
| `test_ability_members_build.py` | v1 解析、`scan_members`、生成区幂等、`bootstrap`/`migrate`、目录发现与合成、SKILL.md 对齐、分层守卫、catalog 合并 lock 问题 |
| `loading_legacy/loading/test_ability_{scan,containers,catalog}.py` | 扫描/聚合/投影的历史口径（fixture 已迁 v1） |
| `test_skill_context.py` / `test_skill_lifecycle.py`（均在 `loading_legacy/loading/`） | 技能清单、正文读取、初始化与漂移 |
| `test_content_container.py` | 捕获、路径校验、版本提交、授予、受控读取（含损坏与越权用例） |
| `test_resource_tree.py` | 授权视图与 TS 快照一致性 |

断言口径：能断言**返回结构**就不断言日志文案；能断言**摘要**就不断言文件时间；每个测试自己造包（`path_root(tmp_path)`），不依赖仓库里的真实包。

## 19. 改动指南

| 想改什么 | 动哪里 | 不要动 |
| --- | --- | --- |
| 新增一个内置成员 | `builtin/<容器>/operations/<key>.py` + 跑 `build` 重建该容器 | 手改 `members` |
| 改工具参数 | 实现里的 `bind_tool()` | 定义面（没有 schema 字段） |
| 改技能名字/说明 | `skill/SKILL.md` 的 frontmatter | `ability.yaml` 的 `members.skill` |
| 新增定义面字段 | `ability_schema.py` + `build.py` 的生成/保留规则 + 文档 | 只改 schema（生成器会抹掉） |
| 改扫描口径 | `member_index.py`（纯扫描）或 `ability_scan.py`（发现与对齐） | `build.py` |
| 改生成/写盘 | `build.py` | `ability_scan.py` |
| 改授权行为 | `access/store.py`（权威）+ `scheme/host.py`（投影） | 视图层（不可单独写） |
| 加 Hook 或权限规则 | `execution/hooks.py` / `permissions/`（内核侧） | 能力包内（包只声明 `PERMISSIONS`） |

改完跑：`uv run pytest ghrah-core/tests/ability -q`、`uv run ruff check .`、`uv run ruff format --check .`。

## 20. 旧概念对照

| 旧 | 新 |
| --- | --- |
| 包名 `scheme_ability/` | `ghrah.ability/` |
| `sa/__init__.py` 163 条惰性导出 | `__init__.py` 27 个显式导出 |
| `scheme_ability/doc/调用说明文档.md` | 本包的调用面在本文件 §6 与 §9–§13 |
| 定义格式 `schema_version: 0.1-draft` / `0.2` | `'1'` 两段式（`members`） |
| 定义面 `operations.<key>.input_schema` | 实现 `bind_tool()`（定义面无 schema） |
| 定义面 `skills.<key>.title/description` | `SKILL.md` frontmatter |
| `skills/.lock.yaml`（技能目录内） | `<容器>/ability.lock.yaml`（容器根） |
| `load_tool_list()` / `tool_list.py` | `load_all_builtin_manifests()` + `manifest.tool` |
| `expose` 控制工具面 | `bind_tool()` 控制工具面；`expose` 控制授权面 |
| 三个面（定义/装载/执行） | 三个面（目录/实现/内容授权）+ 执行支撑 |