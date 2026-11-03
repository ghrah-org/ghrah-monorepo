# MCP 客户端模块（`ghrah.mcp`）模块设计

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-DESIGN-009 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `91f8220`（2026-10-30），分支 `andrew-dev-new`，`ghrah-core` 0.2.0a0 |
| 读者 | 改 `ghrah.mcp` 的人；给 MCP 接配置或装配的人；评审"工具为什么只有一个入口"的人 |
| 自闭环 | 本文**不依赖任何其它文档**。本模块的定位、十条关键决策与被否决方案、数据结构与常量、三层接口、凭据解析、失败模型、协议协商、结果投影、与能力执行面的接缝、未实施项，全部在本文件内描述；每条论断后跟 `文件路径:行号` |
| 取代 | 无。本模块的模块级设计首版 |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-30 | 首版（自闭环形态）。按 `c25f1dc`（2026-10-29 落地）与 `91f8220` 当日代码整理：定位、十条关键决策、数据结构、三层接口、失败模型、协议协商、与能力面的接线、偏差与缺口 |

---

## 1. 范围

### 1.1 本文的覆盖面与权威性

本文覆盖 `ghrah.mcp` 这个模块的**全部设计内容**：它为什么与能力体系并列而不是从属、十条关键决策及其被否决的方案、数据结构与常量、三层接口、凭据解析、失败模型、协议协商、结果投影、与能力执行面的那处接缝，以及当前未实施的部分。本文的定位是**该模块的唯一设计依据**，读它不需要再读别处。

权威顺序：**代码 > 本文**。本文每条论断都跟 `file:line`；与代码不一致时以代码为事实，并把差异记进 §13。

本包内随附一份面向使用者的用法手册（`mcp/doc/使用手册.md`，878 行），它讲**怎么调**、给 API 参考与排障步骤，不讲**为什么这样设计**、也不列被否决的方案。两者不互相替代：要判断"能不能改"读本文，要判断"怎么用"读它。

本模块在实现之前另有一份仓外个人笔记（约 1157 行），不随仓分发、也不作为本文的依据。它写于实现之前，其中关于第三方 SDK 的三条假设已被实测推翻——凡此类判断以本文与代码为准。这些被推翻的假设本身作为教训留在 §14 末的偏差清单里，因为"设计期以为 SDK 会怎样、实测发现不是"是后来者最容易重复踩的坑。

不在本文范围：

- 技能包目录里 `<skill>/mcp/*.yaml` 的字段语义与扫描——那是技能装载面的事，且**当前未落地**（§13 D-1）。
- MCP 生态的其余部分：OAuth 2.1 / CIMD、resources / prompts 基元、server 侧实现、stdio 传输。这些是明确非目标（§4 M-4）。
- 能力层自身的完整契约不在本模块范围内；本包与它只有 §12 一处接缝。

### 1.2 路径约定

| 缩写 | 展开 |
| --- | --- |
| `mcp/...` | `ghrah-core/src/ghrah/mcp/...` |
| `sa/...` | `ghrah-core/src/ghrah/scheme_ability/...` |
| `slice/...` | `ghrah-core/src/ghrah/ability/...` |

路径均相对仓库根。

---

## 2. 术语

| 术语 | 定义 | 代码位置 |
| --- | --- | --- |
| MCP | Model Context Protocol。本包只实现**客户端侧、streamable-http、工具调用**这一条竖切 | `mcp/__init__.py:5-30` |
| 服务器（server） | 一台 MCP 服务端点，用 `server` 名（`^[a-z][a-z0-9_]*$`，≤32）标识 | `mcp/identity.py:39,46` |
| 工具（tool） | 服务器声明的一个可调用函数，名字 ≤128 且**原样保留**、不归一化 | `mcp/identity.py:47,76` |
| spec | `McpServerSpec`：一台服务器的连接声明（地址、headers、工具白名单、超时、限额） | `mcp/spec.py:217` |
| 会话 | `McpSession`：持有一次 HTTP 连接，负责调用与终止恢复 | `mcp/session.py:59` |
| 运行时 | `McpRuntime`：按 `agent_scope` 记录"能调哪些 (skill, server, tool)"并执行调用 | `mcp/runtime.py:43` |
| `agent_scope` | 作用域标识（一次 agent 运行/会话）。激活、查询、关闭都以它为键 | `mcp/runtime.py:59,79,92` |
| 凭据占位符 | headers 里唯一允许的模板变量 `{{auth}}`，建连前替换为该地址登记的密钥 | `mcp/spec.py:57` |
| 不确定态 | `uncertain=True`：请求失败但服务端**可能已执行**，不可自动重放 | `mcp/errors.py:146` |
| 工具目录 | "这台服务器有哪些工具"的静态快照，来自本地配置，不带参数 schema 的第二真源 | §4 M-6 |
| 元工具 | 面向模型的唯一入口 `mcp_call(server, tool, arguments)` | `sa/ability/mcp/operations/mcp_call.py` |

---

## 3. 设计定位

### 3.1 与能力体系并列，不从属

