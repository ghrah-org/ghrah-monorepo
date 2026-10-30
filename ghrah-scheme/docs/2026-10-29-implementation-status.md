---
title: 第一代文档的实现状态与代码地图
description: 对照当前控制平面、Core Ability 接入与目标产品树，说明支持边界、代码位置及验证范围
tags: [Scheme, 实现状态, 代码地图]
created: 2026-10-29
updated: 2026-10-30
status: active
---

# 第一代文档的实现状态与代码地图

> 本文用于回答“现在能调用什么、目标模型还缺什么、应该到哪里找代码”。第一代指这套技术文档，不表示软件已经完成第一代产品树。

校准基线：`ghrah-monorepo` 提交 `acf06fe`，2026-10-29；包版本 `ghrah-scheme 0.1.0a0`。当日工作区包含未提交的 `ghrah.ability` 门面、来源接线及文件资源切片，下文明确区分。已有 Core／Subject 产品通路尚未整体切换到 Scheme 控制平面，包未纳入对外发布白名单。

## 1. 已实现、工作区新增与目标模型

本表是支持范围索引。当前行为以实际代码与测试为准；目标模型说明设计方向，不构成已有方法或传输协议。

| 能力 | 2026-10-29 状态 | 使用边界 |
| --- | --- | --- |
| SchemeSpec、ActionSpec、严格输入输出和公共描述格式 | 已实现 | 类型化进程内契约；公开描述由 ghrah-protocol 提供 |
| AgentScope 与局部 alias 解析 | 已实现 | project_id、agent_id、run_id 三维隔离；alias 不是全局实例 ID |
| acquire／submit／observe／cancel／release | 已实现 | 有界消费控制，不等同于通用树增删 API |
| catalog／describe | 已实现 | 当前 scope 的受控发现；无通用递归目录、分页和订阅 |
| ProviderLifecycle 与共享 owner 收尾 | 已实现 | 显式 prepare／aclose、健康代次、残余报告与失败重试 |
| 真实 Core 文件 Ability 通过 Provider 执行 | 已有测试验证 | 测试适配旧单动作后端，不代表新容器产品链已整体迁移 |
| Ability 容器定义、扫描、Skill 与单动作执行实质 | Core 已有实现 | 归 `ghrah.scheme_ability`，不是机制包的导出 |
| `ghrah.ability` 薄门面及 BuiltinAbilitySource | 当日工作区新增，尚未在基线提交中 | 明确新增内置来源、兼容解析和后端实例化；不自动授权或注册端点 |
| 人类管理范围内的全局树与跨树链接 | 目标模型，未完整实现 | 当前第三段仍按调用者 alias 解析，不能仅靠拼字符串获得全局语义 |
| 人类／管理服务主体与可信 self 解析 | 工作区已有独立 ControlPrincipal | 文件切片可保留操作员身份管理；生产认证传输和通用 self 解析未接线 |
| Agent 获授关联、bound_tools 及节点变更入口 | 文件切片已实现持久闭环 | 唯一领域写入口、修订比较、当前关系接纳和旧引用失效；生产 Agent 尚未迁移 |
| View 的同源裁切投影与跨 Agent 枚举 | 文件切片已实现根 View | 普通 Agent 读自己、操作员读目标；无通用递归枚举和逐子节点访问 |
| Scheme 面板入口与 UI 产物装配 | 已有同源样例、客户端、替身和独立组件 | 选择／解绑／冲突展示可测；未接生产主页面与 Subject 连接 |
| 进程外调用传输、OS 沙箱、自由引用委托 | 未实现 | 现有机制是进程内受信调用边界 |
| 跨 boot 持久去重、通用 Job／流框架 | 未实现为公共机制 | 当前去重限 boot；领域可有自身持久资源，不自动获得通用 Job 支持 |

## 2. 代码地图

