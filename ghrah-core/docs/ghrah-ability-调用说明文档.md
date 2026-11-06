# ghrah.ability 调用说明（目录面 / 执行面）

本文只覆盖两个面的调用接口：**目录面**（这台机器上有哪些能力包、包里有什么成员、技能正文怎么读）与**执行面**（成员怎么投影成模型工具、怎么拿到可执行实例并运行）。

内容版本、授权、受控读取、安装期与装配期入口不在本文范围。

## 0. 通用约定

| 约定 | 说明 |
| --- | --- |
| 返回值类型 | 目录面是 frozen dataclass（只读、可哈希）；执行面的 view 是 pydantic 模型（`model_config = extra="forbid"`） |
| 顺序 | 包按 `name` 升序、成员按 `key` 升序、技能按 `full_name` 升序——跨文件系统、跨机器一致，可直接用于快照比对 |
| 无缓存 | 每次调用都重新扫盘/重新投影；`ability.yaml` 改了下一次调用即生效 |
| `None` 的含义 | "该场景没有这个值"，不是错误；错误集中在 `issues`（收集式），或按文档直接抛异常 |
| 副作用 | 目录面只读定义面与目录结构，**不 import 实现脚本、不读整篇正文**；执行面的 `_project` 会 import 实现脚本 |

入口一览：

| 面 | 函数 | 返回 |
| --- | --- | --- |
| 目录面 | `ghrah.ability.scan_abilities(pool=None)` | `tuple[AbilityContainer, ...]` |
| 目录面 | `ghrah.ability.loading.ability_scan.scan_ability_definitions(pool=None)` | `AbilityScanResult` |
| 目录面 | `ghrah.ability.loading.skill_context.load_skill_catalog(pool=None)` | `SkillCatalog` |
| 执行面 | `ghrah.ability.loading.ability_catalog.load_all_builtin_manifests()` | `Mapping[str, AbilityManifest]` |
| 执行面 | `...ability_catalog.builtin_handlers()` / `has_handler(h)` / `ability_class(h)` / `create_ability(h, **params)` | 见 §2.2 |

## 1. 目录面

### 1.1 `scan_abilities(pool=None) -> tuple[AbilityContainer, ...]`

扫描目录池，按"能力包"返回全貌。`pool=None` 取默认池：内置树（rank 0）+ `~/.ghrah/skill-ability`（rank 100）。同名包 rank 小者胜出，落败副本在胜出者的 `issues` 里留一条记录。

```python
from ghrah.ability import scan_abilities

for container in scan_abilities():
    print(container.name, container.version, container.kinds)
    for operation in container.operations:
        print("  op", operation.key, operation.effects, operation.definition.expose)
    for skill in container.skills:
        print("  skill", skill.catalog_name, skill.body_path, skill.tags)
```

返回的每个元素是 `AbilityContainer`：

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `name` | `str` | 容器名 = 包目录名 = `identity.name`（三者已校验一致） | 恒非空 |
| `namespace` | `str` | 点分身份的命名空间前缀 | 内置包为 `ghrah`；用户池为各包自己的命名空间 |
| `version` | `str` | 定义面声明的版本 | 恒非空；不存在"未标版本"的包 |
| `title` | `str` | 容器标题（给人看） | 定义面必填 |
| `description` | `str` | 容器整体用途说明 | 定义面必填 |
| `root` | `AbilityRoot` | 来源根：来自哪个目录池、什么优先级 | 见下表 |
| `locator` | `Traversable` | 容器目录句柄（实现脚本、正文都相对它定位） | 真实目录是 `Path`；内置树是包资源 |
| `definition` | `AbilityDefinition` | 解析后的定义面（权威载荷） | 恒有；`definition.origin` 区分 `source` / `mcp` |
| `operations` | `tuple[AbilityOperation, ...]` | operation 成员，按键升序 | 纯技能包为空元组 |
| `skills` | `tuple[AbilitySkill, ...]` | skill 成员，按键升序 | 纯工具包为空元组 |
| `assets` | `tuple[AbilityAsset, ...]` | 顶层 asset 成员（旧格式遗留，通常为空） | 新包一律为空；附件归属技能，见 `skills[i].assets` |
| `issues` | `tuple[AbilityScanIssue, ...]` | 归属于本包的问题 | 健康包为空元组；同名副本被忽略、正文缺失等会有一条 |

