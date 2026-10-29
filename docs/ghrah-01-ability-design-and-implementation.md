# 能力系统设计与实现

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-DESIGN-001 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `f8ff157`（2026-10-28），`ghrah-core` 0.2.0a0 |
| 读者 | 维护能力系统的开发者、要新增能力包的开发者、评审运行时契约的架构人员 |
| 取代 | `02-能力系统.md`、`能力系统建设设计方案.md`、`2026-10-13-ability-system-object-fields-design.md`、`2026-10-13-ability-terminology-and-structure-rulings.md`、`ability加载架构.md`、`ability架构适配设计文档.md`、`2026-10-26-ability-scheme-draft.md`、`2026-10-12-ability-command-plane-p1.md`、`2026-10-13-ability-gaps-blocking-triage.md`、`能力协议层调用依赖分析.md`（全部归档至 `docs/archive/`） |
| 相关文档 | `ghrah-02-ability-package-guide.md`（怎么加一个成员）、`ghrah-04-skill-system.md`（skill 成员与目录注入）、`ghrah-06-code-map.md`（文件级索引）、`07-设计决策与演进留痕.md`（为什么变成现在这样） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。以代码为准重建：定义面（`ability.yaml`）、目录池、扫盘、运行时投影、工具面、执行面、Hook、权限与 HITL、两条装配路径、不变式与已知偏差；旧设计中的注册表/挂载表/指令面概念在 §14 逐条判定为不存在 |

## 1. 范围

本文描述 Ghrah 能力系统（Ability System）**当前实现**的结构与契约：能力包如何被定义、如何被发现、如何被投影成模型工具、一次工具调用如何被执行、权限与人工审批在哪一层生效、以及装配期由谁把这些对象接起来。

**权威顺序**：代码 > 本文 > `docs/archive/` 里的历史设计稿。本文与代码冲突时以代码为准，并把冲突当作文档缺陷（见 §13.2）。文中所有 `path:line` 引用都以校准基线 commit 的行号为准，行号会随重构漂移，符号名不会——找不到行号时按符号名检索。

**不在本文范围**：Agent 驱动循环本身（见 `ghrah-03-drive-loop.md`）、技能目录与 `SKILL.md` 的完整语义（见 `ghrah-04-skill-system.md`）、构建/启动/日志（见 `ghrah-05-build-startup-and-troubleshooting.md`）、协议与前端下发（见 `ghrah-06-code-map.md` §5）。

### 1.1 路径约定

为压缩引用长度，本文使用下列缩写（展开后为相对 `D:\codeWorkSpace\ghrah-project\` 的真实路径）：

| 缩写 | 展开 |
| --- | --- |
| `scheme/...` | `ghrah-monorepo/ghrah-core/src/ghrah/scheme/...` |
| `core/...` | `ghrah-monorepo/ghrah-core/src/ghrah/core/...` |
| `agents/...` | `ghrah-monorepo/ghrah-core/src/ghrah/agents/...` |
| `unit.py` | `ghrah-monorepo/ghrah-core/src/ghrah/core/unit.py` |
| `subject/...` | `ghrah-monorepo/ghrah-subject/src/ghrah/subject/...` |

## 2. 术语

| 术语 | 定义 | 代码位置 |
| --- | --- | --- |
| 能力包 / 容器（container） | 一个含 `ability.yaml` 的目录。容器名 = `identity.name or identity.namespace`，且必须等于目录名 | `scheme/loading/ability_schema.py:294-302`、`scheme/loading/ability_scan.py:505-516` |
| 成员（member） | 容器内三类可寻址项之一：`operations` / `skills` / `assets`，键即成员名 | `scheme/loading/ability_scan.py:255-361` |
| 定义面（definition） | `ability.yaml` 表达的静态事实：身份、提供者槽、成员契约 | `scheme/loading/ability_schema.py` |
| 实现面（implementation） | `operations/<key>.py` 里的实现脚本，按路径约定被 import | `scheme/loading/ability_catalog.py:166` |
| 目录面（catalog） | 全池扫盘结果，含用户目录，收集式不抛错 | `scheme/loading/ability_scan.py:532-604` |
| 实现面投影 | 只扫内置树、严格抛错的运行时视图 | `scheme/loading/ability_catalog.py:102-127` |
| 运行时视图（`AbilityManifest`） | 从定义面+实现面**投影**出的旧格式对象，供列表类工具与兼容层使用 | `scheme/loading/ability_catalog.py:338-396` |
| 工具面（tool list） | 交给模型的 function-calling schema 集合，工具名 = 成员键 | `scheme/loading/tool_list.py:107-161` |
| handler | 成员键同名标识，用来定位实现模块与类 | `scheme/loading/ability_catalog.py:452-466` |
| 提供者槽（provider slot） | `implementation.provider_slot`，声明本成员依赖哪个后端契约 | `scheme/loading/ability_schema.py:176-179`、`:199-200` |
| HITL | Human-In-The-Loop，人工审批 | `scheme/permissions/hitl.py`、`scheme/execution/executor.py:651-763` |

## 3. 总体结构

### 3.1 四个面

```
ability.yaml  ──parse──►  AbilityDefinition ──┐
（定义面）                                     ├─► scan_ability_definitions() ──► AbilityScanResult
                          <root>/<容器>/…  ────┘        （目录面：全池，收集 issue）
                                                                 │
                      内置树（rank 0） ──► load_ability_definitions()（实现面：只内置，严格抛错）
                                                                 │
                                        build_tool_schema() ──► ToolList（工具面：交给模型）
                                                                 │
                                        operations/<key>.py ──► Ability 实例 ──► AbilityExecutor
