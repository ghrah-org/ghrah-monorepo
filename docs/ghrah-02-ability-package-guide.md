# 能力包开发指南

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-GUIDE-002 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `f8ff157`（2026-10-28），`ghrah-core` 0.2.0a0 |
| 读者 | 要新增一个工具（能力）、要改现有能力行为、要给能力加权限或审批的开发者 |
| 取代 | `package技术文档.md`、`package增量开发指引.md`（归档至 `docs/archive/`） |
| 相关文档 | `ghrah-01-ability-design-and-implementation.md`（为什么这样设计、契约全貌）、`ghrah-04-skill-system.md`（技能成员）、`ghrah-06-code-map.md` §8（新文件放哪） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。以 `read_file` 为参照实现，给出可直接抄的模板、字段表、权限写法、测试点与错误对照 |

## 1. 你要产出什么

一个能力包 = 一个目录，至少两个文件：

```
ghrah-core/src/ghrah/scheme/ability/<容器名>/
├── ability.yaml              # 定义面：身份 + 成员契约
└── operations/<成员键>.py    # 实现面：Ability 子类 + 四件套常量
```

可选：`skills/SKILL.md`（技能包，一个容器至多一个）、`mcp/*.yaml`（MCP 声明）、`ability.lock.yaml`（框架生成，不手写）。

三件事必须同时成立，否则包加载不上：

1. 目录名 == `identity.name or identity.namespace`；
2. `operations` 的成员键 == `implementation.operation`；
3. 每个 `operations/<键>.py` 里**恰好一个** `Ability` 子类，且该类的 `__module__` 就是本模块。

## 2. 五分钟最小可行包

`ghrah-core/src/ghrah/scheme/ability/hello/ability.yaml`：

```yaml
format: ghrah.ability-definition
schema_version: 0.1-draft
identity:
  namespace: ghrah
  name: hello                 # 必须等于目录名 hello
  version: 0.1.0
title: Hello
description: Demo container with a single echo tool.
operations:
  hello_echo:
    title: Hello Echo
    description: Echo the given text back to the caller.
    input_schema:
      type: object
      properties:
        text:
          type: string
          description: Text to echo
      required: [text]
      additionalProperties: false
    output_schema:
      type: object
      properties:
        text:
          type: string
      required: [text]
      additionalProperties: false
    errors: {}
    implementation: {provider_slot: hello_backend, operation: hello_echo}
    effects: []
    resources: {}
    execution:
      mode: request
      timeout_ms: 30000
      cancellation: unsupported
      on_revoke: drain
      retry: never
    expose: true
provider_requirements:
  hello_backend:
    contract: ghrah.hello.v1
    required_capabilities: [request]
```

`operations/hello_echo.py`：

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ghrah.scheme.execution.base import Ability
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme.execution.context import AbilityExecutionContext
    from ghrah.scheme.execution.hooks import Hook

PERMISSIONS: dict[str, Any] = {}
HOOKS: dict[str, Any] = {}
SUMMARY = "Echo the given text back to the caller"
TAGS = ["demo"]


class HelloEchoInput(BaseModel):
    text: str


class HelloEchoAbility(Ability):
    @property
    def name(self) -> str:
        return "hello_echo"

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        args = context.tool_args or context.accumulated_data.get("tool_args", {})
        text = str(args.get("text", ""))
        if not text:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "text is required"},
            )
        return ActionResult(outcome=ActionOutcome.SUCCESS, data={"text": text})
