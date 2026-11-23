# member 定义规范

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-SPEC-011 |
| 文档状态 | 现行（唯一范式声明，已实现） |
| 校准基线 | `a742fcf`（2026-11-23） |
| 读者 | 写能力包的人、写模板/装配/授权/工具面的人、评审与测试 |
| 效力 | 本文是 `member` 这一概念的**唯一定义**。其它文档、代码注释与 UI 文案出现冲突时，以本文为准并回来修正它们 |
| 不覆盖 | 能力包的目录布局（另一份规范）、驱动的循环语义、MCP 协议本身 |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-11-19 | 首版。把此前散落在模板、装配输入、授权账本、工具面四处的写法收敛为一份定义 |
| 2.0 | 2026-11-23 | 模板层改为 `available_members` 成员身份数组；成员全名规范形态定为三段（退化包两段为兼容层）；⑤ 层补技能目录与 `read` 地址；新增不变式 M-13/M-14 |

## 1 定位：member 在装配链上的位置

`member` **不是**某个模块的内部对象，而是贯穿整条装配链的交换单位。同一条信息在五个层次上有五种形态，但**指向的必须是同一个成员**：

```text
①定义面            ②模板                    ③装配输入              ④授权事实              ⑤工具面/目录
ability.yaml  →  AgentTemplateMember   →  AgentAssemblyMember  →  GrantFacts          →  tool_name / catalog_name
members.*        (身份六字段)            (member, type, axes)      (members, content,    (模型可见的名字)
                                                                     axes)
     │                  │                      │                       │                     │
  作者声明          用户选什么            这次要装什么            实际授予了什么         模型看到什么
```

规则：

- 五层之间**只能单向派生**：定义面 → 申请 → 装配 → 授予 → 工具面。任一层都不得反向改写上一层。
- 每一层都保留**成员全名**（规范形态三段；退化包两段见 §3.1），否则跨层追踪断裂。
- 「模板只是申请」：②③ 不产生权限；④ 才是权限事实；⑤ 由 ④ 投影。
- 创建时另把 ③ 补成**定义态快照**（`agent_instances.members`，带 `data`），它是 ③ 的冻结副本，不是第六层。

## 2 定义：member 是什么

> **member 是能力包内一个可被单独申请、单独授予、单独装载的最小单元。**

| 是 | 不是 |
| --- | --- |
| 包内最小粒度（一个 operation / 一个 skill / 一个远端 mcp 工具） | 不是包（包是成员的容器） |
| 可被模板逐条勾选、被授权账本逐条记录 | 不是「能力」的同义词：能力系统里 ability 包才是容器 |
| 可带自己的权限上限与两轴（visible/bind） | 不是一个运行时对象：运行时对象是「装载后的工具」 |
| 有稳定全名，跨版本不变（版本在包上） | 不是脚本/附件：脚本与附件是 skill 的从属资源，不是独立成员 |

### 2.1 三种成员类型

| type | 语义 | 有 `execute` | 有 `bind` 轴 | 定义位置 |
| --- | --- | --- | --- | --- |
| `operation` | 一次可调用的动作（读写文件、执行命令、结束任务…） | 是（`async def execute(call, services)`） | 有 | `ability.yaml` 的 `members.operations.<key>` + `operations/<key>.py` |
| `skill` | 一段可注入模型上下文的说明书（`SKILL.md`）及其资源 | 否（被 `load_skill` 读取正文） | 有（决定是否进每轮技能目录） | `members.skills.<key>` + `skills/<key>/` |
| `mcp` | 远端 MCP 服务器暴露的一个工具，经快照绑定 | 否（转发给远端） | 有 | 声明 `origin: mcp` + 安装生成的快照 |

`skill` 之下的 `scripts` 是**资源**，不是成员：它们出现在授权账本的地址里（`skill/<key>/scripts/<script>`），但不会成为 `AgentAssemblyMember`。

## 3 命名规范

### 3.1 包全名与成员全名

