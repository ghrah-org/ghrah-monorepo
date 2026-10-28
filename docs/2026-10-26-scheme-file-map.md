---
title: scheme 包文件级功能与调用关系
description: 逐个文件说明 ghrah/scheme 下每个模块的职责、依赖与消费者，并给出关键调用链；调用链停在文件粒度
tags: [清点, 能力系统, Scheme, 调用链]
keywords: [scheme, 调用链, 依赖, 冗余, tool_list, manifest]
created: 2026-10-26
updated: 2026-10-26
status: done
---

# scheme 包文件级功能与调用关系

> 配套文档：[模块清点与冗余排查](2026-10-26-scheme-module-inventory.md)（讲冗余怎么判）、[Ability 与 Manifest 一体化迁移记录](2026-10-26-scheme-ability-migration.md)（讲改动是怎么搬过来的）。
>
> 本文的行数、依赖边、消费者清单由 AST 静态扫描生成（脚本 `plans/gen_filemap.py` → `plans/filemap.json`），不是人工印象。调用链只到**文件**粒度，不追函数。

## 0. 先回答：`load_tool_list` 与 `load_all_builtin_manifests` 是一回事吗

**不是一回事；两者只在「工具体怎么算」这一步共用同一份实现，所以没有两套参数规则。**

| 维度 | `loading/tool_list.py::load_tool_list` | `loading/ability_catalog.py::load_all_builtin_manifests` |
| --- | --- | --- |
| 扫描范围 | 整个目录池：内置树 `ability/`（rank 0）+ 用户目录 `~/.ghrah/skill-ability`（rank 100） | **只有内置树**（`ability_roots.builtin_pool()`） |
| 是否 import 实现脚本 | 否——纯定义读数 | 是——要读 `PERMISSIONS` / `HOOKS` / `SUMMARY` / `TAGS` |
| 能否处理「只有声明、没有实现」的包 | 能（用户包就是这种） | 不能，投影期直接 `ManifestValidationError` |
| 产物 | `ToolList`：`tools`（可直接喂模型）+ `entries`（工具名 → 容器/成员）+ `roots` + `issues` | `{全名: AbilityManifest}`，工具体在 `manifest.tool` |
| 内置树覆盖的成员 | 定义面里 `expose != false` 的成员 | 除 `send`（registry-only）外的全部成员，其中 `expose == false` 者的 `tool` 为 `None` |
| 失败模式 | 收集式：单个包坏了记 `issues`，其余照出 | 抛异常：内置树坏了就是发布事故 |

同源证据：两者的工具体都由**同一个函数**算出来——`ability_catalog.build_tool_schema()`（`loading/tool_list.py:39` import 它，`_project()` 内部也走它）。所以「参数契约」只有一份实现。

**真正的重复在两处**：

1. **成员集合重合**：在内置树这一层，两者覆盖的成员完全相同——都以 `operation.expose` 为准（`build_tool_schema()` 对 `expose: false` 返回 `None`，`_project()` 也把 `manifest.tool` 置为 `None`），因此 `conversation` 两边都不进工具面。差别只在「能不能处理没有实现脚本的包」与「要不要抛出」。
2. **同一份定义面的两次派生**：`AbilityManifest.tool` 与 `ToolList.tools` 都是从 yaml 派生的工具体。前者跟随 view（必须先 import 实现脚本），后者只要 yaml。任何「给模型注入工具面」的新代码都可以只用后者，拿到的字段与前者逐个相同（`tests/scheme/loading/test_tool_list.py` 已钉住形状）。

**建议的收敛方向**（未做）：把「模型看到哪些工具」收敛到 `load_tool_list` 一条路径；`AbilityManifest.tool` 保留字段但不参与装配；`list_abilities` 的展示继续读 view（它还需要 `SUMMARY` / `PERMISSIONS` 这些**实现面**信息，那是 `tool_list` 拿不到的）。

## 1. 分层总览（文件粒度）