`ghrah.mcp` 与 `ghrah.scheme_ability`（能力 + 技能）是**并列**的两个体系，本包不 import 它们中的任何一个。本包只做一件事：

> 把**一台 MCP 服务器的连接声明**变成**可执行的工具调用**。

桥接由能力层**反向依赖**本包完成——`sa/ability/mcp/operations/mcp_call.py:27,34` 依赖 `ghrah.mcp.errors` / `ghrah.mcp.identity`。方向是能力 → MCP，不是 MCP → 能力。

### 3.2 依赖形状（实测）

包内 42 处 import，**全部指向自己**（`from ghrah.mcp.*`），没有一处 `from ghrah.<其他包>`。外部依赖只有两个库：

| 库 | 用在哪 | 证据 |
| --- | --- | --- |
| `mcp`（官方 Python SDK） | 传输、类型、协议协商 | `mcp/client.py:35,54,55`、`mcp/result.py:29` |
| `agentconf` | 从本地配置库读服务器登记与密钥 | `mcp/agentconf_source.py:51` |

这条"零内部依赖"是可验证的硬约束，也是它敢和能力系统并列的理由：它能在没有任何能力/技能模块存在的环境里单独跑（`tests/mcp/` 就是这么测的）。

### 3.3 三条不变量

1. **不解析任何配置文件。** `McpServerSpec` 由外部（技能模块或 `AgentconfMcpSource`）产出，本包只校验。
2. **不持有工具目录的第二份真源。** `activate()` 只登记"工具名集合"，不存 tool schema。
3. **不 import 能力 / 技能 / 清单模块。**

### 3.4 对外只暴露两类东西

| 类别 | 内容 | 说明 |
| --- | --- | --- |
| 数据结构 | `McpServerSpec`、`McpToolSpec` | 描述"要连什么、能调什么" |
| 执行入口 | 无状态 API、`McpSession`、`McpRuntime` | 持有连接、执行调用 |

`__all__` 共 **51** 个名字（`mcp/__init__.py:88` 起），含数据结构、四类执行入口、七类异常、判定与投影辅助函数。

---

## 4. 关键设计决策

每条给结论、理由、代价、落地位置。

### M-1 只加一个元工具 `mcp_call`，不为每个远端工具造一个能力

**结论**：模型侧只看到 `mcp_call(server, tool, arguments)` 一个工具；远端有几百个工具也不改变本地工具列表。

**否决的方案**：把每台服务器的每个工具注册成独立能力（`mcp__<server>__<tool>`）。这在工具少、服务器稳定时更"顺手"，但要求把任意远端工具名改造成合法 Python/函数名——截断、加哈希、调用时再反解。这类改名方案会产生一整类问题：名字碰撞、哈希表要持久化、反解失败时错误信息对不上用户看到的工具名。

**理由（按权重）**：

1. **提示词前缀缓存稳定性**——这是决定性的。工具列表一旦随服务器工具变动，缓存前缀整段失效，长会话里每轮都重新计费。
2. 上下文开销与远端工具数量解耦：工具面 N 恒定，不随远端膨胀。
3. 消除命名 clamp / hash / 反解整类问题。
4. 与 MCP 自身的 `tools/call` 形状 1:1，多一层映射不带来语义。
5. 少一个耦合点：能力系统不需要知道 MCP 的存在。

**代价**：模型要先知道"有哪些工具"才能调对——于是必须有工具目录进上下文（M-2）；参数错误只能由远端 schema 判，本地不做第二套校验（M-6）。

**落地**：`sa/ability/mcp/operations/mcp_call.py`，入参 `{server, tool, arguments, skill?}`，`arguments` 刻意声明为开放对象。

### M-2 工具目录 push 进上下文，不注入 `tools[]`

**结论**：服务器的工具名与一行 purpose 以文本形式进技能上下文；不进原生 function calling 列表。

**关键澄清**：这条**不是**为了省 token——目录文本进 system prompt 同样花 token。换来的东西是**缓存前缀稳定**：目录内容随技能激活一次性写入，之后不变；而 `tools[]` 若随远端变动就会打穿前缀。

**配套约束**：

- 目录条目只留名字 + 一行 purpose，描述按 **500 字符**截断（`mcp/identity.py:48` `MAX_TOOL_DESCRIPTION_CHARS`、`:105` `truncate_description`）。
- **权威是 `McpRuntime` 的激活集合**，上下文里的目录只是派生投影，**禁止反写**。这是 SSOT 纪律在本包的具体化。

### M-3 连接生命周期归 `McpRuntime`，不归 `load_skill`

**结论**：谁激活服务器，谁负责断开；`load_skill` 只请求激活，不持有连接。

**理由**：`load_skill` 的语义是"把文本读进上下文"，它没有"这个作用域什么时候结束"的信息。连接必须活在能观察到作用域结束的地方，否则会话泄漏。

**落地**：`McpRuntime.activate(spec, *, agent_scope)`（`mcp/runtime.py:59`，**纯内存、不联网**）与 `deactivate(*, agent_scope, skill_full_name)`（`:79`，关掉不再被引用的连接）。

