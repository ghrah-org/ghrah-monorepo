---
title: 文件 Ability 资源切片：权威关系、Scheme 接入与面板
description: 使用已实现的文件实例、获授节点、工具选择和 View，说明领域服务、Scheme 适配与独立 UI 的调用方法和边界
tags: [Scheme, Ability, API, UI]
created: 2026-10-29
updated: 2026-10-30
status: active
---

# 文件 Ability 资源切片：权威关系、Scheme 接入与面板

> 本文介绍 2026-10-29 工作区中可执行的首个产品资源切片。它把真实文件 Operation、持久化资源关系、Scheme 调用和面板契约连接起来，供后续 Agent 与传输接入复用。

基准提交为 `acf06fe`，本文新增代码尚未提交。生产 Core／Subject 的 Agent 装配没有切换；以下 API 需要受信宿主显式创建和注入，不会因导入模块自动启用服务。

## 1. 实际资源形状与职责

本切片使用同一个文件 Ability 定义中的 `read_file` 和 `list_directory` 两个成员。文件读取、枚举和权限检查复用 Core 已有后端。Scheme 适配负责确定调用目标；领域服务负责当前资源关系和接纳；UI 消费同源 View。

```text
已提交定义与实现版本 → 文件实例 files-alice（确定根目录）
                       ↑
Agent alice 的 ability/files 获授关联（两个成员、允许范围、修订）
                       ↑
Agent alice 的 bound_tools/read_report（只选择 read_file）
                       ↓
View → 工具选择面板／模型工具描述 → 固定引用 → 当前关系核对 → 真实 Operation
```

实例与获授关联不是两份权限状态。实例说明后端和根目录；获授节点记录 Agent 对这个实例的成员和范围；bound_tools 记录其中被选择为模型工具的成员。View 每次从这些事实读取生成，不具有独立写入口。

| 职责 | 当前代码入口 |
| --- | --- |
| 确定定义与实现版本 | [versions.py](../../ghrah-core/src/ghrah/ability/versions.py)：capture_file_version、AbilityVersion |
| 领域写入、只读投影和接纳 | [access/store.py](../../ghrah-core/src/ghrah/ability/access/store.py)：AbilityAccessStore |
| 真实文件后端 | [file_backend.py](../../ghrah-core/src/ghrah/ability/file_backend.py)：FileBackend |
| Scheme 装配和资源解析 | [scheme/host.py](../../ghrah-core/src/ghrah/ability/scheme/host.py)：AbilitySchemeHost、AbilityTreeSession、ToolProjection |
| 公共数据格式 | [ability_tree.py](../../ghrah-protocol/src/ghrah/protocol/ability_tree.py) 和 [ability-tree.ts](../../ghrah-observer-webui/packages/protocol/src/ability-tree.ts) |
| 可注入客户端和面板 | [ability-tree.ts](../../ghrah-observer-webui/packages/observer-core/src/ability-tree.ts)、[scheme-abilities-panel.vue](../../ghrah-observer-webui/packages/observer-web/src/components/scheme-abilities-panel.vue) |

## 2. 受信装配与完整调用示例

Core 默认安装不依赖尚未发布的 ghrah-scheme。Scheme 适配需要显式启用 `ghrah-core[scheme]`；当前在 workspace 或本地三个 wheel 组合中使用，Scheme 尚无对外发布通道。领域访问服务本身不导入 ghrah.scheme。

宿主核定身份后才创建 AccessIdentity／Principal／ControlPrincipal。`operators` 是此项目允许的管理主体集合，创建 ControlPrincipal 本身不获得管理资格。业务参数不接收自报 principal 或目标 run。

在 monorepo 根执行下列完整代码，会输出 `hello tree`。临时目录和 SQLite 文件由示例创建，关闭宿主和数据库后删除。

<!-- runnable: ability-resource-slice -->
```python
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from ghrah.ability.access import AbilityAccessStore, AccessIdentity
from ghrah.ability.scheme import AbilitySchemeHost
from ghrah.ability.versions import capture_file_version
from ghrah.protocol.ability_tree import GrantAbilityRequest
from ghrah.scheme import Principal


async def main():
    with TemporaryDirectory() as directory:
        base = Path(directory)
        root = base / "files"
        root.mkdir()
        (root / "memo.txt").write_text("hello tree", encoding="utf-8")
        admin = AccessIdentity("demo", "admin", "control", True)
        # 此字典只充当示例的生命周期 owner；生产接入须读取真实 owner。
        current_runs = {"alice": "run-1"}
        store = await AbilityAccessStore.open(
            base / "project.db",
            "demo",
            operators=frozenset({"admin"}),
            runs=current_runs.get,
        )
        host = AbilitySchemeHost(store)
        try:
            version = capture_file_version()
            await store.commit_version(admin, version)
            await store.register_agent(admin, "alice")
            await store.select_instance(admin, "files-alice", version.digest, root)
            await store.grant(
                admin,
                "alice",
                GrantAbilityRequest(
                    alias="files",
                    instance_id="files-alice",
                    members=["read_file", "list_directory"],
                    allowed_roots=[str(root)],
                    expected_revision=0,
                ),
            )
            await host.install("files-alice")
            client = host.session(Principal("demo", "alice", "run-1"))
            view = await client.read("alice")
            assert len(view.abilities[0].operations) == 2
            assert view.bound_tools == []
            result = await client.call(
                "/scheme/agent/alice",
                "bind_tool",
                {
                    "alias": "files",
                    "member": "read_file",
                    "tool_name": "read_report",
                    "expected_revision": view.revision,
                },
                request_key="choose-read-report-1",
            )
            assert result["error"] is None
            tools = await client.project_tools()
            try:
                assert len(tools.tools) == 1
                output = await tools.call("read_report", {"file_path": "memo.txt"})
                print(output["data"]["content"])
            finally:
                await tools.close()
        finally:
            assert (await host.close()).complete
            await store.close()


asyncio.run(main())
```

