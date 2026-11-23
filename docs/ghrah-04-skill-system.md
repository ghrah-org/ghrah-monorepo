# 技能成员与技能面装载

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-DESIGN-004 |
| 文档状态 | 现行（已实现） |
| 校准基线 | `ghrah-monorepo` @ `a742fcf`（2026-11-23），`ghrah-core` 0.2.0a0 |
| 读者 | 写技能包的人、改技能目录/正文装载的人、排查"技能没进目录/按名调不到/正文读不到"的人 |
| 不覆盖 | 包目录与构建安装规则（`ghrah-12`）、成员定义与两轴（`ghrah-11`）、SQLite 数据底座与启动提交（`ghrah-13`）、驱动循环（`ghrah-03`） |
| 取代 | 本文件 1.x 的"两套实现并存 + 两代 lock schema + ContextManager 注入"口径；那些结论已失效 |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。记录旧技能包与另一套 Scheme 实现的并存关系、两代 lock schema |
| 2.0 | 2026-11-23 | 按 `a742fcf` 整篇重写为一条主线：成员声明 → 构建 → 安装 → 启动提交 → 每轮目录注入 → 按名/按地址读正文。删除三条失效主线，补多技能、frontmatter 归约、`read:` 地址、1MB 上限、bind 分流与纯技能包进目录 |

## 1 文档说明

本文只回答一个问题：**一个 `type: skill` 成员，从作者写下的目录与正文，到模型在某一轮看到目录、再按需读到正文，中间经过哪些权威与哪些校验。** 包怎么构建、成员全名怎么拼、表怎么设计，分别以 `ghrah-12`、`ghrah-11`、`ghrah-13` 为准。

| 术语 | 含义 |
| --- | --- |
| 技能成员 | `Member`（`ghrah-core/src/ghrah/ability/member.py`）中 `type="skill"` 的一条记录 |
| 技能面 | 模型可见的技能集合：每轮目录 + 按需正文；不产函数 schema |
| 目录条目 | ``- `alias/key` (read: `<path>`): description`` |
| 正文 | `skills/<key>/SKILL.md` 全文，只经 `load_skill` 读取 |
| 安装记录 | 版本目录旁的 `<version>.lock.yaml`（`schema: 1`），运行期装载链不读 |
| 授予 / 绑定 | `visible` 决定可发现，`bind` 决定是否进每轮目录 |
| `read` 地址 | 正文相对 ability 根的路径；外部件带版本段，内核件不带 |

| 阶段 | 发生什么 | 状态 | 证据（符号 + 文件） |
| --- | --- | --- | --- |
| ① 声明 | 作者只写 `skills/<name>/SKILL.md` 与 `ability.yaml` 头段，`members` 由构建生成 | 已实现 | `generate._skill_member`（`ghrah-core/src/ghrah/ability/package/generate.py`） |
| ② 构建 | 读 frontmatter 生成 `members`，写回 `ability.yaml`（定义面 `schema_version: '2'`） | 已实现 | `generate.build` / `generate.check`（同文件） |
| ③ 安装 | 只读复制到 `<install_root>/<ns>/<name>/<version>/`，旁边写 `<version>.lock.yaml` | 已实现 | `AbilityInstaller.install`（`.../package/installer.py`） |
| ④ 启动提交 | 捕获定义与正文，提交成 `ability_versions` + `ability_content` | 已实现 | `commit_version`（`.../access/store.py`）、`register_package_instance`（`.../access/startup.py`） |
| ⑤ 每轮目录 | 驱动循环调用 `ToolSurfaceRuntime.skill_reminder()`，按 `bind` 分流后追加提醒 | 已实现 | `.../surface/runtime.py`、`.../agents/base.py` |
| ⑥ 读正文 | 按 `alias/skill`、裸键或 `read:` 地址定位，从 `ability_content` 读全文 | 已实现 | `LocalGrantedContentProvider.load_skill`（`.../ability/local_content.py`） |

证据一律用「符号 + 文件」，行号以 `a742fcf` 为准（§9 的裸文件名相对 `ghrah-core/src/ghrah/ability/`）；状态只用**已实现 / 未实施 / 已废弃**三态。

## 2 技能成员是什么