### M-4 传输只做 streamable-http

**结论**：`Transport` 枚举只声明一个字面量 `"streamable-http"`（`mcp/spec.py:72`）。

**理由**：stdio 传输要在宿主上拉起子进程。在 Windows 上杀不干净子进程树——这会变成"关掉会话之后远端还在跑"的静默泄漏。与其做一个半数平台不可靠的通道，不如让"不支持"在类型层可见：调用方写 `Transport.STDIO` 直接 `AttributeError`，而不是运行期才发现。

**代价**：本地 MCP 服务器（多为 stdio）无法接入，必须自己套一层 HTTP。

### M-5 凭据以 MCP 地址为关联键，单一占位符 `{{auth}}`

**结论**：密钥存在 agentconf 的 `providers` 表（`base_url` 等于 MCP 地址、`api_key` 存密钥）；`McpServerSpec.headers` 里用 `{{auth}}` 占位，建连前替换。

**否决的方案**：自建 `(server_name, url)` 二元组表。否决理由：同一个 MCP 端点常被多个技能声明，二元组会逼用户为每个技能各登一次密钥；而地址是端点的事实身份。

**失败关闭三条**（都在 `mcp/spec.py`）：

1. `{{auth}}` 是唯一保留占位符。
2. 其他任何 `{{...}}` 一律 `McpConfigError`——防技能声明任意密钥名去偷模型密钥，也守住"残留占位符发出去必被 401"。
3. 写了 `{{auth}}` 但地址未登记 → `McpCredentialMissingError`，文案指向"去 agentconf 登记 provider"。**不发出去变 401**：401 会被误读成"凭证错"，而实际是"没配"。

**代价**：两个技能指同一地址会共用凭据；header 名/前缀/scheme 全不固化（`Authorization` 是默认而非硬编码，`mcp/agentconf_source.py:92`）。

**已知前置**：agentconf 的 `api_key` 是明文存储，加密管理器在全仓零调用点（见 §13 D-5）。

### M-6 工具目录取自本地配置快照（配置即真相）

**结论**：工具名、描述、`read_only`、`input_schema` 全部来自本地声明；运行期不向服务器取。

**理由**：把不确定性从运行期搬到编写期。「运行时取真相」与「编写时对齐真相」是两种工程契约，本包选后者：

| 维度 | 运行期取 | 编写时对齐（本包） |
| --- | --- | --- |
| 是否需要联网才能知道有哪些工具 | 是 | 否 |
| 服务器宕机 / 凭证缺失时模型能否看到工具 | 不能 | 能 |
| 参数 schema 友好度 | 原始 JSON Schema | 可按工具面需要裁剪 |
| 单一真源 | 远端 | 本地声明 |
| 漂移风险 | 低 | 高 |
| 作者负担 | 低 | 高 |
| 测试可否离线 | 否 | 是 |

**代价必须配三件配套工具**，否则这份契约会烂：生成器（连一次服务器输出可粘贴的声明）、校验器（联网比对、只报告不改文件）、调用期参数错误原样回灌并提示"schema 可能不一致"。**三件当前都未落地**（见 §13 D-1）。

### M-7 标识精确匹配，绝不拼接解析

**结论**：`server` 与 `tool` 是两个独立字符串，各自精确匹配；不拼成 `server/tool` 再切分。

**理由**：拼接解析要求两边都不含分隔符，于是要么限制工具名（远端不归我们管），要么写转义——转义一旦出现，所有错误信息里的名字都和用户看到的不一样。精确匹配把问题从"制造标识"退化成"校验标识"。

**配套**：

- 配置期约束 `server` 名（`mcp/identity.py:58` `validate_server_name`）。
- 调用期 `tool` 名原样保留，不归一化（`:76`）。
- 报错必须回灌候选列表：`format_unknown_tool_message`（`:159`）+ `suggest_tool_names`（`:124`）。建议只生成提示，**绝不自动纠正**——自动纠正会把拼写错误变成"调用了另一个工具"。

### M-8 失败四分类，写操作绝不自动重放

**结论**：`FailureKind` 四值（`mcp/errors.py:80`），配合 `uncertain` 标记与 `should_replay` 判定（`:252`）。

| kind | 触发信号 | 含义 | 可重放 |
| --- | --- | --- | --- |
| `BAD_ARGUMENTS` | JSON-RPC `-32602` | 参数错，重试无意义 | 否 |
| `SESSION_TERMINATED` | `-32600` + `"Session terminated"`，或 `-32001`，或 404 且传输层确实持会话 | 会话死了 | 只读工具可重放一次 |
| `TRANSIENT` | `-32000`、408/425/429、5xx、执行超时 | 可能只是抖动 | 只读工具可重放 |
| `PERMANENT` | 401/403、路径写错、未知应用级错误 | 确定性失败 | 否 |

**核心判断**：**失败信号不可信。** 代理可能在上游已经执行完之后才伪造"会话过期"；而 MCP 写操作**没有回滚**。因此：

