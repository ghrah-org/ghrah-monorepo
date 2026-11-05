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

`reporting/` 是一个 v1 源包：`ability.yaml` 只写头段（`format` / `schema_version: '1'` / `identity` / `title` / `description`），`members` 由生成器按 `operations/` 与 `skills/` 的声明生成。示例包只有内容成员：`skills/guide/` 下有 `SKILL.md` 正文、文本与二进制 `assets` / `references`，以及一个 `scripts/render.py`；`assets` 与 `references` 同类别内按键区分，不根据文件扩展名猜归属。仅在正文中提及的模板也在显式清单内。外部包的 Operation 由安装后的进程内 Provider 加载（P2），本示例不含可执行 Operation。

示例依次捕获字节、提交版本、选择实例、登记 Agent、显式授权、安装导出，然后获取目录并按需读取正文和参考文件。最后输出同源 View、二进制的 base64 结果以及实际 Context 消息。`whole_container=True` 只展开本次选定版本，不授权未来新增成员，也不选择模型工具。

`render` 是 Skill 内的委托脚本（`skills/guide/scripts/render.py`），不是 Operation：它只登记内容快照，不生成工具 schema；脚本执行入口 `run_script` 属 P3。内容读取与文件切片是两套机制——本示例只演示内容读取；真实文件操作的文件切片由 `test_resource_tree.py` 用内置 `ghrah.fs` 覆盖。完整生产 Agent、HITL、MCP、脚本执行和后台协调需要各自验收。

## 相关文档

- [Ability 容器 API 与 Scheme 接入](../../../ghrah-scheme/docs/2026-10-29-ability-api.md) — 定义、提交、授权和消费端口。
