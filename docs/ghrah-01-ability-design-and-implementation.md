# 能力系统设计与实现

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-DESIGN-001 |
| 文档状态 | 现行（authoritative，已实现） |
| 校准基线 | `ghrah-monorepo` @ `a742fcf`（2026-11-23） |
| 读者 | 维护能力系统的开发者、写能力包的人、评审运行时契约的架构人员 |
| 相关文档 | `ghrah-11-member-spec.md`（成员概念唯一定义）、`ghrah-12-ability-package-layout.md`（包布局与构建/安装职责）、`ghrah-13-ability-data-store.md`（数据底座）、`ghrah-02`（新增成员手册）、`ghrah-04`（技能系统） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。以旧 `ghrah.scheme` 布局为基线描述定义面、目录池、扫盘、工具面与执行面 |
| 2.0 | 2026-11-23 | 全文重写。基线升到 `a742fcf`：定义面 v2、授权投影 + 绑定轴、启动提交到 SQLite（运行期只读库）、工具面 static/progressive、技能目录每轮注入、执行三段、装配收敛为单一路径；旧布局与 v1 查询链在 §14 逐条判定 |

## 1 文档说明

### 1.1 目的、范围与权威顺序

本文描述能力系统**当前实现**：包如何被定义、如何加载并提交进关系库、授权事实如何投影成模型可见的工具与技能目录、一次调用如何被接纳与执行、权限与人工审批在哪一层生效、装配期由谁把这些对象接起来。

**权威顺序**：代码 > 本文 > `docs/archive/` 历史稿；冲突以代码为准并当作文档缺陷修正。`member` 的数据形状以 `ghrah-11` 为准，包目录与构建/安装边界以 `ghrah-12` 为准，关系库表与读写路径以 `ghrah-13` 为准——本文不重复其逐字段定义，只在解释机制时引用。

**不在范围**：驱动循环（`ghrah-03`）、`SKILL.md` 正文语义与技能包治理（`ghrah-04`）、构建/启动/日志排障（`ghrah-05`）、协议 wire 与前端（`ghrah-08`）。

### 1.2 证据约定

- 关键论断给「**符号名 + 文件路径**」，例：`store.surface_snapshot`，`ghrah-core/src/ghrah/ability/access/store.py`。
- **行号只作当前校准指针**，不承诺稳定；找不到行号按符号名检索。
- 状态只用「**已实现 / 未实施**」。
- 路径缩写：`ability/` = `ghrah-core/src/ghrah/ability/`；`core/` = `ghrah-core/src/ghrah/core/`；`protocol/` = `ghrah-protocol/src/ghrah/protocol/`。

### 1.3 术语

| 术语 | 定义 | 代码位置 |
| --- | --- | --- |
| 能力包（package） | 含 `ability.yaml` 的目录；身份由 `identity` 决定，**不取自目录名** | `ability/loading/ability_schema.py` `AbilityDefinition` |
| 成员（member） | 包内可单独申请、授予、装载的最小单元：`operation` / `skill` / `mcp` | `ability/member.py` `Member` |
| 定义面（definition） | `ability.yaml` 的静态事实：身份、成员契约、执行约束 | `ability/loading/ability_schema.py` |
| 提交（commit） | 把定义 + 实现标识 + 内容清单 + 内容字节写入关系库 | `ability/access/store.py` `commit_version` |
| 授权投影 | 关系库里「某 Agent 对某包获授哪些成员、各成员可见/可用」的读模型 | `ability/access/facts.py` `GrantFacts`、`protocol/ability_tree.py` |
| 绑定轴（bind） | 成员是否常驻模型上下文；权威是实例快照 `agent_instances.members[].axes.bind` | `ability/member.py` `MemberAxes`、`core/agents/bindings.py` |
| 工具面（surface） | 交给模型的 function-calling schema 集合 | `ability/surface/` |
| 技能面（skill catalog） | 每轮注入的技能目录 + 按需读取正文 | `ability/loading/skill_context.py`、`ability/surface/runtime.py` |
| HITL | Human-In-The-Loop，人工审批 | `ability/permissions/hitl.py`、`ability/execution/executor.py` |

## 2 能力系统的四层视角

```text
① 定义面            ② 成员 / 授权              ③ 工具面 / 技能面            ④ 执行
ability.yaml   →  Member + GrantFacts      →  tool_name / catalog_name  →  OperationCall
（已构建索引）      （关系库 + 实例快照）        （每轮从库重建的投影）        + ExecutionServices
     │                     │                          │                        │
 包作者声明          启动核定写入事实            模型看到什么            一次调用如何落地
```

| 层 | 权威 | 产物 | 失败语义 |
| --- | --- | --- | --- |
| ① 定义面 | 已提交版本的 `ability_versions.definition` | `AbilityDefinition` / `Member` | 构建期严格报错；安装期 `index_mismatch` |
| ② 成员 / 授权 | `ability_grants`（授予）+ `agent_instances.members`（绑定轴） | 授予地址集合与两轴 | fail-closed：不一致即拒绝 |
| ③ 工具面 / 技能面 | ②的单向投影，不持第二份授权 | `SurfaceCatalog`、工具定义、目录文本 | 读不到即空，不猜 |
| ④ 执行 | 装配时固定的成员引用与受信身份 | `ActionResult` | 拒绝落成 `FAILURE`，不抛异常 |

**单向派生**：③ 只能从 ② 生成，② 只能从 ① 与显式策略生成；任何一层都不反向改写上一层（细节见 `ghrah-13` §1.1）。

## 3 定义面 v2

### 3.1 文件与顶层字段

路径固定 `<包根>/ability.yaml`（`DEFINITION_FILE_NAME`，`ability/loading/ability_schema.py`）。顶层字段由白名单固定，未知字段直接拒绝：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `format` | 字面量 `ghrah.ability-definition` | 是 | 格式不对即拒绝 |
| `schema_version` | `'1'` / `'2'` | 是 | **生成器只写 `'2'`**；`'1'` 仅存量安装根只读兼容 |
| `identity` | `{namespace, name?, version}` | 是 | `name` 缺席时包全名退化为 `namespace` |
| `title` / `description` | 非空串 | 是 | 目录与 UI 展示 |
| `origin` | `source` / `mcp`（默认 `source`） | 否 | `mcp` 时成员来自远端探测快照 |
| `mcp` | `{server}` | 否 | 仅 `origin: mcp` 允许，且只允许 `server` 一个键 |
| `members` | 成员记录列表（v2） | 是 | 由构建生成，禁止手写 |

`namespace` / `name` 走 `^[a-z][a-z0-9_-]*$`；`version` 必须是 SemVer core 三段（`V1_SEMVER_PATTERN`，每段 1–9 位、无前导零、不带预发布/构建元数据）——它同时是安装路径段。

### 3.2 `members`：两种 wire 形状，一种运行时成员表

| schema_version | `members` 形状 | 解析路径 | 现状 |
| --- | --- | --- | --- |
| `'2'` | **成员记录列表**，每项是 `Member.to_wire()` 形状 | `_members_list_to_tables` → 复用两段式校验 | 已实现（生成器唯一输出） |
| `'1'` | 两段映射 `operations` / `skills` | `_from_members` | 已实现，只读兼容 |

两种形状归一成同一份运行时成员表（`AbilityDefinition.operations` / `.skills`），字段级校验只写一份（`ability/loading/ability_schema.py`）。已退役的草案版式与 `0.2` 在解析期明确拒绝，不提供适配器或迁移旁路。

v2 示例（能力件里的纯技能包）：

