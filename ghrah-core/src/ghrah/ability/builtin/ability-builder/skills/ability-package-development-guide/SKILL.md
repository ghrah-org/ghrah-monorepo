---
name: ability-package-development-guide
description: 定义一个 Ability 包的标准结构、手写 ability.yaml 字段，以及 Operation 与 Skill 的声明方式。
metadata:
  title: Ability 包开发指南
  tags: [ability, guide, package]
---

# Ability 包开发指南

> Ability 包是 九夏/ghrah 最常用和泛用的扩展点，可以在不改动内核的任何源码的情况下扩展系统的功能
> Ability 包是 对于当前智能体的常用扩展点的统一收纳，支持通过MCP、Skill、Tool来拓展智能体的行为边界
> 统一定义扩展的目的之一是为了统一的管理权限，Ability包组的安装，启用，可见，授权，执行是完全分离的
> 最细的粒度可以控制到哪一个Agent可以看到和使用哪一个具体的脚本或者进行什么具体的行为
> 本文定义一个 Ability 包的目录结构、开发者的开发范式、如何声明 Operation 与 Skill。本文本身也是Skill形式，可供Agent参考。
> 本说明文档部分由九夏生成

## 术语

| 术语 | 定义 | 说明 |
| --- | --- | --- |
| **Ability** | 能力包组，一种九夏的统一化智能体行为边界拓展包 | 本文中与“Ability 包”“能力包组”同义；一个 Ability 是一个目录，包含 `ability.yaml`，可以带多个 Operation 与多个 Skill |
| **Operation** | 可以绑定为工具调用的一个具体 `.py` 入口 | 一个 Operation 对应一个工具调用；声明与实现都在 `operations/<键>.py` |
| **Skill** | 标准术语；本指南以 Claude Skill 标准为准 | 以 `SKILL.md` 为正文，可带 `references/`、`assets/`、`scripts/`；属于内容型能力，不是 Operation |
| **MCP** | 标准术语；Model Context Protocol | 本指南以 2026-07-28 版 MCP 规范为基准；MCP 连接包从服务器的 `tools/list` 生成 Operation |

## 1. 一个包是什么

创建一个目录以开始创建一个 Ability 包。**这个目录名本身是任意的，与产物的分发与安装无关**；身份只来自 `ability.yaml` 的 `identity`。

根据接入的对象不同，有两种包形态：

| 形态 | 目录内容 | 用途 |
| --- | --- | --- |
| 源码包 | `ability.yaml` + `operations/` + `skills/` | 进程内工具与 Skill 内容 |
| MCP 连接包 | 只有 `ability.yaml` | 从 MCP 服务器的 `tools/list` 生成工具 |

Ability 是包的单位；Operation 是可执行成员；Skill 是内容成员；MCP 是外部工具来源。一个 Ability 可以只包含 Operation、只包含 Skill，或两者都有；MCP 连接包是只有工具成员的 Ability。
两种形态都必须至少有一个成员（Operation 或 Skill），不能是空包。

## 2. 源码包目录结构

```text
reporting/
  ability.yaml
  operations/
    render.py
    _shared.py          # 可选：以下划线开头，辅助模块，不是成员
    __init__.py         # 可选：需要 Python 包语义时使用，不是成员
  skills/
    reporting-guide/
      SKILL.md
      references/
      assets/
      scripts/
```

规则：

- `operations/` 只放顶层 `*.py`；每个 `<键>.py` 是一个 Operation。当前不允许子目录。
- `operations/` 下以下划线开头的`*.py`会被视为辅助模块，不会被加载为 Operation。
- `skills/` 下每个 `<name>/` 是一个 Skill；目录名必须等于 `SKILL.md` frontmatter 的 `name`。
- `skills/<name>/` 只允许 `SKILL.md`、`references/`、`assets/`、`scripts/`；其他条目会被拒绝（常见缓存文件忽略）。
- `operations` 与 `skills` 需要至少有一个不为空。
- **Ability包不支持嵌套Ability包**

## 3. `ability.yaml` 的手写字段

定义ability包的元数据，源码包的最小头段：

