# 技能系统

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-DESIGN-004 |
| 文档状态 | 现行（authoritative），但 §12 记录了**两套并存实现**的事实 |
| 校准基线 | `ghrah-monorepo` @ `f8ff157`（2026-10-28），`ghrah-core` 0.2.0a0 |
| 读者 | 要写技能包的人、改会话注入的人、排查"技能没出现/技能名调用不到/lock 报警"的开发者 |
| 取代 | `skill-system-porting-guide.md`（归档至 `docs/archive/`） |
| 相关文档 | `ghrah-01-ability-design-and-implementation.md` §4.8（`skills` 成员字段）、`ghrah-03-drive-loop.md` §5（注入发生在 `_action` 内部）、`ghrah-05-build-startup-and-troubleshooting.md` §6.3（lock 错误原文） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。首次把「旧 `ghrah.skills` 包（schema=1）」与「活跃 `ghrah.scheme.loading` 实现（schema=2）」的并存关系写成明面事实，并给出迁移建议 |

## 1. 范围

本文描述技能（skill）从**声明**到**进入模型上下文**的完整路径：技能在能力包里的位置、扫描根的优先级、正文定位规则、`SKILL.md` 的两种格式、`ability.lock.yaml` 的两代 schema、会话开始时的目录注入、`load_skill` 工具的匹配与返回。

技能不是独立子系统，它是**能力包（container）的一种成员**（`ghrah-01-ability-design-and-implementation.md` §4.8）。因此"技能怎么被发现"与"能力怎么被发现"共用同一套扫描器与根池，差别只在成员类型与正文处理。

路径缩写：`scheme/...` = `ghrah-monorepo/ghrah-core/src/ghrah/scheme/...`；`skills/...` = `ghrah-monorepo/ghrah-core/src/ghrah/skills/...`；`subject/...` = `ghrah-monorepo/ghrah-subject/src/ghrah/subject/...`。

## 2. 两套实现并存（先读这一节）

仓库里有**两份**技能实现，接口像、语义不同、**lock 格式不兼容**：

| 维度 | A：旧包 `ghrah.skills` | B：活跃实现 `ghrah.scheme.loading` |
| --- | --- | --- |
| 文件 | `skills/{roots,scan,lock,models,provider,initialize,query,catalog,document,errors}.py`（11 个） | `scheme/loading/{ability_roots,ability_schema,ability_scan,skill_fingerprint,skill_lock,skill_models,skill_context,skill_provider}.py` |
| 识别单位 | 目录 + 容器根 `SKILL.md`（扁平 `<name>/SKILL.md`） | 目录 + `ability.yaml` 的 `skills:` 声明（正文默认 `skills/SKILL.md`） |
| `SKILL.md` | **有** YAML frontmatter（`name`、`description` 必填） | **无** frontmatter，整篇逐字读取 |
| 身份 | 目录名即 kebab-case 全名 | `identity.namespace/name/version`，成员键为技能名 |
| lock schema | `1`，指纹只覆盖 `SKILL.md` 单文件字节 | `2`，指纹覆盖整棵容器目录（tree + snapshot + entry 三层） |
| 扫描根 | 自带常量 `~/.ghrah/skill-ability`（`skills/roots.py:42-44`） | 复用 `scheme/loading/ability_roots.py` 的池（内置 rank 0 + 用户 rank 100） |
| 谁在用 | **仅** `subject/units/skill.py:21`、`:50-51` 的 `SkillUnit.init` | `core/unit.py:654-660`、`core/context/manager.py:46`、`:1114-1161`（生产注入链）、`scheme/ability/core/operations/load_skill.py` |
| 生产消费者（core 侧） | 0（`docs/2026-10-26-scheme-module-inventory.md:540` 已记录） | 全部 |

结论：**注入与 `load_skill` 走 B；建目录与补 lock 走 A**。这就是 §12 D-SKL-1 的根因——A 写 schema=1，B 读 schema=2，于是漂移永远"无法裁决"。改这一块之前先确认要收敛到哪一套（建议 B，见 §12.2）。