```yaml
format: ghrah.ability-definition
schema_version: '2'
identity: {namespace: research, name: research, version: 0.1.0}
title: research
description: 竞品调研流程与资料索引
members:
  - namespace: research
    name: research
    member: research.research.research
    type: skill
    version: 0.1.0           # 内核件恒 null；能力件必填 SemVer
    ability_type: external
    axes: {visible: true, bind: true}
    data:
      title: research
      description: 从问题拆解到资料归档的完整调研流程
      path: research/research/0.1.0/skills/research/SKILL.md
      media_type: text/markdown
      assets: {}
      references: {}
      scripts: {}
```

operation 成员同形状，`type: operation`，`data` 用 `path` + `input_schema` + `output_schema` + `execution` 等（见 §3.3）；`mcp` 成员用 `server` / `tool` 取代 `path`。

### 3.3 成员 `data` 契约

必填键与允许键按类型固定（`ability/member.py` `_REQUIRED_DATA` / `_ALLOWED_DATA`），缺一即拒绝、多一即拒绝：

| 类型 | 必填 | 可选 |
| --- | --- | --- |
| `operation` | `title`、`description`、`path`、`input_schema`、`output_schema`、`execution` | `tags`、`effects`、`permissions`、`resources`、`expose` |
| `skill` | `title`、`description`、`path`、`media_type` | `tags`、`assets`、`references`、`scripts` |
| `mcp` | `title`、`description`、`server`、`tool`、`input_schema`、`output_schema`、`execution` | 同 operation，但用 `server` / `tool` 取代 `path` |

`path` 是**相对 ability 根**的路径，自带 `<namespace>/<name>[/<version>]/` 前缀（`Member.logical_prefix`）。operation 的 `path` 必须是 `operations/<成员键>.py`（`definition_from_data` 校验），skill 正文必须是 `skills/<成员键>/SKILL.md`（`_from_members` 校验）。

`expose` 是「该成员有无工具形态」的**唯一权威**，`False` 表示只在内部可用（内置 `conversation` 即如此）。`execution` 是 `ExecutionSpec`：`mode=request`、`timeout_ms>0`、`cancellation`、`on_revoke`、`retry=never`；v1 未声明时回落 `DEFAULT_EXECUTION`。

### 3.4 校验与错误

入口 `parse_definition(raw, *, source)` → `definition_from_data`（`ability/loading/ability_schema.py`）。YAML 走 `_UniqueLoader`：**拒绝重复键与 `<<` merge key**。`origin: mcp` 要求 `namespace=mcp`、必须具名、只含非空 operation 快照、成员绑定包服务器且不得声明 `entry`、不允许 `skills`；`origin: source` 时任何 `mcp` 声明都拒绝。包必须至少一个成员。错误类型统一 `ManifestValidationError`。

### 3.5 内核件与能力件的差异

| 维度 | 内核件（kernel） | 能力件（external） |
| --- | --- | --- |
| 位置 | `ability/builtin/<name>/`，随源码分发 | `<install_root>/<ns>/<name>/<version>/` |
| 版本 | yaml 有 `identity.version`，但成员 `version` 恒 `null`，不参与选择 | 成员 `version` 必须等于 `identity.version` |
| 安装记录 | 无 | 版本目录旁 `<version>.lock.yaml` |
| `data.path` 前缀 | `<ns>/<name>/` | `<ns>/<name>/<version>/` |
| 来源判定 | 位置（源码树） | 位置（安装根），同身份时安装根优先 |

路径规则与落位细节见 `ghrah-12` §1–§4；本节只声明「同一套成员契约、两套落位」。

## 4 成员实体

### 4.1 `Member` 与两种形态

`Member`（`ability/member.py`）是 Core 内部**唯一**的成员数据实体（`frozen`），字段：`namespace` / `name` / `member`（全名）/ `type` / `version` / `ability_type` / `axes` / `data`。派生：`key`、`package_full_name`、`logical_prefix()`、`package_relative_path()`、`resolve_path(builtin_root=, install_root=)`。

- **定义态**（`data` 非空）：来自 `ability.yaml` / 版本画像 / 实例快照，构造期按 `type` 校验必填键与未知键。
- **引用态**（`data` 为空）：只记录「哪个包的哪个成员、什么类型、什么轴」，用于模板勾选与授权申请；`external` 引用态可暂不写 `version`。

### 4.2 `MemberAxes`：可见与绑定

| visible | bind | 合法 | 语义 | 装配结果 |
| --- | --- | --- | --- | --- |
| `true` | `true` | ✅ | 已绑定 | schema 常驻模型上下文 |
| `true` | `false` | ✅ | 可发现未绑定 | `search_tools` 可找到、`execute_tool` 可调用；可 `bind_member` 装上 |
| `false` | `false` | ✅ | 隐藏 | 不进目录、不可发现 |
| `false` | `true` | ✅ | 常驻但不进发现面 | schema 常驻上下文，但不出现在 `search_tools` 结果里（内置 `search_tools` 创建时固定此状态） |

`MemberAxes.from_wire` 过渡期把历史字段 `available` 当 `bind` 读，也接受直接写 `bind`；`to_wire` 只写 `{visible, bind}`。注意**同名不同义**的另一套轴：关系库 grant 的 `axes` 是 `{visible, available}`，`available` 表示「可执行」（§6.3、§13.2 P-1）。

### 4.3 全名、退化包与地址

规范形态三段 `<namespace>.<ability_name>.<member_key>`。`identity.name` 省略的退化包，定义面历史给两段名 `<package>.<key>`（如 `research.research`），模板/快照用三段规名；`Member` 两种都接受（`canonical` / `legacy` 判断）。Core 在三处按等价形态兼容：成员解析回退两段、安装记录回退 `namespace`、绑定集合等价比较（`surface/runtime.py` `_member_forms`）——兼容层而非新约定，长期方向是 `identity.name` 恒有值（`ghrah-11` §3.1/§11、`ghrah-12` §2.1）。

成员键字符集：operation `^[a-z][a-z0-9_]{0,63}$`；skill `^[a-z0-9]+(?:-[a-z0-9]+)*$`（允许连字符），都禁止含 `.`。

授予事实按**地址**索引（`GrantFacts`）：

| 成员 | 地址 |
| --- | --- |
| operation | `operations/<key>` |
| skill 正文 | `skill/<key>` |
| skill 脚本 | `skill/<key>/scripts/<script>` |
| skill 附件 / 参考 | `skill/<key>/assets/<name>`、`skill/<key>/references/<name>` |

operation 地址带复数前缀、skill 地址是单数——既有契约，不要"统一"。模型侧地址是另一套：技能正文的 `read:` 地址是 ability 根相对路径（§8.2），脚本调用地址是 `skills/<skill>/scripts/<key>`（§9.6）。

## 5 加载与提交

### 5.1 扫描根

`ability/loading/ability_roots.py` 是「包在哪里」的唯一权威：

| 根 | 位置 | rank | source | required | 可写 |
| --- | --- | --- | --- | --- | --- |
| 内置树 | `importlib.resources.files("ghrah.ability.builtin")` | 0 | `builtin` | 是 | 否 |
| 外部目录 | 调用方显式传入（`path_root`） | 100 | `skill-ability` | 否 | 是 |

