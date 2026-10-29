# Agent 驱动循环

| 项 | 内容 |
| --- | --- |
| 文档编号 | GHRAH-DESIGN-003 |
| 文档状态 | 现行（authoritative） |
| 校准基线 | `ghrah-monorepo` @ `f8ff157`（2026-10-28），`ghrah-core` 0.2.0a0 |
| 读者 | 改 Agent 循环、调上下文窗口/压缩策略、排查"为什么这轮没执行/为什么回滚了"的开发者 |
| 取代 | `Agent驱动循环执行轨迹.md`（归档至 `docs/archive/`） |
| 相关文档 | `ghrah-01-ability-design-and-implementation.md` §9–§11（工具调用与审批）、`ghrah-04-skill-system.md`（技能目录注入点）、`ghrah-05-build-startup-and-troubleshooting.md`（日志与落盘） |

## 修订记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-28 | 首版。以 `agents/base.py` 为准重建：8 阶段循环、`_action` 全流程、并行工具调用、await_reply 两阶段提交、压缩闸门与四级应急降级、异常回滚语义、13 条硬序约束、配置字段表与偏差清单 |

## 1. 范围与读法

本文描述 `ActorAgent` 从"收到一条消息"到"给出最终回复"的完整控制流：循环有哪些阶段、每个阶段读写什么、什么情况下回滚、上下文压缩在哪一步发生、工具调用如何并行、配置字段在哪里生效。

权威顺序：代码 > 本文 > `docs/archive/Agent驱动循环执行轨迹.md`。本文所有 `path:line` 以校准基线为准；行号会漂移，符号名不会。

路径缩写同 `ghrah-01-ability-design-and-implementation.md` §1.1：`agents/...` = `ghrah-monorepo/ghrah-core/src/ghrah/agents/...`，`core/...` = `ghrah-monorepo/ghrah-core/src/ghrah/core/...`。

## 2. 对象结构

`class ActorAgent`（`agents/base.py:132-2123`，57 个方法）。构造函数（`:158-195`）注入：`config`、`context_manager`、`ability_executor`、`llm_factory`、`supervisor`、`event_publisher`；内部自建 `HookStore` + `HookRunner`、消息队列、`AgentRunner`、`IterationState`。

关键状态与方法分组：

| 分组 | 方法（行号） |
| --- | --- |
| 生命周期 | `__init__` `:158-195`、`_all_hooks` getter `:197-201` / setter `:203-206`、`busy` `:299-302` |
| 能力注册 | `register_ability` `:212-259`（去重 + `bind_tool` schema + hooks + 默认状态 + 刷新工具面）、`unregister_ability` `:261-293`、`get_abilities` `:295-297` |
| 消息入口 | `receive` `:482-487`、`receive_atomic` `:493-520`、`resume_atomic` `:522-580`、`_receive_once` `:582-664`、`_receive_command_once` `:679-727` |
| 循环核心 | `_finish_response` `:729-752`、`request_compact` `:754-773`、`_drive_loop` `:775-1017`、`_run_compact_round` `:1019-1197`、`_publish_context_usage` `:1199-1250`、`_action` `:1252-1459` |
| 工具结果 | `_await_tool_results` `:1461-1514`、`_add_tool_results` `:1516-1536`、`_restore_pending_tools` `:1538-1567`、`_execute_routed_ability` `:1569-1612` |
| Hook 装配 | `_run_hooks` `:1618-1638`、`_build_hook_context` `:1644-1669`、`_build_ability_context` `:1671-1702` |
| 会话/分支 CRUD | `:1753-1956`（每个都发事件） |
| 其他 | `chat` `:1962`、`get_history` `:1981`、`get_state` `:1994`、`get_context_usage_snapshot` `:2007`、`set_state` `:2035`、`reset` `:2042-2092`（`_runner.busy or _drive_active` 时拒绝）、`send` `:2094-2123` |

## 3. 三个入口

| 入口 | 用途 | 关键约束 |
| --- | --- | --- |
| `receive`（`:482-487`） | 普通收消息；有 `message_manager` 时走 supervisor 路由，否则直接 `_runner.run(_receive_once)` | — |
| `receive_atomic`（`:493-520`） | 会话选择 + 持久化迭代作用域；把不匹配会话的消息**重新入队** | 消息不丢 |
| `resume_atomic`（`:522-580`） | 崩溃恢复：校验 head/branch 身份、恢复 pending tools、要求非零预算、驱动循环 | `config.max_iterations == 0` 时 `RuntimeError`（`:553-558`） |

