# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""上传包入库规范化：先扫盘提取成员，再按标准格式补写 ``ability.yaml``。

覆盖「允许不完整、但不允许装不进去」的边界：缺 ``ability.yaml``、声明与磁盘
不一致都能修；既没有 ``operations/`` 也没有 ``skills/`` 才算不是能力包。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghrah.ability.package.intake import IntakeError, normalize_source

OP_SOURCE = '''\
"""最小可授予 operation（故意不写 TITLE / SUMMARY）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ghrah.types.results import ActionOutcome, ActionResult


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(description="探活目标")


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="是否可达")


async def execute(call: Any, services: Any) -> ActionResult:
    del call, services
    return ActionResult(outcome=ActionOutcome.SUCCESS, data=Output(ok=True).model_dump())
'''

SKILL_BODY = """\
---
name: guide
description: 写作指南。
---
正文。
"""

HEADER = """\
format: ghrah.ability-definition
schema_version: '1'
identity:
  namespace: acme
  name: tools
  version: 2.0.0
title: Tools
description: 手写包
members: {operations: {}, skills: {}}
"""


def _package(root: Path, *, operations: int = 1, skill: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if operations:
        (root / "operations").mkdir(exist_ok=True)
        for index in range(operations):
            (root / "operations" / f"op_{index}.py").write_text(
                OP_SOURCE, encoding="utf-8", newline="\n"
            )
    if skill:
        body = root / "skills" / "guide"
        body.mkdir(parents=True, exist_ok=True)
        (body / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8", newline="\n")
    return root


def _document(root: Path) -> dict:
    return yaml.safe_load((root / "ability.yaml").read_text(encoding="utf-8"))


def test_missing_definition_is_built_from_directory_name(tmp_path: Path) -> None:
    root = _package(tmp_path / "acme.tools")
    result = normalize_source(root)

    assert result.definition.identity.namespace == "acme"
    assert result.definition.identity.name == "tools"
    assert result.definition.identity.version == "0.1.0"
    assert [member.member for member in result.definition.to_members()] == ["acme.tools.op_0"]
    document = _document(root)
    assert document["schema_version"] == "2"
    assert document["members"][0]["member"] == "acme.tools.op_0"


def test_single_segment_directory_is_a_degenerate_package(tmp_path: Path) -> None:
    root = _package(tmp_path / "reporting")
    result = normalize_source(root)
    assert result.definition.identity.namespace == "reporting"
    assert result.definition.identity.name is None
    assert [member.member for member in result.definition.to_members()] == ["reporting.op_0"]


def test_skill_only_package_is_accepted(tmp_path: Path) -> None:
    root = _package(tmp_path / "acme.notes", operations=0, skill=True)
    result = normalize_source(root)
    assert [member.member for member in result.definition.to_members()] == ["acme.notes.guide"]


def test_existing_identity_is_kept(tmp_path: Path) -> None:
    root = _package(tmp_path / "whatever")
    (root / "ability.yaml").write_text(HEADER, encoding="utf-8", newline="\n")
    result = normalize_source(root)

    assert result.definition.identity.namespace == "acme"
    assert result.definition.identity.name == "tools"
    assert result.definition.identity.version == "2.0.0"
    assert result.definition.title == "Tools"
    assert result.changed is True


def test_stale_members_are_replaced_by_the_disk_scan(tmp_path: Path) -> None:
    root = _package(tmp_path / "acme.tools", operations=2)
    (root / "ability.yaml").write_text(HEADER, encoding="utf-8", newline="\n")
    result = normalize_source(root)
    assert [member.member for member in result.definition.to_members()] == [
        "acme.tools.op_0",
        "acme.tools.op_1",
    ]


def test_empty_package_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "acme.empty"
    root.mkdir()
    with pytest.raises(IntakeError, match="不是能力包") as failure:
        normalize_source(root)
    assert failure.value.code == "not_a_package"


MCP_DECLARATION = """\
format: ghrah.ability-definition
schema_version: '2'
origin: mcp
identity:
  namespace: mcp
  name: notion
  version: 1.0.0
title: Notion
description: Notion MCP
mcp:
  server: https://mcp.example.com/notion
members: []
"""


def test_mcp_declaration_is_not_treated_as_a_non_package(tmp_path: Path) -> None:
    """MCP 声明只有 ability.yaml：成员来自远端，不能按「没有成员」拒绝。"""
    root = tmp_path / "mcp.notion"
    root.mkdir()
    path = root / "ability.yaml"
    path.write_text(MCP_DECLARATION, encoding="utf-8", newline="\n")

    result = normalize_source(root)

    assert result.is_mcp is True
    assert result.definition is None
    assert result.changed is False
    # 声明原样保留：扫盘规范化不参与 MCP。
    assert path.read_text(encoding="utf-8") == MCP_DECLARATION


def test_mcp_snapshot_with_members_is_also_passed_through(tmp_path: Path) -> None:
    """已带快照成员的 MCP 包同样交给安装器现场刷新。"""
    root = tmp_path / "mcp.notion"
    root.mkdir()
    snapshot = MCP_DECLARATION.replace(
        "members: []",
        "members:\n"
        "- namespace: mcp\n"
        "  name: notion\n"
        "  member: mcp.notion.search\n"
        "  type: mcp\n"
        "  version: 1.0.0\n"
        "  ability_type: external\n"
        "  axes: {visible: true, bind: true}\n"
        "  data:\n"
        "    title: Search\n"
        "    description: 搜索\n"
        "    server: https://mcp.example.com/notion\n"
        "    tool: search\n"
        "    input_schema: {type: object, properties: {}, additionalProperties: false}\n"
        "    output_schema: {type: object, properties: {}, additionalProperties: false}\n"
        "    execution: {mode: request, timeout_ms: 60000, cancellation: unsupported,"
        " on_revoke: drain, retry: never}\n",
    )
    (root / "ability.yaml").write_text(snapshot, encoding="utf-8", newline="\n")

    result = normalize_source(root)

    assert result.is_mcp is True
    assert (root / "ability.yaml").read_text(encoding="utf-8") == snapshot


def test_crlf_in_non_member_file_does_not_block_import(tmp_path: Path) -> None:
    """包里的杂物（CREDITS.md）用 CRLF 不该拒绝整个包；入库时统一成 LF。"""
    root = _package(tmp_path / "acme.tools")
    credits = root / "CREDITS.md"
    credits.write_bytes(b"# Credits\r\n\r\n- someone\r\n")

    result = normalize_source(root)

    assert [member.member for member in result.definition.to_members()] == ["acme.tools.op_0"]
    assert credits.read_bytes() == b"# Credits\n\n- someone\n"
    assert any("LF" in item for item in result.warnings)


def test_stray_files_inside_member_directories_are_ignored(tmp_path: Path) -> None:
    """operations/ 下的说明文件、skills/<name>/ 下的杂物只跳过，不阻断安装。"""
    root = _package(tmp_path / "acme.tools", skill=True)
    (root / "operations" / "notes.txt").write_text("说明\n", encoding="utf-8", newline="\n")
    (root / "skills" / "guide" / "CREDITS.md").write_text(
        "来源\n", encoding="utf-8", newline="\n"
    )

    result = normalize_source(root)

    assert [member.member for member in result.definition.to_members()] == [
        "acme.tools.op_0",
        "acme.tools.guide",
    ]


def test_second_normalisation_is_a_no_op(tmp_path: Path) -> None:
    root = _package(tmp_path / "acme.tools")
    normalize_source(root)
    again = normalize_source(root)
    assert again.changed is False
