# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""工作台动态页面的读取契约与变更事件。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PageGetPayload(BaseModel):
    """读取项目当前页面；未渲染过时 page 为 null。"""

    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)


class PagePayload(BaseModel):
    """项目页面快照：HTML 与数据分离，revision 单调递增。"""

    project_id: str
    page_id: str
    title: str
    html: str
    data: dict[str, Any]
    revision: int = Field(ge=0)
    updated_at: str
    updated_by: str = ""


class PageStateResultPayload(BaseModel):
    """页面读取命令的返回值。"""

    page: PagePayload | None = None


class PageChangedPayload(BaseModel):
    """页面被 Agent 工具写入后的轻量通知；客户端据此重新拉取整页。"""

    project_id: str
    page_id: str
    revision: int = Field(ge=0)
