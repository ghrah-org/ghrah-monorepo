---
title: Scheme 系统模型：全局树与局部入口
description: 说明系统完成 Scheme 接入后的整体形状，以及实例、局部映射、视图和包职责
tags: [Scheme, 架构, 命名空间]
created: 2026-10-29
updated: 2026-10-29
status: active
---

# Scheme 系统模型：全局树与局部入口

> 本文面向第一次接触 ghrah Scheme 的读者。先看系统提供怎样的资源世界，再区分这套目标模型与当前可调用的运行时。

校准基线：`ghrah-monorepo` 提交 `acf06fe`，2026-10-29；当日工作区中的新增公开 Ability 门面另见[实现状态](2026-10-29-implementation-status.md)。本文的系统树是第一代目标模型，路径示例不是已经实现的全局 API。当前包版本为 `0.1.0a0`。

## 1. Scheme 解决什么问题

Scheme 把系统能力组织成有名字、有契约、能解析到确定实现的资源。人类可以查看资源及关系、通过 UI 操作；Agent 可以发现 Ability、读取 Skill、绑定工具并调用 Operation。两者消费同一套资源语义，按各自身份使用。

`ghrah-scheme` 是公共访问机制包，导入名为 `ghrah.scheme`。它负责契约、局部解析、运行引用、有界调用与提供者生命周期；业务逻辑、授权事实和具体 UI 由相应模块持有。它不是宿主文件系统，也不要求磁盘上创建 `/scheme`。

| 术语 | 定义 |
| --- | --- |
| kind | 资源种类，如 `agent`、`ability`、`view`；不是实例名称 |
| 契约 | 对资源动作、输入输出和行为的明确约定 |
| provider | 履行某个契约的提供者实现与运行导出 |
| owner | 对某项事实或资源负责的唯一写者／生命周期持有者，具体语境分别说明 |
| alias | 某个 Agent 运行中的局部名字 |
| scope | 名字解析的范围；当前实现由项目、Agent 和 run 三个 ID 确定 |
| 投影 | 从权威事实读取、过滤或关联而得的结果，可以重建，不独立写回 |

## 2. 管理员看到有明确范围的全局树

管理员需要看到“系统有哪些对象、Agent 得到了什么、关系指向哪里”。因此目标模型提供可浏览的全局树。这里的全局以一个明确项目或部署为范围，不表示所有部署共用一个无边界根，也不表示任何用户自动看见全部数据。

```text
/scheme
├── agent/
│   ├── agent1/
│   │   ├── identity
│   │   ├── runs/run1
│   │   ├── lifecycle
│   │   ├── ability/
│   │   │   └── reports → /scheme/ability/reports-a
│   │   ├── bound_tools/
│   │   │   └── render_report → 本 Agent 的 ability/reports/operations/render
│   │   ├── context
│   │   └── status
│   └── agent2/
│       └── ability/
│           └── reports → /scheme/ability/reports-b
├── ability/
│   ├── reports-a/
│   │   ├── definition
│   │   ├── operations/render
│   │   ├── skills/reporting
│   │   └── assets/template
│   └── reports-b/
│       └── operations/render
└── view/
    ├── agent1/
    │   ├── ability
    │   ├── bound_tools
    │   └── status
    └── agent2/
        └── status
```

`agent` 组织身份、运行及关联事实；`ability` 组织可共享的能力实例；`view` 是 Agent 子树及相关事实的裁切与只读投影。多个 Agent 的状态查询复用 `view/<agent-id>`，不为“查看多个 Agent”另建 `cluster` kind。成员关系、租约等写入仍归明确的领域 owner。

通信、任务、项目、工作区和宿主管理各有真实职责，但不在本图中预先分配顶层种类。新增 kind 要从实际消费者和不能由现有契约承接的需求论证，不能按模块、进程或实例数量复制种类。

## 3. Agent 与一般用户使用局部入口和获准子集

Agent1 与 Agent2 都可以使用 `/scheme/ability/reports/operations/render`，分别映射到 reports-a 与 reports-b。全局树展示两条确定关系；局部入口保留简短名字。局部名字不同义不构成拒绝全局树的理由。

| 场景 | 路径的解释上下文 |
| --- | --- |
| 管理员浏览 Agent1 | 明确管理范围、目标 Agent 及目标运行，按管理员身份读取 |
| Agent1 调用自己的 reports | 从可信会话确定 Agent1 的运行 scope，解析其 alias |
| 一般用户查看获准内容 | 从认证后的用户上下文确定可见范围，不假定其就是某个 Agent |
| 查看 `view/self` | 必须有可信 Agent 执行上下文；无此上下文时明确指定目标 |

