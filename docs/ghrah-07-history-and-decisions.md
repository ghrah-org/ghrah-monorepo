# 旧设计演进与决策记录

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-ADR-007 |
| 文档状态 | 现行（authoritative，只读存档性质） |
| 校准基线 | `ghrah-monorepo` @ `f8ff157`（2026-10-28 19:18–19:22 重构后），`ghrah-core` 0.2.0a0 |
| 读者 | 想知道"某个旧概念为什么没了""某条裁定为什么没落地"的维护者；评审新设计前需要避免重走弯路的人 |
| 范围 | 解释 16 份旧设计（其中 4 份原在本仓 `docs/`，另 12 份在仓外 `doc/archive/`）的去留，以及 2026-10-12 ~ 10-28 期间的关键取舍 |
| 相关文档 | [ghrah-00-doc-index.md](ghrah-00-doc-index.md)、[ghrah-01](ghrah-01-ability-design-and-implementation.md) §14（旧概念对照表）、[archive/README.md](archive/README.md)（本仓归档清单） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。基于 10 份旧文档的逐份只读复核 + git 历史，记录判定与可复用教训 |

## 1. 为什么要有这份文档

2026-10-28 19:18–19:22 的四次提交（`1938ffa` 退役 `abilities/manifest` 目录树、`fc032df` subject 改指 `ghrah.scheme`、`f89731f` 备份旧 manifest 解析栈，外加协议侧对齐）把 `docs/` 里十份文档描述的架构整体换掉了。旧文档因此出现三种状态：

1. **已被实现并超越**（设计成真，且代码走得更远）；
2. **被代码反向否决**（文档明确选了 A，代码选了 B）；
3. **从未实施**（治理/账本/信任层）。

三种状态对读者的价值完全不同：第一种可以读代码，第二种**必须**知道，否则会照着旧文档把已被否决的设计重新做一遍；第三种是 backlog，不是规范。本文件把三者分开，并把"为什么"记下来——结论可以变，理由不该丢。

## 2. 逐份判定

### 2.1 唯一被代码追上的设计：`2026-10-26-ability-scheme-draft.md`

**主旨**：以 `/scheme/ability/<alias>` 为落位、`ghrah.ability-definition` 为格式、`schema_version: 0.1-draft` 为版本锚，容器内 `skills/SKILL.md` + `ability.lock.yaml` 双文件。
**现状**：**全部落地**——`scheme/loading/ability_schema.py:54/63/66/283`、`scheme/ability/__init__.py:16-25`、5 个 `ability.yaml`（core/fs/shell/cluster/mcp）、22 个 `operations/*.py`。
**未实现部分**（读该文档时请注意）：`GrantRecord`（仅存于 `scheme/ability/__init__.py:31` 的 docstring）、`AbilityBinding`（近似物是 `ghrah-scheme/_state.py:28` 的 `Binding`）、`boundary/grant/revoke`（只有 `Authority`/`DenyAuthority`，`authority.py:10-22`）、`invoke` 改名 `submit`、`resolve` 改名 `acquire`。

### 2.2 被代码反向否决的两条

| 旧主张 | 出处 | 代码的选择 |
| --- | --- | --- |
| 「工具面不放每能力一命名工具」 | `能力系统建设设计方案.md` §5.1 | `scheme/loading/tool_list.py:23` 明写"工具名取成员键（`read_file`）"，仍是每能力一命名工具 |
| G13 裁定"快照不重算"（加载后封印） | `2026-10-13-ability-gaps-blocking-triage.md` | `scheme/loading/tool_list.py:30` 明写"每次调用都重新扫盘（无缓存）"，选的是现值而非快照 |

第二条尤其要注意：**裁定文档的判决被反向执行，而裁定文档没有更新**。这是本项目最贵的一类文档债务——读者会以为裁定仍然有效。

### 2.3 完全废弃（全仓 0 命中）