`_receive_once`（`:582-664`）的护栏：本 agent 只挂了 `execute_ability` 时走命令路径（`:596-...`）；没有任何能力则 `AgentError`；然后入队用户 `ChatMessage`、设置 `max_iterations` 并 `reset()`、置 `_drive_active=True`、跑 `_drive_loop`、最后 `_finish_response`。`AgentError` 原样重抛（`:653-654`），其他异常转成 ERROR 回复（`:655-664`）。

## 4. `_drive_loop`：八个阶段

签名：`async def _drive_loop(self) -> None`（`agents/base.py:775-1017`）。初始化（`:793-796`）：`cm`、`accumulated_data={}`、`emergency_halvings=0`（**跨回滚存活**）。

循环条件：`while self._iteration_state.should_continue:`（`:798`）。`IterationState.should_continue` 的语义是 `is_unlimited or iteration < max_iterations`（`agents/iteration_state.py:55-57`，`is_unlimited` 见 `:50-52`）。

| 阶段 | 行号 | 做什么 | 失败/退出 |
| --- | --- | --- | --- |
| **P0 压缩闸门** | `:803-806` | `if cm.check_compact_trigger(): await self._run_compact_round("manual" if cm.compact_requested else "threshold"); continue` | 不排空队列、不消耗 `max_iterations`、不 `advance`、不发任何 Agent HookPoint |
| **P1 `BEFORE_ACTION`** | `:809-822` | 建 hook 上下文 `:809`、`await _run_hooks` `:810`、合并 `modified_context` `:812-813`；`!should_continue` 时可选 `_execute_routed_ability(..., route_to)` `:815-821` 后 `break` `:822` | 出口 2 |
| **P2 开迭代 + 排空队列** | `:825`、`:829-833` | `cm.begin_iteration()` `:825`；排空消息队列 `:829-831`、`cm.add_messages(new_messages)` `:832`，并保留列表用于回滚重入队 | — |
| **P3 执行 action** | `:835-839` | `action_output = await self._action(accumulated_data)`；解出 `results`、`llm_metadata` | 异常 → `:906` |
| **P4 提交** | `:842-865` | `ability_names`（空则 `["no_action"]`）`:842-846`；`node_metadata` + `delivery_context` `:847-852`；`compaction_decision = cm.evaluate_compaction_decision()` `:858-860`；`await cm.commit_iteration_async(...)` `:861-865` | — |
| **事件** | `:870-882` | 发 `ActionChainUpdatedEvent`（序列化的 node + branch） | — |
| **用量累积** | `:886-905` | `node.metadata["token_usage"]` → `accumulated_data["cumulative_token_usage"]`（`input`/`output`/`total`/`cache_read`/`cache_write`，`.get(k, 0)` 兼容旧值） | — |
| **P5 取上一结果** | `:980-987` | 从最后一条 entry 取 `last_action_result` / `last_ability_name`；空则 `None` | — |
| **P6 `AFTER_ACTION`** | `:989-999` | 建上下文 `:989`、运行 `:990`、合并 `:992-993`；`route_to` → `iteration_state.pending_route` + `advance()` + `continue` `:994-997`；`!should_continue` → `break` `:998-999` | 出口 3 / 重入 |
| **P7 迭代上限** | `:1002-1015` | `if not is_unlimited and iteration + 1 >= max_iterations:` → `ON_MAX_ITERATIONS` hook `:1007-1008`、可选 routed ability `:1009-1014`、`break` `:1015` | 出口 4 |
| **P8 递增** | `:1017` | `self._iteration_state.advance()`——**方法最后一条语句** | — |

**五个出口**：`:798` 条件为假、`:822`、`:998-999`、`:1015`、`:971` 抛错。**三个重入**（不递增迭代）：`:806`（压缩）、`:967`（应急降级）、`:997`（route）。

预算由调用方设定，不由循环设定：`:553-558`（`max_iterations == 0` 直接 `RuntimeError`，要求非零）、`:634-635`、`:2068-2069`。失败迭代不消耗预算——`advance()` 只在 `:1017` 执行。

## 5. `_action`：一次 LLM 调用与工具执行

签名 `async def _action(self, accumulated_data: dict[str, Any]) -> dict[str, Any]`（`agents/base.py:1252-1459`）。

