# 能力方案包（`scheme_ability`）设计

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-DESIGN-008 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `91f8220`（2026-10-30），分支 `andrew-dev-new`，`ghrah-core` 0.2.0a0 |
| 读者 | 要改本包任一文件的人；新增或删除能力成员的人；评审能力系统契约、权限边界与装配顺序的人 |
| 自闭环 | 本文**不依赖任何其它文档**。本模块的包结构、能力定义面逐字段、目录池、扫描、运行时投影、工具面、技能成员与 lock/漂移、执行面、Hook、权限与 HITL、装配现状、不变式与已知偏差，全部在本文件内描述；每条论断后跟 `文件路径:行号` |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-30 | 首版（自闭环形态）。按 `91f8220` 与 `a5beb74`（2026-10-29 落地）反向重建：包分层与依赖方向、163 条惰性导出、定义面逐字段、目录池与扫描、运行时投影、工具面、技能成员与 lock、执行流水线、十个 HookPoint、权限与 HITL、两条装配路径、不变式 INV-01~INV-16、已知偏差 D-1~D-26 |

## 1. 范围

### 1.1 本文描述什么

`ghrah-core/src/ghrah/scheme_ability/` 这一个包：能力包如何被定义、如何被发现、如何被投影成模型工具、一次工具调用如何被执行、权限与人工审批在哪一层生效、装配期由谁把这些对象接起来，以及**所有这些事情当前的实现状态与已知偏差**。

**权威顺序：代码 > 本文。** 本文与代码冲突时以代码为准，并把冲突当作文档缺陷来修。文中所有 `path:line` 都以校准基线的行号为准；行号会随重构漂移，符号名不会——找不到行号时按符号名检索。

本包对外有三个身份，读本文时不要混淆：

| 身份 | 含义 | 消费者 |
| --- | --- | --- |
| 能力系统框架 | `loading/` `execution/` `permissions/` 三层的规则与流水线 | 任何要跑能力的东西 |
| 内置能力包集合 | `ability/<容器>/` 里 5 个容器、22 个成员 | 运行时投影与工具面 |
| 惰性导出门面 | 163 个公开名字的单一索引（§6） | 上层按名字 import |

### 1.2 路径缩写