`skills/roots.py:20` 的注释声称"只转调 `ability_roots`，不再持有自己的路径常量"，但 `skills/roots.py:42-44` 仍自带常量、目录内对 `ability_roots|from ghrah.scheme` 的 grep 为 0 命中——**该重构未落地**，路径定义重复两处（D-SKL-3）。

## 3. 技能在能力包中的位置

### 3.1 声明

`ability.yaml` 的 `skills.<key>`（`scheme/loading/ability_schema.py:257-275`），key 即技能名（`catalog_name`）。字段：`title`、`description`（必填）、`body{path, media_type}`（必填，默认 `skills/SKILL.md`，`:231-240`）、`tags`、`references`、`annotations`。

非法的 `skills` 只有一种：**一个容器超过一个技能成员**。错误原文（`:136-142`）：

```
一个能力包只支持一个 skill 成员，这里声明了 N 个（keys）。技能是容器的插件包，容器与技能一一对应；要装第二个技能请独立成包
```

`skills: {}` 完全合法：容器照常被发现，只是不贡献技能（`scheme/loading/skill_context.py:321-322` 明说"纯 operations 包（如 fs）不会出现在技能面里"）。代价是**误写空表也没有任何告警**（D-SKL-5）。

### 3.2 目录布局

```
<扫描根>/<容器名>/
  ability.yaml            # 声明 skills.<key>.body.path，默认 skills/SKILL.md
  ability.lock.yaml       # 由框架生成，勿手改
  skills/
    SKILL.md              # 正文；B 下没有 frontmatter
  operations/             # 可选：与技能共存的工具
```

三个文件名常量：`DEFINITION_FILE_NAME = "ability.yaml"`、`SKILL_BODY_DIR = "skills"`、`SKILL_FILE_NAME = "SKILL.md"`、`LOCK_FILE_NAME = "ability.lock.yaml"`（`scheme/loading/ability_schema.py:57-66`）。

仓库现状：5 份能力包**全部 `skills: {}`**，全仓 `**/SKILL.md` 匹配 0 个——即当前没有任何内置技能包，技能只能来自用户根。

## 4. 扫描根与优先级

### 4.1 B（活跃）的根

| 根 | 位置 | rank | source | required | 可写 |
| --- | --- | --- | --- | --- | --- |
| 内置 | `importlib.resources.files("ghrah.scheme.ability")` | `0` | `builtin` | 是 | 否（包资源，无 `path`） |
| 用户 | `~/.ghrah/skill-ability` | `100` | `skill-ability` | 否 | 是 |

（`scheme/loading/ability_roots.py:49-66`、`:117-128`、`:131-159`；`writable` 仅当 locator 是 `Path`，`:96-102`。）

池按 `(rank, label)` 升序扫描（`scheme/loading/ability_scan.py:554`），同名容器 rank 小者胜、败者记 issue（`:569-578`）。**内置技能包优先于用户同名技能**，用户无法覆盖内置（理由同 `01` §5.3：防止外部文件改写内置语义）。

`scheme/loading/ability_roots.py` 内没有 `os.environ` 读取——**没有环境变量能改这个根**。用户根可被上层注入覆盖：`subject/config.py:171` 的 `SkillConfig.root`（默认 `os.path.expanduser("~/.ghrah/skill-ability")`，env `GHRAH_SUBJECT_SKILL_ROOT` 见 `:633-635`）→ `subject/core_cluster/registry.py:440` → `CoreUnitConfig.skill_root` → `core/unit.py:660` 的 `SkillAbilityProvider.from_root(...)` → `path_root(...)`。

**实测（`f8ff157` 的开发机）**：用户根下装了 12 个 skill-only 容器——`axiomatic-thinking`、`boundary-innovation`、`contrarian-decision`、`founder-cognitive-boundary`、`implicit-assumption`、`iterate-not-perfect`、`logic-triple-check`、`multi-mental-models`、`organizational-refresh`、`pr`、`reductionism-deconstruction`、`research`。它们各有 1 个技能成员、0 个 operation，`scan_ability_definitions()` 会以 `kinds=["skills"]` 收进容器清单，因此**仓库里的 5 个内置操作容器不是全部容器**；报"容器总数"时必须说明是否含用户根（见 `01` §7.5 的口径说明）。

