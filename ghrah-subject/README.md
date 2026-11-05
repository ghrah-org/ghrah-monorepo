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

## 配置

通过环境变量配置，前缀为 `GHRAH_SUBJECT_`。主要配置项：

| 环境变量 | 说明 | 默认值 |
|---------|------|-------|
| `GHRAH_SUBJECT_WORKSPACE_ROOT` | 工作区根路径 | `~/ghrah-workspace` |
| `GHRAH_SUBJECT_DB_PATH` | SQLite 数据库路径 | `~/.ghrah/subject.db` |
| `GHRAH_SUBJECT_LOG_LEVEL` | 日志级别 | `INFO` |

## legacy 装配模式（已废弃）

`GHRAH_SUBJECT_ABILITY_SOURCE=legacy` 已废弃，仅保留旧部署兼容，用于尚未迁移到
`builtin` 的存量环境；默认值仍为 `legacy` 只是为了避免旧部署启动失败，不代表
推荐使用。

legacy 模式**不在权限治理范围内**：能力声明、工具实例与 Skill 读取仍走旧注册表
和旧物化路径，不计算有效权限，不执行只读约束、`fs_write`/`net_access`/
`shell_access` 权限位检查，命令检查器也不消费 `allowed_commands`/
`denied_commands`。因此不要把它当作受治理或安全的装配方式；新部署应显式设置
`GHRAH_SUBJECT_ABILITY_SOURCE=builtin` 并按下一节配置授权策略。

## builtin 能力的授权与旧库处理

设置 `GHRAH_SUBJECT_ABILITY_SOURCE=builtin` 后，Agent 模板只表达申请；授权由
`GHRAH_SUBJECT_ABILITY_GRANT_POLICY` 的显式策略核定并写入项目关系库。授权主体与
策略中的 `agent` 都使用稳定 Agent ID，显示名不作为授权键。未提供 ID 的新建入口
在受信装配处生成 UUID；恢复原主体时需要携带原 ID。

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
