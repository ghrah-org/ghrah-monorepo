---
title: Ability 与 Manifest 一体化迁移记录
description: 把 abilities 实现层与 manifest 声明层合成一套迁入 ghrah/scheme/，20 份随包 yaml 并入 4 份容器定义；第二轮扁平化后 manifest 十模块合并为五，scheme 下除 ability/ 外无子目录
tags: [迁移, 能力系统, Scheme]
keywords: [AbilityDefinition, OperationDefinition, loader, projection, shim]
created: 2026-10-26
updated: 2026-10-26
status: done
---

# Ability 与 Manifest 一体化迁移记录

> **⚠ 已归档（2026-10-29）**：本文记录 `ghrah/scheme/` 迁移过程中的中间态，已被 2026-10-28 的重构（`1938ffa` 退役 `abilities/`+`manifest/`、`fc032df` subject 改指 `ghrah.scheme`）整体推翻。现役口径：[ghrah-01](../ghrah-01-ability-design-and-implementation.md) §5.3（容器清单，已是 5 个而非文中 4 个）、[ghrah-06](../ghrah-06-code-map.md)。**不要照着本文写代码**；本文内部的相对链接多为失效的历史链接。

> 本文记录一次已完成的结构迁移：`ghrah/abilities/`（实现层）与 `ghrah/manifest/`（声明解析层）合并为一套，全部落在 `ghrah/scheme/` 下；随包 20 份 ability yaml 并入 4 份容器定义。
>
> 分两轮完成：**第一轮**搬迁 + 合并声明载体；**第二轮**扁平化目录——`manifest/` 十个模块按功能合并为五个，`ability/` 只留能力包，加载器与格式模型平铺到 `scheme/`。
>
> 验证口径：`ghrah-core` 2093 passed，其余四包 1053 passed，`ruff check` / `ruff format --check` 全绿。未提交。

## 0. 结论摘要

| 项目 | 迁移前 | 迁移后 |
| --- | --- | --- |
| 实现与声明目录 | `abilities/` + `manifest/` 两处 | `scheme/` 一处：30 个平铺模块 + `ability/` |
| 子目录 | 两层包 | 只有 `ability/`（见 §2.1） |
| ability 声明载体 | 20 份平铺 yaml（`manifest/builtins/*.yaml`） | 4 份容器定义（`ability/<容器>/ability.yaml`）+ 21 个实现脚本 |
| manifest 模块数 | 10 个模块 + 1 个子包 | 5 个平铺模块（合并依据见 §2.2） |
| 声明与实现的关联方式 | `implementation.handler` 字符串 + 硬编码 20 项名单 | 目录约定：`ability/<容器>/operations/<键>.py` |
| 名单来源 | 人工维护的 tuple（历史上已漂移过一次） | 由定义目录派生 |
| 运行时取声明 | 读 yaml 文件 | 定义面 + 实现面 → 投影成 `AbilityManifest` |
| 旧路径 | 权威 | 53 个模块别名 shim（同一模块对象，非副本） |

一行话：**定义面（yaml）与实现面（py）成为唯一来源，`AbilityManifest` 降级为可重建的运行时 view。**

> 现状补充（第十轮之后）：`scheme/` 已按主题分为 `execution/`(12) + `loading/`(16) + `permissions/`(3) + 根部 6 个模块；`ability/` 下 5 个容器（`core`/`fs`/`cluster`/`shell`/`mcp`）；旧路径 shim 全部删除；技能包并入同一定义面（§8.7 / §8.8）。上表 23 / 26 / 30 行是迁移当时的口径。

## 1. 目标形态

### 1.1 目录（现状，2026-10-26 第十轮之后）

```text
ghrah-core/src/ghrah/scheme/
├── __init__.py                 # 惰性门面：_MODULE_OF 索引表 + __getattr__（导入期不拉起子模块，§7.2）
├── agent_manifest.py           # agent 定义格式（manifest: agent）
├── agent_resolver.py           # AgentManifest → AgentConfig + ResolvedAbility[]
├── errors.py                   # AbilityError / AbilityNotFoundError
├── kinds.py                    # FS_ABILITY_TYPES / CLUSTER_ABILITY_TYPES
├── paths.py                    # 路径参数规格表 + is_subpath
├── _cluster_common.py          # 集群能力共用小件
│
├─ execution/ (12) ──────────── 执行运行时（原 abilities/*）
├── execution/base.py                     # Ability ABC（纯接口，无行为）
├── execution/context.py                  # AbilityExecutionContext
├── execution/hooks.py                    # Hook 框架 + HookPoint
├── execution/hook_context.py             # AbilityExecutionContext → ExecutionFrame
├── execution/hook_runner.py              # hook 的唯一执行路径
├── execution/hook_store.py               # 按 (HookPoint, ability) 索引的 hook 存储
├── execution/execution_data.py           # 类型化 key + 数据总线
├── execution/execution_frame.py          # ExecutionFrame
├── execution/execution_services.py       # 类型化服务注册表
├── execution/invocation.py               # AbilityInvocation（一次调用的身份与参数）
├── execution/executor.py                 # AbilityExecutor / LocalAbilityExecutor
├── execution/ability_build.py            # ResolvedAbility → Ability 实例（权限与 HITL 接线）
│
├─ loading/ (16) ────────────── 声明读取、目录池、视图投影、锁与指纹
├── loading/ability_roots.py              # 目录池（内置 rank 0 + 用户 rank 100）
├── loading/ability_scan.py               # 按池扫盘：定义 / 容器视图 / 技能成员（含锁与指纹）
├── loading/ability_schema.py             # ability.yaml 格式模型（含 SkillDefinition）
├── loading/ability_catalog.py            # 实现面投影：定义 + 实现脚本 → AbilityManifest
├── loading/ability_view.py               # AbilityManifest / AbilityMetadata / AbilityHooks
├── loading/manifest_fields.py            # 两侧共用件（格式版本、异常、PermissionFlags、ToolSchema）
├── loading/manifest_io.py                # 声明来源 + BuiltinManifestStore（含旧格式 parse/validate）
├── loading/catalog.py                    # 对外目录报告（能力面 + 技能面合流）
├── loading/tool_list.py                  # 模型工具面（→ OpenAI function 列表）
├── loading/skill_context.py              # 模型技能面：目录文案 + 正文读取
├── loading/skill_errors.py / skill_models.py / skill_fingerprint.py / skill_lock.py / skill_mcp.py / skill_provider.py
│
├─ permissions/ (3) ─────────── 只判定"是否允许 / 是否需人工批准"，不做执行
├── permissions/fs_permissions.py         # FSPermissionChecker + 审批 hook
├── permissions/command_safety.py         # 命令安全分类器 + 审批 hook
├── permissions/hitl.py                   # HITLApprovalHook / HITLRequiredAbility
│
└── ability/                    # ← 唯一子目录：只放能力包
    ├── __init__.py             # 包标记（目录扫描与 import 需要）
    ├── fs/      __init__.py + ability.yaml + operations/{__init__.py, 7 个实现}
    ├── core/    __init__.py + ability.yaml + operations/{__init__.py, 6 个实现}
    ├── cluster/ __init__.py + ability.yaml + operations/{__init__.py, 7 个实现}
    ├── shell/   __init__.py + ability.yaml + operations/{__init__.py, 1 个实现}
    └── mcp/     __init__.py + ability.yaml + operations/{__init__.py, 1 个实现}
```