```text
包全名（package full name）
  ├─ 常态：<namespace>.<name>          例：ghrah.fs、ghrah.core
  └─ 退化：<namespace>                 当 identity 不写 name 时，name 视为等于 namespace
                                       例：identity.namespace=research → 包全名 research

成员全名（member full name，规范形态）
  = <namespace>.<ability_name>.<成员键>  例：ghrah.fs.read_file、research.research.research
```

- **规范形态是三段**：`namespace.ability_name.member_name`。模板、装配输入、实例快照、
  日志、UI 一律用三段全名。
- 退化身份包（`identity.name` 省略）在**定义面**仍按两段登记（`research.research`），
  Core 在三处按等价形态兼容：成员解析失败回退两段、安装记录回退 `namespace`、绑定集合
  等价比较。这是兼容层，不是新约定；长期方向是让 `identity.name` 恒有值（见 `ghrah-12`）。
- 包名与成员键都用 `.` 分隔，成员键本身**禁止**包含 `.`。

### 3.2 字符集

| 对象 | 规则 | 依据 |
| --- | --- | --- |
| 包命名空间 | `^[a-z][a-z0-9_-]*$` | `Member`/`ability_schema` 身份校验（**不允许点号**） |
| 包名 | `^[a-z][a-z0-9_-]*$` | 工具名算法 |
| operation 键 | `^[a-z][a-z0-9_]{0,63}$` | 工具名算法 |
| skill 键 | `^[a-z0-9]+(?:-[a-z0-9]+)*$`（允许连字符） | 生成器声明常量 |
| 工具名 | `^[A-Za-z0-9_-]{1,64}$`，且不得占用保留名 | 工具名算法 |

### 3.3 键归一（重要）

skill 键常带连字符，而装载层的内部注册名只接受 `[a-z0-9_]`，因此**加载投影处做一次归一**：

```text
normalize(key) = 逐字符：字母/数字/下划线保留，其余替换为 "_"，且先转小写
ability-package-development-guide → ability_package_development_guide
```

于是存在**两个键**，必须都被登记，否则查不到：

- **定义键**（raw）：`ability-package-development-guide`——出现在目录、模板、UI；
- **装载键**（normalized）：`ability_package_development_guide`——出现在 `ResolvedAbility.ability_name` 与内部注册名。

成员全名以**定义键**为准（`ghrah.ability-builder.ability-package-development-guide`）；装载层用归一键时，必须保持"两个键指向同一个 manifest"，并按成员全名去重，不能出现两条。

### 3.4 保留名

| 名字 | 占用者 | 约束 |
| --- | --- | --- |
| `search_tools` / `describe_tool` / `execute_tool` | 内置 `ghrah.core` 的元工具 | 任何包不得占用 |
| `bind_member` / `unbind_member` | 内置 `ghrah.core` 的绑定元工具 | 任何包不得占用 |
| `run_script` | 工具表面层（脚本调用入口） | 任何包不得占用（含内置包） |
| `conversation` / `end_task` | 循环控制成员（见 §7） | 只有 `ghrah.core` 可以声明 |

保留名集合在安装期与运行期共用同一份（`surface/naming.py` 的 `META_TOOL_NAMES` / `RESERVED_TOOL_NAMES`）：成员键撞上保留名会被安装/装配拒绝，不会自动改名。

## 4 数据格式规范（五层逐层）

### 4.1 ① 定义面：`ability.yaml` 的 `members`