- 未标注 `read_only` 的工具一律按可写处理——`McpToolSpec.read_only` 默认 `False`（`mcp/spec.py:177` 起），运行期不看服务器的 `readOnlyHint`。
- 写操作失败返回 `uncertain=True`，模型层文案必须**明说不要重试**，要求先核验外部状态。

**刻意不认的信号**：`is_session_terminated`（`mcp/errors.py:204`）**不**匹配 broken pipe / EOF / `ClosedResourceError`。这些常在一次调用回包中途触发，上游可能已执行完；当成会话过期去重放就是重复副作用。

### M-9 协议协商交给 SDK 的 `auto`

**结论**：本包只做"配置偏好 → SDK mode"的映射（`mcp/client.py:85` `sdk_connect_mode`），不自己实现协商。

**两代协议差异**：

| 维度 | 旧代（legacy） | 新代（modern） |
| --- | --- | --- |
| 握手 | 必须 `initialize` | 取消握手 |
| 会话 | 有 `Mcp-Session-Id` | 无会话 |
| 能力发现 | 从 `initialize` 结果取 | 从 `server/discover` 取 |
| 必需请求头 | — | `Mcp-Method`、`Mcp-Name`、`MCP-Protocol-Version` |

**两条硬纪律**：

1. 只在**明确版本信号**上回退（`-32022` UnsupportedProtocolVersion、`-32601` method not found），**不因超时回退**——超时是网络问题，不是"服务器只支持旧代"。
2. 非协议类异常直接上抛，不静默回退。

**为什么可以不自己写**：SDK 的 `mode="auto"` 就是 modern-first + 信号回退，且其探测逻辑把任何非 MCP 协议异常向上传播（"一个故障或进程内 bug 永远不是时代判定"）。这条是实测结论，直接推翻了"需要自己实现协商"的设计期假设。

### M-10 会话终止自己判，只读工具重放一次

**结论**：本包自己判 `SESSION_TERMINATED`，然后**丢弃 session id → 重建 transport → 只读工具透明重放一次**；写工具返回 `uncertain=True`。

**为什么必须自己判**：Python SDK 的 streamable-http transport 在收到 404 时会发一条 `JSONRPCError(code=-32600, message="Session terminated")` 然后 `return`——transport 在此后**整个 client 生命周期内都不可用**。规范要求"带 `Mcp-Session-Id` 的请求收到 404 必须丢弃 session id 重新 initialize"，SDK 未实现（上游 issue 仍在开）。所以这层不是过度设计，是对 SDK 缺陷的补偿。

**判定必须带前置条件**：`classify_http_status(status, *, has_session_id:)`（`mcp/errors.py:236`）的 `has_session_id` 是必须先判断的——现代无状态协议下 404 表示**路径写错**，不是会话过期。

**行为**：`McpSession.call_tool`（`mcp/session.py:137`）整次调用受 `spec.execution_timeout_ms` 约束；超时抛 `McpRequestError(kind=TRANSIENT, uncertain=not tool.read_only)`；`generation` 每次建连或断开递增（`mcp/session.py:87`），供上层判断是否换了连接。

---

## 5. 模块结构

| 文件 | 行数 | 职责 | 关键符号 |
| --- | --- | --- | --- |
| `__init__.py` | 145 | 导出面（51 个名字），不含逻辑 | `__all__` |
| `spec.py` | 301 | 数据结构与全部构造期校验 | `McpServerSpec:217`、`McpToolSpec:177`、`Transport:72`、`ProtocolPreference:82` |
| `identity.py` | 172 | 标识校验、描述截断、候选建议 | `validate_server_name:58`、`suggest_tool_names:124` |
| `schema.py` | 137 | JSON Schema 紧凑渲染与降级摘要 | `render_input_schema:123`、`DEFAULT_MAX_SCHEMA_BYTES=8192:34` |
| `errors.py` | 260 | 七类异常 + 四值分类 + 判定函数 | `FailureKind:80`、`classify_rpc_error:222`、`should_replay:252` |
| `client.py` | 249 | **唯一**碰 SDK 传输的无状态层 | `open_client:104`、`call_tool_once:231`、`translate_exception:158` |
| `session.py` | 223 | 持连接、超时、终止恢复 | `McpSession:59`、`call_tool:137` |
| `runtime.py` | 165 | 按作用域管激活集合与连接 | `McpRuntime:43`、`activate:59`、`call:138` |
| `credentials.py` | 186 | 凭据解析器三实现 | `no_credentials:87`、`StaticCredentials:96`、`AgentconfCredentialResolver:118` |
| `agentconf_source.py` | 480 | agentconf 库 → `McpServerSpec` 的适配层 | `AgentconfMcpSource:135`、`load_spec:468` |
| `result.py` | 157 | 结果投影（内容块 → 文本） | `ToolOutcome:43`、`project_result:114` |
| `diagnostics.py` | 204 | HTTP 探测与状态解释，挂在失败路径上 | `probe_endpoint:101`、`explain_status:157`、`enrich_request_error:177` |
| `doc/使用手册.md` | 878 | 用户手册 | — |