`Member` 是 Core 内部唯一的成员数据实体，`operation` / `skill` / `mcp` 并列；技能同样参与模板勾选、启动核定、授权与实例快照，只是装载面不同（`.../ability/member.py:4-33`）。它的 identity 字段与 operation 完全一样，区别只在 `data`。

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `title` | 是 | 展示名；构建期来自 `metadata.title`，缺省为成员键 |
| `description` | 是 | 一行说明；进目录并参与目录摘要 |
| `path` | 是 | 正文的 ability 根相对路径，同时就是目录里的 `read:` 地址 |
| `media_type` | 是 | 正文媒体类型，构建期固定 `text/markdown` |
| `tags` | 否 | 字符串数组，构建期去重排序 |
| `assets` / `references` / `scripts` | 否 | 三类资源表：资源键 → `{path, media_type, size, digest}` |

必填键与允许键由 `Member` 构造期校验（`.../ability/member.py:73-125`）；缺键、未知键都拒绝，不隐式补默认。`data.path` 自带包前缀：外部件 `<ns>/<name>/<version>/skills/<key>/SKILL.md`，内核件 `<ns>/<name>/skills/<key>/SKILL.md`（`Member.logical_prefix`、`AbilityDefinition.to_members`，`.../loading/ability_schema.py:788-879`）——这就是同一条目录在内置件与外部件上 `read:` 地址长度不同的原因。

| 维度 | `operation` | `skill` | `mcp` |
| --- | --- | --- | --- |
| 装载面 | 工具面（函数 schema） | 技能面（目录 + 按需正文） | 工具面（远端工具包装） |
| 有工具形态 | 有（受 `expose` 约束） | 无 | 有 |
| 可执行 | 是 | 否（脚本另算，见 §8） | 是（经 MCP 后端） |
| 主要数据 | `input_schema` / `output_schema` / `execution` / `permissions` | `path` / `media_type` / 三类资源 | 远端工具契约 |
| 典型数量 | 一个包 0..N | 一个包 0..N（**多技能合法**） | 一个包 1..N（快照） |

技能成员有两条轴：`visible`（能否被发现）与 `bind`（是否进每轮正式目录）。唯一硬规则是 **`bind=true` 要求 `visible=true`**，构造期拒绝 `visible=false, bind=true`（`MemberAxes`，`.../ability/member.py:132-178`）。`visible=true, bind=false` 合法：可发现、未常驻，模型可调 `bind_member` 装上。两轴与定义面的 `expose` 不是一回事：`expose` 是包作者声明"有没有工具形态"，两轴是授予后的每 Agent 状态（`ghrah-11` §5）。

`assets` / `references` / `scripts` 是技能的从属资源，不是独立成员：会出现在授权账本地址里（`skill/<key>/assets/<资源键>` 等），但不成装配成员，也不产工具（`ghrah-11` §2.1）。

## 3 定义面：`skills/<name>/SKILL.md`

```text
<package-root>/
├── ability.yaml                    # 头段（作者写）+ members（构建生成）
└── skills/
    └── <name>/                     # 目录名 = 成员键 = frontmatter name
        ├── SKILL.md                # 正文：frontmatter + 正文体
        ├── assets/                 # 可选附件
        ├── references/             # 可选参考资料
        └── scripts/                # 可选脚本（只允许 .py）
```

构建只认 `skills/<name>/…` 三层以上结构；`skills/` 下直接放文件、目录名不合 `^[a-z0-9]+(-[a-z0-9]+)*$` 或超过 64 字符都在构建期报错（`generate._walk_members`，`.../package/generate.py:232-284`）；`skills/<name>/` 下只允许 `SKILL.md` 与 `references/`、`assets/`、`scripts/`（`.../package/generate.py:315-324`）。

现行 `SKILL.md` **必须有 frontmatter**（首行逐字 `---`，以 `\n---\n` 闭合），不再是"整篇纯 Markdown"（`generate._parse_frontmatter`，`.../package/generate.py:287-300`）。

| frontmatter 键 | 规则 | 归约结果 |
| --- | --- | --- |
| `name` | 必填，必须等于 `skills/` 下的目录名（= 成员键） | 不一致直接构建失败（`.../package/generate.py:327-330`） |
| `description` | 必填，非空、≤1024 字符 | 写入 `members.skills.<key>.description`（`:331-333`） |
| `metadata.title` | 可选字符串，缺省为成员键 | 写入 `title`（`:334-339`） |
| `metadata.tags` | 可选 `list[str]` | 去重、排序后写入 `tags`（`:340-346`） |