```yaml
format: ghrah.ability-definition
schema_version: '2'          # 只接受 '1'/'2'；构建产物恒为 '2'
identity: {namespace: ghrah, name: core, version: 0.1.0}
title: Core
description: ...
members:                     # 由 `python -m ghrah.ability.tools.build` 生成，禁止手写
- namespace: ghrah
  name: core
  member: ghrah.core.bind_member
  type: operation            # operation | skill | mcp
  version: null              # 内核件恒 null；能力件写安装版本
  ability_type: kernel       # kernel | external
  axes: {visible: true, bind: true}
  data:
    title: Bind Member
    description: Bind a granted, visible tool into the model context…
    path: ghrah/core/operations/bind_member.py   # 能力根相对路径（内核件无版本段）
    input_schema: {...}      # 来自实现脚本的 Input 模型
    output_schema: {...}     # 来自实现脚本的 Output 模型
    tags: [membership]
    effects: [...]
    permissions: {...}       # 包声明的权限上限（成员级申请只能收紧）
    resources: {...}
    execution: {mode, timeout_ms, cancellation, on_revoke, retry}
    expose: true             # 是否产出工具面
    mcp: {...}               # 仅 origin: mcp 的包
- namespace: ghrah
  name: ability-builder
  member: ghrah.ability-builder.ability-package-development-guide
  type: skill
  version: null
  ability_type: kernel
  axes: {visible: true, bind: true}
  data:
    title: Ability Package Guide
    description: ...
    path: ghrah/ability-builder/skills/ability-package-development-guide/SKILL.md
    media_type: text/markdown
    tags: [ability, guide]
    assets: {<资源键>: {path: ...}}
    references: {<资源键>: {path: ...}}
    scripts: {<资源键>: {path: ..., entry: ...}}
```

规则：

- `members` 是**成员记录数组**，不是 `{operations, skills}` 两个映射；数组里按 `member` 唯一。
- operation 的实现位置在 `data.path`（旧字段 `entry` 已不再出现）；`input_schema`/`output_schema`/`tags`/`effects`/`permissions`/`expose` 都由构建器从实现脚本的 `Input`/`Output` 与模块常量生成，**不要手写**。
- 纯 skill 包（没有 operation 成员）合法：`members` 只含 skill 记录；「纯技能包也进目录」是运行期要求（见 §4.6）。
- 头段的 `identity` 与 `title`/`description` 保留作者写法，构建只重写 `schema_version` 与 `members`。

### 4.2 ② 模板：`available_members` 成员数组

```jsonc
[
  {
    "namespace": "ghrah",            // 包命名空间
    "name": "core",                  // 能力名（退化包与 namespace 相同）
    "member": "ghrah.core.conversation", // 三段全名
    "type": "operation",             // operation | skill | mcp
    "version": null,                 // 内核件恒 null；能力件为钉住的版本
    "ability_type": "kernel"         // kernel | external
  }
]
```

规则：

- 模板**只存成员身份**：不存 `data`、`axes`、`surface`、`mcp_refs`、脚本 `exec` 选择。
- 模板是申请，不是授权：渲染成装配输入后仍要经启动核定落 `ability_grants`。
- 成员必须属于项目能力目录里可选的成员；模板保存时按 `(namespace, name, member)` 去重。
- 旧形状（`ability_request.packages` 的包维度申请）已废弃，库内旧行由启动迁移转成成员数组。

### 4.3 ③ 装配输入：`AgentAssemblyMember`（引用态）

```jsonc
{
  "member": "ghrah.fs.read_file",   // 成员全名，必填
  "type": "operation",              // operation | skill | mcp
  "version": null,                  // 能力件为钉住的版本；内核件恒 null
  "ability_type": "kernel",         // kernel | external —— 只是来源标记，装载逻辑不分叉
  "permissions": {                  // 九维，全部可空；null = 未申请
    "allowed_paths": null, "denied_paths": null, "require_hitl": null,
    "fs_read_only": null, "fs_write": null, "shell_access": null,
    "net_access": null, "allowed_commands": null, "denied_commands": null
  },
  "axes": { "visible": true, "available": true }
}
```

关键约定：