```yaml
format: ghrah.ability-definition
schema_version: '1'
identity:
  namespace: acme
  name: reporting
  version: '1.4.0'
title: Reporting
description: 生成周报与月报，按模板渲染。
```

| 字段 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- |
| `format` | 是 | `ghrah.ability-definition` | 固定值 |
| `schema_version` | 是 | 字符串 `'1'` | 必须带引号；YAML 的整数 `1` 不是 `'1'` |
| `identity.namespace` | 是 | `^[a-z][a-z0-9_-]*$` | 身份第一段；`ghrah` 与 `mcp` 保留 |
| `identity.name` | 否 | `^[a-z][a-z0-9_-]*$` | 源码包可省略；省略时全名退化为 `namespace` |
| `identity.version` | 是 | `MAJOR.MINOR.PATCH` | 三段十进制，无前导零、无 `v`或者`a`后缀 |
| `title` | 是 | 非空字符串 | 包标题 |
| `description` | 是 | 非空字符串 | 包说明，给目录与安装界面看 |
| `origin` | 否 | `source` / `mcp` | 缺省 `source` |
| `mcp.server` | 条件 | Agentconf `mcp_servers.name` | 仅 `origin: mcp`；不要写 URL、headers 或密钥 |

不要再手写其他顶层字段。`members` 由构建工具生成；`permissions` / `hooks` / `provider_requirements` 之类不要放进 `ability.yaml`。

`origin: mcp` 时 `identity.namespace` 固定为 `mcp`，`identity.name` 必填（服务器 slug），并且不能有 `operations/` 或 `skills/`。

## 4. `members` 是生成物

`build` 会读取每个 Operation 模块的模型与常量，生成 `members`，写回 `ability.yaml`。开发者不应当手改 `members`；如果yaml失效，（由于 Operation 模块契约改动或新增 `SKILL.md`），可重新 `build`。

生成后的结构大致如下（省略了部分契约细节）：

```yaml
members:
  operations:
    render:
      title: Render Report
      description: 按模板渲染一份报告。
      entry: operations/render.py
      input_schema: {type: object, additionalProperties: false}   # properties / required 省略
      output_schema: {type: object, additionalProperties: false}  # properties / required 省略
      tags: [render, reporting]
      effects: [filesystem:write]
      permissions: {fs_write: true, require_hitl: true}
      resources: {paths: [template, rows]}
      execution: {mode: request, timeout_ms: 120000, cancellation: unsupported, on_revoke: drain, retry: never}
      expose: true
  skills:
    reporting-guide:
      title: Reporting Guide
      description: 写周报与月报的步骤、模板与检查清单。
      tags: [reporting]
      body: {path: skills/reporting-guide/SKILL.md, media_type: text/markdown}
      assets: {}
      references: {}
      scripts: {}
```

- `entry` 永远等于 `operations/<键>.py`。
- `tags`、`effects`、`permissions`、`execution` 由模块常量生成。
- 外部包必须随包分发生成好的 `ability.yaml`；安装器会做校验。
- `ability.lock.yaml` 由安装Ability包时候通过校验生成，包含文件的hash；安装后它在版本目录旁，文件名是 `<version>.lock.yaml`。

## 5. Operation

一个 Operation 是 `operations/<键>.py`。键必须匹配 `^[a-z][a-z0-9_]{0,63}$`，以下四个工具保留名不能被 Operation 键或脚本键占用：

`search_tools`、`describe_tool`、`execute_tool`、`run_script`

`bind_tool` / `unbind_tool` 是运行时内部的绑定映射 API，不是模型可见工具，不占用工具保留名。

模块里声明以下内容：