`default_pool()` **只含内置树**（模块 docstring：「默认只扫描随产品交付的内置树，外部目录由调用方显式传入」）。没有用户级默认扫描根，也没有环境变量可改扫描根；能力件经 `CoreUnitConfig.ability_install_root` → `AbilityAssembly.select(install_root=...)` → `BuiltinAbilitySource` → `InstalledAbilityProvider` 进入装配（§12.1）。当前内置树 6 个包（`ability-builder`、`cluster`、`core`、`fs`、`mcp`、`shell`），27 个 operation、1 个 skill。

### 5.2 扫描

| 入口 | 语义 | 失败行为 |
| --- | --- | --- |
| `scan_ability_definitions(pool=None)` | 全池扫描，返回扁平结果 + 问题清单 | 单包问题记入 `issues` 后继续 |
| `scan_abilities(pool=None)` | 同一趟扫描，按容器聚合 | 同上 |
| `load_ability_definitions()` | 实现面：只读内置树、严格抛错 | 第一条问题即抛 |
| `scan_catalog()` | 能力 + handler + 容器 + skill + 问题的合并报告 | 诊断用途 |

算法要点（`ability/loading/ability_scan.py`）：池按 `(rank, label)` 升序，每根只 `iterdir()` 一层不递归；忽略非目录、`_`/`.` 开头、无 `ability.yaml` 的项；解析失败记 issue 并继续；**容器名取 `definition.container_name`**，目录名只作定位（两者不等不再报错，旧不变式已撤销）；同名容器先出现者胜、后来者记一条忽略；skill 正文逐段 `joinpath` 且拒绝 `..`，正文不可用的技能不进清单；根存在但不可列举时抛 `OSError`（环境问题不吞）。`lock_issues` 与 `issues` 分开：前者只表示「漂移判定不可用」，成员仍可加载。`load_ability_definitions` 的 docstring 仍把「容器名与目录名不一致」列为抛错原因，实际该情形已不再报错，以代码行为为准。

### 5.3 启动提交（唯一把来源写进库的路径）

启动核定把安装目录/内置树提交进 SQLite，之后运行期只读库（时序与表结构见 `ghrah-13` §2.2）：

| 步 | 调用 | 作用 |
| --- | --- | --- |
| 1 | `store.startup_registration(actor, agent)` | 库级独占占位；失败只回滚**本次拥有**的修订 |
| 2 | `source.capture_package(package)` → `capture_ability_version` | 读包根，算整树摘要 + 定义 + 实现标识 + 内容清单 + 内容字节 |
| 3 | `store.commit_version(actor, version)` | 校验摘要自洽后落 `ability_versions` / `ability_content`；同 digest 幂等 |
| 4 | `store.select_instance(actor, instance, digest, root=..., invalidate_grants=...)` | 选定实例版本槽；版本/根变化默认撤销旧授予 |
| 5 | `store.write_grant(actor, agent, request)` | 写入授予事实（成员集合、内容白名单、根、轴、来源） |
| 6 | `release_registration(actor, agent)` | 解除占位，持久授权保留 |

`commit_version` 要求 `version.expected_digest() == version.digest`，清单键集合与定义里声明的 skill 正文/资源逐一对应，内容 blob 按摘要与长度复核，任一不符即 `invalid_change`。`select_instance` 是包级单版本槽（`id` 由包全名派生，点换横线），CAS 用 `expected_revision`；无启动策略的存量模式必须传 `invalidate_grants=False`，否则唯一重建源（库里的授予）会被删掉。`capture_package` 对内置包读 `builtin/<container>/ability.yaml` 后算整树摘要，能力件先过 lock 校验，`ghrah.*` 不在登记表时拒绝（避免把后来塞进安装根的同名包当内置来源）。

## 6 授权与数据权威

### 6.1 关系库

库文件为 `<project_root>/db/action-chains.sqlite3` 的 `ability_*` 表，schema 版本 5（`_SCHEMA`，`ability/access/store.py`）：

| 表 | 主键 | 语义 |
| --- | --- | --- |
| `ability_versions` | `(project, digest)` | 一次提交：规范化定义 JSON、实现摘要、内容清单 |
| `ability_content` | `(project, digest)` | 内容字节（正文/附件/脚本），按摘要不可变 |
| `ability_instances` | `(project, id)` | 项目内一个包槽的当前版本摘要与实例根 |
| `ability_agents` | `(project, id)` | Agent 的授权修订计数 |
| `ability_grants` | `(project, agent, alias)` | 授予：成员集合、根、内容白名单、轴、来源 |
| `ability_tools` | `(project, agent, name)` | 已绑定为常驻工具的成员（工具名 → alias/member/修订） |
| `ability_changes` | `seq` | 变更审计：修订、原因、操作者、版本摘要、策略 |
| `ability_registrations` | `(project, agent)` | 启动独占占位：token、状态、拥有的修订 |

### 6.2 写路径

| 写操作 | 入口 | 语义 |
| --- | --- | --- |
| 授予 | `grant` / `write_grant` | 授予集合是**授权时刻的快照**；相同事实不产生新历史 |
| 撤销 | `revoke` / `write_revoke` | `members=None` 整条撤销；否则按地址移除（skill 地址按前缀移除其下内容） |
| 绑定工具 | `bind_tool` / `write_bind_tool` | 只能从本 Agent 当前获授集合选择，不能增加授权 |
| 解绑工具 | `unbind_tool` / `write_unbind_tool` | 删除选择，保留授予 |

全部写操作要求 `expected_revision` 与当前修订一致（CAS），冲突返回 `conflict` 而**不静默覆盖**；写入来源记入 `ability_changes.source`（`admin` / `deployment` / `agent-default` / `migration` / `assembly`）。`write_grant` 还校验：成员属于该版本且 `expose`、内容地址在清单里、文件类成员必须有落在实例根内的授予根。

### 6.3 `_check`：每次读取与调用的复核链

`store._check(actor, fence, arguments=None)` 是一次读取/调用的**唯一**授权复核，按序检查：

| 序 | 检查 | 失败码 |
| --- | --- | --- |
| 1 | 实例行存在且 `revision` 等于 fence 捕获值 | `access_denied`（`version_changed`） |
| 2 | 版本摘要与 fence / 实例一致，且版本仍可用 | `access_denied` / `unavailable` |
| 3 | 内容成员在清单里且不携带工具名；operation 存在且 `expose` | `access_denied` |
| 4 | 关系库身份：Agent 行存在、run 与调用方一致；操作员身份不能代替 Agent 自身 | `access_denied` |
| 5 | grant 行存在、`revision` 一致、成员在 `members` / `content` 里 | `access_denied`（`grant_revoked`） |
| 6 | operation 的轴 `visible` 与 `available` 均为真 | `access_denied` / `unavailable` |
| 7 | 工具行存在且 `revision`、`grant_revision`、`alias`、`member` 全一致 | `access_denied`（`tool_revoked`） |
| 8 | 参数接纳：`admission_for(definition, member)` 按契约核资源范围 | `access_denied`（`arguments_out_of_scope`）/ `invalid_change` / `unavailable` |

返回 `"<版本摘要>:<grant 修订>:<tool 修订>"` 作为接纳依据。第 8 步的后端参数检查在 `ability/access/backends.py`：文件成员逐路径核「在实例根内且在授予根内」、命令成员核 `command` / `working_dir`、集群成员核身份参数、MCP 成员核 `server` / `tool` 与 `arguments` 形状、core 成员核显式声明的参数形状；未登记后端一律 `unavailable`。

### 6.4 撤销语义与变更审计

