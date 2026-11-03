# ghrah.ability 调用说明

一个能力包 = 一个含 `ability.yaml` 的目录。运行时**没有注册表、没有缓存**：每次调用都重新扫盘、重新投影。

本包对外提供三个面（目录面 / 实现面 / 内容授权面）+ 两个生命周期入口（构建与安装、装配）。所有入口都在 `ghrah.ability` 包及其同名子模块下，调用方不需要 `import` 更深层的文件。

## 入口总览

| 要做什么 | 调用 |
| --- | --- |
| 看有哪些能力包、各有什么成员 | `scan_abilities()` |
| 看有哪些技能、渲染技能目录 | `load_skill_catalog()` |
| 读一个技能的正文 | `load_skill_catalog().read(name)` |
| 拿模型工具清单（OpenAI 形状） | `load_all_builtin_manifests()` 逐项 `.tool.to_openai_tool()` |
| 造一个能执行的能力实例 | `create_ability(handler, **params)` |
| 把一个能力包封成可提交的内容版本 | `capture_ability_version(raw, root=...)` |
| 提交版本 / 授权 / 绑定工具 | `AbilityAccessStore.open(...)` |
| 把授权关系投影成受控读服务 | `AbilitySchemeHost(store).install(instance)` |
| 往 Context 注入获授正文 | `GrantedContentProvider(client, agent).load_into_context(...)` |
| 补齐 `ability.yaml` 生成区 | `ghrah.ability.build.write_members(pkg)` 或 `SkillAbilityProvider(...).initialize()` |
| 两面一起看（自检用） | `scan_catalog()` |
| 装配期选择旧/新来源 | `AbilityAssembly.select(mode)` |

## 三个面与调用链

### 目录面：给模型看的上下文与工具索引

```
<扫描根>/<容器>/ability.yaml
      │  read_text(encoding="utf-8")
      ▼
(raw: str, *, source: str) -> parse_definition -> AbilityDefinition
      │                                                │
      │ _normalize_v1（v1 → 运行时形状）                 │ .operations / .skills
      ▼                                                ▼
AbilityEntry ──► (pool) -> scan_ability_definitions -> AbilityScanResult
                                                │ .containers（按容器名升序）
                                                ▼
                            AbilityContainer{name, title, operations, skills, issues}
                                                │
                 ┌──────────────────────────────┼───────────────────────────┐
                 ▼                              ▼                           ▼
      (pool) -> scan_abilities      (*, pool) -> scan_catalog      (pool) -> load_skill_catalog
       tuple[AbilityContainer]          CatalogReport                SkillCatalog
                                                                    │ (name) -> .read
                                                                    ▼
                                                              SkillDocument（正文原文）
```

### 实现面：tool call 落到真实实现

```
AbilityDefinition.operations[<key>]
      │  (container: str, operation_key: str)
      ▼
_operation_module -> import ghrah.ability.builtin.<容器>.operations.<key>
      │  (module)
      ▼
_operation_class -> type[Ability]（约定：一个脚本恰好一个实现类）
      │                                      └── 同时读模块级 PERMISSIONS / SUMMARY / TAGS
      ▼
(definition, key, operation) -> _project -> AbilityManifest
      │                                        ├── .tool: ToolSchema | None
      │                                        └── .metadata: AbilityMetadata
      ▼
() -> load_all_builtin_manifests() -> Mapping[full_name, AbilityManifest]
      │
      ├─ (handler: str) -> ability_class -> type[Ability]
      └─ (handler: str, **params) -> create_ability -> Ability（可执行实例）

工具参数唯一来源：ToolSchema ◄── _bound_tool ◄── instance.bind_tool() -> dict | None
（实现返回 None 即该成员不进工具面）
```

### 内容 / 授权面：内容寻址、授予、受控读取

```
(ability.yaml 文本, root: Path) -> capture_ability_version -> AbilityVersion
                                    │  ├─ parse_definition
                                    │  ├─ capture_bytes（逐成员 sha256，路径/链接/上限校验）
                                    │  └─ manifest_json（canonical JSON，成员 → 摘要）
                                    ▼
                        (actor, version) -> AbilityAccessStore.commit_version
                                    │  写 sqlite：ability_versions / ability_content / ability_changes
                                    ▼
                        (actor, agent, change) -> .grant / .revoke / .bind_tool / .unbind_tool
                                    │  返回 AbilityTreeView（协议视图：AbilityNode / SkillNode）
                                    ▼
                        AbilitySchemeHost(store).install(instance) -> Scheme Provider
                                    │
                                    ▼
             AbilityTreeSession.call("/scheme/ability/<alias>/<member>", "read")
                                    │
                                    ▼
   GrantedContentProvider(client, agent).read / .read_relative / .load_into_context
                                    │
                                    ▼
                          ContentReadResult（分块、UTF-8 或 base64）
```