**frontmatter 是作者侧事实源，但只在构建期生效**：`_skill_member` 用它生成 `members` 并原子写回，手改 `members` 会被 `--check` 判为索引漂移。捕获期（安装后的启动提交）重新解析 frontmatter，但只核对 `name` 与成员键一致、`description` 非空；`title` / `description` / `tags` **以索引为准**（`capture_content` 注释，`ghrah-core/src/ghrah/ability/content.py:243-244`）。`SkillDefinition` docstring 仍写"捕获期以正文为准覆盖"（`.../loading/ability_schema.py:712-713`），与实现相反——该能力**未实施**，排障以实现为准。frontmatter 的 YAML 解析还拒绝重复键与 `<<` merge key（`load_yaml`，`.../loading/ability_schema.py:117-160`）。

一个包可以有 0..N 个技能，成员键必须唯一；解析期按成员全名去重，重复报 `成员重复`（`_members_list_to_tables`，`.../loading/ability_schema.py:259-267`）。`skills: {}` 合法，但包必须至少有一个成员——`operations` 与 `skills` 合计为零时拒绝（`:765-766`；构建期同，`.../package/generate.py:508-509`）。多技能在目录里按 `alias/<成员键>` 区分，例如同包的 `alpha`、`beta` 渲染成 `demo/alpha`、`demo/beta`（`test_multi_skill_package.py:133-161`）。

| 目录 | 角色 | 资源键 | 限制 |
| --- | --- | --- | --- |
| `assets/` | 附件（图片、数据文件） | 去后缀、`/` 换 `-` | 键不得冲突 |
| `references/` | 参考资料 | 同上 | 键不得冲突 |
| `scripts/` | 可执行脚本 | 同上 | 只允许 `.py`，键不得冲突 |

资源键由 `derive_resource_key` 派生：`references/api/auth.md` → `api-auth`（`.../package/paths.py:115-121`）。构建期为每个资源写 `path` / `media_type` / `size` / `digest`（`generate._attach_resources`，`.../package/generate.py:356-381`），捕获期按摘要核对字节（`content.capture_content`，`.../ability/content.py:245-256`）。两个不同路径若派生出同一个键，构建期直接报冲突（`:369-370`）。

正文位置是硬约束：`members.skills.<key>.body.path` 必须逐字等于 `skills/<key>/SKILL.md`（`.../loading/ability_schema.py:443-451`），`media_type` 由构建期固定 `text/markdown`（`.../package/generate.py:347`）。

## 4 构建与安装

在源包根执行：

```sh
python -m ghrah.ability.tools.build <package-root> [--check] [--builtin|--shipped]
```

不带 `--check` 时扫描 `operations/` 与 `skills/`、生成 `members`、原子写回 `ability.yaml`；带 `--check` 时只校验不写盘，索引与声明不一致就非零退出并输出逐条 diff。入口见 `ghrah-core/src/ghrah/ability/tools/build/__init__.py`，生成逻辑在 `generate.build` / `generate.check`（`.../package/generate.py:606-642`）。构建是**严格**的：未知文件、未知子目录、`.py` 以外的脚本都报错；安装 intake 另有**宽松**路径，上传导入先把文本源归一成 UTF-8 无 BOM + LF，并跳过包内杂物、只跳过不登记（`normalize_source`，`.../package/intake.py:76-126`）。"作者构建失败、上传安装成功"是预期口径差。

```text
<install_root>/
└── <namespace>/
    └── <name or namespace>/           # 退化身份时第二段与第一段同名
        ├── <version>/                 # 版本目录：只读、不可变
        │   ├── ability.yaml
        │   ├── operations/...
        │   └── skills/<key>/...
        └── <version>.lock.yaml        # 安装记录：与版本目录同级，不在其内
```

安装由 `AbilityInstaller.install` 完成：校验来源、解析定义、拒绝保留命名空间、收集文件、校验成员文件与内容摘要、计算整树摘要、检查目标槽位，最后"临时目录完整拷贝 → 摘要自检 → 写安装记录 → 原子 rename"（`.../package/installer.py:389-451`、`:667-741`）。同版本重复安装：摘要相同 → 幂等返回 `already_installed`；摘要不同 → `version_conflict`，绝不覆盖（`:620-665`）。

`<version>.lock.yaml` 是**安装事实**，不是运行期契约；`schema` 固定 `1`，作者不应感知、更不应手写：

```yaml
# ability.lock.yaml —— 安装期生成，勿手改
schema: 1
package_digest: sha256:<整树摘要>
digest_algorithm: sha256-tree-v1
installed_at: '2026-11-23T10:00:00Z'
source: {kind: directory, ref: <来源路径>}
files:
  ability.yaml: sha256:<...>
  skills/<key>/SKILL.md: sha256:<...>
```