按职责找代码，不根据历史上同名的 `scheme` 模块猜归属。当前机制导入 `ghrah.scheme`，Core 容器实质在 `ghrah.scheme_ability`，当日门面为 `ghrah.ability`。

| 职责 | 代码与主要符号 |
| --- | --- |
| 公共导出 | [__init__.py](../src/ghrah/scheme/__init__.py)：公开类型与端口 |
| 单一作者编解码 | [codec.py](../src/ghrah/scheme/codec.py)：PydanticCodec、JsonSchemaCodec；ActionSpec 可直接复用领域作者 |
| 本地契约 | [contracts.py](../src/ghrah/scheme/contracts.py)：SchemeModel、SchemeSpec、ActionSpec、ProviderCall、ProviderLifecycle |
| 主体、引用、状态与预算 | [models.py](../src/ghrah/scheme/models.py)：Principal、AgentScope、Target、InvocationSnapshot、RuntimeLimits |
| 核定与接纳端口 | [authority.py](../src/ghrah/scheme/authority.py)：Authority、DenyAuthority |
| 分派和消费／管理 API | [runtime.py](../src/ghrah/scheme/runtime.py)：SchemeRuntime、Session、RuntimeManager、路径解析 |
| 受控发现投影 | [_discovery.py](../src/ghrah/scheme/_discovery.py)：逐动作摘要、schema、调用意图过滤 |
| 生命周期与内部记录 | [_lifecycle.py](../src/ghrah/scheme/_lifecycle.py)、[_state.py](../src/ghrah/scheme/_state.py)：内部实现，不作为外部接入 API |
| 错误分类 | [errors.py](../src/ghrah/scheme/errors.py)：SchemeError、ErrorCode |
| Python 公共描述格式 | [protocol/scheme.py](../../ghrah-protocol/src/ghrah/protocol/scheme.py)：ContractDescriptor、CatalogSnapshot 等 |
| TS 描述格式 | [protocol/src/scheme.ts](../../ghrah-observer-webui/packages/protocol/src/scheme.ts)：与 Python 样本对齐 |
| Ability 公开门面和来源 | [ability/__init__.py](../../ghrah-core/src/ghrah/ability/__init__.py)、[source.py](../../ghrah-core/src/ghrah/ability/source.py)：当日工作区接入 |
| 容器格式、目录与 Skill | [ability_schema.py](../../ghrah-core/src/ghrah/scheme_ability/loading/ability_schema.py)、[ability_scan.py](../../ghrah-core/src/ghrah/scheme_ability/loading/ability_scan.py)、[skill_provider.py](../../ghrah-core/src/ghrah/scheme_ability/loading/skill_provider.py) |
| 既有 Operation 执行接口 | [execution/base.py](../../ghrah-core/src/ghrah/scheme_ability/execution/base.py)：旧名 Ability，当日门面名称 Operation |

## 3. 已有验证能证明什么

测试验证机制的访问与生命周期边界，不将机制通过推导为所有产品领域已经迁移。

| 测试入口 | 主要覆盖 |
| --- | --- |
| [test_scheme_contracts.py](../tests/test_scheme_contracts.py) | 严格输入输出、契约与内容复制 |
| [test_contract_identity_scope.py](../tests/test_contract_identity_scope.py) | 共享项目／namespace 下的 Agent 隔离、代次与契约身份 |
| [test_scheme_runtime.py](../tests/test_scheme_runtime.py) | 调用、引用、去重、观察和取消 |
| [test_runtime_boundaries.py](../tests/test_runtime_boundaries.py) | 容量、失效、嵌套和运行边界 |
| [test_discovery.py](../tests/test_discovery.py) | 披露过滤、只读发现、状态变化和有界核定 |
| [test_provider_lifecycle.py](../tests/test_provider_lifecycle.py) | 准备、共享资源、健康、关闭和残余 |
| [test_core_file_ability.py](../tests/test_core_file_ability.py) | 通过真实 Core 后端验证文件效果及控制行为 |