`ability/` 里只有包标记与能力包本身，**没有任何加载/解析代码**：目录扫描在
`loading/ability_scan.py`、投影在 `loading/ability_catalog.py`，格式模型在 `loading/ability_schema.py`。

已删除（历史，勿在此处再找）：`execution/registry.py`（§3.9）、`hook_registry.py` 与 `_utils.py`（§3.1）、`command_proxy.py`（§3.8）、`manifest_responses.py`（§3.1）、`loading/skill_scan.py` / `skill_query.py` / `skill_init.py` / `skill_roots.py`（§8.8）。

### 1.2 三个面的职责与权威方向

```text
ability/<容器>/ability.yaml        定义面：身份、成员 schema、提供者槽、执行约束
ability/<容器>/operations/<键>.py  实现面：实现类 + PERMISSIONS/HOOKS/SUMMARY/TAGS/TOOL
              │
              ▼  （单向投影，每次调用重新扫盘，不缓存）
        AbilityManifest            运行时 view：喂给 agent_resolver / ability_build / list_abilities
```

- **定义面**不写授权结果、不写 Hook 编排（草案要求）。
- **实现面**承担实现与"草案格式暂无字段承载"的元信息（见 §6）。
- **view 不被写回**：它由定义面 + 实现面在现场算出，丢弃进程内存即可完整重建；`load_all_builtin_manifests()` 每次调用都重新扫盘（§3.10 起不再有 `lru_cache`）。
- **技能包**是同一套定义面的一个成员种类（`skills.<key>`），不是第二类对象——扫描、锁、指纹都在 `loading/ability_scan.py`（§8.8）。

## 2. 搬迁映射

### 2.1 abilities → scheme（平铺）

| 旧路径 | 新路径 | 形态 |
| --- | --- | --- |
| `abilities/*.py`（17 个模块） | `scheme/*.py` | 模块别名 shim |
| `abilities/builtin/<键>.py`（21 个） | `scheme/ability/<容器>/operations/<键>.py` | 模块别名 shim |
| `abilities/builtin/{fs_permissions,command_safety,command_proxy,_cluster_common}.py` | `scheme/<同名>.py` | 模块别名 shim |
| `abilities/__init__.py` | 保留为对外门面（50+ 符号聚合 + 注册） | 门面 |
| `abilities/builtin/__init__.py` | 保留（聚合 + 内置 Hook 注册） | 门面 |
| `abilities/builtin/mcp_call.py` | 未纳入（另一个会话后续接入了，仍是真实源码） | 原地保留 |

### 2.2 manifest → scheme（合并后平铺）

| 旧路径 | 新路径 | 合并理由 |
| --- | --- | --- |
| `manifest/errors.py` + `types.py` + `ability.py` + `agent.py` | `scheme/manifest_fields.py`（拆分后） | 全是旧格式声明面的纯数据模型与它们抛出的校验异常；四者只向内依赖、总被一起 import |
| `manifest/protocols.py` + `parser.py` + `store.py` | `scheme/manifest_io.py` | 三者都在回答"声明从哪来、怎么拿到"：文本→模型、按名取、取用接口 |
| `manifest/resolver.py` | `scheme/agent_resolver.py` | 声明 → 运行时的第一段（AgentManifest → AgentConfig + ResolvedAbility），与物化职责不同，不合并 |
| `manifest/materialize.py` | `scheme/ability_build.py` | 第二段（ResolvedAbility → 实例 + 权限/HITL 接线），与解析职责不同 |
| `manifest/responses.py` | `scheme/manifest_responses.py`（**其后已删除**） | 对外 wire 形状，与内部模型解耦就先单独保留；后续清点发现 7 个 DTO 零消费者，已随模块一起移除（冻结副本见 `legacy/manifest-old-format/`） |
| `manifest/builtins/__init__.py` | 折入 `scheme/ability_catalog.py`（尾部） | 它只是 loader 的旧口径门面，无独立逻辑 |
| `manifest/__init__.py` | 旧路径保留为聚合门面（§4） | — |
| `manifest/builtins/*.yaml`（20 份） | **删除**，内容并入 `ability/*/ability.yaml` | — |

合并后 `manifest/` 十分之六的模块数与代码量都在，但**功能边界按"来源 / 解析 / 物化 / 对外形状"重新划分**，
不再按"一个类一个文件"。合并未丢符号：合并前后顶层符号集做过集合比对。

容器划分沿用原有 namespace：`ghrah.fs` / `ghrah.core` / `ghrah.cluster` / `ghrah.shell` 各成为一个容器，
`identity.namespace=ghrah`、`identity.name=<容器名>`，成员键即原来的 ability 名，
因此 `ghrah.fs.read_file` 这类旧全名保持不变。

## 3. 新增的两个模块

### 3.1 `loading/ability_schema.py`

`ability.yaml` 的类型化契约，全部子模型 `extra="forbid"`（未知字段拒绝）：

| 模型 | 承载 |
| --- | --- |
| `AbilityDefinition` | `format` / `schema_version` / `identity` / `title` / `description` / `provider_requirements` / `operations` / `skills` / `assets` |
| `AbilityIdentity` | `namespace` / `name` / `version` |
| `ProviderRequirement` | `contract` / `contract_digest`（发布时填）/ `required_capabilities` |
| `OperationDefinition` | `title` / `description` / `input_schema` / `output_schema` / `errors` / `implementation` / `effects` / `resources` / `execution` |
| `ExecutionSpec` | `mode=request` / `timeout_ms` / `cancellation` / `on_revoke` / `retry=never` |

解析时强制的三条结构约束：`schema_version` 必须等于 `0.1-draft`；`identity.name` 必须等于目录名；
`implementation.operation` 必须等于成员键，且 `provider_slot` 必须在 `provider_requirements` 里声明过。

### 3.2 `loading/ability_catalog.py`

- `load_ability_definitions()`：扫描 `ability/*/ability.yaml`（`importlib.resources`），**不**走 yaml 清单。
- `_project()`：逐成员投影，从实现脚本读四类声明，产出 `AbilityManifest`。
- `load_all_builtin_manifests()` / `load_builtin_manifest()`：旧口径 API，`lru_cache` 缓存。
- `builtin_full_names()`：派生 `_BUILTIN_YAML_NAMES`，人工名单消失。

两个关键判断：

