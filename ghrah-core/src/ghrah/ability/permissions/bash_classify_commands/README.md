# bash-classify 仓内覆盖

本目录作为 `CommandDatabase(..., user_dir=本目录)` 的用户规则根。

自 **bash-classify 0.14.1** 起，内置规则已覆盖阶段 1 所需的 fail-closed（未知 git 子命令、`pnpm exec` 无 `--`、`npm exec` 委托等），因此此处默认**不放**同名整文件覆盖。

若需微调，请使用 0.14.1 的继承写法（勿再整文件替换）：

```yaml
command: git
extends: builtin
# 仅声明要改动的字段 / 子命令
```

同名 `*.yaml` 会经库的 merge 叠在内置定义之上；错误配置在加载时校验失败，而不是静默降级。