`LOCK_SCHEMA_VERSION = 1`，旧版记录不读、不自动改写（`ghrah-core/src/ghrah/ability/package/lock.py:69-70`）；文件名是 `<version>.lock.yaml`（`:72-75`、`:144-146`）；内容就是 schema、算法、包摘要、逐文件摘要、安装时间、来源与 MCP 健康块，不含身份、版本、权限或整树文本（`InstallLock`，`:126-141`）。源包里带任何 `*.lock.yaml` 会被拒绝（`lock_in_source`，`.../package/installer.py:421-426`）——锁只能由安装器生成。`AbilityInstaller.verify` 用 `files` 逐文件 diff、复算整树摘要，再核对索引里的正文/资源摘要与大小；缺记录视为拒绝而非"假装健康"（`:944-1051`）。

安装根由发行版显式给出：配置项 `ability.install_root`（`ghrah-subject/src/ghrah/subject/config.py:170-187`）与环境变量 `GHRAH_SUBJECT_ABILITY_INSTALL_ROOT`（`:811-817`），都必须是绝对路径；安装提供器构造时再校一次（`.../package/provider.py:43-49`）。装配来源只有内置树与安装根（`AbilityAssembly.select`，`.../ability/assembly.py:43-53`；`BuiltinAbilitySource`，`.../ability/source.py:51-64`），目录池默认只含内置树（`ability_roots.default_pool`，`.../loading/ability_roots.py:159-161`）。

安装记录**不在运行期装载链上**。读它的有三类：安装域命令（`show` / `verify` / `list`）；管理/目录接口，只把 `lock_state` 等字段用于展示（`.../core/read/ability_catalog.py`）；启动捕获时 `InstalledAbilityProvider.capture` 先 `verify`、再把捕获版本的整树摘要与 `package_digest` 对齐（`.../package/provider.py:70-96`）。启动提交完成后，运行期只读 SQLite 与已提交字节，不再打开版本目录或安装记录（§5）；磁盘上的 `SKILL.md` 被改也不会改变模型读到的正文。

## 5 启动提交

启动核定器把"要授予的包"变成"库里的确定版本 + 授权"（`ghrah-core/src/ghrah/ability/access/startup.py:348-440`；完整流程见 `ghrah-13` §2.2）：

| 步 | 动作 | 证据 |
| --- | --- | --- |
| 1 | 捕获包版本：内置件读内置树，外部件走安装提供器并校验安装记录 | `source.capture_package`（`.../ability/source.py:130-159`）、`InstalledAbilityProvider.capture`（`.../package/provider.py:70-96`） |
| 2 | 提交版本：定义、实现标识、内容清单、内容字节落库（幂等） | `store.commit_version`（`.../access/store.py:564-617`） |
| 3 | 选定实例版本槽 | `store.select_instance`（`.../access/startup.py:423-430`） |
| 4 | 写逐 Agent 授予（成员集合、内容白名单、轴） | `reconcile.apply_grants` → `store.write_grant`（`.../access/reconcile.py:142-204`） |

| 表 | 主键 | 存入什么 |
| --- | --- | --- |
| `ability_versions` | `(project, digest)` | `definition`（规范化定义 JSON）、`implementation`（实现标识）、`manifest`（正文/资源的路径、媒体类型、大小、摘要） |
| `ability_content` | `(project, digest)` | 按摘要寻址的字节：技能正文、附件、脚本 |

表结构在 `ghrah-core/src/ghrah/ability/access/store.py:57-92`；`manifest` 的成员地址形如 `skill/<key>`、`skill/<key>/assets/<资源键>`（`ghrah-13` §2.1）。

`commit_version` 在写事务里做三道硬校验（`.../access/store.py:572-617`）：① 版本身份自洽——整树版本的 `digest` 必须等于 `package_digest`，文件切片必须等于定义与实现的密封；② `manifest` 的内容成员集合必须精确等于定义声明，不能多也不能少；③ 每个内容摘要都必须能在 `ability_content` 找到 blob，且复算摘要、长度与清单一致。任何一项不成立都抛 `invalid_change`，不发布不完整版本。

运行期读取只有两条入口：目录由 `store.surface_snapshot` 在同一读事务里读授权、实例、版本、定义，产出 `SurfaceCatalog`（`.../access/store.py:1330-1349`）；正文由 `store.read_chunk` 按成员引用读 `ability_content` 字节，并在同一只读事务里再核授权与摘要（`:1756-1802`）。两条都不扫描安装目录、不读 `ability.yaml`、不读安装记录（`ghrah-13` §5）。