管理者查看 Agent1 的绑定时仍是管理者，不借用 Agent1 的执行权。全局路径和局部路径可以抵达同一资源，使用权限取决于真实请求者及当前获授关系。

当前机制已有 Agent 的 Principal 和独立管理主体 ControlPrincipal。底层 Session 仍把 `/scheme/ability/reports` 中的 reports 当作调用者 alias；Core 的 AbilityTreeSession 已在文件切片中显式支持全局实例入口、局部映射和 View，尚无通用递归树浏览。实际范围见[文件 Ability 资源切片：权威关系、Scheme 接入与面板](2026-10-29-ability-resource-slice.md)，底层机制见[运行时 API](2026-10-29-runtime-api.md)。

## 4. 定义、实例、成员和调用各自有身份

树的缩进表达组织关系，不替代对象身份。用“实体”泛指对象时，仍需说明它属于哪一层。

| 对象 | 含义 | 身份示意 |
| --- | --- | --- |
| Ability 定义 | 可复用的成员声明和内容 | 定义身份、版本及确定摘要 |
| Ability 实例 | 依定义、配置和接线装配的对象 | `reports-a` |
| Agent 获授关联 | Agent 获授某实例及其成员的关系 | `agent1/ability/reports` |
| Operation 入口 | 实例内的已绑定成员 | `(reports-a, operations/render)` |
| provider 启动实例 | 实际导出的一次运行 | incarnation |
| Invocation | 对 Operation 的一次调用 | invocation ID |

`/scheme/ability/reports-a/operations/render` 是合理的“实例 → 成员”嵌套。若成员没有独立配置、状态或生命周期，不必再增加 Operation 实例层；一次调用归 Invocation。删除 Agent 的获授关联，也不自动销毁其他 Agent 使用的共享实例。

## 5. 树上可以有链接，执行依赖确定引用

树形导航可以展开共享、投影和交叉引用。底层关系可能组成图，不要求复制资源。链接作为叶节点显示即可；不必递归展开到循环。

路径链接定位当前目标；取得的运行引用则固定目标、契约、绑定修订和提供者代次。重绑定后，新解析可以定位新目标，旧引用明确失效，不能静默改指。成员改名或资源前缀转接属于待定义的链接能力，当前 `bind()` 不实现这些重写。

树表达位置和资源关系，Operation 表达行为，事件和日志表达变化与事实。它们可以互相引用：消息日志可有树形入口，但路径层级不替代消息因果；一次目录读取也不代表全系统同一时刻的快照。

## 6. 三项基本原则

1. **SSOT：同一事实只有一个权威来源。** Agent 获授关系可以就是权限事实的公共表示，不再并列维护另一份可独立修改的权限目录。
2. **CQRS：写入与读取投影分工。** 修改 Agent 关联进入对应 owner 的写路径；View 从同一事实重建，不通过编辑投影改变业务事实。
3. **节点由用途证明。** 每个公开节点要直接或间接参与 Operation 行为、UI 消费或已明确的恢复／诊断场景；无用途节点可以删除、合并或留作内部实现。

这些原则在跨机部署时同样成立：事实可分散持有，投影按来源单向重建，不依赖共享 Python 对象。基本原语提供一致访问机制，并不要求把所有业务逻辑搬进运行时包。

## 相关文档

- [文件 Ability 资源切片：权威关系、Scheme 接入与面板](2026-10-29-ability-resource-slice.md) — 当日工作区新增的可执行文件资源闭环与独立面板。

- [ghrah-scheme](../README.md) — 文档入口与阅读顺序。
- [资源、权限与节点必要性](2026-10-29-resources-and-authority.md) — 获授树和工具绑定的具体语义。
- [运行时 API：契约、原语与生命周期](2026-10-29-runtime-api.md) — 当前真实可调用的 Python API。
- [Ability 容器 API 与 Scheme 接入](2026-10-29-ability-api.md) — 容器领域与机制包的边界。
- [二次开发：逻辑、Scheme 接入与 UI 并行推进](2026-10-29-development-guide.md) — 如何依契约分别实现。
- [第一代文档的实现状态与代码地图](2026-10-29-implementation-status.md) — 当前实现、目标模型与待定项。
