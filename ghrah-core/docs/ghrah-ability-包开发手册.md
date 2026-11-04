# ability 包开发手册

面向要为 ghrah 写能力包（ability package）的开发者。读完这一篇，你应该能：写一个能被扫出来、能被模型调用、能带**多个技能**的包，并知道每条规则为什么存在。

配套阅读：[调用说明文档](./ghrah-ability-调用说明文档.md)（宿主侧怎么调用本包）。

## 1. 一个 ability 包是什么

**一个目录 = 一个能力包**。目录里必须有 `ability.yaml`；其余全部可选。

```
reporting/
├── ability.yaml                    # 定义面：头段手写 + members（成员清单）
├── operations/                     # 实现面：每个成员一个脚本（可选）
│   ├── __init__.py
│   ├── inspect.py
│   └── render.py
└── skills/                         # 技能面：每个技能一个目录（可选，可多个）
    ├── reporting-guide/
    │   ├── SKILL.md                # 正文 + frontmatter（技能名与说明的作者）
    │   ├── references/             # 参考材料（可选）
    │   └── assets/                 # 附件（可选，二进制放这里）
    └── style-guide/
        └── SKILL.md
```

三条硬规则：

| 规则 | 说明 |
| --- | --- |
| 包名 = 目录名 | `identity.name`（省略时取 `namespace`）必须等于包目录名 |
| 技能名 = 目录名 | `members.skills.<name>` 的键必须等于 `skills/<name>/` 的目录名 |
| 一个能力包可以有多个技能 | 每个技能独占一个目录；键不同即可，不再限制为 1 个 |

`members` 是**成员索引**：它把磁盘结构记录成一份可被外部消费的清单（schema、标签、权限、执行约束、附件路径），但加载仍以磁盘为准。当前还没有生成器，`members` 由作者手写；将来由构建工具重算"扫得出来"的部分。

## 2. ability.yaml：头段 + members

```yaml
# ── 头段：作者手写 ──
format: ghrah.ability-definition
schema_version: '1'
identity: {namespace: acme, name: reporting, version: '1.4.0'}
title: Reporting
description: 生成周报与月报，按模板渲染。

# ── members：成员清单（当前手写；将来由构建工具生成）──
members:
  operations:
    inspect:
      title: Inspect
      description: 检查数据源是否可用
      entry: operations/inspect.py
      input_schema:
        type: object
        properties: {source: {type: string}}
        required: [source]
        additionalProperties: false
      output_schema:
        type: object
        properties: {ok: {type: boolean}}
        required: [ok]
        additionalProperties: false
      tags: [reporting, read]
      effects: []
      permissions: {fs_read_only: true}
      execution: {mode: request, timeout_ms: 60000,
                  cancellation: unsupported, on_revoke: drain, retry: never}
      expose: true
    render:
      title: Render
      description: 按模板渲染报告
      entry: operations/render.py
      effects: [filesystem:write]
  skills:
    reporting-guide:
      title: 报告编写
      description: 使用获准模板和写作指南整理周报与月报。
      tags: [reporting, writing]
      body: {path: skills/reporting-guide/SKILL.md, media_type: text/markdown}
      assets:
        template: {path: skills/reporting-guide/assets/template.docx,
                   media_type: application/vnd.openxmlformats-officedocument.wordprocessingml.document}
      references:
        style-guide: {path: skills/reporting-guide/references/style-guide.md,
                      media_type: text/markdown}
      scripts: {}
    style-guide:
      title: 写作规范
      description: 报告用语与排版规范。
      body: {path: skills/style-guide/SKILL.md, media_type: text/markdown}
```

### 头段字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `format` | 是 | 固定 `ghrah.ability-definition` |
| `schema_version` | 是 | 固定 `'1'`（务必带引号；未加引号会被 YAML 读成整数 1，解析器也认，但别再依赖） |
| `identity.namespace` | 是 | 命名空间，`^[a-z][a-z0-9_-]*$` |
| `identity.name` | 否 | 容器名；省略时取 namespace。**必须等于目录名**，允许连字符 |
| `identity.version` | 是 | 包版本（字符串，`MAJOR.MINOR.PATCH`） |
| `title` | 是 | 一行标题（给人看） |
| `description` | 是 | 一段说明（给人看） |
| `origin` | 否 | `source`（缺省，包内脚本/技能）或 `mcp`（`tools/list` 生成，成员来自 MCP 服务） |
| `mcp` | 否 | `origin: mcp` 时的连接声明 `{server: <agentconf 名>}`；**不写 URL 与密钥** |
| `provider_requirements` | 否 | 只有"实现由运行期后端提供"的成员才需要，见 §4.5 |

