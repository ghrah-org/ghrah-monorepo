# 能力与成员数据底座设计说明

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-DESIGN-013 |
| 文档状态 | 现行（已实现） |
| 校准基线 | `a742fcf`（2026-11-23），工作区干净 |
| 读者 | 改能力系统/授权/实例快照/模板存储的人；排查"授权从哪来、重启为什么这样恢复"的人 |
| 不覆盖 | 能力包目录结构与构建规则（见 `ghrah-12`）、成员定义与命名（见 `ghrah-11`）、驱动循环（见 `ghrah-03`） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-11-23 | 首版。记录本轮"数据底座切换"：能力关系、成员授权、实例快照与模板成员从安装目录 / YAML / 旧 Scheme 树迁到 SQLite 关系库 + 内容 blob，并说明运行期只读库、启动期提交来源 |

## 1. 这次切换了什么

旧模型（已废弃）：

```text
安装目录 ability.yaml / *.lock.yaml  ──直接读──▶  运行期装配、工具面、技能正文
Scheme 树（ghrah.scheme.loading）    ──直接读──▶  能力目录 / 技能目录
模板 = 模板文件 / 包维度 ability_request.packages
实例 = 进程内存里的装配结果，重启重扫目录
```

现行模型（SQLite 权威）：

```text
安装目录（只读源） ──启动提交──▶ ability_versions + ability_content（SQLite，按 digest 不可变）
                                      │
模板库 agent_templates.available_members ──渲染──▶ AgentAssemblySpec
                                      │
                                      ▼
                            创建装配（补齐成员 data）
                                      │
             ┌────────────────────────┼────────────────────────┐
             ▼                        ▼                        ▼
   ability_grants（授权事实）  ability_instances（版本槽）  agent_instances.members（快照+轴）
             │                        │                        │
             └──────────────┬─────────┴──────────┬─────────────┘
                            ▼                    ▼
                 surface_snapshot（目录）   resolve / _check（读取与授权校验）
```

一句话：**文件系统是"提交来源"，不是运行期权威；运行期只读关系库与已提交字节。**

### 1.1 三处权威与各自边界

| 库 | 位置 | 权威内容 | 不负责 |
| --- | --- | --- | --- |
| 能力关系库 | `<project_root>/db/action-chains.sqlite3` 的 `ability_*` 表 | 已提交版本、内容字节、实例版本槽、逐 Agent 授权、工具绑定、变更日志 | 业务记录、链消息 |
| 实例快照库 | `<project_root>/db/agents.sqlite3` 的 `agent_instances` | 创建时冻结的装配配置、成员定义快照、成员轴（含 `bind`） | 授权（授权在关系库，不互相复制） |
| 模板库 | 发行版 `<data>/subject.db` 的 `agent_templates` | 模板成员集合 `available_members`、模型/提示词/工作区/权限申请 | 成员定义数据、运行期绑定 |

三库同属一个项目根，但**写路径互不重叠**：模板只写 `agent_templates`；创建线只写 `agent_instances`；启动核定与 `bind_member` 只写 `ability_*` 与快照的 `axes`。

## 2. 能力关系库

### 2.1 表结构（schema version 5）

`_SCHEMA` 常量在 `ghrah-core/src/ghrah/ability/access/store.py:57`，版本号 `ability_tree_schema.version=5`。

| 表 | 主键 | 语义 |
| --- | --- | --- |
| `ability_versions` | `(project, digest)` | 一次提交的确定版本：`definition`（规范化定义 JSON）、`implementation`（操作实现索引）、`manifest`（正文/资源路径 + 摘要 + 大小） |
| `ability_content` | `(project, digest)` | 按内容摘要存储的字节（技能正文、附件、脚本）；`read_chunk` 按范围读 |
| `ability_instances` | `(project, id)` | 项目内一个包槽当前选中的版本摘要与安装根；`id` 由包身份派生（点换横线） |
| `ability_agents` | `(project, id)` | Agent 的授权修订计数与存活标记；所有授权变更先过它 |
| `ability_grants` | `(project, agent, alias)` | 一个 Agent 对某包（alias）的授予：成员集合、根、内容白名单、`axes`、来源、修订 |
| `ability_tools` | `(project, agent, name)` | 已绑定为常驻工具的成员（工具名 → alias/member/grant 修订） |
| `ability_changes` | `seq` | 变更审计：修订、原因、操作者、版本摘要、策略 |
| `ability_registrations` | `(project, agent)` | 启动独占占位：token、状态、拥有的修订；防并发重启互相覆盖 |

实例版本槽是**包级**的：`ability_instances.id` 由包全名派生（`ghrah.fs` → `ghrah-fs`），不含版本。因此同一包在项目内同一时刻只有一个"当前版本"（见 §6 已知限制）。

