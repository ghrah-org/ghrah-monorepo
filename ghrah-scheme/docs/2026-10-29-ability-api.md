---
title: Ability 容器 API 与 Scheme 接入
description: 区分现有容器定义、扫描和执行接口，以及目标模型中的实例、获授关系和工具绑定
tags: [Scheme, Ability, Operation, API]
created: 2026-10-29
updated: 2026-10-29
status: active
---

# Ability 容器 API 与 Scheme 接入

> 本文面向能力作者与接入开发者。先说明容器和成员，再给出当前可用接口，最后说明如何接到公共运行时。

校准基线：`acf06fe` 与 2026-10-29 工作区。`ghrah.ability` 门面及 BuiltinAbilitySource 来自当日未提交的接入改动；核心定义和执行实质仍由 `ghrah.scheme_ability` 提供。当日已新增文件切片的获授树 API，装配与调用见[文件 Ability 资源切片：权威关系、Scheme 接入与面板](2026-10-29-ability-resource-slice.md)；它尚未接管生产 Agent。

## 1. 容器 API 与控制原语属于不同层

Ability 是包含 Operation、Skill 和 Asset 的容器。Operation 定义可执行行为，Skill 提供说明内容，Asset 表达资源内容。当前资产字段较宽泛。

| 接口层 | 当前归属 | 负责什么 |
| --- | --- | --- |
| 定义与来源 | Core 的 `ghrah.ability` 门面及 `ghrah.scheme_ability.loading` | 定义模型、扫描、目录和明确的内置来源 |
| Operation 执行 | Core 的 `ghrah.scheme_ability.execution` | 执行上下文、执行器、Hook 及既有外部效果 |
| 公共访问机制 | 独立包 `ghrah.scheme` | 契约、局部绑定、引用、调用和生命周期 |
| 获授树／bound_tools | 文件切片由 ghrah.ability.access／scheme 实现，尚未接管生产 | Agent 的有效成员关系和已生效工具选择 |

`ghrah.scheme` 不导出 AbilityDefinition，也不依赖 Core 才能运行。能力作者无需为每个工具新建 scheme；多个实例和异构成员仍可以由同一个 ability kind 组织。

## 2. 当前容器定义

能力包以 `ability.yaml` 定义身份、成员和 provider 槽需求。定义模型是 [ability_schema.py](../../ghrah-core/src/ghrah/scheme_ability/loading/ability_schema.py) 的 AbilityDefinition；文本解析使用该模块 `parse_definition(raw, source=...)`，它还执行版本、槽引用、成员键和 Skill 数量等跨字段校验。

| 顶层字段 | 作用 |
| --- | --- |
| format、schema_version | 当前格式分别为 `ghrah.ability-definition`、`0.1-draft` |
| identity | namespace、可选 name、version；与显示名、Agent alias 分开 |
| title、description | 容器的人类／模型说明 |
| provider_requirements | 提供者槽到契约要求的映射 |
| operations | 成员键到 OperationDefinition 的映射 |
| skills | Skill 声明；当前解析器至多接受一个成员 |
| assets | 当前为自由结构，尚无完整字段级服务契约 |

OperationDefinition 声明 input_schema、output_schema、errors、implementation、effects、resources、execution 和 expose 等内容。implementation.operation 必须等于成员键，provider_slot 必须已声明。expose 决定当前定义工具投影是否包含该成员，不等于 Agent 已获授调用权。

ExecutionSpec 当前只接受 request 模式、正数 timeout_ms、cooperative／unsupported 取消、drain／request_cancel 撤销策略和 never 重试。字段能够被解析，不等于所有执行后端已经完整强制这些策略；适配者需要核对真实后端支持。

下面是一份可通过当前文本解析器校验的纯 Operation 容器。它说明定义格式，不会仅因解析成功就注册 provider 或授予权限。

<!-- validate: ability-definition -->
```yaml
format: ghrah.ability-definition
schema_version: 0.1-draft
identity:
  namespace: demo
  name: text
  version: 0.1.0
title: 文本处理
description: 对文本执行无外部副作用的规范化。
provider_requirements:
  text_backend:
    contract: demo.text
    required_capabilities: [request]
operations:
  normalize:
    title: 规范化文本
    description: 去掉两端空白并转换为小写。
    input_schema:
      type: object
      properties:
        text: {type: string}
      required: [text]
      additionalProperties: false
    output_schema:
      type: object
      properties:
        text: {type: string}
      required: [text]
      additionalProperties: false
    implementation: {provider_slot: text_backend, operation: normalize}
    effects: []
    execution:
      mode: request
      timeout_ms: 30000
      cancellation: cooperative
      on_revoke: drain
      retry: never
skills: {}
assets: {}
```

静态 YAML 与 SchemeSpec 不应各自演化成不同的参数契约。当前文件切片已将两个所选成员的确定 schema 通过 JsonSchemaCodec 接入 SchemeSpec；尚无覆盖任意 AbilityDefinition 的通用转换器。接入时应明确唯一作者，再生成或核对重叠 schema。公共描述格式由 ghrah-protocol 持有，不能仅手工修改 UI 一侧。

## 3. 当前公开来源与扫描 API

当日工作区的 [ghrah.ability](../../ghrah-core/src/ghrah/ability/__init__.py) 是薄门面，直接导出原实现对象。Operation 是既有单动作执行基类 Ability 的公开名称；它不是新的容器实例类型，也不是 Scheme 的 ActionSpec。