### members.operations 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `title` / `description` | 是 | 目录里给人看的标题与说明 |
| `entry` | 与 `provider` 二选一 | 包内脚本相对路径，如 `operations/inspect.py` |
| `provider` | 与 `entry` 二选一 | 运行期后端槽名（如 `fs_backend`） |
| `input_schema` / `output_schema` | 否 | JSON Schema；缺省是"空对象、拒绝未知字段" |
| `errors` | 否 | 错误码表，原样保留 |
| `tags` | 否 | 发现/过滤标签 |
| `effects` | 否 | 副作用标签（如 `filesystem:write`、`net:request`） |
| `permissions` | 否 | 权限**申请值**（`require_hitl` / `fs_read_only` / `fs_write` / `net_access` / `shell_access` / `allowed_paths` / `denied_paths` / `allowed_commands` / `denied_commands`） |
| `resources` | 否 | 资源约束（内存、并发等），原样保留 |
| `execution` | 否 | 执行约束；缺省 `{mode: request, timeout_ms: 60000, cancellation: unsupported, on_revoke: drain, retry: never}` |
| `expose` | 否 | 缺省 `true`；`false` 表示该成员不进入模型工具面、也不可授予（如内部轮次能力） |

### members.skills 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `title` / `description` | 建议 | 技能目录里显示的名字与一行说明；捕获期以正文 frontmatter 为准覆盖 |
| `body` | 是 | `{path, media_type}`；`path` 相对包根，典型 `skills/<name>/SKILL.md` |
| `tags` | 否 | 技能标签（与 frontmatter `metadata.tags` 同源） |
| `assets` | 否 | `{键: {path, media_type, expected_digest?}}` |
| `references` | 否 | 同上，长材料放这里 |
| `scripts` | 否 | 技能委托执行的脚本（`scripts/*.py`）；**不是 operation**，不生成 schema、不可读为正文 |
| `annotations` | 否 | 作者自定义键（如 `source_book`），原样保留、框架不解释 |

## 3. 技能：SKILL.md

### 3.1 目录与 frontmatter

```
reporting/skills/reporting-guide/
├── SKILL.md
├── references/style-guide.md
├── assets/template.docx
└── scripts/render.py
```

```markdown
---
name: reporting-guide
description: 使用获准模板和写作指南整理周报与月报，包含排版与用语规范。
metadata:
  title: 报告编写
  tags: [reporting, writing]
---

# 报告编写

1. 读取 `assets/template.docx` 获取模板。
2. 按 `references/style-guide.md` 的用语与排版规范生成正文。
3. 只使用用户提供的数字，不补造数据。
```

| frontmatter 字段 | 必填 | 约束 |
| --- | --- | --- |
| `name` | 是 | 必须等于技能目录名；`^[a-z0-9]+(?:-[a-z0-9]+)*$` |
| `description` | 是 | 非空；说明**做什么 + 什么时候用**；建议第三人称、≤1024 字符 |
| `metadata.title` | 否 | 目录显示标题；缺省取 `name` |
| `metadata.tags` | 否 | 标签列表；捕获期会并入成员 `tags` |

`name` / `description` 是技能的**唯一作者**：捕获期读正文，覆盖 `ability.yaml` 里可能过期的副本。正文必须是 **UTF-8 无 BOM、LF 换行**，且以 `---` 开头（v1 / 0.2 包缺 frontmatter 会直接失败）。

### 3.2 正文与附件

- 只写"怎么做"，不要复述 `ability.yaml` 里已有的事实；
- 长材料放 `references/`，正文里用**相对链接**引用（`[规范](references/style-guide.md)`）；
- 二进制与模板放 `assets/`；可执行脚本放 `scripts/`，由操作在需要时调用；
- 附件只跟随**所属技能目录**：`skills/<name>/references/...`、`skills/<name>/assets/...`，不能跨到包根或其他技能目录；
- 正文里引用的本地链接必须已声明（`references` / `assets`），否则捕获期报"链接未登记/链接指向不存在"。

### 3.3 多个技能怎么分