| 步 | 行号 | 内容 |
| --- | --- | --- |
| 1 | `:1266` | `llm = await self._ensure_llm()`（懒建工厂、`configure_tools`、注入摘要 LLM、按模型解析预算，`:428-473`） |
| 2 | `:1269-1273` | `PRE_LLM_CALL` hook，合并 `modified_context` |
| 3 | `:1276-1277` | `config.window is not None` 时 `_publish_context_usage("pre_call")` |
| 4 | `:1278-1279` | `messages = await cm.get_llm_messages()`；`llm.generate(messages)`——**全文件唯一生成调用点** |
| 5 | `:1286-1303` | 空可见响应重试：无 tool_calls 且正文空白时，在头部 system 消息之后插入 `_VISIBLE_RESPONSE_RETRY_PROMPT`（`:98-102`）再生成一次（`:1302`）；首次空响应**不写历史**（`:1282-1285`） |
| 6 | `:1306-1334` | 计量：每响应 `extract_token_usage` `:1312`、`extract_reasoning_content` `:1313`、`extract_response_metadata` `:1314`；CoT → `accumulated_data["cot_content"]` `:1317-1318`；`llm_meta["token_usage"]` 求和 `:1321-1330`；`response_metadata` `:1331-1332`；`visible_response_retry_count` `:1333-1334` |
| 7 | `:1339-1344` | 锚定：`first_usage = token_usages[0]` → `cm.record_real_usage(first_usage)` `:1341`（只记首个响应，重试提示不计，理由 `:1336-1338`）→ `_publish_context_usage("post_call", real_usage=first_usage)` `:1344` |
| 8 | `:1347` | AI 响应入历史 `cm.add_messages`（`source=f"agent:{name}"`） |
| 9 | `:1350-1354` | `POST_LLM_CALL` hook |
| 10 | `:1357-1362` | 解析 `tool_calls` / `text`；重试后仍全空 → `_EMPTY_VISIBLE_RESPONSE`（`:103`，文案 `"模型未返回可展示的正文，请重试。"`） |
| 11 | `:1370` | `accumulated_data["llm_response"] = response_content`——**总是**写入，含工具调用轮 |
| 12a | `:1372-1390` | 无 tool_calls → 走 `abilities["conversation"]`（`:1378-1380`，awaited，单结果）；缺 `conversation` 只记日志并返回零结果（节点记 `no_action`，循环继续） |
| 12b | `:1391-1447` | 有 tool_calls → `await self._ability_executor.execute_tool_calls(...)` `:1399-1405` |
| 13 | `:1407-1447` | `await_reply` 分支：任一结果 `data["await_reply"]` 为真 → 建 `delivery_context` `:1411-1417`、带 `pending_tool_replies`/`pending_tool_call_ids`/`delivery_context` 提交迭代 `:1418-1429`、发事件 `:1430-1436`、累积用量 `:1437-1442`、`llm_meta = {}` `:1444`（**不重复计费**）、`cm.begin_iteration()` `:1445`、`await _await_tool_results(results)` `:1446`、`_add_tool_results(results)` `:1447` |
| 14 | `:1459` | 返回 `{"results": results, "llm_metadata": llm_meta}` |

### 5.1 并行工具调用

多个 tool_call 交给 `LocalAbilityExecutor.execute_tool_calls`（`scheme/execution/executor.py:429-534`）：

- 未知 ability → 内联 `ActionResult(FAILURE, {"error": f"Unknown ability: {name}"})`（`:470-479`）；
- 已知调用 `asyncio.gather(..., return_exceptions=True)`（`:492-505`）**并行**，每个调用一个独立 `AbilityExecutionContext`、独立 PRE/POST_EXECUTE hook 与独立 HITL 等待；
- 单次调用同一 `Exception` → `FAILURE`（`:512-522`）；非 `Exception` 的 `BaseException`（取消）原样重抛（`:507-511`）；
- 单个 tool_call 也走同样的一元素 gather。

顺序限制：未知 ability 结果先入列，已知批次后入列，结果顺序可能与 `tool_calls` 顺序不一致（`ghrah-01-ability-design-and-implementation.md` §13.2 D-6）。

### 5.2 结果回流

`_add_tool_results`（`:1516-1536`）把每个结果转成 `ChatMessage.tool(tool_call_id=..., content=str(result.data), success=..., error=..., source=f"agent:{name}")`（`:1526-1534`），再 `cm.add_messages`（`:1536`）——这些消息成为下一轮 LLM 输入。

## 6. `_finish_response`