| 旧文档 | 主张 | 取代物 |
| --- | --- | --- |
| `02-能力系统.md` | `AbilityMount`、`ability_mount/update/grant/revoke` 指令面、`BOUNDARY_TRANSITION`、`effective_at_iteration` 封印 | `ghrah.scheme` + `ghrah-scheme/runtime.py:810-865` 的 catalog/describe/acquire/submit/observe/cancel/release/close |
| `2026-10-13-ability-system-object-fields-design.md` | `AbilityDescriptor`/`CatalogEntry`/`AbilityMount`/`MountTable`/`SeedSnapshot`/`BoundaryTransitionEvent`/`TRUSTED_ROOT` | `AbilityDefinition`/`AbilityIdentity`/`OperationDefinition`；`AccessRequest`/`Authorization`/`Admission`（`ghrah-scheme/models.py:81-110`） |
| `2026-10-13-ability-terminology-and-structure-rulings.md` | B6 加载/挂载消歧、B7 三环、B8 工具名冲突 fail-closed、B9 验收判据制 | 术语压成 catalog/describe/acquire/submit/release；"挂载"一词在代码里只剩 subject 侧 `mount_unit` 桥（`core/unit.py:10/227/558`） |
| `ability加载架构.md` | A/B/C 三清单 + M1–M5 物化器 + L1–L6 装载路径 + "C(Registry) 才是可实例化权威" | `scheme/loading/ability_roots.py` 目录池（内置树 rank 0 + `~/.ghrah/skill-ability` rank 100，同名容器 rank 小者胜） |

### 2.4 部分实现

| 旧文档 | 落地 | 未落地 / 改名 |
| --- | --- | --- |
| `2026-10-12-ability-command-plane-p1.md` | 独立列举命令确实加了 | 名字不是 `ABILITY_LIST`，而是 `enums.py:115` 的 `MANIFEST_LIST_ABILITIES` + `payloads/manifest.py:16`（落在 manifest 域）；"不加 wire caller 字段"被采纳但未用 |
| `ability架构适配设计文档.md`（73 行骨架） | `SKILL.md` + `ability.lock.yaml` 双文件（`skill_lock.py:61`） | `scripts/` 被 `operations/<key>.py` 取代；`coreutils` 拆包未做；作者标注"临时思考、落地时删除"的 DSH 插件包条目已删 |
| `能力协议层调用依赖分析.md` | 三协议原文仍在（`core/ability_protocol.py:28-58`）；HITL 链移到 `scheme/execution/` | `RegistryProtocol` 实现者已退役＝死契约；`ExecutorProtocol` 实现者改名 `scheme/execution/executor.py` |
| `能力系统建设设计方案.md`（64KB，目标态 SSOT） | catalog/describe、skill 包、lock、script 形态 | `ledger.py`、`MountTable`、LOAD/UNLOAD、`protected` 信任根、`bind_strategy`、`TRUSTED_ROOT` 评级、衰减律、委托租约；`command_proxy` 已退役 |

## 3. 跨文档的关键决策（ADR 摘要）

**D-1 定义面用目录，不用注册表。** `ability.yaml` 就在 `scheme/ability/<容器>/` 里，扫描器读目录；`AbilityRegistry` 类已删，`RegistryProtocol` 是无人实现的死协议（`core/ability_protocol.py:28-58`）。理由：少一个必须与文件同步的中央表，装配期零注册代码。

**D-2 身份是字符串键，不是对象。** 容器身份 = `identity.name or namespace`；成员身份 = 成员键；`_abilities: dict[str, AbilityProtocol]`（`agents/base.py:177`，写入 `:243`）仍是旧设计的遗留物。多份旧文档反复点名要改（"名字即身份"），至今未改——这是当前**最大的设计债**，也是新设计必须先回答的问题。

**D-3 加载面不缓存。** 每次调用重新扫盘（`tool_list.py:30`），换来"改文件即生效"，代价是每次工具面构造都要 stat 一遍池。否决了快照/封印方案（见 §2.2）。

**D-4 lock 用双 schema 并存，不做静默迁移。** `schema=1`（旧，`skills/lock.py`）与 `schema=2`（新，`scheme/loading/skill_lock.py`）并存；读到 `schema=1` 直接报 `lock schema='1' 与当前 2 不符（需显式重建，不自动改写）`（`skill_lock.py:189-193`），拒绝半可信摘要。理由：宁可让漂移"无法裁决"（`content_drifted=None`），也不要算出假结论。