### 4.2 A（旧）的根

`skills/roots.py:42-44`：`Path.home() / ".ghrah" / "skill-ability"`，`rank=100`（`:31`）、`source="skill-ability"`（`:38`）；`ensure_roots` 直接 `mkdir(parents=True, exist_ok=True)`（`:75`）。没有内置根概念——A 永远看不到 `scheme/ability/` 里的包。

## 5. 扫描与正文定位

### 5.1 B 的扫描

继承 `01` §6 的全部规则（深度 1、忽略 `_`/`.` 开头、必须 `ability.yaml`、容器名 == 目录名、收集式 issue）。技能特有的三步：

1. `skills` 成员按成员键升序装配（`scheme/loading/ability_scan.py:261-262`）；
2. 正文定位 `_skill_body_locator`：按 `body.path` 逐段 `joinpath`，**拒绝 `..`**（`:427-447`）；
3. 正文不可用（越界或不存在）→ 记 issue 且该技能**不进清单**（`:458-459`、`:582-587`）：
   - `skills.{key}: 正文路径非法（不能越出容器目录）：…`（`:471`）
   - `skills.{key}: 声明的正文不存在：…`（`:473`）

技能目录**不由目录名识别**，只由 `ability.yaml` 声明（`:517-520`）。

### 5.2 A 的扫描

只认 `<root>/<name>/SKILL.md`；扁平 `<name>.md` 与缺 `SKILL.md` 的子目录**静默跳过**（`skills/scan.py:157-163`）。解析错误收进 `ScanIssue`（`:171-173`），不中断。

`SKILL_NAME_RE = ^[a-z0-9]+(?:-[a-z0-9]+)*$`（`:42`）——A 的全名是 **kebab-case**，与 B 的 `namespace.name` 点分全名不是同一命名空间。

## 6. `SKILL.md` 的两种格式

### 6.1 B（推荐，当前活跃）

**没有 frontmatter**。整篇文件逐字作为正文（`scheme/loading/skill_models.py:186-188` docstring 明确：身份与摘要已迁到 `ability.yaml`）。渲染时不做插值、不做转义（转义只发生在目录清单里，`scheme/loading/skill_context.py:329-331`）。

### 6.2 A（旧）

必须有 frontmatter，分隔符逐字 `---`（`skills/scan.py:52`）。错误消息原文：

| 情形 | 消息 | 行号 |
| --- | --- | --- |
| 首行不是 `---` | `缺少 frontmatter 起始分隔符 ---` | `:71` |
| 没有结束 `---` | `缺少 frontmatter 结束分隔符 ---` | `:77` |
| YAML 坏 | `frontmatter YAML 解析失败: {exc}` | `:81` |
| 顶层不是映射 | `frontmatter 顶层不是 YAML 映射` | `:83` |
| 缺字段 | `缺少必填字段 {field_name}` | `:117` |
| `name` 非 kebab-case | `name 格式非法（应为 kebab-case）` | `:121` |
| 目录名非 kebab-case | `包目录名格式非法（full_name 应为 kebab-case）` | `:125` |

必填字段：`name`、`description`（`skills/scan.py:53`）；`full_name = skill_dir.name`（`:123`）。正文 = 结束分隔符之后所有行 `"\n".join(...).strip()`（`:84`）。

## 7. lock 与漂移

### 7.1 两代 schema

