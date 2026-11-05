# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""构建与运行时共用的 schema 元数据归一化；不导入作者代码。"""

from typing import Any


def strip_schema_metadata(node: Any, *, top_level: bool = True) -> Any:
    """剥离所有层级 title 与顶层 description，保留参数契约。"""
    if isinstance(node, dict):
        return {
            key: strip_schema_metadata(value, top_level=False)
            for key, value in node.items()
            if key != "title" and not (key == "description" and top_level)
        }
    if isinstance(node, list):
        return [strip_schema_metadata(item, top_level=False) for item in node]
    return node