- **按地址撤销**：`write_revoke` 先校验目标地址在本条授予里再移出事实；事实清空则整条 grant 删除，并把 `ability_tools` 里指向被撤成员的绑定一并删除。
- **版本切换撤销**：`select_instance` 在版本/根变化时对受影响 grant 逐一 `_bump` + `_remove_grant`（`invalidate_grants=True`，默认），并打 warning。
- **立即生效**：不需要重启——每次调用都走 `_check`，撤销后当轮即拒；工具名列表由 `tool_table_projector` 每轮重算，撤销成员从下轮模型视野消失。
- **`ability_tools` 不是授权**：只表示「选择」；`bind_member` 只能绑定已获授且可见成员，解绑不影响授予。
- **`ability_changes`**：每次 `_bump` 追加一行（`reason`、`actor`、`before_revision`、`version_digest`、`source`、`policy`）；`store.changes(after_seq, limit)` 提供有界增量读取，但**不是**重建来源——完整重建永远读权威表。

## 7 工具面

### 7.1 目录来源

```text
store.surface_snapshot(actor, agent)
  └─ 同一读事务：ability_grants → ability_instances → ability_versions.definition
       └─ view() 授权投影（AbilityTreeView）
            ├─ project_grants(view)   → 工具条目（bound_tools ∩ operations）
            └─ _skill_entries(...)    → 技能条目（body_allowed 的技能）
  └─ catalog_from_view(view, definitions) → SurfaceCatalog（tools + skills）
```

`store.surface_snapshot`（`ability/access/store.py`）在一次读事务内完成授权 → 版本 → 定义；`catalog_from_view`（`ability/surface/catalog.py`）把投影裁成目录，**不扫安装目录**；`read_agent_catalog` 额外核对 `agent` / `run` 与当前运行代次一致，跨 run 或跨 Agent 的快照一律拒绝。

### 7.2 静态集与长尾

`assemble_surface`（`ability/surface/assembly.py`）把可执行授权目录分成 `static` 与 `long_tail`：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `DEFAULT_DEFINITION_TOKEN_RATIO` | 0.02 | 定义 token 占上下文窗口比例上限 |
| `DEFAULT_MAX_STATIC` / `DEFAULT_HARD_CAP` | 20 / 30 | 静态工具数上限 / `auto` 切渐进的总成员数上限 |
| `STATIC_ABS_LIMIT` | 64 | 显式 `static` 的固定绝对上限，超过即装配期报错 |
| `DEFAULT_SEARCH_LIMIT` / `SEARCH_LIMIT_ABS_MAX` | 8 / 20 | 搜索结果上限（硬上限不开放配置） |
| `DEFAULT_LOADED_LIMIT` | 8 | 已加载集上限 |

`mode=auto` 在「定义 token 超预算、成员数超 `max_static`、超 `hard_cap`」任一成立时切渐进；`mode=progressive` 只保留显式成员；显式 `static` 按包定义全名解析，未知/保留名/多实例歧义均拒绝。token 估算走 `definition_tokens`（与生产工具表同形状的完整 JSON），预算扣除已占用的 `reserved_definition_tokens`。

### 7.3 两轴决定通道

`ToolSurfaceRuntime.refresh`（`ability/surface/runtime.py`）按绑定轴分流：

| 状态 | 通道 | 模型体验 |
| --- | --- | --- |
| `visible` + `bind` | 工具面静态集 | schema 常驻，可直接调用 |
| `visible` + 未 `bind` | 工具面长尾（`progressive`） | 不在列表里；`search_tools` 可发现、`describe_tool` 看 schema、`execute_tool` 可调，也可 `bind_member` 装上 |
| 隐藏（`visible=false`） | 不出现在任何目录 | 不可发现；`_check` 对该 operation 直接拒绝，因此也不可调用 |
| 控制成员 | 独立 control 通道 | `conversation` / `end_task` 由循环驱动，不受两轴影响 |
| 脚本 | 独立 `run_script` 入口 | 存在任何 `available` 脚本时才装配 |

`run_script` 与 `META_TOOL_NAMES`（`search_tools` / `describe_tool` / `execute_tool` / `bind_member` / `unbind_member`）永远走 `extras` 通道，不能出现在 `static` 里（装配会拒绝保留名）。

### 7.4 工具名算法

`assign_tool_names(members, allow_reserved=...)`（`ability/surface/naming.py`）是纯函数，输入 `(包名, 成员键)` 序列，输出按工具名升序：

1. 成员键唯一时 `tool_name = 成员键`；
2. 同一成员键多次出现时全部改为 `<包名>__<成员键>`（两段里的 `-` 换成 `_`）；
3. 超过 64 字符取前 54 字符 + `__` + 候选名 sha256 前 8 位十六进制；
4. 与保留名冲突即拒绝（`allow_reserved` 只放行 core 元工具名）；
5. 最终仍冲突即报错，**不静默改名**；输出确定，与输入顺序无关。

保留名：`search_tools` / `describe_tool` / `execute_tool` / `bind_member` / `unbind_member`（core 占用）+ `run_script`（表面层占用）。工具名是模型可见别名，`tool_name → (alias, member)` 的映射是唯一权威；执行边界把调用改写回装配时固定的成员引用（§9.3），不从工具名反推模块或服务器地址。

### 7.5 元工具与渐进披露

披露文案由 `DISCLOSURE_GUIDANCE`（`ability/surface/runtime.py`）固定：先 `search_tools` 发现、再 `describe_tool` 看 schema，之后可直接调用，`execute_tool` 亦可用；**发现永不产生新授权**。检索是授权域内的确定性关键词检索（`ability/surface/search.py`）：索引只保存名字/成员/包/一行摘要/标签/副作用，**不保存 schema 与磁盘路径**；分桶加权 TF-IDF，同分按工具名排序；索引随授权修订与实例版本变化重建，读取失败即丢弃缓存并拒绝。`describe_tool` 返回目标输入/输出 schema 与当前 `available`，并把目标加入已加载集（受 `loaded_limit` 约束，LRU 淘汰）。

## 8 技能面

### 8.1 每轮注入与按轴分流

`ToolSurfaceRuntime.skill_reminder` 每轮从库重建目录，再交给 `next_skill_reminder`（`ability/loading/skill_context.py`）渲染：

| 情形 | 注入文本 |
| --- | --- |
| 有技能 | 每轮给完整清单；清单摘要变化用「完整替换」口径，否则用介绍口径（文案稳定，利于前缀缓存） |
| 无技能且从未注入 | `None`（不占上下文） |
| 从有到无 | 注入一次「已清空」提醒，之后不再注入 |

目录文案是 `<system-reminder>` 包裹的 `<available_skills>` 块，单条描述空白折叠后截断到 500 字符（`CATALOG_DESCRIPTION_MAX_CHARS`），尖括号转义。摘要只取「名字 + 截断描述 + `read` 地址」，正文变化不改摘要——正文编辑不会误触发重注入。

绑定集合来自实例快照（`_bound_names` 取 `resolved.member_axes` 里 `bind` 为真的成员）；`None` 表示调用方没有提供成员轴（旧路径），此时按「全部可见技能都常驻」处理。分流：

| 状态 | 目录 | 提示 |
| --- | --- | --- |
| `bind=true` | 正式目录条目 | 无 |
| `visible=true, bind=false` | 不进正式目录 | 一行「可用但未绑定」提示，列出目录名并给出 `bind_member` 入口 |
| `visible=false` | 完全不出现 | 无 |

技能成员没有工具形态，但保留在绑定集合里供 `skill_reminder` 消费；集合修剪按等价形态判断，避免纯技能包被误删。

### 8.2 目录条目、`read` 地址与纯技能包