派生属性（无需再聚合）：

| 属性 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `full_name` | `str` | 点分身份（`ghrah.fs`） | 恒非空 |
| `kinds` | `tuple[str, ...]` | 本包实际有哪几类成员：`operations` / `skills` / `assets` | 成员都为空时为 `()`（定义合法但没成员） |
| `operation_count` / `skill_count` / `asset_count` | `int` | 各类成员数量 | 至少一个为 0 很常见 |
| `member_count` | `int` | 三类成员总数（不含问题） | 同上 |
| `is_operation_only` | `bool` | 是否只有 operation 成员（纯工具包，如 `fs` / `shell`） | 纯技能包为 `False` |
| `is_skill_only` | `bool` | 是否只有 skill 成员（用户池里的纯技能包） | 纯工具包为 `False` |
| `__str__` | `str` | `名字 (根标签) operations=N skills=M` 一行 | 日志/控制台可直接打印 |

#### `AbilityRoot`（`container.root`）

| 字段/属性 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `rank` | `int` | 优先级，越小越优先 | 内置树 `0`；用户目录 `100` |
| `source` | `str` | 根标签，写进报告 | `builtin` / `skill-ability` / 自定义 |
| `required` | `bool` | 该根缺失算不算问题 | 内置树 `True`（缺失是硬故障）；用户目录 `False`（首次安装还没建目录是正常状态） |
| `locator` | `Traversable` | 根句柄 | 真实目录或包资源 |
| `label` | `str` | 可读定位串 | 真实目录给绝对路径；包资源给 `package:<包名>` |
| `path` | `Path \| None` | 根的真实目录 | 包资源（可能在 zip 里）为 `None` |
| `writable` | `bool` | 能否往这个根写盘（如 lock） | 包资源为 `False` |

#### `AbilityOperation`（`container.operations[i]`）

| 字段/属性 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `container` | `str` | 所属包名 | 恒非空 |
| `key` | `str` | 成员键 = 工具名 | 恒非空 |
| `full_name` | `str` | `<包点分身份>.<成员键>`（`ghrah.fs.read_file`） | 恒非空 |
| `definition` | `OperationDefinition` | 定义面权威载荷，见下表 | 恒有 |
| `title` | `str` | 成员标题 | 定义面必填 |
| `description` | `str` | 成员说明（给模型看的用途） | 定义面必填 |
| `provider_slot` | `str` | 实现所在槽（`fs` / `core` / `shell` / `script`） | 包内脚本为 `script` |
| `handler` | `str` | 槽内动作名（运行期据此定位实现） | 恒非空 |
| `timeout_ms` | `int` | 单次调用超时上限 | 来自 `execution.timeout_ms`，缺省 60000 |
| `effects` | `tuple[str, ...]` | 声明的外部副作用 | 只读成员为空元组 |

`OperationDefinition`（`definition` 字段，`extra="forbid"`）：

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `title` / `description` | `str` | 标题与说明 | 必填非空 |
| `input_schema` | `dict` | 入参 JSON Schema（工具契约的唯一来源） | 缺省 `{type: object, properties: {}, additionalProperties: false}` |
| `output_schema` | `dict` | 成功返回的 JSON Schema | 缺省同上的空对象契约 |
| `errors` | `dict` | 错误码表 | 未声明为 `{}` |
| `implementation` | `OperationImplementation` | `{provider_slot, operation}` 路由 | 恒有 |
| `effects` | `list[str]` | 副作用标签 | 只读成员为 `[]` |
| `resources` | `dict` | 资源约束（内存/并发等），原样保留 | 未声明为 `{}` |
| `execution` | `ExecutionSpec` | `{mode, timeout_ms, cancellation, on_revoke, retry}` | 未声明时进入缺省值（见 §1.1 表下注） |
| `script` | `ContentResource \| None` | 包内脚本 `{path, media_type}` | provider 型成员为 `None` |
| `tags` | `list[str]` | 发现/过滤标签 | 未声明为 `[]` |
| `permissions` | `dict` | 权限**申请值**（`require_hitl` / `fs_write` / `net_access` …） | 未声明为 `{}` |
| `expose` | `bool` | 是否进模型工具面、是否可授予 | 缺省 `True`；`False` 用于内部轮次能力 |

