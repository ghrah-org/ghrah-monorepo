# Runtime 管理契约与验证链路交付说明

本文记录 Runtime `/v1` 管理扩展、Subject HTTP 宿主、Observer Runtime 调试页与 Observer Mock 的当前交付边界。它不是正式 Observer API 的迁移结论；普通 Observer 仍以 Subject `/ws` 和 `/api/v1/*` 为唯一外部边界。

## 通信形态

当前存在两条刻意分离的链路。

### 既有产品链路

```text
Observer WebUI
  → Subject /ws 与 /api/v1/*
  → Subject Project / Room / Agent / Ability units
  → CoreCluster / CorePort / CoreUnit
  → 既有 Core SQLite 权威
```

这条链路继续承载 Project、Room、HITL、既有 Agent 管理和 Observer 事件投影。本文交付不改变它的启动方式、数据权威或前端 stores。

### 新 Runtime 契约验证链路

```text
Runtime debug UI
  → RuntimeObserverClient
  → Subject runtime_http /v1/*
  → RuntimeClient
  → CoreRuntime.dispatch
  → Runtime Agent / Management API
  → Runtime Agent Store 与既有授权库
```

这条链路使用真实 Subject HTTP 宿主、RuntimeClient、CoreRuntime、SQLite、消息投递、Timeline 与 Node 读面。`scripts/runtime_mock_server.py` 仅替换 LLM 最终回复为固定 `RUNTIME_MOCK_OK`，用于去除外部模型、凭据和网络的不确定性；协议和持久化链路均真实。

两条链路目前平行存在，这是迁移中的过渡状态。不能把 Runtime debug UI 解释为普通 Observer 已迁移，也不能让普通 Observer 直接依赖 Core Runtime 地址。

## Runtime 管理契约

新增契约定义在 `ghrah-protocol/src/ghrah/protocol/runtime_management.py`，OpenAPI 与 TS schema 均从 Python 协议导出。

### Agent 配置与删除

```text
PATCH   /v1/agents/{agent_id}
DELETE  /v1/agents/{agent_id}
```

- 两者均要求 `If-Match`。
- `PATCH` 在 Agent 停止后原子替换完整配置，保留 `agent_id`、`external_ref` 和历史事实。
- 运行中 `PATCH` 返回 `409 INVALID_STATE`，需先停止。
- `DELETE` 先停止运行实例，再删除定义；不删除历史消息和节点事实。
- 条件修订不匹配返回 `412 PRECONDITION_FAILED`，携带当前 ETag。

### 能力目录、实例与审计

```text
GET   /v1/abilities
POST  /v1/ability-instances
GET   /v1/agents/{agent_id}/abilities
GET   /v1/agents/{agent_id}/abilities/changes
```

- 目录只列出当前宿主存在的内置包，不隐式安装第三方包。
- 登记实例是显式操作，本身不授予 Agent 权限。
- Agent Ability 视图组合授权权威、工具选择和运行时快照；`requires_respawn` 表示待重启生效。
- 变更审计按 `after_seq` 有界读取，授权重建依赖当前事实而非事件写回。

### 授权与工具选择

```text
PUT     /v1/agents/{agent_id}/abilities/grants/{alias}
DELETE  /v1/agents/{agent_id}/abilities/grants/{alias}
PUT     /v1/agents/{agent_id}/abilities/tools/{tool_name}
DELETE  /v1/agents/{agent_id}/abilities/tools/{tool_name}
```

- 写操作要求 `If-Match: "ability-{revision}"`。
- 授权请求不能自报操作员身份；Subject 侧受信主体由宿主注入。
- 工具绑定只能从已授予成员中选择，不能通过绑定接口新增权限。
- 失败保留 `403 / 404 / 412 / 422 / 503` 等领域错误语义。

## Core 实现边界

`RuntimeManagementAPI` 复用 `RuntimeAgentAPI` 的生命周期锁、受信主体、资源存储和既有 `AbilityAccessStore`：

- 没有第二份 Agent 定义权威；
- 没有第二份能力授权事实；
- GET 不写库；
- 删除定义不清理历史消息和 Timeline；
- 配置字节配额、支持组合校验和 canonical digest 复用资源入口；
- 停止态配置替换后递增 `config_revision`，返回新的 `ETag`。

## Subject Runtime HTTP 宿主

`ghrah-subject/src/ghrah/subject/runtime_http.py` 提供显式 HTTP 宿主：

- 生命周期由 `RuntimeConnection` 持有：启动等待 Core ready，退出时停止 Core。
- 只透传 Runtime 契约语义，不把 Core 地址暴露给浏览器。
- 保留 `Authorization`、`If-Match`、`If-None-Match`、`Idempotency-Key`。
- 响应保留 `ETag`、`Location`、`Idempotency-Replayed`、`Allow`。
- JSON 请求必须是对象；非对象或非法 JSON 返回 `422 INVALID_ARGUMENT`。
- 请求体限制默认 9 MiB，超限返回 `413`。
- 非回环监听必须配置 `GHRAH_RUNTIME_TOKEN`。

该宿主当前用于 Runtime 契约联调与调试页面，不是普通 Observer 产品的最终 API 形态。

## Observer Runtime 调试页

新增 `/runtime` 页面与 `RuntimeObserverClient`：