依赖形状：

```
spec ──► identity / schema / errors
client ──► SDK（唯一传输触点）+ errors / result / spec / diagnostics
session ──► client
runtime ──► session / credentials / spec / result / errors
agentconf_source ──► agentconf + spec / identity / credentials / errors
```

**注意**：`mcp/result.py:29` 也 `from mcp import types`。所以"SDK 类型不出 `client.py`"这条设计期约束**只在内容上成立、在 import 上不成立**：`result.ToolOutcome` 的输入是 `types.CallToolResult`。记入 §13 D-3。

---

## 6. 数据结构

### 6.1 `McpServerSpec`（`mcp/spec.py:217`，`@dataclass(frozen=True)`）

| 字段 | 类型 | 默认 | 校验 |
| --- | --- | --- | --- |
| `skill_full_name` | `str` | 必填 | 声明该服务器的技能 |
| `server` | `str` | 必填 | `^[a-z][a-z0-9_]*$`，≤32 |
| `url` | `str` | 必填 | `http`/`https`、有主机、**无内嵌凭据**、无空白 |
| `headers` | `Mapping[str,str]` | `{}` | 至多一个值含 `{{auth}}`；不许出现其他 `{{...}}` 或 `${...}` |
| `tools` | `tuple[McpToolSpec,...]` | `()` | 工具名不可重复（构造期即 `McpConfigError`） |
| `transport` | `Transport` | `STREAMABLE_HTTP` | 仅此一值 |
| `protocol` | `ProtocolPreference` | `MODERN_FIRST` | 三值 |
| `connect_timeout_ms` | `int` | `60_000` | 正数 |
| `execution_timeout_ms` | `int` | `300_000` | 正数 |
| `max_result_bytes` | `int` | `2_000_000` | 正数 |
| `max_pages` | `int` | `50` | 正数，上限 `MAX_PAGES_LIMIT=500` |

派生属性：`tool_names`（`:271`）、`credential_headers`（`:276`，供重定向时剥离）、`requires_credential`（`:285`）、`find_tool`（`:289`）、`require_tool`（`:296`，缺失抛 `McpToolMissingError`）。

`tools=()` 是**合法**的：可以激活服务器但任何调用都会在白名单校验处失败，且此时**不建连、不使用凭据**。

### 6.2 `McpToolSpec`（`mcp/spec.py:177`）

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `name` | `str` | 必填 | ≤128，**原样保留**，不归一化 |
| `description` | `str` | 必填 | 非空；渲染时按 500 字符截断（折行） |
| `input_schema` | `Mapping` | 必填 | 必须是 object 形状（`schema.py:68` `require_object_schema`） |
| `read_only` | `bool` | `False` | **未标注只读一律当可写**；它是"能否重放"的唯一依据 |

`render_schema(*, max_bytes=None) -> SchemaRender(text, degraded, original_bytes)`（`:210`）：默认上限 8192 字节，超限降级为「属性名: 类型 (required)」摘要，最多 30 个属性（`schema.py:37`）。

### 6.3 枚举与常量

| 名字 | 值 | 位置 |
| --- | --- | --- |
| `AUTH_PLACEHOLDER` | `"{{auth}}"` | `mcp/spec.py:57` |
| `Transport.STREAMABLE_HTTP` | `"streamable-http"` | `:72` |
| `ProtocolPreference` | `MODERN_FIRST` / `LEGACY` / `MODERN`（`"2026-07-28"`） | `:82` |
| `DEFAULT_PROBE_TIMEOUT_S` | `5.0` | `mcp/diagnostics.py:45` |
| `PROBE_BODY_SNIPPET_CHARS` | `300` | `:48` |
| `_PROBE_PROTOCOL_VERSION` | `"2025-06-18"` | `:51`（固定用握手代版本，不跟随 `spec.protocol`） |
| `DEFAULT_MAX_SCHEMA_BYTES` | `8192` | `mcp/schema.py:34` |

`compact_json` 用 `ensure_ascii=False`（`mcp/schema.py:59`）——中文描述不转义成 `\uXXXX`，否则一个中文工具描述的体积会翻好几倍。

---

## 7. 对外接口

### 7.1 三层

| 层 | 入口 | 用途 | 状态 |
| --- | --- | --- | --- |
| 无状态 | `open_client`（`client.py:114`，async context manager）、`call_tool_once`（`:365`）、`list_tools`（`:256`）、`list_tool_names`（`:287`）、`list_endpoint_tools`（`:312`，地址直达） | 诊断、脚本、单测 | 已实现 |
| 会话 | `McpSession`（`session.py:59`） | 需要多次调用、`generation` 与重放语义 | 已实现 |
| 运行时 | `McpRuntime`（`runtime.py:43`） | 能力层唯一入口 | 已实现，但**在 `src/` 内无装配点**（§13 D-2） |
| 适配 | `AgentconfMcpSource`、`load_spec`（`agentconf_source.py:135,468`） | 从 agentconf 库造 spec | 已实现 |