`_finish_response`（`:729-752`）：`response = self._build_response(message)`（`:732`）→ 取 `get_delivery_finisher(cm)` 并 `await finisher(response)`（`:734-737`）——**先把最终结果持久化，再通知观察端**，理由见注释 `:736`（"事件失败不丢回复"）→ 发 `AgentResponseEvent`（`:740-750`，`content`、`content_blocks`、`message_type="result"`、`metadata={"iteration": ...}`）→ 返回（`:752`）。普通 `receive` 与 `resume_atomic` 共用此方法（`:651`、`:578`）。

## 7. 上下文压缩

### 7.1 闸门

`_drive_loop` 的第一件事是 `cm.check_compact_trigger()`（`:803`）。判定在 `core/context/manager.py:1255-1273`：`_compact_requested` 为真（`:1266-1267`）直接成立；否则要求有阈值与 `window_manager`（`:1268-1269`）、锚点非空且未被 `_compact_skip_until_reanchor` 抑制（`:1270-1271`）、且 `_window_occupied >= int(max_tokens * compact_threshold)`（`:1272-1273`）。手动请求**无条件**优先。

闸门位置是硬序约束（`:799-802` 注释）：压缩轮**不执行任何 Agent HookPoint、不排空队列、不消耗 `max_iterations`、不 `advance`**。

### 7.2 压缩轮

`_run_compact_round(self, source: str, *, budget_override: int | None = None)`（`:1019-1197`），`source ∈ {"threshold","manual","emergency"}`（`:1034`）：

1. `get_window_status()`（`:1057`）：`keep_recent`（`:1058`）、estimator（`:1059`）、消费手动请求标记（`:1063`）；
2. `split_compact_windows(cm.get_history(), keep_recent)`（`:1065`）；返回 `None` 时消费手动请求（`:1067`）、仅对 `"threshold"` 调 `skip_compact_until_reanchor()`（`:1072-1075`）、返回 `{"executed": False, "reason": "empty_window", ...}`（`:1076`）；
3. `collect_window_messages`（`:1079`）；
4. `window_config.tool_call_max_length`（缺省 500）`:1087-1088` → 两侧 `fold_long_tool_outputs`（`:1089-1092`）；
5. `budget = budget_override or status["budget_tokens"]`（`:1094-1096`）；
6. `truncate_summary_input`（超出预算 ×2 时丢最旧，`:1099-1101`）；
7. 摘要 LLM：agent 自己的 LLM + `COMPACT_SUMMARY_PROMPT`（`:1108-1115`），用量捕获（`:1119-1125`）；空/失败 → `degraded=True`（`:1126-1133`）；
8. `assemble_snapshot`（`:1141-1147`）或降级的折叠视图（`:1150-1156`）；
9. `post_check_snapshot`（`:1158-1164`）；
10. 元数据（`trigger_source`、`method: "ghrah.builtin"`、`summarized_range`、`kept_node_ids`、`kept_recent_nodes`、`tokens_before/after`、`post_check`、`degraded`、`truncated_summary_input`、`usage`）`:1166-1179`；
11. `await cm.commit_compact_node_async(snapshot, metadata)`（`:1181`）→ `consume_compact_request()`（`:1182`）→ `ActionChainUpdatedEvent`（`:1184-1190`）→ 返回 dict（`:1192-1197`）。

压缩轮用的方法就是 `ghrah.builtin`（元数据 `:1168`，`core/types/config_types.py:61-64` 说这是唯一合法值），由 `ghrah.context.compact` 的原语（`core/context/compact.py:74/95/135/190/217/259`）在 `base.py` 内联拼装。**不是** `WindowConfig.strategies` 那条流水线——后者在每次发送时由 `cm.get_llm_messages()` → `WindowManager.apply`（`manager.py:1107-1112`）执行，两者互不替代。

硬序约束（`:1028-1031`）：压缩轮**必须**产出节点；摘要 LLM 失败就降级成确定性的折叠视图。

### 7.3 相关配置

| 键 | 默认 | 位置 |
| --- | --- | --- |
| `window.max_tokens` | `None`（回落 `DEFAULT_WINDOW_MAX_TOKENS = 200_000`） | `core/types/config_types.py:43`、`:12` |
| `window.strategies` | `["tool_call_fold", "truncation"]` | `:44` |
| `window.tool_call_max_length` | `500` | `:45` |
| `window.sliding_window_size` | `20` | `:46` |
| `window.compact_threshold` | `None`（关闭），合法区间 `(0, 1]` | `:47`、`:52-55` |
| `window.compact_keep_recent` | `2`，必须 `>= 2` | `:48`、`:56-60` |
| `window.compact_method` | `"ghrah.builtin"` | `:49` |
| `max_iterations` | `10`；`-1` 无限；`0` 在恢复时被拒 | `:154`、`agents/base.py:553-558` |