`SkillSurfaceEntry`（`ability/surface/catalog.py`）是「成员记录 + 目录名」：`name` 是 `<alias>/<key>`（`catalog_name`），`member` 是统一 `Member` 实体。条目文本形如：

```text
- `research/research` (read: `research/research/0.1.0/skills/research/SKILL.md`): 竞品调研流程
```

`read` 地址是正文在 ability 根下的相对路径（能力件带版本段，内核件不带）。`load_skill` 接受目录名、裸键或该地址三种写法；`store.resolve_address` 只在**当前 Agent 已授予实例**里按声明路径逐字匹配，命中后仍走 `_resolve` 的授权与版本校验——地址不是绕开授权的新入口（`ghrah-11` M-14）。二义时拒绝而不是猜。

技能条目**按包节点独立收集**，不挂在工具循环里：纯技能包（0 个 operation）没有任何工具，但它的技能必须进目录（`catalog_from_view` 显式注释这一点）。正文读取走 `ABILITY_CONTENT` 服务（`LocalGrantedContentProvider`），输出成员地址、包内相对路径、`media_type`、`digest`、`total_bytes` 与正文，不暴露宿主绝对路径。

## 9 执行

### 9.1 `Ability` 契约与调用对象

`class Ability(ABC)`（`ability/execution/base.py`）：必须实现 `name`（property）、`async execute(call, services) -> ActionResult`、`get_hooks()`；可选覆盖 `bind_tool()`（默认 `None`）、`to_prompt_description()`、`get_default_state()`；实现脚本还可提供异步 `prepare_arguments(call)`，执行器与投影层都会在 Hook 之前调用它。签名固定为 `execute(call, services)`——没有 `context` 门面，参数只有 `call.arguments` 一个来源。

| 对象 | 内容 | 位置 |
| --- | --- | --- |
| `MemberReference` | `instance_id` / `alias` / `member` / `version_digest` | `ability/execution/invocation.py` |
| `InvocationIdentity` | `project_id` / `agent` / `run` / `agent_name`（装配者绑定，不从参数读） | 同上 |
| `OperationCall` | `member` / `arguments` / `tool_call_id` / `identity` / `arguments_prepared` | 同上 |

`ExecutionServices` 是执行期依赖的**唯一**形态（`ability/execution/execution_services.py`）：新增依赖加服务键、不加调用对象属性。9 个键：`CONTEXT_MANAGER`、`SUPERVISOR`、`LOOP`（`LoopData`）、`MANIFEST_STORE`、`MCP_RUNTIME`、`ABILITY_CONTENT`、`TOOL_SURFACE`、`MESSAGE_FACTORY`、`HOST_COMMAND_PORT`。`build_execution_services(...)` 是所有执行入口共用的组装函数；漏传即 `None`，由使用方显式报错（元工具会返回 "Tool surface service is not wired"），不做隐式默认对象。

### 9.2 `ProjectedAbility` 的三段

`ProjectedAbility`（`ability/access/startup.py`）是模型可见工具与本地执行实例之间的唯一包装：

| 段 | 方法 | 做什么 |
| --- | --- | --- |
| 固定 | `prepare_call(call)` | 把成员引用与受信身份替换成启动时捕获的事实；参数无权覆盖 |
| 预检 | `check_call(call, services)` | 未绑定 run 即拒；run 不是当前代次即拒；`Input` 模型校验失败即 `Invalid arguments`；再调内层 `check_call`（如 `PermissionDeniedAbility`） |
| 执行 | `execute(call, services)` | 再 `prepare_call` / `check_call` → `prepare_arguments` → `admission`（`store.admit_tool`）→ 内层 `execute` |

接纳在 HITL 钩子之后运行：审批只决定「范围外的调用要不要问人」，关系与授予范围的复核**不能被审批跳过**——撤销、改绑、越界在此最后兜底，失败时无副作用。`get_hooks()` 用 `_ProjectedHook` 把内层 Hook 绑定到本次物化的实例与成员，避免同一 Agent 的其他文件工具拿自己的检查器审查本成员。

### 9.3 一次调用的时序

`LocalAbilityExecutor.execute_ability`（`ability/execution/executor.py`）：

1. `prepare_call`（若有）→ `check_call`，被拒即返回 `FAILURE`；
2. `prepare_arguments`（若有）；
3. `PRE_EXECUTE`：`HookRunner.run` 选中并合并 Hook；
4. 分派：`requires_hitl` → 审批等待；`not should_continue` → `FAILURE`；`route_to` → `DELEGATE`；
5. `await ability.execute(call, services)`；
6. `data["await_reply"]` 为真时登记延迟调用并**在 POST_EXECUTE 之前**返回（`finish_deferred` 只重放 POST_EXECUTE）；
7. `POST_EXECUTE`（带结果），返回值丢弃；
8. 原样返回 `ActionResult`。

`execute_tool_calls` 对已知工具用 `asyncio.gather(..., return_exceptions=True)` 并行执行；非 `Exception` 的 `BaseException`（含取消）**原样重抛**，`Exception` 转成 `FAILURE{str(e)}`；未知工具名立即记 `FAILURE{"error": "Unknown ability: <name>"}`。

### 9.4 参数校验的位置

| 位置 | 校验 | 失败表现 |
| --- | --- | --- |
| `ProjectedAbility.check_call` | 内置件用实现脚本的 `Input` 模型（启动时 `import_module` 取 `Input`） | `Invalid arguments; check describe_tool's input_schema` |
| `MetaToolAbility.execute` | 元工具自己的输入模型 | `Invalid meta-tool arguments` |
| `store._check(..., arguments)` | 后端资源范围（路径/命令/身份/`server`+`tool`） | `access_denied` / `invalid_change` / `unavailable` |

工具 schema 与描述一律取自**已提交版本的定义面**（`operation["input_schema"]` / `operation["description"]`），不从实现脚本二次读取；构建期生成器从实现脚本的 `Input`/`Output` 生成定义（§15.1），两侧因此同源。

### 9.5 HITL 与硬拒绝

`handle_hitl_hook_result`（`ability/execution/executor.py`）走 `HITLFutureStore` + `HITLRequestEvent` / `HITLResolvedEvent` + promise registry，终态三种：`approved`（复位连续超时计数并继续）、`rejected`（`FAILURE`，`data` 含 `hitl_rejected`、`promise_id`、`tool_call_id`，消息带审批人理由）、`timeout`（`FAILURE`，`data` 含 `hitl_timeout`、`hitl_degraded` 与归因字段）。**超时不得折算成用户拒绝**（注释明写）。连续超时熔断默认关闭（`hitl_consecutive_timeout_limit=0`）；开启后第 N 次起等待降级为 `hitl_degraded_timeout`，文案提示收敛并改调 `end_task`。

`PermissionDeniedAbility`（`ability/permissions/hitl.py`）是硬拒绝：`check_call` 直接返回 `FAILURE`，**不进审批**，也不执行副作用。

### 9.6 `run_script`

脚本不是 operation 成员，不走 `execute_command`。链路：`RunScriptTool`（`ability/surface/runtime.py`）→ `ToolSurfaceRuntime.run_script` → `execute_run_script`（`ability/scripts/runner.py`）。地址是模型侧 `skills/<skill>/scripts/<key>`，与授予侧 `skill/<skill>/scripts/<key>` 由 `resolve_script` 对应；起进程前完成「地址越界、未授权、脚本 `available` 为假、语言不对、参数不是对象」检查；工作目录取本次授予实例的根，不从参数解释路径；默认预算 120 秒；结果 `action` 恒为 `script.call`，stderr 只进日志。