| 接口 | 返回或效果 | 边界 |
| --- | --- | --- |
| `scan_abilities(pool=None)` | `tuple[AbilityContainer, ...]` | 按定义聚合 operations／skills／assets 和问题；不授予权限 |
| `scan_catalog(pool=None)` | CatalogReport | 来源和实现相关的目录报告，不是当前 Agent 的有效工具集合 |
| `default_pool()` | 默认目录池 | 内置包资源及用户能力目录；不自行创建目录 |
| `SkillAbilityProvider(pool)` | 固定目录池的门面 | 构造不扫描、不写盘、不监视目录 |
| `provider.scan()` | AbilityScanResult | entries、skills、issues、lock_issues；与 scan_abilities 的聚合容器返回不同 |
| `provider.get_skill(name)`、`read_skill(name)` | 技能成员或正文对象 | 精确全名优先，宽松别名必须唯一；读取不授予访问权 |
| `initialize(...)`、`relock(...)`、`verify(...)` | 显式初始化、登记或校验 | 写入和较昂贵整树校验不混入普通扫描 |

目录池 rank 较小者优先；同名容器冲突收集为问题。当前扫描重新读取定义，不保证目录已经是固定运行版本。执行接入需要另行固定所选择的定义、内容和配置，不能一边调用一边把可变目录当作已授权的固定对象。

以下只读示例核对内置容器及明确来源，不执行文件操作：

<!-- runnable: ability-catalog -->
```python
from ghrah.ability import BuiltinAbilitySource, scan_abilities

containers = scan_abilities()
filesystem = next(item for item in containers if item.name == "fs")
assert any(item.key == "read_file" for item in filesystem.operations)

source = BuiltinAbilitySource()
manifest = source.load_ability("ghrah.fs.read_file")
assert manifest.full_name == "ghrah.fs.read_file"
print(manifest.full_name)
```

BuiltinAbilitySource 还提供 `load_builtin(type)`、`list_abilities(namespace=None)`、`resolver()` 和 `create_operation(full_name, **params)`。它显式选择新增内置来源，返回兼容投影或为受信装配者创建后端；不在来源缺失时回退旧注册表，不注册运行端点，不提交授权。Agent 声明存储通过 agent_store 显式注入。

## 4. 接入 Scheme 的最小边界

把现有 Operation 接到 Scheme，需要一个 Provider 适配器：从 ProviderCall 取得可信主体、目标、已验证参数和调用关联；映射到领域调用；将结果转换为契约规定的 SchemeModel。若后端使用既有执行器和 Hook，就继续使用它们承载领域效果，避免复制第二套业务。

```text
确定 Ability 定义及实例选择
  → 成员契约与资源路径
  → SchemeSpec／ActionSpec 与 Provider 适配
  → 受信 publish 与局部 bind
  → Authority 读取当前获授关系并核定／接纳
  → Session acquire／submit
  → 既有 Operation／领域逻辑
```

每个 provider 槽可由进程内逻辑、脚本或远端适配满足，但当前运行时只实现进程内分派；外部传输和进程生命周期适配仍需具体实现。一个容器可含异构成员，不能因为同处容器就假定它们都有相同参数、输出或可用性。

当前 [真实文件 Ability 接入测试](../tests/test_core_file_ability.py) 演示 Provider 映射到 Core 执行器并验证文件效果。它使用旧单动作后端，不证明新容器驱动已经完成产品获授树、Subject 装配或模型工具切换。

`catalog／describe` 是运行时的受控发现方法；Skill 读取、Asset 读取、获授关联变更、工具选择等则需要各领域动作契约。不能据这些概念存在就写出尚不存在的 `session.read_skill()` 或 `session.bind_tool()`。

## 5. 容器接入完成的判断

一个容器的接入应能解释：定义和实例是谁；成员如何找到提供者；Agent 获授哪些成员和范围；工具绑定如何引用它们；调用撤销或 provider 重启后如何失效；UI 与 Agent 如何读取结果。

这些事实在目标树中有明确位置，在实现中有明确写者。只完成目录扫描或执行对象实例化，不等于完成该闭环。具体当前边界统一查阅实现状态，避免将目标模型写成已有运行事实。

## 相关文档

- [文件 Ability 资源切片：权威关系、Scheme 接入与面板](2026-10-29-ability-resource-slice.md) — 当日工作区新增的可执行文件资源闭环与独立面板。

- [ghrah-scheme](../README.md) — 阅读入口。
- [Scheme 系统模型：全局树与局部入口](2026-10-29-system-model.md) — 定义、实例和局部入口。
- [资源、权限与节点必要性](2026-10-29-resources-and-authority.md) — 获授集合与工具选择。
- [运行时 API：契约、原语与生命周期](2026-10-29-runtime-api.md) — 完整的可执行接入示例。
- [二次开发：逻辑、Scheme 接入与 UI 并行推进](2026-10-29-development-guide.md) — 容器、适配和 UI 的开发分工。
- [第一代文档的实现状态与代码地图](2026-10-29-implementation-status.md) — 工作区 API 与提交基线的区别。
- [能力包开发指南](../../docs/ghrah-02-ability-package-guide.md) — 既有包实现模板；其中历史模块路径以本文代码链接核对。
- [技能系统](../../docs/ghrah-04-skill-system.md) — Skill 内容与登记机制的现有说明。
