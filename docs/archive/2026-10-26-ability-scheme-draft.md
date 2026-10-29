--
title: Ability scheme 初步定义
description: 为新版 Ability 容器定义局部地址、成员契约、提供者绑定、授权记录、访问入口和引用失效规则，作为首个原型的讨论基线
tags: [设计, 能力系统, Scheme]
keywords: [AbilityDefinition, Operation, Skill, Asset, Binding, Grant, Invocation]
created: 2026-10-26
updated: 2026-10-26
status: draft
---

# Ability scheme 初步定义

> 本文把 [Scheme 与 Ability 容器模型](../10-Scheme与Ability容器模型.md) 的方向推进为第一份可以逐项讨论、制作原型的定义。这里的 scheme 指一种服务资源域，包含寻址、绑定、访问和生命周期规则；schema 指承载这些规则的数据结构，二者分别说明。
>
> 本文是设计草案。下述字段、方法名和错误码均为候选，不是已发布协议，也不表示当前运行时已经支持。本文只定义 Ability 域，不先建立通用 scheme 框架。

本轮工作优先转向由对象需求推导[公共基座设计](2026-10-26-scheme-foundation-design.md)，实施依据见[基座调研](../analysis/2026-10-26-scheme-foundation-research.md)。本草案作为需求样本；共用描述符、引用和调用上下文应向公共模型归并，不维护并行权威。

## 1. 定义全景：一个域、三类成员、三种访问动作

Ability scheme 是 Agent 访问能力容器的受控入口。它把当前 Agent 的局部名字解析为一个确定版本的 Ability，按授权提供成员摘要、内容或执行入口。

该 Agent 的局部视野可经 [View scheme](2026-10-26-view-scheme-draft.md) 的只读投影统一查看，包含能力可见／可用面、上下文引擎、manifest 与状态。View 中的能力条目引用本域的确定 binding 和 member，不创建第二套成员契约、执行入口或权限约束；本域 catalog、resolve 与 invoke 继续依据 Agent 的有效授权、命名空间与策略判断。

建议采用以下最小形态：

```text
/scheme/ability/reports
  ├─ operations/inspect       执行动作
  ├─ operations/render        执行动作
  ├─ skills/reporting         说明内容
  └─ assets/report-template   静态内容

局部别名 → 确定定义与提供者的绑定 → 授权检查 → 读取或调用
```

| 对象 | 是什么 | 可授予的访问动作 |
| --- | --- | --- |
| Ability | 具有共同身份、版本和治理归属的成员容器 | `discover`：发现容器摘要 |
| Operation | 一项可执行动作的定义 | `discover`、`invoke` |
| Skill | 一份说明性技能及其明确内容关联 | `discover`、`read` |
| Asset | 一个不可变静态内容对象 | `discover`、`read` |

`read`、`invoke` 都包含访问该成员必要摘要的权限，并使其容器摘要可见。只有 `discover` 时可看摘要，不可读正文或执行。一个 Ability 至少有一个成员，三类不必齐全；纯 Skill、纯 Asset、纯 Operation 容器都合法。

授权管理是另一层职责。`grant`、`revoke` 可以由受控管理入口或专门的 Operation 暴露；普通成员的 `invoke` 权不会自动包含这些管理权。委托上限也不从容器可见性推导。

本文沿用四条已有方向：定义不可原地变更；安装不等于授权；名字不是权限凭证；工具列表和内容目录从权威授权事实派生。

## 2. 身份与名字：哪些可以改，哪些必须钉住

相同的 `reports` 在两个 Agent 中可以指向不同实现。因此，局部名字、定义身份和运行引用必须分别保存。

### 2.1 身份表

| 名称 | 示例 | 用途 |
| --- | --- | --- |
| 定义归属与名称 | `namespace=acme`、`name=reports` | 表达作者定义的身份；归属字符串本身不是可信来源证明 |
| 定义发布版本 | `0.1.0` | 人读的版本标签，不单独保证内容相同 |
| 定义摘要 | `definition_digest` | 固定成员、契约和内容引用的确切版本 |
| 包内容摘要 | `package_digest` | 固定文件、脚本和依赖锁等安装内容 |
| 公共契约身份与摘要 | `acme.reporting.v1`、`contract_digest` | 核对不同实现是否承诺同一边界 |
| 成员键 | `operations/render` | 在该确定定义内定位一个成员 |
| 局部别名 | `reports` | 在一个 Agent 运行实例中寻址 |
| 绑定身份与代次 | `binding_id`、`generation` | 区分接线关系以及引用失效前后的代次 |
| 提供者运行实例 | `provider_instance_id` | 区分同一部署配置的不同启动实例 |
| 授权与调用身份 | `grant_id`、`invocation_id` | 分别定位许可事实和一次执行 |