| 项 | schema=1（A） | schema=2（B） |
| --- | --- | --- |
| 结构 | `{schema, full_name, version, provenance{source, fingerprint}, permission{approve, files, net}, protected}` | `{schema, full_name, provenance{source, algorithm, content_digest, snapshot_digest, recorded_at}, manifest{text, degraded, issues}, permission, protected}` |
| 常量 | `LOCK_SCHEMA_VERSION = 1`（`skills/lock.py:43`） | `LOCK_SCHEMA_VERSION = 2`（`scheme/loading/skill_lock.py:59`） |
| 指纹对象 | 单文件 `SKILL.md` 的 sha256（`skills/lock.py:54-57`） | 整棵容器目录 |
| 算法标识 | 无 | `MANIFEST_MAGIC = "ghrah-skill-tree-v1"`（`scheme/loading/skill_fingerprint.py:79`）、`sha256-tree-v1`（`:85`，不含 mtime）、`sha256-snapshot-v1`（`:86`，含 mtime）、`sha256-entry-v1`（`:87`） |
| 覆盖范围 | `SKILL.md` | 全目录；排除 `.git node_modules __pycache__ .venv dist ability.lock.yaml .DS_Store Thumbs.db *.pyc *.pyo *.swp *.swo *~`（`:92-106`） |
| 上限 | 无 | `DEFAULT_MAX_ENTRIES = 1024`（`:109`）、`DEFAULT_MAX_TOTAL_BYTES = 64 MiB`（`:112`），超限降级 `entry_limit`/`byte_limit`/`unreadable`/`unsafe_path`（`:109-120`） |
| 头注释 | `# ability.lock.yaml —— 框架生成，勿手改`（`skills/lock.py:47`） | 同语义，见 `render_lock`（`scheme/loading/skill_lock.py:141`） |

schema=2 的三层指纹含义：`content_digest` 判内容漂移（忽略 mtime，跨机器可用）；`snapshot_digest` 判快照漂移（含 mtime，同机增量用）；`entry_digest` 是单技能入口摘要，只覆盖 `ability.yaml` + `skills/SKILL.md`（`scheme/loading/skill_fingerprint.py:346-395`）。lock 自身必须排除，否则自我指涉（`:97-104`）。

### 7.2 三个动作的语义差异（schema=2）

| 动作 | 语义 | 行号 |
| --- | --- | --- |
| `ensure_locks(scan)` | **只给缺 lock 的成员建**，已存在的一律不覆盖（`reason="已存在，未覆盖"`），写失败记 `登记失败: ...`。docstring 自称"唯一登记写入入口" | `scheme/loading/ability_scan.py:795-863` |
| `verify_skill(skill)` | 重算整树 + `diff_manifests`；缺 lock 时 `lock_issue="尚无登记文件"`。**昂贵、不自动调用** | `:889-928`、`scheme/loading/skill_provider.py:204-220` |
| `relock_skill(skill)` | **唯一允许覆盖**的路径：写 `.tmp` 后 `os.replace`（原子） | `:866-886`、`scheme/loading/skill_lock.py:280-296` |

schema=1（A）只有"初始化"一种语义：`initialize_skills` 只补缺失、坏 lock 保留原文件并记 issue（`skills/initialize.py:62-86`），**没有 verify/relock**。

### 7.3 漂移裁决

`schema=2` 的 `BundleVerification.content_drifted` / `snapshot_drifted` 是 `bool | None`：`None` 表示**无法裁决**（降级、缺文件、schema 不符）（`scheme/loading/skill_models.py:115-160`）。这与"没漂移"不是一回事，据 `None` 做"安全"判断是错的。

### 7.4 谁在写 lock

- 生产链：`SkillUnit.init`（`subject/units/skill.py:21`、`:50-51`）走 **A 的** `provider.initialize()` → 只建目录 + 按 A 规则补 schema=1 lock。`subject/core_cluster/registry.py:437-439` 注释明确"建目录/lock 由 SkillUnit 装配期完成，Core 不重复初始化"。
- `ghrah-core/src` 内 grep 不到 `provider.initialize()/verify()/relock()` 的调用点，只有 `scheme/__init__.py:115-117` 的导出。
- 因此：**B 的 schema=2 校验在生产上从来不会通过**，因为盘上写的是 schema=1（D-SKL-1）。

## 8. 会话注入（技能目录如何进入模型上下文）

### 8.1 接线

| 步 | 位置 | 说明 |
| --- | --- | --- |
| 1 | `core/unit.py:654-660` | **仅当** `config.skill_root` 为真才 `SkillAbilityProvider.from_root(...)`；注释明确"未注入即不接线…零隐式" |
| 2 | `core/unit.py:674` | 传入 `SupervisorActor(skill_provider=...)` |
| 3 | `communication/supervisor.py:102`、`:143-144` | 持有 |
| 4 | `agents/base.py:1668`、`:1701`、`agents/builder.py:278`、`context/rebase.py:112` | 逐层下传 |
| 5 | `scheme/execution/context.py:92-93` | 放进服务表 `ServiceKey.SKILL_PROVIDER`（`scheme/execution/execution_services.py:83`） |