`compact_method` 的唯一合法值定义在 `core/types/config_types.py:15`（`DEFAULT_COMPACT_METHOD`）。

## 8. 异常、回滚与应急降级

`_action` **没有 try/except**（`:1252-1459`）：所有异常上抛到 `_drive_loop` 的 `except`（`:906-971`）。只有三处内部吞异常：`_publish_context_usage` 捕 `Exception` 仅 warning（`:1244-1250`，调用点 `:1277`、`:1344`）；执行器把单次工具 `Exception` 折成 `FAILURE`（`executor.py:512-522`）；`_await_tool_results` 把异常折成 `FAILURE`（`:1498-1505`）。

`except` 分支（`:906-971`）的语义：

1. 发 `AgentErrorEvent`（`:908-913`）；
2. 若 `cm.in_iteration` → `cm.rollback_iteration(e)`（`:916-917`）：创建并激活 `retry-{n}` 分支、写入 `is_rollback` 元数据、回滚状态事务（`core/context/manager.py:984-1019`）；
3. 仅当输入尚未提交（`get_iteration_committer(cm) is None` 或无 `delivery_id`）时，把排空的消息重新入队（`:918-923`）——注释 `:917-918`：已持久提交后，事件失败**不得**重新注入输入；
4. 唯一的**不抛**路径（`:931-967`）：`_is_context_limit_error(e)`（`:123-129`，类型名标记 `contextwindowexceeded`/`contextlengthexceeded` 见 `:108`，消息标记 `:109-117`）且存在 `window_manager` 且 `emergency_halvings < 4` → 用厂商窗口值回填（`parse_vendor_window_tokens` `:936-939`）或预算减半（`:940-960`）→ 有 committer 时把 `delivery_id` 消息重新入队（`:961-965`）→ `await self._run_compact_round("emergency", budget_override=...)`（`:966`）→ `continue`（`:967`，迭代不递增）；
5. 否则（`:969-971`）：先跑 `ON_ERROR` hook，再 `raise AgentError(self.config.name, f"Action failed: {e}") from e`。

应急降级上限 `_MAX_EMERGENCY_HALVINGS = 4`（`:120`），第 n 次减半即 `max_tokens // 2**n`（`:943`、`:953`）。

失败后留下的状态：没有 `ContextNode` 被提交；迭代计数不变；`retry-{n}` 分支处于激活状态（分叉点 = 迭代前 head）；消息按上述规则重新入队。

`finally`（`:972-977`）无条件 `cm.ensure_iteration_closed()`（幂等，覆盖 `BaseException`）。相应的关闭动作见 `core/context/manager.py:952-982`：丢弃 pending 缓冲、回滚状态事务、清 `_window_occupied`、warning 日志。

`asyncio.CancelledError` 绕过 `except Exception`，由 `finally` 收尾后继续向上传播。

## 9. 硬序约束清单

代码注释里以"必须/不得/先于"表述的约束，改循环前逐条确认：

| # | 约束 | 注释位置 |
| --- | --- | --- |
| C-1 | 压缩闸门位于 `BEFORE_ACTION` 之前；压缩轮不发 Agent HookPoint、不排空队列、不计 `max_iterations`、不 `advance` | `agents/base.py:799-802` |
| C-2 | 压缩决策必须在 `commit_iteration_async` **之前**产生，随节点一起落库；回滚（无节点）时决策一起消失；`needs_compaction` 只是审计字段，真正的触发在 `:803` 重新判定 | `:853-857` |
| C-3 | 压缩轮必须总产出节点，摘要失败降级为确定性折叠视图 | `:1028-1031` |
| C-4 | `llm_response` 永远是"本轮可见文本"，含工具调用轮；`end_task` 必须消费本轮文本而不是上一次工具结果 | `:1366-1370` |
| C-5 | 同步 await 只允许在"准备消息 + 执行状态"同一事务提交之后发生 | `:1407-1408` |
| C-6 | 等待态节点已经记过模型费用，结果节点不得再记（`llm_meta = {}`） | `:1443-1444` |
| C-7 | 持久提交后，事件失败不得重新注入输入 | `:917-918` |
| C-8 | 内部压缩重试属于同一次投递，回滚后持久输入必须重新入队 | `:961-962` |
| C-9 | 不要在此处再追加 LLM 响应（会产生节点外的幽灵消息 → 持久化/恢复后最终回复重复） | `:1742-1744` |
| C-10 | `remove_state` 必须在本地变更之前调用，护栏抛错时注册表保持未修改 | `:273-276` |
| C-11 | 先持久化最终结果，再通知观察端 | `:736` |
| C-12 | 首次空响应不得写入历史 | `:1282-1285` |
| C-13 | `commit_compact_node*` 在 `_in_iteration` 期间拒绝执行——这正是闸门必须位于 `begin_iteration` 之前的原因 | `core/context/manager.py:1320-1321`、`:1351-1352` |

