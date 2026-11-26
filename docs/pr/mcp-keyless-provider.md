# fix(mcp): 支持选择无密钥的 MCP 提供商

## Summary

首次联调「无认证 MCP」时发现：提供商本身没有 `api_key` 是合法配置，但安装向导把 `api_key_configured=false` 的选项直接禁用，后端也把无密钥一律判成 `credential_missing`。本 PR 把“无密钥”与“配置错误”分开处理。

```diff
 选择 MCP 提供商
-  刀具本体平台MCP · 刀具本体平台   (disabled，无法选择)
+  刀具本体平台MCP · 刀具本体平台 · 无密钥

 探测 / 安装
-  provider.api_key == null
-    → credential_missing
+  provider.api_key == null && provider.default_binding == null
+    → 无认证连接：不带 Authorization，Server auth.mode=none
+  provider.api_key != null
+    → 复用 Provider 密钥
```

### 变更点

- **Observer Web**
  - 无密钥提供商不再禁用，选项增加「无密钥」标记
  - 选中后提示“按无认证方式连接”，不显示密钥输入
  - 保留有密钥提供商的复用逻辑
- **Core**
  - 草稿探测：无密钥且无绑定 → 空请求头 / 空凭据
  - 安装：写入 `auth={mode:"none"}` 的 agentconf Server，而不是错误的 provider 引用
  - 有绑定但缺密钥 → 仍按 `config_invalid` 明确拒绝
- **测试**
  - Core 测试：无密钥 provider 探测、Server 保存、Provider 保持无密钥
  - Web 测试：选项可选、提示文案、探测请求为 provider 模式
- **文档**
  - [MCP 集成设计 HTML 归档](../design/mcp-integration-design.html) 与 PR 正文已存在；本修复不改变设计文档中的成员驱动模型

## Evidence

### Before

- 下拉项置灰：
  ```text
  刀具本体平台MCP · 刀具本体平台   (disabled)
  ```
- 强行探测时后端返回无密钥错误，无法进入选择工具步骤。

### After

使用实际的无密钥提供商 `刀具本体平台MCP`，连接无认证 mock MCP：

```text
option disabled: false
提示: 该提供商未配置密钥，将按无认证方式连接。
连接成功 · 10ms
server: ghrah-local-mock · 获取到 8 个工具
```

### 测试

```text
Python（protocol + core MCP draft/probe + subject ability control）
  313 passed, 1 skipped

Observer Web
  471 passed, 3 skipped
  vue-tsc --noEmit 通过

部署验证
  debug_v2 / 2026.11-3d914d4
  Workbench / 2026.11-ce75698（5173，选项与探测已验证）
  health: ok
```

## Merge Danger

**Door:** two-way

这是局部兼容性修复；回滚只会恢复旧行为（无密钥提供商再次不可选），不会破坏已有数据。

**Blast Radius:** narrow

- 仅影响 MCP 提供商选择、草稿探测和安装时的认证分支；
- 有密钥提供商的行为不变；
- 无密钥提供商安装后使用 `auth.mode=none`，运行时不再读取凭据；
- 仍保留“有绑定但缺密钥”这一配置错误分支，避免把真实错误静默成无认证。

## 相关提交

```text
70cf79d6 fix(mcp): 支持选择无密钥的 MCP 提供商
```

分支：`andrew-dev-new`