`load_skill_catalog()`（`scheme/loading/skill_context.py:311`）本身在 `ghrah-core/src` **没有生产调用点**（只有 `scheme/__init__.py:99` 导出与文档示例）。真实注入直接吃 `provider.scan()` 的结果——别被函数名误导。

### 8.2 注入时序

`core/context/manager.py:46` 导入 `digest_skill_catalog`、`render_skill_catalog`；`get_llm_messages` 在**窗口管理之后**追加一条目录消息（`:1114-1119`）。`_skill_catalog_message`（`:1121-1161`）的分支：

| 条件 | 行为 | 行号 |
| --- | --- | --- |
| `provider` 为 `None` | 不注入 | `:1121-…` |
| 有 provider 但缺 `message_factory` | warning `skill_provider is wired but message_factory is missing — skill catalog injection skipped` | `:1121-1161` |
| 扫描抛错 | warning `skill catalog scan failed — injection skipped` | 同上 |
| **从未发布过且清单为空** | 不注入 | `:1142-1144` |
| 正常 | `digest_skill_catalog(scan.skills)` 判 first/changed；有扫描 issue 时告警 `skill catalog: %d skill(s) failed to scan and are omitted`；变化时日志 `skill catalog changed — injecting the replacement catalog`；最后 `create_message(role="user", text=render_skill_catalog(..., changed=changed), source="skill:catalog")` | `:1157-1161` |

要点：注入是一条**`role="user"` 的独立消息**，`source="skill:catalog"`，不是 system prompt 拼接；它出现在窗口管理之后，所以**不参与压缩历史**，每轮按需重算。

### 8.3 渲染模板

`render_skill_catalog`（`scheme/loading/skill_context.py:135-160`，与 A 的 `skills/catalog.py:75-98` 逐字相同）：

```
<system-reminder>
A skill is a reusable set of task-specific instructions. The following skills are available in this session:

<available_skills>
- `<name>`: <description>
</available_skills>

If a task clearly matches a skill's description, apply the approach it describes. This catalog contains summaries only — skill bodies are not included in this message.
</system-reminder>
```

变化版首行替换为 `The available skill catalog changed. This complete catalog replaces every earlier available-skills list in this session:`；空清单另有 `No skills are currently available. Do not use names from earlier skill catalogs.`。

细节：描述先做空白折叠，超过 `CATALOG_DESCRIPTION_MAX_CHARS = 500`（`:60`）截为 497 + `...`（`:100-113`）；文本转义 `&`/`<`/`>`（`:329-331`）；清单里用的是 `catalog_name`（= 成员键，`scheme/loading/ability_scan.py:175-182`），全名只用于去重与定位。

## 9. `load_skill` 工具

### 9.1 入参

`LoadSkillInput`（`scheme/ability/core/operations/load_skill.py:72-93`，`extra="forbid"`）：`full_name: str`、`server: str = ""`、`tool: str = ""`。工具名 `load_skill`（`:109-110`），模型可见 schema 由 `bind_tool()` 给出（`:112-126`）。

### 9.2 匹配是三级，不是猜路径

`context.skill_provider`（`:154`）→ `provider.read_skill(full_name)`（`scheme/loading/skill_provider.py:131-147`）→ `get_skill` 依次按 `full_name` 精确 → `catalog_name`（成员键）→ `container` 匹配（`:101-129`）；多个命中报

```
技能名 ... 命中了 N 个技能，请改用全名
```

然后 `read_skill_document`（`scheme/loading/skill_context.py:334-382`）：有 `skill_dir` 且有 `body_path` 时 `path = skill_dir.joinpath(*body_path.split("/"))`，否则退回 `body`；两者皆无报 `技能 ... 没有可读的正文路径（容器来自包资源，或在文件系统上但未声明 body.path）`（`:363-371`）。`SkillCatalog.read(name)` 未知名报 `未知的技能成员: ...（不按名字猜路径）`（`:307`）。

