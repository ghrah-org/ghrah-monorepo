# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""生成器 fixture 的 Operation 实现：探活。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    """探活参数。"""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(description="目标主机")


class Output(BaseModel):
    """探活结果。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="是否可达")


TITLE = "Ping"
SUMMARY = "探活。"
TAGS: list[str] = ["probe"]
EFFECTS: list[str] = ["net:request"]
PERMISSIONS: dict[str, object] = {"net_access": True}
TIMEOUT_MS = 5000
RESOURCES: dict[str, object] = {}
EXPOSE = True


async def execute(call, services):  # pragma: no cover - 生成器不执行
    """探活入口（fixture 占位；build 只读声明，不执行）。"""
    raise NotImplementedError