## 10 Hook

### 10.1 触发点与契约

`HookPoint`（`ability/execution/hooks.py`）共 10 个：drive_loop 层 `BEFORE_ACTION` / `AFTER_ACTION` / `ON_ERROR` / `ON_MAX_ITERATIONS`（驱动循环发），action 层 `PRE_LLM_CALL` / `POST_LLM_CALL` / `PRE_TOOL_EXECUTE` / `POST_TOOL_EXECUTE`（驱动循环发），ability 层 `PRE_EXECUTE` / `POST_EXECUTE`（`execute_ability` 发）。权限与 HITL Hook 全部挂 `PRE_EXECUTE`。

`class Hook`：类属性 `hook_point`、可选 `scope: HookScope | None`；抽象 `async should_trigger(call, services) -> bool` 与 `async execute(call, services, result=None) -> HookResult`。Hook 与 Operation 看到**同一份**调用对象与服务表：成员从 `call.member` 读、参数从 `call.arguments` 读、依赖从 `services` 取。`HookScope`（frozen dataclass）声明 `points` 与可选 `target_abilities`，`points` 为空或 `target_abilities == frozenset()` 直接 `ValueError`；`scope is None` 的 Hook 是 legacy（每次都要 `should_trigger`），声明了 `scope` 的由索引直接选中并跳过 `should_trigger`。

### 10.2 `HookResult` 与执行顺序

| 工厂 / 字段 | 语义 | 执行器行为 |
| --- | --- | --- |
| `continue_()` | 全默认 | 继续 |
| `stop(message=)` | `should_continue=False`，`requires_hitl=False` | 立即 `FAILURE` |
| `hitl(message=)` | `should_continue=False`，`requires_hitl=True` | 进入审批等待 |
| `route(target)` | `route_to` | `DELEGATE` + `next_action_hint` |
| `modified_context` / `data_updates` | dict（互为别名） | 合并进 `LOOP` 服务里的循环数据 |

`merge`：`should_continue` 取 AND、`route_to` 后者覆盖、`modified_context` 字典合并且后者覆盖、`message` 后者覆盖、`requires_hitl` 取 OR——一个 Hook 要求停，整条链就是停；一个要求审批，就是审批。

`HookRunner.run(point, call, services, result)`（`ability/execution/hook_runner.py`）是唯一执行路径：`store.select(point, call.member.member)` → 逐个（legacy 先 `should_trigger`）→ `execute` → 依次 `merge` → 若有 `modified_context` 写入 `LOOP`。**没有短路**；异常包成 `HookError` 上抛，最终由 `execute_tool_calls` 转成 `FAILURE`。`HookStore.select` 取 global + legacy + `(point, ability)` 三个桶的并集，再按注册顺序重排。

## 11 权限

### 11.1 三层结构

```text
members[*].data.permissions（包声明）+ 模板成员 permissions（申请）
        ↓ resolve_effective_permission（启动物化时算一次）
  EffectivePermission  →  FSPermissionChecker / CommandSafetyChecker
        ↓ 挂在 PRE_EXECUTE
  AccessApprovalHook / CommandApprovalHook / HITLApprovalHook
        ↓ hitl() → LocalAbilityExecutor.handle_hitl_hook_result
```

`resolve_effective_permission(declaration, request, workspace_root)`（`ability/permissions/scope.py`）的合并规则：

| 维度 | 合并 |
| --- | --- |
| `allowed_paths` | 层间**取交集**；`None` = 未声明范围，空元组 = 声明了但什么都不自动批准 |
| `denied_paths` | 层间**取并集**（硬拒绝只增） |
| `allowed_commands` | 层间取交集（按 base+sub 归一比较，保留更窄一条） |
| `denied_commands` | 层间取并集 |
| `require_hitl` / `fs_read_only` | 取 OR |
| `fs_write` / `net_access` / `shell_access` | 取 AND（申请只能收紧）；`fs_read_only` 生效时强制 `fs_write=False` |

`EffectivePermission.sandbox_roots` 是部署方文件硬边界，缺省用工作区根；文件类成员的授予根由启动核定写入 `allowed_roots`。

实现脚本的 `PERMISSIONS` 常量经构建写进成员 `data.permissions`，它是**申请值（上限）**，不是授权结果：真正生效的授权在 `ability_grants`，模板成员 `permissions` 只能在此上限内收紧（`resolve_effective_permission` 的 `request` 层，`None` 字段表示未申请）。

### 11.2 硬拒绝先于审批

`_REQUIRED_PERMISSION`（`ability/execution/ability_build.py`）声明「成员需要哪项能力」：`write_file` / `edit_file` / `delete_file` / `move_file` → `fs_write`，`execute_command` → `shell_access`。有效范围缺该项时用 `PermissionDeniedAbility` 包一层：`check_call` 直接失败，审批无法覆盖。

### 11.3 FS 判定

`FSPermissionChecker.check_access(path, operation)`（`ability/permissions/fs_permissions.py`）返回二元组：`(True, None)` 自动批准、`(True, "pending")` 待审批、`(False, reason)` 拒绝。判定顺序：只读且写操作 → 拒；沙盒硬边界外 → 拒（**审批也不能放宽**）；命中 denied → 拒；命中 allowed 或工作区内 → 准；否则 `require_approval` → pending，不然拒。

`AccessApprovalHook` 只看能力名（`WRITE_ABILITIES` / `READ_ABILITIES`），逐个检查目标路径（`move_file` 查源与目标、`search_files` 查搜索根、其余查 `file_path` 或 `dir_path`）：任一拒绝即 `stop`，任一 pending 即 `hitl`，否则 `continue_`。

### 11.4 命令分级与三张审批 Hook

`CommandSafetyChecker`（`ability/permissions/command_safety.py`）以 bash-classify 前端解析出的表达式为基础，叠加声明范围与危险名单，产出 `CommandSafetyCategory` 三值：`safe` / `dangerous` / `require_hitl`；`CommandSafetyVerdict` 带 `category`、`base_command`、`sub_command`、`reason`，待审批时附 `shell_remember`（供"记住本次允许"）。`extend_safe_lists` 只并入 safe 集合且小写归一，只在 bash-classify 段级门禁允许的档位内生效，不能抬升未知/危险档。`CommandApprovalHook` 只拦 `execute_command`：SAFE 放行、DANGEROUS 拒绝、其余转 HITL。

| Hook | 管辖 | 触发条件 |
| --- | --- | --- |
| `AccessApprovalHook` | 文件路径范围 | 能力名属于读写文件集合 |
| `CommandApprovalHook` | 命令分级 | 能力名是 `execute_command` |
| `HITLApprovalHook` | 「声明要审批但能力自己不检查」的通用情形 | `call.member.member == 宿主成员名` |

`HITLRequiredAbility` 是**组合**（非继承）：委托内层、把 `HITLApprovalHook` 追加到内层 Hook 之后、其余属性透传。装配优先级链统一为 `auto_approve > 声明的 require_hitl > 兜底默认`。

## 12 装配

### 12.1 来源选择：`AbilityAssembly.select`

`AbilityAssembly.select(mode, *, agent_store=None, install_root=None)`（`ability/assembly.py`）只接受 `builtin` 模式（`AbilitySourceMode.parse` 对其它值直接报错），构造唯一来源 `BuiltinAbilitySource(agent_store=..., install_root=...)`：内置树按包投影，安装根交给 `InstalledAbilityProvider`；同全名在内置树与安装根同时存在时**明确失败**（`_build_origins`），拒绝静默覆盖。`ability_install_root` 由部署方显式给出（`CoreUnitConfig.ability_install_root` 默认 `None`），领域层不拼默认路径。