重启时实例快照提供成员定义数据与 `axes.bind`，启动核定再按关系库重新核定授权；快照不能凭空产生许可（`ghrah-13` §3.3；`startup._bound_names`，`.../access/startup.py:811-821`）。技能绑定集合就是快照里 `axes.bind` 为真的成员全名；退化包的两段名与三段名按等价形态互认（`ToolSurfaceRuntime._member_forms`，`.../surface/runtime.py:44-55`）。

## 6 目录注入（核心）

技能成员走**消息面**，工具成员走**工具面**。驱动循环在构造本轮 LLM 请求时调用 `ToolSurfaceRuntime.skill_reminder()`，拿到文本后追加一条 `role="user"`、`source="system:runtime"` 的消息（`ghrah-core/src/ghrah/agents/base.py:1405-1414`）：

```python
if self._tool_surface is not None:
    try:
        reminder = await self._tool_surface.skill_reminder()
    except Exception:
        logger.warning("Skill catalog injection failed; skipping", exc_info=True)
        reminder = None
    if reminder:
        messages = [*messages, ChatMessage.user(reminder, source="system:runtime")]
```

这条消息只存在于本次请求的 `messages` 副本里，**不写回 Context 历史**，因此不随轮次堆叠、也不参与历史压缩；每轮重新计算。没有工具面（未接线 `ToolSurfaceRuntime`）的 Agent 不会有技能目录。成员来源是同一次关系库快照：`_catalog()` → `read_agent_catalog` → `surface_snapshot`（`.../surface/runtime.py:167-184`；`.../surface/catalog.py:162-176`），不是重扫安装目录。

`skill_reminder` 委托 `skill_context.next_skill_reminder`（`ghrah-core/src/ghrah/ability/loading/skill_context.py:182-207`）：

| 情形 | 本轮输出 |
| --- | --- |
| 有绑定的技能 | 每轮返回完整目录文本 |
| 会话首次（`last_digest is None`） | "介绍"口径：`The following skills are available in this session:` |
| 摘要与上次相同 | 仍用"介绍"口径（文案稳定，利于前缀缓存） |
| 摘要变化 | "完整替换"口径：`This complete catalog replaces every earlier available-skills list...` |
| 从有到无 | 一次"已清空"口径（`No skills are currently available...`），之后不再注入 |
| 从未有过技能 | 返回 `None`，不占上下文 |

摘要由 `digest_skill_catalog` 计算，只取 `catalog_name`、截断后的 `description` 与 `read` 地址，**正文变化不改变摘要**（`.../loading/skill_context.py:117-139`）。所以"摘要不变"的含义是**不重复宣告目录变化**，不是不注入；目录每轮出现一次。注意 `agents/base.py:1406` 的注释写作"摘要变化时才注入"，与 `next_skill_reminder` 实际行为不一致，排障以 `next_skill_reminder` 与 `test_skill_injection.py:229-246` 为准。

`skill_reminder` 先按绑定轴分组（`.../surface/runtime.py:184-201`）：

| 授予状态 | 进正式目录 | 额外输出 |
| --- | --- | --- |
| `visible=true, bind=true` | 是，完整条目 | 无 |
| `visible=true, bind=false` | 否 | 末尾一行"可用但未绑定"，只列名字与绑定入口，不展开摘要 |
| `visible=false` | 否 | 目录与提示都不出现 |
| 调用方未提供成员轴（旧装配） | 全部可见技能按已绑定处理 | 无 |

未绑定提示模板固定在 `_UNBOUND_SKILLS_TEMPLATE`（`.../surface/runtime.py:35-41`），示例名取第一个未绑定技能；绑定操作走 `set_member_bound`，技能按目录名（`alias/key`）、成员全名或成员键匹配，变更立即写回实例快照（`:304-362`）。解绑不会把技能从授予快照里删掉，只是不再进正式目录。

每条目录固定为 ``- `alias/key` (read: `<ability 根相对路径>`): description``，渲染在 `skill_context._skill_line`（`.../loading/skill_context.py:142-152`）：`catalog_name` 是目录名，也是模型传给 `load_skill` 的名字；`read` 地址来自成员 `data.path`；description 空白折叠后超过 500 字符截为 497 + `...`（`CATALOG_DESCRIPTION_MAX_CHARS`，`:61`、`:101-114`），其中的 `&` / `<` / `>` 被转义以免破坏 `<system-reminder>` 边界（`_escape_text`，`:382-384`）。示例：