### 9.3 返回

`ActionOutcome.SUCCESS` + `{full_name, body_path, skill_dir, path, content, note: "skill-ability document — instructions to apply"}`（`:189-196`、`:264`）。`content` 是**全文**。

### 9.4 错误对照

| 情形 | 消息 | 位置 |
| --- | --- | --- |
| provider 未接线 | `SkillAbilityProvider not wired — skill loading is unavailable in this deployment (assembly-time injection, not implicit)` | `load_skill.py:154-…` |
| 未找到 | `{"error": "Skill not found: '...'", "note": "Use list_abilities to see the available skill full names."}` | 同上 |
| 读失败 | `Failed to read skill document: ...` | 同上 |
| `tool` 无 `server` | `'tool' requires 'server'` | `:200-262` |
| 技能不在文件系统上却点名 MCP | `Skill '...' is not on the filesystem, so MCP server ... cannot be resolved` | 同上 |
| 无 MCP 规格却点名 server | `declares no MCP servers` | 同上 |

### 9.5 没有长度限制

`load_skill.py` 全 264 行没有任何正文截断；`scheme/loading` 内的 `truncat|max_chars` 只命中目录描述的 500 字符上限（`skill_context.py:60`）与 MCP 工具描述截断（`skill_mcp.py:39`、`:239`、`:281`）。**超大 `SKILL.md` 会整篇灌进上下文**（D-SKL-4）。

## 10. 与能力容器的关系（速查）

| 问题 | 答案 | 依据 |
| --- | --- | --- |
| 技能能同时有 operations 吗 | 能，同一 `ability.yaml` 三张表并列 | `scheme/loading/ability_schema.py:288-291` |
| 一个包能有几个技能 | 至多 1 个 | `:136-142` |
| 纯技能包叫什么 | `is_skill_only` 为真；单技能包 `full_name` 收敛为容器名，多成员包为 `container.key` | `scheme/loading/ability_scan.py:626`、`:311-324` |
| `skills: {}` 会报错吗 | 不会，且无告警 | `:290` vs `:136-142` |
| 技能会出现在 `list_abilities` 里吗 | 会（作为容器成员），但不会出现在工具面 | `ghrah-01-ability-design-and-implementation.md` §8 |
| MCP 声明放哪 | `skills.<key>` 同目录的 `mcp/*.yaml`，由 `skill_mcp.py` 解析 | `scheme/loading/skill_mcp.py:58-69`、`:185-283` |

## 11. 不变式

| 编号 | 不变式 | 校验点 |
| --- | --- | --- |
| S-01 | 一个容器至多一个技能成员 | `ability_schema.py:136-142` |
| S-02 | 正文路径不能越出容器目录（逐段 join，拒绝 `..`） | `ability_scan.py:427-447` |
| S-03 | 正文不可用的技能不进清单 | `ability_scan.py:458-459`、`:582-587` |
| S-04 | 清单里显示的是成员键（`catalog_name`），不是点分全名 | `ability_scan.py:175-182` |
| S-05 | 内置根优先于用户根，用户不能覆盖内置技能 | `ability_scan.py:554`、`:569-578` |
| S-06 | `ensure_locks` 永不覆盖既有 lock，`relock` 是唯一覆盖路径 | `ability_scan.py:795-863`、`:866-886` |
| S-07 | 漂移结果三态：`True` / `False` / `None`（无法裁决）；`None` 不等于安全 | `skill_models.py:115-160` |
| S-08 | 注入消息恒为 `role="user"` + `source="skill:catalog"`，且不进入压缩历史 | `manager.py:1114-1161` |
| S-09 | 目录描述上限 500 字符，超出截断而非报错 | `skill_context.py:60`、`:100-113` |
| S-10 | `load_skill` 按"全名 → 成员键 → 容器"三级匹配，匹配不唯一即报错 | `skill_provider.py:101-129` |

## 12. 已知偏差与迁移建议

### 12.1 偏差