### 12.2 授权装配边界

`build_startup_materializer(...)`（`ability/access/boundary.py`）构造本次部署**唯一**的 `StartupGrantMaterializer`：顶层 spawn、子 Agent `spawn_agent`、程序化装配都必须经它拿工具表；`SupervisorActor` 在 builtin 模式拒绝非边界产出的工具表（`AuthorizedToolTable`）。启动动作的操作员身份是 `AccessIdentity(project_id, "deployment", "deployment", True, "deployment")`。

`materialize(agent=, resolved=)` 的步骤（`ability/access/startup.py`）：

1. `_split`：按 handler 是否在 `CONTROL_HANDLERS`（`{conversation, end_task}`）且全名以 `ghrah.core.` 开头，分出控制成员与能力包成员；
2. 逐包 `_register`：`capture_package` → `commit_version` → `select_instance`（同版本幂等）；
3. 算目标事实：有策略走 `desired_grants`（模板 × 策略，`ability/access/reconcile.py`），无策略走 `_existing_desired`（只认库内既有事实，不扩权）；两者都为空即 `StartupGrantError`（不回退模板自动授予）；
4. 注入元工具：普通授权成立后才把 `META_TOOL_NAMES` 加进 core 的授予（策略可显式 revoke）；
5. `apply_grants` 写库（CAS，失败即 `StartupGrantError`）；
6. `_bind`：`assign_tool_names` 定名 → 清理陈旧 `ability_tools` → 逐条 `write_bind_tool`；
7. `_instantiate`：为每个绑定成员构造内层实现 + `ProjectedAbility`，附加 `ToolSurfaceRuntime` 与 `RunScriptTool`，返回 `AuthorizedToolTable`。

`materialize_stored` 是恢复分支：只从库内管理员事实恢复，不执行策略写回。

### 12.3 内层实例化与物化包装

`instantiate_resolved_abilities(...)`（`ability/execution/ability_build.py`）按 handler 分派：`conversation` 原样、`end_task` 加 `mode="toolcall"`、文件类挂 `AccessApprovalHook` + `FSPermissionChecker`、`execute_command` 挂 `CommandApprovalHook` + `CommandSafetyChecker`；实现类型为 `python` 的成员（安装包 Operation）改由 `provider.create_operation(...)` 取进程内适配，缺能力时包 `PermissionDeniedAbility`，需审批时包 `HITLRequiredAbility`。非 builtin 且无 handler 只 warning 跳过（不中断整个 spawn）。

`ProjectedAbility` 的 `tool_schema` 直接取已提交定义的 `description` + `input_schema`；`member_reference` 固定实例/别名/成员/版本摘要；`identity` 固定项目/Agent/run/显示名；`admission` 是 `_admission(agent, binding, version_digest)`：比对别名与成员 → 刷新 MCP 后端 → 核对当前 run → `store.admit_tool(..., expected_alias=, expected_member=)` → 版本摘要前缀比对，任一不符给出可读拒绝（`version_changed` / `tool_revoked` / `arguments_out_of_scope` / `stale_invocation`）。`bind_run(run)` 在 Agent 注册完成后补齐受信 run；绑定前 run 为空，接纳层按「旧对象不得借用新 run」拒绝。

### 12.4 控制成员通道与每轮投影

`conversation`、`end_task` 属于成员体系但**不进授权账本**：`_split` 把它们分流到 `control` 列表，`_instantiate` 直接实例化；工具面把它们放进 `controls`（`member_reference is None` 且不是 `run_script`）单独通道，撤销与授权投影都裁剪不到它们。规范见 `ghrah-11` §7。

`tool_table_projector(agent, materialization)` 返回无参异步投影器：每轮从库重算可见工具名，只保留本次实际物化过且仍可用的成员；`AccessError` 时返回空元组（宁可少给，不给过期）。绑定变更由 `set_member_bound` 写实例快照（`MemberBindingState.save` → `AgentInstanceStore.set_member_bindings`），工具成员与技能成员走同一入口；`ability_tools` 由启动核定与管理面 `enable_tool`/`disable_tool` 维护，元工具不直接写它。持久化失败只告警，不回滚内存视图。

## 13 不变式与已知偏差

### 13.1 不变式（可被测试引用）

| 编号 | 不变式 | 校验点 |
| --- | --- | --- |
| I-01 | 运行期只读 SQLite，不读安装目录 | `surface_snapshot` / `resolve` / `read_chunk` 的输入只有库内数据 |
| I-02 | 成员契约唯一来源是已提交定义；工具 schema 不从实现脚本二次读取 | `_instantiate` 取 `operation["input_schema"]` |
| I-03 | 两轴相互独立：`visible` 只决定能否被发现，`bind` 只决定是否常驻上下文；`search_tools` 在实例创建时固定为 `visible=false, bind=true` | `MemberAxes` / `member_axes_for_creation` |
| I-04 | 授权地址规范唯一：`operations/<k>`、`skill/<k>[/<role>/<k>]` | `GrantFacts.__post_init__`（地址全集必须被 axes 覆盖） |
| I-05 | 授权只经关系库写，且必须带 `expected_revision` | `write_grant` / `write_revoke` / `write_bind_tool` |
| I-06 | 版本按 digest 不可变，提交幂等 | `commit_version` 的摘要自洽校验 |
| I-07 | 撤销与改绑在下次调用即生效（fail-closed） | `_check` 每次读库比对修订 |
| I-08 | 工具名与成员的映射唯一且确定，冲突不静默改名 | `assign_tool_names` |
| I-09 | 保留名不可被任何包占用（core 元工具名仅 core 可用） | `RESERVED_TOOL_NAMES`、`_bind` 的别名校验 |
| I-10 | 控制成员不进授权账本，也不被授权投影裁剪 | `CONTROL_HANDLERS`、`controls` 通道 |
| I-11 | 权限拒绝不抛异常，一律 `ActionResult(FAILURE, {"error": …})` | `PermissionDeniedAbility`、`_denial` |
| I-12 | 取消（非 `Exception` 的 `BaseException`）必须原样重抛；超时不得折算为用户拒绝 | `execute_tool_calls`、`execute_ability` 的 timeout 分支 |
| I-13 | 工具目录可从库完整重建，不缓存跨事务结论 | `catalog_from_view` + `read_agent_catalog` |

### 13.2 已知偏差（按 `a742fcf` 重测）

| 编号 | 偏差 | 证据 | 影响 |
| --- | --- | --- | --- |
| P-1 | 两套同名 `axes` 语义不同：成员侧 `{visible, bind}`，授权事实侧 `{visible, available}` | `ability/member.py` `MemberAxes` vs `ability/access/facts.py` `GrantFacts` | 跨层阅读易把"绑定"当"可执行" |
| P-2 | 已修复：两轴解耦后，`Member.reference` 不再声明「`bind` 必须与 `visible` 一致」 | `ability/member.py` 的 `reference` | — |
| P-3 | `execution.timeout_ms` 对能力件生效（`asyncio.timeout`），对内核件不生效 | `ability/package/provider.py` `PythonOperation.execute` vs `ability/execution/executor.py` | 同一字段两种运行行为 |
| P-4 | `surface_snapshot` 读的是 `include_unavailable_bindings=True` 的投影，不可用成员仍在目录里（`available=false`），必须由上层过滤 | `store.surface_snapshot` / `runtime.refresh` | 漏过滤会让模型看到不可执行工具 |
| P-5 | 退化身份包成员名两段/三段并存，靠三处等价兼容维持 | 解析回退、安装记录回退、`surface/runtime.py` `_member_forms` | 兼容层与长期债务（`ghrah-11` §11、`ghrah-12` §11、`ghrah-13` §6.2） |
| P-6 | 定义面仍接受 `schema_version: '1'`（只读），两条解析路径并存 | `ability_schema._from_members` vs `_members_list_to_tables` | 新增字段必须两处同步，否则 v1 存量读不出 |
| P-7 | 技能存在两套名字：目录名 `<alias>/<key>` 与成员全名 `<package>.<key>` | `ability/surface/catalog.py` `SkillSurfaceEntry` | 调用方必须明确用哪套，不能混用 |
| P-8 | 多个服务键允许 `None`，使用方必须显式报错 | `build_execution_services` | 漏接线是运行期 `FAILURE` 而非装配期错误 |