```

自检：

```bash
cd ghrah-monorepo
uv run pytest ghrah-core/tests/scheme/loading -q
uv run ruff check ghrah-core/src/ghrah/scheme/ability/hello
```

`load_ability_definitions()` 若抛 `ManifestValidationError`，说明定义面有问题（错误消息会指向具体字段）；若抛 `期望恰好一个 Ability 实现类，实际 N 个`，说明实现面有问题（见 §10）。

## 3. 命名与目录规则

| 规则 | 原因 | 依据 |
| --- | --- | --- |
| 目录名 == 容器名 | 目录名只作定位，身份以定义面为准，两者必须相等 | `scheme/loading/ability_scan.py:505-516` |
| `namespace` 允许 `-` 与 `_`（`^[a-z][a-z0-9_-]*$`） | 它是逻辑命名空间 | `scheme/loading/ability_schema.py:155` |
| `name` 只允许 `[a-z0-9_]` | 它参与 Python 包路径 `scheme/ability/<容器>/operations/<键>.py` | `:156` |
| 成员键（`operations` 的 key）== 工具名 | 工具名就是成员键，不加前缀 | `01` §8.2 |
| 成员键 == `implementation.operation` | 跨字段校验 2 | `:127-132` |
| 一个容器至多一个技能 | 跨字段校验 4 | `:136-142` |

**不要**：新建 `ghrah-core/src/ghrah/abilities/` 旧目录、写 `manifest: ability` 文本、在 Python 里注册容器、给工具名加 `fs__` 之类的命名空间前缀。

## 4. `ability.yaml` 逐字段写法

### 4.1 顶层

| 字段 | 必填 | 写法要点 |
| --- | --- | --- |
| `format` | 是 | 恒为 `ghrah.ability-definition` |
| `schema_version` | 是 | 恒为 `0.1-draft`；其他值报错 |
| `identity` | 是 | `namespace`、`version` 必填；`name` 可选但建议写 |
| `title` | 是 | 人看的短名（容器级） |
| `description` | 是 | 一句话说明容器做什么 |
| `provider_requirements` | 否 | 声明槽；成员用到哪个槽就先在这里声明 |
| `operations` | 否 | 工具成员 |
| `skills` | 否 | 技能成员（至多 1 个） |
| `assets` | 否 | 无字段契约，内容原样持有 |

### 4.2 `operations.<键>`

| 字段 | 必填 | 写法要点 |
| --- | --- | --- |
| `title` | 是 | 简短标题 |
| `description` | 是 | 声明面/人读视图用的摘要。**模型实际读到的是 `bind_tool()` 里那份 description**（`load_skill.py:117-123`），两处都要写、都要写清"什么时候用/边界"（D-14） |
| `input_schema` | 是 | JSON Schema；`additionalProperties: false` 是惯例。**不参与入参校验**，校验靠 `bind_tool()` 里的 pydantic 模型（§5.3） |
| `output_schema` | 是 | 同上；模型不直接看，但它是契约 |
| `errors` | 否 | 错误码/说明的映射，当前不参与运行时 |
| `implementation` | 是 | `{provider_slot, operation}`，两个都必填 |
| `effects` | 否 | 副作用标签，当前不参与运行时 |
| `resources` | 否 | 资源声明，当前不参与运行时 |
| `execution` | 是 | 见 §4.3 |
| `expose` | 否 | 默认 `true`；`false` 表示只在内部可用（不进工具面） |

### 4.3 `execution`

```yaml
execution:
  mode: request            # 首版只有 request
  timeout_ms: 60000        # 必填 >0
  cancellation: unsupported # 或 cooperative
  on_revoke: drain         # 或 request_cancel
  retry: never             # 只有 never
```

**`timeout_ms` 当前不生效**：执行器不给 `ability.execute()` 包超时（`scheme/execution/executor.py:400`，唯一 `wait_for` 在 `:710` 只用于 HITL）。写它是为了契约完整，不要以为填了 60s 就真会 60s 中断（`01` §13.2 D-1）。同理 `cancellation`/`on_revoke`/`retry` 目前都没有运行时消费者。

### 4.4 `expose` 的决策

| 场景 | 取值 | 例子 |
| --- | --- | --- |
| 模型应能直接调用 | `true`（缺省） | `read_file`、`execute_command` |
| 只在内部被其他代码调 | `false` | `conversation`（`core/ability.yaml:42`）、`send`（`cluster/ability.yaml:293-297`） |
| 想让工具"存在但不可见"又保留能力 | `false` + 用 `create_ability()` 手动取 | `send` 即此类 |

`expose: false` 的成员仍然在 `operation_containers()`/`builtin_handlers()` 里，仍可 `create_ability()`。

## 5. 实现脚本模板

`scheme/ability/fs/operations/read_file.py` 是完整参照（237 行），结构固定为五段：

```python
# 1) 导入：Ability、permissions（按需）、types.results、TYPE_CHECKING 的 context/hooks
from ghrah.scheme.execution.base import Ability
from ghrah.scheme.permissions.fs_permissions import FSPermissionChecker
from ghrah.types.results import ActionOutcome, ActionResult