- `permissions` 的 `null` 表示**未申请**，Core 沿用包声明上限；显式 `false` 才是收紧。用默认值填充等于把「未申请」误判成「显式收紧」。
- `version` 只对 `ability_type=external` 有意义；内核件写 `null`，禁止写一个"看起来像版本"的值。
- `axes` 可以省略（默认 `true/true`），但一旦给出必须满足 §5。
- **创建时补齐**：装配输入是引用态（不带 `data`）；创建线按来源读定义（external 按版本读安装记录，kernel 读内置树），把成员补成定义态后写进实例快照 `agent_instances.members`。快照是 ③ 的冻结副本，不是第六层。

### 4.4 ④ 授权事实：账本里的两个模型

启动策略规则（扁平，一条只指向一个目标）：

```text
GrantRule { package, agent|null, operation|null, skill|null, script|null,
            effect: grant|remove, visible: true, bind: true }
```

授予后的事实（按成员地址索引）：

```text
GrantFacts {
  members: {<operation 键>},
  content: {skill/<key>, skill/<key>/scripts/<s>, skill/<key>/assets/<a>, skill/<key>/references/<r>},
  axes:    {成员地址 → (visible, bind)}            # 地址全集必须被 axes 覆盖，缺一即报错
}
```

**地址规范**（跨层唯一）：

| 成员 | 地址 |
| --- | --- |
| operation | `operations/<key>` |
| skill 正文 | `skill/<key>` |
| skill 脚本 | `skill/<key>/scripts/<script>` |
| skill 附件 | `skill/<key>/assets/<name>` |
| skill 参考 | `skill/<key>/references/<name>` |

注意 operation 地址带复数前缀 `operations/`，skill 地址是单数 `skill/`——这是既有契约，不要"统一"。

### 4.5 ⑤ 工具面：`tool_name`

装载后成员成为工具，名字由算法决定（纯函数，输入一组 `(包名, 成员键) → 工具名`）：

```text
1. 基础名 = 成员键（operation）；skill 不产工具，只产「可加载的说明书」
2. 同一批内重名（不同包的同名成员）：全部改写为 `<package>__<key>`（`.`/`-` 归一为 `_`）
3. 长度上限 64；超长时取前 54 + "__" + sha256 前 8 位十六进制
4. 与保留名（search_tools / describe_tool / execute_tool / bind_member / unbind_member / run_script）
   冲突：直接拒绝，不自动改名
5. 输出按工具名升序
```

`tool_name` 到成员是**唯一权威的映射**：调用只查表，不拼模块路径、不拼服务器地址。

### 4.6 ⑤ 技能面：目录条目与 `load_skill`

技能成员不产工具，产的是**每轮注入的技能目录**（`ToolSurfaceRuntime.skill_reminder`）：

- 绑定（`bind=true`）的技能进正式目录，条目格式固定为
  ``- `alias/key` (read: `<ability 根相对路径>`): description``；
- 可见未绑定的技能不进目录，只在同一提醒末尾出现一行"可用 `bind_member` 绑定"的提示；
- `visible=false` 的技能既不进目录也不出提示；
- **纯技能包**（没有 operation 成员）照常进目录：目录按授权投影里的技能节点收集，不依赖同包是否有工具。

`load_skill` 接受三种写法，最终都落到同一套授权与版本校验：

| 写法 | 解析 |
| --- | --- |
| `alias/skill` | 直接按 alias + 成员键解析 |
| 裸技能键（如 `research`） | 在当前 Agent 已获授技能里按成员键匹配；唯一命中可用，多个命中报歧义并列出候选，未命中回列可用清单 |
| 目录条目的 `read:` 地址 | 只在当前 Agent 已授予的实例里按声明路径反查，再走同一套校验 |

正文来自已提交的 `ability_content` blob（不是磁盘文件），读取时校验声明摘要与字节数，单次上限 1MB；`path` 参数只能读该技能已登记的附件/参考。

**绑定集合的等价形态**：快照用三段名、定义面对退化包给两段名，比较时按等价形态互认（`a.b.c` ↔ `a.b` 当 `a==b`）。`refresh` 修剪绑定集合时也按等价形态保留，否则退化包技能会被误判为未绑定。

