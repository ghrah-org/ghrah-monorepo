# ghrah 文档索引

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-INDEX-000 |
| 文档状态 | 现行（authoritative，本目录的入口） |
| 校准基线 | 端口索引按 `a742fcf`（2026-11-23）重新校准；能力/成员数据底座按同一提交校准 |
| 读者 | 所有人：先读这一页，再决定读哪份 |
| 上一次全量重写 | 2026-11-23（`ARCHITECTURE.md` 全面重写并重新生成 HTML；新增 `ghrah-13`） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-29 | 首版。16 份旧文档重写为 7 份权威文档（外加本索引）；本仓 `docs/` 原有的 4 份 `2026-10-26-*` 移入 `archive/` |
| 2.0 | 2026-10-29 | 迁入 `ghrah-monorepo/docs/`；`ARCHITECTURE.md` §6.2/§6.6/§6.7/§6.8 按定义面口径改写并回链本套文档；`docs/2026-10-26-*` 四份迁入 `archive/` |
| 2.1 | 2026-10-29 | 接入 `ghrah-scheme` 第一代包文档：系统模型、资源与权限、运行时 API、Ability API、开发方法和实现状态；新增文档以 `acf06fe` 与当日工作区单独校准 |
| 2.2 | 2026-11-12 | 新增 `ghrah-08-interface-port.md`：Core 侧 50 项能力已实现，补齐 Core ↔ Subject 的调用边界与按功能点时序图，并登记 `ARCHITECTURE.md` §4.4 / §7 / §10 的 `ActionChainLedger` 直读描述已过期 |
| 2.3 | 2026-11-13 | `ghrah-08` 全量重写为「模块能力端口设计说明」（自闭环、50 条能力逐条列出、作用域/装配/错误/闸门/偏差）；`ARCHITECTURE.md` 修正 `ActionChainLedger` 直读相关九处（§2.1 总览图、§7 读写分离图与 `SqliteBackend` 只读开关、§9.3 时序图、§10.1 权威图、§10.2/§10.3/§10.5 表、§13 跨机边界行），统一改为经 `CorePort` 调用；`ghrah-05`/`ghrah-06`/`ghrah-07` 同步该口径 |
| 2.4 | 2026-11-13 | `ARCHITECTURE.md` **全量重写**为 14 章 + 2 附录（2954 行，逐章重新从代码取证，2310 处 `file:line`），新增 §4「模块能力端口」独立成章、§14 记录 12 条已核实偏离；`ARCHITECTURE.html` 改为由 Markdown 生成（`scripts/gen_architecture_html.py`）；`ghrah-06` §5.2 工具面口径改为 `ToolSurfaceRuntime`，并修正 `ghrah-protocol` 依赖边描述 |
| 2.5 | 2026-11-15 | 将端口接入和维护指引改指向 `ARCHITECTURE.md` §4，将 `ghrah-08` 标记为旧端口模型的历史参考 |
| 2.6 | 2026-11-19 | 新增 `ghrah-09`（Agent 模板管理设计）与 `ghrah-10`（Agent 实例化设计）：按「模板库 → 装配输入 → 实例快照」现行实现逐功能重写，含数据建模、接口入参出参、时序图与类图；功能设计口径，不写实现细节 |
| 2.7 | 2026-11-19 | 新增两份范式声明：`ghrah-11`（member 定义规范：定位、五层数据格式、命名归一、两轴语义、控制成员、不变式）与 `ghrah-12`（ability 包结构与定义规范：内核件/能力件/MCP 三类目录、lock 结构、构建-安装-装载职责、可移植性规则） |
| 2.8 | 2026-11-20 | member 两轴改为 `visible` / `bind`（绑定要求可见）；`ghrah-11` §5 重写并补运行期绑定与 wire 过渡说明；`ghrah-10` 增 `initial_bindings` 创建参数与 `INST-11` 绑定/解绑 |
| 2.9 | 2026-11-20 | 启动按成员类型分流注入：operation 进工具面静态集、skill 进每轮目录（均按 `bind` 过滤）、控制成员走 control 通道；`bind_member` 支持技能目录名；`ghrah-11` §5 补技能分流 |
| 3.0 | 2026-11-23 | 模板成员改为 `available_members`（三段全名，只存身份六字段）；创建线补齐成员定义数据写入实例快照；技能目录注入 `read` 地址、`load_skill` 接受裸键/地址；新增 `ghrah-13`（能力与成员数据底座：SQLite 权威、启动提交、快照恢复、迁移）；`ARCHITECTURE.md` 全面重写并重新生成 HTML，删除 Scheme 树权威等旧特性设计 |
| 3.1 | 2026-11-23 | 成员两轴解耦：`visible`（可发现）与 `bind`（常驻上下文）相互独立，`visible=false, bind=true` 合法；内置 `search_tools` 在实例创建/恢复时固定为该组合（常驻但不进自身发现面）；`search_tools` 改为扫描实例快照 `axes`，返回 `{status,data,reason}`；删除 `ghrah.core.list_abilities`（发现面由 `search_tools` 与技能目录承担） |

