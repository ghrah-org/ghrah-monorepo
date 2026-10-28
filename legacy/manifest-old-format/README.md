# legacy/manifest-old-format —— 旧格式能力声明栈（冻结快照）

**冻结日期**：2026-10-26
**冻结原因**：能力定义面已切换到新格式（`ghrah.ability-definition`），旧格式（`manifest: ability`）不再是能力的定义来源。
**状态**：**只读备份**。本目录不属于任何 Python 包，不被导入、不参与构建、不参与测试、不被 lint 门禁之外的流程引用。

## 这里有什么

| 文件 | 原位置 | 内容 |
| --- | --- | --- |
| `manifest_source.py` | `ghrah-core/src/ghrah/scheme/manifest_source.py` | 旧格式的文本→模型解析（`parse_ability_manifest` / `parse_agent_manifest` / `validate_manifest`）+ 取用协议 `ManifestStoreProtocol` + 内置实现 `BuiltinManifestStore` |
| `manifest_responses.py` | `ghrah-core/src/ghrah/scheme/manifest_responses.py` | 7 个旧声明面的对外 wire DTO（`AbilityManifestData` / `AgentManifestData` / `ManifestPutResult` / `ManifestDeleteResult` / `ValidateResult` / `ResolvedAbilityData` / `ResolvedAgentData`）——已核实零消费者 |
| `sample-ability.yaml` | — | 一份**真实**的旧格式能力声明（由投影结果经 subject 落盘时的同一条 `model_dump → yaml.dump` 路径生成），用于看清旧格式字段形状 |

冻结的是**副本**：原文件当时仍在 `ghrah/scheme/` 中（见下"删除进度"）。

## 关键事实：旧格式此刻是 subject 的落盘格式，不是定义来源

定义来源已经唯一：`ghrah.scheme.ability_loader` 只扫描 `ability/<容器>/ability.yaml`（新格式）。
但旧格式仍在一条**回路**里活着：

```
ability/<容器>/ability.yaml（新格式，定义）
   │  ability_loader 投影
   ▼
AbilityManifest（运行时视图）
   │  subject/manifest_store/builtins.py:39-40  序列化成旧格式 YAML
   ▼
{root}/abilities/{ns}/{name}.yaml（旧格式，落盘）
   │  subject/manifest_store/store.py:80  parse_ability_manifest 读回
   ▼
AbilityManifest（再次）
```

证据（均为当前代码）：

| 位置 | 用途 |
| --- | --- |
| `ghrah-subject/src/ghrah/subject/manifest_store/builtins.py:39-40` | `ensure_builtins` 把 20 份投影结果**序列化成旧格式 YAML** 写入 store |
| `ghrah-subject/src/ghrah/subject/manifest_store/store.py:80` | `get_ability` → `parse_ability_manifest(content)` |
| `ghrah-subject/src/ghrah/subject/manifest_store/store.py:203` | `_put_entry` → `validate_manifest(content)`，并强制 `manifest == "ability"` |
| `ghrah-subject/src/ghrah/subject/manifest_store/service.py:209` | `manifest_validate` 命令 |
| `ghrah-core/examples/agents_manifest/runner.py:39,77` | 示例直接解析旧格式能力声明 |
| `ghrah-core/tests/manifest/test_parser.py`、`ghrah-subject/tests/test_manifest_store.py` | 覆盖旧格式解析与校验 |

**不可删的部分**：`AbilityManifest` / `AbilityMetadata` / `AbilityHooks` / `ToolSchema` / `ToolParameter` / `PermissionFlags` /
`ImplementationDef` 在 `ghrah.scheme.manifest_models` 中**必须保留**——它们是投影目标与运行时视图，被 56 个文件消费。
"旧格式的加载"指的是**解析旧格式文本**这件事，不是这些模型。

## 删除进度

| 步骤 | 状态 | 说明 |
| --- | --- | --- |
| ① `manifest_responses.py` 的 7 个 DTO | **已从活树删除**（2026-10-26） | 核实零消费者；同时删除了 `ghrah/manifest/responses.py` 别名 shim 与 `ghrah/manifest/__init__.py` 中的转发 |
| ② `parse_ability_manifest` / `validate_manifest` 的能力分支 / `BuiltinManifestStore` | **未删**，卡在一个决定上 | 见下 |

### ② 的前置决定：subject 的能力存储走哪种格式

- **方案 A：subject 直接吃新格式**（不再为每个内置能力落盘一份旧格式 YAML）
  需要改：`ensure_builtins` 的 seed 路径、`get_ability` / `put_ability` / `validate_ability` / `delete_ability`、
  `manifest_list_abilities` / `manifest_get_ability` 的输出形状、以及相应测试。
  改完之后，core 侧 `parse_ability_manifest`、`validate_manifest` 的能力分支、`BuiltinManifestStore` 都可以删除，
  本目录即可整体移出仓库。
- **方案 B：subject 保留旧格式落盘**
  那么 core 侧解析器**必须保留**——它服务的是"运行时视图的落盘格式"，与"能力的定义加载"是两件事。
  此时本目录只作为历史参考，可随时删除。

推荐先做方案 A 的最小版本：内置能力不再落盘（`get_ability` 未命中时回落到 `ability_loader` 的投影），
只保留用户自建能力的 CRUD；这样既能删掉旧格式解析的内置路径，也不用先改 subject 的对外命令形状。

## 旧格式 vs 新格式（字段对照）

| 旧格式（`manifest: ability`） | 新格式（`ghrah.ability-definition`） |
| --- | --- |
| `metadata.namespace` + `metadata.name`（两者拼出 `ghrah.fs.read_file`） | `identity.namespace` + `identity.name`（容器）+ `operations.<键>`（成员） |
| `metadata.title` / `metadata.description` | `title`；成员的 `description`，目录摘要见实现侧 `SUMMARY` |
| `metadata.permissions` | **无**（授权归 GrantRecord）；暂存实现侧 `PERMISSIONS` |
| `metadata.tags` / `icon` / `deprecated` / `since` | **无**；`tags` 暂存实现侧 |
| `tool.name` / `tool.parameters`（自定义参数方言） | 成员的 `input_schema`（JSON Schema）；工具名由投影器生成 |
| `implementation.handler` | `implementation.provider_slot` + `implementation.operation` |
| `hooks` | **无**（草案不把 Hook 编排写进定义）；暂存实现侧 `HOOKS` |
| `version: "1"`（格式版本） | `schema_version: 0.1-draft`（格式）+ `identity.version`（能力版本） |
| 无 | `provider_requirements` / `output_schema` / `errors` / `effects` / `resources` / `execution` |