`runs(agent_id)` 是宿主必须注入的同步生命周期读取函数，返回当前 run 或 None；每次核定直接读取它。关系库不再保存 run，`register_agent` 只建立关系修订行，不启动或切换 Agent。返回 None 时，本切片拒绝该 Agent 的消费；停止态管理 View 尚未建模。生产接线需要将此函数接到真正的生命周期 owner，不能再建立第二份人工更新的运行表。

`install` 从已提交实例装配提供者，不读取原始 YAML。重复安装同一代次幂等；切换时先 withdraw 旧提供者，确认收束后复用同一 provider ID 发布新 incarnation。在途责任未收束时返回 BUSY，不能把旧实例冒充新实例。实际契约不变时，定义元数据或根目录变化不占用新的 contract 槽；真正不同的契约仍遵守运行时有界预算。

重启后，受信装配者可调用 `await host.restore(admin)` 从 list_instances 枚举的当前实例重建导出；list_versions 读取已提交定义。局部路由和会话在消费时派生。宿主 close 只收束自己的运行时；数据库仍由创建者关闭。

## 3. 已支持的路径与动作

公开路径由 AbilityTreeSession 解析为原有 Session 的受限引用。底层运行时的 `/scheme/<kind>/<alias>/<resource...>` 规则仍然存在；本切片没有将底层第三段统一改成全局 ID。

| 公开定位 | 实际语义 |
| --- | --- |
| `/scheme/ability/<alias>/operations/<member>` | 当前 Agent 的局部获授 alias；A/B 同名 alias 可以指向不同实例 |
| `/scheme/agent/<self>/ability/<alias>/operations/<member>` | 同一局部成员的显式 Agent 路径；本切片不能借此切换调用身份 |
| `/scheme/ability/<instance>/operations/<member>` 加 `global_instance=True` | 操作员显式取得全局实例成员，仍保留操作员身份 |
| `/scheme/agent/<self>/bound_tools/<name>` | 当前工具选择指向的成员，引用同时固定选择修订 |
| `/scheme/view/<agent>`，动作 `read`，参数 `{}` | 返回 AbilityTreeView；普通 Agent 只能读自己，操作员可读此项目中的目标 Agent |
| `/scheme/agent/<agent>` | 动作 `grant`、`revoke`、`bind_tool`、`unbind_tool`；输入模型见公共协议 |

grant／revoke 仅允许已配置操作员。工具选择允许当前 Agent 或操作员操作；它只能从当前获授成员中选择，不能扩大获授集合。只有 expose 为 true 的成员可以获授、发布或选择为工具，View 与调用核定也遵守这一声明。所有关系变更都带 `expected_revision`，与实际修订不符返回 `conflict`。同内容的变更不推进修订；替换获授关联会移除其旧工具选择。

公开 AbilityTreeSession／ToolProjection 的消费错误统一为 SchemeError；领域 AccessError 在适配边界转换。关系动作的业务拒绝仍属于类型化结果，而不是另一套异常通道。

关系动作使用 TreeChangeResult：成功返回提交后的 view，失败返回领域 error，两者恰有一项。外层 Scheme 核定仍可能先拒绝未获许可的调用。`AbilityTreeSession.call` 对变更要求显式 request_key；观察只等待原调用，不重新提交。调用完成不代表 UI 可自行猜测新的授权状态。

View 根返回结构化子树；目前不支持逐层目录浏览、单个 View 子节点 acquire、通用树 CRUD 或任意跨树链接解析。全局树的产品语义已经确定，这些通用浏览能力仍需后续接入。

## 4. 版本、关系与旧引用如何失效

capture_file_version 显式读取并密封两个所选成员的定义，记录定义摘要和当前文件后端代码摘要。版本摘要覆盖两者；它不是完整 Ability 包所有 Skill／Asset 的内容指纹。文件后端代码改变后要求重新加载服务进程，当前进程拒绝把磁盘新代码冒充已加载实现。

commit_version 只提交版本；select_instance 才选定实例版本和根目录。相同内容不推进实例代次。实际切换会撤去依赖该实例的旧获授关联及其工具选择，并记录受影响 Agent 的修订；无关实例和 Agent 不变。重新授予必须显式进行。普通 View／工具查询不重新扫描来源。选定实例代次与实际导出代次不符时，即使旧提供者仍在内存中，新 acquire 和接纳也拒绝；重新 install 前不会把新根目录的许可交给旧根目录后端。