- **按"读者什么时候需要它"拆**：一个技能 = 一份独立的操作规程；同一套包里的不同技能不互相依赖正文；
- 技能名用短横线小写（`reporting-guide`），不要用下划线；
- 技能目录之间不能嵌套、不能共享 `references/`——需要共享就把公共材料各自复制或抽成单独技能；
- 一个包只放**一个**技能当然也合法；那种情况下目录名与包名相同最省事。

## 4. operations：可执行成员

### 4.1 文件约定

`operations/<成员键>.py`，成员键 = 文件名主干（`inspect.py` → `inspect`）。一个脚本**恰好一个** `Operation` 子类；组内共享逻辑放 `operations/_xxx.py`（`_` 前缀是辅助模块，不算成员）。

```python
# operations/inspect.py
from ghrah.ability import Operation
from ghrah.types.results import ActionResult, ActionOutcome

TITLE = "Inspect"  # 可选，缺省由成员键生成
SUMMARY = "检查数据源是否可用"  # 目录里的一行说明（实现面必填）
TAGS = ["reporting", "read"]  # 发现/过滤
PERMISSIONS = {"fs_read_only": True}  # 安全声明
EFFECTS = []  # 副作用标签


class InspectOperation(Operation):
    @property
    def name(self) -> str:
        return "inspect"

    async def execute(self, context) -> ActionResult:
        return ActionResult(outcome=ActionOutcome.SUCCESS, data={"ok": True})
```

### 4.2 模块级声明

| 常量 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `SUMMARY` | str | 是 | 目录摘要文案（缺了实现面投影直接报错） |
| `PERMISSIONS` | dict | 是 | 安全声明，运行期 view 的权限来源 |
| `TAGS` | list[str] | 是 | 标签 |
| `TITLE` | str | 否 | 成员标题，缺省由成员键生成 |
| `EFFECTS` | list[str] | 否 | 副作用标签 |

运行期 view（`AbilityManifest`）的说明取 `SUMMARY`、标签取 `TAGS`、权限取 `PERMISSIONS`；**工具参数 schema 取 `members.operations.<键>.input_schema`**——定义面是工具契约的唯一来源，实现脚本不再重复一份参数表（`bind_tool()` 仍可用于旧装配路径，但不是本包的权威）。

### 4.3 由运行期后端实现的成员

不是包内脚本、而是运行时提供的后端（如文件后端的 `read_file`）：

```yaml
provider_requirements:
  fs_backend: {contract: ghrah.fs.v1, required_capabilities: [request]}

members:
  operations:
    read_file: {title: Read File, description: ..., provider: fs_backend, effects: []}
```

- `entry` 与 `provider` 至少写一个；都写时 `provider` 决定槽位、`entry` 决定脚本位置；
- 写了 `provider` 就必须在 `provider_requirements` 里声明同名槽（v1 会跳过这条校验，因为 v1 头段不写 `provider_requirements`——缺后端在运行期报错，不在解析期冒充契约错误）；
- 这类成员扫不出来，将来构建工具重建时原样保留。

### 4.4 暴露面

`expose: false` 的成员不生成工具、不可授予；它只用于框架内部轮次（如 `conversation`）。这是"是否进模型工具面"的**唯一开关**，实现脚本里不要再放同名开关。

## 5. 扫描器怎么读你的包

一次 `scan_abilities()` 的输出按三层组织，**每层都带归属与问题**：

| 层 | 类型 | 你能读到的字段 |
| --- | --- | --- |
| 包 | `AbilityContainer` | `name` / `full_name` / `version` / `title` / `description` / `operation_count` / `skill_count` / `kinds` / `root` / 原始 `definition` / `issues` |
| 操作 | `AbilityOperation` | `container` / `key` / `full_name` / 原始 `OperationDefinition`（schema、tags、effects、permissions、resources、execution、expose） |
| 技能 | `AbilitySkill` | `container` / `key` / `full_name` / `catalog_name` / `title` / `description` / `media_type` / `body_path` / `tags` / `assets` / `references` / `scripts`（`SkillAttachment{key,path,media_type}`） |

命名规则：

- **单技能包**：技能目录名就是模型看到的名字（`research`），不额外加前缀；
- **多技能包**：`catalog_name` 为 `包名/技能名`（`demo/alpha`），否则不同包里的同名技能无法区分；
- `full_name` 恒为点分身份 `namespace.name.skill`（键等于包名时收敛为包名），只用于去重与定位。