- 通过生成后的 TS schema 解析响应；
- 支持状态、元信息、Agent 列表 / 详情 / 创建 / 启停 / 配置替换 / 删除；
- 支持消息提交、Delivery 轮询、Timeline 和 Node 详情；
- 支持能力目录、授权、工具绑定 / 解绑和变更审计；
- 统一处理 `412` 冲突并重新读取权威资源。

启动示例：

```sh
uv run python scripts/runtime_mock_server.py \
  --scope observer-dev \
  --database /tmp/ghrah-runtime/core.sqlite3 \
  --port 4114
```

前端环境变量：

```sh
VITE_GHRAH_RUNTIME_URL=http://127.0.0.1:4114
VITE_GHRAH_RUNTIME_ENDPOINT=runtime-mock
```

## Observer Mock 改动

Mock 继续模拟 Subject 对外行为，不模拟“Observer 直连 Core”的正式形态：

- Agent 管理 HTTP 补齐列表 / 详情、草稿创建、规格替换、期望状态和带版本删除；
- HTTP 与 WS 共享项目、Agent 身份和运行状态，避免双写投影；
- 能力管理补齐读树、授予、撤销、绑定、解绑、CAS 冲突和审计；
- 写入后经 WS 发送失效通知，前端重新读取权威视图；
- Mock README 记录支持边界和未模拟内容。

## 已验证内容

Python 侧：

- Runtime Agent / Management API 目标测试；
- Runtime lifecycle、read API、Subject Runtime client 与 HTTP host；
- ghrah-protocol Runtime contract 与 schema conformance；
- `ghrah-subject` 全量测试：555 passed、6 skipped；
- Runtime 相关 Core / Subject / Protocol 集合：115 passed；
- Ruff、格式检查、`git diff --check` 与契约导出检查。

真实链路验收覆盖：

- Agent 创建、幂等重放、`201`、`Location`、`ETag`；
- 旧 ETag 写入 `412`；
- 停止定义 → 显式启动 → 停止；
- 消息 `202` → Delivery `completed`；
- Timeline 与 Node 正文读取、Node `If-None-Match` `304`；
- 运行中配置替换 `409`、停止后替换成功且 revision 递增；
- 授权视图、工具解绑 / 重绑和 CAS 冲突；
- 删除定义后读取 `404`；
- Core 重启后定义存在、Agent 保持 stopped、incarnation 为空、历史仍可读。

已知显式语义：

- `POST /v1/agents` 的 `state=running` 当前返回 `501`，要求先创建 stopped 定义，再 `PUT state=running`；
- Runtime 事件平面尚未完整接入 Observer WS；
- TS 构建与门禁在 Node 24.11.0 下执行通过，但输出 Unsupported engine 警告；正式验收仍应使用 Node >= 26 复跑。

## 后续工作

### P0：定义正式 Subject 资源边界

- 明确普通 Observer 使用的 Subject `/api/v1/*` 资源语义。
- 定义 `project_id / agent_id` 与 Runtime `scope_ref / external_ref / agt_*` 的稳定映射。
- 定义 Observer 用户身份、Subject 受信主体与 RuntimePrincipal 的转换。
- 明确 Runtime `/v1` 只作为 Subject → Core 的内部契约，Runtime debug host 不作为普通产品入口。

### P1：接入既有产品链路

- Subject 正式 Agent 创建、启停、删除和配置接口内部改调 `RuntimeClient`。
- Subject Agent 列表与详情读取 Runtime 权威资源。
- Timeline / Node 经 Subject API 暴露给普通 Observer。
- Runtime 状态、Delivery、配置和能力变更转换为 Observer WS 事件。
- Observer 重连后从权威读面重建，不新增第二份可写事实。

### P1：补齐 Mock 与真实链路行为

- Mock 基于同一份生成协议校验请求和响应。
- 补齐 Runtime 调试链路对应的状态码、错误体和 ETag 行为。
- 增加 Core 未 ready、条件冲突、游标失效和重启恢复场景。
- 在 Node >= 26 下执行 `pnpm build:deps && pnpm test:mock && pnpm type-check && pnpm lint`。

### P2：意图收敛与漂移治理

- Subject 保存业务意图，Core 保存运行事实。
- 按外部引用对齐 Agent 定义、目标状态、配置和能力授权。
- 生成配置、授权、状态和未知 Agent 的漂移报告。
- 提供人工“以 Subject 为准修复 Core”与“采纳 Core 事实”两类处置。
- 增加持久收敛状态和有界重试。

### P3：架构清理

- 设计并实现 `SubjectPort`，收束 Subject 对 Observer 的 REST / WS 契约。
- 降低 Subject 对 Core 的静态 import 基线。
- 明确 CorePort 与 Runtime `/v1` 的长期分工。
- 完成迁移后逐步下线旧 Agent 管理路径。

## 验收入口

```sh
# Runtime HTTP + 真实 Core 集成链路
env -u PYTHONPATH uv run pytest -q ghrah-subject/tests/test_runtime_http.py

# Runtime 相关契约面
env -u PYTHONPATH uv run pytest -q \
  ghrah-core/tests/core/test_runtime_agent_api.py \
  ghrah-core/tests/core/test_runtime_management_api.py \
  ghrah-core/tests/core/test_runtime_lifecycle.py \
  ghrah-subject/tests/test_runtime_http.py \
  ghrah-protocol/tests/test_runtime_contract.py \
  ghrah-protocol/tests/conformance/test_runtime_schemas.py

# TS 门禁（Node >= 26）
pnpm build:deps
pnpm test:mock
pnpm type-check
pnpm lint
```