```text
                      ┌──────────────────────────────┐
   yaml 定义面 ──────▶ │ loading/ability_scan.py      │ 按目录池扫盘（只读，不 import 实现）
   （ability/*/）      │  ├ ability_roots.py（池）    │
                      │  └ ability_schema.py（格式） │
                      └───────────┬──────────────────┘
                                  │ AbilityScanResult / AbilityContainer
        ┌─────────────────────────┼──────────────────────────┐
        ▼                         ▼                          ▼
 loading/tool_list.py     loading/ability_catalog.py    loading/catalog.py
 （工具面，纯定义）        （运行时 view，import 实现）    （对外报告，两面合流）
        │                         │                          │
        │                         ▼                          │
        │            loading/ability_view.py（AbilityManifest）│
        │                         │                          │
        │            ┌────────────┴───────────┐              │
        │            ▼                        ▼              │
        │   agent_resolver.py          execution/ability_build.py
        │   （agent 定义 → 能力引用）    （ResolvedAbility → Ability 实例）
        │                                     │
        ▼                                     ▼
  模型工具面                          execution/executor.py（执行）
                                             │
                              ┌──────────────┼───────────────┐
                              ▼              ▼               ▼
                    execution/hook_runner  permissions/*   ability/*/operations/*.py

  loading/skill_context.py ── 技能目录文案 + 正文读取 ──▶ context/manager.py、load_skill
  loading/skill_provider.py ── 外部聚合入口（scan/read/verify/initialize/relock）
```

## 2. 逐文件说明

表中「依赖（→）」只列 `ghrah.scheme` / `ghrah.skills` 内部的 import 边；「被谁 import」只列 **scheme 之外**的仓内文件（`ghrah-subject`、`ghrah-core` 其它包、examples），最后一列给出 scheme 内部还有多少个文件 import 它。测试不混进生产消费者。

本文逐文件覆盖 **70 个模块 / 14686 行**。四个子包的 `__init__.py`、`ability/__init__.py`、五个容器目录的 `__init__.py` 与 `operations/__init__.py` 不在表内：它们只有 docstring，不含逻辑。

### 2.1 根部（6 个）

| 文件 | 行 | 职责 | 依赖（→） | 被谁 import（scheme 之外） | scheme 内 |
| --- | --- | --- | --- | --- | --- |
| `_cluster_common` | 50 | 集群能力共用件（目标解析、supervisor 未接线时的统一错误） | — | — | 6 个 |
| `agent_manifest` | 142 | agent 定义格式模型（`manifest: agent`） | `ghrah.scheme.loading.manifest_fields` | `manifest_store/store.py` | 2 个 |
| `agent_resolver` | 193 | `AgentManifest` → `AgentConfig` + `ResolvedAbility[]` | `ghrah.scheme.agent_manifest`、`ghrah.scheme.loading.ability_catalog`、`ghrah.scheme.loading.ability_view`、`ghrah.scheme.loading.manifest_fields` 等 5 个文件 | `core/unit.py`、`manifest_store/service.py` | 2 个 |
| `errors` | 26 | 能力面异常（`AbilityError` / `AbilityNotFoundError`） | — | `agents/base.py`、`core/__init__.py`、`core/exceptions.py` | — |
| `kinds` | 42 | 能力类型常量（`FS_ABILITY_TYPES` / `CLUSTER_ABILITY_TYPES`） | — | `core/unit.py` | 1 个 |
| `paths` | 180 | 路径参数规格表 + `is_subpath` + 模板展开 | — | `sandbox/executor.py` | 3 个 |

### 2.2 `execution/` — 执行运行时（12 个）