## 1. 这个目录是什么

`docs/` 是本仓技术文档的总入口，平台与能力子系统说明集中于此；包级手册可由本索引链接到包内文档。当前分三层：

- **平台层**：[ARCHITECTURE.md](ARCHITECTURE.md)——16 章 + 2 个附录，讲 7 个 Python 包、TS 侧、协议、接口化端口、上下文、能力与数据底座、模板与实例化、插件、HITL、部署全景。**它是全仓的事实基线。**
- **能力子系统层**：本索引 + `ghrah-01` ~ `ghrah-08` 共 9 份——把 ARCHITECTURE §6 那一章展开到"改得动代码"的粒度：逐字段契约、开发手册、排障、历史判决、跨模块调用边界。
- **Scheme 包文档层**：[ghrah-scheme](../ghrah-scheme/README.md) 及其六篇基础技术文档和文件资源切片指南——说明资源树、权限关系、当前控制平面 API，以及逻辑／Scheme／UI 并行开发方法。目标模型与现状明确分开，不将新设计冒充已完成迁移。

三条硬约定：

1. **代码是事实，文档是索引。** 每份文档的关键论断后面都有 `file:line`；冲突时以代码为准，并回来改文档。
2. **基线必须写明。** 每份文档都有一行"校准基线（commit + 日期）"。看不到基线，就不该相信它的细节。
3. **不写承诺，只写现状。** 未实施的机制明确标"未实施"（例：`mount_id`、`ledger`、`GrantRecord`、信任闸），不用"将支持"的语气。想知道为什么没做，去 `ghrah-07`。

## 2. 文档地图

### 2.1 平台层

| 文档 | 覆盖 |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | §1 定位与五条原则 / §2 仓库结构与包边界 / §3 协议层（端口契约 + wire 契约）/ **§4 模块能力端口** / §5 Subject 运行时 / §6 Core 认知半区 / §7 上下文系统 / §8 能力系统 / §9 业务域 / §10 Observer 观察端 / §11 一致性与 CQRS / §12 恢复与弹性 / §13 配置启动与运维 / §14 已知偏离与技术债；附录 A 术语表、附录 B 按角色选读 |

`ARCHITECTURE.html` 是由 `ARCHITECTURE.md` 生成的单文件分享页（`uv run python scripts/gen_architecture_html.py`），带侧边目录、搜索与移动端折叠。**内容以 Markdown 为准**；改文档只改 `.md`，然后重跑生成器，不要手改 HTML。

### 2.2 能力子系统层