## 1. 目录面：列出能力包

调用函数: `scan_abilities(pool=None) -> tuple[AbilityContainer, ...]`

```python
from ghrah.ability import scan_abilities

for container in scan_abilities():
    print(container)  # reporting (package:...) operations=2 skills=1
    for op in container.operations:
        print("  ", op.key, "|", op.title, "|", op.effects)
    for skill in container.skills:
        print("  ", skill.catalog_name, "|", skill.title, "|", skill.body_path)
```

辅助说明：

- 内置树（rank 0）与用户目录 `~/.ghrah/skill-ability`（rank 100）一起扫；同名容器 rank 小者胜出，落败副本在胜出者的 `issues` 里留一条。
- 只读定义面与目录结构，**不 import 实现脚本、不读整篇正文**；技能名与说明取正文 frontmatter。
- 成员类别都可不出现：`operations` 看 `<包>/operations/*.py`，`skill` 看 `<包>/skill/SKILL.md`（历史写法 `skills/` 也认），`references` / `assets` 看技能目录下是否存在这两个文件夹。
- **发现口径是磁盘**：包里有 `SKILL.md` 或 `operations/*.py` 就算能力包。`ability.yaml` 缺失或不受支持时不丢包——按目录与正文合成只读视图，`container.synthesized` 为 `True`，原因记在该包 `issues` 里。
- **技能面以 `SKILL.md` 为准**：定义面只是索引。盘上有正文而定义面没声明 → 按磁盘补出成员；声明的正文路径漂移 → 用盘上那份并记一条问题（重建生成区可消除）。
- `container.kinds` 给出本包实际有哪几类成员；`issues` 是本包自己的扫描问题。

## 2. 目录面：技能清单与正文

调用函数: `load_skill_catalog(pool=None) -> SkillCatalog`

```python
from ghrah.ability import load_skill_catalog

catalog = load_skill_catalog()
print(catalog.names, catalog.digest)
print(catalog.render())  # <system-reminder> ... </system-reminder>

doc = catalog.read("reporting")  # 目录名 / 成员键 / 容器名都能用
print(doc.path, doc.media_type, doc.content[:200])
```

辅助说明：

- `SkillCatalog.digest` 是属性，只覆盖“名字 + 一行说明”，正文变化不触发重新注入。
- `read()` 逐字返回正文（含 frontmatter），不裁剪、不解释；路径只认定义面声明的 `body`。
- `SkillAbilityProvider` 是同一实现的类门面，适合需要固定目录池的调用方：

```python
from ghrah.ability import SkillAbilityProvider

provider = SkillAbilityProvider.from_root("/path/to/skills")
provider.initialize()  # 补生成区 + 补 lock（见第 7 节）
```

## 3. 实现面：工具清单与实例

调用函数: `load_all_builtin_manifests() -> Mapping[str, AbilityManifest]`

```python
from ghrah.ability import load_all_builtin_manifests

for full_name, manifest in load_all_builtin_manifests().items():
    tool = manifest.tool
    print(full_name, "|", manifest.metadata.description)
    if tool is not None:
        print("   ", tool.name, sorted(tool.parameters))
        print("   ", tool.to_openai_tool())  # 直接塞进 tools=[...]
```

辅助说明：

- 只扫**内置树**且遇错直接抛：内置定义坏了是发布事故，不降级成问题条目。
- 工具参数**唯一来源是实现自己的 `bind_tool()`**；返回 `None` 的成员（如 `conversation`）没有工具，不会出现在清单里。
- `registry-only` 成员（`send`）不产出运行时视图。
- 每次调用重新投影；已 import 的 Python 模块按解释器规则复用，不热重载。

调用函数: `create_ability(handler, **params) -> Ability`

```python
from ghrah.ability import create_ability, ability_class

handler = "read_file"
print(ability_class(handler))  # <class '...fs.operations.read_file.ReadFileAbility'>
ability = create_ability(handler)  # 参数按实现构造函数给
print(ability.name, ability.bind_tool()["function"]["name"])
```