| 文件 | 行 | 职责 | 依赖（→） | 被谁 import（scheme 之外） | scheme 内 |
| --- | --- | --- | --- | --- | --- |
| `execution.ability_build` | 251 | 能力物化：`ResolvedAbility` → `Ability` 实例 + 权限/HITL 包装 | `ghrah.scheme.agent_resolver`、`ghrah.scheme.kinds`、`ghrah.scheme.loading.ability_catalog`、`ghrah.scheme.paths` 等 7 个文件 | `core/unit.py` | 1 个 |
| `execution.base` | 100 | `Ability` 基类：能力实现的接口契约（纯接口，无行为） | `ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | 23 个 |
| `execution.context` | 215 | 执行上下文门面（数据总线 + 服务注册表 + 调用标识） | `ghrah.scheme.execution.execution_data`、`ghrah.scheme.execution.execution_services`、`ghrah.scheme.execution.invocation` | `agents/base.py`、`context/manager.py`、`core/unit.py` | 29 个 |
| `execution.execution_data` | 86 | 执行期数据总线：按类型键存取值，能力与上下文之间传数据 | — | — | 2 个 |
| `execution.execution_frame` | 29 | 执行帧：hook 与 drive-loop 共享的「数据 + 服务」容器 | `ghrah.scheme.execution.execution_data`、`ghrah.scheme.execution.execution_services` | — | 1 个 |
| `execution.execution_services` | 86 | 执行期服务注册表：按类型取宿主服务（文件系统、HITL 等） | — | — | 2 个 |
| `execution.executor` | 802 | 能力执行器：把能力执行从 agent 循环中解耦 | `ghrah.scheme.execution.context`、`ghrah.scheme.execution.hook_context`、`ghrah.scheme.execution.hook_runner`、`ghrah.scheme.execution.hook_store` 等 7 个文件 | `agents/builder.py` | — |
| `execution.hook_context` | 64 | `HookRunner` 消费的上下文对象（执行帧 + 调用标识） | `ghrah.scheme.execution.context`、`ghrah.scheme.execution.execution_frame`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.execution.invocation` | `agents/base.py` | 2 个 |
| `execution.hook_runner` | 59 | 唯一的 hook 执行路径（agent 与本地能力执行共用） | `ghrah.scheme.execution.hook_context`、`ghrah.scheme.execution.hook_store`、`ghrah.scheme.execution.hooks` | `agents/base.py` | 1 个 |
| `execution.hook_store` | 128 | 按 target 索引的 hook 存储 | `ghrah.scheme.execution.hooks` | `agents/base.py` | 2 个 |
| `execution.hooks` | 237 | Hook 机制：能力级控制流（声明 + 触发点） | `ghrah.scheme.execution.context` | `agents/base.py` | 30 个 |
| `execution.invocation` | 21 | 单次能力调用的标识（id + 名字） | — | `agents/base.py` | 3 个 |

### 2.3 `loading/` — 声明读取、扫盘、投影、锁与指纹（16 个）

