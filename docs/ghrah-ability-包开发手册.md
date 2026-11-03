# ability 包开发手册

面向要为 ghrah 写能力包（ability package）的开发者。读完这一篇，你应该能：写一个能被扫出来、能被模型调用、能带技能正文的包，并知道每条规则为什么存在。

配套阅读：[调用说明文档](./ghrah-ability-调用说明文档.md)（宿主侧怎么调用本包）。

## 1. 一个 ability 包是什么

**一个目录 = 一个能力包**。目录里必须有 `ability.yaml`；其余全部可选。

```
reporting/
├── ability.yaml            # 定义面：手写区 + 生成区（必须有）
├── operations/             # 实现面：每个成员一个脚本（可选）
│   ├── __init__.py
│   ├── inspect.py
│   └── render.py
├── skill/                  # 技能目录 = 一个标准 skill 包（可选）
│   ├── SKILL.md            # 正文 + frontmatter（技能的唯一作者）
│   ├── references/         # 技能参考材料（可选）
│   │   └── style-guide.md
│   └── assets/             # 技能附件（可选，二进制放这里）
│       └── template.docx
└── ability.lock.yaml       # 安装期自动生成（勿手改）
```

`skill/` 就是**一个标准的 Anthropic skill 目录**：`SKILL.md` 在它根部，`references/`、`assets/` 是它的子目录——可以原样拿去给任何支持 skill 的宿主用。`ability.yaml` 里的 `members` 只是把这份结构摊平记录下来，**不改变磁盘组织**：加载仍按 `skill/` 目录走。

三条判定规则（全部是“存在即生效”，没有隐式默认）：

| 成员类别 | 什么时候出现 |
| --- | --- |
| `operations` | 包内有 `operations/*.py` |
| `skill` | 包内有 `skill/SKILL.md`（历史写法 `skills/SKILL.md` 与定义面声明的别名路径也认） |
| `references` / `assets` | **技能目录下**存在 `references/`、`assets/` 文件夹 |

技能面**以 `SKILL.md` 为准**，`ability.yaml` 只是索引：盘上有正文而定义面没声明，扫描会照常把技能给模型（并在 `issues` 里提示重建）；定义面声明的正文路径不存在时，按盘上那份修正。

没有 `SKILL.md` 就没有技能，也就没有附件归属——此时 `references/`、`assets/` 只当普通子目录，不报错、不进生成区。

## 2. ability.yaml：两段式

```yaml
# ── 作者手写，工具不改 ──
format: ghrah.ability-definition
schema_version: '1'
identity: {namespace: acme, name: reporting, version: '1.4.0'}
title: Reporting
description: 生成周报与月报，按模板渲染。

# ── 以下由 `ghrah ability build` 生成，勿手改 ──
members:
  operations:
    inspect:
      title: Inspect
      description: 检查数据源是否可用
      entry: operations/inspect.py
      effects: []
    render:
      title: Render
      description: 按模板渲染报告
      entry: operations/render.py
      effects: [filesystem:write]
  skill: {body: skill/SKILL.md, media_type: text/markdown}
  references:
  - {path: skill/references/style-guide.md, media_type: text/markdown, digest: 'sha256:...'}
  assets:
  - {path: skill/assets/template.docx, media_type: application/vnd...}
```

### 手写区字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `format` | 是 | 固定 `ghrah.ability-definition` |
| `schema_version` | 是 | 固定 `'1'`（务必带引号；未加引号会被 YAML 读成整数 1，也认，但别再依赖） |
| `identity.namespace` | 是 | 命名空间，`^[a-z][a-z0-9_-]*$` |
| `identity.name` | 否 | 容器名；省略时取 namespace。**必须等于目录名** |
| `identity.version` | 是 | 包版本（字符串） |
| `title` | 是 | 一行标题（给人看） |
| `description` | 是 | 一段说明（给人看） |
| `provider_requirements` | 否 | 只有“实现由运行期后端提供”的成员才需要，见 §3.3 |

### 生成区写法

生成区**不要手改**，用工具重建：

```sh
python -m ghrah.ability.build path/to/reporting            # 写回
python -m ghrah.ability.build path/to/reporting --dry-run  # 只看会写什么
python -m ghrah.ability.build path/to/reporting --check    # 未同步时退出码 1（CI 用）
python -m ghrah.ability.build path/to/reporting --migrate  # 旧格式（0.1-draft / 0.2）重写成 v1
```

**扫描口径是磁盘，不是 `ability.yaml`**：目录里有 `SKILL.md` 或 `operations/*.py` 就会被扫到。定义面缺失或还是旧格式时不会被丢掉——扫描会按目录与正文合成一份**只读视图**（成员照常出现在目录面，容器标 `synthesized`，并在 `issues` 里说明），补定义用上面的 `build`（缺文件加 `bootstrap`、旧格式加 `--migrate`），或让安装期 `initialize()` 顺手补齐。

