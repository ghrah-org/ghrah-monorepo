# ability 调试脚本

五个脚本，各覆盖一条面，**只用 `ghrah.ability` 对外的函数**（`ghrah.ability.build` /
`ghrah.ability.versions` / `ghrah.ability.access` / `ghrah.ability.scheme` 为该面的
公开子模块）。都是只读的，唯一写盘的是 05 的临时 sqlite（退出即删）。

```sh
# 仓库根执行
uv run python ghrah-core/examples/ability_debug/01_catalog.py
uv run python ghrah-core/examples/ability_debug/02_build.py
uv run python ghrah-core/examples/ability_debug/03_implementation.py
uv run python ghrah-core/examples/ability_debug/04_content.py
uv run python ghrah-core/examples/ability_debug/05_grant.py
```

PyCharm：解释器选仓库根的 `.venv`（`uv sync --all-packages` 生成），对单个文件
右键 Run 即可；脚本用 `__file__` 定位仓库，所以工作目录不影响结果。

| 脚本 | 面 | 验证什么 | 期望 |
| --- | --- | --- | --- |
| `01_catalog.py` | 目录面 | 扫描根、包与成员、技能清单与正文 | 2 个扫描根；17 个包（12 个合成）；12 个技能；`render()` 输出 `<system-reminder>` 目录 |
| `02_build.py` | 生成区（写盘前） | `scan_members` 扫出什么、磁盘上的 `members` 是否已同步（只读 dry-run） | 内置 fs 与示例包都输出 `已同步` |
| `03_implementation.py` | 实现面 | 运行时视图、工具参数是否来自 `bind_tool()`、实例化 | 21 个成员视图 / 19 个工具；`read_file` 4 个参数；`conversation`、`end_task` 无工具 |
| `04_content.py` | 内容面 | 内容寻址：版本三摘要、逐成员清单、文件切片 | 6 个成员（含 `operations/render/script`、4 个附件）；`fs_backend` 契约在 |
| `05_grant.py` | 授权面 | 提交→登记→选择实例→授予→安装→受控读取（临时库） | 视图 3 个 operation + 技能 `guide` + 2 类附件；能读到正文与 `references/style.md` |

改动 ability 模块后，按 01 → 03 → 04 → 05 的顺序跑一遍，就能覆盖
"目录读得对 / 工具面对 / 内容封得住 / 授权读得到" 四条主链；`02` 是写盘前体检，改生成规则时单独跑。

> `05_grant.py` 需要一个可写的临时目录。若 IDE 环境里 `%TEMP%` 不可写（Windows 上常见），
> 脚本会退回仓库内 `.scratch/` 并在结束时删除；两者都失败时按提示设置 `TEMP` 即可。