## 10. 不变式与已知偏差

### 10.1 不变式

| 编号 | 不变式 | 校验点 |
| --- | --- | --- |
| L-01 | 一次 `_drive_loop` 最多提交 `max_iterations` 个迭代；失败迭代不计数 | `:1017` 是唯一 `advance` |
| L-02 | 每个迭代恰好一次 `begin_iteration`（`:825` / await_reply 的 `:1445`）与一次 `commit_iteration_async`（`:861`），二者由 `finally` 的 `ensure_iteration_closed` 兜底 | `:825`、`:861`、`:972-977` |
| L-03 | 压缩轮永不递增迭代计数 | `:806` 的 `continue` 在 `:1017` 之前 |
| L-04 | 工具调用并行，单次失败不影响同批其他调用 | `executor.py:492-522` |
| L-05 | 取消信号不被任何层折算成结果 | `executor.py:507-511`、`agents/base.py:972-977` |
| L-06 | 上下文超限是可自愈错误（最多 4 次降级），其他错误一律 `AgentError` | `:931-971`、`:120` |
| L-07 | 最终回复先持久化后广播 | `:734-737` |

### 10.2 已知偏差

| 编号 | 偏差 | 证据 | 影响 |
| --- | --- | --- | --- |
| LD-1 | `pending_route` 字段已死 | `agents/iteration_state.py:13`、`:33` 自述"documented dead" | 读它做判断会永远为 `None` |
| LD-2 | 结果顺序可能与 `tool_calls` 顺序不一致 | `executor.py:462-480` vs `:506-525` | 并行多工具时结果对位要靠 `tool_call_id` |
| LD-3 | `window.max_tokens` 未配置时静默用 200k | `core/types/config_types.py:12`、`:43` | 换模型不改配置会导致压缩阈值失真 |
| LD-4 | `compact_threshold` 默认关闭 | `core/types/config_types.py:47` | 不配就永不自动压缩，只能手动 |
| LD-5 | 应急降级只看异常类型名/消息文本 | `agents/base.py:108-117` | 换供应商的错误文案可能不触发自愈 |
| LD-6 | `_publish_context_usage` 静默吞异常 | `:1244-1250` | 前端用量面板可能缺数而不报错 |
| LD-7 | `_receive_once` 把非 `AgentError` 异常转成 ERROR 回复 | `:655-664` | 上层看不到原始异常类型 |

## 11. 与其他子系统的接缝

| 接缝 | 位置 | 说明 |
| --- | --- | --- |
| 工具面 | `register_ability` `:212-259` → `_refresh_bound_tools` `:416-426` → `_ensure_llm` `:454` 的 `configure_tools` | 工具 schema 来自各能力实现的 `bind_tool()`；新增/移除能力会刷新工具面 |
| 技能目录注入 | `core/context/manager.py:1114-1161` | 在窗口管理**之后**追加一条 `source="skill:catalog"` 的 user 消息，详见 `ghrah-04-skill-system.md` |
| 能力执行 | `_ability_executor.execute_tool_calls` `:1399-1405` | 详见 `ghrah-01-ability-design-and-implementation.md` §9 |
| 事件 | `ActionChainUpdatedEvent` `:870-882`、`AgentResponseEvent` `:740-750`、`AgentErrorEvent` `:908-913` | wire 载荷见 `ghrah-protocol/src/ghrah/protocol/payloads/agent.py:194` |
| 落盘 | `cm.commit_iteration_async` `:861` | Core sqlite 为写侧权威，见 `ghrah-05-build-startup-and-troubleshooting.md` §6 |