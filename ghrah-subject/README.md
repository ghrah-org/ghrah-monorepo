# ghrah-subject

ghrah 控制面/执行层 - 持有工作区、执行沙箱命令、持久化 ActionChain、裁决 HITL。

## Workspace 挂载语义

Workspace 是**登记 + 授权 + 解挂**三件事，对挂载目录零物理操作：

- `workspace_register` 仅登记已有目录（store 记录 + sandbox cwd 授权），不 git
  init、不写 git config、不 add/commit、不落 marker；`provider_type="git"` 显式拒绝。
- 解挂（`destroy_workspace` / `unregister_workspace`）只软删 store 记录 + 解除
  sandbox 授权，**永不物理删除目录**（rmtree 全域退出）。
- 版本能力命令（`workspace_snapshot`/`workspace_rollback`/`workspace_diff`）随
  legacy GitWorkspaceProvider 一并移除，路由保留但返回
  `capability_not_supported`；`workspace_status` 降级为存在性/可写性。git 观测
  由 agent 侧只读命令承担（`git status`/`git diff` 在 SAFE 子命令白名单内）。
- 快照/回滚的后续方案是 harness 侧 shadow-git checkpoint 库（存储移出挂载
  目录，backlog）。
- 存量 store 记录在 `WorkspaceStore.start()` 时自动 retag：`git` → `plain`。

## 安装

```bash
uv sync
```

## 运行

```bash
ghrah-subject
```

### 系统宿主入口

`ghrah-subject-host` 启动独立微内核与代内声明的系统服务。产品清单中的
`subject/host` 指向这个入口；工具链负责构建、安装和启动，工作目录必须是
同时包含 `catalog.json` 与 `GENERATION.toml` 的代目录。

宿主先核对 catalog 文件摘要，再创建三个固定消息连接、启动内核及全部必需
服务。自检通过前 `/health` 返回 503，完成后返回 200；WebSocket 也受同一
就绪门控。内核或必需服务无法恢复时，宿主退出非零，不重建内核。

正常停止先关闭业务接入，再逆序关闭服务资源、排空调用并回收内核。POSIX
支持 SIGINT/SIGTERM；Windows 支持 Ctrl+C/Ctrl+Break。工具链的后台停止还
使用私有停止文件，因此无控制台的 Windows 实例也能执行相同收尾流程。

配置中的 `${DATA_ROOT}`、`${DISTRO_ROOT}`、`${GENERATION_ID}` 仅从工具链
注入的 `GHRAH_DATA_ROOT`、`GHRAH_DISTRO_ROOT`、`GHRAH_GENERATION_ID` 展开。
未识别的占位符、缺失变量、身份目录不一致或非法启动策略会在创建内核前拒绝。
可显式设置 `GHRAH_BOOT_REPORT_PATH` 保存本轮结果；未设置时不写宿主报告文件。

旧 `ghrah-subject-server` 入口仍保留，可供显式选择旧装配路径的发行版使用。

## 配置

通过环境变量配置，前缀为 `GHRAH_SUBJECT_`。主要配置项：

| 环境变量 | 说明 | 默认值 |
|---------|------|-------|
| `GHRAH_SUBJECT_WORKSPACE_ROOT` | 工作区根路径 | `~/ghrah-workspace` |
| `GHRAH_SUBJECT_DB_PATH` | SQLite 数据库路径 | `~/.ghrah/subject.db` |
| `GHRAH_SUBJECT_LOG_LEVEL` | 日志级别 | `INFO` |

## 能力装配与授权

Subject 使用 `ghrah.ability` 提供能力声明、工具和 Skill。能力来源的默认值是
`builtin`，现有显式配置也可沿用这个值。升级前请移除
`GHRAH_SUBJECT_ABILITY_SOURCE=legacy`，为旧 Project 的 Agent 配置 v2 模板
`manifest_ref`，并使用授权库授予模板需要的成员。