`ExecutionSpec` 缺省值：`{mode: "request", timeout_ms: 60000, cancellation: "unsupported", on_revoke: "drain", retry: "never"}`。

#### `AbilitySkill`（`container.skills[i]`）

| 字段/属性 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `container` | `str` | 所属包名 | 恒非空 |
| `key` | `str` | 技能键 = `skills/<key>/` 目录名 | 恒非空 |
| `full_name` | `str` | `<包点分身份>.<技能键>`；键等于包名时收敛为包名 | 恒非空 |
| `title` | `str` | 目录显示标题 | 定义面缺省时为空串（捕获期会以正文 frontmatter 覆盖） |
| `description` | `str` | 一行说明 | 同上，可能为空串 |
| `media_type` | `str` | 正文媒体类型 | 通常 `text/markdown` |
| `body_path` | `str` | 定义面声明的正文相对路径（`skills/<key>/SKILL.md`） | 恒非空 |
| `body_locator` | `Traversable` | 正文定位器（已相对容器解析，**不保证存在**） | 存在性由扫描期报在 `issues` |
| `body` | `Path \| None` | 正文的真实路径 | 容器来自包资源（不在文件系统上）时为 `None` |
| `tags` | `tuple[str, ...]` | 技能标签 | 未声明为 `()` |
| `source` | `str` | 来源根标签 | 同 `container.root.source` |
| `rank` | `int` | 来源优先级 | 同 `container.root.rank` |
| `skill_dir` | `Path \| None` | 容器的真实目录 | 包资源根为 `None` |
| `lock_path` | `Path \| None` | `ability.lock.yaml` 路径 | `skill_dir` 为 `None` 时同样为 `None` |
| `fingerprint` | `SkillFingerprint \| None` | 入口哨兵 + 登记记录（扫描期一次读出） | 无真实目录时为 `None` |
| `lock_issue` | `str \| None` | 登记面不可用/不可裁决的原因 | 正常为 `None`；lock 坏掉或降级时非空 |
| `drifted` | `bool` | 入口哨兵自登记以来是否变过 | 未登记、或当前值读不出来时恒 `False` |
| `catalog_name` | `str` | 目录里显示/查找用的名字 | 单技能包为技能键（`research`）；多技能包为 `包/键`（`demo/alpha`） |
| `assets` / `references` / `scripts` | `tuple[SkillAttachment, ...]` | 声明过的三类附件，按键升序 | 未声明为 `()` |

`SkillAttachment`：

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `key` | `str` | 附件键（定义面的键，通常等于文件名主干） | 列表简写时取文件名主干 |
| `path` | `str` | 附件相对包根的 POSIX 路径（`skills/<key>/references/x.md`） | 恒非空 |
| `media_type` | `str` | 媒体类型 | 定义面声明；缺省按扩展名推断，未知按 `application/octet-stream` |

`SkillFingerprint`（`skill.fingerprint`）：

| 字段/属性 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `full_name` | `str` | 成员限定名 | 恒非空 |
| `skill_dir` / `lock_path` | `Path` | 容器目录与 lock 路径 | 恒有（`fingerprint` 非 `None` 时） |
| `current` | `str \| None` | 入口（定义面 + 正文）当前哨兵 `sha256-entry-v1:<hex>` | 入口文件读不出来时为 `None` |
| `recorded` | `str \| None` | 登记值 | 尚无 lock，或 lock 降级派生不出哨兵时为 `None` |
| `record` | `LockRecord \| None` | 整树登记记录 | 尚无 lock 时为 `None` |
| `lock_issue` | `str \| None` | 不可用/不可裁决原因 | 正常为 `None` |
| `drifted` | `bool` | `recorded != current`（两者都存在时） | 未登记为 `False`——"不知道"不报成"变了" |

#### `AbilityAsset`（`container.assets[i]`，旧格式遗留）

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `container` / `key` / `full_name` | `str` | 身份三件套 | 恒非空 |
| `definition` | `Any` | 定义面声明的资产内容（自由结构，原样持有） | 新包通常为空 |