1. **`REGISTRY_ONLY_OPERATIONS = {"send"}`**。`send` 由 Core 的 room 装配期注入，历史上没有 manifest 声明；
   投影时跳过它以保持既有语义（`list_abilities` 的 registry-only 差异面依赖这一点）。它仍是
   `cluster/ability.yaml` 的正式成员，只是不产出运行时 view。是否升级为声明式成员是待决项。
2. **`TOOL` 由实现脚本显式声明**。不能用"是否覆写 `bind_tool`"推断——`ConversationAbility` 覆写了
   它但恒返回 `None`。缺省 `True`，`conversation` 显式 `TOOL = False`。

## 4. 旧路径兼容层

53 个旧模块改为**模块对象别名**，而非显式 re-export：

```python
# ghrah/abilities/registry.py（旧路径）
import sys

import ghrah.scheme.execution.registry as _target

sys.modules[__name__] = _target
```

选别名而非 re-export 的原因，是现有调用方会穿透模块边界：

- `monkeypatch.setattr("ghrah.abilities.builtin.search_files.shutil.which", …)`——测试按字符串路径改模块内部属性；
- `from ghrah.abilities.builtin.execute_command import _fold_text, _FOLD_HEAD_LINES`——测试导入私有辅助件；
- `from ghrah.abilities.base import ActionResult`——`execution/base.py` 兼作结果类型门面（约 30 个测试文件这样用）。

别名让旧路径与新路径是**同一个模块对象**，上述三种用法都自然成立，也不会出现两份实现互相漂移。
代价：模块的 `__name__` 指向新路径（对 `isinstance`、导入、monkeypatch 均无影响）。

## 5. 等价性验证

### 5.1 方法

第一轮迁移前抓一份基线快照，迁移后逐叶子比对：

- 20 份 `AbilityManifest` 的 `model_dump(mode="json")`；
- `AbilityRegistry.list_types()`；
- 每个已注册类型实例的 `bind_tool()` 返回值。

另外做三项能力完整性检查：每份声明的 handler 能在 registry 里 join 上；20 份声明经真实物化通路
（`instantiate_resolved_abilities`）产出 20 个实例；物化后的工具面与声明一致。

第二轮（扁平化）用同一份快照再比一次：**20 份 manifest 视图零差异**，说明合并与搬迁没有触碰声明面。
那一轮出现 17 处差异，全部来自另一个会话同时接入的 MCP 能力（`mcp_call` 进注册表、
`load_skill` 新增 `server`/`tool` 参数），与本次重构无关。

### 5.2 快照差异（第一轮：5 处，其中 4 处为真实差异）

| # | 位置 | 迁移前 | 迁移后 | 判定 |
| --- | --- | --- | --- | --- |
| 1 | `ghrah.fs.read_file.tool.description` | 短句 `Read the content of a file at the specified path` | 长句（含 offset/limit 分页说明） | **修正**：取代码 `bind_tool()` 的真实文本 |
| 2 | `ghrah.fs.read_file.tool.parameters.offset` | 缺失 | `integer, default 0, min 0` | **修正**：旧 yaml 漏声明，代码 `ReadFileInput` 一直有 |
| 3 | `ghrah.fs.read_file.tool.parameters.limit` | 缺失 | `integer, default 0, min 0` | **修正**：同上 |
| 4 | `ghrah.cluster.send_message.tool.parameters.fire_and_forget.default` | 无默认值 | `true` | **修正**：代码 `SendMessageInput` 默认 `True`，旧 yaml 没写 |
| 5 | `.tools.command_proxy` | `{__error__, msg}` | `{__error__}` | 比对脚本噪声，非代码差异 |

前四处都是**旧 yaml 与实现漂移**，方向统一为"声明对齐实现"。它们只影响 `manifest.tool` 这个声明镜像
（`list_abilities` 与 subject 的清单会显示更完整的参数），不影响模型实际拿到的工具 schema
（那来自 `bind_tool()`，迁移前后完全未变）。

### 5.3 测试

| 范围 | 结果 |
| --- | --- |
| `ghrah-core` | 2093 passed / 4 skipped / **1 failed（预存在）** |
| `ghrah-subject` `ghrah-plugin` `ghrah-protocol` `ghrah-taskstore` | 1053 passed / 7 skipped / **1 failed（预存在）** |

两个失败均与本次迁移无关，迁移前即红：

1. `ghrah-core/tests/abilities/test_fs_permissions.py::test_denied_error_lists_allowed_roots`
   —— 断言 `/tmp/data`，Windows 上路径解析成 `D:\tmp\data`。
2. `ghrah-protocol/tests/test_scheme_descriptors.py::test_python_generated_snapshot_is_current`
   —— `read_text()` 未指定编码，在 GBK locale 上解不开 UTF-8 的
   `ghrah-observer-webui/packages/protocol/src/__snapshots__/scheme-discovery.json`。

另有 3 个 collection error 来自未提交的 MCP WIP（`ghrah.mcp.errors` 尚不存在），测试时已排除：
`tests/mcp/`、`tests/skills/test_mcp_config.py`、`tests/abilities/builtin/test_mcp_call.py`。

### 5.4 静态检查

`uv run ruff check .` 与 `uv run ruff format --check .`（610 files）全绿。

## 6. 字段口径对照

草案格式（`0.1-draft`）缺若干旧 manifest 字段。为避免信息丢失，这些字段暂存**实现侧模块常量**，
与 `PERMISSIONS`/`HOOKS` 同处一地，由 loader 读回：

| 旧 manifest 字段 | 临时落点 | 说明 |
| --- | --- | --- |
| `metadata.permissions` | `PERMISSIONS`（dict） | 草案把授权结果排除在定义外，但授权模型尚未落地，故暂存实现侧 |
| `hooks` | `HOOKS`（dict） | 草案不把 Hook 编排写进定义 |
| `metadata.description`（目录摘要） | `SUMMARY`（str） | 与成员的模型可见 `description` 是两段文本，不能合并 |
| `metadata.tags` | `TAGS`（list） | `ghrah-subject` 的 `manifest_list_abilities` 会展示它 |
| `tool`（有/无） | `TOOL`（bool，缺省 True） | 只有 `conversation` 显式 `False` |
| `tool.name` / `tool.parameters` / `tool.description` | 由成员键 + `input_schema` + `description` 投影 | 不再单独维护 |
| `metadata.icon` / `since` / `deprecated` | 未承载 | 全仓无消费方，按空值投影 |

`AbilityManifest.version` 固定投影为 `"1"`（**格式**版本），与 `identity.version`（**能力**版本）分层，
避免同名不同义。

## 7. 已知问题与待决

### 7.1 `ghrah.scheme` 导入名冲突（阻塞级待决）

`ghrah-scheme` 发行包也提供 `ghrah/scheme/`（`authority.py` / `contracts.py` / `models.py` / `runtime.py` 等）。
`ghrah-core/src/ghrah/scheme/__init__.py` 赢得解析，于是 **`ghrah-scheme` 整个包不可用**：