| 文件 | 行 | 职责 | 依赖（→） | 被谁 import（scheme 之外） | scheme 内 |
| --- | --- | --- | --- | --- | --- |
| `loading.ability_catalog` | 563 | 内置树投影：扫盘 → 解析 → 运行时 view（兼 handler 索引与能力工厂） | `ghrah.scheme.execution.base`、`ghrah.scheme.loading`、`ghrah.scheme.loading.ability_roots`、`ghrah.scheme.loading.ability_scan` 等 7 个文件 | `communication/supervisor.py`、`core/unit.py`、`manifest_store/builtins.py` 等 4 个文件 | 6 个 |
| `loading.ability_roots` | 220 | 能力目录池：扫描根在哪里，以及各自多高优先级。 | — | — | 6 个 |
| `loading.ability_scan` | 939 | 能力目录扫描：在目录池上按「能力容器」收集成员，问题不中断整轮。 | `ghrah.scheme.loading`、`ghrah.scheme.loading.ability_roots`、`ghrah.scheme.loading.ability_schema`、`ghrah.scheme.loading.manifest_fields` 等 8 个文件 | — | 6 个 |
| `loading.ability_schema` | 325 | Ability 定义面模型：``ability.yaml`` 的类型化契约与解析入口。 | `ghrah.scheme.loading.manifest_fields` | — | 5 个 |
| `loading.ability_view` | 85 | 能力的运行时视图：由定义面 + 实现面投影而来，喂给展示与工具面注入。 | `ghrah.scheme.loading.manifest_fields` | `manifest_store/builtins.py`、`manifest_store/store.py` | 4 个 |
| `loading.catalog` | 114 | 对外目录扫描：一次拿到「能力定义面 + 运行时面 + skill 面」，供外层框架调用。 | `ghrah.scheme.loading.ability_catalog`、`ghrah.scheme.loading.ability_roots`、`ghrah.scheme.loading.ability_scan`、`ghrah.scheme.loading.ability_schema` 等 5 个文件 | — | — |
| `loading.manifest_fields` | 187 | 两侧共用的字段模型与校验异常（ToolSchema / PermissionFlags / HookDef / 版本） | — | `communication/supervisor.py`、`manifest_store/service.py`、`manifest_store/store.py` 等 4 个文件 | 7 个 |
| `loading.manifest_io` | 150 | 声明的来源：文本 → 模型（解析）、按名取（内置 store）、取用接口（协议）。 | `ghrah.scheme.agent_manifest`、`ghrah.scheme.loading.ability_catalog`、`ghrah.scheme.loading.ability_view`、`ghrah.scheme.loading.manifest_fields` | `manifest_store/store.py` | 1 个 |
| `loading.skill_context` | 366 | 模型技能面装载：目录注入文案 + 正文按需读取。 | `ghrah.scheme.loading.ability_roots`、`ghrah.scheme.loading.ability_scan`、`ghrah.scheme.loading.skill_errors`、`ghrah.scheme.loading.skill_models` | `context/manager.py` | 1 个 |
| `loading.skill_errors` | 27 | 技能发现与初始化体系的错误类型 | — | — | 7 个 |
| `loading.skill_fingerprint` | 627 | 能力包整树指纹：定义面观测、两枚投影摘要、入口哨兵、差异比对 | `ghrah.scheme.loading.ability_schema`、`ghrah.scheme.loading.skill_errors` | — | 3 个 |
| `loading.skill_lock` | 287 | ``ability.lock.yaml`` 的读取与首次生成。 | `ghrah.scheme.loading.ability_schema`、`ghrah.scheme.loading.skill_errors`、`ghrah.scheme.loading.skill_fingerprint`、`ghrah.scheme.loading.skill_models` | — | 1 个 |
| `loading.skill_mcp` | 283 | 技能包内 MCP 配置的解析与渲染 | — | — | 1 个 |
| `loading.skill_models` | 204 | 技能发现与初始化用到的数据结构 | `ghrah.scheme.loading.ability_scan`、`ghrah.scheme.loading.skill_fingerprint` | — | 5 个 |
| `loading.skill_provider` | 251 | 技能文件系统 Provider：目录池的持有者 + 面向"技能包"的便捷门面。 | `ghrah.scheme.loading.ability_roots`、`ghrah.scheme.loading.ability_scan`、`ghrah.scheme.loading.skill_context`、`ghrah.scheme.loading.skill_errors` 等 5 个文件 | `core/unit.py` | — |
| `loading.tool_list` | 161 | 模型工具面装载：只读 ``ability.yaml``，产出 OpenAI ``tool_list``。 | `ghrah.scheme.loading.ability_catalog`、`ghrah.scheme.loading.ability_roots`、`ghrah.scheme.loading.ability_scan` | — | — |

### 2.4 `permissions/` — 授权判定（3 个）

| 文件 | 行 | 职责 | 依赖（→） | 被谁 import（scheme 之外） | scheme 内 |
| --- | --- | --- | --- | --- | --- |
| `permissions.command_safety` | 469 | 命令安全检查模块：分类和审批逻辑。 | `ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | `core/unit.py` | 2 个 |
| `permissions.fs_permissions` | 302 | 文件系统权限检查模块：共享权限逻辑和访问批准 Hook。 | `ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.paths` | `core/unit.py` | 8 个 |
| `permissions.hitl` | 100 | 内核 HITL 包装件：``require_hitl`` 声明的统一物化实现。 | `ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | 1 个 |

### 2.5 `ability/` — 定义面与实现面（只放能力包）

每个容器一个目录，内含 `ability.yaml`（定义面）与 `operations/<键>.py`（实现面）。成员脚本由 `ability_catalog._operation_module()` 按路径约定 `import`，**不参与任何 import 期注册**。

| 容器 | 成员（= 实现脚本） | 行数合计 |
| --- | --- | --- |
| `cluster` | `broadcast_message`、`query_agents`、`query_manifests`、`send`、`send_message`、`spawn_agent`、`terminate_agent` | 1066 |
| `core` | `conversation`、`end_task`、`list_abilities`、`load_skill`、`recall_context`、`session_info` | 1583 |
| `fs` | `delete_file`、`edit_file`、`list_directory`、`move_file`、`read_file`、`search_files`、`write_file` | 1848 |
| `mcp` | `mcp_call` | 261 |
| `shell` | `execute_command` | 328 |

