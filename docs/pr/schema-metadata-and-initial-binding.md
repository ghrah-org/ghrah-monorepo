# fix(core): 修复工具 schema 元数据剥离误删同名字段，并收口初始绑定授权轴

## Summary

接入工作台页面能力（`workbench.page`，工具参数含 `html` / `page_title` / `data`）时，模型在
OpenAI 兼容端点（`/compatible-mode/v1/responses`）上调用工具直接返回：

```text
400 invalid JSON schema: required field 'title' is not defined in properties
```

根因在 `strip_schema_metadata`：该函数为兼容旧端点而递归删除所有层级的 `title` 与顶层
`description`，但 `properties` / `$defs` 这类**映射的键是字段名而非 schema 关键字**——当某个
工具参数恰好叫 `title`（或 `description`）时，属性定义被误删，而 `required` 仍引用它，schema
不再自洽，端点判为非法。

本 PR 净变更 3 个文件：

1. `ghrah-core`：schema 剥离只剥关键字，不再误删同名字段（附回归测试）；
2. `ghrah-subject`：Agent 初始装配时把每个成员的可见/绑定轴同时写入 `axes`，避免
   `GrantPlan` 与成员 `available` 轴不一致、把未选成员绑上工具面。

> 说明：分支历史里还包含工作台页面能力的实现与同分支回退提交（`35a24b92` →
> `09bfc7b0`，以及 `9c24a13f` → `12d0002b`），两者相互抵消，**净差异为 0**；审阅时可忽略，
> 只看本文档描述的 3 个文件。

## 变更点

### 1. `ghrah-core/src/ghrah/ability/package/schema.py`

```diff
+_SCHEMA_MAP_KEYWORDS = frozenset({"properties", "$defs", "definitions", "patternProperties"})
+
 def strip_schema_metadata(node: Any, *, top_level: bool = True) -> Any:
     if isinstance(node, dict):
-        return {
-            key: strip_schema_metadata(value, top_level=False)
-            for key, value in node.items()
-            if key != "title" and not (key == "description" and top_level)
-        }
+        result: dict[str, Any] = {}
+        for key, value in node.items():
+            if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
+                # 映射的键是字段名，原样保留
+                result[key] = {
+                    name: strip_schema_metadata(subschema, top_level=False)
+                    for name, subschema in value.items()
+                }
+                continue
+            if key == "title" or key == "description" and top_level:
+                continue
+            result[key] = strip_schema_metadata(value, top_level=False)
+        return result
```

- `properties` / `$defs` / `definitions` / `patternProperties` 的键不再参与"关键字剥离"；
- 子 schema 内部真实的 `title` / 顶层 `description` 仍然按原约定剥离；
- 语义不变的部分：列表、标量递归逻辑保持原样。

### 2. `ghrah-core/tests/ability/package/test_schema_metadata.py`（新增）

- 字段名恰为 `title` / `description` 时，`properties` 与 `required` 必须完整保留；
- 模型级 `title` 与顶层 `description` 仍被剥离；
- 覆盖 `properties` 内 `title` 子 schema 的嵌套场景。

### 3. `ghrah-subject/src/ghrah/subject/agents/render.py`

```diff
                 control = package == "ghrah.core" and key in AGENT_CONTROL_MEMBER_KEYS
                 bind = control or member.member in bindings
+                axes[f"operations/{key}"] = (visible, bind)
...
             skill_bind = True if bindings is None else member.member in bindings
+            if bindings is not None:
+                axes[f"skill/{member.key}"] = (True, skill_bind)
```

- 初始装配时把每个 operation / skill 成员的 `(visible, bind)` 写入 `axes`；
- 否则 `GrantPlan` 仍按 `(True, True)` 生成授权，与成员 `available` 轴不一致，工具面会出现
  未选成员（例如未勾选的组件/工具被暴露给模型）。

## Evidence

### Before

页面上调用 `workbench.page.render` 时端点直接拒绝：

```text
400 invalid JSON schema
required field 'title' is not defined in properties
```

`strip_schema_metadata` 处理 `{"properties": {"title": {...}}, "required": ["title"]}` 后得到：

```json
{"properties": {}, "required": ["title"]}
```

### After

```json
{"properties": {"title": {"type": "string"}}, "required": ["title"]}
```

同一工具在端点正常调用；工作台页面渲染链路（tool_call → 行动链事件 → 前端）验证通过。

### 测试

```text
ghrah-core/tests/ability/package/test_schema_metadata.py     2 passed
ghrah-core/tests/ability/package                           266 passed, 13 skipped
ghrah-subject/tests -k 'render or assembly or binding'      28 passed, 774 deselected
ghrah-protocol + ghrah-subject                             1071 passed, 23 skipped
ruff check ghrah-protocol ghrah-subject ghrah-core          全绿
```

## Merge Danger

- **行为收敛**：字段名恰为 `title` / `description` 的 schema 属性不再被剥离。若有下游逻辑
  依赖"这类字段被删"（例如旧工具契约通过删除字段来规避端点校验），合并后行为会变——应改为
  调整参数命名或 schema 本身。
- **`$defs` 内字段名**同样保留；嵌套结构中的真实 `title` 元数据仍被剥离，不会重新把
  `title` 关键字带回去。
- **初始绑定轴**：合并后未授权成员不会再出现在工具面。若有测试/脚本依赖"初始装配即全量绑定"
  的旧行为，需要一并更新。
- 分支中的页面能力提交已被同分支回退，**不存在运行时页面状态变更**；本 PR 不包含后端页面
  数据库、命令或事件。