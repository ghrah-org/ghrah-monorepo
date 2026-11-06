# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""三种包形态（仅 operation / 单 skill / 多 skill）都能正常加载。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from ghrah.ability.loading import ability_roots as roots
from ghrah.ability.loading.ability_scan import scan_abilities
from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.loading.skill_context import load_skill_catalog
from ghrah.ability.versions import capture_ability_version

OP_ONLY = "operations-only"
SINGLE = "single-skill"
MULTI = "multi-skills"
SHAPES = (OP_ONLY, SINGLE, MULTI)


def _skill_member(key: str, *, attachment: bool = False) -> dict[str, Any]:
    """一个 v1 技能成员声明；可带一份参考材料。"""
    member: dict[str, Any] = {
        "title": key.replace("-", " ").title(),
        "description": f"{key} 说明书。",
        "tags": [key, "guide"],
        "body": {"path": f"skills/{key}/SKILL.md", "media_type": "text/markdown"},
        "assets": {},
        "references": {},
        "scripts": {},
    }
    if attachment:
        member["references"] = {
            "notes": {
                "path": f"skills/{key}/references/notes.md",
                "media_type": "text/markdown",
            }
        }
    return member


def _package(tmp_path: Path, shape: str, name: str = "demo") -> Path:
    """按形态落一个 v1 包（容器名 = 目录名），返回包目录。"""
    package = tmp_path / name
    package.mkdir()

    members: dict[str, Any] = {"operations": {}, "skills": {}}
    if shape == OP_ONLY:
        (package / "operations").mkdir()
        (package / "operations" / "ping.py").write_text(
            "print('ping')\n", encoding="utf-8", newline="\n"
        )
        members["operations"]["ping"] = {
            "title": "Ping",
            "description": "探活。",
            "entry": "operations/ping.py",
            "input_schema": {
                "type": "object",
                "properties": {"host": {"type": "string"}},
                "required": ["host"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            "tags": ["probe"],
            "effects": ["net:request"],
            "permissions": {"net_access": True},
            "execution": {
                "mode": "request",
                "timeout_ms": 5000,
                "cancellation": "unsupported",
                "on_revoke": "drain",
                "retry": "never",
            },
        }
    else:
        keys = ("alpha",) if shape == SINGLE else ("alpha", "beta")
        for key in keys:
            body = package / "skills" / key / "SKILL.md"
            body.parent.mkdir(parents=True, exist_ok=True)
            body.write_text(
                f"---\nname: {key}\ndescription: {key} 说明书。\n---\n\n# {key}\n",
                encoding="utf-8",
                newline="\n",
            )
            members["skills"][key] = _skill_member(key, attachment=key == "beta")
        notes = package / "skills" / "beta" / "references" / "notes.md"
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text("# notes\n", encoding="utf-8", newline="\n")

    definition = {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": {"namespace": "acme", "name": name, "version": "1.0.0"},
        "title": "Demo",
        "description": f"形态：{shape}",
        "members": members,
    }
    (package / "ability.yaml").write_text(
        yaml.safe_dump(definition, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    return package


@pytest.mark.parametrize("shape", SHAPES)
def test_definition_parses_for_every_shape(tmp_path: Path, shape: str) -> None:
    """三种形态都能被 v1 解析器接受，成员表与形态一致。"""
    package = _package(tmp_path, shape)

    definition = parse_definition(
        (package / "ability.yaml").read_text(encoding="utf-8"), source=shape
    )

    assert definition.container_name == "demo"
    assert definition.schema_version == "1"
    assert len(definition.operations) == (1 if shape == OP_ONLY else 0)
    assert len(definition.skills) == {OP_ONLY: 0, SINGLE: 1, MULTI: 2}[shape]


@pytest.mark.parametrize("shape", SHAPES)
def test_scan_loads_every_shape(tmp_path: Path, shape: str) -> None:
    """目录面把三种形态都加载成健康包，成员带完整归属。"""
    _package(tmp_path, shape)

    (container,) = scan_abilities((roots.path_root(tmp_path),))

    assert container.name == "demo"
    assert container.issues == ()
    if shape == OP_ONLY:
        assert container.kinds == ("operations",)
        assert container.skill_count == 0
        (operation,) = container.operations
        assert operation.container == "demo"
        assert operation.full_name == "acme.demo.ping"
        assert operation.handler == "ping"
        assert operation.effects == ("net:request",)
        assert operation.definition.permissions == {"net_access": True}
        assert operation.definition.input_schema["required"] == ["host"]
    else:
        assert container.kinds == ("skills",)
        assert container.operation_count == 0
        assert [skill.key for skill in container.skills] == (
            ["alpha"] if shape == SINGLE else ["alpha", "beta"]
        )
        alpha = container.skills[0]
        assert alpha.container == "demo"
        assert alpha.full_name == "acme.demo.alpha"
        assert alpha.body_path == "skills/alpha/SKILL.md"
        assert alpha.tags == ("alpha", "guide")
        assert alpha.body is not None and alpha.body.is_file()
        assert alpha.drifted is False
    if shape == SINGLE:
        # 单技能包沿用裸键命名。
        assert container.skills[0].catalog_name == "alpha"
    if shape == MULTI:
        # 多技能包必须带归属，且附件挂在各自技能下。
        assert [skill.catalog_name for skill in container.skills] == ["demo/alpha", "demo/beta"]
        assert [item.path for item in container.skills[1].references] == [
            "skills/beta/references/notes.md"
        ]


@pytest.mark.parametrize("shape", SHAPES)
def test_skill_catalog_reflects_shape(tmp_path: Path, shape: str) -> None:
    """技能目录只列出技能；正文可按目录名读回。"""
    _package(tmp_path, shape)

    catalog = load_skill_catalog((roots.path_root(tmp_path),))

    if shape == OP_ONLY:
        assert catalog.names == ()
        assert "No skills are currently available" in catalog.render()
        return
    names = ("alpha",) if shape == SINGLE else ("demo/alpha", "demo/beta")
    assert catalog.names == names
    for name in names:
        document = catalog.read(name)
        assert document.container == "demo"
        assert document.content.startswith("---\nname: ")
    if shape == SINGLE:
        # 单技能包的包名可唯一解析到该技能。
        assert catalog.get("demo") is not None
    else:
        # 多技能包里包名二义，目录面返回 None 而不是随便挑一个。
        assert catalog.get("demo") is None


@pytest.mark.parametrize("shape", SHAPES)
def test_content_capture_reflects_shape(tmp_path: Path, shape: str) -> None:
    """内容版本恰好包含该形态声明过的成员。"""
    package = _package(tmp_path, shape)

    version = capture_ability_version(
        (package / "ability.yaml").read_text(encoding="utf-8"), root=package
    )
    members = set(json.loads(version.manifest_json))

    if shape == OP_ONLY:
        assert members == set()
    elif shape == SINGLE:
        assert members == {"skill/alpha"}
    else:
        assert members == {
            "skill/alpha",
            "skill/beta",
            "skill/beta/references/notes",
        }