原 `manifest/abilities/` 中的独立 Ability YAML 已退役。Agent 模板仍保存在
`manifest/agents/`。仍需使用的自定义工具和 Skill 可以作为 v1 能力包安装，
再通过授权库向目标 Agent 授予成员。已退役的 `command_proxy` 自动注册桥可迁移为
可授权的 v1 Operation。

Agent 模板只表达申请，授权由
`GHRAH_SUBJECT_ABILITY_GRANT_POLICY` 的显式策略核定并写入项目关系库。授权主体与
策略中的 `agent` 都使用稳定 Agent ID，显示名不作为授权键。未提供 ID 的新建入口
在受信装配处生成 UUID；恢复原主体时需要携带原 ID。

需要文件能力访问多个目录时，可以显式设置 `GHRAH_SUBJECT_ABILITY_FILESYSTEM_ROOTS` 为绝对路径的 JSON 数组。文件检查器与关系授权使用这些根的并集，实例公共父目录仅用于定位路径。默认工作目录和 `{{workspace}}` 仍由原工作区配置提供。

稳定 ID 必须是 32 位小写十六进制 UUID；直接传显示名、空 ID 的程序化装配、策略
中的名称键和迁移快照中的名称键都会被拒绝。新建顶层/子 Agent 由装配者生成 ID；
程序化调用需在物化前分配 ID，并在 `supervisor.spawn_reservation(config)` 内完成
核定、注册和 run 绑定，不能在工具表固定之后再补一个身份。

例如，只向一个确定主体授予读取文件能力：

```sh
export GHRAH_SUBJECT_ABILITY_SOURCE=builtin
export GHRAH_SUBJECT_ABILITY_GRANT_POLICY='{"mode":"replace","rules":[{"agent":"0123456789abcdef0123456789abcdef","package":"ghrah.fs","operation":"read_file"}]}'
```

创建或恢复该 Agent 时，显式携带相同的 `agent_id`。存量 Project 定义缺少 ID 时，
builtin 恢复会返回 `stable_agent_id_required`；先备份 Project 定义，再通过
`project_remove_agent` / `project_add_agent` 显式重建主体并重新授权，不按旧显示名
猜测 UUID 或自动继承旧 Context。

有效权限只能收紧包声明。写入、网络与 shell 权限按 AND 合并；模板省略该字段时
继承包声明，显式 false 则禁用。只读和审批要求按 OR 合并。自动审批、白名单和
人工批准均不能解除危险命令、只读、沙盒、显式拒绝或授权撤销。

Ability 关系域使用版本 5。旧版本的名称授权不会自动转给同名新主体；遇到旧域时
启动明确失败。开发环境可停止使用该项目数据库的全部相关实例后，先查看范围：

```sh
uv run python scripts/reset_ability_grants.py --database /path/to/project.db
```

确认范围后，显式指定一个尚不存在的备份文件再应用：

```sh
uv run python scripts/reset_ability_grants.py --database /path/to/project.db \
  --backup /path/to/project-before-ability-reset.db --apply
```

工具先备份完整数据库，再在事务内删除列出的 Ability 自有表；Context、消息等
其他领域的数据保留。重新启动会初始化新的 Ability 域，再按稳定 ID 显式授权。
若使用迁移快照，快照的 `agents` 键也必须是操作员确认过的稳定 ID，不以名称猜测
归属。需要保留旧授权时应先准备确定的主体映射，不能直接套用旧名称快照。

跨集群启动通过项目数据库的 `ability_registrations` 独占同一主体；该表不保存 run，
运行代次仍从当前 Supervisor 读取。正常停止后释放占位；失败启动按修订回滚本次
授权和工具选择，新建 checkpoint 被回收，恢复前的 checkpoint 保留。若遇到并发
管理写入，或进程异常退出留下占位，系统拒绝自动夺取主体；须停止相关实例、备份
并显式处理本域。维护工具只做逻辑删除，不承诺擦除备份或数据库空闲页中的旧字节。