成员脚本逐个说明（`→` 为它 import 的 scheme 模块）：

| 文件 | 行 | 职责 | 依赖（→） | 被谁 import（scheme 之外） | scheme 内 |
| --- | --- | --- | --- | --- | --- |
| `ability.cluster.operations.broadcast_message` | 142 | BroadcastMessageAbility：向集群中所有 Agent 广播消息。 | `ghrah.scheme._cluster_common`、`ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.cluster.operations.query_agents` | 118 | QueryAgentsAbility：查询集群中已注册的 Agent 信息。 | `ghrah.scheme._cluster_common`、`ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.cluster.operations.query_manifests` | 181 | QueryManifestsAbility：只读查询 agent manifest 定义（含能力清单）。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.cluster.operations.send` | 173 | SendAbility：向 room（及可选定向 targets）发消息的 send 工具。 | `ghrah.scheme._cluster_common`、`ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.cluster.operations.send_message` | 122 | SendMessageAbility：向指定 Agent 发送消息，在本轮提交中准备持久请求。 | `ghrah.scheme._cluster_common`、`ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.cluster.operations.spawn_agent` | 213 | SpawnAgentAbility：以 manifest 实例化的方式动态创建新的平级 Agent。 | `ghrah.scheme._cluster_common`、`ghrah.scheme.agent_resolver`、`ghrah.scheme.execution.ability_build`、`ghrah.scheme.execution.base` 等 6 个文件 | — | — |
| `ability.cluster.operations.terminate_agent` | 117 | TerminateAgentAbility：终止集群中的一个 Agent。 | `ghrah.scheme._cluster_common`、`ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.core.operations.conversation` | 146 | ConversationAbility：无 tool call 的最简对话能力。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.core.operations.end_task` | 215 | EndTaskAbility：终止循环并生成最终回复。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.core.operations.list_abilities` | 419 | ListAbilitiesAbility：发现当前部署的全部 ability（只读自省面）。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.loading.ability_catalog` 等 6 个文件 | — | — |
| `ability.core.operations.load_skill` | 248 | LoadSkillAbility：按 full_name 加载某一个 skill 的 SKILL.md 原文（skil… | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.loading.skill_errors` 等 5 个文件 | — | — |
| `ability.core.operations.recall_context` | 384 | RecallContextAbility：按动作链历史精确回忆消息（compact 配套召回面）。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.core.operations.session_info` | 171 | SessionInfoAbility：环境/会话自省原语（008 T-2 工具形态）。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.fs.operations.delete_file` | 195 | DeleteFileAbility：文件删除能力。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.permissions.fs_permissions` | — | — |
| `ability.fs.operations.edit_file` | 251 | EditFileAbility：精确字符串替换能力。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.permissions.fs_permissions` | — | — |
| `ability.fs.operations.list_directory` | 275 | ListDirectoryAbility：目录列表能力。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.permissions.fs_permissions` | — | — |
| `ability.fs.operations.move_file` | 249 | MoveFileAbility：文件移动/重命名能力。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.permissions.fs_permissions` | — | — |
| `ability.fs.operations.read_file` | 237 | ReadFileAbility：带工具绑定的文件读取能力。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.permissions.fs_permissions` | — | — |
| `ability.fs.operations.search_files` | 424 | SearchFilesAbility：内容/文件名搜索能力。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.permissions.fs_permissions` | — | — |
| `ability.fs.operations.write_file` | 217 | WriteFileAbility：文件写入能力。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.permissions.fs_permissions` | — | — |
| `ability.mcp.operations.mcp_call` | 261 | McpCallAbility：调用 MCP 服务器上的一个工具。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks` | — | — |
| `ability.shell.operations.execute_command` | 328 | ExecuteCommandAbility：命令执行能力。 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.permissions.command_safety` | — | — |

> 这一组的「scheme 内」列全是 `—`，不代表没人用：成员脚本**没有静态 import 边**——模块名由 `_operation_module()` 按路径拼出后动态 import。所以它们的调用入口在 §3.1 的 `_project()` 与 §3.4 的 `create_ability()`，不在 import 图里。

