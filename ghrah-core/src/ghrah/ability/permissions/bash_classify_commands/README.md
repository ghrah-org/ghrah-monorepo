# bash-classify 仓内覆盖

本目录作为 `CommandDatabase(..., user_dir=本目录)` 的用户规则根。

自 **bash-classify 0.14.1** 起，内置规则已覆盖多数 fail-closed。本目录只用
`extends: builtin` **增量**覆盖，禁止整文件替换。

当前覆盖：

| 文件 | 意图 |
| --- | --- |
| `node.yaml` | `--version` / `-v` → READONLY，降低只读误报 |

示例：

```yaml
command: git
extends: builtin
# 仅声明要改动的字段 / 子命令
```

同名 `*.yaml` 经库 merge 叠在内置定义之上；错误配置在加载时校验失败。