```text
- `reports/guide` (read: `reporting/reporting/0.1.0/skills/guide/SKILL.md`): 报告编写规范与版式要求
```

目录名用成员键而不是点分全名：模型要拿这个名字去调加载入口，显示限定名会让它调不动（`skill_context.py:20-22`）。

目录收集技能时按授权投影里的**技能节点**独立遍历，不挂在工具循环里，因此没有 operation 的纯技能包同样进目录（`catalog_from_view` 第二段循环，`.../surface/catalog.py:143-151`）；只有 `body_allowed` 为真才进目录（`_skill_entries`，`:94-107`）。这是已修复的缺陷：此前技能目录依赖同包是否有工具，纯技能包会整体消失；回归用例见 `test_skill_injection.py:177-182`。

没有技能且从未注入过 → `None`，完全不占上下文；有技能但全部未绑定 → 不返回"空目录"文案，只返回绑定提示；绑定集合从有到空 → 注入一次"已清空"，之后安静（`next_skill_reminder`）。

为什么不在 ContextManager：Context 的职责是历史、窗口管理与压缩。技能目录是"这一轮给模型的临时上下文"，放进 Context 会与窗口管理顺序耦合（旧实现必须排在窗口管理之后，否则被压缩），还需要 `message_factory` 等接线，缺一个就静默跳过。现在注入点放在驱动循环，`skill_reminder` 只产文本，Context 完全不知道目录存在；目录不进历史、不参与压缩，撤销/换代对下一轮立即生效。

## 7 `load_skill`

`LoadSkillInput`（`ghrah-core/src/ghrah/ability/builtin/core/operations/load_skill.py:37-59`，`extra="forbid"`）只有两个字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `full_name` | `str`（必填，非空） | 目录名 `alias/skill`、裸技能键，或目录条目的 `read:` 地址 |
| `path` | `str \| None` | 可选；相对该技能正文目录的附件路径，如 `references/style.md` |

没有"服务器""工具"之类的附加参数；附件读取用 `path`，不另开入口。工具摘要明确"Content comes from the committed version; returned paths are package-relative."（`:25-29`）。

`LocalGrantedContentProvider.load_skill`（`ghrah-core/src/ghrah/ability/local_content.py:201-241`）按输入形状分流，最终都落到同一套"授权 + 版本 + 摘要"校验：

| 写法 | 解析 |
| --- | --- |
| `alias/skill` | 按 `alias` + 成员键 `resolve` 到 `skill/<key>`（`:218-229`） |
| 裸技能键（如 `research`） | 在**当前 Agent 已获授**技能里按成员键匹配；唯一命中可用，多个命中 fail-closed 并列出候选，未命中回列可用清单（`_resolve_skill_key`，`:248-258`） |
| `read:` 地址 | 只在本 Agent 已授予实例里按定义声明的正文路径反查，命中后仍走 `_resolve` 的授权与版本校验（`.../access/store.py:1366-1415`） |

地址判定：字符串含 `/skills/` 段，或以 `.md` / `.markdown` / `.txt` 结尾，就按地址处理；只剩两段的 `alias/skill` 不会命中（`_looks_like_address`，`.../local_content.py:31-36`）。裸键歧义示例：`Skill 名称有歧义：guide；请用 docs/guide, reports/guide`；未知名字：`Skill 不存在：ghost；可用：reports/guide`。地址反查只在本 Agent 的授予里做：别的 Agent 的地址、或版本对不上的地址都按 `not_found` 拒绝，不泄露存在性（`.../access/store.py:1385-1415`；`test_local_content.py:131-138`）。

给了 `path` 时：先按 `relative_path` 拒绝绝对路径、`..`、反斜杠与盘符（`ghrah-core/src/ghrah/ability/content.py:57-67`）；以正文所在目录为基准拼目标路径；只匹配该技能在授权投影里已登记的 `assets` / `references` / `scripts` 条目，路径逐字相等；目标成员必须与正文同实例、同版本摘要、同授予修订，否则 `access_denied`（`_relative_reference`，`.../local_content.py:141-165`）。因此 `path` 不是"读宿主文件"的入口，也不能跨技能、跨版本读文件。