#### `AbilityScanIssue`（`container.issues[i]`）

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `path` | `str` | 出错定位串（`<根>/<包>` 或更具体位置） | 恒非空 |
| `reason` | `str` | 可读原因 | 恒非空 |
| `container` | `str \| None` | 问题归属的包名 | 根级问题（根不存在）为 `None`，此类只出现在 `AbilityScanResult.issues` |

常见 `reason`：定义非法（解析错误原文）、`容器名 'x' 与目录名 'y' 不一致`、`声明的正文不存在：...`、同名副本被忽略。

### 1.2 `scan_ability_definitions(pool=None) -> AbilityScanResult`

`scan_abilities` 的底层形态：不想在聚合上丢信息（根级问题、扁平技能清单、登记面问题）时用它。

| 字段/属性 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `roots` | `tuple[AbilityRoot, ...]` | 本轮目录池（按优先级排序） | 恒非空（默认池至少含内置树） |
| `entries` | `tuple[AbilityEntry, ...]` | 命中的包，按容器名升序 | 一个都没扫到时为空元组 |
| `skills` | `tuple[AbilitySkill, ...]` | 全部技能成员（扁平），按全名升序 | 没有技能包时为空元组 |
| `issues` | `tuple[AbilityScanIssue, ...]` | 扫描期问题（含根级问题） | 健康环境为空元组 |
| `lock_issues` | `tuple[AbilityScanIssue, ...]` | 登记面问题：lock 读不出/降级 | 没有 lock 或未启用登记时为空；**不代表成员坏了** |
| `containers` | `tuple[AbilityContainer, ...]` | 与 `scan_abilities()` 同一份数据、同一套聚合 | 恒等于 `scan_abilities(pool)` |
| `definitions` | `Mapping[str, AbilityDefinition]` | `{包名: 定义}` | 与 `entries` 同源 |

`AbilityEntry` 只用于底层传递：`container`（包名）、`definition`、`root`、`locator`。

### 1.3 `load_skill_catalog(pool=None) -> SkillCatalog`

技能面：清单 + 目录文案 + 按需读正文。

```python
from ghrah.ability.loading.skill_context import load_skill_catalog

catalog = load_skill_catalog()
print(catalog.names)  # ('demo/alpha', 'research', ...)
print(catalog.render())  # <system-reminder> ... </system-reminder>
doc = catalog.read("demo/alpha")
```

| 字段/属性 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `roots` | `tuple[AbilityRoot, ...]` | 本轮目录池 | 恒非空 |
| `skills` | `tuple[AbilitySkill, ...]` | 全部技能成员，按全名升序 | 没有技能包时为空元组 |
| `issues` | `tuple[AbilityScanIssue, ...]` | 扫描期问题（收集式） | 健康环境为空元组 |
| `digest` | `str` | 目录摘要（只覆盖"名字 + 一行说明"） | 恒非空；正文变化不会改变它 |
| `names` | `tuple[str, ...]` | 目录名列表（`catalog_name` 顺序） | 与 `skills` 一一对应 |
| `render(changed=False)` | `str` | 注入模型的目录文本 | `skills` 为空时返回"当前无可用技能"的提示文本 |

`get(name) -> AbilitySkill | None`：

- 先按 `catalog_name` 精确匹配；再接受裸技能键、包名、点分全名；
- **二义时返回 `None`**（不同包里的同名技能不会替你猜）；
- 未知名字同样返回 `None`（不按名字猜路径）。

`read(name) -> SkillDocument`：按目录名读正文；未知名字抛 `SkillContextError`。

`SkillDocument`：

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `full_name` | `str` | 成员限定名 | 恒非空 |
| `name` | `str` | 目录名（成员键） | 恒非空 |
| `container` | `str` | 所属包名 | 恒非空 |
| `key` | `str` | 成员键 | 恒非空 |
| `path` | `str` | 正文位置的可读串 | 问题定位/日志用 |
| `media_type` | `str` | 正文媒体类型 | 通常 `text/markdown` |
| `content` | `str` | 正文全文（含 frontmatter，原样返回、不解释） | 恒非空（空文件会由扫描期报缺失/非法） |
| `source` | `str` | 来源根标签 | 同 `AbilitySkill.source` |
| `rank` | `int` | 来源优先级 | 同 `AbilitySkill.rank` |