| 缩写 | 展开（相对仓库根 `D:\codeWorkSpace\ghrah-project\ghrah-monorepo\`） |
| --- | --- |
| `sa/...` | `ghrah-core/src/ghrah/scheme_ability/...` |
| `slice/...` | `ghrah-core/src/ghrah/ability/...`（文件 Ability 资源切片，本包的现行消费者） |
| `core/...` | `ghrah-core/src/ghrah/core/...` |
| `unit.py` | `ghrah-core/src/ghrah/core/unit.py` |
| `subject/...` | `ghrah-subject/src/ghrah/subject/...` |

> 仓库里不存在 `ghrah-core/src/ghrah/scheme/` 这个目录，任何指向它的旧路径都应按上表改写为 `sa/`。本包自 `a5beb74` 落地起目录名就是 `scheme_ability`。

### 1.3 计数口径

本包里「有多少个能力」有三个互不相同的数字，本文每次引用都会写明是哪一个，定义与差额原因见 §10.7：

| 口径 | 数字 | 取数 API |
| --- | --- | --- |
| handler 全集 | **22** | `builtin_handlers()` |
| 运行时视图 | **21** | `load_all_builtin_manifests()` / `builtin_full_names()` |
| 模型工具面 | **20** | `load_tool_list()` |
| 公开导出名字 | **163** | `len(sa.__all__)` |

### 1.4 不在本文范围

- **驱动循环**（八阶段、一次 LLM 调用怎么触发工具批）——本文只写到本包向执行器提供的接口为止，不描述谁在什么时机调 `execute_tool_calls`。
- **Subject 侧的配置与注入链**——本文只给默认值表（§15.4）与"注入发生在哪个函数"的锚点，不展开 Subject 的配置解析。
- **`ghrah.mcp` 包内部**——本文只描述能力侧如何通过 `provider_slot` 与 `mcp_runtime` 服务键与它对接（§13.7、§16.4），不描述该包的连接、凭据与协商。
- **Agent 清单（`agent.yaml`）**——本包只保留一个转出入口（`sa/agent_manifest.py`），schema 不在此演进（§18 D-22）。

## 2. 术语

| 术语 | 定义 | 代码位置 |
| --- | --- | --- |
| 能力包 / 容器（container） | 一个含 `ability.yaml` 的目录。容器名 = `identity.name or identity.namespace`，且必须等于目录名 | `sa/loading/ability_schema.py:294-302`、`sa/loading/ability_scan.py:505-516` |
| 成员（member） | 容器内三类可寻址项之一：`operations` / `skills` / `assets`，键即成员名 | `sa/loading/ability_scan.py:255-361` |
| 定义面（definition） | `ability.yaml` 表达的静态事实：身份、提供者槽、成员契约 | `sa/loading/ability_schema.py` |
| 实现面（implementation） | `operations/<key>.py` 里的实现脚本，按路径约定被 import | `sa/loading/ability_catalog.py:166` |
| 目录面（catalog） | 全池扫盘结果，含用户目录，收集式不抛错 | `sa/loading/ability_scan.py:532-604` |
| 实现面投影 | 只扫内置树、严格抛错的运行时视图 | `sa/loading/ability_catalog.py:102-127` |
| 运行时视图（`AbilityManifest`） | 从定义面 + 实现面**投影**出的旧格式对象，供列表类工具与兼容层使用；**不是**定义来源 | `sa/loading/ability_catalog.py:338-396`、`sa/loading/ability_view.py:55` |
| 工具面（tool list） | 交给模型的 function-calling schema 集合；投影口径下工具名 = 成员键 | `sa/loading/tool_list.py:107-161` |
| 投影口（bind_tool） | 实现类上的 `bind_tool()`，**生产链路真正的工具面来源**（§11.3） | `sa/execution/base.py:70-81` |
| handler | 成员键同名标识，用来定位实现模块与类 | `sa/loading/ability_catalog.py:452-466` |
| 提供者槽（provider slot） | `implementation.provider_slot`，声明本成员依赖哪个后端契约 | `sa/loading/ability_schema.py:176-179`、`:199-200` |
| 服务键（ServiceKey） | 执行期依赖的注入点，与数据键分开 | `sa/execution/execution_services.py:32-86` |
| 数据键（DataKey） | 执行期在 `ExecutionData` 里传递的数据槽 | `sa/execution/execution_data.py:28-86` |
| HITL | Human-In-The-Loop，人工审批 | `sa/permissions/hitl.py`、`sa/execution/executor.py:651-763` |
| registry-only | 在定义面是正式成员，但**不产出运行时视图**的 operation | `sa/loading/ability_catalog.py:81` |
| 惰性导出 | 顶层 `__getattr__` 按名字表延迟 import 子模块 | `sa/__init__.py:226-248` |
| 漂移（drift） | 技能包内容指纹与 lock 记录不一致 | `sa/loading/skill_fingerprint.py:626-657` |

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

四个面职责互斥，**不允许**把一面的结论写回另一面：定义面不承载权限与 hook（§7.9），实现面不决定工具是否可见（§7.7），工具面不去猜实现位置（§11.2）。

### 3.2 零缓存

本包所有查询 API **每次调用重新扫盘或重新投影**，不持有缓存：

| API | 行号 |
| --- | --- |
| `load_ability_definitions()` | `sa/loading/ability_catalog.py:112` |
| `load_all_builtin_manifests()` | `sa/loading/ability_catalog.py:405` |
| `operation_containers()` | `sa/loading/ability_catalog.py:460` |
| `has_handler()` | `sa/loading/ability_catalog.py:490` |
| `scan_ability_definitions()` | `sa/loading/ability_scan.py:535` |
| `load_tool_list()` | `sa/loading/tool_list.py:107` |
| `load_skill_catalog()` | `sa/loading/skill_context.py:311` |

成立前提是 YAML 解析足够便宜：`load_yaml()` 显式绑定 `yaml.CSafeLoader`（`sa/loading/ability_schema.py:78-91`），注释给出量化：36KB 定义从 ~75ms 降到 ~6ms。**新增查询 API 时不要自己加 `lru_cache`**，否则会引入"改了 `ability.yaml` 但不生效"的第二权威。

> 已知文档缺陷：`sa/loading/ability_catalog.py:20` 的模块 docstring 仍提到 `load_all_builtin_manifests.cache_clear()`，该函数并没有 `lru_cache`，同文件 `:405` 明写"每次调用都重新投影（无缓存）"。以 `:405` 为准。

顶层 `__init__.py:5-30` 把这条写成第一句设计声明：「运行时没有注册表、没有缓存——需要时扫盘，投影出 `AbilityManifest` 这个 view」。

### 3.3 模块职责

| 模块 | 职责 | 是否对外 |
| --- | --- | --- |
| `sa/loading/ability_schema.py` | `ability.yaml` 的 pydantic 模型与跨字段校验；文件名常量 | 是 |
| `sa/loading/ability_roots.py` | 扫描根与优先级（内置 rank 0 / 用户 rank 100） | 是 |
| `sa/loading/ability_scan.py` | 全池扫盘、成员装配、技能指纹与 lock | 是（`scan_abilities`） |
| `sa/loading/ability_catalog.py` | 内置树严格投影、handler→类 解析、`create_ability` | 是 |
| `sa/loading/catalog.py` | 能力 + handler + 容器 + 技能 + issue 的合并报告 | 是（`scan_catalog`） |
| `sa/loading/tool_list.py` | 工具面投影 | 是（`load_tool_list`） |
| `sa/loading/ability_view.py` | `AbilityManifest` 视图模型（投影目标形态） | 内部 |
| `sa/loading/skill_context.py` | 技能目录渲染、`SKILL.md` 读取、目录摘要截断 | 是 |
| `sa/loading/skill_fingerprint.py` | 技能包内容指纹（manifest 构建/解析/差异） | 是 |
| `sa/loading/skill_lock.py` | lock 文件读写与渲染 | 是 |
| `sa/loading/skill_models.py` | 技能侧数据模型（lock、指纹、校验结果） | 是 |
| `sa/loading/skill_provider.py` | 技能 ↔ 能力的提供者桥（`SkillAbilityProvider`） | 是 |
| `sa/loading/manifest_io.py` / `manifest_fields.py` | 旧 manifest 形态的解析与字段（遗留兼容层，§10.6） | 兼容 |
| `sa/execution/*` | `Ability` 契约、执行上下文、执行器、Hook | 是 |
| `sa/permissions/*` | FS 权限、命令分级、HITL hook | 是 |
| `sa/paths.py` / `kinds.py` / `errors.py` / `_cluster_common.py` | 路径规格、能力分类、能力异常、集群诊断 | 是 |
| `sa/agent_manifest.py` / `agent_resolver.py` | Agent 清单转出与解析（§18 D-22） | 兼容 |
| `sa/ability/<容器>/` | 能力包本身（**只放包，不含加载逻辑**） | 数据 |

## 4. 依赖形状

### 4.1 向外只依赖三类

`sa/` 对 `ghrah.*` 的 import 只有三类，没有第四类：

| 目标 | 具体模块 |
| --- | --- |
| `ghrah.core` | `ability_protocol`、`supervisor_protocol`、`event_publisher`、`events`、`hitl`、`exceptions`、`_base_error`、`room_protocol`、`config.builders` |
| `ghrah.types` | `results`、`config_types` |
| `ghrah.chat.content` | 内容块类型 |

### 4.2 三处反向依赖旧树

| 位置 | 依赖 | 说明 |
| --- | --- | --- |
| `sa/agent_manifest.py:9` | `ghrah.manifest.agent` | 只做转出（`AbilityRef`/`AgentHooks`/`AgentManifest`/`AgentMetadata`/`ContextOverrides`/`ModelConfig`/`PersistenceOverrides`/`WindowOverrides`），docstring 明写"能力定义和 Agent 配置分别演进；本模块只保留原导入入口，不复制 Agent schema" |
| `sa/loading/manifest_fields.py:10,17` | `ghrah.manifest.errors`、`ghrah.manifest.types` | 遗留兼容层共用异常与字段 |
| `core/unit.py:68,477,577-579,986-987,1393` | `ghrah.abilities.*`、`ghrah.manifest.*` | Core 装配仍用旧树，见 §16.1 |

前两处让本包**没有**真正独立——`ghrah.manifest` 仍在 `ghrah-core/src/ghrah/manifest/` 下活着（§18 D-22）。

### 4.3 唯一越界点

框架层（`loading/` `execution/` `permissions/`）不 import 任何具体下游包。唯一的"成员依赖具体包"出现在

```
sa/ability/mcp/operations/mcp_call.py:27  from ghrah.mcp.errors import McpCredentialMissingError, McpError, ...
sa/ability/mcp/operations/mcp_call.py:34  from ghrah.mcp.identity import format_unknown_tool_message
```

这是被允许的方向：**成员可以直接依赖具体能力库，框架层不许**。理由——成员是"某一类能力怎么实现"的知识，绑定具体库是它的本职；框架层若也依赖，本包就与具体能力库一起演化，`sa/` 不再是通用框架。

### 4.4 分层不变量

| 编号 | 不变量 | 违反后果 |
| --- | --- | --- |
| L-1 | `loading/` 不 import `execution/`，除 `ability_catalog.py:33`（取 `Ability` 基类做子类判定） | 出现环；已被承认（§18 D-23） |
| L-2 | `execution/` 不 import `permissions/` | 权限不得成为执行器的隐式前提 |
| L-3 | `permissions/` 只依赖 `execution/hooks.py` 的 Hook 契约 | 权限实现被绑死在某个 Hook 形态上 |
| L-4 | 顶层 `__init__.py` 不 import 任何子模块 | 破坏惰性导出（§6） |

现状：L-2、L-3、L-4 成立；L-1 有一处已知反向缝（`sa/execution/ability_build.py:36` import `sa/loading/ability_catalog.create_ability`），构成 `loading ↔ execution` 的双向边（§18 D-23）。

```
            ┌─────────────────────────────┐
            │  sa/__init__.py（163 条索引）│
            └─────────────┬───────────────┘
                          │ __getattr__ 延迟 import
      ┌───────────────────┼────────────────────┐
      ▼                   ▼                    ▼
 sa/loading/         sa/execution/       sa/permissions/
      │                   │                    │
      └──── L-1（有缝）────┘                    │
                          └──── hooks 契约 ────┘

 sa/ability/<容器>/operations/*.py  →  可实现任意库（含 ghrah.mcp）
```

## 5. 包结构

### 5.1 顶层 7 个模块

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `sa/__init__.py` | 257 | 163 条导出的唯一索引 + PEP 562 惰性 `__getattr__`（§6） |
| `sa/agent_manifest.py` | 29 | 旧 Agent 清单类型的转出入口（§4.2、§18 D-22） |
| `sa/agent_resolver.py` | 193 | 从配置解析出 Agent 定义与能力引用 |
| `sa/paths.py` | 180 | Core 与 Subject 共享的路径处理：`AbilityPathSpec`（`path_keys`/`working_dir_keys`/`alt_keys`）、`ABILITY_PATH_SPECS`、`resolve_fs_permission_paths`、`is_subpath`、`extract_paths` |
| `sa/kinds.py` | 42 | `FS_ABILITY_TYPES`（7 个文件类 handler）、`CLUSTER_ABILITY_TYPES`（7 个集群 handler）；docstring 自称"单一真源，runner 与 router 共用，避免两份副本漂移" |
| `sa/errors.py` | 26 | `AbilityError`（继承 `ActorAgentError`）、`AbilityNotFoundError`；**只有这两个**（权限拒绝不抛异常，见 INV-10） |
| `sa/_cluster_common.py` | 50 | 集群成员的诊断错误常量两档 + `cluster_supervisor_error(supervisor)`：`supervisor is None` 是 standalone 正常态，有 supervisor 但 `get_cluster_context` 缺失或 `cluster_id` 为空才算装配缺陷 |

### 5.2 `loading/`（15 个文件，按职责分 9 组）

| 组 | 文件 | 职责 |
| --- | --- | --- |
| 定义面 | `ability_schema.py`(335) | pydantic 模型、跨字段校验、`SCHEMA_VERSION="0.1-draft"`、`DEFINITION_FILE_NAME="ability.yaml"` |
| 目录池 | `ability_roots.py`(220) | `AbilityRoot`、内置/用户两个根与优先级 |
| 扫描 | `ability_scan.py`(952) | 全池扫盘、成员装配、技能指纹与 lock（§9） |
| 运行时投影 | `ability_catalog.py`(564) | 只扫内置、handler→类 解析、四件套常量校验、`create_ability` |
| 合并报告 | `catalog.py`(114) | `CatalogReport` / `scan_catalog()` |
| 工具面 | `tool_list.py`(161) | `ToolEntry` / `ToolList` / `load_tool_list()` |
| 视图模型 | `ability_view.py`(85) | `AbilityMetadata` / `AbilityHooks` / `AbilityManifest` |
| 技能包面 | `skill_context.py`(382)、`skill_fingerprint.py`(657)、`skill_lock.py`(297)、`skill_models.py`(218)、`skill_provider.py`(253) | 技能目录渲染、内容指纹、lock 读写、模型、提供者桥（§12） |
| 兼容层 | `manifest_io.py`(152)、`manifest_fields.py` | 旧 manifest 文本形态的解析与字段；`SUPPORTED_VERSIONS` 从这里出（§10.6） |

### 5.3 `execution/`（12 个文件）

| 文件 | 行数 | 关键符号 |
| --- | --- | --- |
| `base.py` | 100 | `Ability(ABC)`；再导出 `ActionOutcome`/`ActionResult` |
| `context.py` | 215 | `AbilityExecutionContext`（兼容门面，§13.2） |
| `execution_data.py` | 86 | `DataKey` / `ExecutionData` / 5 个预置数据键 |
| `execution_services.py` | 86 | `ServiceKey` / `ExecutionServices` / 6 个预置服务键 |
| `execution_frame.py` | 21+ | `ExecutionFrame`（Hook 与驱动循环共用，§18 D-5） |
| `executor.py` | 802 | `AbilityExecutor(ABC)`、`LocalAbilityExecutor`、`HITLWaitResult`（§13.4） |
| `ability_build.py` | 254 | `instantiate_resolved_abilities`（装配点 B，§16.3） |
| `hook_runner.py` | — | `HookRunner`（唯一执行路径，§14.5） |
| `hook_store.py` | 128 | `HookStore` / `HookListView`（§14.6） |
| `hook_context.py` | — | `HookContext`（§14.7） |
| `hooks.py` | 237 | `HookPoint` / `HookScope` / `HookResult` / `Hook`（§14） |
| `invocation.py` | — | `AbilityInvocation` |

### 5.4 `permissions/`（3 个文件）

| 文件 | 行数 | 关键符号 |
| --- | --- | --- |
| `fs_permissions.py` | 302 | `FSPermissionChecker`、`AccessApprovalHook` |
| `command_safety.py` | 469 | `CommandSafetyCategory`、`CommandSafetyVerdict`、`CommandSafetyChecker`、`CommandApprovalHook`、四张默认名单 |
| `hitl.py` | 100 | `HITLApprovalHook`、`HITLRequiredAbility` |

### 5.5 五个容器与 22 个成员

| 容器 | 成员数 | 成员键 |
| --- | --- | --- |
| `cluster` | 7 | `broadcast_message`、`query_agents`、`query_manifests`、`send`、`send_message`、`spawn_agent`、`terminate_agent` |
| `core` | 6 | `conversation`、`end_task`、`list_abilities`、`load_skill`、`recall_context`、`session_info` |
| `fs` | 7 | `delete_file`、`edit_file`、`list_directory`、`move_file`、`read_file`、`search_files`、`write_file` |
| `mcp` | 1 | `mcp_call` |
| `shell` | 1 | `execute_command` |
| **合计** | **22** | 即 §1.3 的 handler 口径 |

每个容器目录的结构固定：`ability.yaml` + `operations/__init__.py` + `operations/<成员键>.py`。

## 6. 导出契约

### 6.1 一张表 + 一个函数

```python
# sa/__init__.py
_MODULE_OF: dict[str, str] = {          # :37-213，163 条
    "AbilityDefinition": "loading.ability_schema",
    "scan_abilities": "loading.ability_scan",
    "LocalAbilityExecutor": "execution.executor",
    ...
}
__all__ = sorted(_MODULE_OF)            # :216

_SUBPACKAGES = ("ability", "execution", "loading", "permissions")   # :219
_PLAIN_MODULES = ("agent_manifest", "agent_resolver", "errors", "kinds", "paths")  # :222

def __getattr__(name: str):             # :226-248  PEP 562
    module_name = _MODULE_OF.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__}.{module_name}")
    value = getattr(module, name)
    globals()[name] = value             # :244 写回，后续访问不再走 __getattr__
    return value

def __dir__():                          # :251-257  索引键 ∪ 已加载的 globals
    ...
```

`_MODULE_OF` 的注释（`:37-213`）按功能分了 11 段：定义面、目录池、目录面扫描、加载与查询、技能包面、错误、运行时 view、agent 侧、能力类型与路径、执行、权限。`:214` 的 docstring 写明「这是本包唯一的导出清单」。

### 6.2 为什么不用 `from .loading import *`

三个理由，按重要性排序：

1. **初始化环**。子模块（如 `execution/ability_build.py`）会 import `sa.loading.ability_catalog`。若顶层 `__init__` 在导入期就 import 全部子模块，父包 `sa` 的 `__init__` 会早于某个子模块完成初始化，`from sa import X` 在子模块内部触发时拿到半初始化的模块对象。
2. **启动成本**。`import ghrah.scheme_ability` 若拉起四个子包 30 个模块，任何只想用 `sa.errors.AbilityError` 的调用方都要付全量 import 时间（含 pydantic 模型构建）。
3. **星号导入会把"子模块名"变成"公开名"**。`from .loading import *` 会连带把 `ability_schema`、`ability_scan` 这些模块对象挂到包上，`__all__` 就不能如实表达"我承诺导出什么"。

### 6.3 代价与护栏

- 代价一：163 条手写映射，新增公开符号要改两处（子模块里定义 + `_MODULE_OF` 登记）。漏登记的表现是 `AttributeError`，不是静默失效——这类错误在第一个用例就会暴露。
- 代价二：静态分析工具（IDE 跳转、`mypy` 对包属性的推断）看不到这些名字的来源，只能跳转到 `__getattr__`。
- 护栏：`ghrah-core/tests/scheme_ability/test_package_facade.py` 固定三件事——`__all__` 与 `_MODULE_OF` 键集相等、每个名字都能取到且 `getattr` 后可 `dir()` 见到、`_SUBPACKAGES`/`_PLAIN_MODULES` 与磁盘目录一致。

### 6.4 计数表

| 名字 | 数字 | 取数方式 |
| --- | --- | --- |
| 公开导出 | 163 | `len(sa.__all__)` |
| handler 全集 | 22 | `builtin_handlers()` |
| 运行时视图 | 21 | `builtin_full_names()` |
| 模型工具面 | 20 | `load_tool_list()` |
| 扫描到的条目 | 17 | `scan_abilities()`（5 个容器 + 12 个技能包目录） |

五个数字都实测于 `91f8220`。它们的差额原因见 §10.7；`scan_abilities()` 的 17 不是"能力数"，见 §19.2。

## 7. 定义面：`ability.yaml`

### 7.1 位置、身份与命名

- 路径：`<扫描根>/<容器名>/ability.yaml`，扫描深度固定为 1（`sa/loading/ability_scan.py:491`）。仓库内只有 5 份：`sa/ability/{fs,core,cluster,shell,mcp}/ability.yaml`。
- `format` 必须是字面量 `ghrah.ability-definition`（`sa/loading/ability_schema.py:283`），`schema_version` 必须是 `0.1-draft`（`:54`、校验 `:121-125`）。
- 容器身份 `identity.namespace` 允许 `-` 与 `_`（`^[a-z][a-z0-9_-]*$`）；`identity.name` 只允许 `[a-z0-9_]`，**不允许连字符**（`:155-156`）——因为 `identity.name` 会参与 Python 包路径（`<容器>/operations/<key>.py`）。
- 容器名与目录名必须相等，否则该包记一条 issue 并跳过（`sa/loading/ability_scan.py:505-516`）。理由：目录名只作定位，身份以定义面为准。

### 7.2 顶层字段

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

### 7.3 `identity`

| 字段 | 类型 | 必填 | 默认 | 行号 |
| --- | --- | --- | --- | --- |
| `namespace` | `str`，`^[a-z][a-z0-9_-]*$` | 是 | — | `:155` |
| `name` | `str \| None`，`^[a-z][a-z0-9_]*$` | 否 | `None` | `:156` |
| `version` | `str`（≥1） | 是 | — | `:157` |

派生：`full_name` = `namespace.name`，`name` 缺席时退化为 `namespace`（`:160-168`）；`legacy_namespace` 同值（`:314-322`）；`full_name_of(key)` = `<点分身份>.<key>`（`:324-335`）。

点分全名（如 `ghrah.fs.read_file`）**只**用于视图键、去重与问题定位，**不是**工具名（§11.2）。

### 7.4 `provider_requirements`

| 字段 | 类型 | 必填 | 默认 | 行号 |
| --- | --- | --- | --- | --- |
| `contract` | `str`（≥1） | 是 | — | `:176` |
| `contract_digest` | `str \| None` | 否 | `None` | `:177` |
| `required_capabilities` | `list[str]` | 否 | `[]` | `:179` |

`contract_digest` 由发布路径从唯一规范计算填入，**作者不自签**（`:178`）。`implementation.provider_slot` 必须在 `provider_requirements` 里声明过，否则拒绝（跨字段校验 3，`:135-140`）。

### 7.5 `operations.<key>` 与成员契约

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

### 7.6 `execution`（`ExecutionSpec`）

| 字段 | 取值 | 必填 | 行号 |
| --- | --- | --- | --- |
| `mode` | `Literal["request"]`（首版只支持 request 语义） | 是 | `:187` |
| `timeout_ms` | `int`，`>0` | 是 | `:188` |
| `cancellation` | `Literal["cooperative","unsupported"]` | 是 | `:189` |
| `on_revoke` | `Literal["drain","request_cancel"]` | 是 | `:190` |
| `retry` | `Literal["never"]` | 是 | `:191` |

**重要事实：`timeout_ms` 目前不参与执行。** 它是定义面声明，`AbilityOperation.timeout_ms` 也把它透出（`sa/loading/ability_scan.py:229-231`），但执行器从不读取它——`execute_ability` 里没有为 `ability.execute()` 包任何超时（`sa/execution/executor.py:400`；全文件唯一 `wait_for` 在 `:710`，只用于 HITL 等待）。内置包取值：`fs`/`core`/`cluster` 全部 `60000`，`shell`/`mcp` 为 `300000`（`sa/ability/shell/ability.yaml:65`、`sa/ability/mcp/ability.yaml:73`）。改这个字段不会改变运行时行为，改执行超时目前无受支持途径（§18 D-1）。

### 7.7 `expose` 是工具面唯一开关

原文（`sa/loading/ability_schema.py:218-222`）：`True`（缺省）表示该成员按定义面投影成一个模型工具，工具名即成员键；`False` 表示只在内部可用。**本字段是"是否进工具面"的唯一权威：实现脚本不再声明同名开关。**

仓库内两个 `expose: false` 的例子：`core/ability.yaml:42`（`conversation`）与 `cluster/ability.yaml:293-297`（`send`）。二者的差别见 §10.7。

### 7.8 `skills.<key>`

| 字段 | 类型 | 必填 | 默认 | 行号 |
| --- | --- | --- | --- | --- |
| `title` | `str`（≥1） | 是 | — | `:268` |
| `description` | `str`（≥1） | 是 | — | `:269` |
| `body` | `SkillBody{path, media_type}` | 是 | — | `:270`、`:231-240` |
| `tags` | `list[str]` | 否 | `[]` | `:271` |
| `references` | `list[str]` | 否 | `[]` | `:272` |
| `annotations` | `dict[str, Any]` | 否 | `{}` | `:273` |

约束：**一个能力包至多一个 skill 成员**（跨字段校验 4，`:136-142`），超出的报错原文为"一个能力包只支持一个 skill 成员…技能是容器的插件包，容器与技能一一对应；要装第二个技能请独立成包"。正文固定落在 `skills/SKILL.md`，因此不引入第二级目录（`:260-261`）；`title`/`description` 即"原 `SKILL.md` frontmatter 迁出的两个字段"（`:263`）。

仓库内 5 份 `ability.yaml` 全部 `skills: {}`，**没有任何打包的 skill 种子包**（全仓 `**/SKILL.md` 匹配 0 个）。技能成员只能来自用户目录，其扫描、指纹与 lock 见 §12。

### 7.9 刻意不在定义面的两件事

模块 docstring 明写（`sa/loading/ability_schema.py:11-12`）：`permissions`（授权结果）与 `hooks`（编排）不在定义面，前者属授权记录，后者随实现，暂存于 `operations/<key>.py` 的模块级常量。所以"这个能力要不要审批"在 `ability.yaml` 里查不到，只能查实现脚本或装配参数——这是当前设计的一处可读性代价（§18 D-3）。

`assets` 只有身份与键，没有字段级契约：`dict[str, Any]`，内容原样持有、不解释语义（`:243-244`、`sa/loading/ability_scan.py:241-251`）。

### 7.10 校验与错误

解析入口 `parse_definition(raw: str, *, source: str) -> AbilityDefinition`（`sa/loading/ability_schema.py:94`）：`load_yaml` → 非 `dict` 报"不是映射"（`:115-116`）→ `model_validate`（`:118`）→ 四条跨字段校验（`:121-142`）。错误类型 `ManifestValidationError`（`sa/loading/manifest_fields.py:40`，基类 `ManifestError` `:36`）。

校验时机有两套语义，不要混淆：

| 路径 | 时机 | 失败行为 |
| --- | --- | --- |
| 目录面 `scan_ability_definitions` | 每包解析一次（`sa/loading/ability_scan.py:501`） | 记 issue 后 `continue`，不影响其他包 |
| 实现面 `load_ability_definitions` | 同上，但把**第一条** issue 直接抛出（`sa/loading/ability_catalog.py:124-126`） | 整次查询失败 |

四条跨字段校验（编号沿用代码注释）：

| 编号 | 规则 | 行号 |
| --- | --- | --- |
| 1 | `format` 与 `schema_version` 取值固定 | `:121-125` |
| 2 | `implementation.operation == 成员键` | `:127-132` |
| 3 | `implementation.provider_slot` ∈ `provider_requirements` | `:135-140` |
| 4 | 一个容器至多一个 skill 成员 | `:136-142` |

## 8. 目录池与优先级

### 8.1 `AbilityRoot`

`@dataclass(frozen=True)`（`sa/loading/ability_roots.py:71`）：`locator: Traversable`、`rank: int`、`source: str`、`required: bool = False`。属性：

| 属性 | 语义 | 行号 |
| --- | --- | --- |
| `label` | 真实路径给绝对路径；包资源给 `package:ghrah.scheme.ability` | `:85-93` |
| `writable` | 仅 `locator` 是 `Path` 时为 `True` | `:96-102` |
| `path` | 包资源为 `None` | `:105-114` |

### 8.2 两个根

| 根 | 位置 | rank | source | required | 可写 |
| --- | --- | --- | --- | --- | --- |
| 内置 | `importlib.resources.files("ghrah.scheme.ability")`，即 `sa/ability/` | `0` | `builtin` | 是 | 否 |
| 用户 | `Path.home()/.ghrah/skill-ability` | `100` | `skill-ability` | 否 | 是 |

常量 `BUILTIN_ROOT_RANK = 0` / `USER_ROOT_RANK = 100`（`sa/loading/ability_roots.py:52`、`:63`）；工厂 `builtin_root()` `:117`、`default_user_root()` `:131`、`user_root(root=...)` `:143`、`path_root(path, *, rank=USER_ROOT_RANK, source=..., required=False)` `:162`。两个池函数：`builtin_pool()` 单元素（实现面用它，`:183`）、`default_pool()` = 内置 + 用户（目录面用它，`:192`）。

### 8.3 冲突裁决：内置必胜

扫描按 `(rank, root.label)` 升序（`sa/loading/ability_scan.py:554`），同名容器先出现者胜，后来者记 issue（`:567-579`）。因此 rank 0 的内置包永远赢，用户目录**不允许**同名覆盖内置。设计原文（`sa/loading/ability_roots.py:53-57`）：否则一份外部 `fs/ability.yaml` 就能改掉文件系统能力的语义。

### 8.4 覆盖途径

`sa/loading/` 内没有任何 `os.environ` 读取——**没有环境变量可改扫描根**。可用的三条路：

1. `user_root(root=...)` 传参；
2. `path_root()` 自建根；
3. 上层装配期注入字符串路径（`unit.py:197`、Subject 的 `SkillConfig.root`）。

`ghrah.skills.roots` 只是转调本模块（`sa/loading/ability_roots.py:133-135`）。

### 8.5 `ensure_roots`

`ensure_roots(pool)`（`sa/loading/ability_roots.py:203-220`）只对 `locator` 是 `Path` 的根 `mkdir(parents=True, exist_ok=True)`，包资源根跳过；幂等，返回顺序与入参一致。

## 9. 扫描（目录面）

入口 `scan_ability_definitions(pool: Sequence[AbilityRoot] | None = None) -> AbilityScanResult`（`sa/loading/ability_scan.py:532`）；`pool=None` 时用 `default_pool()`（`:553`）。薄封装 `scan_abilities()`（`:754`）是对外主入口，`load_ability_skills()`（`:781`）只取 skill 成员。

### 9.1 返回结构

| 类型 | 字段 | 语义 | 行号 |
| --- | --- | --- | --- |
| `AbilityEntry` | `container` / `definition` / `root` / `locator` | 一个成功解析的包 | `:87` |
| `AbilitySkill` | `container`、`key`、`full_name`、`title`、`description`、`media_type`、`body_path`、`body_locator`、`tags`、`source`、`rank`、`skill_dir`、`lock_path`、`fingerprint`、`lock_issue` | skill 成员；`catalog_name` = `key`（`:176-182`，理由：显示全名会让模型拿一个加载入口不认的名字去调用） | `:104` |
| `AbilityOperation` | `container`、`key`、`full_name`、`definition`；属性 `title`/`description`/`provider_slot`/`handler`/`timeout_ms`/`effects` | operation 成员；**不复制**定义字段，只补归属（`:193`） | `:186` |
| `AbilityAsset` | `container`、`key`、`full_name`、`definition` | asset 成员 | `:240` |
| `AbilityContainer` | `name`、`namespace`、`version`、`title`、`description`、`root`、`locator`、`definition`、`operations`/`skills`/`assets`、`issues`；属性 `full_name`、`kinds`、`operation_count`、`skill_count`、`asset_count`、`member_count`、`is_operation_only`、`is_skill_only` | 聚合视图 | `:255` |
| `AbilityScanIssue` | `path`、`reason`、`container` | 单条问题，`__str__` = `"{path}: {reason}"` | `:365` |
| `AbilityScanResult` | `roots`、`entries`、`skills`、`issues`、`lock_issues`；属性 `definitions`（`{容器名: 定义}`）、`containers` | 全池结果 | `:382` |

`AbilityOperation` 与 `AbilityAsset` 的区别只有成员类型；`is_operation_only` / `is_skill_only` 供上层区分"纯能力包"与"纯技能包"。

### 9.2 算法

1. 排序根：`sorted(active, key=lambda item: (item.rank, item.label))`（`:554`）。
2. 每个根只做一次 `iterdir()`（`:491`），**不递归**。
3. 忽略：非目录、名字以 `_` 或 `.` 开头（`:491-497`）、目录内没有 `ability.yaml`（注释：`operations/` 这类普通子目录不是能力包）。
4. 解析定义，失败记 issue 并 `continue`（`:502`）。
5. 校验容器名 == 目录名（`:505-516`）。
6. 装配成员：operation/skill/asset 按成员键升序，容器按名字升序（`:261-262`）。
7. 同名容器去重（§8.3）。
8. skill 正文定位：`_skill_body_locator` 逐段 `joinpath`，`..` 一律拒绝（`:427-447`）；正文不可用的 skill **不进清单**（`:458-459`、`:582-587`）。

skill 目录**不由目录名识别**，只由 `ability.yaml` 的 `skills:` 表声明（`:517-520`、`:582-587`）。

### 9.3 issue 分类

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

## 10. 运行时投影（实现面）

### 10.1 只扫内置

`load_ability_definitions() -> Mapping[str, AbilityDefinition]`（`sa/loading/ability_catalog.py:102`）用 `builtin_pool()`，任一 issue 即抛第一条。`builtin_pool()` 单独成函数（`:90`）是给测试换临时目录用的。

### 10.2 handler → 模块 → 类

路径约定（`sa/loading/ability_catalog.py:166`）：handler 落在哪个容器的 `operations` 表，实现就在 `ghrah.scheme_ability.ability.<容器>.operations.<handler>`，类是**该脚本里唯一的 `Ability` 子类**。

判定要求（`:190-201`）：`obj.__module__ == module.__name__`、`issubclass(obj, Ability)`、不是 `Ability` 本身，**数量必须恰好 1**，否则报 `"期望恰好一个 Ability 实现类，实际 N 个"`。这条规则是"注册表不存在"的技术原因：类型名到类的映射被文件路径取代（§21）。

### 10.3 四件套常量

投影时必须能在实现脚本里找到四个模块级常量，缺失即 `ManifestValidationError`（`sa/loading/ability_catalog.py:363-374`）：

| 常量 | 类型 | 用途 | 投影后去向 |
| --- | --- | --- | --- |
| `PERMISSIONS` | `dict` | 旧 `metadata.permissions` | `AbilityManifest.metadata.permissions` |
| `HOOKS` | `dict` | 旧 `hooks` | `AbilityManifest.hooks` |
| `SUMMARY` | `str` | 目录摘要；空串时回落 `operation.description` | `metadata.description` |
| `TAGS` | `list[str]` | 发现/过滤 | `metadata.tags` |

实例（`sa/ability/fs/operations/read_file.py:45-58`）：`PERMISSIONS = {"fs_read_only": True}`、`HOOKS = {}`、`SUMMARY = "Read the content of a file at the specified path"`、`TAGS = ["filesystem","read"]`。

注意摘要与描述分属两面：`metadata.description` 取 `SUMMARY`，**工具面 description 取定义面 `description`**（`sa/loading/ability_catalog.py:388-389` 与 `sa/loading/tool_list.py:24`）。两处文案可以不一致，改工具描述要去改 `ability.yaml`。

### 10.4 查询与创建 API

| API | 签名 | 语义 | 行号 |
| --- | --- | --- | --- |
| `load_definition` | `(container: str) -> AbilityDefinition` | 未知容器抛 `ManifestValidationError(f"未知的 ability 容器：{container!r}")` | `:130` |
| `operation_containers` | `() -> Mapping[str, str]` | `{handler: 容器名}`，**含** registry-only | `:453` |
| `builtin_handlers` | `() -> tuple[str, ...]` | 升序 handler 列表，**含** registry-only | `:470` |
| `has_handler` | `(handler: str) -> bool` | 存在性 | `:482` |
| `ability_class` | `(handler: str) -> type[Ability]` | 未知抛 `f"未知的 ability handler: {handler!r}。已知: {builtin_handlers()}"` | `:496` |
| `create_ability` | `(handler: str, **params) -> Ability` | 原注册表 `create` 的唯一替代入口 | `:519` |
| `builtin_full_names` | `() -> tuple[str, ...]` | 声明路径成员全名，**不含** registry-only | `:425` |
| `load_builtin_manifest` | `(full_name: str) -> AbilityManifest` | 缺失抛 `FileNotFoundError(f"未知的内置 ability 全名：{full_name!r}")` | `:540` |
| `load_all_builtin_manifests` | `() -> list[AbilityManifest]` | 每次重新投影，无缓存 | `:399` |
| `build_tool_schema` | `(key, operation) -> ToolSchema \| None` | `None` 即 `expose: false` | `:284` |

### 10.5 `REGISTRY_ONLY_OPERATIONS`

`REGISTRY_ONLY_OPERATIONS: frozenset[str] = frozenset({"send"})`（`sa/loading/ability_catalog.py:81`）。原文理由（`:82-87`）：`send` 由 Core 的 room 装配期注入，历史上没有 manifest 声明；投影时跳过它以保持既有语义，`list_abilities` 的 registry-only 差异面依赖这一点。它仍是 `cluster/ability.yaml` 里的正式成员，只是不产出运行时视图。

### 10.6 `AbilityManifest` 投影形态与遗留层

`_project()`（`sa/loading/ability_catalog.py:338`）产出的 `AbilityManifest`（`sa/loading/ability_view.py:55`）：

| 字段 | 值 | 说明 |
| --- | --- | --- |
| `manifest` | `Literal["ability"]` | 视图标识 |
| `version` | `LEGACY_MANIFEST_VERSION = "1"`（`:78`） | 投影出的**旧格式版本**，不是能力定义版本 |
| `metadata` | `AbilityMetadata{namespace, name, title, description, tags, permissions}` | `permissions: PermissionFlags` 默认布尔全 False |
| `tool` | `ToolSchema \| None` | `expose: false` 时为 `None` |
| `implementation` | `{type: "builtin", handler}` | 固定 |
| `hooks` | `AbilityHooks{pre/post/on_error}` | 来自 `HOOKS` 常量 |

版本校验（`sa/loading/ability_view.py:66`）：major 必须在 `SUPPORTED_VERSIONS` 里，否则 `ManifestVersionError`。另一个 validator（`:75`）：`tool is None` 且 `implementation.type in ("sandbox","wasm")` 时抛「tool is required when implementation.type is '{...}'」。

`manifest_io.parse_ability_manifest` / `validate_manifest` / `BuiltinManifestStore`（`sa/loading/manifest_io.py:61-150`）针对的是**另一种文本形状**（`manifest: ability` + `version: "1"` + `metadata/tool/implementation/hooks`）。生产代码零调用，引用只在 `ghrah-core/examples/` 与 `tests/manifest/*`、`tests/core/test_*`。`load_agent` 恒抛 `ManifestNotFoundError`、`list_agents` 恒返回 `[]`（`:140-150`）。

结论：**写能力包时不要写 `manifest: ability`**，那是遗留形状；真路径是 `ability.yaml` + `schema_version: 0.1-draft`。

### 10.7 三口径与差额

| 口径 | 含 `send`？ | 含 `conversation`？ | 数字 | API |
| --- | --- | --- | --- | --- |
| handler 全集 | 是 | 是 | **22** | `builtin_handlers()` |
| 运行时视图 | 否 | 是 | **21** | `builtin_full_names()` / `load_all_builtin_manifests()` |
| 模型工具面 | 否 | 否 | **20** | `load_tool_list()` |
| 扫描条目 | 是 | 是 | 17（5 容器 + 12 技能包） | `scan_abilities()` |

两个差额各有各的原因，都发生在**不同的面**上：

- `send` 少一次：在 `REGISTRY_ONLY_OPERATIONS` 里（§10.5），投影时被跳过。它仍可被 `has_handler("send")` 与 `create_ability("send")` 解析。
- `conversation` 少一次：定义面写了 `expose: false`（`core/ability.yaml:42`），且实现的 `bind_tool()` 返回 `None`（§11.3）——**两处都关**，工具面才真的少一个。

`send` 与 `conversation` 的差别很重要：前者是"有实现、无视图"，后者是"有实现、有视图、但不进工具面"。写文档时若把两者混为一谈，就会得出"关掉 `expose` 就能隐藏工具"的错误推论——`expose` 只影响投影口径，真正决定模型能否调用的是 `bind_tool()`。

20 个工具名（升序，实测 `91f8220`）：

```
broadcast_message  delete_file   edit_file        end_task        execute_command
list_abilities     list_directory load_skill      mcp_call        move_file
query_agents       query_manifests read_file      recall_context  search_files
send_message       session_info  spawn_agent      terminate_agent write_file
```

### 10.8 扫描报告的合并形态

`scan_catalog()`（`sa/loading/catalog.py:90`）把目录面、运行时视图、handler 表、容器表与 issue 合成一个 `CatalogReport`（`:43`），给"看一眼全貌"的场景用。它不引入新语义，只是把上面几张表并排放；任何一项缺失都在 `issues` 里体现。

## 11. 工具面

### 11.1 结构与命名

`load_tool_list(pool=None) -> ToolList`（`sa/loading/tool_list.py:107`）。`ToolEntry`（`:47`）：`container`、`operation`（= 工具名）、`name`（与 `operation` 同值，单列字段便于客户端直接取）、`tool`、`root`，属性 `full_name` = `"{container}.{operation}"`。`ToolList`（`:72`）：`roots`、`tools`、`entries`、`issues`，属性 `names`，方法 `get(name)`（线性查，不按名字猜容器）。

工具体由 `ToolSchema.to_openai_tool()` 产出（`:143`），形状 `{"type":"function","function":{"name","description","strict","parameters"}}`。

工具名 = 成员键（`read_file`），**没有** `namespace__op`、没有 `fs.read_file` 前缀（`:23`、`:53-57`、`:129-131`）。跨容器撞名的机制是"先入者胜 + 记 issue"，没有去重前缀机制（`:131-142`，文案 `重复的工具名 {key!r}：已由容器 {holder.container!r} 提供，本条被忽略`）。

### 11.2 筛选、排序、去重

- 筛选：`build_tool_schema(key, operation)` 返回 `None`（即 `expose: false`）就跳过（`:128-130`）。
- 收集顺序：容器名字升序 × 定义内成员声明顺序。
- 输出排序：最后按**工具名升序**重排 `tools` 与 `entries`（`:155-160`），两者同序。
- `ToolList.get()` 不做模糊匹配。

### 11.3 模型实际看到的工具面：`bind_tool()`

> **本节是全文最容易读错的地方。** `load_tool_list()` 是**投影**，不是生产链路；模型实际拿到的工具来自各实现类的 `bind_tool()`。

同一个 operation 的参数形状被写了两遍：`ability.yaml` 的 `operations.<键>.input_schema`（**声明面**）与实现里 `bind_tool()` 返回的输入模型 `model_json_schema()`（**实现面**）。

生产链路走实现面：`register_ability()`（`core/../agents/base.py:212`）调 `ability.bind_tool()`（`:235`），非 `None` 就追加进 `_bound_tools`（`:236-237`）；LLM 创建时（`:453-454`）与运行期每次注册/注销之后（`:416-426`）经 `self._llm.configure_tools(self._bound_tools)`（`chat/format/__init__.py:84`）下发 provider，最终在 `openai.py` 拼进 `api_kwargs["tools"]`。

**`bind_tool()` 同时决定"有没有这个工具"和"它长什么样"**：

- 返回 `None` ⇒ schema 不进 `_bound_tools` ⇒ 模型看不到、也调不到该工具。`conversation` 就是这样被刻意隐藏的（`bind_tool` 返回 `None`）。所以**新增成员必须实现 `bind_tool()`**，它不是可选项。
- `function.description` 由实现自己写（如 `sa/ability/core/operations/load_skill.py:117-123`、`sa/ability/fs/operations/read_file.py:130-137`）——**模型读的是这段文字**，不是 `ability.yaml` 里的 `description`。
- `function.parameters` 由 pydantic 输入模型 `model_json_schema()` 生成（`sa/ability/fs/operations/read_file.py:138`）。

### 11.4 声明面 vs 实现面的实测漂移

内置树 22 个成员中 19 个可比，**18 个存在差异**（2026-10-28 记账，`91f8220` 未变）。多数只是 pydantic 装饰（`title`、`additionalProperties: false`、`strict`），语义差异 5 处：

| 成员 | 差异 |
| --- | --- |
| `load_skill` | 声明面缺 `server`、`tool`（实现接受） |
| `search_files` | 声明面缺 `context_lines` 的 min/max、`max_results` 边界、`include` 可空 |
| `recall_context` | 声明面 `mode` 是自由字符串，实现是枚举 |
| `execute_command` | `working_dir` 声明非空、实现可空 |
| `list_directory` | `limit` / `offset` 声明有 `minimum: 0`、实现没有 |

**当前口径：以实现面为准。** 声明面的 `input_schema` 是文档性载荷，不参与校验（没有代码读它校验入参），只出现在 `list_abilities` 之类的人读视图里。新增成员时若只改 `ability.yaml` 不改 `bind_tool()`，模型会继续拿到旧说明书——不报错、不告警。

为什么现在不修：三条收敛路径（① `bind_tool()` 从声明投影；② 删掉声明面 `input_schema`、只留实现面；③ 两份都留、加一致性测试钉住）任选其一，都要动全部实现脚本或全部 5 份 `ability.yaml`，且会削弱"定义面自足"。在那之前同步靠人，记作 D-14（§18）。

另两个同源遗留：`to_prompt_description()`（`sa/execution/base.py:83-93`）注释已标过时，没有调用者；`bind_tool()` 在协议里的位置是 `core/ability_protocol.py:41`。

## 12. 技能成员：目录、指纹与 lock

### 12.1 技能成员与它对应的目录

一个容器可以有 0 个或 1 个技能成员（§17 INV-04）。与能力成员的区别在于"实现"是什么：能力成员的实现是 `operations/<成员键>.py` 里的 `Ability` 子类，会被投影成 tool 并产生 tool call；技能成员的实现是**一个技能包目录**，入口正文固定为 `skills/SKILL.md`（`sa/loading/skill_fingerprint.py:124`，`ENTRY_BODY_RELPATH = "skills/SKILL.md"`），由模型在需要时按名读取，不产生 tool call。

本组 5 个文件各管一段，全部由扫描器驱动（`sa/loading/ability_scan.py:50`、`:55`、`:62` import 它们）：

| 文件 | 管什么 | 一句话 |
| --- | --- | --- |
| `skill_context.py` | 装载与渲染 | 目录注入文案（纯渲染，不读盘）+ 按定位器读正文（无缓存） |
| `skill_fingerprint.py` | 整树观测 | 产 `ghrah-skill-tree-v1` 文本 manifest 与两枚派生摘要 |
| `skill_lock.py` | 落盘摘要 | 把 manifest 与权限写成 `skills/.lock.yaml` |
| `skill_models.py` | 结果模型 | `LockOutcome` / `LockRecord` / `SkillFingerprint` / `BundleVerification` / `SkillInitResult` |
| `skill_provider.py` | 门面 | 把上面四件事包成 `SkillAbilityProvider` 给执行层用 |

### 12.2 两段式注入：目录常驻、正文按需

模块 docstring（`sa/loading/skill_context.py:5-11`）称这条设计为"两段式注入"：目录（目录名 + 一行摘要）随上下文注入，正文在模型显式调用 `load_skill` 时才读。`:13-18` 明确划清职责：**目录摘要与注册指纹是两件事**，前者给人/模型看、可以截断，后者要逐字节稳定。

`:24-25` 又收窄了范围："本模块只做两件事：纯渲染（不读盘）与按定位器读正文（无缓存）"。所以目录渲染可以完全离线、可以按 `changed` 做增量替换，而"读到什么"每次都是此刻磁盘上的内容。

| API | 语义 | 行号 |
| --- | --- | --- |
| `catalog_description(text, max_length=500)` | 空白折叠，保留前 `max_length-3` 个字符再接 `"..."` | `:100-113` |
| `digest_skill_catalog(skills)` | 按 name 排序 → `json.dumps([name, truncated_description])` → `"\n"` 连接 → sha256 hex | `:116-132` |
| `render_skill_catalog(skills, *, changed)` | `changed` 或目录为空时换用完整 header，否则用"有变化"header | `:135-160` |
| `read_skill_text(locator)` | **唯一**读盘实现；`OSError`/`UnicodeDecodeError` 归一为 `SkillContextError`，消息 `读取技能文档失败: {locator} ({exc})` | `:163-181` |
| `read_skill_body(skill)` | 读技能正文并包成 `SkillDocument` | `:216-242` |
| `read_skill_document(skill)` | 弱类型入口：`getattr` 嗅探输入形态 | `:334-382` |

两段 header 常量：`_HEADER_FIRST` / `_HEADER_CHANGED`（`:63-70`）、`_FOOTER` / `_FOOTER_EMPTY`（`:71-76`）；截断上限 `CATALOG_DESCRIPTION_MAX_CHARS = 500`（`:60-61`）。

`SkillCatalog`（`:245-308`）把"目录"做成对象：`.digest`、`.names`、`.render(changed=False)`、`.get()`（未命中返回 `None`）、`.read()`（未命中抛错）。`load_skill_catalog(pool=None)`（`:311-326`）在 `pool is None` 时落到 `default_pool()`——这是"不给参数就用内置树"的唯一入口。

一处容易混的口径：**`digest_skill_catalog()` 的返回值没有算法前缀**，而 lock 里的 `content_digest` 有（`"sha256-tree-v1"` 记录在 `provenance.algorithm`）。两者不能互相替代存放。

### 12.3 整树观测：`ghrah-skill-tree-v1`

manifest 是纯文本（格式说明 `sa/loading/skill_fingerprint.py:18-24`，实现 `:259-261`）：首行 `ghrah-skill-tree-v1`，第二行 `skill<TAB>full_name`，其后每行以 `d`（目录）/`f`（文件）/`l`（链接）开头；文件行 5 列，带 `size` 与 `mtime_ns`。UTF-8 + LF，末尾必有 `\n`，按 relpath 的 **UTF-8 字节序**排序（`:526`、`:636`）。

算法与上限：

| 常量 | 值 | 行号 |
| --- | --- | --- |
| `MANIFEST_MAGIC` / `ENTRY_MAGIC` | 格式标识 | `:79` / `:82` |
| `CONTENT_ALGORITHM` | `sha256-tree-v1` | `:85` |
| `SNAPSHOT_ALGORITHM` | `sha256-snapshot-v1` | `:86` |
| `ENTRY_ALGORITHM` | `sha256-entry-v1` | `:87` |
| `FILE_DIGEST_PREFIX` | `sha256:` | `:89` |
| `DEFAULT_MAX_ENTRIES` | 1024 | `:109` |
| `DEFAULT_MAX_TOTAL_BYTES` | 64 MiB | `:112` |
| `_CHUNK_SIZE` | 1 MiB（流式读） | `:115` |
| `ENTRY_BODY_RELPATH` | `skills/SKILL.md` | `:124` |

排除表（决定"哪些东西不算技能内容"）：目录 `.git`/`node_modules`/`__pycache__`/`.venv`/`dist`（`:92`）、文件 `.lock`/`.DS_Store`/`Thumbs.db`（`:97`）、后缀 `.pyc`/`.pyo`/`.swp`/`.swo`/`~`（`:106`）。

manifest 是**权威**，两枚摘要是它的派生投影：`content_digest`（内容维度）与 `snapshot_digest`（快照维度）。这条在 lock 的字段注释里写死（`sa/loading/skill_lock.py:11-13`）。

| API | 职责 | 行号 |
| --- | --- | --- |
| `entry_relpaths(*, definition_present, body_relpath=ENTRY_BODY_RELPATH)` | 入口成员应有的相对路径集合 | `:346-364` |
| `entry_digest(manifest, *, body_relpath)` | 从 manifest 里取入口摘要；降级/缺失/非普通文件/无 digest → `None` | `:373-395` |
| `fingerprint_entry(skill_dir, *, body_relpath)` | 单入口指纹；缺入口文件抛 `SkillParseError` | `:398-428` |
| `build_manifest(skill_dir, *, full_name, max_entries, max_total_bytes)` | 两阶段建 manifest | `:431-555` |
| `parse_manifest(text)` | 文本 → `(full_name, entries)` | `:558-575` |
| `parse_degrade(raw)` | 解析降级记录 | `:598-623` |
| `diff_manifests(recorded, current)` | 两次 manifest 求差 | `:626-657` |

数据结构：`ManifestDegrade`（`:128-158`，`reason`/`entries`/`total_bytes`/`limit`/`detail`，`to_mapping` 省略空值）、`BundleEntry`（`:161-199`，`kind`/`relpath`/`size`/`digest`/`mtime_ns`/`target`，另有把自身渲染成内容行与快照行的两个方法）、`ManifestChange`（`:202-208`，`relpath` + `kind ∈ added|removed|kind|content|mtime|link`）、`BundleManifest`（`:211-290`，`full_name`/`entries`/`degraded`/`issues`，派生 `complete`/`entry_count`/`files`/`total_bytes`/`max_mtime_ns`/`text`/`content_text`/`content_digest`/`snapshot_digest`/`find`）。

降级（degrade）四个触发点：条目数超限（`:493`）、总字节超限（`:502`）、出现不安全路径字符（`\t`/`\n`/`\r`，`:512-522`）、目录不可读（清空 entries，`:535`）。**降级绝不产出"部分树摘要"**（`:110`、`:257-267`、`:524-525`）——宁可没有摘要，也不能给一个看起来合法、实际只覆盖半棵树的摘要。

漂移口径（`diff_manifests` 与 `BundleVerification` 共用）：`content`/`added`/`removed`/`kind`/`link` 属内容维度；`mtime` **只**属快照维度；同时变化只报 `content`（`elif` 短路 `:650-653`）；无法裁决时返回 `None` 而不是猜（`sa/loading/skill_models.py:154-161`）。

三个实现细节值得记住：

- **Windows junction 不是 symlink**：`is_symlink()` 对 junction 返回 `False`，所以判定走 `st_reparse_tag`（`:308-321`），否则 junction 会被当成普通目录递归进去。
- **观测期被改写不降级**：只记 issue 并采读后值（`:543-545`）。
- **`_read_link` 失败用 `path.name` 记 issue**（`:337-343`），不是空串。

### 12.4 lock 文件：`skills/.lock.yaml`

lock 是本模块唯一的落盘产物。字段归属写在 `sa/loading/skill_lock.py:7-8`：框架写 `full_name`/`provenance`/`permission`/`protected`；`:9` 特别声明「**版本不在本文件**」——版本属于定义面，lock 不复制。

版本化在 schema 上：`:11-13` 说明 **schema 2 = 整树观测，manifest 是权威、两枚摘要是派生投影**；`:14-15` 说明 schema 1 不自动改写，读取时按"需迁移"报出。

落盘形状（`build_lock` 的输出，`:122-139`）：

```yaml
schema: 2
full_name: <技能全名>
provenance:
  source: <来源>
  algorithm: sha256-tree-v1
  content_digest: <内容维度摘要>
  snapshot_digest: <快照维度摘要>
  recorded_at: <RFC3339>
manifest:
  text: <整树 manifest 原文>
  degraded: {reason, entries, total_bytes, [limit], [detail]}
  issues: [...]
permission:
  approve: ...
  files: ...
  net: ...
  # 其余键原样透传
protected: false
```

注意两件事：`entry` **不落盘**，读的时候现算（`:239`）；没有 `version` 键（`:9`）。

| API | 语义 | 行号 |
| --- | --- | --- |
| `utc_now_rfc3339()` | 记录时间 | `:68-74` |
| `extract_permission_request(frontmatter)` | 从技能正文 frontmatter 抽权限声明 | `:77-98` |
| `build_lock(*, full_name, source, manifest, frontmatter, recorded_at, protected=False)` | 组 dict | `:101-139` |
| `render_lock(data)` | dict → YAML 文本 | `:142-150` |
| `read_lock(path)` | 粗校验读取（不验 schema 与字段完整性） | `:153-169` |
| `read_lock_record(lock_path, *, body_relpath=ENTRY_BODY_RELPATH)` | 严格读取并组 `LockRecord` | `:172-240` |
| `create_lock(...) -> bool` | 用 `open(x)` 独占创建；已存在返回 `False` 不抛 | `:266-278` |
| `replace_lock(...)` | 写临时文件后 `os.replace` 原子替换 | `:281-297` |

`extract_permission_request` 的默认值策略是**保守为中心**：`approve` 取非空字符串否则 `hitl`；`files` 非 Mapping 则 `{}`；`net` 否则 `deny`；其余键原样保留（`:77-98`）。

`read_lock_record` 的失败语义（`:189-227`）全部归一为 `SkillLockError`：schema ≠ 2、缺 `full_name`、`provenance` 非映射、`source`/`algorithm` 缺失、`recorded_at` 非字符串、`manifest` 非映射、未降级却缺摘要、降级却有摘要、摘要非字符串。降级时**不校验** `text`（`:256-257`），未降级缺 `text` 则抛（`:259-261`）。

### 12.5 结果模型

| 模型 | 字段 | 行号 |
| --- | --- | --- |
| `LockOutcome` | `full_name`/`path`/`created`/`reason` | `sa/loading/skill_models.py:32-39` |
| `LockRecord` | `schema`/`full_name`/`source`/`algorithm`/`content_digest`/`snapshot_digest`/`recorded_at`/`manifest`/`entry` | `:42-70` |
| `SkillFingerprint` | `full_name`/`skill_dir`/`lock_path`/`current`/`recorded`/`record`/`lock_issue`/`drifted` | `:73-113` |
| `BundleVerification` | `full_name`/`skill_dir`/`lock_path`/`record`/`current`/`changes`/`lock_issue`/`content_drifted`/`snapshot_drifted`/`max_mtime_ns` | `:116-161` |
| `SkillInitResult` | `scan`/`locks`/`lock_issues` | `:164-179` |
| `SkillDocument` | `full_name`/`skill_dir`/`path`/`body_path`/`content` | `:182-209` |

两处口径差异必须记住，不要按直觉混用：

- `SkillFingerprint.drifted`（`:105-113`）只覆盖**入口那两个文件**，且"不知道"时用 `False` 兜底（`:109-110`）；`BundleVerification` 的 `content_drifted`/`snapshot_drifted`（`:120-121`）覆盖整树，且"不知道"时用 `None`。**`False` 与 `None` 在这里语义完全不同**：前者是"比过了、没漂移"，后者是"没得比"。
- `LockRecord.schema` 由 `read_lock_record` 常量回填（`skill_models.py:51` vs `skill_lock.py:231`），不从文件里读，所以文件里写 `schema: 99` 只会在校验阶段被拒，不会出现在模型里。

`SkillDocument` 在本模块与 `skill_context.py` 各有一个同名类，`skill_models` 侧以别名 `SkillPackageDocument` 导出（`:212-218`）。这是刻意的双形态：一个描述"读到的正文"，一个描述"包里的正文"。

### 12.6 提供者门面：`SkillAbilityProvider`

`sa/loading/skill_provider.py`，构造 `__init__(pool)`（`:61-73`，空池抛 `SkillError`）：

| 方法 | 语义 | 行号 |
| --- | --- | --- |
| `from_root(root)` | 以单根建 provider | `:80-90` |
| `.scan()` | 扫盘 | `:92-99` |
| `.get_skill(full_name)` | 全名 → 成员键 → 容器名的宽松解析，要求唯一 | `:101-129` |
| `.read_skill(...)` | 读正文，返回 `object`（弱标注） | `:131-147` |
| `.query_fingerprints()` / `.get_fingerprint(...)` | 观测指纹；**每次都重新扫盘，不校验** | `:149-157` / `:159-178` |
| `.ensure_roots()` / `.initialize(*, ensure_dirs=True)` | 建根、首次扫描 | `:180-186` / `:188-204` |
| `.verify(...)` / `.relock(*, recorded_at=None)` | 校验 / 无条件重写 lock | `:206-222` / `:224-241` |
| `default_provider()` | 内置树的默认实例，**不读配置** | `:244-253` |

`:162-164` 的注释自己承认了代价：连续两次调用会扫两次盘。`initialize` 则只扫一次（`:202`）。

### 12.7 三个写盘时机的差异

`ensure_locks`（`sa/loading/ability_scan.py:795-863`）、`relock_skill`（`:866-886`）、`verify_skill`（`:889-928`）都跟 lock 有关，但战略完全不同：

| 维度 | `ensure_locks` | `relock_skill` | `verify_skill` |
| --- | --- | --- | --- |
| 写盘 | 只写**缺失**的 lock | **无条件**覆盖 | 不写 |
| 输入 | `AbilityScanResult` | 单个 `AbilitySkill` | 单个 `AbilitySkill` |
| 开销 | 只对付费成员建 manifest | 必付 | 必付（含本来就没有 lock 的情形） |
| 失败 | 写失败**不产** `LockOutcome`，只记 issue 并 `continue`（`:836-844`） | 抛 `SkillLockError` | 坏 lock **不抛**，填 `lock_issue`（`:911-916`） |

`ensure_locks` 产 `LockOutcome` 的三个时机：lock 已存在（`:824-832`）、新建成功（`:845-852`）、并发创建撞上 `FileExistsError` → `created=False`（`:850`，配合 `skill_lock.py:271-275`）。"写失败只记 issue"是有意的：一个技能包写不进 lock 不该挡住整棵树。

### 12.8 已知陷阱

| 陷阱 | 事实 | 证据 |
| --- | --- | --- |
| 权限直通不成立 | `skill_lock.py:17-19` 声称 permission 直通正文申请值，但唯一生产写入路径传 `frontmatter={}`，落盘恒为保守默认 `{approve: hitl, files: {}, net: deny}` | `sa/loading/ability_scan.py:950` |
| 死常量 | `_PERMISSION_KEY_ORDER` 声明后全模块无引用 | `sa/loading/skill_lock.py:65` |
| `protected` 恒 False | 默认 `False` 且生产路径从不传 | `sa/loading/skill_lock.py:108`、`:120` |
| 双异常域 | 同一文件里 `SkillParseError`（缺入口）与 `SkillLockError`（lock 坏）并存，捕获方要同时接 | `:425` vs `:569` |
| `body_relpath` 传错 | `entry` 静默变 `None`，不报错 | `:239` |
| 同名双类 | `skill_context.SkillDocument` vs `skill_models.SkillDocument`（别名 `SkillPackageDocument`） | `skill_models.py:212-218` |

### 12.9 接线现状

这套 provider 在**仓内没有生产装配**。`ghrah-core/src/ghrah/core/unit.py:720-722` 装配的是旧树 `ghrah.skills.SkillAbilityProvider`（`from_root(config.skill_root)`），而旧树是 **schema 1** 的单文件指纹实现（`skills/lock.py:44-49`，`INITIAL_VERSION = "0.1.0"`）。本组 `sa/loading/skill_provider.SkillAbilityProvider` 的可见引用只有两处：`ghrah/ability/__init__.py:28` 的再导出，以及 `tests/scheme_ability/test_source_integration.py:12`。

消费方（`list_abilities`、`load_skill`、`execution_services.py:81` 的 `SKILL_PROVIDER` 槽）全部按 duck-type 接收，所以**注入哪一份决定跑哪一份实现**——这是本组最大的接线风险，也是 §18 D-21 在技能面的具体表现。改造这 5 个文件时还要同步 `ability_scan.py`（`:50`、`:55`、`:62`）的调用点。

顺带记录一处重复定义：`FS_ABILITY_TYPES` 在本包 `sa/kinds.py:18` 有一份，在旧树 `ghrah/abilities/__init__.py:116` 有一份逐字相同的副本，两边都自称"单一真源"；`CLUSTER_ABILITY_TYPES` 同样两份（`sa/kinds.py:31`、`ghrah/abilities/__init__.py:133`）但零消费者（§18 D-19）。

## 13. 执行面

### 13.1 `Ability` 基类契约

`class Ability(ABC)`（`sa/execution/base.py:32`）：

| 成员 | 签名 | 必需 |
| --- | --- | --- |
| `name` | `property -> str` | 抽象 |
| `execute` | `async (context: AbilityExecutionContext) -> ActionResult` | 抽象 |
| `get_hooks` | `() -> list[Hook]` | 抽象 |
| `bind_tool` | `() -> dict[str, Any] \| None`（默认 `None`） | 可选，但**生产上必填**（§11.3） |
| `to_prompt_description` | `() -> str`（默认 `""`） | 可选，已过时 |
| `get_default_state` | `() -> dict[str, Any]`（默认 `{}`，作 StateManager 初始作用域） | 可选 |

`base.py` 的 `__all__` 再导出 `ActionOutcome` / `ActionResult`（`:19`、`:25-29`），实现脚本不必额外 import。

**方法名是 `execute`，不是 `run`**；执行器只调 `execute`（`sa/execution/executor.py:400`）。

docstring（`:5-12`）给了四句话的设计意图：一个 Ability 就是一次 tool call；它是最小能力单元；`bind_tool()` 是原生 function-calling 绑定（高优先级，生产链路走这条）；`to_prompt_description()` 是 prompt 模式（低优先级，docstring 注明"目前已经没有新的模型不支持原生工具调用了"）。

### 13.2 `AbilityExecutionContext`（兼容门面）

`@dataclass(init=False)`（`sa/execution/context.py:38`），模块 docstring（`:5-9`）自称 compatibility facade：「keeps the old construction and attribute surface while delegating the real invocation/data/services fields」。

字段：`invocation: AbilityInvocation`（`:46`）、`data: ExecutionData`（`:47`）、`services: ExecutionServices`（`:48`）、`agent_state: dict[str, Any]`（`:49`）、`last_action_result: ActionResult | None`（`:50`）。

关键字专用 `__init__`（`:52-98`）合并规则：`ability_name = current_ability_name or invocation.ability_name`（`:71`）；`args = tool_args if tool_args is not None else invocation.tool_args`（`:72`）；`invocation` 重建且保留 `tool_call_id`（`:73-77`）；`accumulated_data` 若给则**整包重建** `ExecutionData(accumulated_data)`（`:80-81`）；其余旧关键字（`context_manager`/`supervisor`/`agent_name`/`manifest_store`/`skill_provider`/`mcp_runtime`）逐个 `services.set(...)`（`:84-95`）。

兼容属性读写性（重要，踩过坑）：

| 属性 | 可读 | 可写 | 行号 |
| --- | --- | --- | --- |
| `current_ability_name` | 是 | 是（property + setter） | `:100-110` |
| `tool_args` | 是 | 是（property + setter） | `:112-120` |
| `accumulated_data` | 是（返回**活的** `data.raw`） | 是 | `:124-130` |
| `context_manager` / `supervisor` / `agent_name` / `manifest_store` | 是 | 是 | `:132-163` |
| `skill_provider` | 是 | **否**，赋值抛 `AttributeError` | `:165-168` |
| `mcp_runtime` | 是 | **否**，赋值抛 `AttributeError`；`None` = 未接线 | `:170-173` |

状态 API：`get_ability_state()`（`:175`）、`update_state(changes)`（`:182-196`，缺 `context_manager` 抛 `RuntimeError("Cannot update state: context_manager is not set. This context may have been created outside the drive loop.")`）、`update_global_state()`（`:198-205`）。

调用标识只有一个：`invocation.tool_call_id`（默认 `""`），没有 trace/span 概念。

### 13.3 数据总线与服务总线

`DataKey` / `ExecutionData`（`sa/execution/execution_data.py`）：`DataKey` 是 `{name, value_type}` 的 frozen dataclass，但 `value_type` **纯声明**，`get`/`set` 从不做 `isinstance` 校验（`:52-60`）。`ExecutionData.__init__` 收到 `dict` 时**按引用持有**（`:41-42`），`raw` 返回底层活字典（`:46-50`），`snapshot()` 深拷贝（`:76-79`）。

预置 `DataKey`（`:82-86`）：

| 键常量 | 存储名 | 标注类型 |
| --- | --- | --- |
| `TOOL_ARGS` | `tool_args` | `dict[str, Any]` |
| `LLM_RESPONSE` | `llm_response` | `str` |
| `COT_CONTENT` | `cot_content` | `str` |
| `CUMULATIVE_TOKEN_USAGE` | `cumulative_token_usage` | `dict` |
| `FINAL_RESPONSE` | **`response`** | `str` |

`FINAL_RESPONSE` 的实际存储名是 `"response"`，不是 `"final_response"`——按下划线直觉拼 key 会静默读不到（§18 D-16）。

预置 `ServiceKey`（`sa/execution/execution_services.py:75-86`）：`CONTEXT_MANAGER`（`ContextManagerProtocol`）、`SUPERVISOR`（`SupervisorProtocol`）、`AGENT_NAME`（`str`）、`MANIFEST_STORE`（`Any`）、`SKILL_PROVIDER`（`Any`）、`MCP_RUNTIME`（`Any`）。`require()` 对未设置与 `None` 都抛 `RuntimeError(f"Required execution service '{key.name}' is not set.")`（`:56-61`）。

`ExecutionFrame`（`sa/execution/execution_frame.py:22`）是 Hook 与驱动循环共用的精简上下文：`data`、`services`、`agent_state`、`last_action_result`、`current_node_id`。它与 `AbilityExecutionContext` 重复 4 个字段却少了 `invocation`，是两套并行模型（§18 D-5）。

### 13.4 一次工具调用的时序

`execute_tool_calls`（`sa/execution/executor.py:429`）→ 每个调用一个 `_execute_single_with_context`（`:536`）→ `execute_ability`（`:302`）。单次执行的步骤：

1. **无参数校验**。参数已在 `_execute_single_with_context:558` 经 `_resolve_paths`（`:590`）做过路径解析（只处理 `ABILITY_PATH_SPECS` 的键，绝对路径原样保留）。schema 校验不发生在执行器里。
2. `PRE_EXECUTE` hook（`:323-325`）。
3. hook 结果分派（`:328-397`），四个分支：

   | 分支 | 条件 | 产出 |
   | --- | --- | --- |
   | 审批 | `not should_continue` 且 `requires_hitl` | `handle_hitl_hook_result`（`:332`），见 §15.4 |
   | 拦截 | `not should_continue` 且无 HITL | `ActionResult(FAILURE)`，`error = hook_result.message or "Execution blocked by hook"`（`:382-387`） |
   | 改道 | `should_continue` 且 `route_to` | `ActionOutcome.DELEGATE` + `next_action_hint=route_to`（`:388-394`） |
   | 改上下文 | `should_continue` 且 `modified_context` | `context.accumulated_data.update(...)`（`:395-397`） |

4. **没有权限检查**。执行器的闸门只有 PRE_EXECUTE hook 与 HITL；权限判定在 hook 里（§15）。
5. `action_result = await ability.execute(context)`（`:400`）。
6. **没有异常处理**。docstring 只声明 `Raises: HookError`（`:319-321`）；隔离在 `execute_tool_calls`。
7. `await_reply` 早退（`:402-406`）：`action_result.data.get("await_reply")` 为真时把 `tool_args` 塞回、把 context 停进 `_deferred_contexts[message_id]`，**在 POST_EXECUTE 之前返回**。
8. `POST_EXECUTE`（`:409-411`，带 `result`）；返回值被丢弃——post hook 里的 `stop()` / `route_to` **不生效**（§18 D-13）。
9. 原样返回 `action_result`（`:413`）。延迟重放走 `finish_deferred`（`:415`），只重发 POST_EXECUTE（docstring：「持久同步回复确定后执行原 POST_EXECUTE，恢复时不重跑 PRE/工具」）。

### 13.5 并行与顺序

已知 ability 的调用被收集后 `asyncio.gather(..., return_exceptions=True)`（`:492-505`），**并行**执行，单次调用也走同样的一元素 gather。未知 ability 直接内联产出 `ActionResult(FAILURE, {"error": f"Unknown ability: {name}"})` 并**立刻 append**（`:462-480`），而已知调用的一批结果在 `:506-525` 才 append——因此结果列表顺序可能与 `tool_calls` 顺序不一致（§18 D-6）。

### 13.6 异常与取消

`:506-525`：非 `Exception` 的 `BaseException`（含 `CancelledError`）**原样重抛**（`:507-511`）；`Exception` 转成 `ActionResult(FAILURE, {"error": str(r)})`（`:512-522`）。注释（`:440-441`）明确子任务取消不得折算成结果——否则 `r["tool_call_id"]` 会抛 `TypeError`，把一个控制流信号变成新的故障。

一次 `ability.execute` 挂住会挂住整批 gather：执行面没有能力级超时（§18 D-1）。

### 13.7 上下文装配：三件套、路径解析与服务注入

每次调用的上下文在 `:568-584` 装配，包含三部分：

| 部分 | 内容 |
| --- | --- |
| 身份 | `AbilityInvocation(tool_call_id=...)`、`current_ability_name`、`agent_name` |
| 数据 | `tool_args=解析后的参数`、`accumulated_data={**deepcopy(accumulated_data), "tool_args": resolved_args}`、`last_action_result`、`agent_state=context_manager.get_current_state()` |
| 服务 | 从 `getattr(self, "_supervisor", None)` 取 `supervisor` / `manifest_store` / `skill_provider` / `mcp_runtime`（`:563-566`） |

服务注入用 `getattr` 而非属性直取，是给"没有 supervisor 的独立运行"留位。`:563-566` 的注释记录了一次真实故障：tool-call 路径此前不注入 supervisor，导致集群类成员的 `supervisor` 恒为 `None`，`_cluster_common.cluster_supervisor_error()` 于是把所有集群调用都报成 standalone。

`deepcopy(accumulated_data)` + 塞入 `tool_args` 的含义：每个并行调用拿到**自己的一份**累积数据，父级数据只读；`tool_args` 在顶层覆盖，成员读 `context.tool_args` 才是解析后的值。

## 14. Hook 机制

### 14.1 十个 HookPoint

`HookPoint(str, Enum)`（`sa/execution/hooks.py:41`）：

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

执行器只发 `PRE_EXECUTE`（`:323-325`）与 `POST_EXECUTE`（`:409-411`、延迟重放 `:421-423`）；其余 8 个归驱动循环与 action 层（层级说明见 `:12-18`、`:44-58`）。因此权限与 HITL hook 全部挂在 `PRE_EXECUTE`。

### 14.2 Hook 契约

`class Hook`（`sa/execution/hooks.py:190`）：类属性 `hook_point: HookPoint`（`:209`）、可选 `scope: HookScope | None = None`（`:210`）；抽象 `async should_trigger(context) -> bool`（`:212-222`）与 `async execute(context, result: ActionResult | None) -> HookResult`（`:224-237`，`result` 在 PRE_EXECUTE 为 `None`、在 POST_EXECUTE 为 `ActionResult`）。

### 14.3 `HookResult` 语义矩阵

`@dataclass HookResult`（`:114`）：`should_continue: bool = True`、`route_to: str | None`、`modified_context: dict | None`、`data_updates: dict | None`、`message: str | None`、`requires_hitl: bool = False`。`__post_init__`（`:133-141`）把 `modified_context` 与 `data_updates` 当同一份合并后的 dict 的别名。

| 工厂 | 产生的字段 | 执行器行为 |
| --- | --- | --- |
| `continue_()`（`:143`） | 全默认 | 继续 |
| `stop(message=)`（`:148`） | `should_continue=False`，`requires_hitl=False` | 直接 `FAILURE`（`executor.py:382-387`） |
| `hitl(message=)`（`:153`） | `should_continue=False`，`requires_hitl=True` | 进入审批等待（`executor.py:330-332`） |
| `route(target, message=)`（`:161`） | `route_to` | `ActionOutcome.DELEGATE`（`executor.py:388-394`） |
| `modified_context` / `data_updates` | dict | 合并进 `accumulated_data`（`executor.py:395-397`、`hook_runner.py:56-57`） |

合并规则 `merge()`（`:166-187`）：`should_continue` 取 **AND**（`:177`）、`route_to` 后者覆盖（`:178`）、`modified_context` 字典合并且后者覆盖（`:179-184`）、`message` 后者覆盖（`:185`）、`requires_hitl` 取 **OR**（`:186`）。含义：**一个 hook 要求停，整条链的结果就是停**；一个 hook 要求审批，就是审批。

### 14.4 `HookScope`

不是 Enum，是 `@dataclass(frozen=True)`（`:79`）：`points: frozenset[HookPoint]`、`target_abilities: frozenset[str] | None = None`。校验（`:85-89`）：`points` 为空抛 `ValueError("HookScope.points must not be empty.")`；`target_abilities == frozenset()` 抛 `ValueError("HookScope.target_abilities must be None or non-empty.")`。工厂 `global_(*points)`（`:91`）与 `for_abilities(points, abilities)`（`:97`）。

`scope is None` 的 hook 是 legacy：每次运行都要 `await should_trigger()` 再决定（`hook_store.py:80`、`:104-107`、`hook_runner.py:31-41`）；声明了 `scope` 的 hook 由索引直接选中，**跳过** `should_trigger`。现有权限 hook（`AccessApprovalHook`、`CommandApprovalHook`、`HITLApprovalHook`）都用 legacy 形态。

### 14.5 `HookRunner`：唯一执行路径

`async run(context) -> HookResult | None`（`sa/execution/hook_runner.py:17`）：

1. `context.to_ability_context()` 重建一次门面（`:27`）；
2. `store.select(point, ability_name)`（`:28`）；
3. 遍历**全部**命中 hook（`:30`），legacy 的要先过 `should_trigger`（`:31-33`，假则 `continue` `:40-41`）；
4. `await hook.execute(...)`（`:44`，总是 await）；
5. 逐个 `merge`（`:51-54`）；
6. 循环后若 `merged.modified_context` 非空，写入 `context.frame.data`（`:56-57`）；
7. 返回合并结果（`:59`）。

**没有短路**：没有 `break`，所有命中 hook 都会跑，优先级由 `merge` 的 AND/OR 决定。异常不吞：`should_trigger` 失败抛 `HookError(context.point.value, f"should_trigger failed for {type(hook).__name__}: {e}")`（`:34-38`），`execute` 失败抛同形 `HookError`（`:45-49`）；由于 `execute_ability` 无 try/except，`HookError` 会逃到 `execute_tool_calls` 变成 `FAILURE{str(e)}`。

副作用：PRE_EXECUTE 的 `modified_context` 实际被写两次——`hook_runner.py:56-57` 写 `frame.data`（与 `context.data.raw` 同一对象），`executor.py:395-397` 又写 `accumulated_data`。内容相同、幂等，但两个写入点（§18 D-7）。

### 14.6 `HookStore`

索引（`sa/execution/hook_store.py:64-69`）：`_entries`（注册顺序）、`_global_by_point`、`_by_point_and_ability`、`_legacy_by_point`。`AGENT_OWNER = "__agent_hooks__"`（`:61`）。

| API | 语义 | 行号 |
| --- | --- | --- |
| `view(owner=AGENT_OWNER)` | 返回 `HookListView` | `:71` |
| `add_hooks(owner, hooks)` | `legacy = getattr(hook, "scope", None) is None` | `:76` |
| `remove_owner(owner)` | 摘除并重建索引 | `:84` |
| `select(point, ability_name="")` | global + legacy + `(point, ability)` 三个桶取并集，再按注册顺序重排 | `:95` |
| `is_legacy(hook)` | 判定 | `:104` |

`HookListView`（`:29`）是给 `agent._all_hooks` 的兼容门面，支持 `append` / `extend` / `clear`；其 `__len__` 统计的是 `store.list_all()`（所有 owner）而不是本 owner 的子集（`:36-37`），多 owner 场景下长度会偏大（§18 D-17）。

### 14.7 `HookContext`

`@dataclass HookContext`（`sa/execution/hook_context.py:24`）：`point`、`frame`、`invocation`、`result`。`from_ability_context()`（`:32-51`）把 `data` **按引用**放进 `ExecutionFrame`（`:43-48`）并复用 `invocation`（`:49`）；`to_ability_context()`（`:53-64`）为 legacy hook 重建门面。

## 15. 权限与 HITL

### 15.1 三层结构

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

三层缺一层就会出事：判定层只出结论、Hook 层把结论翻译成控制流、执行器层负责等待与终态。跳过 Hook 层（只挂 checker）等于权限判定不生效；跳过判定层（只挂 Hook）则 Hook 没有依据。

### 15.2 FS 判定链

`FSPermissionChecker(allowed_paths=None, workspace_root=None, denied_paths=None, require_approval=True)`（`sa/permissions/fs_permissions.py:41`）；三个路径参数都 `Path.resolve()`（`:79-92`），即白名单/黑名单也消解符号链接。`require_approval` 类级默认 **True**。

`check_access(path, operation="read") -> tuple[bool, str | None]`（`:136-171`）按序判定：

| 序 | 条件 | 返回 | 行号 |
| --- | --- | --- | --- |
| 1 | 既无白名单也无 `workspace_root` | `(True, "pending")` 若 `require_approval`，否则 `(False, "Permission denied: no allowed paths configured (set allowed_paths or workspace_root, or enable require_approval for HITL)")` | `:151-157` |
| 2 | 命中 denied | `(False, "Permission denied: {path} is in denied paths")` | `:159-160` |
| 3 | 命中 allowed 或 workspace 内 | `(True, None)` | `:162-163` |
| 4 | `require_approval` | `(True, "pending")` | `:165-166` |
| 5 | 其他 | `(False, "Permission denied: {path} not in allowed paths; allowed roots: …")` | `:168-171` |

二元组语义：`(True, None)` 自动批准；`(True, "pending")` 需 HITL；`(False, reason)` 拒绝。**没有三元组**，也**没有读/写分级**——`operation` 形参在函数体内从未使用（`:136` vs `:149-171`），`check_read_path` / `check_write_path` 只是转调（`:173-203`）。读写在 hook 层由两张名单区分：`WRITE_ABILITIES={"write_file","edit_file","move_file","delete_file"}`、`READ_ABILITIES={"read_file","list_directory","search_files"}`（`:260-261`）。

`AccessApprovalHook`（`:234-299`）：`hook_point=PRE_EXECUTE`（`:258`）；`should_trigger` 只看 ability 名（`:266-269`）；`execute` 取 `tool_args`（`:275`）、无目标路径则 `continue_`（`:278-279`）、`check_access`（`:282`）后：拒绝 → `HookResult.stop(message=status)`（`:284-285`）；pending → `HookResult.hitl(message=f"Human approval required for {operation} operation on: {target_path}")`（`:287-290`）；否则 `continue_`（`:292`）。目标路径提取只取**一个**：`move_file` 取 `destination_path`，其他取 `file_path or dir_path`（`:294-299`，§18 D-11）。

关键耦合：ability 内联检查把 `(True, "pending")` **当允许放行**（`sa/ability/fs/operations/read_file.py:182-188`、`write_file.py:173-179`）。若装配期没挂 `AccessApprovalHook`，pending 就等于放行。代码注释已承认这条断链（`unit.py:512-515`、`sa/execution/ability_build.py:144-146`），见 §18 D-10。

### 15.3 命令分级

枚举精确值（`sa/permissions/command_safety.py:53`）：`SAFE="safe"`、`DANGEROUS="dangerous"`、`REQUIRE_HITL="require_hitl"`。`CommandSafetyVerdict`（`:60`）带 `category`、`base_command`、`sub_command`、`reason`。

四张默认名单：`DEFAULT_SAFE_COMMANDS`（`:67`）、`DEFAULT_DANGEROUS_COMMANDS`（`:124`）、`DEFAULT_SAFE_SUB_COMMANDS`（`:153`）、`DEFAULT_DANGEROUS_SUB_COMMANDS`（`:228`）。

判定顺序（实现 `:244-390`）：空命令 → DANGEROUS（`:311-316`）→ base 在危险表 → DANGEROUS（`:322-328`）→ base 有子命令路由表（`:330-333`）时：子命令危险 → DANGEROUS（`:337-344`）、子命令安全 → SAFE（`:345-352`）、其余 → REQUIRE_HITL（`:358-367`）→ base 在安全表 → SAFE（`:369-375`）→ `require_approval=False` → SAFE（`:377-383`，**危险名单不受影响**）→ 兜底 REQUIRE_HITL（`:385-390`）。

`parse_command()`（`:392-435`）用 `shlex.split`，`ValueError` 回退 `split()`（`:413-416`）；base 取 `Path(parts[0]).name`（`:421`，故 `/usr/bin/ls` → `ls`）；子命令 = 首个"不以 `-` 开头、不含 `/`、不含 `.`（除非以 `.` 开头）"的 token（`:424-433`）。副作用：`python script.py` 得 `sub=None`（`:430` 因 `.` 跳过），而 `git -C repo status` 会把 `repo` 误当子命令（§18 D-8）。

`extend_safe_lists()`（`:279-300`）只并入安全集合、小写归一，危险名单只增不减。

`CommandApprovalHook`（`:438`）：PRE_EXECUTE（`:448`），只拦 `execute_command`（`:453-454`），命令取 `tool_args["command"]`（`:460`），SAFE → `continue_()`、DANGEROUS → `stop(reason)`、其余 → `hitl(reason)`（`:463-469`）。

### 15.4 HITL 等待协议

`handle_hitl_hook_result`（`sa/execution/executor.py:651`）：

1. 生成 `tool_call_id`（取 `tool_args["call_id"]`，否则 `uuid4().hex[:12]`）与 `promise_id = uuid4().hex`（`:671-672`）；
2. 发布 `HITLRequestEvent`（`:676-685`）；
3. `HITLFutureStore.create_future(agent, ability, tool_call)`（`:688-692`，同键旧 future 会被 cancel 替换）；
4. 登记 promise registry（`:693-699`）；
5. 熔断判定：`degraded = limit > 0 and consecutive >= limit`（`:702-706`），默认 `hitl_consecutive_timeout_limit=0` 即**关闭**；
6. `await asyncio.wait_for(future, timeout=wait_timeout)`（`:710`，全文件唯一 `wait_for`）。

结果三种（`HitlOutcome = Literal["approved","rejected","timeout"]`，`:59`）：

| 终态 | 产出 | 行号 |
| --- | --- | --- |
| approved | 复位熔断计数，`HITLResolvedEvent(status="approved")`，`HITLWaitResult(outcome="approved", …)` | `:711-734` |
| rejected | 同上但 `status="rejected"`；消息 `f"HITL request rejected by human: {reason}"`，无理由退回 `hook_result.message or "Execution blocked by HITL"`；data 含 `hitl_rejected=True` | `:362-378`、`:729-734` |
| timeout | 计数 +1、移除 promise、`HITLResolvedEvent(status="timeout")`；data 含 `hitl_timeout=True`、`hitl_degraded`、`promise_id`、`tool_call_id` | `:337-361`、`:735-763` |

超时消息两种：普通 `f"HITL approval timed out after {self._hitl_timeout}s with no response delivered; {attribution}"`（`:348-351`）；降级 `"...(degraded wait; approval channel likely offline after {limit}+ consecutive timeouts); ...; consider wrapping up and calling end_task instead of requesting more approvals"`（`:339-346`）。注释明确**超时不得折算成用户拒绝**（`:334-336`），这是 INV-11。

`HITLWaitResult`（`:59`）：`outcome`、`promise_id`、`tool_call_id`、`reason`（审批人给的 `{"reason": ...}`）、`degraded`、`degraded_timeout`。`_hitl_attribution`（`:624`）产出归因片段，`_extract_reject_reason`（`:644`）从审批回执里挑理由。

回执入口 `receive_hitl_response(ability_name, tool_call_id, approved, result=None) -> bool`（`:765`），构造 `HITLResult(approved, result)` 调 `resolve_future`。定位键是 `(agent_name, ability_name, tool_call_id)`。

已知竞态：`HITLRequestEvent` 在 future 创建**之前**发布（`:676-685` vs `:688-692`），审批人若在此窗口内立即回执，会命中一个尚不存在的 future（§18 D-9）。

### 15.5 默认开关

| 配置 | 默认 | 位置 |
| --- | --- | --- |
| `PermissionFlags.*`（manifest 字段） | 布尔全 `False`，列表全空 | `sa/loading/manifest_fields.py:60-71` |
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

### 15.6 装配期按字段挂 Hook

权限 Hook 不是无条件挂上的，而是装配时**按能力声明里出现了哪些字段**决定（`sa/execution/ability_build.py`）：

| 判定 | 函数 | 行号 |
| --- | --- | --- |
| 是否有 FS 权限字段（`require_hitl`/`allowed_paths`/`denied_paths`/`workspace_root`） | `_has_fs_permission_fields` | `:183` |
| 造 `FSPermissionChecker` | `_make_fs_permission_checker` | `:193` |
| 造 `CommandSafetyChecker`（含扩展名单） | `_make_command_approval` | `:232` |

未声明即无 Hook——这是零隐式行为的体现：一份不写权限字段的 `ability.yaml` 不会凭空获得审批，代价是"要不要审批"读不出来（§18 D-3），且 FS 的 pending 在未挂 Hook 时等于放行（§18 D-10）。

## 16. 装配：谁把这一切接起来

能力对象由两个装配点创建，**两者对 FS 的默认行为不同**（§18 D-2）。现状是两条并行路径同时活着（§18 D-21）：

| 路径 | 入口 | 状态 |
| --- | --- | --- |
| 旧路径（装配点 A） | `unit.py:448-544` `_create_ability_from_def` | 现行，Core 走这条 |
| 新路径（装配点 B） | `sa/execution/ability_build.py:58` `instantiate_resolved_abilities` | 已实现，被 Core 的清单装配引用（`unit.py:986-987`） |
| 资源切片消费者 | `slice/__init__.py:9-28` 等 | 现行，直接 import 本包的类型与扫描 API（§16.7） |

### 16.1 `CoreUnitConfig`

`@dataclass(frozen=True)`（`unit.py:116-197`），与本主题相关的字段：`workspace_root: str | None = None`（`:179`）、`auto_approve_abilities: tuple[str, ...] = ()`（`:180`）、`require_approval_by_default: bool = True`（`:181`）、`safe_extra_commands: tuple[str, ...] = ()`（`:182`）、`safe_extra_sub_commands: tuple[tuple[str, tuple[str, ...]], ...] = ()`（`:183`）、`hitl_timeout: float = 300.0`（`:178`）、`hitl_consecutive_timeout_limit: int = 0`（`:184`）、`hitl_degraded_timeout: float = 30.0`（`:185`）。

### 16.2 装配点 A：`_create_ability_from_def`（`unit.py:448-544`）

输入 `ability_def`（带 `ability_type` 与 `params`），按类型分派：

- FS（`ability_type in FS_ABILITY_TYPES` 且 params 命中 `{"require_hitl","allowed_paths","denied_paths","workspace_root"}`，`:491-493`）：弹出四个字段（`:494`），`require_approval` 决策 `auto_approved → False`，否则显式 `require_hitl`，否则 `require_approval_by_default`（`:499-504`）；构造 `FSPermissionChecker`（`:505-510`）；`require_approval` 为真时挂 `AccessApprovalHook`（`:516-517`）。
- `execute_command`（`:519-539`）：`require_approval` 决策同上（`:521-527`）；`CommandSafetyChecker`（`:528`）；有扩展白名单则 `extend_safe_lists`（`:530-535`）；**恒**挂 `CommandApprovalHook`（`:537`）。
- `end_task`（`:541-542`）：`params["mode"] = "toolcall"`。
- 最后 `create_ability(ability_type, **params)`（`:544`）——这里是旧路径与新路径唯一的交汇点：新旧都通过 `sa/loading/ability_catalog.py:519` 的 `create_ability` 造对象。

docstring（`:461-475`）给出的优先级：`auto_approve > manifest 显式声明 > 兜底默认`。

### 16.3 装配点 B：`instantiate_resolved_abilities`（`sa/execution/ability_build.py:58`）

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

兜底分支对通用 ability 的包装：`create_ability(handler)`（`:168`），若 `not auto_approved and bool(permissions.require_hitl)` 则包一层 `_HITLRequiredAbility(ability, ability_name=handler, reason=f"manifest requires HITL approval for '{handler}'")`（`:171-176`）。

失败语义：函数自身不抛异常；非 builtin 只跳过 + warning（docstring `:17-19` 显式对比独立 runner 的 `raise ValueError`）；`create_ability` / checker 的异常不捕获，直接上抛。

### 16.4 两条装配路径的差异

| 项 | 装配点 A | 装配点 B |
| --- | --- | --- |
| FS `require_approval` 兜底 | `require_approval_by_default`（`unit.py:504`） | 只用 `bool(permissions.require_hitl)`（`ability_build.py:219`），`require_approval_by_default` 被接收但**不读**（`:196`、`:141`） |
| command 兜底 | `require_approval_by_default`（`unit.py:527`） | `bool(require_hitl) or require_approval_by_default`（`:242`） |
| FS hook 挂载 | `require_approval` 为真才挂（`unit.py:516`） | 同样条件（`:147-148`） |
| command hook 挂载 | **恒**挂（`unit.py:537`） | **恒**挂（`:251`） |

后果：同一份清单在两条路径下可能一边是 HITL、一边是直接拒绝（`unit.py:504` 兜底 `True` vs `ability_build.py:219` 兜底 `False`）。`ability_build.py` 的 docstring（`:24-25`、`:71-72`、`:201`）声明了 FS 也会用 `require_approval_by_default`，与实现不符。

另一个顺序副作用：FS 判断在命令判断之前，一个带 `fs_*` 字段的 `execute_command` 会走 FS 分支并**丢失** `command_checker` / `command_runner`（§18 D-4）。

### 16.5 `HITLRequiredAbility` 与三个审批 hook 的分工

| 组件 | 管什么 | 位置 |
| --- | --- | --- |
| `FSPermissionChecker` + `AccessApprovalHook` | FS 路径白/黑名单与工作区 | `sa/permissions/fs_permissions.py:41`、`:234` |
| `CommandSafetyChecker` + `CommandApprovalHook` | 命令分级 | `sa/permissions/command_safety.py:244`、`:438` |
| `HITLRequiredAbility` + `HITLApprovalHook` | "声明要审批但能力自身不检查权限"的通用情形 | `sa/permissions/hitl.py:69`、`:40` |

- `HITLRequiredAbility`（`sa/permissions/hitl.py:69`）是**组合**不是继承：委托 inner（`:76-78`、`:84-85`），`get_hooks()` 把 `HITLApprovalHook` 追加到宿主 hooks 之后（`:87-88`），`__getattr__` 透传（`:99-100`）。
- `HITLApprovalHook`（`:40`）：PRE_EXECUTE（`:50`），`should_trigger` 只在 `context.current_ability_name == self._ability_name` 时为真（`:56-57`），消息 `f"{reason} — ability='{name}' args={summary}"`（`:64-66`，`summary = str(tool_args)[:200]`）。

### 16.6 `mcp_runtime` 的接线（已落地）

`mcp` 容器只有一个成员 `mcp_call`，它通过提供者槽拿运行时对象，而不是全局单例：

| 环节 | 代码 | 说明 |
| --- | --- | --- |
| 定义面声明 | `sa/ability/mcp/ability.yaml`（78 行） | `provider_requirements.mcp_runtime = {contract: "ghrah.mcp.v1", required_capabilities: ["request"]}`；`operations.mcp_call.implementation = {provider_slot: "mcp_runtime", operation: "mcp_call"}`；`effects: ["net:request"]`；`execution = {mode: request, timeout_ms: 300000, cancellation: unsupported, on_revoke: drain, retry: never}` |
| 服务键 | `sa/execution/execution_services.py:86` | `MCP_RUNTIME = ServiceKey[Any]("mcp_runtime")` |
| 注入 | `sa/execution/context.py:68`（构造参数）、`:94-95`（`if mcp_runtime is not None: services.set(...)`） | `None` 表示未接线 |
| 取用 | `sa/ability/mcp/operations/mcp_call.py:185` | `runtime = context.mcp_runtime` |
| 未接线行为 | 同上 `:191` | 明确返回失败「McpRuntime not wired — MCP calling is unavailable in this ...」，**不静默降级** |
| 透传 | `sa/execution/executor.py:566`、`:577` | 从 `getattr(supervisor, "mcp_runtime", None)` 取并传进上下文 |

`cancellation: unsupported` 与 `retry: never` 是诚实声明，不是占位：执行器确实没有能力级取消与重试（§18 D-1），MCP 调用也不可能重放。

> 端到端仍有缺口：**没有人往 `supervisor.mcp_runtime` 上设值**——`activate()` 在 `ghrah-core/src/` 内零调用点。所以这张表全绿，但模型实际调 `mcp_call` 会拿到"未接线"失败。缺的是上游装配，不是本包（§18 D-21 与 §19.4）。

### 16.7 资源切片消费者（`slice/`）

`ghrah-core/src/ghrah/ability/` 是一个独立的资源切片，直接 import 本包：

| 文件 | 引入 | 用途 |
| --- | --- | --- |
| `slice/__init__.py:9-28` | `execution.base.Ability`（别名 `Operation`）、`ability_roots`、`ability_scan`、`ability_schema`、`catalog.scan_catalog`、`skill_provider.SkillAbilityProvider` | 切片门面 |
| `slice/file_backend.py:9-11` | 同上若干 | 文件类能力的后端 |
| `slice/file_members.py:7-9` | 同上 | 成员表 |
| `slice/source.py:13-14` | 扫描 API | 源目录抽象 |
| `slice/versions.py:13-15` | 扫描 API | 版本与指纹 |

这是本包**当前唯一活跃的生产消费者**，比 Core 的装配更干净：它只用公开导出、不碰旧树。

## 17. 不变式

可被测试直接引用的不变量。前 12 条来自能力契约，后 4 条属于包自身结构。

| 编号 | 不变式 | 校验点 |
| --- | --- | --- |
| INV-01 | 容器名 == 目录名 == `identity.name or identity.namespace` | `sa/loading/ability_scan.py:505-516` |
| INV-02 | `implementation.operation == 成员键` | `sa/loading/ability_schema.py:127-132` |
| INV-03 | `implementation.provider_slot` ∈ `provider_requirements` | `sa/loading/ability_schema.py:135-140` |
| INV-04 | 一个容器至多一个 skill 成员 | `sa/loading/ability_schema.py:136-142` |
| INV-05 | 每个 `operations/<key>.py` 恰好一个 `Ability` 子类，且 `__module__` 匹配 | `sa/loading/ability_catalog.py:190-201` |
| INV-06 | 每个实现脚本有 `PERMISSIONS`/`HOOKS`/`SUMMARY`/`TAGS` 四件套 | `sa/loading/ability_catalog.py:363-374` |
| INV-07 | 工具名 = 成员键；`expose: false` 不进投影 | `sa/loading/tool_list.py:128-131` |
| INV-08 | 内置根 rank 0 且不可写，用户根 rank 100，同名内置胜 | `sa/loading/ability_roots.py:52-66`、`sa/loading/ability_scan.py:554-579` |
| INV-09 | 所有查询无缓存，改盘立即生效 | §3.2 各 API |
| INV-10 | 权限拒绝不抛异常，一律 `ActionResult(FAILURE, {"error": …})` | `sa/errors.py:11`（只有两个异常类）、`sa/execution/executor.py:382-387` |
| INV-11 | 超时不得折算为用户拒绝 | `sa/execution/executor.py:334-336` |
| INV-12 | 取消（非 `Exception` 的 `BaseException`）必须原样重抛 | `sa/execution/executor.py:507-511` |
| INV-13 | 顶层导出只经 `_MODULE_OF`；`__init__` 不 import 子模块 | `sa/__init__.py:214-248` |
| INV-14 | 分层方向符合 L-1~L-4 | §4.4（L-1 有已知缝） |
| INV-15 | 定义面不承载 `permissions` / `hooks` | `sa/loading/ability_schema.py:11-12` |
| INV-16 | `skill_provider` 与 `mcp_runtime` 只读，赋值抛 `AttributeError` | `sa/execution/context.py:165-173` |

## 18. 已知偏差与技术债

先写代码现状，再单列编号。**D-1 ~ D-20 是能力契约层面的**，**D-21 ~ D-26 是包结构层面的**。

| 编号 | 偏差 | 证据 | 影响 |
| --- | --- | --- | --- |
| D-1 | `execution.timeout_ms` 不生效；能力执行无超时 | `sa/loading/ability_schema.py:188` vs `sa/execution/executor.py:400` | 能力挂住会挂住整批 gather |
| D-2 | 两条装配路径 FS 默认不同 | `unit.py:504` vs `sa/execution/ability_build.py:219` | 同清单行为可能不同 |
| D-3 | 权限与 hooks 不在定义面 | `sa/loading/ability_schema.py:11-12` | 判断"要不要审批"必须读实现脚本 |
| D-4 | FS 判断先于命令判断 | `sa/execution/ability_build.py:135` vs `:151` | 带 `fs_*` 字段的 `execute_command` 丢失命令安全层 |
| D-5 | `ExecutionFrame` 与 `AbilityExecutionContext` 双模型 | `sa/execution/execution_frame.py:22-29` | 字段漂移风险 |
| D-6 | 未知 ability 结果先 append | `sa/execution/executor.py:462-480` vs `:506-525` | 结果顺序可能与 `tool_calls` 不一致 |
| D-7 | PRE_EXECUTE `modified_context` 写两次 | `sa/execution/hook_runner.py:56-57`、`sa/execution/executor.py:395-397` | 幂等但两个写入点 |
| D-8 | `parse_command` 把带值选项实参当子命令 | `sa/permissions/command_safety.py:424-433` | `git -C repo status` 误判为需审批 |
| D-9 | `HITLRequestEvent` 早于 future 创建 | `sa/execution/executor.py:676-685` vs `:688-692` | 极快回执可能丢失 |
| D-10 | `pending` 在能力内联检查中等同放行 | `sa/ability/fs/operations/read_file.py:182-188`、`write_file.py:173-179` | 未挂 hook 时越权静默通过 |
| D-11 | `move_file` 只审 destination | `sa/permissions/fs_permissions.py:294-299` | 白名单外 source 不触发审批 |
| D-12 | 非 `"."` 的相对 `allowed_paths` 按进程 CWD 解析 | `sa/paths.py:142` + `sa/permissions/fs_permissions.py:87` | 与"绝不泄漏 $PWD"承诺不一致 |
| D-13 | POST_EXECUTE 返回值被丢弃 | `sa/execution/executor.py:409-411` | post hook 的 `stop()`/`route_to` 无效 |
| D-14 | `bind_tool()` 与 `ability.yaml` 双份 schema | `sa/ability/fs/operations/read_file.py:124-140` vs `fs/ability.yaml:30-51` | 19 个可比成员中 18 个有差异（5 处语义）；以实现面为准，同步无校验、无告警 |
| D-15 | `DataKey.value_type` / `ServiceKey.service_type` 无运行时校验 | `sa/execution/execution_data.py:31-32`、`sa/execution/execution_services.py:35-36` | 类型错误延迟到使用点 |
| D-16 | `FINAL_RESPONSE` 实际键名是 `response` | `sa/execution/execution_data.py:86` | 按常量名猜键会读不到 |
| D-17 | `HookListView.__len__` 统计所有 owner | `sa/execution/hook_store.py:36-37` | 多 owner 下长度偏大 |
| D-18 | `catalog.py:104` 声明 `AbilityDefinitionError`，实际抛 `ManifestValidationError` | `sa/loading/catalog.py:104` vs `sa/loading/ability_catalog.py:126` | 异常捕获可能漏 |
| D-19 | `CLUSTER_ABILITY_TYPES` 无消费者（`FS_ABILITY_TYPES` 也有一份逐字相同的旧树副本，旧树那份才是 Core 实际在用的） | `sa/kinds.py:18`、`:31` 与 `ghrah/abilities/__init__.py:116`、`:133` | 死常量；"单一真源"的自述名不副实（§20 说明为什么保留） |
| D-20 | `extract_paths` 生产代码无消费者 | `sa/paths.py:58` | 规格表与运行时判定已解耦 |
| D-21 | **Core 装配未迁到本包**：`unit.py` 仍 import `ghrah.abilities`/`ghrah.manifest`（`:68,477,577-579,986-987,1393`），而本包当前只被 `slice/` 消费 | `unit.py:68` 等 | 本包的 `AbilityExecutionContext` 与旧 `ghrah.abilities.context` 同名类并存，`HITLRequiredAbility` 也有两份；跨消费者做类型标注会踩"两个同名类不是同一个类" |
| D-22 | 本包**反向依赖旧树**两处：`sa/agent_manifest.py:9` → `ghrah.manifest.agent`、`sa/loading/manifest_fields.py:10,17` → `ghrah.manifest.errors`/`types` | 同左 | 旧 `ghrah/manifest/` 不能删；Agent 清单 schema 在本包里只做转出 |
| D-23 | `loading ↔ execution` 双向缝：`ability_catalog.py:33` import `execution.base.Ability`，`ability_build.py:36` import `loading.ability_catalog.create_ability` | 同左 | 两个子包无法独立演化；建议把 `Ability` 基类提到顶层（如 `sa/base.py`）以消掉 L-1 的例外 |
| D-24 | `load_tool_list()` 无生产调用点 | `sa/loading/tool_list.py:107`；调用点只在测试与仓外的自检脚本里 | "工具面 20"是投影口径，真实工具面由 `bind_tool()` 决定（§11.3）；两者可能不一致 |
| D-25 | 文档/代码不一致：曾提到的 `sa/loading/skill_mcp.py` 在仓内不存在 | 实测 `ghrah-core/src/` 下 `**/*mcp*` 只命中 `sa/ability/mcp/operations/mcp_call.py` | 看旧描述找 MCP 声明解析会扑空；MCP 代码全在 `ghrah.mcp` 包（§16.6） |
| D-26 | `tests/scheme_ability/` 没有针对 `executor.py` 并行/取消路径的直接用例 | `ghrah-core/tests/scheme_ability/` 文件清单（§19.1） | `asyncio.gather` + `BaseException` 重抛分支靠上层集成用例间接覆盖；是测试形状缺口，不是行为缺失 |

D-19 与 D-20 的处理约定：**保留常量与函数，不删**。`CLUSTER_ABILITY_TYPES` 是给驱动循环/router 预留的分类真源，`extract_paths` 是 `AbilityPathSpec` 的另一半（规格在表里、判定在 `_resolve_paths`）。删掉会让"规格与判定同源"这条设计失去一处落点；保留的代价只是读者要多问一句"谁在用"。

## 19. 测试形态

### 19.1 文件清单

`ghrah-core/tests/scheme_ability/` 共 11 个文件：

| 文件 | 覆盖 |
| --- | --- |
| `loading/test_ability_scan.py` | 目录面扫描、成员装配、issue 分类 |
| `loading/test_ability_catalog.py` | 运行时投影、handler→类、四件套校验 |
| `loading/test_ability_roots.py` | 根的 priority/可写性/`ensure_roots` |
| `loading/test_ability_containers.py` | 5 个容器与成员清单 |
| `loading/test_skill_context.py` | 技能目录渲染与 `SKILL.md` 读取 |
| `loading/test_skill_lifecycle.py` | 指纹、lock、漂移判定 |
| `loading/test_tool_list.py` | 工具面投影、命名、去重 |
| `test_package_facade.py` | 163 条导出契约（§6.3） |
| `test_agentconf_compatibility.py` | agentconf 侧兼容 |
| `test_source_integration.py` | `slice/` 与本包的集成 |

实际数量：**289 passed / 2.20s**（`91f8220`；`a5beb74` 落地时记 292，合并 `main` 后有增减）。

### 19.2 断言口径怎么选

| 想断言的语义 | 用哪个 API | 不要用 |
| --- | --- | --- |
| "内置树实现了多少个成员" | `builtin_handlers()`（22） | `scan_abilities()` 的长度 |
| "模型能看见多少工具（投影口径）" | `load_tool_list()`（20） | `builtin_handlers()` |
| "有多少个运行时视图" | `builtin_full_names()`（21） | 工具面数字 |
| "磁盘上扫到几个条目" | `scan_abilities()`（17，含 12 个技能包） | 当成能力数 |

`scan_abilities()` 的 17 里有 12 个是技能包目录（0 个 operation 的 skill-only 容器），把它当"能力数量"会同时错两个方向。

### 19.3 覆盖缺口

执行器的两条分支没有直接用例：`asyncio.gather(..., return_exceptions=True)` 的乱序 append（D-6）与 `BaseException` 原样重抛（INV-12）。两者都由上层集成用例间接覆盖；要直接测，需要构造"一个成员阻塞、一个成员抛 `CancelledError`"的假成员（§18 D-26）。

## 20. 改动指南

| 你要改什么 | 改哪里 | 同步什么 |
| --- | --- | --- |
| `ability.yaml` 顶层字段或校验 | `sa/loading/ability_schema.py` | 本文 §7、§17 的 INV-02/03/04 |
| 新增/删除内置成员 | `sa/ability/<容器>/operations/` + 该容器 `ability.yaml` 的 `operations` | **四处一起改**：本文 §1.3 的三个数字、§5.5 成员表、§10.7 三口径表、§19.2 断言口径；实现脚本必须同时写 `bind_tool()` 与四件套常量 |
| 新增一个能力容器 | 新建 `sa/ability/<名>/` 目录，`identity.name` 必须等于目录名且只用 `[a-z0-9_]` | INV-01；§5.5 表 |
| 执行器或 Hook 行为 | `sa/execution/executor.py` / `hook_runner.py` | 本文 §13.4 时序表、§14.5、§17 的 INV-10/11/12 |
| 权限判定或命令分级 | `sa/permissions/*` | 本文 §15.2/§15.3 判定链表 |
| 新增公开符号 | 子模块内定义 + `sa/__init__.py` 的 `_MODULE_OF` | INV-13；`test_package_facade.py` |
| 新增一个服务键或数据键 | `sa/execution/execution_services.py` / `execution_data.py` | 本文 §13.3 两张表；注意 `FINAL_RESPONSE` 的存储名是 `response` |
| 把 Core 迁到本包 | `unit.py` 的装配点 A | 删掉 D-21、D-2；本文 §16 两张路径表并成一张 |

**不要做的事**：不要给查询 API 加 `lru_cache`（§3.2）；不要让 `loading/` 依赖具体下游包（§4.3）；不要在 `__init__.py` 里 import 子模块（§6.2）；不要把权限判定搬进执行器（§15.1）。

## 21. 旧概念对照

早期设计稿里出现过的概念，现状逐条判定：

| 旧概念 | 现状 | 当前对应物 |
| --- | --- | --- |
| `AbilityRegistry` 类 | **不存在** | 扫盘 + 路径约定：`sa/loading/ability_catalog.py:166`、`:496-536` |
| `AbilityRegistry.create/has/list_types/get_class/register/unregister/clear` | 前四个有替代，后三个**删除** | `create_ability` / `has_handler` / `builtin_handlers` / `ability_class` |
| `AbilityMount` / `MountTable` / `mount_id` | **不存在**（全仓 0 命中，仅设计稿里出现） | 无对应物：容器 + 目录池取代挂载表 |
| `LoadLockEntry` | **不存在** | `LockRecord` / `LockOutcome` / `SkillFingerprint`（§12） |
| `SeedSnapshot` | **不存在** | 无对应物 |
| `CatalogEntry` | 裸名**不存在** | `SkillCatalogEntry`（Protocol）/ `CatalogReport` |
| `AbilityDescriptor` | 不在 `ghrah-core` | Subject 侧概念，非本仓代码 |
| 能力 CRUD 指令面（P1 计划） | **未实现** | `list_abilities` 是只读工具（`sa/ability/core/operations/list_abilities.py`） |
| G1–G14 缺口清单 | 历史分类，不再适用 | 现以 §18 的 D-1…D-26 表达 |
| "注册表在 import 期快照" | **不成立** | 全部查询每次重新扫盘（§3.2） |
| 工具名 `namespace__op` 或 `container.op` | **错** | 工具名 = 成员键（§11.2） |
| `manifest: ability` 文本形态 | **遗留**，生产代码零使用 | `ability.yaml` + `schema_version: 0.1-draft`（§10.6） |

这些判定的共同点：凡是"需要一个中心登记处"的设计都被**目录约定**取代了——身份来自目录名与 `identity`，实现来自文件路径，可见性来自定义面字段，投影每次重算。代价是每次调用付一次扫盘成本（§3.2），收益是没有第二份真相。