无状态层的两个要点：

- `open_client` 的 `read_timeout_s` 是**单次请求读超时**，不是整次预算；整次预算在 `McpSession` 用 `spec.execution_timeout_ms` 约束。
- `translate_exception`（`client.py:168`）把所有底层异常翻成 `McpError` 子类，**`CancelledError` 原样穿透**——取消不是错误。
- 握手失败会自动补一次 HTTP 探测，把 401 / 404 分开报告。

### 7.2 `McpRuntime.call` 的五步顺序即权限边界

`mcp/runtime.py:138`。顺序不是随手写的，每一步都是一道闸：

| # | 检查 | 失败异常 | 说明 |
| --- | --- | --- | --- |
| 1 | `server` 在该 `agent_scope` 激活了吗 | `McpNotActivatedError` | — |
| 2 | 给了 `skill` 时，`server` 属于该技能吗 | `McpNotActivatedError` | — |
| 3 | `tool` 在 `spec.tools` 白名单里吗 | `McpToolMissingError`（带候选） | **此时还没取凭据、没建连** |
| 4 | 按地址取凭据，建连或复用 | `McpCredentialMissingError` / `McpConnectionError` | — |
| 5 | 调用并投影 | `McpRequestError` 等 | — |

`McpNotActivatedError` **不带**可用工具清单（`mcp/errors.py:97`）——授权失败不该泄露远端有什么。这是与 `McpToolMissingError`（`:105`，带 `available`）刻意的不对称。

### 7.3 运行时其余语义

| 方法 | 语义 |
| --- | --- |
| `activate(spec, *, agent_scope)` | **纯内存、不联网**。同作用域同 server 同技能幂等覆盖；同 server 不同技能抛 `McpConfigError`；runtime 已 close 抛 `McpConfigError("runtime is closed")` |
| `deactivate(*, agent_scope, skill_full_name)` | 移除该技能声明的全部服务器；关闭不再被引用的连接 |
| `activated_servers(*, agent_scope)` | **方法不是属性**，返回字典序元组 |
| `spec_for(*, agent_scope, server)` | 未激活返回 `None` |
| `close()` | 重复安全 |

---

## 8. 凭据

### 8.1 三个解析器

`CredentialResolver = Callable[[str], "str | None"]`——输入 URL，输出密钥或 `None`。

| 实现 | 语义 | 位置 |
| --- | --- | --- |
| `no_credentials` | 恒定返回 `None`。**默认值** | `mcp/credentials.py:87` |
| `StaticCredentials({url: secret})` | 内存映射，另有 `set`/`delete` | `:96` |
| `AgentconfCredentialResolver(config=None, *, db_path=None)` | 读 agentconf 本地库 | `:118` |

默认值是 `no_credentials` 而不是 agentconf 解析器，这是**零隐式行为**的直接体现：构造一个 `McpRuntime` 不应该顺手在磁盘上开一个库。

### 8.2 解析时机

**建连前、`McpSession` 构造时取一次**，不在 spec 解析时。理由：密钥可轮换（长活的 spec 不该缓存密钥）；spec 可能被打印、写日志、缓存到别处（它本身不含密钥）。

### 8.3 失败关闭

见 M-5 三条。另有一条实现细节：agentconf 的 `base_url`/`api_key` 是 pydantic `SecretStr`，`isinstance(x, str)` 恒为 `False`——本包统一走 `_secret_str`（`mcp/credentials.py:68`）取值。读到库失败**不致命**：只记 warning 并按"没配"处理（随后 `{{auth}}` 缺失会走 `McpCredentialMissingError`，错误仍可见）。

### 8.4 agentconf → spec 的映射

`AgentconfMcpSource`（`mcp/agentconf_source.py:135`）：

| agentconf 字段 | spec 字段 | 规则 |
| --- | --- | --- |
| `name` | `server` | 非法字符压成 `_`；超长截断到 32；纯中文退化成 `mcp`（`_server_name_from:105`） |
| `url` | `url` | 原样 |
| `headers` | `headers` | 含密钥的那段替换成 `{{auth}}` |
| `connect_timeout`（秒） | `connect_timeout_ms` | ×1000（`_connect_timeout_ms:461`） |
| `protocol_version_hint` | `protocol` | `auto`/`modern-first`→`MODERN_FIRST`；`legacy`/`handshake`→`LEGACY`；`modern`/`2026-07-28`→`MODERN`；未知→`MODERN_FIRST`（`_protocol_from_hint:120`） |

- 多个 headers 同时命中密钥 → `McpConfigError`（无法判断该替换哪个）。
- `spec_for_url` 三种情形：已登记 → 沿用 headers/超时/偏好；未登记 → 退化成"只带凭据头的最小 spec"；无匹配 provider → 无 headers 无凭据。
- agentconf 侧约定：`mcp_servers.connection.credential_ref` 写的是 **provider 名**，agentconf 只当字符串存，不解析环境变量/文件/凭据服务——解释权在宿主。

