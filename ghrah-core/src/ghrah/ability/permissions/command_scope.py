# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""共享 Shell 规则的兼容导出；实现由协议包统一提供。"""

from ghrah.protocol.shell.command_scope import (
    ParsedCommand,
    command_entry_covers,
    command_in_scope,
    normalize_command_entry,
    parse_command_entry,
)

__all__ = [
    "ParsedCommand",
    "command_entry_covers",
    "command_in_scope",
    "normalize_command_entry",
    "parse_command_entry",
]