## 5 两轴：`visible` 与 `bind`

两个轴描述「授予之后」的状态，与定义面的 `expose`（是否有工具形态）是**不同维度**：

| 维度 | 定义面 `expose` | 授予轴 `visible` / `bind` |
| --- | --- | --- |
| 位置 | `ability.yaml` 的成员声明 | 创建请求的 `initial_bindings` + 运行期 `bind_member`/`unbind_member` → 实例快照 |
| 谁决定 | 包作者 | 模板作者（经创建请求） / 创建者 / 模型运行时 |
| 回答 | 这个成员有没有工具形态 | 能不能被发现、是否常驻模型上下文 |
| 是否可逐 Agent 不同 | 否（包级固定） | 是（每个 Agent 每个成员） |

轴的两个来源（模板本身不存轴）：

1. **创建时**：`initial_bindings` 列出要绑定的成员全名；未列出的可见成员 `bind=false`；`visible` 恒 `true`。控制成员强制绑定。
2. **运行期**：`bind_member` / `unbind_member` 立即改写实例快照的 `axes.bind`，重启按快照恢复。

三条合法组合：

| visible | bind | 合法 | 语义 | 装配结果 |
| --- | --- | --- | --- | --- |
| `true` | `true` | ✅ | 已绑定 | schema 常驻模型上下文，可直接调用 |
| `true` | `false` | ✅ | 可发现未绑定 | 可被 `search_tools` 找到、`execute_tool` 调用；模型可 `bind_member` 装上 |
| `false` | `false` | ✅ | 隐藏 | 不进目录，不可发现也不可调用 |
| `false` | `true` | ❌ | 绑定却不可见——无法解释 | 构造期拒绝（`bind=true` 要求 `visible=true`） |

运行期变更：`bind_member` / `unbind_member` 两个元工具由模型自行调用；绑定的权威是
**实例快照**（`agent_instances.members[].axes`），每次变更立即写库，重启按库恢复。
控制成员（`conversation` / `end_task`）不参与两轴，也不允许被绑定或解绑。

技能成员同样有两轴，`bind` 决定它是否进每轮技能目录（正文始终按需 `load_skill`，
附件/脚本的可见性另由授权内容白名单表达）；脚本的可执行性仍是独立字段 `exec`
（默认 `false`），与两轴无关。

技能成员的三种组合：

- `bind=true`：进每轮技能目录（名字 + 一行摘要 + `read` 地址），正文仍按需 `load_skill`；
- `visible=true, bind=false`：不进正式目录，但每轮有一行"可用但未绑定"提示（名字 + 绑定入口），模型可 `bind_member` 按目录名绑定；
- `visible=false`：目录与提示都不出现。

装配边界按成员类型分流：`operation` 走工具面（绑定集合 → 静态 schema），
`skill` 走消息面（`skill_reminder` 按绑定过滤后每轮注入，目录条目带正文读取地址），
控制成员走 control 通道（不受两轴影响）。

**wire 与快照过渡**：

- 模板/装配输入的成员条目沿用 `axes.available` 承载绑定轴（`available=true` 即绑定），
  Core 解析时映射成 `MemberAxes.bind`；也接受直接写 `bind`。
- 模板已改为 `available_members` 成员数组（三段全名，只存身份六字段），不再存 `data`
  与脚本 `exec` 选择；定义数据在创建时由 Core 按来源补齐。
- 实例快照写 `axes {visible, bind}`；旧快照的 `available` 键在写回时同步，读侧两种都认。

## 6 成员生命周期

```mermaid
stateDiagram-v2
    [*] --> Declared: ① 包定义（ability.yaml）
    Declared --> Requested: ② 模板勾选（available_members，只存身份）
    Requested --> Assembled: ③ 渲染成装配输入（钉版本、合并路径权限）
    Assembled --> Granted: ④ 启动核定（control 通道除外）
    Assembled --> Snapshot: 创建时补齐定义数据 → 实例快照
    Granted --> Loaded: ⑤ 装载（visible 且 bind）
    Loaded --> Visible: 进工具面（expose 为真且有 schema）
    Visible --> Called: 模型调用
    Loaded --> Hidden: 有 schema 但 visible=false（不进模型列表）
    Granted --> Skipped: visible/bind 不满足 → 不装载
    Declared --> Rejected: 成员不存在 / 版本不匹配 / 越界
```