问题清单（`issues`）覆盖：根不存在、定义非法、容器名与目录名不一致、正文不存在、同名容器被更高优先级副本覆盖等。**不吞错、不自动改盘**。

## 6. 从零写一个 reporting 包

```sh
mkdir -p reporting/operations \
         reporting/skills/reporting-guide/references \
         reporting/skills/reporting-guide/assets
```

**第 1 步 · 头段**

```yaml
# reporting/ability.yaml
format: ghrah.ability-definition
schema_version: '1'
identity: {namespace: acme, name: reporting, version: '1.0.0'}
title: Reporting
description: 生成周报与月报，按模板渲染。
```

**第 2 步 · 成员与实现**

写 `reporting/operations/inspect.py`（照 §4.1），在 `members.operations.inspect` 里声明 `entry`、两个 schema、`tags`、`permissions`。

**第 3 步 · 技能正文与附件**

写 `reporting/skills/reporting-guide/SKILL.md`（frontmatter 的 `name` 必须等于 `reporting-guide`），再放 `references/style-guide.md` 与 `assets/template.docx`，并在 `members.skills.reporting-guide` 里登记这三个文件。

**第 4 步 · 验证**

```sh
uv run python ghrah-core/examples/ability_debug/01_scan_abilities.py
```

能列出 `reporting`、`operations=1 skills=1`、正文路径与附件路径，且 `issues` 为空，即通过。

**第 5 步 · 封版本（可选，发布前）**

```python
from pathlib import Path
from ghrah.ability.versions import capture_ability_version

root = Path("reporting")
version = capture_ability_version((root / "ability.yaml").read_text(), root=root)
print(version.digest, sorted(__import__("json").loads(version.manifest_json)))
```

## 7. 校验与常见错误

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `不支持的 schema_version 'x'` | 版本不是 `0.1-draft` / `0.2` / `'1'` | 新包支持 `0.1-draft`、`0.2` / `'1'` |
| `容器名 'x' 与目录名 'y' 不一致` | `identity.name` 与目录名不同 | 改成一致，或省略 `name` 用 namespace |
| `members.operations.<键>: 未知字段 [...]` | 写了 v1 不认识的成员字段 | 只写 §2 表里列出的字段（契约拒绝未知字段） |
| `members.skills.<键> 缺少 body` | 技能没声明正文路径 | 补 `body: {path, media_type}` |
| `非法成员键` | 键不是小写字母/数字/下划线/连字符，或首字符非字母 | 改键名 |
| `提供的正文不存在: ...` | `body.path` 指向的文件不在包内 | 补文件或改路径 |
| `SKILL.md 必须包含 name 和 description` | frontmatter 缺字段 | 补 `name`（= 目录名）与 `description` |
| `标准 SKILL.md 缺少 frontmatter` | v1 / 0.2 包的正文没有 `---` 头 | 加 frontmatter |
| `内容摘要不符: ...` | 声明了 `expected_digest` 但内容变了 | 更新声明，或去掉 `expected_digest` |
| `包内路径越界: ...` | 路径含 `..` / 绝对路径 / 符号链接 | 只用包内相对路径 |

## 8. 版本与兼容

- 当前格式：`schema_version: '1'`（`members` 两段式，多技能）。**新包一律用它。**
- 旧格式（`0.1-draft` / `0.2`）仍被兼容解析：`skills` 在顶层、正文固定 `skills/SKILL.md`、只允许一个技能。兼容是为了让存量包继续工作，不是新写法。
- 存量包迁移：`uv run python scripts/migrate_skill_pool_v1.py <技能池> --apply`（默认 dry-run；动盘前整池备份到 `.backup-v1-<时间戳>/`）。脚本会搬正文到 `skills/<name>/SKILL.md`、补 frontmatter、重写 `ability.yaml`，并删除过期的 `ability.lock.yaml`。
- `ability.lock.yaml` 由框架维护（整树指纹 + 入口哨兵），**源码包里不手写**；要承认新内容用 `SkillAbilityProvider.relock(name)`。
- 还没有 `ghrah ability build` 生成器：`members` 目前手写。生成器落地后，规则是"只重算扫得出来的部分，保留作者意图（`provider` 成员、`expose: false`、别名正文路径）"。

## 相关文档

- [调用说明文档](./ghrah-ability-调用说明文档.md) — 宿主侧怎么扫描、捕获、授权、读取
- [内置 Ability 参考](./builtin-abilities.md) — 内置包的成员清单