### 8.5 已知前置

agentconf `providers.api_key` 明文落盘，加密管理器在全仓零调用点。这不是本包能修的，但它是 MCP 凭据方案的前提，必须记在文档里（§13 D-5）。

---

## 9. 错误模型

### 9.1 七类异常

| 类 | 何时抛 | 携带 | 建议重试 |
| --- | --- | --- | --- |
| `McpError` | 基类 | — | — |
| `McpConfigError` | spec 非法，**建连前** | 消息 | 否（改配置） |
| `McpNotActivatedError` | server 未在该作用域激活 | 消息（**无工具清单**） | 否 |
| `McpToolMissingError` | tool 不在白名单 | `server`/`tool`/`available` | 否（换名字） |
| `McpCredentialMissingError` | 需要凭据但取不到 | `server`/`url` | 否（去登记） |
| `McpConnectionError` | 建连失败 | 消息 | 视 kind |
| `McpRequestError` | 请求失败 | `kind`/`code`/`uncertain`/`http_status`/`body_snippet` | 见 §9.2 |
| `McpProtocolError` | 协议层不符 | 消息 | 否 |

### 9.2 判定与重放

```
classify_rpc_error(code, message) -> FailureKind      # mcp/errors.py:222
classify_http_status(status, *, has_session_id)        # :236
is_session_terminated(*, code, message)                # :204
should_replay(kind, *, read_only)                      # :252
```

`should_replay` 只在 `SESSION_TERMINATED` 或 `TRANSIENT`，**且**工具确实标注了 `read_only=True` 时返回 `True`。

### 9.3 为什么不信连接层信号

broken pipe / EOF / `ClosedResourceError` 常在"请求已送达、回包路上断了"时触发。把它判成会话过期并重放，等于对一次**已经执行完**的写操作再执行一遍。所以：

- 这三类信号不判 `SESSION_TERMINATED`，落 `TRANSIENT` + `uncertain`。
- 只有明确的 `-32600 "Session terminated"`、`-32001`，或"带 session id 的 404"才判会话终止。

---

## 10. 协议协商

`sdk_connect_mode(preference)`（`mcp/client.py:85`）：

| 偏好 | SDK mode | 行为 |
| --- | --- | --- |
| `MODERN_FIRST`（默认） | `auto` | 先试新代，遇明确版本信号回退旧代 |
| `LEGACY` | `legacy` | 直接用旧代 |
| `MODERN` | 固定版本 | 只用新代，不回退 |

回退只认 `-32022`（UnsupportedProtocolVersion）与 `-32601`（method not found）。**超时不触发回退**——超时是网络症状，拿它当"服务器只支持旧代"的判据会把一次网络抖动变成一次协议降级，且下次还会重复。

会话过期恢复逻辑只对旧代成立（新代无会话）。这也是 `has_session_id` 必须先判的原因。

---

## 11. 结果投影

`project_result(result: types.CallToolResult) -> ToolOutcome`（`mcp/result.py:114`）：

| 内容块 | 投影 | 遗失 |
| --- | --- | --- |
| `text` | 原样 | — |
| `image` | 占位符 + 字节数进 `diagnostics` | **二进制负载被丢弃**（无 attachment store） |
| `audio` | 同上 | 同上 |
| `resource_link` | `Resource link: <name> (<uri>)` | — |
| 内嵌 `text` | 原样 | — |
| 内嵌 `blob` | 占位符 + 字节数进 `diagnostics` | 二进制 |

`ToolOutcome(text, is_error, structured, diagnostics)`（`:43`）：

- `render_for_model()`：正文在前、诊断在后；空内容返回 `"(tool returned no content)"`。
- `structured_as_text(outcome)`：`structuredContent` 与 text 重复时只留一份。
- **`isError=True` 必须变成真失败**——它是工具级失败的主要上报路径（SDK 把所有工具级失败都以 `is_error=True` 的结果返回，不抛异常）。把 `isError` 当普通结果读，是这套接口最容易犯的错。

---

## 12. 与能力面的接线

### 12.1 已实现

| 项 | 位置 |
| --- | --- |
| 服务键 | `sa/execution/execution_services.py:86` `MCP_RUNTIME = ServiceKey[Any]("mcp_runtime")` |
| 上下文注入 | `sa/execution/context.py:68`（构造参数）、`:94-95`（`if mcp_runtime is not None: services.set(...)`）、`:171-172`（属性；`None` = 未接线） |
| 执行器透传 | `sa/execution/executor.py:566` `getattr(supervisor, "mcp_runtime", None)` → `:577` 传入 per-call 上下文 |
| 能力声明 | `sa/ability/mcp/ability.yaml`（78 行）：`provider_requirements.mcp_runtime = {contract: ghrah.mcp.v1, required_capabilities: [request]}`；operation `mcp_call` 的 `implementation = {provider_slot: mcp_runtime, operation: mcp_call}`、`effects: [net:request]`、`execution: {mode: request, timeout_ms: 300000, cancellation: unsupported, on_revoke: drain, retry: never}` |
| 成员实现 | `sa/ability/mcp/operations/mcp_call.py`（261 行） |
| 未接线时的行为 | `:191` 返回明确失败「McpRuntime not wired — MCP calling is unavailable…」，**不静默降级** |