要点：

- **申请不等于授予**：模板没选 → 不申请；策略没给 → 不授予；两者都满足才装载。
- **授予不等于可见**：可见性由轴决定，工具面再由 `expose` 与实际 schema 过滤。
- **跳过不报错**：`visible`/`bind` 不满足时该成员只是不装载；只有"申请了但不存在/版本错/权限越界"才是错误。

## 7 控制成员（特殊类别）

`conversation`、`end_task` 是**循环控制成员**：它们属于成员体系，但不受常规授权面约束。

| 方面 | 常规成员 | 控制成员 |
| --- | --- | --- |
| 能否出现在模板 | 能 | 能（历史与前端都允许） |
| 是否写授权账本 | 是 | **否**：启动核定时被分流到 control 通道 |
| 是否受授权投影裁剪 | 是（撤销后从模型视野消失） | **否**：永远保留 |
| 工具形态 | 一般有 schema | `conversation` 无 schema；`end_task` 仅 `toolcall` 模式有 |
| 由谁驱动 | 模型调用 | Agent 主循环 / Hook 路由 |
| 归属 | 任意包 | 仅 `ghrah.core` |

规范要求：

1. 控制成员的判定是**两个条件同时成立**：handler ∈ `{conversation, end_task}` 且成员全名以 `ghrah.core.` 开头。
2. 它们必须能被模板选中并真正进入装配结果——否则循环缺少出口；装配层不得在解析阶段丢弃或拒绝它们。
3. 它们的模板轴应为 `visible=true, bind=true`；定义面的 `expose=false`（`conversation`）不得被当作轴值使用。

## 8 不变式（违反即为缺陷）

| 编号 | 不变式 |
| --- | --- |
| M-1 | 成员全名全局唯一；同一包内键唯一 |
| M-2 | 成员全名中的包部分必须等于所属包的包全名（含退化身份的两段相同情形） |
| M-3 | 成员键不得包含 `.`；字符集符合 §3.2 |
| M-4 | 模板/装配/快照里出现的成员，必须能在对应包版本的定义面找到；找不到即失败，不得静默丢弃 |
| M-5 | 能力件成员必须带 `version`；内核件成员必须 `version=null` |
| M-6 | `permissions` 只能收紧包声明，不得放宽 |
| M-7 | 轴组合必须落在 §5 的三条合法组合内 |
| M-8 | skill 不参与 operation 的成员级权限合并；skill 的授予以「整条 skill」为单位 |
| M-9 | 新模板/装配输入不含 `whole_container`；旧快照出现时按包展开兼容（不静默丢弃、不拒绝） |
| M-10 | 控制成员不得写入授权账本；非控制成员不得被静默排除在账本之外 |
| M-11 | `expose` 与 `visible/bind` 不得互相赋值 |
| M-12 | 工具名与成员的映射唯一且确定：同输入同输出，与顺序无关，冲突不静默改名 |
| M-13 | 创建时补齐的成员 `data` 必须来自成员所属版本的已提交定义，不得来自模板或客户端 |
| M-14 | 技能目录条目附带的 `read` 地址必须能反查到同一授予面内的成员；反查不走第二套权限 |

## 9 版本与来源标记

| 字段 | 取值 | 用途 | 禁止 |
| --- | --- | --- | --- |
| `ability_type` | `kernel` / `external` | 描述成员来自内置树还是安装根 | 不得据此分叉装载逻辑（两者同一套装载） |
| `version` | 字符串 / `null` | 能力件在安装根下的版本层 | 内核件写任何版本值 |

