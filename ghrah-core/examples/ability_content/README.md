---
title: 完整 Ability 内容消费示例
description: 在临时数据库中提交单 Skill 与附件快照，通过获授关系和 Scheme 读入实际 Context
created: 2026-10-30
---

# 完整 Ability 内容消费示例

> 面向受信宿主的接入示例。示例创建临时项目，退出时清理；不会启动生产 Agent。

从 monorepo 根执行：

```sh
uv run --frozen --no-sync python ghrah-core/examples/ability_content/demo.py
```

环境需要显式安装 `ghrah-core[scheme]` 及本地 Scheme 包。基础 Core 的定义、快照和关系服务不要求安装 Scheme；只有 Scheme 适配与本示例需要它。尚未发布的本地包应使用 workspace 或本地 wheel 组合。

`reporting/` 是原样保留的源包：`SKILL.md` 是正文及名称、说明的唯一作者；`ability.yaml` 声明可选单 Skill 的键、附件清单和 Operation。`assets` 与 `references` 均包含文本与二进制，同类别内按键区分，不根据文件扩展名猜归属。仅在正文中提及的模板也在显式清单内。

示例依次捕获字节、提交版本、选择实例、登记 Agent、显式授权、安装导出，然后获取目录并按需读取正文和参考文件。最后输出同源 View、二进制的 base64 结果以及实际 Context 消息。`whole_container=True` 只展开本次选定版本，不授权未来新增成员，也不选择模型工具。

`render` 只演示脚本到 Operation 的声明映射和脚本内容快照。通用脚本后端尚未接入，因此该成员的可用性为 `unavailable`。文件读取与目录列举使用真实文件后端；内容读取另行核权，不依赖文件后端就绪。完整生产 Agent、HITL、MCP、通用脚本执行和后台协调需要各自验收。

## 相关文档

- [Ability 容器 API 与 Scheme 接入](../../../ghrah-scheme/docs/2026-10-29-ability-api.md) — 定义、提交、授权和消费端口。