## 2. 执行面

### 2.1 `load_all_builtin_manifests() -> Mapping[str, AbilityManifest]`

把**内置树**的实现面投影成运行时 view：键是成员全名（`ghrah.fs.read_file`），值是 `AbilityManifest`。

```python
from ghrah.ability.loading.ability_catalog import load_all_builtin_manifests

for full_name, manifest in load_all_builtin_manifests().items():
    print(full_name, manifest.metadata.title, manifest.tool.name if manifest.tool else "-")
    if manifest.tool is not None:
        tools.append(manifest.tool.to_openai_tool())
```

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `manifest` | `"ability"` | 固定标记 | 恒为 `"ability"` |
| `version` | `str` | 兼容版本（`"1"`） | 恒非空 |
| `metadata` | `AbilityMetadata` | 标题/说明/标签/权限，见下表 | 恒有 |
| `tool` | `ToolSchema \| None` | 模型工具绑定 | `expose: false` 的成员为 `None`（不出现）；registry-only 成员（`send`）整条不出现 |
| `implementation` | `ImplementationDef` | 实现落点 | 恒有 |
| `hooks` | `AbilityHooks` | pre/post/on_error 钩子声明 | 未声明时各为空列表 |
| `full_name` | `str`（属性） | `metadata.namespace + "." + metadata.name` | 与字典键一致 |

`AbilityMetadata`：

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `namespace` | `str` | 点分命名空间（`ghrah.fs`） | 恒非空 |
| `name` | `str` | 成员键 | 恒非空 |
| `title` | `str` | 标题 | 可能为空串（实现没声明 `TITLE` 时） |
| `description` | `str` | 说明 | 取实现模块的 `SUMMARY`；必填非空 |
| `tags` | `list[str]` | 标签 | 取 `TAGS`；未声明为 `[]` |
| `icon` / `since` | `str` | 展示/版本元数据 | 缺省空串 |
| `deprecated` | `bool` | 是否弃用 | 缺省 `False` |
| `permissions` | `PermissionFlags` | 权限声明 | 取实现模块的 `PERMISSIONS` |

`ToolSchema`（`manifest.tool`）：

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `name` | `str` | 工具名 = 成员键 | 恒非空 |
| `description` | `str` | 工具说明 | 恒非空 |
| `strict` | `bool` | 是否严格模式 | 缺省 `False` |
| `parameters` | `dict[str, ToolParameter]` | 参数表，键为参数名 | 无参成员为 `{}` |

`ToolParameter`：`{type, description, required, default, enum, minimum, maximum, items, sensitive}`。`type` 取 `string/integer/number/boolean/array/object`；`items` 用于数组元素；`sensitive` 标记敏感参数。

`to_openai_tool()` 的返回结构（可直接塞进 `tools=[...]`）：

```json
{
  "type": "function",
  "function": {
    "name": "read_file",
    "description": "读取文件内容",
    "strict": false,
    "parameters": {
      "type": "object",
      "properties": {"path": {"type": "string", "description": "文件路径"}},
      "required": ["path"]
    }
  }
}
```

`ImplementationDef`：

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `type` | `"builtin" \| "python" \| "sandbox" \| "wasm"` | 实现类型 | 本包内置投影恒为 `builtin` |
| `handler` | `str \| None` | 实现 handler 名（= 成员键） | `builtin` 下非空 |
| `entrypoint` / `source` / `module_ref` | `str \| None` | 其他实现类型的定位字段 | `builtin` 下为 `None` |

**异常**：内置树定义非法或实现脚本装载失败时直接抛 `ManifestValidationError`——内置面坏了是发布事故，不降级成问题条目。

### 2.2 按 handler 取类型 / 造实例