构建严格 / 安装宽松的口径差、`.zip` 不能直接安装、内核件仍写 `identity.version` 等偏差已在 `ghrah-12` §11 记录，本文不重复。

## 14 旧概念对照

| 旧概念（旧 `scheme` 布局 / v1 设计） | 现状 | 当前对应物 |
| --- | --- | --- |
| `ghrah.scheme.loading.tool_list` 与它的工具/条目对象 | **模块已删除**；运行期不扫盘生成工具表 | `ability/surface/`（目录 ← `store.surface_snapshot`） |
| 「零缓存扫盘」查询链（每次调用重扫目录） | 运行期主链**不再扫盘**；库投影替代 | `catalog_from_view`、`read_agent_catalog` |
| v1 提供者槽声明（`implementation.provider_slot`、`required_capabilities`、`implementation.operation`） | 字段已无 | 契约由来源与身份推导：`provider_contracts.contract_for(origin, ns, name)` |
| 顶层 `assets` | 字段已无 | 资源归属各自的 skill：`assets` / `references` / `scripts` |
| 「一个包至多一个 skill」 | **已实现 0..N**：成员是列表，技能各有目录 | `members` 列表 + `skills/<key>/SKILL.md` |
| 「`SKILL.md` 无 frontmatter」 | frontmatter **被读**：`name` 必须等于成员键，构建/安装期校验 | `package/generate.py` `_parse_frontmatter`、`ability/content.py` |
| 「同一 operation schema 写两遍，以实现面为准，手工同步」 | **已收敛**：生成器从实现脚本常量 + `Input`/`Output` 生成定义；运行期只用已提交定义 | `package/introspect.py`、`package/generate.py`、`_instantiate` |
| 旧执行上下文对象（`context.xxx` 门面） | 已删除 | `OperationCall` + `ExecutionServices` |
| 旧数据总线（键值容器）与预置结果键 | 已删除 | 循环数据在 `LOOP` 服务里的 `LoopData` |
| 旧执行帧 / Hook 上下文双模型 | 已删除 | Hook 直接收 `(call, services, result)` |
| 旧的技能提供服务键 | 已删除 | `ABILITY_CONTENT` |
| 装配点 A「按定义创建能力」+ 两条路径差异 | 函数不存在 | 单一路径：`StartupGrantMaterializer.materialize` |
| 旧用户级扫描根与对应环境变量开关 | **已退役**；默认池只有内置树 | 外部包走 `ability.install_root` → `InstalledAbilityProvider` |
| `manifest: ability` + `version: "1"` 的旧清单形状 | 不再是能力包写法 | `ability.yaml` + `schema_version: '2'` |
| `list_abilities` | **已删除**（模型侧不再有全量清单工具） | 发现面由 `search_tools`（可见未绑定成员）与 `load_skill`（技能目录/正文）承担 |
| 旧 §13.2 的 D-1…D-20 表 | 已按当前代码重测，收敛为 §13.2 的 P-1…P-8 | — |

## 15 构建、安装与导入

### 15.1 构建（严格）

入口 `ghrah ability build`（`ability/tools/cli/__init__.py`，verbs = `build` / `install` / `list` / `show` / `verify` / `refresh` / `uninstall`）→ `package/generate.build/check`：

1. 读头段：保留 `format`/`schema_version`/`identity`/`title`/`description`，拒绝未知字段与非 `'1'`/`'2'` 版本；
2. 子进程 import 每个 `operations/<key>.py`，只取模块常量（`TITLE`/`SUMMARY`/`TAGS`/`PERMISSIONS`/`EFFECTS`/`TIMEOUT_MS`/`RESOURCES`/`EXPOSE`）与 `Input`/`Output` 模型，**不执行 `execute`**（`package/introspect.py`）；
3. 生成 `members`（键升序、字段顺序固定）→ 渲染 v2 文本 → 用**运行时解析器**自检读回 → 同目录临时文件 + `os.replace` 原子写；
4. 可移植性严格校验（`package/paths.py` `ensure_portable_tree`）：文本必须 LF、UTF-8 无 BOM、拒绝符号链接/非普通文件/保留名/超长路径；
5. `--check` 不写盘，差异非零退出并列出差异（CI 门禁）。

同一输入两次构建字节相同；生成器只写 `schema_version: '2'`。

### 15.2 安装（容忍）

`AbilityInstaller.install(source)`（`ability/package/installer.py`）：

1. `installer.prepare` → `intake.normalize_source`：头段能读就用其身份，读不出就按根目录名合成；成员**一律以磁盘为准**（`operations/*.py`、`skills/<name>/SKILL.md`），旧 `members` 不参与；渲染标准 v2 文本就地写回；
2. 校验：成员键与保留名、成员文件存在性与 entry 约定、内容摘要/大小、frontmatter 与成员键一致、目录可移植性——**不 import 包代码**；
3. 落槽：只读复制到 `<install_root>/<ns>/<name>/<version>/` 并原子 rename；同版本同摘要幂等，同版本不同内容报 `version_conflict`；
4. 写安装记录 `<version>.lock.yaml`（在版本目录**之外**）：`schema`、`package_digest`（树摘要）、`digest_algorithm`、`installed_at`、`source`、逐文件摘要。

`mcp` 包走 `refresh`（受信客户端探测后重写快照），普通包 `refresh` 被拒绝。

### 15.3 文本归一与导入

- **文本源归一**：`ability.yaml`、`SKILL.md`、`.py` 等统一为 UTF-8 无 BOM + LF；含空字节视为非文本，不参与。
- **包内杂物容忍**：`operations/` 下的说明文件/子目录、`skills/<key>/` 下的未知文件只跳过，不登记成资源，也不阻断安装。
- 归一结果以 warning 回给调用方（例如「已把 N 个文本源统一为 LF」）。

作者侧 `build` 仍严格：同一份源包「作者构建失败、用户安装成功」是**预期行为**，排障时先分清走的是哪条路径（`ghrah-12` §11 第 6 条）。

### 15.4 三方职责

| 阶段 | 谁做 | 做什么 | 绝不做什么 |
| --- | --- | --- | --- |
| build | 作者 / CI | 生成并校验 `members`，保证幂等字节 | 不改头段、不跑 `execute` |
| install | 发行版 | 校验索引与内容、写 lock、原子落槽 | 不 import 包代码、不重建索引 |
| load / commit | Core 启动 | 读定义面、捕获整树、提交进库 | 不按文件系统猜成员、不把 lock 当运行期事实 |
| read | Core 运行期 | 从库投影目录、复核授权、读内容 blob | 不读安装目录、不另存授权副本 |