```

四个面职责互斥，**不允许**把一面的结论写回另一面：定义面不承载权限与 hook（见 §4.9），实现面不决定工具是否可见（见 §4.7），工具面不去猜实现位置（见 §8.2）。

### 3.2 零缓存

能力系统的所有查询 API 都**每次调用重新扫盘或重新投影**，不持有缓存：

- `load_ability_definitions()` `scheme/loading/ability_catalog.py:112`
- `load_all_builtin_manifests()` `scheme/loading/ability_catalog.py:405`
- `operation_containers()` `scheme/loading/ability_catalog.py:460`
- `has_handler()` `scheme/loading/ability_catalog.py:490`
- `scan_ability_definitions()` `scheme/loading/ability_scan.py:535`
- `load_tool_list()` `scheme/loading/tool_list.py:30`
- `skill_context.load_skill_catalog()` `scheme/loading/skill_context.py:24`

成立前提是 YAML 解析足够便宜：`load_yaml()` 显式绑定 `yaml.CSafeLoader`（`scheme/loading/ability_schema.py:91`），注释给出量化：36KB 定义从 ~75ms 降到 ~6ms（`scheme/loading/ability_schema.py:78-80`）。**新增查询 API 时不要自己加 `lru_cache`**，否则会引入"改了 `ability.yaml` 但不生效"的第二权威。

> 已知文档缺陷：`scheme/loading/ability_catalog.py:20` 的模块 docstring 仍提到 `load_all_builtin_manifests.cache_clear()`，该函数并没有 `lru_cache`，同文件 `:405` 明写"每次调用都重新投影（无缓存）"。以 `:405` 为准。

### 3.3 模块职责

| 模块 | 职责 | 是否对外 |
| --- | --- | --- |
| `scheme/loading/ability_schema.py` | `ability.yaml` 的 pydantic 模型与跨字段校验；四个文件名常量 | 是（常量被 skill 层复用） |
| `scheme/loading/ability_roots.py` | 扫描根与优先级（内置 rank 0 / 用户 rank 100） | 是 |
| `scheme/loading/ability_scan.py` | 全池扫盘、成员装配、lock 指纹 | 是（`scan_abilities`） |
| `scheme/loading/ability_catalog.py` | 内置树严格投影、handler→类 解析、`create_ability` | 是 |
| `scheme/loading/catalog.py` | 能力+handler+容器+skill+issue 的合并报告 | 是（`scan_catalog`） |
| `scheme/loading/tool_list.py` | 工具面 | 是（`load_tool_list`） |
| `scheme/loading/ability_view.py` | `AbilityManifest` 视图模型（投影目标形态） | 内部 |
| `scheme/loading/skill_context.py` / `skill_mcp.py` / `skill_lock.py` / `skill_fingerprint.py` | skill 成员、MCP 声明、lock | 见 `ghrah-04-skill-system.md` |
| `scheme/loading/manifest_io.py` / `manifest_fields.py` | 旧 manifest 形态的解析与字段（遗留兼容层，见 §7.6） | 兼容 |
| `scheme/execution/*` | `Ability` 契约、执行上下文、执行器、Hook | 是 |
| `scheme/permissions/*` | FS 权限、命令分级、HITL hook | 是 |
| `scheme/paths.py` / `kinds.py` / `errors.py` | 路径规格、能力分类、能力异常 | 是 |
| `scheme/ability/<容器>/` | 能力包本身（**只放包，不含加载逻辑**，`scheme/__init__.py:19`） | 数据 |

## 4. 定义面：`ability.yaml`

### 4.1 位置、身份与命名

- 路径：`<扫描根>/<容器名>/ability.yaml`，扫描深度固定为 1（`scheme/loading/ability_scan.py:491`）。仓库内只有 5 份：`scheme/ability/{fs,core,cluster,shell,mcp}/ability.yaml`。
- `format` 必须是字面量 `ghrah.ability-definition`（`scheme/loading/ability_schema.py:283`），`schema_version` 必须是 `0.1-draft`（`:54`、校验 `:121-125`）。
- 容器身份 `identity.namespace` 允许 `-` 与 `_`（`^[a-z][a-z0-9_-]*$`）；`identity.name` 只允许 `[a-z0-9_]`，**不允许连字符**（`:155-156`）——因为 `identity.name` 会参与 Python 包路径（`<容器>/operations/<key>.py`）。
- 容器名与目录名必须相等，否则该包记一条 issue 并跳过（`scheme/loading/ability_scan.py:505-516`）。原文理由：目录名只作定位，身份以定义面为准。

### 4.2 顶层字段

| 字段 | 类型 | 必填 | 默认 | 行号 |
| --- | --- | --- | --- | --- |
| `format` | `Literal["ghrah.ability-definition"]` | 是 | — | `:283` |
| `schema_version` | `str` | 是 | — | `:284` |
| `identity` | `AbilityIdentity` | 是 | — | `:285` |
| `title` | `str`（≥1） | 是 | — | `:286` |
| `description` | `str`（≥1） | 是 | — | `:287` |
| `provider_requirements` | `dict[str, ProviderRequirement]` | 否 | `{}` | `:288` |
| `operations` | `dict[str, OperationDefinition]` | 否 | `{}` | `:289` |
| `skills` | `dict[str, SkillDefinition]` | 否 | `{}` | `:290` |
| `assets` | `dict[str, Any]` | 否 | `{}` | `:291` |

所有子模型都设 `extra="forbid"`（`:153`、`:174`、`:185`、`:197`、`:206`、`:229`、`:266`、`:281`），拼错字段名直接报错，不做隐式吸收。

### 4.3 `identity`

| 字段 | 类型 | 必填 | 默认 | 行号 |
| --- | --- | --- | --- | --- |
| `namespace` | `str`，`^[a-z][a-z0-9_-]*$` | 是 | — | `:155` |
| `name` | `str \| None`，`^[a-z][a-z0-9_]*$` | 否 | `None` | `:156` |
| `version` | `str`（≥1） | 是 | — | `:157` |

派生：`full_name` = `namespace.name`，`name` 缺席时退化为 `namespace`（`:160-168`）；`legacy_namespace` 同值（`:314-322`）；`full_name_of(key)` = `<点分身份>.<key>`（`:324-335`）。点分全名（如 `ghrah.fs.read_file`）**只**用于视图键、去重与问题定位，**不是**工具名（§8.2）。

### 4.4 `provider_requirements`

| 字段 | 类型 | 必填 | 默认 | 行号 |
| --- | --- | --- | --- | --- |
| `contract` | `str`（≥1） | 是 | — | `:176` |
| `contract_digest` | `str \| None` | 否 | `None` | `:177` |
| `required_capabilities` | `list[str]` | 否 | `[]` | `:179` |

`contract_digest` 由发布路径从唯一规范计算填入，**作者不自签**（`:178`）。`implementation.provider_slot` 必须在 `provider_requirements` 里声明过，否则拒绝（跨字段校验 3，`:135-140`）。

### 4.5 `operations.<key>` 与成员契约

| 字段 | 类型 | 必填 | 默认 | 行号 |
| --- | --- | --- | --- | --- |
| `title` | `str`（≥1） | 是 | — | `:208` |
| `description` | `str`（≥1） | 是 | — | `:209` |
| `input_schema` | `dict[str, Any]` | 是 | — | `:210` |
| `output_schema` | `dict[str, Any]` | 是 | — | `:211` |
| `errors` | `dict[str, Any]` | 否 | `{}` | `:212` |
| `implementation` | `OperationImplementation` | 是 | — | `:213` |
| `effects` | `list[str]` | 否 | `[]` | `:214` |
| `resources` | `dict[str, Any]` | 否 | `{}` | `:215` |
| `execution` | `ExecutionSpec` | 是 | — | `:216` |
| `expose` | `bool` | 否 | `True` | `:217` |

`implementation` 两个字段都必填且 `min_length=1`：`provider_slot`（`:199`）、`operation`（`:200`）。跨字段校验 2 要求 `implementation.operation == 成员键`（`:127-132`），因此"成员键改名但实现没改"会在扫描期直接失败。

### 4.6 `execution`（`ExecutionSpec`）

| 字段 | 取值 | 必填 | 行号 |
| --- | --- | --- | --- |
| `mode` | `Literal["request"]`（首版只支持 request 语义） | 是 | `:187` |
| `timeout_ms` | `int`，`>0` | 是 | `:188` |
| `cancellation` | `Literal["cooperative","unsupported"]` | 是 | `:189` |
| `on_revoke` | `Literal["drain","request_cancel"]` | 是 | `:190` |
| `retry` | `Literal["never"]` | 是 | `:191` |

**重要事实：`timeout_ms` 目前不参与执行。** 它是定义面声明，`AbilityOperation.timeout_ms` 也把它透出（`scheme/loading/ability_scan.py:229-231`），但执行器从不读取它——`execute_ability` 里没有为 `ability.execute()` 包任何超时（`scheme/execution/executor.py:400`，全文件唯一 `wait_for` 在 `:710`，只用于 HITL 等待）。内置包取值：`fs`/`core`/`cluster` 全部 `60000`，`shell`/`mcp` 为 `300000`（`scheme/ability/shell/ability.yaml:65`、`scheme/ability/mcp/ability.yaml:73`）。改这个字段不会改变运行时行为，改执行超时目前无受支持途径。

### 4.7 `expose` 是工具面唯一开关

原文（`scheme/loading/ability_schema.py:218-222`）：`True`（缺省）表示该成员按定义面投影成一个模型工具，工具名即成员键；`False` 表示只在内部可用。**本字段是"是否进工具面"的唯一权威：实现脚本不再声明同名开关。**

仓库内两个 `expose: false` 的例子：`core/ability.yaml:42`（`conversation`）与 `cluster/ability.yaml:293-297`（`send`）。二者的差别见 §7.5。

### 4.8 `skills.<key>`

| 字段 | 类型 | 必填 | 默认 | 行号 |
| --- | --- | --- | --- | --- |
| `title` | `str`（≥1） | 是 | — | `:268` |
| `description` | `str`（≥1） | 是 | — | `:269` |
| `body` | `SkillBody{path, media_type}` | 是 | — | `:270`、`:231-240` |
| `tags` | `list[str]` | 否 | `[]` | `:271` |
| `references` | `list[str]` | 否 | `[]` | `:272` |
| `annotations` | `dict[str, Any]` | 否 | `{}` | `:273` |

约束：**一个能力包至多一个 skill 成员**（跨字段校验 4，`:136-142`），超出的报错原文为"一个能力包只支持一个 skill 成员…技能是容器的插件包，容器与技能一一对应；要装第二个技能请独立成包"。正文固定落在 `skills/SKILL.md`，因此不引入第二级目录（`:260-261`）；`title`/`description` 即"原 `SKILL.md` frontmatter 迁出的两个字段"（`:263`）。

仓库内 5 份 `ability.yaml` 全部 `skills: {}`，**没有任何打包的 skill 种子包**（全仓 `**/SKILL.md` 匹配 0 个）。技能成员只能来自用户目录，详见 `ghrah-04-skill-system.md`。

### 4.9 刻意不在定义面的两件事

模块 docstring 明写（`scheme/loading/ability_schema.py:11-12`）：`permissions`（授权结果）与 `hooks`（编排）不在定义面，前者属授权记录，后者随实现，暂存于 `operations/<key>.py` 的模块级常量。所以"这个能力要不要审批"在 `ability.yaml` 里查不到，只能查实现脚本或装配参数——这是当前设计的一处可读性代价，见 §13.2 偏差 D-3。

`assets` 只有身份与键，没有字段级契约：`dict[str, Any]`，内容原样持有、不解释语义（`:243-244`、`scheme/loading/ability_scan.py:241-251`）。

### 4.10 校验与错误

解析入口 `parse_definition(raw: str, *, source: str) -> AbilityDefinition`（`scheme/loading/ability_schema.py:94`）：`load_yaml` → 非 `dict` 报"不是映射"（`:115-116`）→ `model_validate`（`:118`）→ 四条跨字段校验（`:121-142`）。错误类型 `ManifestValidationError`（`scheme/loading/manifest_fields.py:40`，基类 `ManifestError` `:36`）。

校验时机有两套语义，不要混淆：

| 路径 | 时机 | 失败行为 |
| --- | --- | --- |
| 目录面 `scan_ability_definitions` | 每包解析一次（`scheme/loading/ability_scan.py:501`） | 记 issue 后 `continue`，不影响其他包 |
| 实现面 `load_ability_definitions` | 同上，但把**第一条** issue 直接抛出（`scheme/loading/ability_catalog.py:124-126`） | 整次查询失败 |

## 5. 目录池与优先级

### 5.1 `AbilityRoot`

`@dataclass(frozen=True)`（`scheme/loading/ability_roots.py:70-82`）：`locator: Traversable`、`rank: int`、`source: str`、`required: bool = False`。属性：`label`（真实路径给绝对路径，包资源给 `package:ghrah.scheme.ability`，`:85-93`）、`writable`（仅 `Path` 为 `True`，`:96-102`）、`path`（包资源为 `None`，`:105-114`）。

### 5.2 两个根

| 根 | 位置 | rank | source | required | 可写 |
| --- | --- | --- | --- | --- | --- |
| 内置 | `importlib.resources.files("ghrah.scheme.ability")`，即 `scheme/ability/` | `0` | `builtin` | 是 | 否 |
| 用户 | `Path.home()/.ghrah/skill-ability` | `100` | `skill-ability` | 否 | 是 |

常量定义在 `scheme/loading/ability_roots.py:49-66`；工厂 `builtin_root()` `:117-128`、`user_root(root=...)` `:143-159`、`path_root(path, *, rank=USER_ROOT_RANK, source=..., required=False)` `:162-180`。两个池函数：`builtin_pool()` 单元素（实现面用它，`:183-189`）、`default_pool()` = 内置 + 用户（目录面用它，`:192-200`）。

### 5.3 冲突裁决：内置必胜

扫描按 `(rank, root.label)` 升序（`scheme/loading/ability_scan.py:554`），同名容器先出现者胜，后来者记 issue（`:567-579`）。因此 rank 0 的内置包永远赢，用户目录**不允许**同名覆盖内置。设计原文（`scheme/loading/ability_roots.py:53-57`）：否则一份外部 `fs/ability.yaml` 就能改掉文件系统能力的语义。

### 5.4 覆盖途径

`scheme/loading/` 内没有任何 `os.environ` 读取——**没有环境变量可改扫描根**。可用的三条路：

1. `user_root(root=...)` 传参；
2. `path_root()` 自建根；
3. 上层装配期注入字符串路径（`core/unit.py:197`、subject 的 `SkillConfig.root`）。

`ghrah.skills.roots` 只是转调本模块（`scheme/loading/ability_roots.py:133-135`）。

`ensure_roots(pool)`（`:203-220`）只对 `locator` 是 `Path` 的根 `mkdir(parents=True, exist_ok=True)`，包资源根跳过；幂等，返回顺序与入参一致。

## 6. 扫描（目录面）

入口 `scan_ability_definitions(pool: Sequence[AbilityRoot] | None = None) -> AbilityScanResult`（`scheme/loading/ability_scan.py:532-604`）；`pool=None` 时用 `default_pool()`（`:553`）。薄封装 `scan_abilities()`（`:754-778`）是对外主入口，`load_ability_skills()`（`:781-792`）只取 skill 成员。

### 6.1 返回结构

| 类型 | 字段 | 语义 | 行号 |
| --- | --- | --- | --- |
| `AbilityEntry` | `container` / `definition` / `root` / `locator` | 一个成功解析的包 | `:87-100` |
| `AbilitySkill` | `container`、`key`、`full_name`、`title`、`description`、`media_type`、`body_path`、`body_locator`、`tags`、`source`、`rank`、`skill_dir`、`lock_path`、`fingerprint`、`lock_issue` | skill 成员；`catalog_name` = `key`（`:176-182`，原文理由：显示全名会让模型拿一个加载入口不认的名字去调用） | `:104-182` |
| `AbilityOperation` | `container`、`key`、`full_name`、`definition`，属性 `title`/`description`/`provider_slot`/`handler`/`timeout_ms`/`effects` | operation 成员；**不复制**定义字段，只补归属（`:193`） | `:186-236` |
| `AbilityAsset` | `container`、`key`、`full_name`、`definition` | asset 成员 | `:240-251` |
| `AbilityContainer` | `name`、`namespace`、`version`、`title`、`description`、`root`、`locator`、`definition`、`operations`/`skills`/`assets`、`issues`；属性 `full_name`、`kinds`、`operation_count`、`skill_count`、`asset_count`、`member_count`、`is_operation_only`、`is_skill_only` | 聚合视图 | `:255-361` |
| `AbilityScanIssue` | `path`、`reason`、`container` | 单条问题，`__str__` = `"{path}: {reason}"` | `:365-378` |
| `AbilityScanResult` | `roots`、`entries`、`skills`、`issues`、`lock_issues`；属性 `definitions`（`{容器名: 定义}`）、`containers` | 全池结果 | `:382-424` |

### 6.2 算法

1. 排序根：`sorted(active, key=lambda item: (item.rank, item.label))`（`:554`）。
2. 每个根只做一次 `iterdir()`（`:491`），**不递归**。
3. 忽略：非目录、名字以 `_` 或 `.` 开头（`:491-497`）、目录内没有 `ability.yaml`（注释：`operations/` 这类普通子目录不是能力包）。
4. 解析定义，失败记 issue 并 `continue`（`:502-504`）。
5. 校验容器名 == 目录名（`:505-516`）。
6. 装配成员：operation/skill/asset 按成员键升序，容器按名字升序（`:261-262`）。
7. 同名容器去重（§5.3）。
8. skill 正文定位：`_skill_body_locator` 逐段 `joinpath`，`..` 一律拒绝（`:427-447`）；正文不可用的 skill **不进清单**（`:458-459`、`:582-587`）。

skill 目录**不由目录名识别**，只由 `ability.yaml` 的 `skills:` 表声明（`:517-520`、`:582-587`）。

### 6.3 issue 分类

| 情形 | 文案 | 行号 |
| --- | --- | --- |
| 必需根不存在 | `扫描根不存在`（`container=None`） | `:560-563` |
| 定义读取/解析失败 | 异常原文（`ManifestValidationError`/`OSError`/`UnicodeDecodeError`） | `:502-504` |
| 容器名 ≠ 目录名 | `容器名 X 与目录名 Y 不一致（目录名只作定位，身份以定义面为准，两者必须相等）` | `:505-516` |
| skill 正文越界/为空 | `skills.{key}: 正文路径非法（不能越出容器目录）：…` | `:469-474` |
| skill 正文缺失 | `skills.{key}: 声明的正文不存在：…` | `:469-474` |
| 同名容器被忽略 | `重复的容器名 {name!r} 已由 {existing.root.label}（rank={rank}）提供，本条被忽略` | `:569-578` |

`lock_issues` 与 `issues` **分开维护**（`:398-403`）：lock 问题只表示"漂移判定不可用"，成员仍可加载。

根存在但不可列举时 `_scan_root` 抛 `OSError`，调用方**不吞**（`:486-487`、`:550-551`）——这是扫描唯一会向外抛的异常。

## 7. 运行时投影（实现面）

### 7.1 只扫内置

`load_ability_definitions() -> Mapping[str, AbilityDefinition]`（`scheme/loading/ability_catalog.py:102-127`）用 `builtin_pool()`，任一 issue 即抛第一条。`builtin_pool()` 单独成函数（`:90-99`）是给测试换临时目录用的。

### 7.2 handler → 模块 → 类

路径约定（`scheme/loading/ability_catalog.py:166`）：handler 落在哪个容器的 `operations` 表，实现就在 `ghrah.scheme.ability.<容器>.operations.<handler>`，类是**该脚本里唯一的 `Ability` 子类**。

判定要求（`:190-201`）：`obj.__module__ == module.__name__`、`issubclass(obj, Ability)`、不是 `Ability` 本身，**数量必须恰好 1**，否则报 `"期望恰好一个 Ability 实现类，实际 N 个"`。这条规则是 §14 里"注册表不存在"的技术原因：类型名到类的映射被文件路径取代。

### 7.3 四件套常量

投影时必须能在实现脚本里找到四个模块级常量，缺失即 `ManifestValidationError`（`scheme/loading/ability_catalog.py:363-374`）：

| 常量 | 类型 | 用途 | 投影后去向 |
| --- | --- | --- | --- |
| `PERMISSIONS` | `dict` | 旧 `metadata.permissions` | `AbilityManifest.metadata.permissions` |
| `HOOKS` | `dict` | 旧 `hooks` | `AbilityManifest.hooks` |
| `SUMMARY` | `str` | 目录摘要；空串时回落 `operation.description` | `metadata.description` |
| `TAGS` | `list[str]` | 发现/过滤 | `metadata.tags` |

实例（`scheme/ability/fs/operations/read_file.py:45-58`）：`PERMISSIONS = {"fs_read_only": True}`、`HOOKS = {}`、`SUMMARY = "Read the content of a file at the specified path"`、`TAGS = ["filesystem","read"]`。

注意摘要与描述分属两面：`metadata.description` 取 `SUMMARY`，**工具面 description 取定义面 `description`**（`scheme/loading/ability_catalog.py:388-389` 与 `scheme/loading/tool_list.py:24`）。两处文案可以不一致，改工具描述要去改 `ability.yaml`。

### 7.4 查询与创建 API

| API | 签名 | 语义 | 行号 |
| --- | --- | --- | --- |
| `load_definition` | `(container: str) -> AbilityDefinition` | 未知容器抛 `ManifestValidationError(f"未知的 ability 容器：{container!r}")` | `:130-147` |
| `operation_containers` | `() -> Mapping[str, str]` | `{handler: 容器名}`，**含** registry-only | `:452-466` |
| `builtin_handlers` | `() -> tuple[str, ...]` | 升序 handler 列表，**含** registry-only | `:469-478` |
| `has_handler` | `(handler: str) -> bool` | 存在性 | `:481-492` |
| `ability_class` | `(handler: str) -> type[Ability]` | 未知抛 `f"未知的 ability handler: {handler!r}。已知: {builtin_handlers()}"` | `:495-515` |
| `create_ability` | `(handler: str, **params) -> Ability` | 原 `AbilityRegistry.create` 的唯一替代入口 | `:518-536` |
| `builtin_full_names` | `() -> tuple[str, ...]` | 声明路径成员全名，**不含** registry-only | `:424-439` |
| `load_builtin_manifest` | `(full_name: str) -> AbilityManifest` | 缺失抛 `FileNotFoundError(f"未知的内置 ability 全名：{full_name!r}")` | `:539-557` |

### 7.5 `REGISTRY_ONLY_OPERATIONS`

`REGISTRY_ONLY_OPERATIONS: frozenset[str] = frozenset({"send"})`（`scheme/loading/ability_catalog.py:81`）。原文理由（`:82-87`）：`send` 由 Core 的 room 装配期注入，历史上没有 manifest 声明；投影时跳过它以保持既有语义，`list_abilities` 的 registry-only 差异面依赖这一点。它仍是 `cluster/ability.yaml` 里的正式成员，只是不产出运行时视图。

三处口径因此不同，必须分清：

| 口径 | 含 `send`？ | API |
| --- | --- | --- |
| handler 全集 | 是（22 个） | `builtin_handlers()` |
| 定义面全名 | 否 | `builtin_full_names()` |
| 运行时视图 / 工具面 | 否（视图 21 / 工具面 20） | `load_all_builtin_manifests()` / `load_tool_list()` |

（上表数字为**仓库内置树**的口径，实测于 `f8ff157`。用户根 `~/.ghrah/skill-ability` 可另装 skill-only 容器——它们 0 个 operation、只贡献技能，不改变上表。）

`send` 仍可被 `has_handler("send")` 与 `create_ability("send")` 解析。

### 7.6 `AbilityManifest` 投影形态与遗留层

`_project()`（`scheme/loading/ability_catalog.py:338-396`）产出的 `AbilityManifest`：`manifest="ability"`、`version=LEGACY_MANIFEST_VERSION="1"`（`:78`，投影出的**旧格式版本**，不是能力定义版本）、`metadata{namespace, name, title, description, tags, permissions}`、`tool`、`implementation{type:"builtin", handler}`、`hooks`。

`manifest_io.parse_ability_manifest` / `validate_manifest` / `BuiltinManifestStore`（`scheme/loading/manifest_io.py:61-150`）针对的是**另一种文本形状**（`manifest: ability` + `version: "1"` + `metadata/tool/implementation/hooks`）。生产代码零调用，引用只在 `ghrah-core/examples/agents_manifest/runner.py:41,71`、`examples/student_grade_system/runner.py:38,66,76` 与 `tests/manifest/*`、`tests/core/test_*`。`load_agent` 恒抛 `ManifestNotFoundError`、`list_agents` 恒返回 `[]`（`:140-150`）。

结论：**写能力包时不要写 `manifest: ability`**，那是遗留形状；真路径是 `ability.yaml` + `schema_version: 0.1-draft`。

## 8. 工具面

### 8.1 结构

`load_tool_list(pool=None) -> ToolList`（`scheme/loading/tool_list.py:107-161`）。`ToolEntry`（`:47-68`）：`container`、`operation`（= 工具名）、`name`（与 `operation` 同值，单列字段便于客户端直接取）、`tool`、`root`，属性 `full_name` = `"{container}.{operation}"`。`ToolList`（`:72-104`）：`roots`、`tools`、`entries`、`issues`，属性 `names`，方法 `get(name)`（线性查，不按名字猜容器）。

工具体由 `ToolSchema.to_openai_tool()` 产出（`:143`），形状 `{"type":"function","function":{"name","description","strict","parameters"}}`。

> **注意**：本模块在仓内**没有生产调用点**——`load_tool_list()` 只被 `scheme/__init__.py:91` 导出，使用者是测试与 `plans/selfcheck/query_all_abilities.py`。模型实际拿到的工具面来自各实现的 `bind_tool()`（见 §8.4）。因此下面 §8.2 / §8.3 描述的是**定义面的工具投影规则**，不是运行时工具面。

### 8.2 命名：工具名就是成员键

工具名 = 成员键（`read_file`），**没有** `namespace__op`、没有 `fs.read_file` 前缀（`:23`、`:53-57`、`:129-131`）。`fs.read_file` 只是定义面全名。跨容器撞名的机制是"先入者胜 + 记 issue"，没有去重前缀机制（`:131-142`，文案 `重复的工具名 {key!r}：已由容器 {holder.container!r} 提供，本条被忽略`）。

### 8.3 筛选、排序、去重

- 筛选：`build_tool_schema(key, operation)` 返回 `None`（即 `expose: false`）就跳过（`:128-130`）。
- 收集顺序：容器名字升序 × 定义内成员声明顺序。
- 输出排序：最后按**工具名升序**重排 `tools` 与 `entries`（`:155-160`），两者同序。
- `ToolList.get()` 不做模糊匹配。

### 8.4 模型实际看到的工具面：`bind_tool()`

同一个 operation 的参数形状被写了两遍：`ability.yaml` 的 `operations.<键>.input_schema`（**声明面**）与实现里 `bind_tool()` 返回的输入模型 `model_json_schema()`（**实现面**）。

**生产链路走实现面**（完整时序见 `ghrah-06` §5.2）：`register_ability()`（`agents/base.py:212`）调 `ability.bind_tool()`（`:235`），非 `None` 就追加进 `_bound_tools`（`:236-237`）；LLM 创建时（`:453-454`）与运行期每次注册/注销之后（`:416-426`）经 `self._llm.configure_tools(self._bound_tools)`（`chat/format/__init__.py:84`）下发 provider，最终在 `openai.py` 拼进 `api_kwargs["tools"]`。`load_tool_list()` 那套投影没有人调用。

**`bind_tool()` 同时决定"有没有这个工具"和"它长什么样"**：

- 返回 `None` ⇒ schema 不进 `_bound_tools` ⇒ 模型看不到、也调不到该工具。`conversation` 就是这样被刻意隐藏的（`bind_tool=None`）。所以**新增成员必须实现 `bind_tool()`**，它不是可选项。
- `function.description` 由实现自己写（如 `scheme/ability/core/operations/load_skill.py:117-123`、`scheme/ability/fs/operations/read_file.py:130-137`）——**模型读的是这段文字**，不是 `ability.yaml` 里的 `description`。
- `function.parameters` 由 pydantic 输入模型 `model_json_schema()` 生成（`read_file.py:138`）。

**声明面 vs 实现面的实测漂移**（2026-10-28 记账）：内置树 22 个成员中 19 个可比，**18 个存在差异**；多数只是 pydantic 装饰（`title`、`additionalProperties: false`、`strict`），语义差异 5 处：

| 成员 | 差异 |
| --- | --- |
| `load_skill` | 声明面缺 `server`、`tool`（实现接受） |
| `search_files` | 声明面缺 `context_lines` 的 min/max、`max_results` 边界、`include` 可空 |
| `recall_context` | 声明面 `mode` 是自由字符串，实现是枚举 |
| `execute_command` | `working_dir` 声明非空、实现可空 |
| `list_directory` | `limit` / `offset` 声明有 `minimum: 0`、实现没有 |

**当前口径：以实现面为准。** 声明面的 `input_schema` 是文档性载荷，不参与校验（没有代码读它校验入参），只出现在 `list_abilities` 之类的人读视图里。新增成员时若只改 `ability.yaml` 不改 `bind_tool()`，模型会继续拿到旧说明书——不报错、不告警。

**为什么现在不修**：三条收敛路径（① `bind_tool()` 从声明投影；② 删掉声明面 `input_schema`、只留实现面；③ 两份都留、加一致性测试钉住）任选其一，都要动全部实现脚本或全部 5 份 `ability.yaml`，且会削弱"定义面自足"。在那之前同步靠人，记作 D-14。

**另两个同源遗留**：`to_prompt_description()`（`scheme/execution/base.py:83-93`）注释已标过时，没有调用者；`Ability.bind_tool()` 在协议里的位置是 `core/ability_protocol.py:41`。

## 9. 执行面

### 9.1 `Ability` 基类契约

`class Ability(ABC)`（`scheme/execution/base.py:32`）：

| 成员 | 签名 | 必需 |
| --- | --- | --- |
| `name` | `property -> str` | 抽象 |
| `execute` | `async (context: AbilityExecutionContext) -> ActionResult` | 抽象 |
| `get_hooks` | `() -> list[Hook]` | 抽象 |
| `bind_tool` | `() -> dict[str, Any] \| None`（默认 `None`） | 可选 |
| `to_prompt_description` | `() -> str`（默认 `""`） | 可选 |
| `get_default_state` | `() -> dict[str, Any]`（默认 `{}`，作 StateManager 初始作用域） | 可选 |

`base.py` 的 `__all__` 再导出 `ActionOutcome`/`ActionResult`（`:19`、`:25-29`），实现脚本不必额外 import。

**方法名是 `execute`，不是 `run`**；执行器只调 `execute`（`scheme/execution/executor.py:400`）。

### 9.2 `AbilityExecutionContext`

`@dataclass(init=False)`（`scheme/execution/context.py:37`），字段：`invocation: AbilityInvocation`（`:46`）、`data: ExecutionData`（`:47`）、`services: ExecutionServices`（`:48`）、`agent_state: dict[str, Any]`（`:49`）、`last_action_result: ActionResult | None`（`:50`）。

关键字专用 `__init__`（`:52-98`）合并规则：`ability_name = current_ability_name or invocation.ability_name`（`:71`）；`args = tool_args if tool_args is not None else invocation.tool_args`（`:72`）；`invocation` 重建且保留 `tool_call_id`（`:73-77`）；`accumulated_data` 若给则覆盖 `data`（`:80-81`）。

兼容属性：`current_ability_name`（`:100-110`）、`tool_args`（`:112-122`）、`accumulated_data`（`:124-130`，返回**活的** `data.raw`）、`context_manager`（`:132-138`）、`supervisor`（`:140-146`）、`agent_name`（`:148-154`）、`manifest_store`（`:156-163`）可读写；`skill_provider`（`:165-168`）与 `mcp_runtime`（`:170-173`）**只读**，赋值会 `AttributeError`。

状态 API：`get_ability_state()`（`:175-180`）、`update_state(changes)`（`:182-196`，缺 `context_manager` 抛 `RuntimeError("Cannot update state: context_manager is not set. This context may have been created outside the drive loop.")`）、`update_global_state()`（`:198-205`）。

调用标识只有一个：`invocation.tool_call_id`（默认 `""`），没有 trace/span 概念。

### 9.3 数据总线与服务总线

`DataKey`/`ExecutionData`（`scheme/execution/execution_data.py`）：`DataKey` 是 `{name, value_type}` 的 frozen dataclass，但 `value_type` **纯声明**，`get`/`set` 从不做 `isinstance` 校验（`:52-60`）。`ExecutionData.__init__` 收到 `dict` 时**按引用持有**（`:41-42`），`raw` 返回底层活字典（`:46-50`），`snapshot()` 深拷贝（`:76-79`）。

预置 `DataKey`（`:82-86`）：

| 键常量 | 存储名 | 标注类型 |
| --- | --- | --- |
| `TOOL_ARGS` | `tool_args` | `dict[str, Any]` |
| `LLM_RESPONSE` | `llm_response` | `str` |
| `COT_CONTENT` | `cot_content` | `str` |
| `CUMULATIVE_TOKEN_USAGE` | `cumulative_token_usage` | `dict` |
| `FINAL_RESPONSE` | **`response`** | `str` |

`FINAL_RESPONSE` 的实际存储名是 `"response"`，不是 `"final_response"`——按下划线直觉拼 key 会静默读不到。

预置 `ServiceKey`（`scheme/execution/execution_services.py:75-86`）：`CONTEXT_MANAGER`（`ContextManagerProtocol`）、`SUPERVISOR`（`SupervisorProtocol`）、`AGENT_NAME`（`str`）、`MANIFEST_STORE`（`Any`）、`SKILL_PROVIDER`（`Any`）、`MCP_RUNTIME`（`Any`）。`require()` 对未设置与 `None` 都抛 `RuntimeError(f"Required execution service '{key.name}' is not set.")`（`:56-61`）。

`ExecutionFrame`（`scheme/execution/execution_frame.py:21-29`）是 Hook 与驱动循环共用的精简上下文：`data`、`services`、`agent_state`、`last_action_result`、`current_node_id`。它与 `AbilityExecutionContext` 重复 4 个字段却少了 `invocation`，是两套并行模型（见 D-5）。

### 9.4 一次工具调用的时序

`execute_tool_calls`（`scheme/execution/executor.py:429-534`）→ 每个调用一个 `_execute_single_with_context`（`:536-588`）→ `execute_ability`（`:302-413`）。单次执行的步骤：

1. **无参数校验**。参数已在 `:558` 经 `_resolve_paths`（`:590-621`）做过路径解析（只处理 `ABILITY_PATH_SPECS` 的键，绝对路径原样保留）。schema 校验不发生在执行器里。
2. `PRE_EXECUTE` hook（`:323-325`）。
3. hook 结果分派（`:328-397`）：
   - `not should_continue` 且 `requires_hitl` → `handle_hitl_hook_result`（`:332`）；`outcome != "approved"`（`:333`）走超时分支（`:337-361`）或拒绝分支（`:362-378`）；approved 落到 `:379` 继续执行。
   - `not should_continue` 且无 HITL → 立即 `ActionResult(FAILURE)`（`:382-387`），`error = hook_result.message or "Execution blocked by hook"`。
   - `should_continue` 且 `route_to` → `ActionResult(outcome=ActionOutcome.DELEGATE, data={"route_to":...}, next_action_hint=route_to)`（`:388-394`）。
   - `should_continue` 且 `modified_context` → `context.accumulated_data.update(...)`（`:395-397`）。
4. **没有权限检查**。执行器的两个闸门只有 PRE_EXECUTE hook 与 HITL；权限判定在 hook 里（§11）。
5. `action_result = await ability.execute(context)`（`:400`）。
6. **没有异常处理**。docstring 只声明 `Raises: HookError`（`:319-321`）；隔离在 `execute_tool_calls`（`:507-522`）。
7. `await_reply` 早退（`:402-406`）：`action_result.data.get("await_reply")` 为真时把 `tool_args` 塞回、把 context 停进 `_deferred_contexts[message_id]`，**在 POST_EXECUTE 之前返回**。
8. `POST_EXECUTE`（`:409-411`，带 `result`）；返回值被丢弃——post hook 里的 `stop()`/`route_to` **不生效**。
9. 原样返回 `action_result`（`:413`）。延迟重放走 `finish_deferred`（`:415-423`），只重发 POST_EXECUTE。

### 9.5 并行与顺序

已知 ability 的调用被收集后 `asyncio.gather(..., return_exceptions=True)`（`:492-505`），**并行**执行，单次调用也走同样的一元素 gather。未知 ability 直接内联产出 `ActionResult(FAILURE, {"error": f"Unknown ability: {name}"})` 并**立刻 append**（`:462-480`），而已知调用的一批结果在 `:506-525` 才 append——因此结果列表顺序可能与 `tool_calls` 顺序不一致（D-6）。

每次调用的上下文在 `:568-584` 装配：`AbilityInvocation(tool_call_id=...)`、`current_ability_name`、`tool_args=解析后的参数`、`agent_state=context_manager.get_current_state()`、从 `self._supervisor` 用 `getattr` 取 `supervisor`/`manifest_store`/`skill_provider`/`mcp_runtime`（`:563-566`）、`accumulated_data={**deepcopy(accumulated_data), "tool_args": resolved_args}`（`:578-581`）、`last_action_result`、`agent_name`。

### 9.6 异常与取消

`:506-525`：非 `Exception` 的 `BaseException`（含 `CancelledError`）**原样重抛**（`:507-511`）；`Exception` 转成 `ActionResult(FAILURE, {"error": str(r)})`（`:512-522`）。注释（`:440-441`）明确子任务取消不得折算成结果。

一次 `ability.execute` 挂住会挂住整批 gather：执行面没有能力级超时（D-1）。

## 10. Hook 机制

### 10.1 十个 HookPoint

`HookPoint(str, Enum)`（`scheme/execution/hooks.py:41-75`）：

| 常量 | 值 | 层级 | 触发点 |
| --- | --- | --- | --- |
| `BEFORE_ACTION` | `before_action` | 循环 | 每次 action 之前（`:62`） |
| `AFTER_ACTION` | `after_action` | 循环 | 每次 action 之后（`:63`） |
| `ON_ERROR` | `on_error` | 循环 | 错误处理（`:64`） |
| `ON_MAX_ITERATIONS` | `on_max_iterations` | 循环 | 达到最大迭代（`:65`） |
| `PRE_LLM_CALL` | `pre_llm_call` | action | LLM 调用前（`:68`） |
| `POST_LLM_CALL` | `post_llm_call` | action | LLM 调用后（`:69`） |
| `PRE_TOOL_EXECUTE` | `pre_tool_execute` | action | 工具执行前（`:70`） |
| `POST_TOOL_EXECUTE` | `post_tool_execute` | action | 工具执行后（`:71`） |
| `PRE_EXECUTE` | `pre_execute` | ability | `ability.execute()` 前（`:74`） |
| `POST_EXECUTE` | `post_execute` | ability | 执行后（副作用）（`:75`） |

执行器只发 `PRE_EXECUTE`（`:323-325`）与 `POST_EXECUTE`（`:409-411`、延迟重放 `:421-423`）；其余 8 个归驱动循环与 `_action`（层级说明见 `:12-18`、`:44-58`）。因此权限与 HITL hook 全部挂在 `PRE_EXECUTE`。

### 10.2 Hook 契约

`class Hook`（`scheme/execution/hooks.py:190-237`）：类属性 `hook_point: HookPoint`（`:209`）、可选 `scope: HookScope | None = None`（`:210`）；抽象 `async should_trigger(context) -> bool`（`:212-222`）与 `async execute(context, result: ActionResult | None) -> HookResult`（`:224-237`，`result` 在 PRE_EXECUTE 为 `None`、在 POST_EXECUTE 为 `ActionResult`）。

### 10.3 `HookResult` 语义矩阵

`@dataclass HookResult`（`:113-131`）：`should_continue: bool = True`、`route_to: str | None`、`modified_context: dict | None`、`data_updates: dict | None`、`message: str | None`、`requires_hitl: bool = False`。`__post_init__`（`:133-141`）把 `modified_context` 与 `data_updates` 当同一份合并后的 dict 的别名。

| 工厂 | 产生的字段 | 执行器行为 |
| --- | --- | --- |
| `continue_()`（`:143-146`） | 全默认 | 继续 |
| `stop(message=)`（`:148-151`） | `should_continue=False`，`requires_hitl=False` | 直接 `FAILURE`（`executor.py:382-387`） |
| `hitl(message=)`（`:153-159`） | `should_continue=False`，`requires_hitl=True` | 进入审批等待（`executor.py:330-332`） |
| `route(target, message=)`（`:161-164`） | `route_to` | `ActionOutcome.DELEGATE`（`executor.py:388-394`） |
| `modified_context`/`data_updates` | dict | 合并进 `accumulated_data`（`executor.py:395-397`、`hook_runner.py:56-57`） |

合并规则 `merge()`（`:166-187`）：`should_continue` 取 **AND**（`:177`）、`route_to` 后者覆盖（`:178`）、`modified_context` 字典合并且后者覆盖（`:179-184`）、`message` 后者覆盖（`:185`）、`requires_hitl` 取 **OR**（`:186`）。含义：**一个 hook 要求停，整条链的结果就是停**；一个 hook 要求审批，就是审批。

### 10.4 `HookScope`

不是 Enum，是 `@dataclass(frozen=True)`（`:78-83`）：`points: frozenset[HookPoint]`、`target_abilities: frozenset[str] | None = None`。校验（`:85-89`）：`points` 为空抛 `ValueError("HookScope.points must not be empty.")`；`target_abilities == frozenset()` 抛 `ValueError("HookScope.target_abilities must be None or non-empty.")`。工厂 `global_(*points)`（`:91-95`）与 `for_abilities(points, abilities)`（`:97-110`）。

`scope is None` 的 hook 是"legacy"：每次运行都要 `await should_trigger()` 再决定（`hook_store.py:80`、`:104-107`、`hook_runner.py:31-41`）；声明了 `scope` 的 hook 由索引直接选中，**跳过** `should_trigger`。现有权限 hook（`AccessApprovalHook`、`CommandApprovalHook`、`HITLApprovalHook`）都用 legacy 形态。

### 10.5 `HookRunner`：唯一执行路径

`async run(context) -> HookResult | None`（`hook_runner.py:17-59`）：

1. `context.to_ability_context()` 重建一次门面（`:27`）；
2. `store.select(point, ability_name)`（`:28`）；
3. 遍历**全部**命中 hook（`:30`），legacy 的要先过 `should_trigger`（`:31-33`，假则 `continue` `:40-41`）；
4. `await hook.execute(...)`（`:44`，总是 await）；
5. 逐个 `merge`（`:51-54`）；
6. 循环后若 `merged.modified_context` 非空，写入 `context.frame.data`（`:56-57`）；
7. 返回合并结果（`:59`）。

**没有短路**：没有 `break`，所有命中 hook 都会跑，优先级由 `merge` 的 AND/OR 决定。异常不吞：`should_trigger` 失败抛 `HookError(context.point.value, f"should_trigger failed for {type(hook).__name__}: {e}")`（`:34-38`），`execute` 失败抛同形 `HookError`（`:45-49`）；由于 `execute_ability` 无 try/except，`HookError` 会逃到 `execute_tool_calls` 变成 `FAILURE{str(e)}`。

副作用：PRE_EXECUTE 的 `modified_context` 实际被写两次——`hook_runner.py:56-57` 写 `frame.data`（与 `context.data.raw` 同一对象），`executor.py:395-397` 又写 `accumulated_data`。内容相同、幂等，但两个写入点（D-7）。

### 10.6 `HookStore`

索引（`hook_store.py:64-69`）：`_entries`（注册顺序）、`_global_by_point`、`_by_point_and_ability`、`_legacy_by_point`。`AGENT_OWNER = "__agent_hooks__"`（`:61`）。

| API | 语义 | 行号 |
| --- | --- | --- |
| `view(owner=AGENT_OWNER)` | 返回 `HookListView` | `:71-74` |
| `add_hooks(owner, hooks)` | `legacy = getattr(hook, "scope", None) is None` | `:76-82` |
| `remove_owner(owner)` | 摘除并重建索引 | `:84-88` |
| `select(point, ability_name="")` | global + legacy + `(point, ability)` 三个桶取并集，再按注册顺序重排 | `:95-102` |
| `is_legacy(hook)` | 判定 | `:104-107` |

`HookListView`（`:29-55`）是给 `agent._all_hooks` 的兼容门面，支持 `append`/`extend`/`clear`；其 `__len__` 统计的是 `store.list_all()`（所有 owner）而不是本 owner 的子集（`:36-37`），多 owner 场景下长度会偏大。

### 10.7 `HookContext`

`@dataclass HookContext`（`hook_context.py:23-30`）：`point`、`frame`、`invocation`、`result`。`from_ability_context()`（`:32-51`）把 `data` **按引用**放进 `ExecutionFrame`（`:43-48`）并复用 `invocation`（`:49`）；`to_ability_context()`（`:53-64`）为 legacy hook 重建门面。

## 11. 权限与 HITL

三层结构，缺一层就会出事：

```
ability.yaml / 装配参数            PERMISSIONS 常量 + CoreUnitConfig
        │                                  │
        ▼                                  ▼
FSPermissionChecker / CommandSafetyChecker   ← 判定层：只产出 (allowed, status)
        │
        ▼
PRE_EXECUTE Hook（AccessApprovalHook / CommandApprovalHook / HITLApprovalHook）
        │  stop() → FAILURE ；hitl() → 等待
        ▼
LocalAbilityExecutor.handle_hitl_hook_result → HITLRequestEvent → 等待 → 终态
```

### 11.1 FS 判定链

`FSPermissionChecker(allowed_paths=None, workspace_root=None, denied_paths=None, require_approval=True)`（`scheme/permissions/fs_permissions.py:72-78`）；三个路径参数都 `Path.resolve()`（`:79-82`、`:85-92`），即白名单/黑名单也消解符号链接。`require_approval` 类级默认 **True**（`:77`）。

`check_access(path, operation="read") -> tuple[bool, str | None]`（`:136-171`）按序判定：

| 序 | 条件 | 返回 | 行号 |
| --- | --- | --- | --- |
| 1 | 既无白名单也无 `workspace_root` | `(True, "pending")` 若 `require_approval`，否则 `(False, "Permission denied: no allowed paths configured (set allowed_paths or workspace_root, or enable require_approval for HITL)")` | `:151-157` |
| 2 | 命中 denied | `(False, "Permission denied: {path} is in denied paths")` | `:159-160` |
| 3 | 命中 allowed 或 workspace 内 | `(True, None)` | `:162-163` |
| 4 | `require_approval` | `(True, "pending")` | `:165-166` |
| 5 | 其他 | `(False, "Permission denied: {path} not in allowed paths; allowed roots: …")` | `:168-171` |

二元组语义：`(True, None)` 自动批准；`(True, "pending")` 需 HITL；`(False, reason)` 拒绝。**没有三元组**，也**没有读/写分级**——`operation` 形参在函数体内从未使用（`:136` vs `:149-171`），`check_read_path`/`check_write_path` 只是转调（`:173-203`）。读写在 hook 层由两张名单区分：`WRITE_ABILITIES={"write_file","edit_file","move_file","delete_file"}`、`READ_ABILITIES={"read_file","list_directory","search_files"}`（`:260-261`）。

`AccessApprovalHook`（`:234-299`）：`hook_point=PRE_EXECUTE`（`:258`）；`should_trigger` 只看 ability 名（`:266-269`）；`execute` 取 `tool_args`（`:275`）、无目标路径则 `continue_`（`:278-279`）、`check_access`（`:282`）后：拒绝 → `HookResult.stop(message=status)`（`:284-285`）；pending → `HookResult.hitl(message=f"Human approval required for {operation} operation on: {target_path}")`（`:287-290`）；否则 `continue_`（`:292`）。目标路径提取只取**一个**：`move_file` 取 `destination_path`，其他取 `file_path or dir_path`（`:294-299`）。

关键耦合：ability 内联检查把 `(True, "pending")` **当允许放行**（`scheme/ability/fs/operations/read_file.py:182-188`、`write_file.py:173-179`）。若装配期没挂 `AccessApprovalHook`，pending 就等于放行。代码注释已承认这条断链（`unit.py:512-515`、`scheme/execution/ability_build.py:144-146`）。

### 11.2 命令分级

枚举精确值（`scheme/permissions/command_safety.py:53-56`）：`SAFE="safe"`、`DANGEROUS="dangerous"`、`REQUIRE_HITL="require_hitl"`。`CommandSafetyVerdict`（`:59-65`）带 `category`、`base_command`、`sub_command`、`reason`。

判定顺序（实现 `:302-390`）：空命令 → DANGEROUS（`:311-316`）→ base 在危险表 → DANGEROUS（`:322-328`）→ base 有子命令路由表（`:330-333`）时：子命令危险 → DANGEROUS（`:337-344`）、子命令安全 → SAFE（`:345-352`）、其余 → REQUIRE_HITL（`:358-367`）→ base 在安全表 → SAFE（`:369-375`）→ `require_approval=False` → SAFE（`:377-383`，**危险名单不受影响**）→ 兜底 REQUIRE_HITL（`:385-390`）。

`parse_command()`（`:392-435`）用 `shlex.split`，`ValueError` 回退 `split()`（`:413-416`）；base 取 `Path(parts[0]).name`（`:421`，故 `/usr/bin/ls` → `ls`）；子命令 = 首个"不以 `-` 开头、不含 `/`、不含 `.`（除非以 `.` 开头）"的 token（`:424-433`）。副作用：`python script.py` 得 `sub=None`（`:430` 因 `.` 跳过），而 `git -C repo status` 会把 `repo` 误当子命令（D-8）。

`extend_safe_lists()`（`:279-300`）只并入安全集合、小写归一，危险名单只增不减。

`CommandApprovalHook`（`:438-469`）：PRE_EXECUTE（`:448`），只拦 `execute_command`（`:453-454`），命令取 `tool_args["command"]`（`:460`），SAFE → `continue_()`、DANGEROUS → `stop(reason)`、其余 → `hitl(reason)`（`:463-469`）。

### 11.3 HITL 等待协议

`handle_hitl_hook_result`（`scheme/execution/executor.py:651-763`）：

1. 生成 `tool_call_id`（取 `tool_args["call_id"]`，否则 `uuid4().hex[:12]`）与 `promise_id = uuid4().hex`（`:671-672`）；
2. 发布 `HITLRequestEvent`（`:676-685`）；
3. `HITLFutureStore.create_future(agent, ability, tool_call)`（`:688-692`，同键旧 future 会被 cancel 替换，`core/hitl.py:88-90`）；
4. 登记 promise registry（`:693-699`）；
5. 熔断判定：`degraded = limit > 0 and consecutive >= limit`（`:702-706`），默认 `hitl_consecutive_timeout_limit=0` 即**关闭**；
6. `await asyncio.wait_for(future, timeout=wait_timeout)`（`:710`，全文件唯一 `wait_for`）。

结果三种（`HitlOutcome = Literal["approved","rejected","timeout"]`，`:55`）：

| 终态 | 产出 | 行号 |
| --- | --- | --- |
| approved | 复位熔断计数，`HITLResolvedEvent(status="approved")`，`HITLWaitResult(outcome="approved", …)` | `:711-734` |
| rejected | 同上但 `status="rejected"`；消息 `f"HITL request rejected by human: {reason}"`，无理由退回 `hook_result.message or "Execution blocked by HITL"`；data 含 `hitl_rejected=True` | `:362-378`、`:729-734` |
| timeout | 计数 +1、移除 promise、`HITLResolvedEvent(status="timeout")`；data 含 `hitl_timeout=True`、`hitl_degraded`、`promise_id`、`tool_call_id` | `:337-361`、`:735-763` |

超时消息两种：普通 `f"HITL approval timed out after {self._hitl_timeout}s with no response delivered; {attribution}"`（`:348-351`）；降级 `"...(degraded wait; approval channel likely offline after {limit}+ consecutive timeouts); ...; consider wrapping up and calling end_task instead of requesting more approvals"`（`:339-346`）。注释明确**超时不得折算成用户拒绝**（`:334-336`）。

`HITLWaitResult`（`:58-74`）：`outcome`、`promise_id`、`tool_call_id`、`reason`（审批人给的 `{"reason": ...}`）、`degraded`、`degraded_timeout`。

回执入口 `receive_hitl_response(ability_name, tool_call_id, approved, result=None) -> bool`（`:765-791`），构造 `HITLResult(approved, result)` 调 `resolve_future`（`core/hitl.py:31-41`）。定位键是 `(agent_name, ability_name, tool_call_id)`。

已知竞态：`HITLRequestEvent` 在 future 创建**之前**发布（`:676-685` vs `:688-692`），审批人若在此窗口内立即回执，会命中一个尚不存在的 future（D-9）。

### 11.4 默认开关

| 配置 | 默认 | 位置 |
| --- | --- | --- |
| `PermissionFlags.*`（manifest 字段） | 布尔全 `False`，列表全空 | `scheme/loading/manifest_fields.py:60-71` |
| `PermissionFlags.merge` | 布尔 OR、`allowed_*` 替换、`denied_*` 追加 | `:73-97` |
| `CoreUnitConfig.require_approval_by_default` | `True` | `unit.py:181` |
| `CoreUnitConfig.auto_approve_abilities` | `()` | `unit.py:180` |
| `CoreUnitConfig.workspace_root` | `None` | `unit.py:179` |
| `CoreUnitConfig.hitl_timeout` | `300.0` | `unit.py:178` |
| `CoreUnitConfig.hitl_consecutive_timeout_limit` | `0`（熔断关闭） | `unit.py:184` |
| `CoreUnitConfig.hitl_degraded_timeout` | `30.0` | `unit.py:185` |
| `HITLPolicyConfig.require_approval_by_default`（Subject） | `True` | `subject/config.py:88` |
| `HITLPolicyConfig.environment_injection` | `False` | `subject/config.py:95` |

环境变量（Subject 侧）：`GHRAH_SUBJECT_HITL_REQUIRE_APPROVAL` 默认 `"true"`（`subject/config.py:478-481`）、`GHRAH_SUBJECT_HITL_ALLOWED_PATHS`（`:482-484`，`；` 分隔）、`GHRAH_SUBJECT_HITL_WORKSPACE_ROOT`（`:485`）、`GHRAH_SUBJECT_HITL_AUTO_APPROVE_ABILITIES`（`:473-477`）。注入链：`subject/registry.py:420-434` 把 `config.hitl_policy.*` 映射进 `CoreUnitConfig`。

生效读法：命令未知名在 `require_approval=True` 下是 REQUIRE_HITL；FS 在无 allowed/workspace 时全 pending（`fs_permissions.py:151-153`）或全拒（`:154-157`）；有 `workspace_root` 时工作区内自动放行。

## 12. 装配：谁把这一切接起来

能力对象由两个装配点创建，**两者对 FS 的默认行为不同**（D-2）。

### 12.1 `CoreUnitConfig`（`unit.py:116-197`）

`@dataclass(frozen=True)`，与本主题相关的字段：`workspace_root: str | None = None`（`:179`）、`auto_approve_abilities: tuple[str, ...] = ()`（`:180`）、`require_approval_by_default: bool = True`（`:181`）、`safe_extra_commands: tuple[str, ...] = ()`（`:182`）、`safe_extra_sub_commands: tuple[tuple[str, tuple[str, ...]], ...] = ()`（`:183`）、`hitl_timeout: float = 300.0`（`:178`）、`hitl_consecutive_timeout_limit: int = 0`（`:184`）、`hitl_degraded_timeout: float = 30.0`（`:185`）。

### 12.2 装配点 A：`_create_ability_from_def`（`unit.py:448-544`）

输入 `ability_def`（带 `ability_type` 与 `params`），按类型分派：

- FS（`ability_type in FS_ABILITY_TYPES` 且 params 命中 `{"require_hitl","allowed_paths","denied_paths","workspace_root"}`，`:491-493`）：弹出四个字段（`:494`），`require_approval` 决策 `auto_approved → False`，否则显式 `require_hitl`，否则 `require_approval_by_default`（`:499-504`）；构造 `FSPermissionChecker`（`:505-510`）；`require_approval` 为真时挂 `AccessApprovalHook`（`:516-517`）。
- `execute_command`（`:519-539`）：`require_approval` 决策同上（`:521-527`）；`CommandSafetyChecker`（`:528`）；有扩展白名单则 `extend_safe_lists`（`:530-535`）；**恒**挂 `CommandApprovalHook`（`:537`）。
- `end_task`（`:541-542`）：`params["mode"] = "toolcall"`。
- 最后 `create_ability(ability_type, **params)`（`:544`）。

docstring（`:461-475`）给出的优先级：`auto_approve > manifest 显式声明 > 兜底默认`。

### 12.3 装配点 B：`instantiate_resolved_abilities`（`scheme/execution/ability_build.py:55-64`）

签名：

```python
def instantiate_resolved_abilities(
    resolved_abilities: list[ResolvedAbility], *,
    workspace_root: str | None,
    auto_approve_abilities: tuple[str, ...] = (),
    require_approval_by_default: bool = True,
    command_runner: Any = None,
    safe_extra_commands: tuple[str, ...] = (),
    safe_extra_sub_commands: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> list[AbilityProtocol]
```

流程：`template_vars = {"workspace": workspace_root} if workspace_root else {}`（`:88`）→ 逐条 `ResolvedAbility`（`:90`）→ 非 builtin 或无 handler 则 `logger.warning("Skipping non-builtin ability '%s' (type=%s) in manifest spawn", …)` 并跳过（`:91-98`）→ `auto_approved = handler in auto_approve_abilities`（`:104`，按 **handler** 匹配，不是 `ability_name`）→ `_instantiate_ability`（`:99-109`）。

分派顺序（`:114-177`）：`conversation` 原样创建（`:129-130`）→ `end_task` 加 `mode="toolcall"`（`:132-133`）→ **FS 分支先于命令分支**（`:135-149`）→ `execute_command`（`:151-166`）→ 兜底（`:168-177`）。

差异点（必须知道）：

| 项 | 装配点 A | 装配点 B |
| --- | --- | --- |
| FS `require_approval` 兜底 | `require_approval_by_default`（`unit.py:504`） | 只用 `bool(permissions.require_hitl)`（`ability_build.py:219`），`require_approval_by_default` 被接收但**不读**（`:196`、`:141`） |
| command 兜底 | `require_approval_by_default`（`unit.py:527`） | `bool(require_hitl) or require_approval_by_default`（`:242`） |
| FS hook 挂载 | `require_approval` 为真才挂（`unit.py:516`） | 同样条件（`:147-148`） |
| command hook 挂载 | **恒**挂（`unit.py:537`） | **恒**挂（`:251`） |

后果：同一份 manifest 在两条路径下可能一边是 HITL、一边是直接拒绝（`unit.py:504` 兜底 `True` vs `ability_build.py:219` 兜底 `False`）。`ability_build.py` 的 docstring（`:24-25`、`:71-72`、`:201`）声明了 FS 也会用 `require_approval_by_default`，与实现不符。

另一个顺序副作用：FS 判断在命令判断之前，一个带 `fs_*` 字段的 `execute_command` 会走 FS 分支并**丢失** `command_checker`/`command_runner`（D-4）。

兜底分支对通用 ability 的包装：`create_ability(handler)`（`:168`），若 `not auto_approved and bool(permissions.require_hitl)` 则包一层 `_HITLRequiredAbility(ability, ability_name=handler, reason=f"manifest requires HITL approval for '{handler}'")`（`:171-176`）。

失败语义：函数自身不抛异常；非 builtin 只跳过 + warning（docstring `:17-19` 显式对比独立 runner 的 `raise ValueError`）；`create_ability`/checker 的异常不捕获，直接上抛。

### 12.4 `HITLRequiredAbility` 与三个审批 hook 的关系

- `HITLRequiredAbility`（`scheme/permissions/hitl.py:69-100`）是**组合**不是继承：委托 inner（`:76-78`、`:84-85`），`get_hooks()` 把 `HITLApprovalHook` 追加到宿主 hooks 之后（`:87-88`），`__getattr__` 透传（`:99-100`）。
- `HITLApprovalHook`（`:40-66`）：PRE_EXECUTE（`:50`），`should_trigger` 只在 `context.current_ability_name == self._ability_name` 时为真（`:56-57`），消息 `f"{reason} — ability='{name}' args={summary}"`（`:64-66`，`summary = str(tool_args)[:200]`）。

三者分工：`AccessApprovalHook` 管 FS 路径、`CommandApprovalHook` 管命令分级、`HITLApprovalHook` 管"manifest 声明要审批但能力本身不检查权限"的通用情形。

## 13. 不变式与已知偏差

### 13.1 不变式（可被测试引用）

| 编号 | 不变式 | 校验点 |
| --- | --- | --- |
| INV-01 | 容器名 == 目录名 == `identity.name or identity.namespace` | `ability_scan.py:505-516` |
| INV-02 | `implementation.operation == 成员键` | `ability_schema.py:127-132` |
| INV-03 | `implementation.provider_slot` ∈ `provider_requirements` | `ability_schema.py:135-140` |
| INV-04 | 一个容器至多一个 skill 成员 | `ability_schema.py:136-142` |
| INV-05 | 每个 `operations/<key>.py` 恰好一个 `Ability` 子类，且 `__module__` 匹配 | `ability_catalog.py:190-201` |
| INV-06 | 每个实现脚本有 `PERMISSIONS`/`HOOKS`/`SUMMARY`/`TAGS` 四件套 | `ability_catalog.py:363-374` |
| INV-07 | 工具名 = 成员键；`expose=false` 不进工具面 | `tool_list.py:128-131` |
| INV-08 | 内置根 rank 0 且不可写，用户根 rank 100，同名内置胜 | `ability_roots.py:52-66`、`ability_scan.py:554-579` |
| INV-09 | 所有查询无缓存，改盘立即生效 | §3.2 各行号 |
| INV-10 | 权限拒绝不抛异常，一律 `ActionResult(FAILURE, {"error": …})` | `errors.py:11`（只有两个异常类）、`executor.py:382-387` |
| INV-11 | 超时不得折算为用户拒绝 | `executor.py:334-336` |
| INV-12 | 取消（非 `Exception` 的 `BaseException`）必须原样重抛 | `executor.py:507-511` |

### 13.2 已知偏差与技术债

| 编号 | 偏差 | 证据 | 影响 |
| --- | --- | --- | --- |
| D-1 | `execution.timeout_ms` 不生效；能力执行无超时 | `ability_schema.py:188` vs `executor.py:400` | 能力挂住会挂住整批 gather |
| D-2 | 两条装配路径 FS 默认不同 | `unit.py:504` vs `ability_build.py:219` | 同 manifest 行为可能不同 |
| D-3 | 权限与 hooks 不在定义面 | `ability_schema.py:11-12` | 判断"要不要审批"必须读实现脚本 |
| D-4 | FS 判断先于命令判断 | `ability_build.py:135` vs `:151` | 带 `fs_*` 字段的 `execute_command` 丢失命令安全层 |
| D-5 | `ExecutionFrame` 与 `AbilityExecutionContext` 双模型 | `execution_frame.py:25-29` | 字段漂移风险 |
| D-6 | 未知 ability 结果先 append | `executor.py:462-480` vs `:506-525` | 结果顺序可能与 `tool_calls` 不一致 |
| D-7 | PRE_EXECUTE `modified_context` 写两次 | `hook_runner.py:56-57`、`executor.py:395-397` | 幂等但两个写入点 |
| D-8 | `parse_command` 把带值选项实参当子命令 | `command_safety.py:424-433` | `git -C repo status` 误判为需审批 |
| D-9 | `HITLRequestEvent` 早于 future 创建 | `executor.py:676-685` vs `:688-692` | 极快回执可能丢失 |
| D-10 | `pending` 在能力内联检查中等同放行 | `read_file.py:182-188`、`write_file.py:173-179` | 未挂 hook 时越权静默通过 |
| D-11 | `move_file` 只审 destination | `fs_permissions.py:294-299` | 白名单外 source 不触发审批 |
| D-12 | 非 `"."` 的相对 `allowed_paths` 按进程 CWD 解析 | `paths.py:142` + `fs_permissions.py:87` | 与"绝不泄漏 $PWD"承诺不一致 |
| D-13 | POST_EXECUTE 返回值被丢弃 | `executor.py:409-411` | post hook 的 `stop()`/`route_to` 无效 |
| D-14 | `bind_tool()` 与 `ability.yaml` 双份 schema | `read_file.py:124-140` vs `fs/ability.yaml:30-51` | 19 个可比成员中 18 个有差异（5 处语义）；以实现面为准，同步无校验、无告警 |
| D-15 | `DataKey.value_type`/`ServiceKey.service_type` 无运行时校验 | `execution_data.py:31-32`、`execution_services.py:35-36` | 类型错误延迟到使用点 |
| D-16 | `FINAL_RESPONSE` 实际键名是 `response` | `execution_data.py:86` | 按常量名猜键会读不到 |
| D-17 | `HookListView.__len__` 统计所有 owner | `hook_store.py:36-37` | 多 owner 下长度偏大 |
| D-18 | `catalog.py:104` 声明 `AbilityDefinitionError`，实际抛 `ManifestValidationError` | `catalog.py:104` vs `ability_catalog.py:126` | 异常捕获可能漏 |
| D-19 | `CLUSTER_ABILITY_TYPES` 无消费者 | `kinds.py:31-41` | 死常量 |
| D-20 | `extract_paths` 生产代码无消费者 | `paths.py:58` | 规格表与运行时判定已解耦 |

## 14. 旧概念对照

| 旧文档中的概念 | 现状 | 当前对应物 |
| --- | --- | --- |
| `AbilityRegistry` 类 | **不存在**（仅存注释提及被取代） | 扫盘 + 路径约定：`ability_catalog.py:166`、`:495-536` |
| `AbilityRegistry.create/has/list_types/get_class/register/unregister/clear` | 前四个有替代，后三个**删除** | `create_ability` / `has_handler` / `builtin_handlers` / `ability_class` |
| `AbilityMount` / `MountTable` / `mount_id` | **不存在**（全仓 0 命中，仅设计稿里出现） | 无对应物：容器 + 目录池取代挂载表 |
| `LoadLockEntry` | **不存在** | `LockRecord` / `LockOutcome` / `SkillFingerprint` |
| `SeedSnapshot` | **不存在** | 无对应物 |
| `CatalogEntry` | 裸名**不存在** | `SkillCatalogEntry`（Protocol）/ `CatalogReport` |
| `AbilityDescriptor` | 不在 `ghrah-core` | subject 侧概念，非本仓代码 |
| 能力 CRUD 指令面（P1 计划） | **未实现** | `list_abilities` 是只读工具（`core/operations/list_abilities.py:99`） |
| G1–G14 缺口清单 | 历史分类，不再适用 | 现以 §13.2 D-1…D-20 表达 |
| "注册表在 import 期快照" | **不成立** | 全部查询每次重新扫盘（§3.2） |
| 工具名 `namespace__op` 或 `container.op` | **错** | 工具名 = 成员键（`tool_list.py:53-57`） |

各条判定的推理过程与当时的设计取舍见 `07-设计决策与演进留痕.md`。