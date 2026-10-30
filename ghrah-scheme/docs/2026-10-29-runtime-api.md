---
title: 运行时 API：契约、原语与生命周期
description: 按当前实现说明 ghrah.scheme 的类型、五个消费原语、发现、受信装配和收尾行为
tags: [Scheme, API, Python, 生命周期]
created: 2026-10-29
updated: 2026-10-29
status: active
---

# 运行时 API：契约、原语与生命周期

> 本文是当前进程内 Python API 的使用参考。示例可在 monorepo 环境执行；产品全局树和获授节点变更另见系统模型，不以此冒充已经实现的 API。

校准基线：`acf06fe`，2026-10-29，`ghrah-scheme 0.1.0a0`。公开导出见 [__init__.py](../src/ghrah/scheme/__init__.py)，签名见 [runtime.py](../src/ghrah/scheme/runtime.py)。

## 1. 三种角色与当前调用关系

宿主在运行中的 asyncio loop 内显式创建 `SchemeRuntime(authority, limits=...)`，保留受信 `runtime.manager`，只向消费者注入 `Session`。当前对象只在创建它的 loop 上使用。

```text
装配者：publish → bind → create_session
消费者：catalog／describe → acquire → submit → observe → release
提供者：接收 ProviderCall → execute → 返回 SchemeModel
关闭：消费者 close；装配者 withdraw／close，检查残余
```

装配者提供的 Principal 必须已经核定；manager 不是认证服务。当前进程内端口不提供对任意恶意 Python 代码的隔离。进程外传输和 OS 沙箱尚未实现。

## 2. 类型与路径

| 类型 | 当前含义 |
| --- | --- |
| `Principal(project_id, agent_id, run_id, display_name=..., namespace=...)` | 可信 Agent 主体；前三个字段构成 scope，后两者仅为元数据 |
| `ControlPrincipal(project_id, subject_id, session_id)` | 独立管理主体，不冒用目标 Agent；是否可管理仍由宿主 Authority 核定 |
| `ControlScope(project_id, subject_id, session_id)` | 与 AgentScope 分离的管理会话作用域，字段同值也不相等 |
| `AgentScope(project_id, agent_id, run_id)` | 当前运行的名字与去重范围 |
| `SchemeModel` | 严格 Pydantic 输入／输出基类，拒绝未知字段和隐式类型转换 |
| `ActionSpec(name, input_model, output_model, ...)` | 动作契约；可声明 mutating、cancellable、description |
| `SchemeSpec(kind=..., contract=..., version=..., actions=(...))` | 资源种类下的确定契约，含内容摘要与 descriptor 导出 |
| `Target` | 固定 scope、kind、alias、resource、provider、incarnation、契约及绑定修订 |
| `ResourceRef` | 当前 boot／会话持有的受限资源引用，不是可随意构造的授权 |
| `InvocationRef` | 一次调用的观察和控制引用 |
| `ProviderCall` | 隔离的主体、目标、动作、已验证参数、调用 ID 与接纳依据 |

路径形式为 `/scheme/<kind>/<alias>/<resource...>`。kind 和 alias 使用小写字母起始的字母、数字、下划线或连字符。路径总长有上限；拒绝空段、`.`、`..`、反斜杠、百分号、查询／片段和 NUL。路径不是磁盘路径，不做 shell 或 realpath 解释。

`_target()` 先按 `(principal.scope, kind, alias)` 选择提供者，再把剩余段作为 resource。剩余段的含义由契约和提供者解释；运行时不会自动将 `operations/render` 分派为动作 render，调用方仍显式提交 `action`。

同 kind 可有不同 contract；同一 `(contract, version)` 的内容不可原地改变，也不能改挂到不同 kind。descriptor 的精确指纹含动作 schema 和执行标记，动作按名称排序、对象键排序、数组保序。它不证明来源可信，不承诺跨 Pydantic 版本稳定或任意 schema 语义兼容。

### 2.1 从同一作者提供 schema 与运行校验