安装或首次扫描时也会自动补齐（与 `ability.lock.yaml` 同一个生命周期、同一个落点）：

```python
from ghrah.ability import SkillAbilityProvider

SkillAbilityProvider.from_root("path/to/skills").initialize()
```

生成规则：扫目录 → 只重算“扫得出来”的部分 → 写回时保留扫不出来的作者意图（`provider` 型成员、`expose: false`、非容器名的技能键、别名正文路径）。

### 生成区内允许手写的两个字段

| 字段 | 说明 |
| --- | --- |
| `expose: false` | 该成员**不可授予 / 不发布 / 不可被选为工具**（离线可判定）。它不决定模型工具面。 |
| `skill.key` | 技能成员键；省略时取容器名。只有在包内历史上已用别的键（成员地址变了会破坏既有授权）时才写。 |

## 3. operations：可执行成员

### 3.1 文件约定

`operations/<成员键>.py`，成员键 = 文件名主干（`inspect.py` → `inspect`）。一个脚本**恰好一个** `Ability` 子类。

```python
# operations/inspect.py
from ghrah.ability import Operation
from ghrah.types.results import ActionResult, ActionOutcome

SUMMARY = "检查数据源是否可用"  # 目录里的一行说明（必填）
TAGS = ["reporting", "read"]  # 发现/过滤用（必填）
PERMISSIONS = {"fs_read_only": True}  # 安全声明（必填）
TITLE = "Inspect"  # 可选，缺省由成员键生成
EFFECTS = []  # 可选，副作用标签（如 filesystem:write）


class InspectAbility(Operation):
    @property
    def name(self) -> str:
        return "inspect"

    def bind_tool(self) -> dict | None:
        return {
            "type": "function",
            "function": {
                "name": "inspect",
                "description": "检查数据源是否可用",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }

    async def execute(self, context) -> ActionResult:
        return ActionResult(outcome=ActionOutcome.SUCCESS, data={"ok": True})
```

### 3.2 模块级声明（定义面不再重复这些事实）

| 常量 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `PERMISSIONS` | dict | 是 | 安全声明，见下表 |
| `SUMMARY` | str | 是 | 目录摘要文案 |
| `TAGS` | list[str] | 是 | 标签 |
| `TITLE` | str | 否 | 成员标题，缺省由成员键生成 |
| `EFFECTS` | list[str] | 否 | 副作用标签，进生成区 |

`PERMISSIONS` 可用键：`require_hitl` / `fs_read_only` / `fs_write` / `net_access` / `shell_access` / `allowed_paths` / `denied_paths` / `allowed_commands` / `denied_commands`。

### 3.3 工具契约只有一个来源：`bind_tool()`

- 模型看到的工具名、说明、参数，**全部**取 `bind_tool()` 的返回值；
- 返回 `None` 表示“这个成员不给模型调用”（如只做一次不带工具的用户轮次）；
- 定义面不写 `input_schema`，工具面因此不可能与实现对不上。

### 3.4 由运行期后端实现的成员

如果成员不是包内脚本，而是运行时提供的后端（例如内置文件后端的 `read_file`），在生成区写 `provider` 而不是 `entry`，并在手写区声明契约：

```yaml
provider_requirements:
  fs_backend: {contract: ghrah.fs.v1, required_capabilities: [request]}

members:
  operations:
    read_file: {title: Read File, description: ..., provider: fs_backend, effects: []}
```

规则：

- `entry` 与 `provider` 至少写一个；都写时 `provider` 决定槽位、`entry` 决定脚本位置（脚本后端）。
- 写了 `provider` 就必须在 `provider_requirements` 里声明同名槽，否则解析期直接报错。
- 这类成员扫不出来，`build` 重建时会原样保留。

## 4. SKILL.md：技能正文

技能正文放在**能力包内的 `skill/` 目录**里，该目录本身就是一个标准 skill 包；正文按 **Anthropic 的 Agent Skill 规范**写（ghrah 沿用同一套 frontmatter 约定）：

```
reporting/skill/
├── SKILL.md
├── references/style-guide.md
└── assets/template.docx
```

```markdown
---
name: reporting-guide
description: 使用获准模板和写作指南整理周报与月报，包含排版与用语规范。
---

# 报告编写

1. 读取 `assets/template.docx` 获取模板。
2. 按 `references/style-guide.md` 的用语与排版规范生成正文。
3. 只使用用户提供的数字，不补造数据。
```

### frontmatter 字段

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `name` | 是 | 技能名。小写字母、数字、连字符；建议 ≤ 64 字符；建议与包名一致 |
| `description` | 是 | 必填且非空。说明**做什么 + 什么时候用**；第三人称；建议 ≤ 1024 字符 |

两个字段是技能的**唯一作者**：`ability.yaml` 不抄第二份，扫描/捕获时读正文得到。缺了或不合法，捕获期直接失败（`SkillParseError`）。

### 正文建议

