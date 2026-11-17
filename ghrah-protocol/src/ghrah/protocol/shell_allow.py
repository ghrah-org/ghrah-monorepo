# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Shell 记忆校验与内置规则的跨模块数据契约；不包含判断实现。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ShellAllowValidationRequest(BaseModel):
    """请求 Core 校验一条待保存的记忆。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["exact", "class"]
    key: str = Field(min_length=1)


class ShellBuiltinRule(BaseModel):
    """Core 提供的只读内置规则说明。"""

    key: str
    label: str
    readonly: Literal[True] = True


class ShellBuiltinRulesView(BaseModel):
    """内置规则列表。"""

    rules: list[ShellBuiltinRule]


class ShellBuiltinRulesRequest(BaseModel):
    """请求内置规则，不携带项目或运行时字段。"""

    model_config = ConfigDict(extra="forbid")
