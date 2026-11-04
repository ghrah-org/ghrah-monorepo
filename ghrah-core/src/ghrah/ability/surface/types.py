# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""表面条目的数据形状。

表面条目是可见面里的一项：模型看到的工具名、它指向的成员、来源包、
是否常驻，以及后端当前是否可用。本模块只定义形状，不读取授权库、
不做投影；授权面与可用性的判定属于表面装配。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["MemberRef", "SurfaceEntry"]


class MemberRef(BaseModel):
    """表面条目指向的成员标识。

    成员由关系库里的实例与成员键共同定位，不是一个可以拼接的路径或
    模块名；模型传来的工具名只能查表，不能反推出模块位置。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str = Field(min_length=1, description="成员所属的能力实例标识")
    member: str = Field(min_length=1, description="实例内的成员键")


class SurfaceEntry(BaseModel):
    """可见面里的一项。

    ``static`` 表示该成员是否常驻工具数组；``available`` 表示后端当前
    是否可用，不可用时仍可描述，但执行会明确失败。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1, max_length=64, description="模型看到的函数名")
    member: MemberRef = Field(description="关系库里的成员标识")
    package: str = Field(min_length=1, description="来源包（展示与限定名用）")
    static: bool = Field(default=False, description="true = 常驻；false = 搜索后加载")
    available: bool = Field(default=True, description="后端可用性")