正文**来自 `ability_content`**，不是磁盘。`read_chunk` 先核权，再按 `manifest` 取 `path` / `digest` / `size`，按摘要读字节；读前校验三件事：清单 `path` 必须等于定义声明路径、清单 `digest` 必须等于定义摘要、blob 复算摘要与长度必须等于清单（`.../access/store.py:1769-1784`）。任何一项对不上都拒绝（`unavailable`），不会"读另一份摘要仍成立的 blob"糊弄过去。`_read_text`（`.../local_content.py:172-199`）要求可加载文本（`text/*`、`application/json`、`application/yaml`；`application/octet-stream` 与其他二进制拒绝，`is_loadable_text`，`.../ability/content.py:40-54`），默认上限 **1 MiB**（`max_bytes=1024 * 1024`），超限报 `内容不是允许加载的有界文本`；跨块读取，读完再核一次授权，保证多块读取期间被撤销不会插入半篇正文（`test_local_content.py:327-352`）。

成功返回 `LoadSkillOutput`（`.../load_skill.py:62-77`）：`full_name`、`instance_id`、`version_digest`、`member`、`path`、`media_type`、`digest`、`total_bytes`、`alias`、`uri`、`content`；`path` 是包内相对路径，不是磁盘绝对路径，`uri` 形如 `ability:<alias>/<member>`。

| 情形 | 结果 |
| --- | --- |
| 内容服务未接线 | `Granted ability content service not wired` |
| 调用身份与当前 run 不一致 | `Granted content unavailable: access_denied` |
| 名字未知 / 地址不属于本 Agent | `Skill 不存在：…；可用：…` / `not_found` |
| 裸键歧义 | `Skill 名称有歧义：…；请用 …` |
| 附件路径非法或未登记 | `ValueError` / `access_denied` |
| 正文非文本或超 1 MiB | `内容不是允许加载的有界文本` |
| 版本/授予在读取期间变化 | `access_denied`（旧引用永久失效） |

`load_skill` 只读，不授予、不绑定、不修改任何授权；每次调用都过一遍当前授权（`.../local_content.py:81-90`；`test_local_content.py:80-95`）。

## 8 脚本资源

`skills/<key>/scripts/<脚本>.py` 登记为技能资源，授权地址是 `skill/<key>/scripts/<资源键>`（单数 `skill/`，与 operation 的 `operations/` 前缀不同）。它不产工具 schema，也不走 `execute_tool` 路由。模型看到的地址是另一套：`skills/<skill>/scripts/<key>`（复数 `skills/`），只接受这个形状，不接受磁盘路径（`parse_model_address`，`.../scripts/resolve.py:29-75`）。

`run_script` 是独立入口，不是 core Operation，也不能被 `execute_tool` 路由（`RunScriptTool`，`.../surface/runtime.py:554-582`）。它只在**至少一个已授予脚本可执行**时进入工具表：

```python
scripts = any(
    script.available
    for node in view.abilities
    for skill in node.skills
    for script in skill.scripts
)
extras = controls + ([RunScriptTool(self).bind_tool()] if scripts else [])
```

证据在 `.../surface/runtime.py:227-233`。脚本的**可见**与**可执行**是两件事：整包授予时技能正文与全部资源可见，脚本执行权默认 `false`，必须由显式轴单独授予（`whole_container_facts`，`ghrah-core/src/ghrah/ability/access/facts.py:210-234`）；脚本执行权（口语中的 `exec` 权限）落在脚本地址的 `available` 轴，`visible=true, available=false` 是"能读正文但不能执行"；授权策略给脚本表达可用性时必须同时给出所属 `skill`，给 skill 表达"不可用"没有这一轴，构造期拒绝（`GrantRule`，`.../access/policy.py:72-89`）。未授予执行权时调用被拒并标注 `script_exec`（`_ACCESS_KIND`，`.../scripts/runner.py:33-38`）。

`prepare_script` 在创建子进程之前完成全部检查（`.../scripts/resolve.py:87-179`）：`resolve_script` 核对脚本地址、可见性、`available` 轴、实例版本与清单条目（`.../access/store.py:1417-1510`）；包根必须可用且含 `ability.yaml`；路径必须落在 `skills/<skill>/scripts/` 下，逐段拒绝链接/junction，最终必须是包根内普通文件；只允许 `.py`；文件大小与摘要必须与授予版本一致。运行器以实例根为工作区、在进程外执行，结果信封 `action` 固定 `script.call`，`stderr` 不进模型结果（`execute_run_script`，`.../scripts/runner.py:41-168`）。脚本执行不走 shell 审批，也不借用 `execute_command` 的权限通道（`RunScriptTool.get_hooks`，`.../surface/runtime.py:576-578`）。

## 9 不变式