2026-10-29 本套文档编写时，`uv run --no-sync pytest -q ghrah-scheme` 通过 140 项测试。运行时篇的完整文本示例、Ability 篇的目录／来源示例和 YAML 定义也已用当日工作区实际执行或解析校验。文档的目标全局树未据此宣称已通过运行验收。

### 3.1 文件资源切片的新增验证

新增 [test_action_codecs.py](../tests/test_action_codecs.py)、[test_resource_tree.py](../../ghrah-core/tests/ability/test_resource_tree.py) 及 Python／TS ability-tree 测试，覆盖真实新文件后端、独立管理身份、A/B 映射、持久关系、模型工具、当前接纳、旧引用和重开。面板与客户端消费 Python 生成的同源样例，支持独立验证。

2026-10-29 切片交付：Python 六包 **3610 passed、8 skipped**；四个受影响 TS 包 **1099 passed、4 skipped**；全仓 Ruff、两包 strict mypy、前端 lint 和全工作区类型检查通过。三个实际 wheel 安装后的新增切片／codec／协议用例 **28 passed**。此前 SearchFiles 等待在沙箱外单跑和全量中均通过，无须跳过或修改旧搜索算法；SQLite 最小复现也确认本次沙箱中的线程唤醒挂起。此结果仍不代替完整生产 Agent 切换验收。

### 3.2 资源切片审计后的修复验证

上述 3610／1099 是首次切片交付时的沙箱外记录，不是后续审计重新执行的结果。审计修复后本轮再次在沙箱外运行六包，**3622 passed、8 skipped**；四个受影响 TS 包 **1102 passed、4 skipped**；实际 wheel 环境新增用例 **40 passed**。两包 strict mypy、全仓 Ruff、前端 lint／全工作区类型检查和 Web 构建通过。

当前修复包括 Core 的可选 scheme extra、实例与后端代次门禁、幂等安装和旧导出收束、只读接纳不落库、expose、外部 run 来源、重建接口、修订通知读取、严格成员引用、host 观测元数据、唯一节点名和统一消费异常。默认 Core wheel 在卸掉 ghrah-scheme 后仍能导入生产模块并通过依赖检查。具体调用与 schema 迁移见文件切片指南；生产认证、peer 可见性和真实 Agent 仍待后续接线。

## 4. 维护这套文档

改动实际 API 时同步运行时参考及示例；改动容器格式或公开来源时同步 Ability 参考；改变产品树、权限关系或 View 时同步系统模型和资源文档。支持状态变更先更新本表，再删去其他篇中已过时的边界说明。

日期和基线限定代码事实的适用范围。目标语义与实现事实冲突时，分别记录，不用目标描述覆盖当前行为。包内技术文档引用代码符号和同仓稳定文档，不依赖仓外计划的阶段、标题或可移动路径。

## 相关文档

- [文件 Ability 资源切片：权威关系、Scheme 接入与面板](2026-10-29-ability-resource-slice.md) — 当日工作区新增的可执行文件资源闭环与独立面板。

- [ghrah-scheme](../README.md) — 包与第一代文档的入口。
- [Scheme 系统模型：全局树与局部入口](2026-10-29-system-model.md) — 目标形状。
- [资源、权限与节点必要性](2026-10-29-resources-and-authority.md) — 权威关系和节点用途。
- [运行时 API：契约、原语与生命周期](2026-10-29-runtime-api.md) — 当前 Python API。
- [Ability 容器 API 与 Scheme 接入](2026-10-29-ability-api.md) — Core 容器 API 与机制接入。
- [二次开发：逻辑、Scheme 接入与 UI 并行推进](2026-10-29-development-guide.md) — 实现与联调方法。
- [ghrah 文档索引](../../docs/ghrah-00-doc-index.md) — 平台和既有子系统文档入口；历史模块路径按本页代码链接核对。
