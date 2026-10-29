# archive —— 被取代的设计文档

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-ARCHIVE-INDEX |
| 状态 | 已废弃（只读历史，**不要照着写代码**） |
| 收录范围 | `docs/` 原有的 4 份 2026-10-26 scheme 系列 |
| 判决书 | [../ghrah-07-history-and-decisions.md](../ghrah-07-history-and-decisions.md) §2 |

## 1. 这里为什么有这些文件

`docs/` 里曾经有 4 份 2026-10-26 的 scheme 系列文档，记录"把 `abilities/` 实现层与 `manifest/` 声明层合并进 `ghrah/scheme/`"这场迁移。它们当时都是 `status: done`，但**迁移的下一轮（2026-10-28 19:18–19:22）又把它们自己写下的结论推翻了**：

| 提交 | 做了什么 |
| --- | --- |
| `1938ffa` | 退役 `abilities/` 与 `manifest/` 两个目录树，定义面收敛到 `ability.yaml` |
| `fc032df` | `ghrah-subject` 改指 `ghrah.scheme`，旧 `ghrah.abilities` 导入清零 |
| `f89731f` | 旧 manifest 解析栈整体备份到 `legacy/manifest-old-format/` |

所以这 4 份不是"错的历史"，而是"半程的历史"：它们描述的中间态已经不存在了。保留它们的唯一理由是——当有人问"`RegistryProtocol` 去哪了""为什么 manifest 有 20 份 yaml 而现在只有 5 份容器"，答案在这里。

## 2. 逐份判决

| 归档件 | 自述状态 | 为什么过期 | 现在的权威文档 |
| --- | --- | --- | --- |
| [2026-10-26-ability-scheme-draft.md](2026-10-26-ability-scheme-draft.md) | draft（36 KB） | 提出"为新版 Ability 容器定义局部地址、成员契约、提供者绑定、授权记录、访问入口、引用失效规则"。**成员契约部分已成真**（`AbilityDefinition`），但授权记录（`GrantRecord`）、引用失效、`mount_id` 全部未实施 | `ghrah-01` §4（契约）、§11（权限） |
| [2026-10-26-scheme-ability-migration.md](2026-10-26-scheme-ability-migration.md) | done（49 KB） | 自称"20 份随包 yaml 并入 **4 份**容器定义"。今天已是 **5 份**（`mcp` 后加），连它最核心的数字都过期 | `ghrah-01` §5.3、`ghrah-07` §2.2 |
| [2026-10-26-scheme-file-map.md](2026-10-26-scheme-file-map.md) | done（32 KB） | 逐文件职责，但停在**文件粒度**，没有调用链与不变式；`abilities/`、`manifest/` 路径已全部失效 | `ghrah-06` 全文 |
| [2026-10-26-scheme-module-inventory.md](2026-10-26-scheme-module-inventory.md) | done（55 KB） | 唯一仍有残值的部分：§7 记录"声明面 vs 实现面双 schema"的实测差异（22 个成员中 19 个可比、18 个有差异）。该实测结论已并入 `ghrah-01` §8.4 | `ghrah-01` §8.4 |

## 3. 另一批旧文档不在这里

首次重写（2026-10-29）所依据的 **16 份** 2026-10-12 ~ 2026-10-28 设计稿、裁定记录、调研报告（`工业智脑` 阶段的 `02-能力系统.md`、`能力系统建设设计方案.md`、`能力协议层调用依赖分析.md`、`package技术文档.md`、`skill-system-porting-guide.md` 等）**没有随仓分发**，它们留在原工作目录 `doc/archive/`。

原因：那 16 份属于 `D:\codeWorkSpace\ghrah-project\doc\` 这份私有资料库，其中多份与 git 历史里的中间态重复；把 600 KB 已废止的中文设计稿塞进本仓 `docs/` 只会掩盖现役文档。逐份去向对照表在那边 `doc/archive/README.md`，**判定理由在本仓 `ghrah-07` §2**（那一节逐份给出了"哪个概念死了、死因是什么"），迁移时不需要两份都读。

## 4. 怎么读一份死文档

1. 先看它的 frontmatter `status`：`draft` 表示连同期的作者都还没定；`done` 只表示"当时的迁移做完了"，不表示结论今天成立。
2. 反查它提到的每个符号是否还存在于 `ghrah-core/src`：`grep -rn "RegistryProtocol" ghrah-core/src` 返回空，就说明那份文档的核心机制已经没了。
3. 想知道替代关系，直接读 `ghrah-07` §2 对应行——那里写的是"为什么废"而不是"什么时候废"。

另外：这 4 份归档件内部的相对链接**多半已经失效**（它们指向同期存在、但从未进本仓的兄弟文档，如 `2026-10-26-scheme-foundation-design.md`、`../analysis/*`）。这些链接不修，归档件不是可跳转的索引。

## 5. 未归档的非 markdown 资产

以下资产既未归档也未重写，引用时自行核对时效：

| 文件 | 位置 | 状态 |
| --- | --- | --- |
| `ARCHITECTURE.html` | `docs/` | 2026-10-27 的导出快照，早于 §6 改写；只作分享 |
| `工业智脑平台总体架构.html` | 原 `doc/` | 平台分层示意，与 0.2.0 代码不完全对齐 |
| `前后端交互模式-可视化.html` | 原 `doc/` | 同上 |
| `架构图审阅.html` | 原 `doc/` | 5 MB 审阅页，加载慢 |
| `启动命令.txt` | 原 `doc/` | 启动命令备忘，可与 `ghrah-05` §2 互证 |