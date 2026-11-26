# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目业务数据源的登记、同步和权威快照契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MaterialStateGetPayload(BaseModel):
    """读取项目业务数据；未登记时 state 为 null。"""

    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)


class MaterialStateRegisterPayload(MaterialStateGetPayload):
    """登记物料契约；相同版本与契约重复登记不重置业务当前值。"""

    material_id: str = Field(min_length=1)
    material_version: str = Field(min_length=1)
    title: str = ""
    initial_data: dict[str, Any]
    data_schema: dict[str, Any]
    patch_schema: dict[str, Any] | None = None


class MaterialStateSyncPayload(MaterialStateGetPayload):
    """按持久游标消费一个 Room 中的 Agent patch，单次有界。"""

    room_id: str = Field(min_length=1)
    limit: int = Field(default=200, ge=1, le=1000)


class MaterialStatePayload(BaseModel):
    """项目业务数据当前值；HTML 和物料归档不属于该快照。"""

    project_id: str
    material_id: str
    material_version: str
    title: str
    data: dict[str, Any]
    revision: int = Field(ge=0)
    updated_at: str


class MaterialPatchFieldPayload(BaseModel):
    """后端确认的字段修改结果。"""

    key: str
    label: str
    value: Any


class MaterialPatchEventPayload(BaseModel):
    """按 RoomLog ID 幂等的 patch 执行回执。"""

    room_log_id: str
    room_id: str
    status: Literal["applied", "unchanged", "rejected"]
    revision: int = Field(ge=0)
    fields: list[MaterialPatchFieldPayload] = Field(default_factory=list)
    error: str = ""


class MaterialStateResultPayload(BaseModel):
    """三个数据源命令的统一返回值；applications 为最近的持久回执。"""

    state: MaterialStatePayload | None = None
    applications: list[MaterialPatchEventPayload] = Field(default_factory=list)
    has_more: bool = False
