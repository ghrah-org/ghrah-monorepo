---
title: Ability 容器 API 与 Scheme 接入
description: 单 Skill 容器的显式内容提交、获授范围、版本恢复、受控读取与 Context 消费
created: 2026-10-29
updated: 2026-10-30
status: active
---

# Ability 容器 API 与 Scheme 接入

> 面向能力作者与受信宿主开发者。Ability 的定义、内容和执行实质归 Core 的 `ghrah.ability`；独立 `ghrah.scheme` 提供访问与生命周期机制。

当前支持混合容器、纯 Operation 容器和单 Skill 内容包。完整生产 Agent、HITL 和生产接线仍须分别验收。下文描述当前已实现的隔离装配接口，不将来源扫描当作运行时授权。

## 一、定义与资源树

Ability 包有 `0..N` 个 Operation、`0..1` 个 Skill。Skill 具有明确键、一个正文，以及各自具名的 assets 和 references。两类附件共享内容底座，保留角色与父节点。

```text
/scheme/ability/<instance>/operations/<key>
/scheme/ability/<instance>/skill/<key>
/scheme/ability/<instance>/skill/<key>/assets/<key>
/scheme/ability/<instance>/skill/<key>/references/<key>
```

全局实例路径需要受信操作员身份并在 `acquire` 时指定 `global_instance=True`。Agent 默认使用自己的 alias；其明确路径为 `/scheme/agent/<id>/ability/<alias>/...`，同源 View 入口也可使用 `/scheme/view/<id>/ability/<alias>/...`。Skill 节点自身提供正文，不增加同级正文文件节点。

新定义使用 `schema_version: '0.2'`，在 `skills` 的受限单项映射内声明正文与附件。例如：

```yaml
skills:
  guide:
    body: {path: SKILL.md, media_type: text/markdown}
    assets:
      template: {path: assets/template.txt, media_type: text/plain}
    references:
      style: {path: references/style.md, media_type: text/markdown}
```

名称与说明从 SKILL.md frontmatter 的 `name`／`description` 单向派生；原文完整保留。Skill 键与源名称、Python 模块名分别处理。第二个 Skill、未知字段、重复 YAML 键、同类重复资源键、非法路径和未登记本地链接均拒绝。不同类别允许同名资源。

`0.1-draft` 定义仍可显式导入旧正文位置。非空的旧顶层 assets 和旧 references 字符串列表要求作者明确转换；系统不猜父 Skill，不制造空正文。旧的来源扫描与 lock 工具保留为受信准备工具，不能作为当前 Agent 的内容消费入口。

## 二、显式提交与恢复

完整流程把准备、提交、选择和授权分开。查询不会推进其中任一步。

1. 调用 `capture_ability_version(raw, root=...)` 在指定包根捕获声明清单。`CaptureLimits` 配置单文件、包总量和条目数上限。逐级目录描述符与 `NOFOLLOW` 拒绝符号链接，也防止校验后替换路径跳出包根。
2. 调用 `store.commit_version(operator, version)` 在同一事务保存定义、清单及按摘要寻址的字节。定义摘要、内容摘要、后端摘要和整体版本摘要分别保留。失败不发布半个版本。
3. 调用 `select_instance` 显式选择版本。纯内容实例可以省略文件根；文件 Operation 必须有真实效果范围。
4. 显式授予 Agent 范围，再由 `AbilitySchemeHost.install` 安装当前代次的导出。

标准源包不必另外编写重复元数据：`import_skill_package(root, key=..., identity=..., assets=..., references=...)` 接受完整显式附件清单，直接捕获标准 SKILL.md 包。不依据 Markdown 链接推断所有文件；只是被提及的模板同样可以登记。

运行内容保存在项目数据库的内容表中，以摘要去重；读取不访问源目录。修改源码不生效，提交新版本也不会自动切换实例。切换实例后，该实例的旧获授关系和工具选择失效，必须重新授权。其他实例不受影响。

宿主重启后通过 `host.restore(operator)` 从已提交数据重建，不扫描来源、不重演 Operation。字节损坏、缺失或清单摘要不符明确拒绝；不回退读源码。后端摘要不匹配的 Operation 不可用，独立内容仍可读。旧文件实例需要匹配的后端，或由操作员重新捕获、选择并授权；不改写历史摘要来复活旧引用。

## 三、获授范围与撤销

`GrantAbilityRequest.members` 明确只表示 Operation 键；Skill 内容用 `skill=SkillReadScope(...)` 独立表达。

```python
from ghrah.protocol.ability_tree import GrantAbilityRequest, SkillReadScope

change = GrantAbilityRequest(
    alias="reports",
    instance_id="reporting",
    members=[],
    skill=SkillReadScope(key="guide", body=True, assets=["template"], references=[]),
    expected_revision=0,
)
```

`assets`／`references` 为 `None` 时展开该类别当前版本的全部成员，空列表表示不授予；`body` 控制正文。`whole_container=True` 同时展开当前可暴露 Operation 与单 Skill 内容，任何成员无效则整组失败。它不选择模型工具，也不覆盖未来版本新增资源。