```text
uv run pytest ghrah-scheme
→ ImportError: cannot import name 'AccessRequest' from 'ghrah.scheme'
```

本次迁移把这个冲突从"潜在"变成"结构性"：`scheme/` 现在承载能力运行时，不可能再让位。两条路：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A. 原型改名 | `ghrah-scheme` 的包改成 `ghrah/control/`（或 `ghrah/scheme_proto/`），其内部 import 与测试同步改 | 触及该包全部模块；它尚未被任何运行时依赖，改动面可控 |
| B. 原型退役 | 把 `ghrah-scheme` 移出 workspace（保留存档目录） | 失去控制平面原型；现状是既有 Core/Subject 都未迁移到它 |

**在决定之前，`pytest ghrah-scheme` 无法通过**，CI 里 scheme 的 mypy/pytest 门禁因此不可信；
其余五包的测试与门禁不受影响。

### 7.2 `scheme/__init__.py` 必须保持轻量（惰性门面）

父包 `__init__` 先于子模块执行。若在 `scheme/__init__.py` 里做重聚合（import 全部 operation），
而各 operation 又 `from ghrah.scheme.execution.base import …`，就会形成初始化环。

现做法是 **PEP 562 惰性门面**：`_MODULE_OF`（125 个公开名 → 归属模块）为唯一索引表，`__all__`
由它派生；`__getattr__` 首次访问时才 `import_module` 并把结果写回 `globals()`。实测
`import ghrah.scheme` 加载 0 个子模块，解析全部名字后才到 30 个。子包（`ability` / `execution` /
`loading` / `permissions`）与包根单文件模块（`agent_manifest` / `agent_resolver` / `errors` /
`kinds` / `paths`）也可按属性直接点到。

约束不变：**不要在 `__init__.py` 顶层 import 任何 operation 或执行运行时**，否则环路重来。
同理，`loading/manifest_io.py` 对 `ability_catalog` 的 import 必须保持惰性（已改）。

### 7.3 `send` 的归属

它是 `cluster` 容器的正式成员但在 `REGISTRY_ONLY_OPERATIONS` 里被排除出投影。要么在草案格式里
为"非声明式成员"补一个显式标记，要么把它从 `ability.yaml` 移出并单独记录。

### 7.4 `conversation` 是否该是 Operation

它不接受模型调用（`TOOL = False`），`invoke` 语义对它不成立。目前靠 `TOOL` 常量维持旧行为。
若草案确认"Operation 必须可 invoke"，它应改由会话层表达，而不是占一个成员位。

### 7.5 适配器没有落点

`command_proxy`（descriptor 驱动的插件桥）与 `mcp_call`（另一会话接入）都不是固定操作，而是 provider 适配器。
草案只说"适配器均产出上述定义形态"，没说适配器自己的脚本放哪。目前 `command_proxy` 平铺在 `scheme/` 根，
`mcp_call` 仍是 `abilities/builtin/` 下的真实源码（它没被纳入 ability 容器，因此没被搬走）。

### 7.6 `BuiltinHookRegistry` 是死代码

`scheme/hook_registry.py` 的注册表只被写入（`abilities/builtin/__init__.py` 注册 3 个具名 hook），
全仓**没有任何读取方**。实际 hook 接线走的是类的 `get_hooks()` 与 `HITLRequiredAbility` 包装。
可以随下一次清理删除。

### 7.7 旧树清理顺序

旧路径现在是别名 shim，`ghrah-core`、`ghrah-subject`、`examples` 仍有 20 个文件从旧路径导入。
若要彻底删旧树，建议顺序：

1. 把 20 处外部 import 改指 `ghrah.scheme.*`；
2. `pytest` 全绿；
3. 删除 `ghrah/abilities/` 与 `ghrah/manifest/` 下所有 shim（注意 `abilities/__init__.py` 与
   `abilities/builtin/__init__.py` 是门面不是 shim，删之前要把聚合与注册迁到别处）；
4. 再跑一次全量测试。

### 7.8 打包注意

`ability/*/ability.yaml` 是运行时读的**数据文件**，必须随 wheel 分发。`hatchling` 的
`packages = ["src/ghrah"]` 会带上它，但换构建后端或加 `include` 白名单时要复查。

## 8. 回滚

迁移全部落在工作树，未提交。回滚方式：

```sh
git checkout -- ghrah-core/src/ghrah/abilities ghrah-core/src/ghrah/manifest
git clean -fd ghrah-core/src/ghrah/scheme
```

回滚后运行时行为与迁移前一致（快照基线可复现）。

## 9. 下一步建议

1. **先解决 `ghrah.scheme` 命名冲突**（§7.1），否则任何"以 scheme 为家"的推进都踩在别人名字上。
2. **定资源与授权落点**：`PERMISSIONS` 中的 `allowed_paths` / `require_hitl` 该进资源槽与授权记录，
   而不是长期躺在实现脚本里。
3. **把 `SUMMARY` / `TAGS` / `TOOL` 迁回定义面**，让实现侧只剩实现与 Hook 声明。
4. **清理死代码与适配器落点**（§7.5、§7.6），再谈删旧树。

## 8. 第五轮：skill 包迁入 ability 定义面（2026-10-26）

skill 不再自带 frontmatter，而是作为一个**能力包**：定义在首层 ``ability.yaml``，
正文在 ``skills/SKILL.md``。

| | 迁移前 | 迁移后 |
| --- | --- | --- |
| 定义面 | ``SKILL.md`` 的 frontmatter（``name`` / ``description``） | ``ability.yaml``（``identity`` / ``title`` / ``description`` / ``skills`` 成员） |
| 正文 | 包根 ``SKILL.md``（含 frontmatter 块） | ``skills/SKILL.md``（只剩正文） |
| 身份 | 包目录名 | ``ability.yaml`` 的 ``identity``；容器名与目录名做一致性校验 |
| 版本 | ``ability.lock.yaml`` 的 ``version`` | ``ability.yaml`` 的 ``identity.version``（lock 不再管版本） |
| 权限 | lock（由 frontmatter 申请值直通） | lock（**不变**，定义面不出现该字段） |

### 8.1 身份口径（乙）

``identity.namespace`` 即**包名**；``identity.name`` 可省。派生规则：

```
full_name = f"{namespace}.{name}"   # name 存在
full_name = namespace               # name 缺席 → 用户级包退化为单层身份
```

用户级包（``~/.ghrah/skill-ability/<包名>/``）只写 ``namespace``，因此
``full_name == 包名``（如 ``research``），与迁移前一致；系统能力写
``namespace: ghrah`` + ``name: fs``，得到 ``ghrah.fs``。

一致性校验的对象是**容器名**（``identity.name``，缺席时取 ``namespace``）与包目录名——
不能直接拿 ``namespace`` 比目录名，因为系统能力的目录是 ``fs`` 而 namespace 是 ``ghrah``。

### 8.2 迁移期双格式并存