# 2) 四件套常量（投影期强校验，缺一即失败）
PERMISSIONS = {"fs_read_only": True}
HOOKS = {}
SUMMARY = "Read the content of a file at the specified path"
TAGS = ["filesystem", "read"]

# 3) Input 模型（仅作 schema 的事实来源之一；工具面实际读 ability.yaml）
class ReadFileInput(BaseModel):
    file_path: str
    encoding: str = "utf-8"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=0, ge=0)

# 4) Ability 子类
class ReadFileAbility(Ability):
    @property
    def name(self) -> str: return "read_file"
    def get_hooks(self) -> list[Hook]: return list(self._hooks)
    async def execute(self, context: AbilityExecutionContext) -> ActionResult: ...

# 5) 私有辅助函数（下划线开头，不会被当成实现类）
def _number_lines(lines, *, start_no: int) -> str: ...
```

### 5.1 四件套常量

| 常量 | 类型 | 投影去向 | 位置 |
| --- | --- | --- | --- |
| `PERMISSIONS` | `dict` | `AbilityManifest.metadata.permissions` | `scheme/ability/fs/operations/read_file.py:45-47` |
| `HOOKS` | `dict` | `AbilityManifest.hooks` | `:48` |
| `SUMMARY` | `str` | `metadata.description`（空串回落 `operation.description`） | `:54` |
| `TAGS` | `list[str]` | `metadata.tags` | `:55-58` |

缺任一个 → `ManifestValidationError`（`scheme/loading/ability_catalog.py:363-374`）。**注意 `SUMMARY` 与 YAML `description` 是两个不同的字符串**：前者进列表视图，后者进工具描述。改文案要改对地方。

### 5.2 `execute` 的写法

```python
async def execute(self, context: AbilityExecutionContext) -> ActionResult:
    # a) 取参数：优先 context.tool_args，兼容 accumulated_data["tool_args"]
    args = context.tool_args or context.accumulated_data.get("tool_args", {})
    file_path = args.get("file_path", "")

    # b) 自己做参数校验（执行器不做 schema 校验）
    if not file_path:
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "file_path is required"})

    # c) 自己做权限检查（执行器不做权限检查）
    if self._checker is not None:
        allowed, reason = self._checker.check_read_path(file_path)
        if not allowed:
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": reason})

    # d) 业务逻辑 + 异常兜底（执行器不 try/except）
    try:
        ...
    except FileNotFoundError:
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": f"File not found: {file_path}"})
    except Exception as e:
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": str(e)})

    # e) 成功
    return ActionResult(outcome=ActionOutcome.SUCCESS, data={...})