成员不另设一份可以与 map 键冲突的 `name` 字段。静态成员身份是“定义摘要 + 类别 + 成员键”。不同版本中的同名成员，不自动视为已经获得同一授权。

`acme.reports` 可以作为显示用全名；机器载体保留 `namespace` 和 `name` 两个字段，不反向拆字符串猜测边界。协议版本、定义版本和提供者启动身份分别管理。

### 2.2 路径风格建议：采用 /scheme/ability 下的绝对路径

**本轮建议采用斜杠分段的绝对逻辑路径，以 `/scheme/ability` 为 Ability 服务域入口。** Redox 当前 Book 的资源访问示例已使用 `/scheme/tcp/127.0.0.1/3000`，其目标说明也将 scheme 放在 `/scheme` 下。因此，借鉴 scheme 不要求采用 `tcp:` 一类冒号写法。[来源：Redox Book — Schemes](https://raw.githubusercontent.com/redox-os/book/master/src/schemes.md)、[Our Goals](https://raw.githubusercontent.com/redox-os/book/master/src/our-goals.md)。

路径选择应服务于本项目的局部命名空间模型。三个候选的取舍如下：

| 候选 | 特点 | 本轮建议 |
| --- | --- | --- |
| `ability:reports/operations/render` | 简短，采用独立的冒号前缀语法 | 不作为第一版规范形式，避免另外引入一套 URI 风格的寻址规则 |
| `/ability/reports/operations/render` | 较短，直接占据逻辑根的一级名字 | 暂不采用；保留一个明确的服务域入口便于解释和后续组织 |
| `/scheme/ability/reports/operations/render` | 显式呈现服务域、容器别名、成员类别和成员键 | 作为第一版规范路径建议，接受多一个固定前缀的长度成本 |

采用该建议后，四种资源地址为：

```text
/scheme/ability/<alias>
/scheme/ability/<alias>/operations/<key>
/scheme/ability/<alias>/skills/<key>
/scheme/ability/<alias>/assets/<key>
```

这里 `/scheme` 是逻辑命名空间的保留入口，`ability` 是服务资源域种类，`<alias>` 是当前 Agent 的容器绑定别名。`/scheme/reports/...` 会把容器别名放到服务域种类的位置，因此不作为本模型的写法。

“绝对”指从当前 Agent 的逻辑视图根开始解析。同一条 `/scheme/ability/reports` 在不同 Agent 中仍可绑定不同目标；前导 `/` 不引入全局身份。此路径不要求宿主真的创建 `/scheme` 目录，也不交给宿主文件系统的 realpath 或 shell 解释。

第一版只接受规范绝对路径，不接受 `ability:` 兼容别名、相对地址、查询参数、片段、authority 或跨 Agent 地址。未来如果需要简写或挂载别名，须通过显式映射解析到同一绑定，不能成为第二套授权事实。

别名与成员键暂限 `[a-z][a-z0-9_-]{0,63}`，大小写敏感。每类内部键唯一，不同类别可重名。静态 `namespace` 暂限小写 ASCII 段以点连接，每段同上述规则；静态 `name` 同成员键。长度规则是原型建议，后续需结合协议和工具投影验证。

拒绝空段、尾部斜杠、`.`、`..`、反斜杠、百分号编码和额外路径段，不进行自动折叠、二次解码或大小写修正。`assets/report-template` 中的键是逻辑标识，不是磁盘路径。

`/scheme/ability` 是成员容器的目录入口，通过 `catalog` 查询；`/scheme` 作为保留前缀不在本轮扩展为完整的通用服务目录。Operation 继续使用类型化 `invoke`，Skill／Asset 使用受控 `read`，斜杠语法本身不赋予通用文件 I/O 语义。结构化请求直接使用 `binding_id` 与 `{kind, key}`，不必先拼路径再解析。

### 2.3 名字解析与引用签发

地址以可信连接／执行帧中的 Agent 运行身份为解析上下文。载荷中自报的 `agent_id` 不能改变这个上下文；面向 Observer 的管理查询需要独立的查询授权。

内部解析先定位绑定，再核对成员和所需动作，最后才签发受限引用。对无权发现的目标与不存在的目标，Agent 接口统一返回 `NOT_FOUND_OR_NOT_VISIBLE`；内部审计保留真实原因。若调用者已获准发现，才可进一步区分 `ACCESS_DENIED` 与 `UNAVAILABLE`。这一规则不承诺时序等侧信道完全消失。

地址是查询条件，不是稳定执行引用。别名重绑定后，旧引用不得被重新解释为新目标。

## 3. 静态定义：容器说清“提供什么”，组件说清“如何承载”

静态定义描述能力本身；组件清单与部署组合选择实现和接线。二者通过明确的接口要求关联，不在 Ability 内重复写一套进程启动系统。

### 3.1 AbilityDefinition 候选字段

| 字段 | 必需性 | 含义 |
| --- | --- | --- |
| `format` | 必需 | 固定为 `ghrah.ability-definition`，区分描述符种类 |
| `schema_version` | 必需 | 本草案暂用 `0.1-draft`，未知版本拒绝 |
| `identity.namespace`、`.name`、`.version` | 必需 | 定义归属、名称和发布版本 |
| `title`、`description` | 必需 | 面向人的标题和摘要；不含授权断言 |
| `provider_requirements` | Operation 按需 | 按槽声明接口身份、摘要和必需执行能力 |
| `operations`、`skills`、`assets` | 必需 map，可为空 | 键为各类成员键，三者总成员数至少为一 |

第一版未知字段拒绝。`definition_digest`、安装核定结果、可信来源和实际提供者实例由发布／安装路径生成，不接受作者在定义里自签“可信”。运行时的 `effective_permissions` 不进入静态定义。

公共契约描述符中的输入输出与本定义不能各自维护一份。若组件单独导出 contract 文件，重叠事实必须从同一规范对象生成并校验；初版按确定版本和摘要精确匹配，不承诺自动判断任意两个 schema 的语义兼容。

### 3.2 OperationDefinition 候选字段

每个 Operation 既要说明参数，也要说明完成、取消和副作用的含义。只有工具参数不足以成为完整调用契约。

| 字段 | 必需性 | 含义 |
| --- | --- | --- |
| `title`、`description` | 必需 | 动作标题与摘要 |
| `input_schema`、`output_schema` | 必需 | 输入与成功结果的数据结构；具体 schema 方言在原型前选定 |
| `errors` | 必需，可空 | 已知业务错误码及其数据结构；网关错误另行统一 |
| `implementation.provider_slot`、`.operation` | 必需 | 指向已声明的提供者槽及该接口中的动作名 |
| `effects` | 必需，可空 | 可观察副作用的说明／分类；不是权限证明 |
| `resources` | 必需，可空 | 每项所需资源的种类、用途、必需性及强制要求 |
| `execution.mode` | 必需 | 第一版只支持 `request`：请求关联的调用；异步 `job` 暂不接受 |
| `execution.timeout_ms` | 必需 | 正整数超时上限；策略可以收紧，不能静默改成无限等待 |
| `execution.cancellation` | 必需 | `cooperative` 或 `unsupported`；终止隔离进程另由执行器声明能力 |
| `execution.on_revoke` | 必需 | `drain` 或 `request_cancel`；均在撤销后拒绝新接纳 |
| `execution.retry` | 必需 | 第一版固定 `never`，不由传输层自动重试业务执行 |

`request` 不保证超时或断线后远端立即停止，只表示不另建可脱离请求生命周期的公共 Job 对象。超时后的效果不确定仍需记录 `unknown`，不能因为第一版没有 Job 就丢掉执行身份。

资源先使用具名槽表达，例如 `project_input`、`report_output`；作者不能通过填写宿主绝对路径获得访问权。策略和提供者在绑定／授权准备时将资源槽落到确定对象和范围。只说“需要文件写入”而不能说明谁会强制范围时，该资源要求尚未满足。

资源类型专属字段、路径包含关系、网络目标集合等须在执行器原型中定义；本草案不以一个无类型字典宣称权限模型已经完整。没有支持其语义和强制位置的资源类型应拒绝，不能忽略。

### 3.3 SkillDefinition 与 AssetDefinition 候选字段

| 类型 | 字段 | 含义 |
| --- | --- | --- |
| Skill | `title`、`description` | 摘要 |
| Skill | `body` | 明确正文入口；发布后固定内容引用、媒体类型、字节数和摘要 |
| Skill | `related_operations`、`related_assets` | 容器内关联键；不是授权列表 |
| Skill | `references` | 正文中的相对链接到明确 Asset 键的映射 |
| Asset | `title`、`description` | 摘要 |
| Asset | `content` | 固定内容引用、媒体类型、字节数和摘要 |

`body` 和 `content` 的作者输入可以引用包内文件，安装核定后转换为不可变内容引用。发布描述符保留原相对路径用于来源解释，但运行时通过内容摘要读取，不能重新打开任意同名工作树文件。

读取 Skill 正文只授予该正文访问权。读取 references 或模板仍需对应 Asset 的 `read`；链接解析不携带额外权限。正文里提到 publish 不会暴露可调用工具，也不会赋予 publish 权限。

脚本文件可以作为安装内容保存，但“文件存在”不会生成 Operation。动态资源、临时生成的报告、远程变化中的数据不属于静态 Asset；通过明确的 Operation 访问，或等待后续独立资源契约。

### 3.4 一个最小作者输入示例

下面用 YAML 展示候选对象，便于阅读；组件导出的规范载体仍按工具链草案使用版本化 JSON。此例只有一个 Operation 和一个 Skill，不虚构尚未选择的资源约束格式。

```yaml
format: ghrah.ability-definition
schema_version: 0.1-draft
identity:
  namespace: acme
  name: reports
  version: 0.1.0
title: 报告能力
description: 查询报告服务状态，并提供使用说明
provider_requirements:
  reports_backend:
    contract: acme.reporting.v1
    # contract_digest 由发布时从唯一规范计算并填入
    required_capabilities: [request]
operations:
  inspect:
    title: 查询状态
    description: 返回报告服务就绪状态
    input_schema:
      type: object
      properties: {}
      additionalProperties: false
    output_schema:
      type: object
      properties:
        ready: {type: boolean}
      required: [ready]
      additionalProperties: false
    errors: {}
    implementation: {provider_slot: reports_backend, operation: inspect}
    effects: []
    resources: {}
    execution:
      mode: request
      timeout_ms: 3000
      cancellation: unsupported
      on_revoke: drain
      retry: never
skills:
  reporting:
    title: 报告服务使用说明
    description: 介绍状态查询与后续使用步骤
    body: {path: skills/reporting/SKILL.md, media_type: text/markdown}
    related_operations: [inspect]
    related_assets: []
    references: {}
assets: {}
```

这是一份作者输入，尚不是已密封的运行时定义。发布路径必须核对文件、填入内容元数据、契约摘要和来源，再生成密封记录。摘要规范化算法及哪些文件纳入包摘要，列为原型前的待定项；不得用示例字符串冒充已验证摘要。

Skill／脚本／MCP 等适配器均产出上述定义形态。它们负责解释来源格式，不能自行写 Agent 的工具列表或授权表。本草案不提前固定这些外部格式的完整映射字段。

## 4. 运行绑定：定义固定，接线明确，权限另查

绑定是一个 Agent 局部别名与确定定义、提供者选择之间的关系。它使同一容器可以用于不同项目，也使相同契约可以选择不同语言实现。

建议的 `AbilityBinding` 读侧形态如下：

| 字段 | 含义 |
| --- | --- |
| `binding_id` | 一次确定接线关系的稳定身份 |
| `agent_id`、`agent_run_id` | 持有者的持久身份与本次运行身份 |
| `alias` | 该 Agent 内唯一的局部别名 |
| `definition_ref` | 确定的 namespace、name、version、definition_digest 及安装记录引用 |
| `selection_ref` | 来源组合计划、服务选择和配置摘要；避免只记一个易变服务名 |
| `providers` | provider 槽到确定导出选择、配置摘要及活跃实例的映射 |
| `generation` | 接线核对后引用签发使用的代次 |
| `member_availability` | 按成员派生的可用性及原因，不是授权状态 |

进程内处理器、脚本执行器、MCP 适配器都可实现 provider 槽。组件清单负责启动入口、传输方式和承载后端；Ability 只要求该槽履行相应契约，不内嵌 `subprocess` 启动脚本或任意 Python 对象。

`AbilityBinding` 是由已提交的授权／选择事实、安装定义和提供者状态重建的视图。不能另建一个可任意编辑的 binding 表，再与授权事实互相修补。准备期候选绑定不进入 Agent 可见 namespace。

首版同一 Agent 的同一别名只对应一个确定版本。需要同能力的两种配置时使用两个别名；替换定义、导出选择或配置摘要时生成新绑定并重新核定授权，不让旧 grant 沿别名自动迁移。

提供者仅发生重启时，可以在重新核对同一锁定选择后推进原绑定代次，保留许可事实并签发新引用。首版建议整条绑定推进代次，保守地让该绑定的所有旧引用失效；未受影响内容可重新读取。未来若需逐成员代次，再以原型收益决定。

## 5. 授权记录：整组原子提交，成员逐项表达

容器整体授权需要方便管理，也需要保留不同成员的权限差异。本文建议先采用“一条 GrantRecord 带显式成员访问项”的形态，减少第一版的分组协调对象。

### 5.1 GrantRecord 候选字段

| 字段 | 含义 |
| --- | --- |
| `grant_id`、`revision` | 授权身份及撤销／变更版本 |
| `subject` | 持久 Agent 身份；绑定运行实例的访问引用另行签发 |
| `binding_id`、`definition_digest` | 钉住确定目标；不通过别名追踪升级 |
| `entries` | 明确容器／成员、动作及有效资源限制的列表 |
| `delegation_ceiling` | 若允许委托，明确成员、动作、资源范围和管理上限；缺省不可委托 |
| `authority_ref`、`granted_by` | 可核验的部署／内核根授权或父授权引用，以及发起者 |
| `parent_grant_id` | 派生授权必需；不能由调用者随意自报一个父 ID |
| `expires_at` | 明确截止时刻，或显式 `never`；若按 session 过期需另存确定 session 身份 |
| `status` | `active`、`revoked`、`expired` |
| `created_at`、`policy_ref` | 授权时间与本次策略核定版本／摘要 |

`preparing` 属于准备工作记录，不算有效 GrantRecord。父授权链必须能到达可信根；撤销父项后，派生项即使尚未清理，也必须在每次门禁检查中无效。

下面只展示 `entries`，省略外围身份字段。`kind: ability` 表示容器本身，成员项必须有 `key`；不使用混合通配符字符串。

```yaml
entries:
  - target: {kind: ability}
    actions: [discover]
    resource_limits: {}
  - target: {kind: skill, key: reporting}
    actions: [read]
    resource_limits: {}
  - target: {kind: operation, key: inspect}
    actions: [invoke]
    resource_limits: {}
```

`read`／`invoke` 隐含的 discover 由投影和门禁按规则计算，不再添加一条来源不明的授权。需要资源的 render 等 Operation 必须在其访问项保存核定后的限制；未知或未解析限制不能留空后放行。

“整组授权”是对当前确定版本成员集合的显式展开。新增 publish 或替换脚本内容后，旧记录不会因此扩大。“整组撤销”作用于这条记录所有项；“成员撤销”在同一受控提交中移除指定项并推进 revision，历史事件保留原记录。

### 5.2 资源交集与多来源的首版建议

有效资源范围不得超过作者可批准请求、容器策略、Agent／项目／部署策略、授权来源上限和提供者实际强制能力的交集。View 只投影这些范围及其依据，不作为额外允许或限制来源；投影刷新不决定授权撤销何时生效，纯工具披露选择也不改变权限。空允许集合表示不允许，不等同于“继承全部”；需要继承的作者输入另用显式语义，在有效记录中展开。

必需资源不满足时拒绝本次授权，不能授权成功后才发现动作永远不可执行。允许型布尔值的 OR、路径列表简单覆盖，都不能充当上述交集算法。不同资源类型需要各自的包含／相交定义和执行器验证。

第一版建议同一 Agent、绑定、成员只接受一个有效授权来源，不拼接 A 的读目录和 B 的写目录生成第三种许可。新请求与已有成员授权冲突时整批拒绝；同一请求 ID 的重试返回原结果。以后若需要多来源，先定义选择和撤销规则，再开放并集或替代来源。

该建议限制的是成员的显式授权归属，派生的容器可见性不产生冲突。不同成员可来自不同记录。若此限制妨碍真实部署，原型报告应记录具体用例，而不是实现时暗自放宽。

## 6. 公共访问入口：只暴露必需语义

第一版以类型化入口表达访问，不用一个无类型 `dispatch(path, payload)` 隐去动作和门禁。下表是领域接口形状，具体线协议封装另定。

| 入口 | 输入要点 | 结果与授权条件 |
| --- | --- | --- |
| `catalog` | 可信调用上下文、分页参数，可选容器范围 | 只返回可见摘要；隐藏成员不进入计数或分页线索 |
| `describe` | 局部地址或结构化目标、`summary`／`contract` 视图 | summary 需 discover；Operation 完整调用契约需 invoke |
| `read` | Skill／Asset 目标或读取引用 | 需 read；校验内容摘要和大小，返回内容及来源元数据 |
| `resolve` | 地址、预期动作 | 校验后返回受限 opaque handle；不产生新的授权 |
| `invoke` | 执行引用、参数、请求 ID | 需有效 invoke；接纳后生成 invocation_id |
| `release` | 引用 | 释放调用者持有的服务端引用；不改变 Grant |
| `boundary` | 获准的主体查询范围 | 授权事实及逐成员可用性；仅 Agent 自查或获准管理查询 |
| `grant`／`revoke` | 确定目标／授权 ID、显式选择器与核定上下文 | 经唯一边界转移入口提交；需要管理权和授权链校验 |

`resolve` 可是 SDK 内部步骤，不要求模型逐次调用。提供者收到的执行上下文由可信网关构造，公共输入与宿主内部执行帧分开，不能把任意宿主对象传过 IPC。

首版的 `read` 采用有上限的完整读取，不引入长驻内容流。分页上限、单次内容上限与请求大小上限需在原型中明确，并由服务端强制。缺少引用权限的调用者不能借错误详情读到契约或内容。

取消已接纳的 Invocation 由既有执行管理入口承接，需明确调用者资格；Invocation ID 只用于定位，不是取消权。第一版不新增 Job 查询、订阅或转交 API。

## 7. 引用与生命周期：每次使用都重新过闸

引用是网关签发的受限访问句柄，不是通用 URL，也不是可以转发即生效的 bearer token。首版建议使用服务端登记的不透明引用，暂不设计跨进程自由委托 token。

引用至少关联 `agent_id`、`agent_run_id`、`binding_id`、generation、definition_digest、成员键、动作、grant_id 和 revision。执行引用另关联 provider_instance_id；内容引用另关联 content_digest。调用载荷只携带不透明引用 ID，这些字段由可信侧保存。

| 变化 | 必须发生的行为 |
| --- | --- |
| 成员撤销或记录过期 | 该访问不能再接纳；旧引用拒绝，其他授权项按新 revision 重新取引用 |
| 容器授权记录撤销 | 该记录全部项同时停止新访问；其他记录和其他 Agent 按各自权限继续 |
| provider 暂时不可用 | Operation 返回 unavailable；不删除授权历史，不让其他静态内容随之不可读 |
| provider 重启 | 核对实例和锁定选择，推进绑定代次；旧执行引用明确失效，不透明重试 |
| 别名换绑或定义升级 | 生成新绑定并核定授权；旧引用不追随别名 |
| Agent 重启／结束 | 旧 run 的引用失效；恢复许可事实后重新验证接线、签发引用 |
| 父授权撤销 | 委托链即时判无效；不能等待后台清理完成后才拒绝 |

引用释放、授权撤销和调用取消是三个动作。释放 handle 不收回权限；撤销 grant 不保证已经发生的外部效果回滚；请求取消不等于远端已经停止。

发生撤销与 invoke 竞争时，必须有明确的接纳点：授权检查、接纳身份／记录提交与边界变更在 Core 中形成确定顺序。撤销先提交则拒绝，接纳先提交则按 Operation 的在途策略处理。外部请求在接纳记录之后发送，远端不参与这笔数据库事务。

崩溃发生在发送前后或回执丢失时，结果可能为 unknown。网关请求去重只避免重复接纳，不自动保证远端效果恰好一次；恢复过程不得重发没有幂等保证的外部调用。

## 8. Invocation 记录：操作定义与一次执行分开

OperationDefinition 讲动作能做什么；Invocation 记录某次动作实际尝试做了什么。即使没有长任务 Job，也需要保留执行身份和不确定结果。

建议最小记录包含：invocation_id、请求 ID 与请求摘要、Agent／run 身份、binding／generation、definition_digest、成员键、grant／revision、provider 实例、接纳时的权限核定引用、开始结束时间、状态及结果／错误引用。

状态暂取 `accepted`、`running`、`succeeded`、`failed`、`cancelled`、`unknown`。网关拒绝发生在接纳前，写拒绝审计，不伪造一个已经运行的业务失败。收到确定结果后可用追加记录澄清 unknown，不能覆盖原有不确定事实。

返回的结构分为协议状态和业务结果：成功结果需符合 output_schema；业务错误符合 errors 中的契约；授权拒绝、引用失效、提供者不可用等属于网关错误。远端输出和错误文案均不能被解释为新的授权或宿主指令。

候选网关错误码：`INVALID_ADDRESS`、`NOT_FOUND_OR_NOT_VISIBLE`、`ACCESS_DENIED`、`STALE_REFERENCE`、`UNAVAILABLE`、`INVALID_INPUT`、`CONTRACT_MISMATCH`、`GRANT_CONFLICT`、`LIMIT_EXCEEDED`。超时与断线还应说明结果是否 unknown，不能用一个普通失败码掩盖效果不确定性。

## 9. 与组件、工具链和模型工具面的接合

scheme 是运行访问边界，工具链负责交付和选择，模型工具面负责向模型表达当前可用入口。三者应共享身份关系，但不互相冒充权威。

现有工具链草案中的连接链保留：

```text
component export（scheme、contract、definition）
  → distro service（选择 instance/export）
  → namespace template（alias → service）
  → Core 核定并提交实际成员授权
  → 有效绑定／目录／工具投影
```

命名空间模板只提供候选接线。模板中的 reports 不等于 Agent 已获得 reports；没有任何可见项的候选绑定不会出现在 Agent 的 catalog 中。`check` 可以验证定义和契约引用，不能签发 grant，也不启动 provider 来“顺便验证”。

工具投影建议每个已获 invoke 的 Operation 生成一个模型工具，Skill 和 Asset 通过受控 read 按需进入上下文。工具名由投影器生成，保留从“本次模型请求 + 工具名”到确定成员身份的反查表；不通过分割下划线猜回路径。

不同模型的工具名称／长度限制属于投影适配，不反向限制 Ability 的领域身份。碰撞时应显式改名或报错，不静默覆盖。尚未进入本次工具投影的 discover-only 成员不能被直接执行。

工具投影按迭代边界更新；每次实际调用仍查当前授权、绑定代次和提供者实例。模型持有旧 schema 时，撤销后的调用仍会拒绝。服务端不以“模型看得到这个工具”为授权依据。

## 10. 权威与恢复：哪些事实可以重建

新 scheme 不增加可独立修改的工具目录。以下责任划分应在原型中直接体现，而不是等待生产迁移再补。

| 事实 | 写入责任 | 恢复方式 |
| --- | --- | --- |
| 定义、内容和核定来源 | 发布／安装路径 | 由密封定义和内容存储读取；开发态变动生成新核定结果 |
| 选择与配置意图 | 版本化清单及生成的组合计划 | 校验摘要，重新生成候选接线 |
| 授权与撤销 | Core 唯一边界转移提交 | 由初始事实和转移历史重建快照；与对应历史同事务提交 |
| provider 活跃实例 | 生命周期 owner／supervisor | 重新握手和确认，不靠历史事件复活连接 |
| 有效绑定、catalog、工具面 | 只读派生 | 从定义、已提交授权、接线事实和活跃状态重建 |
| Invocation | 既有执行权威记录路径 | 恢复记录和不确定状态；不重演外部效果 |

Provider 准备只覆盖本次所选成员的必需依赖，在授权提交前完成，失败释放暂存资源；仅授予 Skill 读取时不要求无关 Operation 的 provider 就绪。授权和边界历史在同一 Core 事务生效。发布目录和工具投影在提交后进行，可重试。投影失败不改变已提交的许可事实，执行闸始终使用当前权威。

对要求“整组都准备好”的授权请求，任何必需 provider／资源准备失败则整组不生效；已经生效后的 provider 故障只降低相应成员的当前可用性。两种情况不能用同一个 active 字段混在一起。

## 11. 首个原型验证：从一个小容器证明规则

先验证上述最小定义，再扩展脚本、MCP 和 Job。原型按工作区规约放在独立的 `experiments/` 子仓，结论回写 `analysis/`；本轮尚未创建实验或修改产品代码。

| 场景 | 必须观察到的结果 |
| --- | --- |
| 一个 Skill + 一个无外部效果的 inspect | 两者共享容器身份，读说明不增加 invoke 权 |
| 两 Agent 将 reports 绑定到不同 provider | 局部解析独立，引用不可跨 run／Agent 使用 |
| 同契约进程内／进程外实现 | 只改显式选择，业务输入输出及拒绝语义一致 |
| discover-only、read-only、invoke 三种许可 | 摘要、正文、完整调用契约分别按规则开放 |
| 地址含越界段或编码歧义 | 明确拒绝，不自动规范化为另一个目标 |
| 批量准备失败、重复请求、来源冲突 | 无部分授权；重试不新增记录；冲突不自动并集 |
| 撤销与接纳并发、旧工具面继续调用 | 按提交顺序接纳或拒绝；旧投影不保留执行权 |
| provider 重启、内容换版、Agent 重启 | 原引用失效；许可恢复与运行接线分别处理 |
| provider 收到请求后失联 | 保留 Invocation 与 unknown，不盲目重发 |
| 清空投影后重建 | catalog、boundary、工具映射可从权威恢复，无独立补写 |

核心检查关系为：工具投影里的可调用成员必须获授权；每次实际执行必须由当前有效授权和当前接线允许。二者在撤销与刷新之间允许存在短暂差异，但差异不能使已撤销调用继续接纳。provider 可用性和工具选择导致的子集也不能被误报为授权丢失。

## 12. 本轮建议与下一轮待定项

本文没有冻结生产协议。它提出一组可逐项修改的候选，目的是让下一轮讨论有确定对象。

**沿用的方向：**Ability 容器、Operation／Skill／Asset 三类成员、局部名字、统一授权入口、逐次门禁、不可变定义、引用失效和投影可重建。

**本轮新增建议：**使用 `/scheme/ability` 下的绝对逻辑路径，四种资源地址不另设冒号兼容形式；成员键按类别组织；一条 GrantRecord 携带显式访问项；同一成员单一授权来源；provider 重启推进整绑定代次；服务端不透明引用；首版只接 request，不接 Job；业务执行不自动重试。这些都是候选，不应标成用户已经裁决。

下一轮应优先解决以下问题：

1. **定义的唯一规范与摘要算法。** JSON 描述符如何从作者输入生成；规范化、包清单和依赖锁如何纳入摘要；公共 contract 与 definition 如何共享字段。
2. **资源约束的第一种可执行类型。** 先选择文件或一种受限服务资源，定义集合、交集和强制点，验证脚本与远端两种边界不能互相冒充。
3. **授权来源与委托。** 单来源限制是否满足真实部署；受保护初始授权、审批引用和父授权撤销如何复用内核现有机制。
4. **执行接纳与协议载体。** 如何在既有事务和执行记录中安置接纳点；最小 provider 握手、请求关联、断线及结果恢复怎样表达。
5. **迁移映射。** 哪个现有 builtin 先转成“一个容器 + 一个 Operation”；旧状态作用域、历史记录和 wire 版本怎样解释。

首个原型通过后，再把确定字段拆成协议变更和实施任务书。当前文档保持 `planning/`、`draft`，不归档为已完成实施。

按用户明确的迁移粒度，Agent scheme 与相关必要支撑 scheme 应同期设计、联调、验收和切换，缩短 Agent 核心业务的中间态。Ability 草案与先行实验是该共同工作的一个部分；必要支撑的清单与 scheme 划分尚未冻结。核心模型整体就绪后，普通旧执行实质可以经受信后端适配复用，独立外围实现可后续替换。

初版路径只接受规范形式，不妨碍旧外部工具名或 API 通过显式翻译进入同一访问入口；翻译失败不得回退旧直写／执行路径。统一授权门禁是整体切换的必要条件，不能替代 Agent 身份、生命周期、状态归属等共同契约的完成。首轮建议按独立 Core 运行代次执行部署切换，尚不承诺混合模式 Agent 或热迁移；依据见[迁移边界评估](../analysis/2026-10-26-scheme-incremental-migration-assessment.md)。

## 13. 相关文档

- [Scheme 基座设计：Python 宿主、局部解析与受控服务引用](2026-10-26-scheme-foundation-design.md) — Ability 的解析、授权、引用与 provider 接入所依赖的公共机制。
- [View scheme：Agent 的统一只读投影](2026-10-26-view-scheme-draft.md) — 投影具体 Agent 的局部能力面与来源，不增加权限上限或第二执行入口。
- [Scheme 的渐进引入与授权切换边界评估](../analysis/2026-10-26-scheme-incremental-migration-assessment.md) — 旧实现／接口的兼容条件、统一门禁及运行代次切换建议。
- [Scheme 与 Ability 容器模型](../10-Scheme与Ability容器模型.md) — 本文的上位模型；容器、成员、生命周期和破坏性迁移方向。
- [ghrah.toml 与组件契约字段草案](2026-10-25-ghrah-toml-scheme-draft.md) — 组件导出、服务选择和 namespace 模板的字段边界。
- [Ability 作为唯一原语：授权空间与内核 API 设计](2026-10-22-ability-primitive-and-kernel-api.md) — 授权链与内核纪律的前序输入；目标容器粒度以上位模型为准。
- [动态更新的下一步实施](../NextStep.md) — 本草案的工作入口及后续原型指针。
