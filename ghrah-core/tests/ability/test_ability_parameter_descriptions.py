# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""内置操作参数必须携带 ``description``（实现面与定义面各一条断言）。

实现面的 ``*Input(BaseModel)`` 是 ``bind_tool()`` 交给模型的那份参数 schema；
定义面（``ability.yaml``）同样声明了参数语义。两份都不允许出现空描述：
空描述意味着模型只能看到参数名，看不到「这个参数是干什么的」。

本用例把这件事固化为回归断言——新增成员或补参数时，不会悄悄退回无描述的
状态，也不再依赖一次性脚本人工核对。
"""

from __future__ import annotations

from ghrah.ability.loading.ability_catalog import (
    builtin_handlers,
    create_ability,
    load_all_builtin_manifests,
)


def test_implementation_schema_describes_every_parameter() -> None:
    """实现面（``bind_tool``）的每个参数都有非空 ``description``。"""
    missing: list[str] = []
    for handler in builtin_handlers():
        tool = create_ability(handler).bind_tool()
        if tool is None:
            continue
        parameters = tool["function"]["parameters"].get("properties", {})
        missing.extend(
            f"{handler}.{name}"
            for name, parameter in parameters.items()
            if not parameter.get("description")
        )

    assert missing == []


def test_definition_schema_describes_every_parameter() -> None:
    """定义面（``AbilityManifest.tool``）的每个参数都有非空 ``description``。"""
    missing: list[str] = []
    for full_name, manifest in load_all_builtin_manifests().items():
        if manifest.tool is None:
            continue
        missing.extend(
            f"{full_name}.{name}"
            for name, parameter in manifest.tool.parameters.items()
            if not parameter.description
        )

    assert missing == []