引用固定实例、run、获授修订以及必要的工具选择修订。撤销后重新授予、解绑后重新绑定同名工具，均不会复活旧引用。ToolProjection 将一次模型输入的工具描述和固定引用保存在一起；模型回调使用原引用，不将工具名重新解释为当前的其他成员。

领域服务在同一 SQLite 文件中管理关系与修订通知，使用独立连接，可与既有 ContextBackend 共存。关系变更使用写事务和修订比较；两个只读文件 Operation 的接纳只用读取事务核对当前关系及路径范围，不写 admission 行、不持有写锁、不保存完整调用参数，admission_record 为 None。后端执行前再核对引用。这里尚未实现与 Context 提交的跨领域原子事务、审批流程、跨 boot 通用去重或完整故障回执。

`changes(admin, after_seq=0, limit=100)` 按项目有界读取修订通知，最多 1000 条，记录请求者、前后修订及适用的版本摘要，供消费者决定重新读取哪些关系。这不是完整事件回放日志；完整重建以关系表为准。schema 1 到 2 的事务迁移保留关系与修订，去掉 run 列，补充通知字段。历史 admission 表原样保留，既有行不自动删除，也不再追加；新数据库不创建此表。

领域直接读取的 View 不含 runtime，availability 为 unknown。宿主投影另附 `runtime.host_id` 和 UTC `runtime.observed_at`，只说明该 host 在此刻是否安装匹配实例。不同 host 的可用性可能不同，不能只比较关系 revision 就把它们当作同一次观测。alias、工具名和同实例 Operation key 在协议边界拒绝重复。

内部成员引用使用带版本的 MemberFence 严格字段编码，拒绝缺失、额外字段和错误类型；文件成员及其路径参数由 file_members.py 的显式注册表决定，不再按未知成员猜测路径字段。

## 5. 契约与 UI 如何独立开发

公共关系输入输出由 ghrah-protocol 的严格 Pydantic 模型定义，PydanticCodec 直接复用这些模型。所选文件 Operation 的参数由已提交 YAML schema 提供，JsonSchemaCodec 在发布前校验支持范围，并从同一作者提供运行验证和工具描述。ActionResult 的成功／失败外层结构由 [contracts.py](../../ghrah-core/src/ghrah/ability/contracts.py) 统一派生，View 与实际 Scheme 导出使用相同结果 schema。

JsonSchemaCodec 只支持明确声明的有限子集：对象和数组、标量类型、两项可空联合、独立 anyOf、标量 enum／const、长度与数值边界。对象必须拒绝额外字段；未知关键字、引用和不适用的约束在发布前拒绝。default 仅作描述，不隐式补值。内部 CodecArguments 包装不进入公开参数或结果。

导出命令在 monorepo 根运行：

```sh
uv run python scripts/export_ability_tree_fixture.py --check
```

该命令从实际定义和协议模型生成／核对正常、已选择、撤销、不可用和冲突样例。Python、TS 和组件测试消费同一文件。TS 的 createAbilityTreeClient 通过宿主注入的 transport 读取 View、提交变更，核对项目和目标；替身只按预先声明的响应推进，不另写授权算法。

SchemeAbilitiesPanel 可通过 agentId 和 client 两个属性注入使用，具备选择、解绑、空集合、不可用和冲突展示。它不乐观写入授权状态，不自动重试未知结果。当前没有挂到生产主页面，也没有新建 WebSocket 认证入口；真实连接由后续受信宿主接入。

## 6. 验证范围与下一步边界

真实集成用例见 [test_resource_tree.py](../../ghrah-core/tests/ability/test_resource_tree.py)：A/B 同名 alias、操作员身份、授权与选择分离、撤销及重授、路径范围、并发修订冲突、SQLite 重开、同文件 Context 共存、显式版本变化、真实文件效果以及旧模型工具引用失效。

本切片不包含完整 Agent 认知循环、HITL 审批、Subject 认证传输、生产页面接线、MCP／exec、多 Skill／完整 Asset 或完整事件协调框架。下一步将这些可复用领域与 Scheme 入口接到真实生命周期和消息／模型工具循环，再验收审批期间撤权、停止／重启和提交链。

## 相关文档

- [ghrah-scheme](../README.md) — 文档入口。
- [Scheme 系统模型：全局树与局部入口](2026-10-29-system-model.md) — 本切片所服从的产品树语义。
- [资源、权限与节点必要性](2026-10-29-resources-and-authority.md) — 关系与消费原则。
- [运行时 API：契约、原语与生命周期](2026-10-29-runtime-api.md) — 公共机制与 codec 接缝。
- [Ability 容器 API 与 Scheme 接入](2026-10-29-ability-api.md) — 定义与后端基础。
- [二次开发：逻辑、Scheme 接入与 UI 并行推进](2026-10-29-development-guide.md) — 三条职责线的开发方法。
- [第一代文档的实现状态与代码地图](2026-10-29-implementation-status.md) — 全包支持范围。