既有 `ActionSpec(name, Input, Output)` 保持可用。对于已有严格领域模型，可使用 `ActionSpec(name, codec=PydanticCodec(Input, Output))`；领域模型及嵌套模型必须 strict 且 extra=forbid。对于确定 YAML schema，使用 `JsonSchemaCodec(input_schema, output_schema)`，未支持的关键字在发布前拒绝。codec 与 input_model／output_model 只能选择一套作者。

codec 提供者收到并返回 `CodecArguments(value=...)`；value 是内部已校验 JSON 包装，公开调用参数、结果和 descriptor 不包含这一层。输入验证、输出验证和描述导出均使用同一个 codec。支持的 JSON Schema 子集与实际文件使用见[文件资源切片](2026-10-29-ability-resource-slice.md#5-契约与-ui-如何独立开发)。

## 3. 最小完整示例

下面把纯文本逻辑接成一个只读 Operation，演示逻辑、提供者和契约分开。固定演示策略只允许这一个 Agent scope、provider、成员与动作；它不实现产品获授树。

在 monorepo 根的 `uv run python` 中执行完整代码，输出 `hello scheme`。

<!-- runnable: normalizer -->
```python
import asyncio

from ghrah.scheme import (
    AccessRequest,
    ActionSpec,
    Admission,
    Authorization,
    InvocationState,
    Principal,
    ProviderCall,
    SchemeModel,
    SchemeRuntime,
    SchemeSpec,
)


def normalize(text: str) -> str:
    return text.strip().lower()


class NormalizeInput(SchemeModel):
    text: str


class NormalizeOutput(SchemeModel):
    text: str


PRINCIPAL = Principal("demo-project", "demo-agent", "demo-run")
SPEC = SchemeSpec(
    kind="ability",
    contract="demo.text",
    version="1",
    actions=(ActionSpec("normalize", NormalizeInput, NormalizeOutput),),
)


class DemoAuthority:
    async def authorize(self, request: AccessRequest) -> Authorization:
        allowed = (
            request.principal.scope == PRINCIPAL.scope
            and request.target.provider_id == "text-1"
            and request.target.contract == SPEC.contract
            and request.target.resource in {(), ("operations", "normalize")}
            and request.actions <= frozenset({"normalize"})
        )
        return Authorization(allowed, "demo-fixed-scope" if allowed else "")

    async def admit(self, request: AccessRequest) -> Admission:
        authorization = await self.authorize(request)
        return Admission(authorization.allowed, authorization.basis)


class TextProvider:
    async def execute(self, call: ProviderCall) -> SchemeModel:
        assert call.action == "normalize"
        assert isinstance(call.arguments, NormalizeInput)
        return NormalizeOutput(text=normalize(call.arguments.text))


async def main() -> None:
    runtime = SchemeRuntime(DemoAuthority())
    try:
        runtime.manager.publish("text-1", SPEC, TextProvider())
        runtime.manager.bind(
            PRINCIPAL.scope,
            "ability",
            "text",
            "text-1",
            contract=SPEC.contract,
            contract_version=SPEC.version,
            contract_digest=SPEC.digest,
        )
        session = runtime.manager.create_session(PRINCIPAL)
        resource = await session.acquire(
            "/scheme/ability/text/operations/normalize",
            actions=frozenset({"normalize"}),
            contract=SPEC.contract,
            contract_version=SPEC.version,
        )
        try:
            invocation = await session.submit(
                resource,
                action="normalize",
                arguments={"text": " Hello Scheme "},
            )
            try:
                snapshot = await session.observe(invocation, wait=1.0)
                while snapshot.pending:
                    snapshot = await session.observe(
                        invocation,
                        wait=1.0,
                        after_version=snapshot.version,
                    )
                assert snapshot.state is InvocationState.COMPLETED
                assert snapshot.result == {"text": "hello scheme"}
                print(snapshot.result["text"])
            finally:
                await session.release(invocation)
        finally:
            await session.release(resource)
        assert (await session.close()).complete
    finally:
        assert (await runtime.manager.close()).complete


asyncio.run(main())
```

## 4. 五个消费原语

五个原语管理访问与调用，不为每个业务动作增加新的公共分发入口。

| Session 方法 | 参数与结果 | 必须理解的语义 |
| --- | --- | --- |
| `acquire(target, *, actions, contract=None, contract_version=None, contract_digest=None)` | 路径及动作上限 → ResourceRef | 解析、核定并固定目标；不新建领域许可 |
| `submit(reference, *, action, arguments, request_key=None, timeout=None)` | 资源引用和严格输入 → InvocationRef | 有界提交；返回引用不等于业务已接纳 |
| `observe(reference, *, wait=0, after_version=None)` | 调用引用 → InvocationSnapshot | 有限等待或查询；观察超时不取消执行 |
| `cancel(reference)` | 调用引用 → CancelDisposition | 协作式取消；不保证外部效果回滚 |
| `release(reference)` | 资源／调用引用 → ReleaseReport | 释放持有关系；不撤销许可，不取消已接纳调用 |

`arguments` 接受 JSON 对象或 SchemeModel。输入输出在本地调用中也验证和复制；provider 不能通过返回裸 handler 绕过契约。

变更动作 `mutating=True` 要求显式 request_key。相同 scope 和键在当前 boot 中关联同一目标与输入；改变内容或绑定会冲突。记录过期返回 `REQUEST_EXPIRED`，不会因忘记结果而重新执行；键索引满时拒绝新增。跨 boot 持久去重及外部业务对账归领域 owner。

## 5. 发现和核定

`await session.catalog(kind=None)` 返回 CatalogSnapshot；`await session.describe(kind, alias)` 返回 DiscoveredResource。它们枚举当前 scope 的绑定，不是通用递归列目录，也不提供全局 Agent／实例枚举。

每个动作分别核定 DISCOVER、DESCRIBE 和 INVOKE。无可见动作的资源不出现；describe 对隐藏和不存在均返回 NOT_FOUND。schema 不可读时相应字段为 None；provider 不可用时可保留可见摘要，但 invokable 不会声称可执行。当前发现返回有界完整目录，没有分页或订阅。

宿主实现 `Authority.authorize(request) → Authorization` 和 `Authority.admit(request) → Admission`。前者核定发现、schema、调用意图、引用取得和结果读取，后者核定当前许可并提交必要的领域接纳。允许结果必须有 basis；Admission 可关联领域 record_id。未注入时使用 DenyAuthority。

发现、引用取得与实际接纳之间允许状态变化；运行时复核绑定和提供者，领域端口复核当前事实。领域撤销与接纳顺序由同一权威定义，不要求只读查询先持久写一条接纳事务，也不宣称领域事务与外部效果原子提交。

## 6. 状态、结果和取消分别表达

InvocationSnapshot 分别给出以下维度，避免把“返回引用”“返回结果”“业务成功”和“已回滚”混成一个布尔值。

| 字段 | 当前语义 |
| --- | --- |
| state | queued／admitting／admitted／running／completed／rejected／cancelled／failed |
| admission | not_requested／pending／accepted／rejected／unknown |
| effect | not_dispatched／unknown／reported |
| cancellation、cancel_reason | 取消意图与原因，独立于终态 error |
| result、result_redacted | 获准读取的 JSON 正文及其是否被遮蔽 |
| version、pending | 当前快照修订及是否仍有运行责任 |

COMPLETED 表示 provider 返回了符合契约的结果，业务成败仍由输出模型解释。请求取消后 provider 可能返回有效结果，此时可为 COMPLETED 且 cancellation 为 REQUESTED。观察 wait 仅限制状态等待，结果读取另有核定预算；撤销后仍允许取得自有调用的最小收尾状态，正文可被遮蔽。

取消是协作请求，不是 OS 强杀或外部回滚。父调用取消传递给已登记的子调用，取消后不能继续发起嵌套工作。同 provider 重入被拒绝，跨 provider 嵌套遇到排队风险返回 NESTED_BUSY，避免执行槽互等；不提供公平排队或硬实时保证。

## 7. 受信装配与资源收尾

管理端口只交给受信装配者；它不替领域授予 Agent 权限。

| RuntimeManager 方法 | 作用 |
| --- | --- |
| `publish(provider_id, spec, provider, *, concurrency=1, pending_limit=16, owner=None)` | 登记导出，返回 incarnation；无 owner 时资源准备和关闭由宿主负责 |
| `bind(scope, kind, alias, provider_id, *, contract, contract_version, contract_digest=None)` | 核对期望契约并安装局部接线，返回绑定修订 |
| `unbind(scope, kind, alias)` | 撤去该运行的局部接线 |
| `create_session(principal)` | 为已核定主体建立消费会话 |
| `create_owner(lifecycle)` | 显式登记 ProviderLifecycle，返回 OwnerRef |
| `wait_prepared(owner, *, timeout=1.0)`、`owner_status(owner)` | 等待准备或读取 owner 快照 |
| `report_health(provider_id, incarnation, state)` | 报告确定启动实例的健康状态 |
| `withdraw(provider_id, *, timeout=1.0, cancel=False)` | 停止新访问并收束该导出 |
| `retry_close(owner, *, timeout=1.0)` | 显式重试已结束但失败的关闭 |
| `close(*, timeout=1.0)`、`diagnostics()` | 关闭整个运行时或取得只读诊断 |

ProviderLifecycle 只有 `prepare()` 和 `aclose()` 两个异步方法。同 owner 可共享多个导出，prepare 执行一次；最后一个导出退出且在途责任结束后关闭资源。关闭一个 Session 或撤回其中一个共享导出，不关闭其他导出使用的资源。

准备有执行预算，wait_prepared 的超时只结束等待。准备取消后迟到成功不会重新发布 READY。健康报告核对 incarnation，旧实例不能更新新实例。单次业务异常不自动把 provider 判为崩溃。

关闭返回 DrainReport，complete 同时检查 pending 调用、核定任务和 pending_owners。失败或非合作关闭保留责任，不能仅凭等待超时报“已停止”，也不能并发 retry_close。旧实例尚有未收束责任时，同名替换被阻止。WITHDRAWN 只表示逻辑导出退出；物理进程或连接由唯一生命周期适配器负责。

`Session.close(timeout=...)` 明确取消自有调用并释放自身引用；其残余仍归原 owner。调用结束或逻辑引用释放不自动替具体领域打开／关闭文件，也不替远端管理租约。

## 8. 容量、错误与验证

RuntimeLimits 明确管理表、会话引用、在途调用、核定、发现、观察、正文和去重容量。限额字段及默认值以 [models.py](../src/ghrah/scheme/models.py) 的 RuntimeLimits 为准。表满显式拒绝新增，当前 boot 内的契约、provider 标识和去重索引不能假定会无限安全回收。

常见直接入口错误包括 INVALID_PATH／INVALID_INPUT、CONTRACT_MISMATCH、ACCESS_DENIED、AUTHORITY_UNAVAILABLE、STALE_BINDING／STALE_PROVIDER、BUSY，以及 REQUEST_KEY_REQUIRED／REQUEST_CONFLICT／REQUEST_EXPIRED。完整分类见 [errors.py](../src/ghrah/scheme/errors.py)；通过 SchemeError.code 判断，不解析错误字符串。

在 monorepo 根验证当前机制：

```sh
uv run pytest ghrah-scheme
uv run ruff check ghrah-scheme
MYPYPATH=ghrah-scheme/src uv run mypy --explicit-package-bases -p ghrah.scheme
```

## 相关文档

- [文件 Ability 资源切片：权威关系、Scheme 接入与面板](2026-10-29-ability-resource-slice.md) — 当日工作区新增的可执行文件资源闭环与独立面板。

- [ghrah-scheme](../README.md) — 安装与文档入口。
- [Scheme 系统模型：全局树与局部入口](2026-10-29-system-model.md) — 当前局部 API 与目标全局树的关系。
- [资源、权限与节点必要性](2026-10-29-resources-and-authority.md) — 核定端口所消费的领域事实。
- [Ability 容器 API 与 Scheme 接入](2026-10-29-ability-api.md) — 将成员执行接到 Provider 的方法。
- [二次开发：逻辑、Scheme 接入与 UI 并行推进](2026-10-29-development-guide.md) — 示例中三个职责如何分别实现。
- [第一代文档的实现状态与代码地图](2026-10-29-implementation-status.md) — 已覆盖的测试和未实现范围。