| 声明 | 要求 | 生成到 | 说明 |
| --- | --- | --- | --- |
| `class XxxInput(BaseModel)` | 必需 | `input_schema` | 参数模型；缺失即构建失败 |
| `class XxxOutput(BaseModel)` | 建议 | `output_schema` | 返回结构；缺失时生成空 schema |
| `TITLE: str` | 必需 | `title` | 给模型看的短标题 |
| `SUMMARY: str` | 必需 | `description` | 一行说明 |
| `TAGS: list[str]` | 必需，可为 `[]` | `tags` | 发现与过滤；生成器排序去重 |
| `PERMISSIONS: dict` | 必需，可为 `{}` | `permissions` | 权限申请值 |
| `EFFECTS: list[str]` | 必需，可为 `[]` | `effects` | 副作用标签；生成器排序去重 |
| `TIMEOUT_MS: int` | 必需 | `execution.timeout_ms` | 必须大于 0 |
| `RESOURCES: dict` | 可选，默认 `{}` | `resources` | 目前只认 `{"paths": [...]}` |
| `EXPOSE: bool` | 可选，默认 `True` | `expose` | 是否可作为工具暴露 |

示例：

```python
from pydantic import BaseModel, ConfigDict


class RenderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str
    rows: list[dict]


class RenderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


TITLE = "Render Report"
SUMMARY = "按模板渲染一份报告。"
TAGS = ["render", "reporting"]
PERMISSIONS = {"fs_write": True, "require_hitl": True}
EFFECTS = ["filesystem:write"]
TIMEOUT_MS = 120_000
RESOURCES = {"paths": ["template", "rows"]}
EXPOSE = True
```

模块顶层只放声明和可安全导入的定义；不要在 import 时执行 I/O、网络或长任务。生成器只读取上面的声明，**不会执行** Operation 的执行体。执行入口按运行时提供的 Operation 基类或模板实现。

### 5.1 `PERMISSIONS`

`PERMISSIONS` 是**申请值**，不是授权。运行时授权库是最终权威；声明本身不会让 Agent 获得权限。

| 键 | 类型 | 含义 |
| --- | --- | --- |
| `require_hitl` | `bool` | 要求人工确认；只能加严，不能被包声明豁免 |
| `fs_read_only` | `bool` | 只读文件系统 |
| `fs_write` | `bool` | 写文件系统 |
| `net_access` | `bool` | 网络访问 |
| `shell_access` | `bool` | 执行 shell 命令 |
| `allowed_paths` | `list[str]` | 允许的工作区相对路径；空 = 不限制 |
| `denied_paths` | `list[str]` | 禁止的工作区相对路径 |
| `allowed_commands` | `list[str]` | 允许的命令；空 = 不限制 |
| `denied_commands` | `list[str]` | 禁止的命令 |

- `PERMISSIONS` 只允许这 9 个键；未知键构建失败。
- `fs_read_only` 与 `fs_write` 不能同时为 `true`。
- `allowed_paths` / `denied_paths` 只写工作区相对路径，`/` 分隔，`"."` 表示工作区根；不能写绝对路径、盘符、UNC、`..`、`~`、环境变量或模板。
- 包内资源用 `context.package_root` 读取，不要靠文件系统权限字段授予包内路径。

### 5.2 `EFFECTS`、`RESOURCES`、`EXPOSE`

- `EFFECTS` 是自由字符串列表，用于展示与策略，例如 `filesystem:read`、`filesystem:write`、`net:request`、`shell:execute`。不要把它当成运行期拦截机制。
- `RESOURCES.paths` 里的每个名字必须是 `Input` 模型的属性名，表示该参数是路径资源。
- `EXPOSE = False` 表示成员只在内部可用，不进入模型工具面。

## 6. Skill

一个 Skill 是 `skills/<name>/`，其中必须有 `SKILL.md`。本节采用 Claude Skill 标准；目录名必须等于 frontmatter 的 `name`。

```markdown
---
name: reporting-guide
description: 写周报和月报的步骤、模板与检查清单。
metadata:
  title: Reporting Guide
  tags: [reporting, docs]
---

# Reporting Guide

这里写 Skill 的正文。
```

规则：

- `name` 必须匹配 `^[a-z0-9]+(?:-[a-z0-9]+)*$`，并与目录名一致。
- `description` 必填、非空、不超过 1024 字符，使用第三人称。
- `metadata.title` 可选；缺省时用 `name`。
- `metadata.tags` 可选；生成器会排序。
- v1 不读取 `annotations`；不要依赖它。
- 正文使用 Markdown；`references/`、`assets/` 放说明与素材。
- `scripts/` 放委托子进程执行的脚本，v1 只允许 `.py`；脚本不是 Operation，不生成 `input_schema`，参数由脚本自己解释；调用入口是 `run_script`。
- 一个包可以有 0 个、1 个或多个 Skill。