### 2.6 `ghrah/skills/` — 已恢复到 HEAD 的独立实现（11 个）

这一族已按用户要求**恢复到 HEAD 版本**：仍是自成体系的旧实现（旧格式扫描 + schema 1 的锁），
不再是指向 scheme 的别名壳。`ghrah.scheme` 里那套新实现（`loading/skill_*`）保持独立存在，
两者当前**没有替代关系**——src 里已无任何模块 import 它，唯一的外部消费者是
`ghrah-subject` 的 `units/skill.py`（下表「scheme 内」列的 8 个都是本包内部子模块互相 import，不是外部消费者）。

| 文件 | 行 | 职责 | 依赖（→） | 被谁 import（scheme 之外） | scheme 内 |
| --- | --- | --- | --- | --- | --- |
| `skills` | 126 | 已恢复到 HEAD 的独立实现（旧格式扫描 + schema 1 锁，src 内无消费者） | `ghrah.skills.catalog`、`ghrah.skills.document`、`ghrah.skills.errors`、`ghrah.skills.initialize` 等 10 个文件 | `units/skill.py` | 8 个 |
| `catalog` | 103 | 技能目录摘要：渲染成可注入上下文的消息文本，并给出内容摘要。 | `ghrah.skills.models` | — | 1 个 |
| `document` | 50 | SKILL.md 正文读取（按需加载）。 | `ghrah.skills.errors`、`ghrah.skills.models`、`ghrah.skills.scan` | — | 2 个 |
| `errors` | 27 | skill 发现与初始化体系的错误类型。 | — | — | 7 个 |
| `initialize` | 114 | skill 初始化：确保目录 → 扫描 → 补齐缺失的 ``ability.lock.yaml``。 | `ghrah.skills.errors`、`ghrah.skills.lock`、`ghrah.skills.models`、`ghrah.skills.roots` 等 5 个文件 | — | 2 个 |
| `lock` | 169 | ``ability.lock.yaml`` 的读取与首次生成。 | `ghrah.skills.errors`、`ghrah.skills.scan` | — | 3 个 |
| `models` | 155 | skill 发现与初始化的数据结构。 | — | — | 8 个 |
| `provider` | 125 | 技能文件系统 Provider：技能存储目录池的持有者。 | `ghrah.skills.document`、`ghrah.skills.errors`、`ghrah.skills.initialize`、`ghrah.skills.models` 等 7 个文件 | — | 1 个 |
| `query` | 92 | skill 指纹查询：当前 SKILL.md 与 lock 登记值的对比。 | `ghrah.skills.errors`、`ghrah.skills.lock`、`ghrah.skills.models`、`ghrah.skills.scan` | — | 2 个 |
| `roots` | 77 | 技能目录池：默认根目录与目录创建。 | `ghrah.skills.models` | — | 3 个 |
| `scan` | 191 | SKILL.md 扫描与 frontmatter 解析。 | `ghrah.skills.errors`、`ghrah.skills.models` | — | 6 个 |

## 3. 关键调用链（文件粒度）

### 3.1 定义 → 运行时 view（`list_abilities` 那条路）

```text
core/unit.py 或 ability/core/operations/list_abilities.py
  └─▶ loading/ability_catalog.load_all_builtin_manifests()
        ├─▶ loading/ability_scan.scan_ability_definitions(builtin_pool())
        │     ├─▶ loading/ability_roots.builtin_pool()
        │     └─▶ loading/ability_schema.load_yaml() / AbilityDefinition
        ├─▶ loading/ability_catalog._project()
        │     ├─▶ _operation_module()  ← import ability/<容器>/operations/<键>.py
        │     ├─▶ _operation_class()   ← execution/base.Ability 子类
        │     ├─▶ _build_tool()        ← ToolSchema（与 tool_list 共用）
        │     └─▶ _build_hooks()
        └─▶ loading/ability_view.AbilityManifest
```

### 3.2 工具面装配（只要工具、不要运行时 view）

