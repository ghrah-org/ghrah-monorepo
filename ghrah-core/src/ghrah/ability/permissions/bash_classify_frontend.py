# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""共享 Shell 规则的兼容导出；实现由协议包统一提供。"""

from ghrah.protocol.shell.bash_classify_frontend import (
    OVERRIDES_DIR,
    classify_command_expression,
    expression_is_dangerous,
    expression_is_unknown,
    get_command_database,
    is_readonly_low,
    iter_classified_segments,
    segment_base_sub,
)

__all__ = [
    "OVERRIDES_DIR",
    "classify_command_expression",
    "expression_is_dangerous",
    "expression_is_unknown",
    "get_command_database",
    "is_readonly_low",
    "iter_classified_segments",
    "segment_base_sub",
]