## 7. MCP 连接包

MCP 连接包只声明“用 Agentconf 的解析连接端点”，成员由 `tools/list` 生成。
关于AgentConf，见 [AgentConf](https://github.com/ghrah-org/agentconf) ,九夏继承了其客户端并通过AgentConf统一管理 AI 和 MCP 的 url 和 apikey

```yaml
format: ghrah.ability-definition
schema_version: '1'
origin: mcp
identity:
  namespace: mcp
  name: github
  version: '0.0.1'
title: GitHub MCP
description: GitHub MCP 工具快照。
mcp:
  server: github-mcp
```

规则：

- `mcp.server` 是 Agentconf 的 `mcp_servers.name`，不是 URL；URL、headers、凭据由 Agentconf 管理。
- 连接与工具清单按 2026-07-28 版 MCP 规范；`tools/list` 分页取尽。
- 不要写 `operations/` 或 `skills/`，也不要手写 `members`。
- `install` / `refresh` 会尝试拉取 `tools/list` 并生成成员；工具集为空时不生成包。
- 每个工具生成一个 Operation，原始工具名保留在 `mcp.tool`，成员键由工具名规范化得到。
- MCP 成员固定 `expose: true`，可被授予；具体执行能力由运行时决定。

## 8. 构建、安装与版本

> 状态（2026-11-04）：`build` / `install` / `verify` 命令尚未提供；当前只有生成器原型（`python -m ghrah.ability.tools.build`），示例包的 `members` 仍按本文规则手工维护；生成器已能处理 v1 源包，并可通过 `--builtin` 重建内置 `ghrah.ability-builder`。

```bash
ghrah ability build <目录>
ghrah ability build <目录> --check
ghrah ability install <目录|归档>
ghrah ability verify <name>@<version>
```

- `build` 在作者侧运行，生成 `members` 并写回 `ability.yaml`；`--check` 只校验不写盘。
- 外部包必须提交生成后的 `ability.yaml`；安装器只校验，不 import 包代码、不重建索引。
- 同一版本号不允许对应两份不同内容；改动任何文件后必须提升 `identity.version`。
- 安装器会生成 `<version>.lock.yaml`；不要把它放进源码包，也不要手改。
- `verify` 会核对整树摘要，并可用逐文件清单指出新增、删除或变化的文件。

## 9. 命名、路径与可移植性

- Operation 键：`^[a-z][a-z0-9_]{0,63}$`。
- Skill 名：`^[a-z0-9]+(?:-[a-z0-9]+)*$`，不超过 64 字符。
- `identity.namespace` / `identity.name`：`^[a-z][a-z0-9_-]*$`；`ghrah` 与 `mcp` 保留。
- v1 只支持 Python Operation（`.py`）；不要提供 `.js` 或其他语言的 Operation。
- 文本文件一律 UTF-8 无 BOM、LF 换行。
- 包内相对路径统一 `/`，NFC 归一；不要使用符号链接或目录 junction。
- 文件名不能只靠大小写区分；Windows 保留名、ADS、尾点/尾空格都会被拒绝。
- 包内相对路径 ≤ 180 个 UTF-16 码元；安装后的绝对路径 ≤ 240 个 UTF-16 码元（给 Windows `MAX_PATH=260` 留余量）。
- 不要把宿主绝对路径、主机名、时间戳写进 `ability.yaml`。

## 10. 发布前检查

- `build --check` 通过，且连续两次 `build` 字节相同。
- 至少有一个 Operation 或 Skill。
- `ability.yaml` 里没有手写的 `members`，也没有顶层 `permissions`；源码包里没有 `ability.lock.yaml`。
- `PERMISSIONS` 只申请真正需要的能力；路径都是工作区相对路径。
- 同一版本内容变化时已经提升版本号。
- 包内没有符号链接、绝对路径、CRLF 或 BOM。