```text
装配方（模型上下文）
  └─▶ loading/tool_list.load_tool_list()
        ├─▶ loading/ability_roots.default_pool()
        ├─▶ loading/ability_scan.scan_ability_definitions(池)
        └─▶ loading/ability_catalog.build_tool_schema() → ToolSchema.to_openai_tool()
```

### 3.3 技能面（目录注入 + 按名读正文）

```text
context/manager.py
  └─▶ loading/skill_context.render_skill_catalog() / digest_skill_catalog()
        ├─▶ loading/ability_scan.load_ability_skills(池)
        └─▶ loading/ability_schema.skill_body_path()

ability/core/operations/load_skill.py
  └─▶ loading/skill_provider.SkillAbilityProvider
        ├─▶ scan() / get_skill()      → ability_scan
        ├─▶ read_skill()              → skill_context.read_skill_body()
        ├─▶ verify() / relock()       → ability_scan.verify_skill() / relock_skill()
        └─▶ initialize()              → ability_scan.ensure_locks()
```

### 3.4 执行装配与执行

```text
core/unit.py（spawn / 注册）
  └─▶ agent_resolver.resolve_agent_manifest()
        ├─▶ loading/manifest_io.BuiltinManifestStore（按 ref 取声明）
        └─▶ execution/ability_build.instantiate_resolved_abilities()
              ├─▶ loading/ability_catalog.create_ability()  → ability/<容器>/operations/<键>.py
              └─▶ permissions/*（FS 检查器、命令审批、HITL 包装）

agents/base.py
  └─▶ execution/executor.py
        ├─▶ execution/hook_runner → hook_store / hook_context / execution_frame
        ├─▶ permissions/*（判定）
        └─▶ ability/<容器>/operations/<键>.py::execute()
```

### 3.5 对外目录报告

```text
外层框架
  └─▶ loading/catalog.scan_catalog()
        ├─▶ loading/ability_catalog.load_all_builtin_manifests()（能力面）
        ├─▶ loading/ability_scan.scan_abilities()（容器视图 + 技能成员）
        └─▶ loading/skill_provider.SkillAbilityProvider.scan()（技能面，可选接线）
```

## 4. 依赖统计

### 4.1 被 scheme 之外的代码 import 最多（枢纽文件，改动影响面大）

| 文件 | 外部消费者数 | 典型消费者 |
| --- | --- | --- |
| `ghrah.scheme` | 7 | `agents/base.py`、`core/__init__.py` 等 7 个文件 |
| `ghrah.scheme.loading` | 7 | `communication/supervisor.py`、`context/manager.py` 等 7 个文件 |
| `ghrah.scheme.execution` | 4 | `agents/base.py`、`agents/builder.py` 等 4 个文件 |
| `ghrah.scheme.loading.ability_catalog` | 4 | `communication/supervisor.py`、`core/unit.py` 等 4 个文件 |
| `ghrah.scheme.loading.manifest_fields` | 4 | `communication/supervisor.py`、`manifest_store/service.py` 等 4 个文件 |
| `ghrah.scheme.errors` | 3 | `agents/base.py`、`core/__init__.py` 等 3 个文件 |
| `ghrah.scheme.execution.context` | 3 | `agents/base.py`、`context/manager.py` 等 3 个文件 |
| `ghrah.scheme.agent_resolver` | 2 | `core/unit.py`、`manifest_store/service.py` |
| `ghrah.scheme.loading.ability_view` | 2 | `manifest_store/builtins.py`、`manifest_store/store.py` |
| `ghrah.scheme.agent_manifest` | 1 | `manifest_store/store.py` |
| `ghrah.scheme.execution.ability_build` | 1 | `core/unit.py` |
| `ghrah.scheme.execution.executor` | 1 | `agents/builder.py` |

### 4.2 依赖最多（编排层，改动成本低但最容易被牵动）