``ghrah.skills.scan`` 按**文件存在性**显式判定：有 ``ability.yaml`` 走新格式，否则回退旧格式
（包根 ``SKILL.md`` + frontmatter）。这样存量包可以逐个迁移而不打断读取端。
旧分支与 ``tests/skills/test_scan.py`` 在存量全部迁完后一并删除；新格式的测试在
``tests/skills/test_scan_definition.py``。

### 8.3 存量迁移结果

``~/.ghrah/skill-ability/`` 下 12 个包全部迁移（备份在
``~/.ghrah/skill-ability.backup-<时间戳>/``）：

- 12 个 ``ability.yaml`` 全部通过 ``AbilityDefinition`` 校验；
- 扫描结果：12 个包、0 issue、指纹无漂移；
- frontmatter 里的附加键（``tags`` / ``related_skills`` / ``source_book`` /
  ``source_chapter`` / ``metadata``）无损保留：``tags`` 进 ``SkillDefinition.tags``，
  其余原样进 ``SkillDefinition.annotations``（框架不解释语义）；
- ``test-prompts.json`` / ``CREDITS.md`` **原样未动**——它们不属于声明的结构，
  若要归入 ``assets/`` 需先定成员键约定。

### 8.4 目录池：能力面与 skill 面合流（2026-10-26）

迁移完成后暴露出最后一处结构性问题：**两面各有一个写死的扫描根**。

- 能力面：旧 `ability_catalog` 用 `importlib.resources` 定位随包的 `ghrah/scheme/ability/`；
- skill 面：`ghrah.skills.roots.default_skill_root()` 定位 `~/.ghrah/skill-ability`。

同一份 ``ability.yaml`` 格式、同一份权威，却被两条互不知情的路径各读一半——12 个已迁移的
用户包在能力面完全不可见（用 `plans/selfcheck/query_all_abilities.py` 一跑即暴露）。

改为**目录池**：根成为一等公民，带 rank/source/required，两面对同一个池取数。

| 件 | 位置 | 作用 |
| --- | --- | --- |
| `AbilityRoot` / `default_pool()` | `ghrah-core/src/ghrah/scheme/loading/ability_roots.py` | 内置树 rank 0（required）+ `~/.ghrah/skill-ability` rank 100（可选） |
| `scan_ability_definitions(pool)` | `ghrah-core/src/ghrah/scheme/loading/ability_scan.py` | 按池扫盘，只解析定义、不 import 实现；同名容器 rank 小者胜出，落败者记 issue |
| `scan_catalog(pool=…)` | `ghrah-core/src/ghrah/scheme/loading/catalog.py` | 能力面 + skill 面合流成一份 `CatalogReport` |
| `ghrah.skills.roots` | `ghrah-core/src/ghrah/skills/roots.py` | 改为**转调** `ability_roots`（不再持有自己的路径常量） |

要点：

- **skill 就是"只有 `skills`、没有 `operations` 的 ability 包"**，因此不需要第三套扫描器；
- **rank 语义**：内置树 rank 0 且 required——它随包分发，若可被同名覆盖或可缺省，一份外部
  yaml 就能改掉 `read_file` 的语义，打包损坏也会被误判成"用户没装能力"；
- **不静默覆盖**：rank 落败的那份记一条扫描问题，否则"我的包为什么没生效"不可诊断；
- **边界**：`list_abilities`（模型工具面）仍只列内置实现面的投影 + 装配期注入的 skill provider
  行；纯 skill 包不进工具面，它们经 `load_skill` 可达——工具面吃"没有实现脚本的声明"会把
  模型引向不可调用的名字。

实测：17 个容器（5 内置 + 12 用户）、22 个 handler、21 个 view、12 个 skill 成员、0 issue。

### 8.5 容器视图：扫描以 ability 包为单位（2026-10-26）

目录池解决"扫哪些根"，但读数仍是扁平的：`entries` 与 `skills` 是两条平行列表，问"每个包里各有几个 operation / skill"要调用方自己按容器名归并。

改为**以 ability 容器为第一维度**——一个包可以有 `operations`（旧口径"工具"），也可以有 `skills`（后续还有 `mcp`），扫完直接看出每一类组件各多少：

```python
from ghrah.scheme.loading.ability_scan import scan_abilities

for container in scan_abilities():  # 池默认取 default_pool()
    print(container.name, container.kinds, container.operation_count, container.skill_count)
    for issue in container.issues:  # 只属于这个包的问题
        print("  !", issue)
```

| 类型 | 字段 |
| --- | --- |
| `AbilityContainer` | `name` / `namespace` / `version` / `title` / `description` / `root` / `locator` / `definition` + `operations` / `skills` / `assets` / `issues`，派生 `full_name` / `kinds` / `operation_count` / `skill_count` / `asset_count` / `member_count` / `is_operation_only` / `is_skill_only` |
| `AbilityOperation` | `key` / `full_name` / `definition`（权威载荷），派生 `title` / `description` / `provider_slot` / `handler` / `timeout_ms` / `effects` |
| `AbilitySkill` | `key` / `full_name` / `title` / `description` / `media_type` / `body_path` / `body_locator` / `tags` / `source` / `rank`——**只给声明读数，不读正文** |
| `AbilityAsset` | `key` / `full_name` / `definition`（`assets` 当前是自由结构，不解释语义） |

要点：

- **顺序确定**：容器按 `name` 升序、成员按 `key` 升序，跨文件系统/跨机器同序，可用于快照比对；
- **问题归属**：包内毛病挂 `AbilityContainer.issues` 且带 `container`；根级毛病（required 根缺失）`container=None`，不进任何容器。仍是收集式——坏包照样出现，不因一份坏 yaml 让整面不可读；
- **不是第二权威**：`AbilityScanResult.containers` 就是 `scan_abilities()` 的返回，`catalog.CatalogReport.containers` 再透传，全部由同一份 `ability.yaml` 派生。

**测试**：`ghrah-core/tests/scheme/loading/test_ability_containers.py`（9 例）。全套 `pytest ghrah-core/tests/scheme` 200 例通过，`ruff check` / `ruff format --check` 全绿。

### 8.6 模型上下文装载：operations → 工具面，skills → 目录 + 正文（2026-10-26）

容器视图回答了"有哪些包、每个包里有什么"，但**装进模型上下文**是另一回事：那一步只需要定义面（`ability.yaml`），不需要 import 任何实现脚本。因此补两个纯定义读数模块，与 `ability_catalog`（产运行时 view）分工如下：

| 模块 | 输入 | 产物 | 是否 import 实现 |
| --- | --- | --- | --- |
| `ability_catalog` | yaml + 实现脚本 | `AbilityManifest`（运行时 view，含 handler / 权限 / hooks） | 是 |
| `tool_list` | 只读 yaml | OpenAI function 列表 | 否 |
| `skill_context` | 只读 yaml + 正文文件 | 目录注入文案 + 按名字读正文 | 否 |

