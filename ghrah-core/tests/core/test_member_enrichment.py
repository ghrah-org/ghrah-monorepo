# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""创建线：引用态成员按来源补齐成定义态成员（data 进快照）。"""

from __future__ import annotations

import pytest

from ghrah.ability.member import Member
from ghrah.core.agents.assembly import AssemblyError
from ghrah.core.agents.members import resolve_member_entities

_EXTERNAL_DEFINITION = {
    "format": "ghrah.ability-definition",
    "schema_version": "2",
    "identity": {"namespace": "acme", "name": "tools", "version": "1.2.3"},
    "title": "Tools",
    "description": "fixture",
    "members": [
        {
            "namespace": "acme",
            "name": "tools",
            "member": "acme.tools.render",
            "type": "operation",
            "version": "1.2.3",
            "ability_type": "external",
            "data": {
                "title": "Render",
                "description": "render it",
                "path": "acme/tools/1.2.3/operations/render.py",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "execution": {
                    "mode": "request",
                    "timeout_ms": 1000,
                    "cancellation": "unsupported",
                    "on_revoke": "drain",
                    "retry": "never",
                },
            },
        },
        {
            "namespace": "acme",
            "name": "tools",
            "member": "acme.tools.guide",
            "type": "skill",
            "version": "1.2.3",
            "ability_type": "external",
            "data": {
                "title": "Guide",
                "description": "usage guide",
                "path": "acme/tools/1.2.3/skills/guide/SKILL.md",
                "media_type": "text/markdown",
            },
        },
    ],
}


def _item(member: str, *, type_: str, version: str | None, ability_type: str) -> dict:
    return {
        "member": member,
        "type": type_,
        "version": version,
        "ability_type": ability_type,
        "axes": {"visible": True, "available": False},
    }


def test_kernel_member_gets_data_from_builtin_tree() -> None:
    members = resolve_member_entities(
        [
            _item(
                "ghrah.fs.read_file",
                type_="operation",
                version=None,
                ability_type="kernel",
            )
        ],
        install_root=None,
    )

    assert len(members) == 1
    member = members[0]
    assert member.is_definition
    assert member.data["path"] == "ghrah/fs/operations/read_file.py"
    assert member.data["input_schema"]["properties"]["file_path"]
    # 轴以引用为准：visible 恒 True，bind 来自规格。
    assert (member.axes.visible, member.axes.bind) == (True, False)


def test_kernel_skill_keeps_media_type_and_path() -> None:
    members = resolve_member_entities(
        [
            _item(
                "ghrah.ability-builder.ability-package-development-guide",
                type_="skill",
                version=None,
                ability_type="kernel",
            )
        ],
        install_root=None,
    )

    member = members[0]
    assert member.type == "skill"
    assert member.data["media_type"] == "text/markdown"
    assert member.data["path"].endswith("skills/ability-package-development-guide/SKILL.md")


def test_external_member_is_read_from_the_requested_version(monkeypatch) -> None:
    seen: list[tuple[str | None, str, str]] = []

    def fake_read(install_root: str | None, full_name: str, version: str) -> dict:
        seen.append((install_root, full_name, version))
        return {"definition": _EXTERNAL_DEFINITION}

    monkeypatch.setattr("ghrah.core.agents.members.read_installed_version", fake_read)
    members = resolve_member_entities(
        [
            _item(
                "acme.tools.guide",
                type_="skill",
                version="1.2.3",
                ability_type="external",
            )
        ],
        install_root="/tmp/ability",
    )

    assert seen == [("/tmp/ability", "acme.tools", "1.2.3")]
    assert members[0].data["path"] == "acme/tools/1.2.3/skills/guide/SKILL.md"


def test_external_member_without_version_fails_closed() -> None:
    with pytest.raises(AssemblyError, match="版本"):
        resolve_member_entities(
            [_item("acme.tools.render", type_="operation", version=None, ability_type="external")],
            install_root="/tmp/ability",
        )


def test_type_mismatch_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "ghrah.core.agents.members.read_installed_version",
        lambda *args: {"definition": _EXTERNAL_DEFINITION},
    )
    with pytest.raises(AssemblyError, match="type mismatch"):
        resolve_member_entities(
            [
                _item(
                    "acme.tools.guide",
                    type_="operation",
                    version="1.2.3",
                    ability_type="external",
                )
            ],
            install_root="/tmp/ability",
        )


def test_snapshot_wire_shape_carries_data() -> None:
    members = resolve_member_entities(
        [_item("ghrah.fs.read_file", type_="operation", version=None, ability_type="kernel")],
        install_root=None,
    )

    wire = Member.to_wire(members[0])
    assert set(wire) == {
        "namespace",
        "name",
        "member",
        "type",
        "version",
        "ability_type",
        "axes",
        "data",
    }
    assert wire["member"] == "ghrah.fs.read_file"
    assert wire["data"]["path"].endswith("operations/read_file.py")