`allowed_roots` 约束文件 Operation 的真实文件访问；不成为 Skill 资源的文件系统权限。纯内容包无需填它。`bound_tools` 只接受已获授且允许暴露的 Operation，Skill 或附件不会自动成为工具。

`RevokeAbilityRequest(alias=..., expected_revision=...)` 撤销整个关联。给出 `members` 可局部撤销规范身份，例如 `operations/read_file`、`skill/guide` 或 `skill/guide/assets/template`。Skill 整体撤销覆盖正文和附件。保留的 Operation 工具选择继续存在，引用重新获取；无关实例和其他 Agent 不受影响。局部变更保守更新该 alias 的获授代次，因此旧引用需要重新获取。

关系库从 schema 2 事务升级到 schema 3；旧短名只转换为 Operation，内容范围为空，保留原修订和历史。升级前应保存数据库的 SQLite 一致备份；回退必须匹配旧代码和旧数据，不单独回滚程序。当前运行 run 直接读取宿主注入的生命周期来源，关系库不维护第二份 run。

## 四、读取、描述和模型消费

Skill、assets 和 references 支持 `read` 与 `describe`。它们复用 Scheme 五个原语；每次最后接纳重新核对请求者 run、实例代次、版本、父 Skill、角色、资源键和获授代次。

```python
body = await client.call(
    "/scheme/agent/alice/ability/reports/skill/guide",
    "read",
    {"offset": 0, "limit": 65536},
)
```

`read` 返回确定实例／版本／成员、原相对路径、媒体类型、摘要、总字节数、offset、byte_count、next_offset、encoding 和 content。文本提供 UTF-8，二进制提供 base64；UTF-8 字符跨块边界时也可返回 base64，确保字节无损。单块上限为 65536 字节，宿主可在 `AbilityAccessStore.open(..., read_limit=...)` 中降低限额。每一块都重新核权，撤销阻止后续读取；已经交付的字节不能收回。

`describe` 返回相同确定身份与内容元数据，不包含正文。读取与描述均不新增数据库写记录，因此另一连接持有写锁时仍可读取已提交状态。

`GrantedContentProvider` 持有某 Agent 的受信 Scheme 会话。它向 `ContextManager(..., ability_content=provider)` 提供获准目录，`load_into_context` 按需把完整有界文本加入实际 Context；二进制不自动注入模型。`read_relative` 只把相对链接解析到该 Skill 已登记且获授的资源。旧 `skill_provider` 与新端口必须明确择一，不因新授权失败而回退旧目录。

完整可运行示例见[完整 Ability 内容消费示例](../../ghrah-core/examples/ability_content/README.md)。它使用真实数据库、Provider、Context 和文本／二进制附件。

## 五、协议与独立面板

树投影升级为 `ghrah.ability-tree.v2`；v1 载荷显式拒绝。Python 作者模型单向生成 TS Zod 校验器，生成命令如下：

```sh
uv run --frozen --no-sync python scripts/export_ability_contracts.py --check
uv run --frozen --no-sync python scripts/export_ability_tree_fixture.py --check
```

独立面板展示容器版本、Operation 与单 Skill，附件显示在 Skill 子树；工具选择只列 Operation。文本安全地按普通文本预览，二进制显示范围并允许下载本次字节范围，后续块另行核权。客户端核对返回的成员和版本，不维护额外授权目录。生产网络传输和完整 Agent 接线仍由宿主明确提供。

## 六、支持边界与源码位置

| 能力 | 当前状态 |
| --- | --- |
| 文件 read_file／list_directory | 明确槽契约、真实后端与文件范围检查 |
| 单 Skill 正文、assets／references | 固定快照、治理、描述、有界读取及真实 Context／面板消费 |
| 其他 19 个内置 Operation | 算法和 YAML 迁入 `ability/builtin`；不据此宣称全部已由资源树运行接入 |
| 显式脚本入口 | `OperationDefinition.script` 保存绑定源内容；无通用执行后端时不可调用 |
| MCP、通用脚本执行、后台协调 | 尚未接入，须另行验收 |
| 生产 Agent、HITL、消息循环、停止／重启 | 仍使用既有生产链，本次隔离接入不替代最终切换验收 |

定义和导入在 `ghrah.ability.loading`、`content.py`、`versions.py`；关系写者在 `access/store.py`；Scheme 适配在 `scheme/host.py`；实际 Context 端口在 `consumer.py`。旧预判驱动目录已删除，保留的领域实现不再通过旧路径转导出。

## 相关文档

- [完整 Ability 内容消费示例](../../ghrah-core/examples/ability_content/README.md) — 可执行隔离装配。
- [文件 Ability 资源切片：权威关系、Scheme 接入与面板](2026-10-29-ability-resource-slice.md) — 首个 Operation 切片；最新内容契约以本文为准。
- [第一代文档的实现状态与代码地图](2026-10-29-implementation-status.md) — 验证结果及支持边界。
- [运行时 API：契约、原语与生命周期](2026-10-29-runtime-api.md) — 独立 Scheme 机制。
