# feat(mcp)!: 成员驱动的 MCP 安装、执行与提供商复用

## Summary

MCP 从「内置 `ghrah.mcp` 包 + 模型可见 `mcp_call` + 包级 binding」改成**安装快照生成独立 MCP 成员**：每个选中的远端工具都是 `mcp.<ability>.<tool>`，执行期由成员自己冻结的 `server/tool` 调用。

```text
安装
  POST /api/v1/ability-packages/mcp/install
    ├─ 解析连接（新建密钥 / 复用已有 MCP Provider）
    ├─ probe tools/list
    ├─ 生成 mcp.<name>@<version> 能力包（每个工具一个 type=mcp 成员）
    └─ 写 agentconf Server，引用 Provider

运行
  Agent 启动
    └─ McpMemberDeployment
         └─ MemberMcpService.call_member(server, tool)
              └─ McpRuntime → 远端 MCP Server
```

### 主要变更

- **Core / 协议**
  - 新增 `McpRuntimeConfig`、`McpMemberDeployment`、`MemberMcpService`，`mcp_call` 变成内部执行适配，不再是模型可见工具。
  - 删除内置 `ghrah.mcp` 包、`list_abilities` 与旧 `McpBinding` / `mcp_setup` 路径。
  - 新增 `ability_mcp_install.py`：草稿探测、安装、agentconf Server/Provider 写入。
  - 新增 `AbilityMcpDraftProbe*`、`AbilityMcpInstall*` 协议模型与 TS 生成契约。
- **Subject**
  - 模板渲染把 `mcp` 命名空间成员归一为 `type=mcp`，兼容旧模板误存的 `operation`。
  - 安装入口支持 `auth.mode=provider`，复用已有 Provider 密钥，不再重复创建 Provider。
- **Observer Web**
  - 「添加 MCP」改为右侧三步抽屉：连接配置、选择工具、生成 Ability。
  - 支持搜索、工具多选、Schema 预览、安装阶段与成功页。
  - 支持选择已有 MCP 提供商；选择后隐藏密钥输入、自动带出厂商。
  - MCP 服务列表展示名称、厂商、密钥状态与关联 Ability。
  - 新建 Agent 模板预填通用 system prompt，编辑已有模板不覆盖。
- **数据与迁移**
  - `scripts/migrate_mcp_v2.py`：清理旧 `ghrah.mcp` / `mcp_call` 引用。
  - `scripts/fix_mcp_template_member_types.py`：把存量模板中误存的 MCP 成员类型修正为 `mcp`，写前自动备份 `subject.db`。
- **文档**
  - 更新架构、能力、成员规范、模板与数据存储文档；删除已退役内置 MCP 包的描述。
  - 生成 [MCP 集成设计 HTML 归档](../design/mcp-integration-design.html)，内嵌可交互的前端 Demo，并补充模板 MCP 成员类型修复。

### 后续追加（2026-11-26）

- **无密钥 MCP 提供商**：`api_key_configured=false` 不再被禁用；无密钥且无认证绑定时按无认证连接，安装为 `auth.mode=none` 的 Server。详见 [无密钥提供商 PR 文档](mcp-keyless-provider.md)。
- **同步 main**：合并 `main` 的消息管理器、轮次回执、HITL 与投递策略等变更。
- **合并冲突修复**：合并后 Room 日志读取路径出现重复方法和残缺方法体，已修复 `RoomManager`、`RoomStore`、`ProjectScopedRoomStore`、`RoomUnit` 与 units 聚合说明；修复提交 `a4e5c221`。该修复不改变设计语义，只恢复正确的两条读取路径：普通 transcript 走 Core facts，物料同步走 MM 权威 DB 的按序读取。

### 文件结构

```diff
 ghrah-core/src/ghrah/
+├── ability/mcp_member.py            # 成员驱动 MCP 运行时
+├── core/read/ability_mcp_install.py # 草稿探测 + 安装编排
 ├── ability/mcp_wiring.py            # 仅保留内部 mcp_call
-├── ability/builtin/mcp/             # 已删除
 └── ability/builtin/core/operations/list_abilities.py  # 已删除

 ghrah-observer-webui/packages/observer-web/src/components/config/
+├── mcp-install-dialog.vue          # 三步安装抽屉
 └── connections-config-page.vue     # 服务列表 + 提供商复用
```