| 编号 | 偏差 | 证据 | 影响 |
| --- | --- | --- | --- |
| D-SKL-1 | **lock schema 双写冲突**：Subject 用 A 初始化（写 schema=1），Core 用 B 读（要 schema=2） | `subject/units/skill.py:21`、`:50-51` vs `skill_lock.py:189-193` | `fingerprint=None` + `lock_issue`，漂移永久"无法裁决"；A 只认容器根 `SKILL.md`，对 B 布局（`skills/SKILL.md`）静默跳过，所以 A 通常只建目录、几乎不写 lock |
| D-SKL-2 | 两套实现并存，A 在 core 侧零消费者 | `docs/2026-10-26-scheme-module-inventory.md:540` | 迁移只完成一半，认知负担翻倍 |
| D-SKL-3 | 路径定义重复两处（`skills/roots.py:42-44` 与 `scheme/loading/ability_roots.py:131-140`） | 注释声称已收敛但未落地 | 改默认根要改两处 |
| D-SKL-4 | 正文无大小上限 | `load_skill.py` 全文 | 超大 `SKILL.md` 直灌上下文，且注入面只显示 500 字符摘要，用户无感 |
| D-SKL-5 | `skills: {}` 与"技能写进 `assets`"都静默通过 | `ability_scan.py:240-251`、`ability_schema.py:290` | 声明错误没有任何信号 |
| D-SKL-6 | `load_skill_catalog()` 生产零调用，真实注入走 `provider.scan()` | `scheme/__init__.py:99` | 照函数名找注入点会找错地方 |

### 12.2 收敛建议（按代价排序）

1. **统一 lock schema**：让 `SkillUnit.init` 改用 B 的 `ensure_locks`（或直接删掉 A 的初始化，改由 `core/unit.py` 在装配期调 `ensure_locks`），否则 schema=2 的三层指纹全部白写。
2. **给正文加硬上限**：`load_skill` 返回前按字符/字节截断并附 `truncated` 字段，或强制 `body.path` 指向的文件不超过阈值，超限在扫描期就报 issue。
3. **删 A**：把 `subject/units/skill.py` 改为 `scheme/loading/skill_provider.SkillAbilityProvider`，然后删除 `ghrah.skills` 整包（11 文件），顺带解决 D-SKL-3。
4. **空表告警**：`skills: {}` 且容器同时也没有 `operations` 时记一条 issue（"空容器"）。

## 13. 怎么写一个技能包

1. 在用户根建目录：`~/.ghrah/skill-ability/<容器名>/`（容器名只用 `[a-z0-9_]`，不能有连字符——`identity.name` 规则，`ability_schema.py:156`）。
2. 写 `ability.yaml`：

```yaml
format: ghrah.ability-definition
schema_version: 0.1-draft
identity:
  namespace: local            # 必填
  name: my-skill-pack         # 可选；给了就必须等于目录名
  version: 0.1.0
title: My Skill Pack
description: 一句话说明这个包做什么
skills:
  my_skill:                   # 成员键 = 技能名（catalog_name）
    title: My Skill
    description: 什么时候该用这个技能（一句话，会进目录清单，500 字符内）
    body:
      path: skills/SKILL.md
      media_type: text/markdown
```

3. 写 `skills/SKILL.md`：**纯 Markdown，不要 frontmatter**（B 格式，§6.1）。正文是给模型的执行指令，写步骤、判据、反例，不要写元数据。
4. 起服务一次，让装配期生成 `ability.lock.yaml`（不要手写、不要手改）。
5. 验证（按当前实现，验证要靠代码而不是 CLI）：

```python
from ghrah.scheme.loading.skill_provider import SkillAbilityProvider
p = SkillAbilityProvider.default_provider()
scan = p.scan()
print([s.catalog_name for s in scan.skills])
print(p.get_skill("my_skill"))     # 三级匹配
```

6. 模型侧验证：目录清单会以 `role="user"`、`source="skill:catalog"` 的消息出现；命中后模型应调 `load_skill(full_name=...)`。

**注意**：由于 D-SKL-1，第 4 步在当前代码里可能产出 schema=1 的 lock，而 `verify` 会报 `lock schema='1' 与当前 2 不符`。这是已知缺陷，不是你的包写错了。