**工具面**（`ghrah-core/src/ghrah/scheme/loading/tool_list.py`）：

```python
from ghrah.scheme import load_tool_list

tools = load_tool_list()  # 池默认 default_pool()
tools.names  # ('read_file', 'write_file', ...) 成员键，升序
tools.tools[0]["function"]["name"]  # "read_file"
tools.get("read_file").container  # "fs"——归属可反查，不靠猜前缀
```

- 工具名就是**成员键**（`read_file`），不是全名——模型要拿这个名字调 handler，显示别的名字会让它调不动；
- `expose` 是 yaml 上的显式开关（`operations.<key>.expose: false`）：`conversation`（模型不通过工具触发）与 `send`（registry-only，运行时不投影 manifest）都标了 false，于是"声明面"与"绑定面"一致；
- 原来各实现脚本里的模块级 `TOOL` 常量已删除——yaml 是唯一开关，脚本不再参与"是否进工具面"的判断。

**技能面**（`ghrah-core/src/ghrah/scheme/loading/skill_context.py`），对齐 DSH 的两段式注入：

```python
from ghrah.scheme import load_skill_catalog

catalog = load_skill_catalog()
catalog.names  # ('research', 'pr', ...) 成员键
catalog.render()  # 一段 <system-reminder>...</available_skills> 文本
catalog.read("research")  # SkillDocument(content=正文逐字, media_type=..., source=...)
```

- 第一段（目录）每轮注入：名字 + 一行 description（空白折叠、超长截到 500 字符、尖括号转义）；
- 第二段（正文）不进注入物：模型选定后按名字读盘，`read()` 只认扫描产出的成员读数，不按名字拼路径；
- **目录摘要 ≠ 注册指纹**：`digest_skill_catalog()` 只对"名字 + 截断后的 description"取 SHA-256，正文改动不触发重新注入；`ghrah.skills.lock` 的指纹才是正文 SHA-256；
- 渲染函数只依赖 `catalog_name` / `description` 两个属性（`SkillCatalogEntry` Protocol），因此 `ghrah.skills` 的 `SkillSummary` 可直接喂进同一套文案。

**skill 侧改为委派**：`ghrah-core/src/ghrah/skills/catalog.py` 与 `skills/document.py` 不再各自实现渲染与读盘，改为转调上述两个函数——"目录文案长什么样"与"正文怎么读"只在 scheme 里定义一次（`skills/query.py` 的 `SkillSummary.description` 也改成取包定义面的 `description`，口径与 scheme 一致）。

**两个面的一致性有测试钉住**：`test_tool_list.py::TestToolListMatchesRuntime` 用真实内置树比对"yaml 工具面"与"运行时绑定面"——名字集合相同、每个工具说明相同（当前各 20 个）。本轮据此发现 `send` 虽在 yaml 里声明、运行时却不绑定，遂补 `expose: false` 使两面收敛。

**测试**：`test_tool_list.py`（11 例）+ `test_skill_context.py`（23 例）；`pytest ghrah-core/tests/scheme ghrah-core/tests/skills` 416 例通过，`ruff check` / `ruff format --check` 全绿。

### 8.7 技能包管理整体并入 scheme（2026-10-26）

上一轮只把"渲染目录文案"和"按名字读正文"两件事搬进了 scheme，技能包**管理**（扫描、注册指纹、lock、初始化、query、MCP 渲染、provider）仍在 `ghrah/skills/` 里。本轮把它们全部搬过去，`ghrah.skills` 退化成纯别名壳，为后续整包删除铺路。

| 原模块（`ghrah/skills/`） | 新模块（`ghrah/scheme/loading/`） | 行数 | 职责 |
| --- | --- | --- | --- |
| `errors.py` → | `skill_errors.py` | 27 | `SkillError` / `SkillLockError` / `SkillNotFoundError` / `SkillParseError` |
| `models.py` → | `skill_models.py` | 291 | `SkillRoot` / `SkillSummary` / `SkillScanResult` / `LockRecord` / `SkillFingerprint` / `BundleVerification` / `SkillInitResult` / `SkillDocument` |
| `scan.py` → | `skill_scan.py` | 360 | 包扫描（新格式解析、正文定位、按 rank 去重、问题收集） |
| `fingerprint.py` → | `skill_fingerprint.py` | 627 | 整树观测与两枚摘要（content / snapshot） |
| `lock.py` → | `skill_lock.py` | 287 | `ability.lock.yaml` 的读写与迁移 |
| `query.py` → | `skill_query.py` | 197 | 指纹查询与校验 |
| `initialize.py` → | `skill_init.py` | 137 | 技能包初始化（写定义面 + lock） |
| `mcp_config.py` → | `skill_mcp.py` | 283 | MCP 规格加载与目录 / schema 渲染 |
| `provider.py` → | `skill_provider.py` | 181 | `SkillAbilityProvider`（外部唯一聚合入口） |
| `roots.py` → | `skill_roots.py` | 90 | 默认技能根与目录池（转调 `ability_roots` 的 rank/source） |

合计 **约 2280 行**实现搬入 scheme；`ghrah/skills/` 下对应文件变成 18 行的别名壳。

**搬运手法：模块对象别名，不复制一行实现。** 每个壳只做一件事：

```python
import sys

from ghrah.scheme.loading import skill_scan as _target

sys.modules[__name__] = _target
```

这样 `ghrah.skills.scan` 与 `ghrah.scheme.loading.skill_scan` 是**同一个模块对象**：属性读写、私有助手（`_fingerprint_file` 之类）与 monkeypatch 都落在新实现上。若改用 `from x import *` 或显式再导出，私有名字会丢、patch 会打到空壳上——这条在上一轮 `abilities/` 的兼容层里已经踩过。

**外部消费点全部改指 scheme**（不再经过壳）：

| 位置 | 原来 | 现在 |
| --- | --- | --- |
| `ghrah-core/src/ghrah/context/manager.py:46` | `from ghrah.skills.catalog import digest_catalog, render_catalog_message` | `from ghrah.scheme.loading.skill_context import digest_skill_catalog, render_skill_catalog`（调用点同步改名） |
| `ghrah-core/src/ghrah/core/unit.py:658` | `from ghrah.skills import SkillAbilityProvider` | `from ghrah.scheme.loading.skill_provider import SkillAbilityProvider` |
| `ghrah-core/src/ghrah/scheme/ability/core/operations/list_abilities.py:38` | `from ghrah.skills import SkillFingerprint, SkillNotFoundError` | 拆成 `loading.skill_models` / `loading.skill_errors` 两行 |
| `ghrah-core/src/ghrah/scheme/ability/core/operations/load_skill.py:29-34` | `from ghrah.skills import ...`、`from ghrah.skills.mcp_config import ...` | `loading.skill_errors` / `loading.skill_mcp` |