| 函数 | 返回 | 说明 | 取值场景 |
| --- | --- | --- | --- |
| `builtin_handlers() -> tuple[str, ...]` | 升序 handler 名 | 定义面声明的全部成员键，含 registry-only（`send`） | 恒非空 |
| `has_handler(handler) -> bool` | 是否已声明 | 先校验再实例化；每次重新扫盘 | 未知 handler 返回 `False` |
| `ability_class(handler) -> type[Ability]` | 实现类（不实例化） | 类型检查、测试断言用 | handler 未知、脚本装载失败或类不唯一时抛 `ManifestValidationError` |
| `create_ability(handler, **params) -> Ability` | 已构造实例 | 唯一实例化入口；`params` 原样传给实现构造函数（如 `permission_checker` / `command_checker` / `hooks`） | handler 未知抛 `ManifestValidationError`；参数不匹配抛 `TypeError` |

```python
from ghrah.ability.loading.ability_catalog import create_ability, ability_class

print(ability_class("read_file"))  # <class '...fs.operations.read_file.ReadFileAbility'>
ability = create_ability("read_file")  # 需要参数的实现按构造签名传
```

### 2.3 执行一个成员

实现侧契约（`Operation` 基类）：

| 成员 | 类型 | 说明 | 取值场景 |
| --- | --- | --- | --- |
| `name` | `str`（属性） | 能力名 / 成员键 | 恒非空 |
| `execute(context)` | `async -> ActionResult` | 执行动作 | 恒需实现 |
| `get_hooks()` | `-> list[Hook]` | 返回该能力注册的 hooks | 无钩子返回 `[]` |
| `bind_tool()` | `-> dict \| None` | 旧装配路径的 tool schema | 返回 `None` 表示不绑定；本包工具面**不读它**，以定义面 `input_schema` 为准 |

`AbilityExecutionContext` 的常用字段/兼容属性：

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `invocation` | `AbilityInvocation` | `{ability_name, tool_args, tool_call_id}` | 恒有 |
| `data` | `ExecutionData` | 执行期数据（累积数据等） | 恒有 |
| `services` | `ExecutionServices` | 宿主注入的服务（LLM、存储等） | 可能为空占位 |
| `agent_state` | `dict` | Agent 完整状态（只读快照） | 空 dict 表示未注入 |
| `last_action_result` | `ActionResult \| None` | 上一次动作结果 | 首轮为 `None` |
| `current_ability_name` / `tool_args` / `accumulated_data` | 兼容属性 | 读写 `invocation` / `data` 的旧口径 | 与上表同源 |

`ActionResult`：

| 字段 | 类型 | 含义 | 取值场景 |
| --- | --- | --- | --- |
| `outcome` | `ActionOutcome` | `success` / `failure` / `needs_input` / `delegate` | 必填 |
| `data` | `dict[str, Any]` | 结果数据（工具返回内容、错误信息等） | 缺省 `{}` |
| `next_action_hint` | `str \| None` | 建议的下一个 action | `None` 表示任务完成 |

```python
from ghrah.ability.loading.ability_catalog import create_ability
from ghrah.ability.execution.context import AbilityExecutionContext

ability = create_ability("read_file")
context = AbilityExecutionContext(current_ability_name="read_file", tool_args={"path": "a.txt"})
result = await ability.execute(context)
print(result.outcome, result.data)
```

## 3. 异常与环境

| 异常 | 出现场景 | 处理 |
| --- | --- | --- |
| `ManifestValidationError` | 定义非法、内置实现装载失败、handler 未知/类不唯一 | 修包或修实现；内置面问题按发布事故处理 |
| `SkillContextError` | `catalog.read(name)` 名字未知 | 先用 `catalog.names` / `catalog.get(name)` 校验 |
| `SkillLockError` | 读取单个技能的登记信息时 lock 损坏（如 `SkillAbilityProvider.get_skill`） | 用 `scan_abilities` 的 `lock_issues` 定位，或重建 lock |
| `TypeError` | `create_ability` 参数与构造签名不匹配 | 按实现构造函数传参 |

平台说明：

- 目录面不 import 实现脚本；内置树单次扫描约 6ms，用户池 12 个技能包约 +10ms。
- 执行面首次投影会 import 实现脚本，首次调用较慢；已导入模块按解释器规则复用，不热重载。
- 所有查询无缓存；不要在调用方加 `lru_cache`（会引入第二权威）。