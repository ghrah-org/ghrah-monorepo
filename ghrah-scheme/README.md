---
title: ghrah-scheme
description: ghrah 的 Scheme 访问机制与第一代技术文档入口
created: 2026-10-26
updated: 2026-10-30
status: active
---

# ghrah-scheme

`ghrah-scheme` 为 ghrah 提供类型化 Scheme 访问机制，导入名为 `ghrah.scheme`。它把资源名字解析到确定提供者，通过受限引用访问动作，并管理有界调用与资源收尾。

当前版本 `0.1.0a0` 已实现进程内控制平面原型，包含严格契约、局部绑定、五个消费原语、受控发现与显式生命周期。当日工作区已新增首个文件 Ability 切片，包含持久获授关系、工具选择、View 和可注入面板；全局产品树及生产接线仍待完善。现有 Core／Subject 通路尚未整体切换，包未纳入对外发布白名单。

## 第一代技术文档

这套文档先解释最终系统外观与原则，再给出当前 API 和开发方法。第一代指文档版本；各项支持范围以实现状态篇为准。

| 文档 | 回答什么问题 |
| --- | --- |
| [Scheme 系统模型：全局树与局部入口](docs/2026-10-29-system-model.md) | 管理员、Agent 和一般用户怎样找到资源，定义、实例和链接如何区分 |
| [资源、权限与节点必要性](docs/2026-10-29-resources-and-authority.md) | Agent 子树如何表达权限和工具绑定，View 如何同源投影，节点为何保留 |
| [运行时 API：契约、原语与生命周期](docs/2026-10-29-runtime-api.md) | 当前 Python API、可执行示例、发现、接纳、取消、去重及关闭 |
| [Ability 容器 API 与 Scheme 接入](docs/2026-10-29-ability-api.md) | 容器定义、Operation／Skill／Asset、现有来源 API 和 Provider 适配 |
| [二次开发：逻辑、Scheme 接入与 UI 并行推进](docs/2026-10-29-development-guide.md) | 如何按共同契约分别开发逻辑、接入和消费界面 |
| [文件 Ability 资源切片：权威关系、Scheme 接入与面板](docs/2026-10-29-ability-resource-slice.md) | 已实现切片的完整装配示例、公开路径、持久关系与 UI 注入 |
| [第一代文档的实现状态与代码地图](docs/2026-10-29-implementation-status.md) | 已实现、当日工作区新增与尚未实现的目标分别在哪里 |

初次接触时依次阅读前两篇，再按角色选读：使用当前机制读运行时 API；新增能力读 Ability API；开发新功能或面板读开发指南。开始编码前核对实现状态。

## 在 workspace 中使用

包目前作为 monorepo 成员开发。Core 默认安装不依赖本包，Scheme 适配属于显式 `scheme` extra；本包尚未发布时使用 workspace 或本地 wheel 组合，不将该 extra 当作已可从 PyPI 独立解析的功能。以下命令在 `ghrah-monorepo` 根执行，Python 依赖通过根 workspace 管理：

```sh
uv sync --all-packages --group dev --frozen
uv run pytest ghrah-scheme
uv run ruff check ghrah-scheme
MYPYPATH=ghrah-scheme/src uv run mypy --explicit-package-bases -p ghrah.scheme
uv build --package ghrah-scheme
```

最小使用关系是受信宿主创建 SchemeRuntime、发布 Provider 并安装局部绑定，再向消费者注入 Session。消费者通过 acquire、submit、observe、cancel、release 使用资源；catalog／describe 负责发现。参见[完整可执行示例](docs/2026-10-29-runtime-api.md#3-最小完整示例)。

## 包边界与文档维护

本包依赖 ghrah-protocol 的公共描述格式与 Pydantic，不依赖 Core 才能运行。Ability 定义和执行实质归 Core；业务权威、宿主装配及 UI 也不搬入公共机制包。源码以 [公开导出](src/ghrah/scheme/__init__.py) 为入口。

文档校准基线为 `acf06fe` 与 2026-10-29 工作区；当日新增 Ability 门面另行标注。当前行为以代码和测试为准，目标设计明确写出尚未实现的部分。平台文档入口见 [ghrah 文档索引](../docs/ghrah-00-doc-index.md)。
