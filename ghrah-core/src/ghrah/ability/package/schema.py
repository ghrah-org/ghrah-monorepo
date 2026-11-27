# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""构建与运行时共用的 schema 元数据归一化；不导入作者代码。"""

from typing import Any

_SCHEMA_MAP_KEYWORDS = frozenset({"properties", "$defs", "definitions", "patternProperties"})
"""子 schema 映射：键是属性/定义名，不是 schema 关键字，不能按关键字剥离。"""


def strip_schema_metadata(node: Any, *, top_level: bool = True) -> Any:
    """剥离所有层级 title 与顶层 description，保留参数契约。

    ``properties`` / ``$defs`` 这类映射的键是字段名而非关键字：字段名恰为
    ``title`` / ``description`` 时必须保留，否则 ``required`` 会引用到不存在的
    属性，工具 schema 在 OpenAI 兼容端点上被判非法。
    """
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
                result[key] = {
                    name: strip_schema_metadata(subschema, top_level=False)
                    for name, subschema in value.items()
                }
                continue
            if key == "title" or key == "description" and top_level:
                continue
            result[key] = strip_schema_metadata(value, top_level=False)
        return result
    if isinstance(node, list):
        return [strip_schema_metadata(item, top_level=False) for item in node]
    return node
