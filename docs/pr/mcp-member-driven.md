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

| 场景 | 结果 |
| --- | --- |
| 复用已有 Provider 探测 | `ok=true`，无新增 Provider，原密钥不变 |
| 旧模板 MCP 成员 `type=operation` | 渲染归一为 `mcp`，创建 Agent 不再报 `member type mismatch` |
| 存量模板修复 | revision 1/2 `bad MCP types: 0` |
| 安装 `mcp.provider_reuse@0.0.1` | 成功写入 agentconf Server 与能力包版本 |

### 测试

```text
Python（plugin/protocol/core/subject/taskstore/scheme）
  4354 passed, 43 skipped

TypeScript（protocol/observer-core/mock-server/plugin-registry/observer-web）
  1478 passed, 7 skipped
  pnpm type-check 通过
  pnpm lint       0 errors
  pnpm build      通过
  mypy protocol/scheme 通过
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