辅助说明：

- handler 就是成员键；唯一权威是 `ability.yaml` 的成员表，**没有 import 期注册表**。
- 构造参数随实现而异（如 `permission_checker=` / `command_checker=` / `hooks=`）。
- 只实例化，不授予、不执行：执行走执行器与权限层，不在本包内。

## 4. 内容 / 授权面：把一个包封成版本

调用函数: `capture_ability_version(raw, *, root, limits=CaptureLimits()) -> AbilityVersion`

```python
from pathlib import Path
from ghrah.ability.versions import capture_ability_version

root = Path("examples/ability_content/reporting")
version = capture_ability_version((root / "ability.yaml").read_text(), root=root)
print(version.digest, version.definition_digest, version.content_digest)
print(version.definition().identity.full_name)
```

辅助说明：

- 只捕获**定义面声明过**的成员：技能正文、`references` / `assets`、`operations/<key>/script`；未声明的文件不进清单。
- 逐成员 `sha256:` 摘要，`manifest_json` 是 canonical JSON；`blobs` 携带原始字节。
- 路径校验：绝对路径、`..`、反斜杠、符号链接一律拒绝。POSIX 用 `dir_fd + NOFOLLOW`，Windows 用“解析后仍在包根内 + 拒绝链接”的等价实现。
- 声明里给了 `digest` 就校验，不符即失败（内容与声明必须一致）。

调用函数: `capture_file_version(raw=None) -> AbilityVersion`

```python
from ghrah.ability.versions import capture_file_version

version = capture_file_version()  # 不给 raw 时取内置 fs 包
print(version.definition().operations.keys())  # dict_keys(['list_directory', 'read_file'])
```

辅助说明：

- 文件切片专用：只保留两个只读成员，并把成员重新绑到 `fs_backend` 契约上（它们由 `FileBackend` 直接复用仓库内实现，不执行快照脚本）。
- 输入契约取实现 `bind_tool()`，成功数据契约由 `FileMember` 声明。

## 5. 内容 / 授权面：提交、授予、绑定

调用函数: `AbilityAccessStore.open(path, project_id, *, operators, runs)`

```python
from ghrah.ability.access import AbilityAccessStore, AccessIdentity

store = await AbilityAccessStore.open(
    "project.db",
    "project",
    operators=frozenset({"admin"}),
    runs=lambda agent: "run-1" if agent == "alice" else None,
)
admin = AccessIdentity("project", "admin", "control", operator=True)
await store.commit_version(admin, version)  # 幂等：相同版本不重复写
await store.register_agent(admin, "alice")
await store.select_instance(admin, "reporting", version.digest, root)
```

```python
from ghrah.protocol.ability_tree import GrantAbilityRequest

view = await store.grant(
    admin,
    "alice",
    GrantAbilityRequest(
        alias="reports",
        instance_id="reporting",
        whole_container=True,
        allowed_roots=[str(root)],
        expected_revision=0,
    ),
)
print([a.alias for a in view.abilities])
```

辅助说明：

- 写侧只有这一条路径：版本提交 → 选择实例 → 登记 Agent → 授予 / 撤销 / 绑定工具。每次写都要 `expected_revision`，冲突报 `conflict` 而不是覆盖。
- `commit_version` 会核对 `manifest` 键集与定义推导出的成员集完全一致，并复核每个字节的摘要。
- 视图是投影，不是第二权威：`view(actor, agent)` 每次从 sqlite 重建。
- `select_instance(root=None)` 表示只读内容包（不需要文件根）。

## 6. 内容 / 授权面：受控读取

调用函数: `AbilitySchemeHost(store).install(instance)` + `session(principal)`

```python
from ghrah.ability.scheme import AbilitySchemeHost
from ghrah.scheme import Principal

host = AbilitySchemeHost(store)
await host.install("reporting")
client = host.session(Principal("project", "alice", "run-1"))
```

```python
from ghrah.ability.consumer import GrantedContentProvider

content = GrantedContentProvider(client, "alice")
print(await content.catalog_text())  # 模型可见的技能目录
doc = await content.read("reports", "skill/guide")  # 分块读正文
ref = await content.read_relative("reports", "guide", "references/style.md")
await content.load_into_context(context_manager, "reports", "skill/guide")
```

辅助说明：