- 只写“怎么做”，不要复述定义面里已有的东西；
- 保持精炼（建议 500 行以内），长材料放 `references/`，按需读取；
- 正文里用相对链接引用附件（`[风格](references/style-guide.md)`）；**未登记在生成区里的本地链接会被拒绝**——先 `build` 再链接。

### 附件

附件放在**技能目录内部**（`skill/references/`、`skill/assets/`），跟随技能正文，不能跨到包根：

| 目录 | 用途 | 内容 |
| --- | --- | --- |
| `skill/references/` | 参考材料（风格指南、schema、示例） | 文本为主 |
| `skill/assets/` | 交付物模板与二进制 | docx/xlsx/图片等 |

生成区会为每个文件记录 `path` / `media_type` / `digest`（路径相对**包根**，即 `skill/references/...`）；成员键取文件名主干（`style-guide.md` → `style-guide`），同名必须改名。

## 5. 三条面怎么读你的包

| 面 | 读什么 | 你的包要满足 |
| --- | --- | --- |
| 目录面（给模型注入上下文/索引） | 目录结构 + `ability.yaml` + `SKILL.md` frontmatter | 容器名 = 目录名；技能正文存在且 frontmatter 合法 |
| 实现面（tool call 执行） | `operations/*.py`：类 + 模块级常量 + `bind_tool()` | 一个脚本恰好一个 `Ability` 子类；三个模块级常量齐全 |
| 内容/授权面（内容寻址与授予） | 定义面声明的成员 → 逐字节捕获 | 声明的文件必须存在且都在包内；摘要要与声明一致 |

## 6. 教程：从零写一个 reporting 包

```sh
mkdir -p reporting/operations reporting/skill/references reporting/skill/assets
```

**第 1 步 · 手写区**

```yaml
# reporting/ability.yaml
format: ghrah.ability-definition
schema_version: '1'
identity: {namespace: acme, name: reporting, version: '1.0.0'}
title: Reporting
description: 生成周报与月报，按模板渲染。
```

**第 2 步 · 一个成员**

写 `reporting/operations/inspect.py`：照 §3.1 的模板，`SUMMARY`、`TAGS`、`PERMISSIONS` 三个常量 + `bind_tool()` + `execute()`。

**第 3 步 · 技能正文与附件**

- `reporting/skill/SKILL.md`：frontmatter 的 `name` / `description` + 正文；
- `reporting/skill/references/style-guide.md`、`reporting/skill/assets/template.docx`（随便放点内容）。

**第 4 步 · 生成**

```sh
python -m ghrah.ability.build reporting
```

看到 `...ability.yaml: 已更新` 即可。再跑一次应输出“无变化”（幂等）。

**第 5 步 · 验证**

```sh
python ghrah-core/examples/ability_debug/01_catalog.py    # 能列出 reporting 与其成员
python ghrah-core/examples/ability_debug/02_build.py      # 生成区与磁盘一致
python ghrah-core/examples/ability_debug/04_content.py    # 能封成版本、清单里能读到附件
```

## 7. 校验与常见错误

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `不支持的 schema_version ...` | 写了 `0.1-draft` / `0.2` | 迁到 `'1'` 两段式 |
| `容器名 'x' 与目录名 'y' 不一致` | `identity.name` 与目录名不同 | 改成一致，或省略 `name` 用 namespace |
| `成员必须声明 entry 或 provider 至少一项` | 生成区成员缺实现落点 | 补 `entry`，或改成 `provider` 并声明槽 |
| `provider 'x' 未在 provider_requirements 声明` | 用了后端槽但没声明契约 | 手写区补 `provider_requirements` |
| `frontmatter 必须包含非空的 description` | SKILL.md 头部缺字段 | 补 `name` / `description` |
| `Skill 链接未登记: ...` | 正文里的相对链接指向未声明文件 | 先 `build`，让文件进生成区 |
| `members.references / assets 必须伴随 members.skill` | 定义面声明了附件但没有技能正文 | 加 `SKILL.md`，或去掉附件条目 |
| `内容摘要不符: ...` | 声明的 `digest` 与文件实际内容不一致 | 内容变了就重建生成区 |

## 8. 版本与兼容

- 当前定义格式：`schema_version: '1'`（`members` 两段式）。旧格式（`0.1-draft` / `0.2`）**不再被解析**——但不会被丢包：目录面按 `SKILL.md` / `operations` 合成只读视图，成员照常可见。
- 迁移办法：`python -m ghrah.ability.build <包目录> --migrate`。身份、标题、说明从旧文件搬过去，`members` 按磁盘重建；原来手写的 `input_schema` 由实现的 `bind_tool()` 替代。
- 没有 `ability.yaml` 的标准 skill 目录同样能被扫到；补齐用 `--bootstrap`（或 `SkillAbilityProvider.initialize()`），容器名取目录名、说明取正文 frontmatter。
- `ability.lock.yaml` 由框架维护（整树指纹 + 入口哨兵），随包分发但不要手改；要承认新内容用 `SkillAbilityProvider.relock(name)`。