| 文件 | 依赖数 | 依赖了谁 |
| --- | --- | --- |
| `ghrah.skills` | 10 | `ghrah.skills.catalog`、`ghrah.skills.document`、`ghrah.skills.errors`、`ghrah.skills.initialize` 等 10 个文件 |
| `ghrah.scheme.loading.ability_scan` | 8 | `ghrah.scheme.loading`、`ghrah.scheme.loading.ability_roots`、`ghrah.scheme.loading.ability_schema`、`ghrah.scheme.loading.manifest_fields` 等 8 个文件 |
| `ghrah.scheme.execution.ability_build` | 7 | `ghrah.scheme.agent_resolver`、`ghrah.scheme.kinds`、`ghrah.scheme.loading.ability_catalog`、`ghrah.scheme.paths` 等 7 个文件 |
| `ghrah.scheme.execution.executor` | 7 | `ghrah.scheme.execution.context`、`ghrah.scheme.execution.hook_context`、`ghrah.scheme.execution.hook_runner`、`ghrah.scheme.execution.hook_store` 等 7 个文件 |
| `ghrah.scheme.loading.ability_catalog` | 7 | `ghrah.scheme.execution.base`、`ghrah.scheme.loading`、`ghrah.scheme.loading.ability_roots`、`ghrah.scheme.loading.ability_scan` 等 7 个文件 |
| `ghrah.skills.provider` | 7 | `ghrah.skills.document`、`ghrah.skills.errors`、`ghrah.skills.initialize`、`ghrah.skills.models` 等 7 个文件 |
| `ghrah.scheme.ability.cluster.operations.spawn_agent` | 6 | `ghrah.scheme._cluster_common`、`ghrah.scheme.agent_resolver`、`ghrah.scheme.execution.ability_build`、`ghrah.scheme.execution.base` 等 6 个文件 |
| `ghrah.scheme.ability.core.operations.list_abilities` | 6 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.loading.ability_catalog` 等 6 个文件 |
| `ghrah.scheme.ability.core.operations.load_skill` | 5 | `ghrah.scheme.execution.base`、`ghrah.scheme.execution.context`、`ghrah.scheme.execution.hooks`、`ghrah.scheme.loading.skill_errors` 等 5 个文件 |
| `ghrah.scheme.agent_resolver` | 5 | `ghrah.scheme.agent_manifest`、`ghrah.scheme.loading.ability_catalog`、`ghrah.scheme.loading.ability_view`、`ghrah.scheme.loading.manifest_fields` 等 5 个文件 |

## 5. 从这份图能看出的重复（按证据排）

### 5.1 工具有两条派生路径（算法一份，入口两个）

见 §0。`loading/tool_list.py` 与 `loading/ability_catalog.py::_project` 都调 `build_tool_schema()`，形状一致，差别只在范围与是否需要实现脚本。**残留的重复是「谁算模型工具面」有两个入口，不是算法有两份。** 收敛方式：装配侧统一调 `load_tool_list()`。

### 5.2 三个「遍历定义面」的循环各写一遍

`load_all_builtin_manifests()`、`builtin_full_names()`、`operation_containers()` 都是同一形状的嵌套遍历（容器 → 成员）。三者产物不同（view / 全名 / handler→容器），但**同一形状重复了三次**，而且各自扫一次盘。一次查询里既要全名又要 handler 索引，就会扫三遍盘（无缓存后的实测代价：单次扫盘约 6 ms、投影约 7 ms）。

### 5.3 `AbilityManifest` 是纯装配体，没有新语义

`AbilityManifest` 内嵌 `ToolSchema`，而 `ToolSchema` 的家在 `loading/manifest_fields.py`。view 只是把「定义面的工具面 + 实现面的元信息」装在一起；它存在的理由是历史消费方（`list_abilities`、subject 的 `manifest_store`）要一个混合视图，而不是它自己有独立事实。

### 5.4 旧格式解析栈仍占 `manifest_io.py` 的一半

`loading/manifest_io.py` 里的 `parse_ability_manifest` / `validate_manifest` 仍被 subject 的 `manifest_store` 使用（它把能力落盘成旧格式 YAML 再读回）。这是 `loading/` 里唯一「和定义面无关」的代码，也是 `ability_view.py` / `manifest_fields.py` 里旧口径字段的成因。移除前提见迁移记录 §3.6。

### 5.5 生产消费者为零的文件

`loading/catalog.py`（对外报告）当前仓内无生产调用方；`ghrah.scheme` 门面里的一批名字同样只有测试在 import。这两类属于「对外承诺」，不是「内部依赖」，与真死代码不同——判据见清点文档 §3.1。