```

四条铁律（都源于执行器"什么都不做"）：

1. **参数校验**自己做——`execute_ability` 不校验 schema（`executor.py:302-413`）；
2. **权限**自己做——执行器只有 PRE_EXECUTE hook 与 HITL 两个闸门，没有权限阶段；
3. **异常**自己兜——`execute_ability` 无 try/except，抛出会被 `execute_tool_calls` 折成 `FAILURE{"error": str(e)}`，但消息会丢栈；
4. **返回值**必须是 `ActionResult`，不要返回 dict 或字符串。

### 5.3 `bind_tool()` 必须写

基类默认返回 `None`（`scheme/execution/base.py:70-81`），而 `None` 的后果是**这个工具从模型视野里消失**：`register_ability()` 只在 `bind_tool()` 返回非 `None` 时才把 schema 追加进 `_bound_tools`（`agents/base.py:235-237`），`configure_tools()` 发出去的就是这份列表。所以：

- **新增成员：必须实现 `bind_tool()`**，返回 `{"type": "function", "function": {"name", "description", "parameters"}}`，`parameters` 用输入模型的 `model_json_schema()`（照抄 `load_skill.py:112-126` 即可）。返回 `None` 等于把这个能力做成"只能被别的能力内部调用"——`conversation` 是唯一这样的例子，它有意不出现在工具面。
- **`ability.yaml` 的 `input_schema` 是文档性载荷**，没有代码拿它校验入参；模型看到的是 `bind_tool()` 的 `parameters`。两份都要写，但**以实现面为准**（`ghrah-01` §8.4 有 18/19 漂移实测）。
- 维护老成员：`read_file.py:124-140` 的 schema 与 `fs/ability.yaml:30-51` 内容相近但不保证同步（D-14）。改入参模型时两份一起看。

## 6. 权限：`PERMISSIONS` 怎么写

`PERMISSIONS` 的字段集合与旧 manifest `metadata.permissions` 一致（`scheme/loading/manifest_fields.py:60-71`）：

| 键 | 类型 | 默认 | 语义 |
| --- | --- | --- | --- |
| `require_hitl` | `bool` | `False` | 是否要人工审批 |
| `fs_read_only` | `bool` | `False` | 只读文件能力 |
| `fs_write` | `bool` | `False` | 写文件能力 |
| `net_access` | `bool` | `False` | 网络访问 |
| `shell_access` | `bool` | `False` | 执行命令 |
| `allowed_paths` | `list[str]` | `[]` | 白名单；支持 `{{workspace}}` 模板 |
| `denied_paths` | `list[str]` | `[]` | 黑名单（硬拒绝，优先于白名单） |
| `allowed_commands` | `list[str]` | `[]` | 命令白名单 |
| `denied_commands` | `list[str]` | `[]` | 命令黑名单 |

合并语义（`manifest_fields.py:73-97`）：布尔取 **OR**、`allowed_*` **替换**、`denied_*` **追加**。

### 6.1 装配期会发生什么

`PERMISSIONS` 被读进 `PermissionFlags` 后，装配点按能力类型分派（**两个装配点行为不完全一致**，见 `01` §12.3）：

| 能力类型 | 判定 | 注入 |
| --- | --- | --- |
| `handler ∈ FS_ABILITY_TYPES` **或** 带任一 `fs_*` 字段 | `require_approval = auto_approved ? False : (manifest.require_hitl ?? default)` | `FSPermissionChecker` + （`require_approval` 为真时）`AccessApprovalHook` |
| `handler == "execute_command"` | `require_approval = auto_approved ? False : (manifest.require_hitl ?? default)` | `CommandSafetyChecker` + **恒挂** `CommandApprovalHook` |
| 其他 | — | `require_hitl` 为真时包一层 `HITLRequiredAbility`（带 `HITLApprovalHook`） |

`default` 取值：装配点 A（`unit.py:504`、`:527`）用 `require_approval_by_default`（默认 `True`）；装配点 B（`ability_build.py:219`）的 FS 分支**不读**它（默认落到 `False`）。所以同一个 `require_hitl: false` 的 FS 能力，在两条路径下可能"要审批"或"直接拒绝"。

### 6.2 写权限的三个反直觉点

1. **`pending` 在内联检查中等同允许**：`read_file.py:182-188` 把 `(True, "pending")` 直接放行，靠 hook 把它转成 HITL 等待。若装配时没挂 hook，就是越权静默通过（`01` D-10）。
2. **读写不分级**：`check_access(path, operation)` 的 `operation` 形参从未使用（`fs_permissions.py:136-171`），读写同构；`fs_read_only`/`fs_write` 只作为"是否进 FS 分支"的触发条件。
3. **`move_file` 只审 destination**：`_extract_target_path` 返回单个路径（`:294-299`），source 只由能力内联 `check_read_path` 判定，白名单外的 source 不会触发审批（D-11）。

### 6.3 命令类能力

`execute_command` 的分级来自 `CommandSafetyChecker`（`scheme/permissions/command_safety.py`）：`SAFE` / `DANGEROUS` / `REQUIRE_HITL` 三种（`:53-56`）。判定顺序：空命令 → DANGEROUS；base 在危险表 → DANGEROUS；有子命令路由时按子命令判；base 在安全表 → SAFE；`require_approval=False` → SAFE（危险名单仍生效）；兜底 REQUIRE_HITL。

内置安全表约 50 条、危险表约 25 条、子命令表覆盖 git/npm/pip/uv/docker/kubectl/cargo（`:67-241`）。扩展只能"只增不减"：`extend_safe_lists()`（`:279-300`，由 `CoreUnitConfig.safe_extra_commands` 注入，`unit.py:530-535`）。

写新的命令类能力时：**不要自己解析命令安全**，挂 `CommandApprovalHook` 并传 `CommandSafetyChecker` 即可。

## 7. 测试与自检

### 7.1 测试放哪

| 被测内容 | 测试目录 | 现有示例 |
| --- | --- | --- |
| 定义面解析、校验、根池 | `ghrah-core/tests/scheme/loading/` | `test_ability_scan.py`、`test_ability_catalog.py`、`test_ability_containers.py`、`test_ability_roots.py` |
| 技能 | 同上 | `test_skill_context.py`、`test_skill_lifecycle.py` |
| 执行器与上下文 | `ghrah-core/tests/abilities/` | `test_ability_executor.py`、`test_ability_context_facade.py` |
| 单个内置能力 | `ghrah-core/tests/abilities/builtin/` | `test_load_skill.py` |
| 技能注入 | `ghrah-core/tests/context/` | `test_skill_catalog_injection.py` |
| 旧 manifest 兼容 | `ghrah-core/tests/manifest/` | `test_ability.py` |

pytest 配置：`asyncio_mode = "auto"`、`testpaths = ["tests"]`（`ghrah-core/pyproject.toml:47-49`）——异步测试不需要 `@pytest.mark.asyncio`。

### 7.2 新增一个能力最少要测什么

| 必测 | 断言什么 |
| --- | --- |
| 定义面可解析 | `parse_definition()` / `load_ability_definitions()` 不抛，且 `implementation.operation == 键` |
| 实现类可解析 | `ability_class("<键>")` 返回你的类 |
| 工具面可见性 | `expose` 与 `build_tool_schema()` 是否一致（`expose=false` 应返回 `None`） |
| 成功路径 | `execute()` 返回 `SUCCESS` 且 `data` 含约定字段 |
| 参数缺失 | 返回 `FAILURE` 且 `data["error"]` 是可直接展示的原因 |
| 权限分支（若声明了 `fs_*`） | 白名单内放行 / 白名单外 `FAILURE` / `pending` 时挂 hook 会 `hitl()` |

### 7.3 命令

```bash
cd ghrah-monorepo
uv run pytest ghrah-core/tests/scheme/loading -q          # 加载面
uv run pytest ghrah-core/tests/abilities -q              # 执行面
uv run ruff check ghrah-core/src/ghrah/scheme            # lint（E,F,I,N,W,UP，行宽 100）
uv run ruff format ghrah-core/src/ghrah/scheme           # 格式化
python scripts/check_docstrings.py --summary             # docstring 覆盖
```

`ruff` 规则见 `ghrah-core/pyproject.toml:40-45`；`mypy` 是 `strict = true`（`:51-53`），但当前 CI 未强制。

## 8. 内置包清单

| 容器 | 成员数 | handler | 说明 |
| --- | --- | --- | --- |
| `fs` | 7 | `read_file` `write_file` `edit_file` `delete_file` `move_file` `list_directory` `search_files` | 文件系统；全部进 `FS_ABILITY_TYPES` |
| `core` | 6 | `conversation`(expose:false) `end_task` `list_abilities` `load_skill` `recall_context` `session_info` | 会话基础能力 |
| `cluster` | 7 | `query_agents` `send_message` `broadcast_message` `spawn_agent` `terminate_agent` `query_manifests` `send`(expose:false, registry-only) | 多 agent 协作 |
| `shell` | 1 | `execute_command` | 命令执行；走命令分级 |
| `mcp` | 1 | `mcp_call` | MCP 工具桥 |

口径（仓库内置树，实测于 `f8ff157`）：handler 全集 **22**；工具面 **20**（去掉 `conversation` 与 `send`）；`AbilityManifest` 视图 **21**（只去掉 registry-only 的 `send`）。用户根 `~/.ghrah/skill-ability` 下还可以装 skill-only 容器，它们不产生 operation，因此不改变这三个数字。

## 9. 常见错误对照

| 错误消息（原文） | 原因 | 处置 |
| --- | --- | --- |
| `不是映射` | YAML 顶层不是 `dict` | 检查缩进/是否漏了 `:` |
| `容器名 X 与目录名 Y 不一致（目录名只作定位，身份以定义面为准，两者必须相等）` | 目录名与 `identity.name` 不符 | 改名一侧（通常改目录） |
| `implementation.operation` 相关校验失败 | 成员键与 `implementation.operation` 不一致 | 两处对齐 |
| `provider_slot` 未声明 | `implementation.provider_slot` 不在 `provider_requirements` | 在 `provider_requirements` 补一条 |
| `一个能力包只支持一个 skill 成员…` | 声明了 2 个以上 `skills` | 拆包 |
| `skills.{key}: 正文路径非法（不能越出容器目录）：…` | `body.path` 含 `..` | 改成容器内相对路径 |
| `skills.{key}: 声明的正文不存在：…` | 文件缺失 | 创建 `skills/SKILL.md` |
| `期望恰好一个 Ability 实现类，实际 N 个` | 同一脚本里有多个 `Ability` 子类，或 import 了别处的 `Ability` 子类 | 拆文件；只留一个类；辅助类改名/移出 |
| `未知的 ability handler: 'x'。已知: (…)` | handler 不在任何容器的 `operations` 里 | 检查 YAML 成员键与文件名 |
| `未知的 ability 容器：'x'` | 容器名拼错或没有被扫到 | 检查目录名与扫描根 |
| `重复的工具名 'x'：已由容器 'y' 提供，本条被忽略` | 跨容器撞名 | 改成员键（工具名全局唯一，无前缀机制） |
| `未知的内置 ability 全名：'x'` | 用了点分全名但该成员是 registry-only | 改用 handler |
| `PERMISSIONS`/`HOOKS`/`SUMMARY`/`TAGS` 缺失 | 四件套不全 | 补齐模块级常量 |
| `Unknown ability: x`（运行时） | 模型调了不存在的工具 | 检查工具面与 `expose` |
| `Execution blocked by hook` | PRE_EXECUTE hook 返回 `stop()` 但没给 message | 检查权限 hook 的目标路径提取 |
| `Permission denied: … not in allowed paths; allowed roots: …` | 路径不在白名单/工作区 | 配 `allowed_paths` 或 `workspace_root` |
| `HITL approval timed out after 300.0s with no response delivered; …` | 没人回执且 `hitl_timeout=300` | 前端回执，或调 `CoreUnitConfig.hitl_timeout` |
| `HITL request rejected by human: …` | 审批人拒绝 | 正常路径；`data["hitl_rejected"]=True` |

## 10. Review 清单

提交能力包 PR 前逐条勾：

- [ ] 目录名 == `identity.name`；`name` 只含 `[a-z0-9_]`
- [ ] 每个成员 `implementation.operation` == 成员键；`provider_slot` 已声明
- [ ] 每个 `operations/*.py` 恰好一个 `Ability` 子类，`name` 属性返回成员键
- [ ] 四件套常量齐全；`SUMMARY` 与 YAML `description` 都已写且不互相矛盾
- [ ] `description` 写的是"何时用、参数含义、边界"，不是实现细节
- [ ] `execute` 内做了参数校验、权限检查（若适用）、异常兜底，返回 `ActionResult`
- [ ] 若声明 `fs_*`：确认 `pending` 语义（挂 hook 才是审批，否则等于放行）
- [ ] 若声明 `require_hitl`：确认在两条装配路径下行为一致（或已知差异）
- [ ] `expose` 选择有理由；`expose:false` 的成员有人能调到
- [ ] 新增/修改了测试；`uv run pytest ghrah-core/tests -q` 与 `ruff check` 通过
- [ ] 没有写 `manifest: ability`、没有新建 `abilities/` 目录、没有引用 `plans/`
- [ ] 实现了 `bind_tool()`（返回 `None` 会让工具对模型不可见），且 description 与 `ability.yaml` 的 `description` 口径一致