## Evidence

### 安装向导

- **Before:** 「添加 MCP」弹窗只有连接参数，工具选择与 Ability 预览分散；安装失败只回显扁平错误。
- **After:** 右侧抽屉完成 连接 → 工具 → Ability → 安装 → 成功 全流程；提供商复用不再要求重复输入密钥。

### 关键回归

| 场景                             | 结果                                                       |
| -------------------------------- | ---------------------------------------------------------- |
| 复用已有 Provider 探测           | `ok=true`，无新增 Provider，原密钥不变                     |
| 无密钥 Provider 选择与探测       | 选项可选，连接成功并获取 8 个工具                          |
| 旧模板 MCP 成员 `type=operation` | 渲染归一为 `mcp`，创建 Agent 不再报 `member type mismatch` |
| 存量模板修复                     | revision 1/2 `bad MCP types: 0`                            |
| 安装 `mcp.provider_reuse@0.0.1`  | 成功写入 agentconf Server 与能力包版本                     |
| 合并 main 后的 Room 日志读取     | transcript 与 material_state_sync 均通过回归               |

### 合并冲突修复

- **Before:** `python -m compileall ghrah-subject/src` 在 `room/manager.py:622` 报 `SyntaxError: '(' was never closed`；`RoomStore` / `ProjectScopedRoomStore` 存在重复的 `get_log_page` 定义。
- **After:** 修复提交 `a4e5c221` 后，subject 全量 775 passed；合并后的 Core 3205 passed，协议与 TS 测试全部通过。

### 测试

```text
Python（plugin/protocol/core/subject/taskstore/scheme）
  core       3205 passed, 19 skipped
  subject     775 passed, 23 skipped
  protocol    293 passed
  plugin/taskstore/scheme 315 passed, 1 skipped

TypeScript（protocol/observer-core/mock-server/plugin-registry/observer-web）
  protocol         568 passed, 4 skipped
  observer-core    405 passed
  mock-server       55 passed
  plugin-registry   19 passed
  observer-web     471 passed, 3 skipped
  合计             1518 passed, 7 skipped
  pnpm type-check 通过
  pnpm lint       0 errors（21 条既有 warning）
  ruff check/format 通过
```

已知环境限制：`ghrah-kernel` 依赖 Unix `fcntl`，Windows 上无法收集；Playwright guards 的 webServer 使用 POSIX 环境变量语法，Windows 下无法启动，均与本次改动无关。

## Merge Danger

**Door:** one-way

旧内置 `ghrah.mcp`、模型可见 `mcp_call` 与包级 binding 被移除，旧模板/实例中的引用必须迁移；代码回滚不能自动恢复已经删除的数据。

**Blast Radius:** broad

- 协议、Core、Subject、Observer Web 五个包同时变化。
- MCP 旧引用与生成式安装 API 不再兼容；发布前应先在副本上跑迁移脚本并确认备份。
- 模板成员类型修复是就地修订，不新增 revision；执行前需停止实例并保留 `subject.db` 备份。
- 分支同时合入了 `main` 的消息管理器/轮次/HITL 变更；合并冲突修复只恢复 Room 日志两条读取路径，不改变协议或存储格式。
- Workbench 需在合并后的提交上重新构建后才能看到全部变化；部署前应确认 `workbench` 的构建版本与目标提交一致。

## 相关提交

```text
7618873 feat(core)!: MCP 改为成员驱动并移除旧绑定层
d6d394b feat(webui): 增加 MCP 安装向导与提供商复用
438417a chore(scripts): 增加 MCP v2 迁移与模板类型修复脚本
060e614 docs(mcp): 更新成员驱动说明并补充 PR 文档
30b866d docs(mcp): 归档设计文档 HTML 并内嵌交互 Demo
70cf79d fix(mcp): 支持选择无密钥的 MCP 提供商
ce756982 docs(mcp): 补充无密钥 MCP 提供商 PR 文档
550b864 Merge branch 'main' into andrew-dev-new
a4e5c221 fix(subject): 修复合并冲突残留的 Room 日志方法
```