- 成员地址：`skill/<技能键>`、`skill/<技能键>/assets/<名>`、`skill/<技能键>/references/<名>`、`operations/<键>`。
- 每次读取都过一遍 Scheme Provider 的权限复核：撤销后旧引用立即失效。
- 正文只有 `text/*` 且不超过 `max_bytes` 才允许注入 Context；二进制返回 base64，由调用方处理。

## 7. 构建与安装期：补生成区、补 lock

调用函数: `write_members(package, *, dry_run=False, bootstrap=False, migrate=False) -> MemberBuildResult`

```python
from ghrah.ability.build import scan_members, write_members

print(scan_members("path/to/reporting"))  # 只看这次会写什么
result = write_members("path/to/reporting")  # 写回 ability.yaml
print(result.changed, result.path)
```

辅助说明：

- `ability.yaml` 两段式：手写区（`format` / `schema_version` / `identity` / `title` / `description` / 可选 `provider_requirements`）逐字节保留；生成区从标记行起到文件尾整体替换。
- 扫描只读目录与实现脚本的模块级字面量（AST），不 import、不执行；同一份包重复生成结果一致。
- 扫不出来的作者意图会保留：`provider` 型成员、`expose: false`、非容器名的技能键、别名正文路径。
- `bootstrap=True`：包里还没有 `ability.yaml` 时按目录与 `SKILL.md` 补一份最小定义（容器名取目录名）。`migrate=True`：旧格式（`0.1-draft` / `0.2`）重写成 v1，身份/标题/说明从旧文件搬过来。
- 命令行等价：`python -m ghrah.ability.build <包目录> [--dry-run|--check|--migrate]`。

调用函数: `SkillAbilityProvider(...).initialize() -> SkillInitResult`

```python
from ghrah.ability import SkillAbilityProvider

result = SkillAbilityProvider.from_root("/path/to/skills").initialize()
print(result.scan.containers, result.locks, result.lock_issues)
```

辅助说明：

- 顺序：确保目录 → 扫描 → **补生成区**（写 `<容器>/ability.yaml`，没有定义的包补最小定义）→ 有写入则重扫 → 补缺失 lock（写 `<容器>/ability.lock.yaml`）。生成区与 lock 在同一生命周期、同一落点。
- 幂等且不覆盖：已同步的定义不重写，已有 lock 不覆盖（要重登用 `provider.relock(name)`）。

## 8. 装配期：选择能力来源

调用函数: `AbilityAssembly.select(mode, *, agent_store=None) -> AbilityAssembly`

```python
from ghrah.ability import AbilityAssembly

assembly = AbilityAssembly.select("builtin")
print(assembly.is_builtin, assembly.manifest_resolver())
```

辅助说明：

- `legacy` 走旧注册表 + 旧技能读取器；`builtin` 三者同源（声明、实例、内容读取），解析失败直接报错、不回退。
- 选择在装配点发生一次：构造 `BuiltinAbilitySource` 时就固定了声明快照，不会中途改接。

## 平台与性能

**分层**（依赖只朝下）：

```
ability_schema / skill_frontmatter        ← 模型与解析
        ▲
loading/member_index                      ← 纯扫描原语：目录 → members，只读
        ▲
loading/ability_scan（目录面）  ·  build（生成区 / 安装期写入 / ensure_members）
```

扫描层不 import 构建层：`import ghrah.ability.loading.ability_scan` 不会把 `build` 拉进来（有测试守着）。写入只发生在 `build` 里，由 `SkillAbilityProvider.initialize()` 在安装期惰性调用。

- 全部查询 API 无缓存：`ability.yaml` 改了下一次调用即生效；不要自行加 `lru_cache`（会引入第二权威）。
- 内置树单次目录面扫描约 6ms；用户池里若有"定义面缺失/旧格式"的包，每个包会额外做一次实现脚本 AST 扫描与正文读取（实测 12 个合成包约 +65ms）。实现面投影会 import 实现脚本，首次较慢。
- 内容捕获在 POSIX 上用 `dir_fd + NOFOLLOW`；Windows 用等价的可移植实现（解析后校验包根内 + 拒绝链接）。符号链接的**创建/替换**类测试在 Windows 上需要开发者模式或管理员权限。

## 相关文档

- [ability 包开发手册](./ghrah-ability-包开发手册.md)
- [模块设计文档（历史基线）](./scheme-ability模块设计文档.md)