`ghrah-subject/src/ghrah/subject/units/skill.py:21` 仍 `from ghrah.skills import SkillAbilityProvider`——它靠壳工作，不受影响。这符合"本轮只改 `ghrah-core/`"的边界；等 skills 包真要删除时，这一行必须先改指 scheme。

**门面新增 30 个名字**（`ghrah-core/src/ghrah/scheme/__init__.py`，导出总数 148 → 178）：`scan_skills`、`initialize_skills`、`SkillAbilityProvider`、`default_provider`、`SkillSummary`、`SkillFingerprint`、`query_fingerprint(s)`、`verify_fingerprint`、`read_lock_record`、`read_skill_document`、`load_mcp_specs` 等。外部此后只 import `ghrah.scheme` 一处。

**同名类的处置**：定义面与技能包面各有一个 `SkillDocument`（前者带容器 / 成员键 / 媒体类型，后者带 `skill_dir` / `path`），形状不同。门面导出时：

- `ghrah.scheme.SkillDocument` = 定义面那个（`loading.skill_context`）；
- `ghrah.scheme.SkillPackageDocument` = 技能包面那个（`loading.skill_models` 里的别名），避免同名遮蔽。

两者是否合并留作后续项（合并要动 `load_skill` 的返回结构与 subject 的用法）。

**测试**：新增 `ghrah-core/tests/scheme/loading/test_skill_home.py`（9 例，三组）钉住"住在哪"——

- `TestSkillCapabilityHome`：`SkillAbilityProvider` / `scan_skills` / `initialize_skills` / `read_skill_body` / `read_skill_document` / `query_fingerprints` / `load_mcp_specs` 的 `__module__` 都在 `ghrah.scheme.loading.*`；
- `TestSkillsPackageIsAlias`：10 个壳模块的 `__name__` 等于 scheme 模块名（证明是同一对象而非复制），`ghrah.skills.scan is skill_scan`；
- `TestSchemeFacadeExportsSkillFace`：门面导出的技能面名字都解析到 scheme，且两个 `SkillDocument` 不互相遮蔽。

**验证**：`pytest ghrah-core` → 2379 passed / 4 skipped / 1 failed（`test_fs_permissions` 的 `/tmp/data` vs `D:\tmp\data`，Windows 平台既有失败，与本轮无关）；`ruff check .` 全绿；`ruff format --check .` 579 文件全绿。

**下一步（删除 `ghrah.skills` 的前置条件）**：

1. `ghrah-subject/src/ghrah/subject/units/skill.py:21` 改指 `ghrah.scheme.loading.skill_provider`（唯一剩下的外部引用）；
2. `ghrah/skills/catalog.py` / `document.py` 两个转调文件里的 `digest_catalog` / `render_catalog_message` / `read_skill_document` 调用点改指 scheme（`ghrah.skills.__init__` 的聚合导出随之删除）；
3. 删掉 `ghrah/skills/` 整包，同时移除 `ghrah-core/pyproject.toml` 里若有指向它的配置（当前无）。

### 8.8 加载流程合并：技能遍历并入 ability 扫盘（2026-10-26）

§8.7 把技能包管理搬进了 scheme，但搬进来的是**一套与能力无关的平行流程**：`skill_scan` 自己遍历目录、`skill_query` 自己比对指纹、`skill_init` 自己写锁、`skill_roots` 自己拼根。于是"加载"这件事有两份实现——`ability_scan` 按池遍历一次定义，`skill_scan` 再遍历一次；容器定义里的 `skills:` 成员与磁盘上的技能包还是两类对象。

本轮的口径是「加载流程只有一条」：**技能成员是池扫描的产物，不是第二次遍历的产物**。

| 删除的模块 | 行数 | 能力去向 |
| --- | --- | --- |
| `loading/skill_scan.py` | 360 | `loading/ability_scan.py`：`_scan_root` 一次遍历里顺带产出 `AbilitySkill` |
| `loading/skill_query.py` | 197 | `loading/ability_scan.py` 的 `verify_skill()`；读数落在 `AbilitySkill.fingerprint` / `AbilitySkill.lock_issue` |
| `loading/skill_init.py` | 137 | `loading/ability_scan.py` 的 `ensure_locks()` / `_lock_data()`；外层入口仍是 `SkillAbilityProvider.initialize()` |
| `loading/skill_roots.py` | 90 | `loading/ability_roots.py`：技能根就是池里的用户根（rank 100），不再有第二份路径表 |

合计 **784 行**实现与 4 个别名壳。合并后 `loading/` 由 20 个模块降到 16 个。

**读数是成员本身。** `AbilitySkill` 新增四个字段：

| 字段 | 含义 |
| --- | --- |
| `skill_dir` | 包目录（`None` = 定义面无实现、无法落锁） |
| `lock_path` | `ability.lock.yaml` 的路径 |
| `fingerprint` | 整树两枚摘要（`content_digest` 跨机可比 / `snapshot_digest` 含 mtime） |
| `lock_issue` | 锁缺失、损坏或与磁盘漂移时的归一化说明（`None` = 一致） |

`AbilityScanResult` 相应增加 `lock_issues`（与 `issues` 分开：前者是"包在、锁不对"，后者是"定义读不出来"）。`load_ability_skills()` 是唯一的技能成员读数，`catalog` / `skill_context` / `list_abilities` 都从它取。

**架构约束：扫描只读盘。** `scan_ability_definitions` / `scan_abilities` / `load_ability_skills` 不写任何文件；写锁只有 `ensure_locks()`（装包时批量补齐）与 `relock_skill()`（单个重登记）两个显式入口。理由：`scan` 会被目录注入、工具列表装配、`list_abilities` 这类只读路径调用，若它顺手补锁，一次"看一眼"就改了用户磁盘状态，也让并发读变成写竞争。

**技能成员全名收敛。** 成员键与容器同名时，`full_name` 取容器名——`research` 而不是 `research.research`。原来 `skill_scan` 一律拼 `f"{container}.{key}"`，导致 12 个真实技能包（包名与技能名相同）在目录文案、`load_skill` 参数、`list_abilities` 详情里都显示成叠词。规则落在一处（`AbilitySkill` 构造处），`operations` 不受影响：能力成员与容器同名是常态（`core.read_file` 之类），且工具名必须与 `handler` 一致。

**顺带收窄**：

- `ghrah/skills/` 的别名壳从 10 个降到 6 个：`errors` / `fingerprint` / `lock` / `mcp_config` / `models` / `provider`（`__init__.py` 的聚合导出同步缩小）；
- `list_abilities` 的成员详情不再读正文 frontmatter，改读定义面字段（`title` / `tags` / `body_path` / `media_type`）。

**验证**：`pytest ghrah-core` → 2305 passed / 4 skipped / 1 failed（`test_fs_permissions` 的 `/tmp/data` vs `D:\tmp\data`，Windows 平台既有失败）；`ruff check .` 与 `ruff format --check .` 全绿（565 文件）；全仓 `*.py` CRLF 0。