### 2.2 写入路径

#### 启动提交（唯一把安装目录写进库的路径）

入口：`ghrah-core/src/ghrah/ability/access/startup.py` 的启动核定器。

```text
启动核定
  1. startup_registration(actor, agent)        # 独占占位，拿 token 与拥有的修订
  2. source.capture_package(package)           # 读安装目录 / 内置树，校验 lock 与可移植性
  3. store.commit_version(actor, version)      # 定义 + 实现 + manifest + 内容 blob 落库（幂等）
  4. store.select_instance(actor, instance, digest, root, invalidate_grants=…)
  5. store.write_grant(actor, agent, GrantAbilityRequest)
  6. release_registration(actor, agent)
```

约束：

- `commit_version` 只接受"摘要自洽"的版本；同一 digest 重复提交幂等。
- `select_instance` 切换版本时默认撤销旧获授关联（`invalidate_grants=True`），禁止旧许可随新版本自动扩大。
- 启动期间只调用 `write_grant`（不要求运行实例身份），Agent 注册前也能落授权。
- 启动独占失败只回滚**本次拥有**的修订，不覆盖并发管理写入；进程异常退出留下的占位必须停机核对后显式处理，不靠超时抢占。

#### 管理面变更

`grant` / `revoke` / `bind_tool` / `unbind_tool`（`store.py:887` 起）在写事务里先要求 `expected_revision` 与当前修订一致，再 `_bump` 修订并写 `ability_changes`；冲突返回 `conflict`，不静默覆盖。

#### 成员绑定变更（`bind_member` / `unbind_member`）

绑定轴有两个落点，但写入口不同：

1. **实例快照**：`bind_member` / `unbind_member` 只改 `agent_instances.members[].axes.bind`（经 `MemberBindingState.save` → `AgentInstanceStore.set_member_bindings`），立即写库、重启按快照恢复。
2. **关系库**：`ability_tools` 记录"常驻工具"的投影，由启动核定（materializer 的 `_bind`）与管理面 `enable_tool` / `disable_tool` 维护；`bind_member` 不直接写它。

因此两者不是同一写路径的双写：快照是绑定轴的权威，`ability_tools` 是启动/管理面的工具投影，运行期 `refresh()` 以快照绑定集合为准重算工具面。

## 3. 实例快照库

### 3.1 表结构

`ghrah-core/src/ghrah/core/agents/instance_store.py:47`：

| 列 | 语义 |
| --- | --- |
| `agent_id` / `name` | 实例标识与名字 |
| `config` | 冻结的装配配置（模型、提示词、权限、工作区、上下文策略） |
| `members` | 成员快照（JSON 数组）：身份、类型、版本、`ability_type`、`axes`、`data` |
| `surface` / `mcp_refs` | 预留列（当前创建线写 `null` / `[]`） |
| `incarnation_id` | 一次进程内的实例化身，用于识别残留进程 |

### 3.2 成员快照的形状

创建时写入的是**定义态成员**（`Member.to_wire()`）：

```json
{
  "namespace": "research",
  "name": "research",
  "member": "research.research.research",
  "type": "skill",
  "version": "0.1.0",
  "ability_type": "external",
  "axes": {"visible": true, "bind": true},
  "data": {
    "title": "research",
    "description": "...",
    "path": "research/research/0.1.0/skills/research/SKILL.md",
    "media_type": "text/markdown",
    "assets": {}, "references": {}, "scripts": {}
  }
}
```

规则：

- `axes.visible` 创建时固定 `true`；`bind` 取创建请求的 `initial_bindings`。
- `data` 由创建线按来源补齐（external 按版本读安装记录；kernel 读内置树），**不来自模板**。
- 快照是"钉住的副本"：定义数据不随安装目录变化；授权仍以关系库为准，二者不互相同步。

### 3.3 恢复

重启时实例快照是恢复入口：`ghrah-core/src/ghrah/core/unit.py` 的 `_restore_from_snapshot` 按快照成员重建装配，`member_axes` 从 `axes.bind` 还原绑定集合（`startup.py:812` 的 `_bound_names`）；关系库仍要重新核定授权，快照不能凭空产生许可。

## 4. 模板库

- 模板成员存 `agent_templates.available_members`（列名与 JSON 形状均与旧 `ability_request` 不同）。
- 成员只存**身份**六字段：`namespace` / `name` / `member` / `type` / `version` / `ability_type`；不存 `data`、`surface`、`mcp_refs`、脚本 `exec` 选择。
- 旧库启动时自动迁移：列改名 + 旧行转成三段成员数组（幂等，见 `ghrah-subject/src/ghrah/subject/template_store/store.py` 的迁移段）。
- 模板是"申请"，不是授权：渲染出的 `AgentAssemblySpec` 交创建线，仍要经启动核定落 `ability_grants`。

