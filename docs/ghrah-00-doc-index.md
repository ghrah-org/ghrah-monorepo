# ghrah 文档索引

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-INDEX-000 |
| 文档状态 | 现行（authoritative，本目录的入口） |
| 校准基线 | `ghrah-monorepo` @ `f8ff157`（2026-10-28 19:18–19:22 重构后），`ghrah-core` 0.2.0a0 |
| 读者 | 所有人：先读这一页，再决定读哪份 |
| 上一次全量重写 | 2026-10-29 |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-29 | 首版。16 份旧文档重写为 7 份权威文档（外加本索引）；本仓 `docs/` 原有的 4 份 `2026-10-26-*` 移入 `archive/` |
| 2.0 | 2026-10-29 | 迁入 `ghrah-monorepo/docs/`；`ARCHITECTURE.md` §6.2/§6.6/§6.7/§6.8 按定义面口径改写并回链本套文档；`docs/2026-10-26-*` 四份迁入 `archive/` |

## 1. 这个目录是什么

`docs/` 是**本仓技术文档的唯一权威位置**，分两层：

- **平台层**：[ARCHITECTURE.md](ARCHITECTURE.md)——13 章 + 3 个附录，讲 6 个 Python 包、TS 侧、协议、上下文、插件、HITL、部署全景。**它是全仓的事实基线。**
- **能力子系统层**：本索引 + `ghrah-01` ~ `ghrah-07` 共 8 份——把 ARCHITECTURE §6 那一章展开到"改得动代码"的粒度：逐字段契约、开发手册、排障、历史判决。

三条硬约定：

1. **代码是事实，文档是索引。** 每份文档的关键论断后面都有 `file:line`；冲突时以代码为准，并回来改文档。
2. **基线必须写明。** 每份文档都有一行"校准基线（commit + 日期）"。看不到基线，就不该相信它的细节。
3. **不写承诺，只写现状。** 未实施的机制明确标"未实施"（例：`mount_id`、`ledger`、`GrantRecord`、信任闸），不用"将支持"的语气。想知道为什么没做，去 `ghrah-07`。

## 2. 文档地图

### 2.1 平台层

| 文档 | 覆盖 |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | §1 全景 / §2 仓库结构 / §3 协议 / §4 上下文 / §5 装配 / **§6 能力系统（骨架；细节见 2.2）** / §7 上下文系统 / §8 集群 / §9 Subject / §10 插件 / §11 TS 侧 / §12 启动部署 / §13 工程约束；附录 A 图索引、附录 B 术语、附录 C 文档偏差 |

`ARCHITECTURE.html` 是同名文档的**导出快照**（2026-10-27，早于 §6 改写），只作分享用，不作为依据。

### 2.2 能力子系统层

| 编号 | 文档 | 回答什么问题 | 什么时候读 |
| --- | --- | --- | --- |
| GHRAH-DESIGN-001 | [ghrah-01-ability-design-and-implementation.md](ghrah-01-ability-design-and-implementation.md) | 定义面/目录池/扫描/投影/工具面/执行/Hook/权限/HITL/装配的全量契约与不变式 | 第一次接触能力系统；改契约或改装配；评审"能力怎么被找到/被调用" |
| GHRAH-GUIDE-002 | [ghrah-02-ability-package-guide.md](ghrah-02-ability-package-guide.md) | 怎么新增一个成员：目录规则、`ability.yaml` 逐字段、实现模板、四件套常量、权限写法、测试点 | 要加能力/改能力行为/给能力加审批 |
| GHRAH-DESIGN-003 | [ghrah-03-drive-loop.md](ghrah-03-drive-loop.md) | 一次任务从收到到结束的八个阶段、一次 LLM 调用 + 工具执行、压缩、异常回滚、硬序约束 | 调 Agent 行为；排查"这轮为什么没执行/执行两次/上下文被截断" |
| GHRAH-DESIGN-004 | [ghrah-04-skill-system.md](ghrah-04-skill-system.md) | 技能（能力包的成员类型）、两套实现并存的成因、扫描根、`SKILL.md`、lock 与漂移、会话注入、`load_skill` | 写技能包；排查"技能没进模型上下文"；处理 lock 漂移 |
| GHRAH-OPS-005 | [ghrah-05-build-startup-and-troubleshooting.md](ghrah-05-build-startup-and-troubleshooting.md) | 13 步启动链、命令面、端口、日志落点、数据落盘、启动期硬失败原文、前端、排障手册 | 起服务；服务起不来；日志找不到；端口/宿主配置 |
| GHRAH-MAP-006 | [ghrah-06-code-map.md](ghrah-06-code-map.md) | 仓库结构与包边界、文件职责速查、一次能力调用的七步链、协议层与前端下发、依赖方向、新文件放哪 | 找文件；判断改动该落在哪个包；避免循环依赖 |
| GHRAH-ADR-007 | [ghrah-07-history-and-decisions.md](ghrah-07-history-and-decisions.md) | 旧件逐份判决、8 条关键决策（D-1~D-8）、5 条教训、未处理的文档债务 | 想知道"某概念为什么没了"；评审新设计前避免重走弯路 |

依赖关系：`02` 与 `04` 都以 `01` 的契约为前提；`06` 是 `01`/`03`/`04`/`05` 的坐标索引；`07` 与其余正交（只解释历史）。平台层与子系统层是**引用关系而非重复**：ARCHITECTURE §6 给骨架与口径，更细的 `file:line` 在子系统文档里；两者冲突时以子系统文档为准并回改 §6。