**已知副作用（待决）**：旧格式正文的 frontmatter 是 permission 的唯一申请通道，新格式正文是纯 Markdown，于是新包无法声明 `approve: auto` 之类——`extract_permission_request` 收到的永远是空映射，锁里恒为保守默认（`approve=hitl`、`files={}`、`net=deny`），只有手工改 `ability.lock.yaml` 才能改变生效值。`ghrah-subject/tests/test_units_skill.py` 的 4 例正是断言旧通道（正文申请 `auto` → 锁出现 `auto`），当前红。三条出路：

| 选项 | 做法 | 代价 |
| --- | --- | --- |
| A（推荐） | 在 `ability.yaml` 的 `skills.<key>` 上加 `permission` 字段，`extract_permission_request` 改读定义面 | 动 `ability_schema` + 承认"权限申请"进入定义面 |
| B | 承认申请通道已退役，删掉那 4 例里关于 `approve` 的断言，只保留"锁被创建且指纹自洽" | 删测试（该特性真没了） |
| C | 保留 frontmatter 只为权限 | 与新格式"正文是纯正文"冲突，不建议 |

**`ghrah.skills` 的删除前置条件**（相比 §8.7 缩短）：只剩 `ghrah-subject/src/ghrah/subject/units/skill.py:21` 一处外部 import `ghrah.skills`，改指 `ghrah.scheme.loading.skill_provider` 后即可整包删除；`skills/catalog.py` 与 `skills/document.py` 两个转调文件已随壳收窄一并删除，不再存在。

### 8.9 `ghrah/skills/` 回退到 HEAD（2026-10-26）

§8.6–§8.8 把技能包管理（扫描、指纹、锁、初始化、查询、MCP 渲染、provider）搬进了 `scheme/loading/skill_*`，`ghrah/skills/` 退化为别名壳。随后决定不再改这个包，**整包恢复到 HEAD**，scheme 侧实现保持独立。

| 动作 | 对象 |
| --- | --- |
| `git checkout HEAD --` | `ghrah-core/src/ghrah/skills/` 全部 11 个模块 |
| 删除（HEAD 中不存在） | `ghrah-core/src/ghrah/skills/fingerprint.py`、`ghrah-core/src/ghrah/skills/mcp_config.py` |
| 测试改指 `ghrah.scheme.loading` | `ghrah-core/tests/context/test_skill_catalog_injection.py`、`ghrah-core/tests/abilities/builtin/test_list_abilities.py`、`ghrah-core/tests/abilities/builtin/test_load_skill.py` |
| 回退测试目录 | `ghrah-core/tests/skills/` 整目录 → HEAD（`test_catalog` / `test_document` / `test_initialize` / `test_provider` / `test_query` / `test_scan`，91 例） |
| 删除测试 | `ghrah-core/tests/skills/test_fingerprint.py`、`test_mcp_config.py`（壳阶段新增，覆盖已在 `tests/scheme/loading/test_skill_lifecycle.py`、`test_ability_scan.py`）；`ghrah-core/tests/scheme/loading/test_skill_home.py`（断言壳别名，前提消失） |
| 回退测试 | `ghrah-subject/tests/test_units_skill.py` → HEAD（重新断言锁 schema 1） |

回退后的格局：

- `ghrah/skills/`：旧格式（`SKILL.md` + frontmatter）、锁 schema 1、单文件 `provenance.fingerprint`；唯一生产消费者是 `ghrah-subject/src/ghrah/subject/units/skill.py`。
- `ghrah/scheme/loading/skill_*`：新格式（`ability.yaml` + `skills/SKILL.md`）、schema 2 树指纹；能力面与技能面共用同一次扫盘。
- 两侧无 import 关系，各跑各的。**并存是当前事实**，是否合并等决策（越界 `ghrah-core/`，见 §7）。

验证：`pytest ghrah-core` → 2313 passed / 4 skipped / 1 failed（既有 Windows 路径断言）；`pytest ghrah-subject` → 717 passed / 7 skipped；`ruff` 全绿（569 文件）。

### 8.10 一个容器一个技能；正文路径只认声明（2026-10-26）

两条收紧，目的都是把「声明与读取可能各说一套」这类隐患在框架侧消掉。

**① 一个能力包只支持一个 skill 成员。** 技能是容器的插件包，容器与技能一一对应，多条声明没有可表达的落点，因此在契约层 fail-closed：`parse_definition` 在 `len(skills) > 1` 时抛 `ManifestValidationError`（报出声明了哪几个键），扫描把它记成问题、该容器不进入清单——与其它契约违规的处理一致，不静默只收其中一条。

**② 读正文一律走声明路径 `skills.<key>.body.path`。** 之前框架在三处各自拼 `skills/SKILL.md`：成员读数、入口哨兵、lock 复算。现在这条路径只有一个来源——定义面。

| 位置 | 变化 |
| --- | --- |
| `ghrah-core/src/ghrah/scheme/loading/ability_scan.py` | `AbilitySkill.body` 由 `body_path` 拼出，不再调 `skill_body_path()`；`_member_fingerprint` 新增 `body_relpath` 参数 |
| `ghrah-core/src/ghrah/scheme/loading/skill_fingerprint.py` | `entry_relpaths` / `entry_digest` / `fingerprint_entry` 新增 `body_relpath` 关键字参数（默认仍是本版约定的 `skills/SKILL.md`） |
| `ghrah-core/src/ghrah/scheme/loading/skill_lock.py` | `read_lock_record` 新增 `body_relpath`，用于从登记的整树观测复算入口哨兵 |
| `ghrah-core/src/ghrah/scheme/loading/skill_context.py` | `read_skill_document` 优先按 `skill_dir + body_path` 定位；`SkillDocument` 增加 `body_path` 字段，`skill_dir` / `path` 允许为 None（包资源容器无磁盘路径时不再拼一个不存在的路径） |
| `ghrah-core/src/ghrah/scheme/loading/ability_schema.py` | 删除 `skill_body_path()`（已无生产调用者）；`SkillBody.path` 增加归一化校验（统一 `/`、去空段与 `.`），越界判定仍只由 `_skill_body_locator` 一处裁决 |
| `ghrah-core/src/ghrah/scheme/ability/core/operations/load_skill.py` | 返回载荷新增 `body_path`；容器不在文件系统上时明确失败/不带 MCP，而不是拼出 `None` |

因为哨兵、lock 复算、正文读取现在共用同一个 `body_path`，「算指纹的那份文件」与「读正文的那份文件」不可能再分叉；把正文改叫别的名字（如 `skills/GUIDE.md`）也能被正确登记与检测漂移。

验证：`pytest ghrah-core` → 2315 passed / 4 skipped / 1 failed（同上）；`pytest ghrah-subject` → 717 passed / 7 skipped；`ruff` 全绿（572 文件）；存量 12 个 skill 包扫盘 `issues 0`、`drifted False` 全绿。