## 5. 读取路径

| 场景 | 入口 | 读什么 |
| --- | --- | --- |
| 目录/工具面 | `surface_snapshot(actor, agent)`（`store.py:1330`） | 同一读事务内：授权 → 版本 → 定义，产出 `SurfaceCatalog`（工具 + 技能） |
| Agent 视图 | `view(actor, agent)` | 授权投影：节点、操作、技能、附件可见性 |
| 成员解析 | `resolve(actor, alias, member)` | 授权 → 实例 → 版本 → 摘要，返回 `MemberFence` |
| 地址反查 | `resolve_address(actor, address)`（`store.py:1366`） | 只在当前 Agent 已授予实例里按声明路径匹配，再走 `resolve` |
| 正文读取 | `read_chunk` / `read_content`（`store.py:1756`） | 按 `MemberFence` 校验收权与摘要，从 `ability_content` 读字节 |
| 授权复核 | `authorize` / `authorize_many`（`store.py:1641`） | 调用前复核当前修订与 grant/instance/version 是否仍匹配 |

关键：**读取不碰安装目录**。目录由库内定义重建，正文由内容 blob 提供；安装目录只在启动提交与人工排障时被读。

## 6. 失败语义与已知限制

### 6.1 失败语义

| 情况 | 结果 |
| --- | --- |
| 版本摘要与定义/实现/manifest 不自洽 | `invalid_change`，拒绝提交 |
| 授权修订冲突 | `conflict`，不覆盖 |
| 成员未授予 / 实例版本不匹配 / 摘要漂移 | `access_denied` / `not_found`，fail-closed |
| 地址反查不在授予面 | `not_found`（不泄露别的 Agent 的成员） |
| 启动占位残留 | 停机核对后显式释放，不自动抢锁 |

### 6.2 已知限制

1. **包级单版本**：`ability_instances` 不含版本，同一包在项目内只能选一个版本；多 Agent 绑定同包不同版本尚未支持。
2. **快照 `data` 不自动失效**：安装根下同版本内容若被替换，快照与已提交版本都不会自动重算；后续可在使用时按 manifest 校验。
3. **退化身份包**：`identity.name` 省略时安装槽是 `<ns>/<ns>/<version>/`、成员名两段（`research.research`）。现行 Core 兼容两段/三段（解析回退、等价形态比较），但这是兼容层而非长期约定——见 `ghrah-12` §身份与命名。
4. **旧 Scheme 控制平面**：`ghrah-scheme` 包仍作为可选组件存在（`[scheme]` extra），但不在运行期主链上；能力目录、授权与正文都不再从 Scheme 树读取。

## 7. 不变式

| # | 不变式 | 校验点 |
| --- | --- | --- |
| D-1 | 运行期只读 SQLite，不读安装目录 | `read_chunk` / `surface_snapshot` 的输入只有库内数据 |
| D-2 | 版本按 digest 不可变，提交幂等 | `commit_version` 的摘要自洽校验 |
| D-3 | 授权只经关系库写，快照不复制许可 | 快照 `members` 无 grant 字段；`_check` 每次核库 |
| D-4 | 变更必须带 `expected_revision` | `write_grant` / `select_instance` / `bind_tool` |
| D-5 | 目录可从库完整重建 | `catalog_from_view` + 定义表，不缓存跨事务结论 |
| D-6 | 绑定轴单一权威 + 工具投影分离 | 快照 `axes.bind` 是绑定权威；`ability_tools` 只由启动核定/管理面维护，`bind_member` 不写它 |
| D-7 | 模板只申请、不授权 | 创建线必须经过启动核定才产生 `ability_grants` |

## 8. 迁移与兼容

| 迁移 | 位置 | 说明 |
| --- | --- | --- |
| 模板列 `ability_request` → `available_members` | `template_store/store.py` 启动迁移 | 改列 + 旧行转三段成员数组，幂等 |
| 旧快照 `axes.available` → `axes.bind` | `instance_store.set_member_bindings` | 新写用 `bind`；若条目里已有 `available` 则同步 |
| 退化包两段成员名 | `assembly._resolve_member` / `members._external_definition` / `surface.runtime._member_forms` | 三种回退，保证存量快照无需迁移 |
| 上传包文本噪声 | `ability/package/intake.py` | UTF-8 无 BOM + LF 归一；成员扫描宽容（`tolerant`），但安装落槽仍严格比对文件集合（包根说明文件除外） |