**D-5 单一写路径 + 派生副本可重建。** Core 的 sqlite 是唯一权威写侧，Subject 的 `ActionChainLedger` 是只读投影（同文件 WAL 双连接），Observer store 由 `core:*` 事件驱动、重连时经 `get_chain_history` 重建（`AGENTS.md`）。任何"第二写路径"都是 bug，不是优化。

**D-6 契约以 `ghrah-protocol` 为唯一权威。** TS 侧 `packages/protocol` 对齐；能力定义只在 Python 侧，不上 wire。所以"能力列表"命令落在 manifest 域（见 §2.4），而不是造一个新的 ability 域。

**D-7 治理层整体缺席。** `mount_id`、`MountTable`、`ledger`、`GrantRecord`、`protected`、信任闸（旧文档设四道，实际只有闸 1/闸 4）、衰减律、委托租约——全部未实施。现在的权限只有单轴 `auto_approve_abilities`（`scheme/execution/ability_build.py:78`）加逐能力的 `PERMISSIONS` 字典。**不要**在评审里假设这些机制存在。

**D-8 文档不引用 `plans/`。** `plans/` 被 `.gitignore` 整体忽略，是过程产物；设计文档一律进 `docs/`，代码里禁止引用 plan 的节点/标题/锚点（`AGENTS.md`）。

## 4. 可复用的教训

1. **裁定要带状态位。** 任何"决定"都必须紧跟一行"已落地 / 被否决 / 未实施"并附证据（file:line 或提交号）。本项目所有歧义都来自"决定写了，状态没写"。
2. **重构时先改文档还是先改代码，要显式选一个。** `1938ffa` 系列先改代码，导致 10 份文档同时失效；代价是后来者必须逐份考古（本文件就是产物）。
3. **最新 ≠ 准确。** `docs/ARCHITECTURE.md`（2026-10-27 写）在 10-28 重构后仍逐行描述 `abilities/__init__.py:153-174`、`AbilityRegistry`、22 个 builtins——它比 `docs/` 里的旧件更新，却同样被重构甩下；直到 2026-10-29 才把 §6.2 / §6.6 / §6.7 / §6.8 改写成定义面口径。**判断一份文档是否可信，看它的基线提交，不看它的修改时间。**
4. **测试目录名会骗人。** `tests/` 下仍有 `abilities/`、`manifest/`、`scheme/`、`skills/` 四个目录名，内容大多已改指 `ghrah.scheme`（如 `tests/abilities/test_abilities.py`），但 `tests/skills/` **仍在测旧的 `ghrah.skills` 包**——与 `ghrah-subject` 的 `SkillUnit` 同源。找测试按内容找，别按目录名推断被测对象。
5. **归档不是删除。** 旧件的价值在于"证明我们想过什么、否决过什么"，所以进 `docs/archive/` 并在 `docs/archive/README.md` 标注取代关系，不直接删。

## 5. 已知文档债务（未处理，供后续排期）

| 债务 | 位置 | 影响 |
| --- | --- | --- |
| `ARCHITECTURE.md` 其余章节未逐行复核 | `ghrah-monorepo/docs/ARCHITECTURE.md`（§6 已于 2026-10-29 按定义面口径改写） | §1–§5、§7–§13 未做 10-28 重构后的逐行校准，引用具体行号前请回源码核对 |
| `ability_catalog.py:20` docstring 提到不存在的 `load_all_builtin_manifests.cache_clear()` | `scheme/loading/ability_catalog.py:20` 与 `:405` 自相矛盾 | 照抄会写出错误注释 |
| 能力面板缺失 | `ghrah-observer-webui` 无 abilities 面板（命中仅在 node_modules） | 文档不得声称有 UI 能力视图 |
| 测试目录名与内容不一致 | `ghrah-core/tests/{abilities,manifest,scheme,skills}/` | 见 §4.4 |