## 3. 按角色选读

| 你是谁 | 建议顺序 |
| --- | --- |
| 刚接手，要把系统跑起来 | `ghrah-05` §1–§2 → `ghrah-06` §2–§4 → `ARCHITECTURE.md` §1–§2 |
| 要新增一个工具（能力） | `ghrah-02` 全文 → `ghrah-01` §4–§9 → `ghrah-06` §8 |
| 要改执行/权限语义 | `ghrah-01` §9–§13 → `ghrah-03` §5 → `ghrah-07` §3（D-7：治理层缺席） |
| 要写技能包 | `ghrah-04` §3–§7 → `ghrah-02` §4.2（`skills` 字段） |
| 排查线上问题 | `ghrah-05` §8 排障手册 → `ghrah-05` §4 日志 → `ghrah-03` §8 异常与降级 |
| 做架构评审 | `ghrah-01` §13（不变式与已知偏差）→ `ghrah-06` §7（依赖方向）→ `ghrah-07` 全文 |
| 只想确认"某份旧文档还能不能信" | `ghrah-07` §2 逐份判定 → [archive/README.md](archive/README.md) |

## 4. 全目录共享的写作约定

| 约定 | 写法 | 反例 |
| --- | --- | --- |
| 证据引用 | `文件路径:行号`，路径相对仓库根（`scheme/loading/ability_schema.py:54`） | "在扫描器里" |
| 口径 | 出现"多少个能力"必须写明口径（handler 22 / 运行时视图 21 / 模型工具面 20），三者不可混用 | "共 20 个能力"（把工具面当能力总数） |
| 状态标记 | 已实现 / 未实施 / 已废弃 三态，不用"计划""将会" | "后续会支持 mount_id" |
| 偏差 | 代码与设计不一致时先写"代码现状"，再单列"已知偏差"并给编号（D-n） | 把偏差写成设计意图 |
| 基线 | 每份文档头部必须有 commit + 日期 | 只写"最新版" |

## 5. 维护规则：改动代码后要改哪份

| 你改了 | 必须同步 |
| --- | --- |
| `scheme/loading/ability_schema.py`（字段/校验） | `ghrah-01` §4、`ghrah-02` §4 |
| 新增/删除内置能力包 | `ghrah-01` §5.3 与 §7、`ghrah-02` §8、`ghrah-06` §3，**外加 `ARCHITECTURE.md` §6.6 与 §6.7 的容器表**——四处口径一起改 |
| 执行器或 Hook 行为 | `ghrah-01` §9–§10、`ghrah-03` §5 |
| 权限判定 / 命令分级 | `ghrah-01` §11、`ghrah-02` §6 |
| 驱动循环阶段或压缩策略 | `ghrah-03` §4、§7、§9 |
| 技能扫描 / lock / 注入 | `ghrah-04` §4–§9、`ARCHITECTURE.md` §6.8 |
| 启动脚本、端口、日志路径 | `ghrah-05` §1–§5 |
| 新增顶层包或改依赖方向 | `ghrah-06` §2、§7、`ARCHITECTURE.md` §2 |
| 推翻了某条既有决策 | `ghrah-07` §3 追加 `D-n` 并标"已否决"，**不要**直接删旧条目 |

## 6. 归档

**[archive/](archive/README.md)** 收录 `docs/` 原有的 4 份 `2026-10-26-*` scheme 系列文档。它们描述的是迁到 `ghrah/scheme/` 的**半程状态**（自称"20 份 yaml 并入 4 份容器"，而今天是 5 份容器），已被 `f8ff157` 这一轮重构再次推翻，**不要照着写代码**。逐份判决见 [archive/README.md](archive/README.md) §2。

本次重写所依据的另 **16 份** 2026-10-12 ~ 2026-10-28 旧稿（`能力系统建设设计方案.md`、`ability加载架构.md`、`skill-system-porting-guide.md` 等）**不随仓分发**：它们属于仓外私有资料库 `doc/archive/`，判定理由已全部吸收进 `ghrah-07` §2，回仓查证时只需读 `ghrah-07`。

## 7. 已知文档债务

以下问题**已知且未处理**，读文档时自行打折，明细见 `ghrah-07` §5：

- `ARCHITECTURE.md` 只有 §6 做过 10-28 重构后的逐行校准；§1–§5、§7–§13 仍是 2026-10-27 的状态，引用具体行号前回源码核对。`ARCHITECTURE.html` 更旧。
- `scheme/loading/ability_catalog.py:20` 的 docstring 提到不存在的 `load_all_builtin_manifests.cache_clear()`，与同文件 `:405`「无缓存」自相矛盾。
- 技能侧**两套实现并存**：`ghrah-core/src/ghrah/skills/` 与 `ghrah.scheme.loading` 各一套，`ghrah-subject` 的 `SkillUnit` 仍用旧的那套（写 schema=1 lock，Core 读 schema=2 时不认）。见 `ghrah-04` §3、§12。
- `ghrah-observer-webui` **没有**能力面板；任何"在 UI 里看能力列表"的说法都不成立。
- `ghrah-core/tests/` 下的 `abilities/`、`manifest/`、`scheme/`、`skills/` 四个目录名是遗留；前三者内容已改指 `ghrah.scheme`，`skills/` 仍在测旧的 `ghrah.skills` 包。