> 旧模板层的 `origin`（`builtin`/`installed`/`mcp`）已随整包申请一起废弃：模板改存成员身份六字段，来源由 `ability_type` + `version` 表达。

版本判定：最新版本按**数值**比较（`1.10.0 > 1.9.0`），字符串比较会把最新版判错。

## 10 类图

```mermaid
classDiagram
    class MemberDefinition {
        +key
        +type: operation|skill|mcp
        +title
        +description
        +expose: bool
        +permissions
        +full_name(package) string
    }
    class AgentTemplateMember {
        +namespace: string
        +name: string
        +member: string
        +type: operation|skill|mcp
        +version: string|null
        +ability_type: kernel|external
    }
    class AgentAssemblyMember {
        +member: string
        +type
        +version: string|null
        +ability_type: kernel|external
        +permissions: AgentMemberPermissions
        +axes: AgentMemberAxes
    }
    class MemberSnapshot {
        +member: string
        +type
        +version: string|null
        +ability_type: kernel|external
        +axes: AgentMemberAxes
        +data: object
    }
    class GrantFacts {
        +members: frozenset
        +content: frozenset
        +axes: dict
        +addresses() frozenset
    }
    class ResolvedAbility {
        +ability_name
        +tool_schema
        +permissions
        +implementation
        +requested_permissions
        +declared_permissions
    }
    class NamedMember {
        +tool_name
        +package
        +member
    }
    class ControlMember {
        <<special>>
        +channel: control
        +ledger: never
    }

    MemberDefinition --> AgentTemplateMember : 被选择
    AgentTemplateMember --> AgentAssemblyMember : 渲染
    AgentAssemblyMember --> MemberSnapshot : 创建时补齐 data
    AgentAssemblyMember --> GrantFacts : 授予（控制成员除外）
    GrantFacts --> ResolvedAbility : 装载
    AgentAssemblyMember --> ResolvedAbility : 解析
    ResolvedAbility --> NamedMember : 命名
    ControlMember --> ResolvedAbility : 直接装载
```

职责边界：`MemberDefinition` 是包作者的声明；`AgentTemplateMember`/`AgentAssemblyMember` 是"要什么"；`MemberSnapshot` 是"创建时定下来的定义态副本"；`GrantFacts` 是"给了什么"；`ResolvedAbility` 是"解析出来的可装载对象"；`NamedMember` 是"模型看到什么"。

## 11 已知偏差与待收敛

1. **控制成员不进账本**：如 §7，属于设计选择，但导致管理面的授权视图与真实工具表存在系统性差异（控制成员在跑却看不见）。
2. **整包展开的 `expose` 当轴值**：旧模板里整包选择 `ghrah.core` 时，`conversation` 的 `expose=false` 曾使展开产生非法轴组合；现行模板已改为成员数组，新数据不再有整包，只剩旧快照的兼容分支。
3. **定义键与装载键双登记**：见 §3.3，目前靠两处登记 + 按成员全名去重维持一致；未来应在装载层只认定义键。
4. **`whole_container` 只剩读侧兼容**：新模板与装配输入不再产生它；快照恢复时遇到按"未展开"处理（`assembly.py` 的兼容分支），后续可整体移除。
5. **绑定轴两个字段名并存**：模板/装配输入用 `axes.available`，实例快照写 `axes.bind`，读侧两种都认。语义已经是"是否绑定"，待调用方全部升级后可删掉 `available` 读取。
6. **`visible=false` 的成员无法被运行时重新绑定**：它不进目录，`bind_member` 找不到目标；如需"先隐藏、按需启用"，应改为 `visible=true, bind=false`。
7. **退化身份包的两段名兼容**：定义面对 `identity.name` 省略的包给两段成员名（`research.research`），模板/快照给三段（`research.research.research`）。Core 在解析、安装记录读取、绑定集合比较三处按等价形态兼容；这是兼容层，长期应让 `identity.name` 恒有值。