# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""schema 元数据剥离：只剥关键字，不误删同名字段。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ghrah.ability.package.provider import strip_schema_metadata


class _Input(BaseModel):
    """字段名恰为 title / description 的输入模型。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    data: str


def test_field_named_title_and_description_are_kept() -> None:
    """properties 的键是字段名：title/description 不能被当成元数据删掉。"""
    schema = strip_schema_metadata(_Input.model_json_schema())
    assert set(schema["properties"]) == {"title", "description", "data"}
    assert schema["required"] == ["title", "description", "data"]


def test_top_level_metadata_is_stripped() -> None:
    """模型级 title 与顶层 description 仍按原约定剥离。"""
    schema = strip_schema_metadata(
        {
            "title": "Input",
            "description": "模型说明",
            "type": "object",
            "properties": {"title": {"title": "Title", "type": "string"}},
        }
    )
    assert "title" not in schema
    assert "description" not in schema
    assert schema["properties"]["title"] == {"type": "string"}