| 编号 | 不变式 | 状态 | 校验点 |
| --- | --- | --- | --- |
| S-01 | 同一包内成员全名唯一；同名包在内置树与安装根同时出现时拒绝静默覆盖 | 已实现 | `ability_schema.py:259-267`、`ability/source.py:104-113` |
| S-02 | 技能成员键 = `skills/` 目录名 = `SKILL.md` frontmatter `name` | 已实现 | `generate.py:327-330`、`content.py:239-242` |
| S-03 | 一个包可以有 0..N 个技能；`operations` 与 `skills` 合计不能为零 | 已实现 | `ability_schema.py:765-766`、`generate.py:508-509` |
| S-04 | 资源键在各自角色内唯一；脚本只允许 `.py`；资源摘要/大小由构建写入、捕获/安装核对 | 已实现 | `generate.py:356-381`、`content.py:245-256` |
| S-05 | `bind=true` 要求 `visible=true`；`visible=false` 的技能既不进目录也不出提示 | 已实现 | `member.py:149-153`、`runtime.py:184-201` |
| S-06 | 目录只含已授予且正文可见（`body_allowed`）的技能；纯技能包与 operation 包同权 | 已实现 | `surface/catalog.py:94-107`、`:143-151` |
| S-07 | 目录条目的名字与 `read:` 地址必须能在当前 Agent 的授予里反查；地址不越权、不跨版本 | 已实现 | `store.py:1366-1415`、`local_content.py:214-229` |
| S-08 | 正文只从 `ability_content` 读；清单路径/摘要/大小与定义、blob 三方一致才返回 | 已实现 | `store.py:1769-1784` |
| S-09 | 正文必须是可加载文本且不超过 1 MiB；超限/二进制按失败处理，不截断 | 已实现 | `local_content.py:172-199`、`content.py:40-54` |
| S-10 | 运行期装载链不读 `<version>.lock.yaml`；它只在安装域、管理面展示与启动捕获期被读 | 已实现 | `provider.py:70-96`、`core/read/ability_catalog.py`、`store.py:1330-1349` |
| S-11 | 脚本可见不等于可执行；执行权是独立轴，默认 `false`，起进程前核对大小/摘要 | 已实现 | `facts.py:210-234`、`scripts/resolve.py:150-179` |
| S-12 | 技能目录每条都能被 `load_skill` 的三种写法之一解析；目录名不是点分全名 | 已实现 | `skill_context.py:142-152`、`local_content.py:31-36` |

## 10 历史：三条被替换的主线

| 已废弃 | 曾是什么 | 为什么废弃 |
| --- | --- | --- |
| `ghrah-core/src/ghrah/skills/`（已废弃）+ `SkillUnit`（已废弃），连同用户扫描根 `~/.ghrah/skill-ability`（已废弃）/ `GHRAH_SUBJECT_SKILL_ROOT`（已废弃） | 一套独立技能包实现：扫扁平 `<root>/<name>/SKILL.md`、解析 frontmatter、按自己的格式写锁；`SkillUnit.init` 是唯一消费者，外部包靠用户目录旁路扫描 | 与现行"能力包成员 + `skills/<key>/` 目录"是两种定义面，同一份磁盘出现两套身份与两套锁；删除后无生产路径依赖，扫描/捕获/装载收敛到 `ghrah.ability.*`，外部包统一走 `ability.install_root` 安装槽 |
| 每技能一份锁 / 双锁（已废弃） | 建目录时按技能补一份锁，安装域还想维护另一份；两代 schema 并存，漂移无法裁决 | 两份摘要没有唯一权威，谁覆盖谁无法从产物判断。现在只有安装域生成的 `<version>.lock.yaml`（`schema: 1`），启动捕获校验一次，运行期不读锁 |
| ContextManager 注入（已废弃） | 由 `ContextManager.get_llm_messages` 在窗口管理后追加目录消息，经 `ServiceKey.SKILL_PROVIDER`（已废弃）与 `source="skill:catalog"`（已废弃）接线 | 注入是"这一轮给模型的临时上下文"，放在历史/窗口管理里会与压缩顺序耦合，还会因缺少消息工厂静默跳过。现在由 `skill_reminder()` 在驱动循环产出、`ActorAgent` 追加，不写历史 |

一句话：**技能是能力包的一种成员；作者写 `skills/<name>/SKILL.md`，构建把它变成 `members`，安装把它变成版本目录 + 安装记录，启动把它提交成 `ability_versions` + `ability_content`，运行期每轮从库里取目录、按需从库里读正文。** 任何"直接读安装目录/直接读旧技能根/按文件名猜正文"的做法都不在这条链上，出现即为缺陷。