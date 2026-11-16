# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""共享 Shell 规则的兼容导出；实现由协议包统一提供。"""

from ghrah.protocol.shell.shell_allow import (
    DEFAULT_DANGEROUS_COMMANDS,
    DEFAULT_DANGEROUS_SUB_COMMANDS,
    ShellAllowLookup,
    ShellRememberOptions,
    build_shell_remember_options,
    extract_shell_class,
    irreversible_reason,
    is_builtin_shell_expression,
    is_shell_expression_allowed,
    matches_shell_allow,
    normalize_shell_command,
)

__all__ = [
    "DEFAULT_DANGEROUS_COMMANDS",
    "DEFAULT_DANGEROUS_SUB_COMMANDS",
    "ShellAllowLookup",
    "ShellRememberOptions",
    "build_shell_remember_options",
    "extract_shell_class",
    "irreversible_reason",
    "is_builtin_shell_expression",
    "is_shell_expression_allowed",
    "matches_shell_allow",
    "normalize_shell_command",
]