| 编号 | 文档 | 回答什么问题 | 什么时候读 |
| --- | --- | --- | --- |
| GHRAH-DESIGN-001 | [ghrah-01-ability-design-and-implementation.md](ghrah-01-ability-design-and-implementation.md) | 定义面/目录池/扫描/投影/工具面/执行/Hook/权限/HITL/装配的全量契约与不变式 | 第一次接触能力系统；改契约或改装配；评审"能力怎么被找到/被调用" |
| GHRAH-GUIDE-002 | [ghrah-02-ability-package-guide.md](ghrah-02-ability-package-guide.md) | **历史**：旧格式作者指南（`0.1-draft` + 包维度）。头部「现状速查」给出当前可执行入口 | 仅在追溯旧写法时读；新作者读 `ghrah-11`/`ghrah-12`/`ghrah-13` 与 ability-builder 技能 |
| GHRAH-DESIGN-003 | [ghrah-03-drive-loop.md](ghrah-03-drive-loop.md) | 一次任务从收到到结束的八个阶段、一次 LLM 调用 + 工具执行、压缩、异常回滚、硬序约束 | 调 Agent 行为；排查"这轮为什么没执行/执行两次/上下文被截断" |
| GHRAH-DESIGN-004 | [ghrah-04-skill-system.md](ghrah-04-skill-system.md) | 技能成员（定义/构建/安装/启动提交/每轮目录注入）、`SKILL.md` frontmatter、目录 `read` 地址、`load_skill` 三种写法 | 写技能包；排查"技能没进模型上下文"；技能正文读不到 |
| GHRAH-OPS-005 | [ghrah-05-build-startup-and-troubleshooting.md](ghrah-05-build-startup-and-troubleshooting.md) | `ghrah` CLI 命令面、实例布局、启动链、数据落盘（三库）、日志、启动期硬失败原文、排障手册 | 起服务；服务起不来；日志找不到；数据在哪个库 |
| GHRAH-MAP-006 | [ghrah-06-code-map.md](ghrah-06-code-map.md) | 仓库结构与包边界、按层的能力地图、启动与调用两段链、协议层与前端下发、依赖方向、新文件放哪 | 找文件；判断改动该落在哪个包；避免循环依赖 |
| GHRAH-ADR-007 | [ghrah-07-history-and-decisions.md](ghrah-07-history-and-decisions.md) | 旧件逐份判决、D-1~D-8 状态位、D-9~D-16 新裁定、二次推翻的教训、未处理的文档债务 | 想知道"某概念为什么没了"；评审新设计前避免重走弯路 |
| GHRAH-DESIGN-008 | [模块接口与端口设计说明（历史）](ghrah-08-interface-port.md) | 旧端口的 50 项接口与项目绑定模型；头部有「现行差异摘要」（57+8、`Interface`、`CoreCatalogPort`、25 条边界基线） | 追溯旧端口设计。现行接口与接入方式见[模块接口端口](ARCHITECTURE.md#4-接口化端口跨模块唯一通道) |
| GHRAH-DESIGN-009 | [Agent 模板管理设计说明](ghrah-09-agent-template-management-design.md) | 模板表结构（`available_members` 成员数组）、七类模板接口的入参出参与状态码、成员去重与版本钉住、列迁移、能力目录查询接口、逐功能时序与类图 | 改模板库/模板表单/模板接口；评审"模板字段能不能加、并发写会怎样失败" |
| GHRAH-DESIGN-010 | [Agent 实例化设计说明](ghrah-10-agent-instantiation-design.md) | 记录/快照/授权三处权威划分、实例生命周期接口、装配输入 v3 契约、模板→成员渲染规则、重启恢复 | 改创建/启停/删除链路；排查"实例配置从哪来、重启后为什么这样恢复" |
| GHRAH-SPEC-011 | [member 定义规范](ghrah-11-member-spec.md) | member 的定位与五层形态（定义面/模板/装配/授权/工具面）、三段全名与退化包兼容、三种类型、两轴与 `expose` 的区别、控制成员、技能目录与 `load_skill`、14 条不变式 | 写能力包成员、改模板或装配输入、判断"这个名字该不该出现/这个轴能不能这么填" |
| GHRAH-SPEC-012 | [ability 包结构与定义规范](ghrah-12-ability-package-layout.md) | 内核件/能力件/MCP 三类目录结构与逐层含义、`ability.yaml` 与 lock 结构、构建/安装/装载职责、可移植性规则、10 条不变式与错误码 | 新建能力包、做发行版预装、排查"包装不进去/装进去起不来/索引漂移" |
| GHRAH-DESIGN-013 | [能力与成员数据底座设计说明](ghrah-13-ability-data-store.md) | SQLite 三处权威（能力关系库/实例快照/模板库）、启动提交与运行期只读、授权与绑定变更、目录与内容读取、失败语义、迁移与不变式 | 改存储 schema、改授权/绑定写路径、排查"授权从哪来/重启为何这样恢复/内容为什么读不到" |

`04` 以 `01` 的契约为前提，`06` 为 `01`、`03`、`04`、`05` 提供代码位置索引；`11`/`12`/`13` 是成员、包结构与数据底座三份规范，`01`/`04` 的细节以它们为准。`07` 与 `08` 用于追溯历史。`ARCHITECTURE.md` §8/§9/§10 概述能力、数据底座与模板/实例化，子系统文档提供实现细节。文档互有出入时，以代码为准。

### 2.3 Scheme 包文档层

[ghrah-scheme 第一代技术文档](../ghrah-scheme/README.md#第一代技术文档)以 `acf06fe` 与 2026-10-29 工作区为基线，由六篇基础文档和一篇文件切片指南组成。它解释目标产品树与已实现机制，当前支持情况统一查阅[实现状态与代码地图](../ghrah-scheme/docs/2026-10-29-implementation-status.md)。

- [文件 Ability 资源切片：权威关系、Scheme 接入与面板](../ghrah-scheme/docs/2026-10-29-ability-resource-slice.md) — 已实现切片的 API、完整示例和独立 UI。

使用 `ghrah.scheme` 的当前 Python API 读[运行时 API](../ghrah-scheme/docs/2026-10-29-runtime-api.md)；理解全局树和局部映射读[系统模型](../ghrah-scheme/docs/2026-10-29-system-model.md)；新增接入或面板读[二次开发指南](../ghrah-scheme/docs/2026-10-29-development-guide.md)。Ability 容器实质仍在 Core，新包文档没有将其执行算法复制到机制包。

旧能力文档中以 `ghrah.scheme.loading` 等路径描述 Core 能力模块的段落带有历史路径，当前模块边界按新文档的代码链接与实际源码核对。本文此次增加入口，不表示旧文档其他章节已完成重新校准。

## 3. 按角色选读

| 你是谁 | 建议顺序 |
| --- | --- |
| 刚接手，要把系统跑起来 | `ghrah-05` §1–§2 → `ghrah-06` §2–§4 → `ARCHITECTURE.md` §1–§2 |
| 要新增一个工具（能力） | `ghrah-12` §2–§7 → `ghrah-01` §3–§5 → `ghrah-06` §8 |
| 要写技能包 | `ghrah-04` §2–§5 → `ghrah-12` §2.3 → ability-builder 的 `SKILL.md` |
| 要改执行/权限语义 | `ghrah-01` §9–§13 → `ghrah-03` §5 → `ghrah-07` §3（D-7：治理层缺席） |
| 排查线上问题 | `ghrah-05` §8 排障手册 → `ghrah-05` §4 日志 → `ghrah-03` §8 异常与降级 |
| 做架构评审 | `ghrah-01` §13（不变式与已知偏差）→ `ghrah-06` §7（依赖方向）→ `ghrah-07` 全文 |
| 只想确认"某份旧文档还能不能信" | `ghrah-07` §2 逐份判定 → [archive/README.md](archive/README.md) |

## 4. 全目录共享的写作约定

| 约定 | 写法 | 反例 |
| --- | --- | --- |
| 证据引用 | `文件路径:行号`，路径相对仓库根（`scheme/loading/ability_schema.py:54`） | "在扫描器里" |
| 术语 | 端口层统一称**接口**（`ghrah.protocol.interface`、`CORE_INTERFACES`），不写"能力 / 能力清单"；`ability`（能力包）是另一概念，指被授权调用的实现单元，两者不可混用 | 把端口接口写成"能力"、把能力包写成"接口" |
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
| 新增或删除 Core 端口接口，或改 `ports/core.py` | `ARCHITECTURE.md` §3.2 契约与 §4 接口表及调用说明 |
| 改跨模块调用路径（谁调谁、经不经端口） | `ARCHITECTURE.md` §2.1 总览图、§4 端口接入与 §11 一致性 |
| `ghrah.scheme` API、生命周期或 Scheme 产品树语义 | [包文档入口](../ghrah-scheme/README.md)所列的相应专题及实现状态；跨语言描述格式仍同步 Python／TS |
| Agent 模板库字段、模板接口或整包展开规则 | `ghrah-09` §3–§5 |
| Agent 创建/启停/删除链路、装配输入 v3 或渲染规则 | `ghrah-10` §3–§6 |
| member 的名字、类型、轴、权限申请或工具面命名 | `ghrah-11` 全文 |
| 能力包目录结构、`ability.yaml`、lock、安装/构建规则 | `ghrah-12` 全文 |
| 能力/成员/授权/模板/实例的存储 schema、写路径或迁移 | `ghrah-13` 全文、`ARCHITECTURE.md` §7 数据底座、§8 能力系统 |
| 推翻了某条既有决策 | `ghrah-07` §3 追加 `D-n` 并标"已否决"，**不要**直接删旧条目 |

## 6. 归档

**[archive/](archive/README.md)** 收录 `docs/` 原有的 4 份 `2026-10-26-*` scheme 系列文档。它们描述的是迁到 `ghrah/scheme/` 的**半程状态**（自称"20 份 yaml 并入 4 份容器"，而今天是 5 份容器），已被 `f8ff157` 这一轮重构再次推翻，**不要照着写代码**。逐份判决见 [archive/README.md](archive/README.md) §2。

本次重写所依据的另 **16 份** 2026-10-12 ~ 2026-10-28 旧稿（`能力系统建设设计方案.md`、`ability加载架构.md`、`skill-system-porting-guide.md` 等）**不随仓分发**：它们属于仓外私有资料库 `doc/archive/`，判定理由已全部吸收进 `ghrah-07` §2，回仓查证时只需读 `ghrah-07`。

## 7. 已知文档债务

以下问题**已知且未处理**，读文档时自行打折，明细见 `ghrah-07` §5：

- `ARCHITECTURE.md` 已于 2026-11-23 按当前实现全面重写（16 章 + 2 附录），但行号只作当前指针；引用具体行号前回源码核对。
- `ARCHITECTURE.md` §16 记录的偏离是**现状**，不是待办计划；改代码前先读该章，避免把已知问题当新发现。
- `ghrah-01` / `ghrah-04` / `ghrah-05` / `ghrah-06` 已于 2026-11-23 重写；`ghrah-02` 整体降级为历史（头部有红旗与现行入口）；`ghrah-03` / `ghrah-07` / `ghrah-08` 做过定点校准与状态位。
- `ARCHITECTURE.html` 是生成物，不要手改；改 `.md` 后重跑 `scripts/gen_architecture_html.py`（无 `--check` 模式）。
- 技能侧只有一套实现：`ghrah-core/src/ghrah/ability/`；`ghrah/skills/` 与 `SkillUnit` 已退役，见 `ghrah-04`。
- 旧目录名（`ghrah-core/tests/{abilities,manifest,scheme,skills}/`）仍在，内容多为迁移遗留；按内容找测试。
- `CORE_COMMANDS`（22 条）与 `CoreUnit._COMMANDS`（31 条）存在 7 条装配/模型命令的集合漂移；`MANIFEST_COMMANDS` 10 条无 owner。见 `ARCHITECTURE.md` §16。
