# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""无作用域的 Shell 规则 API；校验逻辑由 Core 独占，不读写项目数据库。"""

from __future__ import annotations

import shlex

from ghrah.protocol.interface import Handler
from ghrah.protocol.shell_allow import (
    ShellAllowValidationRequest,
    ShellBuiltinRule,
    ShellBuiltinRulesRequest,
    ShellBuiltinRulesView,
)
from ghrah.protocol.views.common import AckView

from ghrah.ability.permissions.bash_classify_frontend import classify_command_expression
from ghrah.ability.permissions.shell_allow import (
    extract_shell_class,
    irreversible_reason,
    is_builtin_shell_expression,
)


def make_shell_handlers() -> dict[str, Handler]:
    """构造命令记忆校验和内置规则读取处理器。"""

    async def validate_allow(request: ShellAllowValidationRequest) -> AckView:
        """验证记忆是否合法；无效输入由统一异常翻译器返回 INVALID_ARGUMENT。"""
        command = request.key
        if request.kind == "class":
            parts = request.key.split(":", 2)
            prefixes = {
                "uv:run": ["uv", "run"],
                "pnpm:script": ["pnpm", "run"],
                "npm:script": ["npm", "run"],
            }
            if len(parts) != 3:
                raise ValueError("Invalid shell class key")
            family, name = ":".join(parts[:2]), parts[2]
            if family not in prefixes or not name:
                raise ValueError("Invalid shell class key")
            command = shlex.join([*prefixes[family], name])
        expression = classify_command_expression(command)
        reason = irreversible_reason(expression)
        if reason is not None:
            raise ValueError(reason)
        if request.kind == "class" and extract_shell_class(expression) != request.key:
            raise ValueError("Unsupported shell class")
        if is_builtin_shell_expression(expression):
            raise ValueError("Builtin rules do not need stored memory")
        return AckView()

    async def builtin_rules(request: ShellBuiltinRulesRequest) -> ShellBuiltinRulesView:
        """返回 Core 内置放行规则的说明。"""
        return ShellBuiltinRulesView(
            rules=[
                ShellBuiltinRule(key="uv:run:pytest", label="uv run pytest"),
                ShellBuiltinRule(key="uv:run:ruff", label="uv run ruff (recognized subcommands)"),
                ShellBuiltinRule(key="pnpm:script:test", label="pnpm test"),
            ]
        )

    return {"shell.validate_allow": validate_allow, "shell.builtin_rules": builtin_rules}
