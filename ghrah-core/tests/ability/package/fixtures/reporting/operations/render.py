# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""等价 fixture 的 Operation 实现：按模板渲染一份报告。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    """渲染参数。"""

    model_config = ConfigDict(extra="forbid")

    template: str = Field(description="模板名（assets 键）")
    rows: list[str] = Field(description="报告行", min_length=1)


class Output(BaseModel):
    """渲染结果。"""

    model_config = ConfigDict(extra="forbid")

    rendered: str = Field(description="渲染后的报告文本")


TITLE = "Render Report"
SUMMARY = "按模板渲染一份报告。"
TAGS: list[str] = ["render", "reporting"]
EFFECTS: list[str] = []
PERMISSIONS: dict[str, object] = {}
TIMEOUT_MS = 120000
RESOURCES: dict[str, object] = {"paths": ["template"]}
EXPOSE = True


async def execute(call, services):  # pragma: no cover - 生成器不执行
    """渲染入口（fixture 占位；build 只读声明，不执行）。"""
    raise NotImplementedError