`cancellation: unsupported` 与 `retry: never` 不是省事，是诚实：本包没有实现调用中途撤销，也没有实现自动重试（只有只读工具的会话终止重放）。把它们写进声明，免得上游以为有。

### 12.2 `mcp_call` 的三道前置失败

| 情形 | 结果 |
| --- | --- |
| `mcp_runtime` 为 `None` | `ActionResult(FAILURE)`，文案说明未接线 |
| `tool` 不在 `spec.tools` 白名单 | `McpToolMissingError` → FAILURE，**不建连**（凭据都不会被使用） |
| `server` 未激活 | `McpNotActivatedError` → FAILURE |

`arguments` 刻意声明为开放对象：本地不做第二套参数校验（M-6），参数错误由远端 schema 判，错误原样回灌给模型。

---

## 13. 已知偏差与缺口

**D-1 没有 spec 生产者。** `<skill>/mcp/*.yaml` 的扫描与解析**未落地**：`ghrah-core/src/` 下不存在任何 `mcp_config.py`（`**/*mcp*` 只命中 `sa/ability/mcp/operations/mcp_call.py`）；`McpRuntime.activate()` 在 `src/` 内**零调用点**（只在 `scripts/`、`tests/` 里）。配套的生成器与校验器也未落地（M-6 的三件）。

**D-2 supervisor 上无人设 `mcp_runtime`。** `executor.py:566` 用 `getattr(supervisor, "mcp_runtime", None)` 取值，但仓内没有装配代码往 supervisor 上挂这个属性。结果是 §12.1 那张表全绿、端到端仍不可用：能力层确实会把 runtime 传下去，前提是有人先创建并挂上它。

**D-3 SDK 类型出现在两个文件。** `mcp/client.py:35,54,55` 与 `mcp/result.py:29` 都 import SDK。设计意图是"SDK 类型不出 `client.py`"，实际 `project_result` 的入参就是 `types.CallToolResult`，无法回避。结论：这条约束在**内容边界**上成立（外部拿到的都是 `ToolOutcome` 等自有类型），在 **import 边界**上不成立。

**D-4 单测覆盖不均。** `ghrah-core/tests/mcp/` 只有一个文件 `test_agentconf_source.py`（23 例），主要覆盖 agentconf 适配层与少量 spec/runtime；`session`、`result`、`errors`、`schema`、`client` 没有直接单测。会话终止恢复、四值分类、schema 降级这三块逻辑最该有单测，目前靠代码评审守。

**D-5 凭据明文落盘。** agentconf `providers.api_key` 无加密，加密管理器全仓零调用点。本包的凭据方案在"关联键"上已收敛，但存储机密性尚未解决。

**D-6 无 CLI 入口。** `ghrah mcp inspect` / `verify` 未实现，`ghrah-core` 顶层包与命令面都没有暴露本包。

**D-7 历史路径残留。** 历史设计稿里提到的 `ghrah/skills/mcp_config.py`、`ghrah/abilities/builtin/mcp_call.py`、`ghrah/abilities/execution_services.py` 在仓内都不存在。它们是 `a5beb74` 重构前的旧树。看到这类路径直接按 `sa/` 与 `mcp/` 替换。

---

## 14. 测试形态

| 项 | 现状 |
| --- | --- |
| 目录 | `ghrah-core/tests/mcp/` |
| 文件 | `test_agentconf_source.py`（1 个） |
| 用例 | **23 passed**（2.18s，`91f8220`） |
| 外部依赖 | 不依赖端口、不依赖子进程；正常路径用 SDK 的进程内直连 |
| 特殊约束 | 该目录**不能有 `__init__.py`**——否则会成为顶层包 `mcp`，遮蔽官方 SDK |

「不能有 `__init__.py`」这条是硬约束，不是风格问题：一旦加了，`import mcp` 解析到测试目录，SDK 全部 API 消失，报错信息还完全不指向这里。

---

## 15. 未实施项

明确列出，避免被读成"已支持"：

| 项 | 触发条件 |
| --- | --- |
| `<skill>/mcp/*.yaml` 扫描与 `McpServerSpec` 生产 | 技能模块落地 MCP 声明目录 |
| `supervisor.mcp_runtime` 装配 | 宿主生命周期里能确定 `agent_scope` 的结束点 |
| `ghrah mcp inspect` | 需要给技能作者生成可粘贴的声明 |
| `ghrah mcp verify` | 需要联网比对声明与远端 |
| stdio 传输 | 需要可靠的跨平台子进程树回收 |
| 二进制结果落盘（attachment store） | 需要统一的附件存储约定 |
| 工具变化通知 | 需要先确定"目录是快照"这条契约的豁免条件 |
| 多凭据头（一台服务器多个 `{{...}}`） | 需要区分的密钥命名空间 |