# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""读接口形状兼容：同一份包内容，v1 两段式与 v2 成员列表必须给出相同响应。

ability.yaml 的 wire 形状升到 v2（成员记录列表）后，读接口（目录 / 详情 / 版本画像 /
包组）一律只消费解析后的运行时定义。本测试用同一份包内容分别落 v1 与 v2 两种
``ability.yaml``，分别安装到两个安装根，然后逐字段比较四个读接口的响应。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ghrah.ability.loading.ability_schema import AbilityDefinition, parse_definition
from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import AbilityInstaller
from ghrah.core.read.ability_catalog import (
    build_package_catalog,
    build_package_detail,
    build_package_groups,
    build_version_profile,
)

OP_SOURCE = """\
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

TITLE = "Render"
SUMMARY = "渲染一份报表。"
TAGS = ["report"]
PERMISSIONS = {"fs_read_only": True}
EFFECTS = ["fs:read"]
TIMEOUT_MS = 5000


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool


async def execute(call: Any, services: Any) -> None:
    del call, services
"""

SKILL_BODY = """\
---
name: guide
description: 写作指南。
metadata:
  title: 指南
  tags: [docs]
---
正文。
"""

HEADER = """\
format: ghrah.ability-definition
schema_version: '1'
identity:
  namespace: fixtures
  name: shapes
  version: 0.1.0
title: Shapes
description: 读接口形状兼容夹具
members: {operations: {}, skills: {}}
"""


def _source(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / "operations").mkdir(parents=True)
    (root / "skills" / "guide").mkdir(parents=True)
    (root / "operations" / "render.py").write_text(OP_SOURCE, encoding="utf-8", newline="\n")
    (root / "skills" / "guide" / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8", newline="\n")
    (root / "ability.yaml").write_text(HEADER, encoding="utf-8", newline="\n")
    return root


def _v1_document(definition: AbilityDefinition) -> str:
    """把运行时定义序列化回 v1 两段式（仅供兼容测试构造对照样本）。"""
    identity: dict[str, Any] = {
        "namespace": definition.identity.namespace,
        "version": definition.identity.version,
    }
    if definition.identity.name:
        identity["name"] = definition.identity.name
    operations = {
        key: {
            "title": item.title,
            "description": item.description,
            "entry": item.entry.path if item.entry else None,
            "input_schema": item.input_schema,
            "output_schema": item.output_schema,
            "effects": list(item.effects),
            "tags": list(item.tags),
            "permissions": dict(item.permissions),
            "resources": dict(item.resources),
            "execution": item.execution.model_dump(mode="json"),
            "expose": item.expose,
        }
        for key, item in definition.operations.items()
    }
    skills = {
        key: {
            "title": item.title,
            "description": item.description,
            "tags": list(item.tags),
            "body": {"path": item.body.path, "media_type": item.body.media_type},
            "assets": {
                k: v.model_dump(mode="json", exclude_none=True) for k, v in item.assets.items()
            },
            "references": {
                k: v.model_dump(mode="json", exclude_none=True) for k, v in item.references.items()
            },
            "scripts": {
                k: v.model_dump(mode="json", exclude_none=True) for k, v in item.scripts.items()
            },
        }
        for key, item in definition.skills.items()
    }
    document = {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": identity,
        "title": definition.title,
        "description": definition.description,
        "members": {"operations": operations, "skills": skills},
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _install(tmp_path: Path, name: str, *, wire_version: str) -> Path:
    """落一个包并装进独立安装根；``wire_version`` 决定 ability.yaml 的形状。"""
    root = _source(tmp_path / wire_version, name)
    build(root)  # 生成 v2 成员列表
    if wire_version == "v1":
        definition = parse_definition(
            (root / "ability.yaml").read_text(encoding="utf-8"), source=str(root)
        )
        (root / "ability.yaml").write_text(_v1_document(definition), encoding="utf-8", newline="\n")
    install_root = tmp_path / f"install-{wire_version}"
    AbilityInstaller(install_root).install(root)
    return install_root


def test_parse_is_identical_for_both_wire_versions(tmp_path: Path) -> None:
    v2_source = _source(tmp_path / "v2", "shapes")
    build(v2_source)
    v2 = parse_definition((v2_source / "ability.yaml").read_text(encoding="utf-8"), source="v2")
    v1 = parse_definition(_v1_document(v2), source="v1")
    # 除 wire 版本号外，运行时定义必须逐字节一致。
    assert v1.model_dump(mode="json") | {"schema_version": "1"} == v2.model_dump(mode="json") | {
        "schema_version": "1"
    }


def _without_tree_digests(value: Any) -> Any:
    """去掉整树摘要再比较。

    ``package_digest`` 覆盖包目录里的普通文件，所以 v1 / v2 两种 ``ability.yaml``
    字节不同必然给出不同摘要——那是**内容身份**差异，不是读接口的形状差异。
    """
    if isinstance(value, dict):
        return {
            key: _without_tree_digests(item)
            for key, item in value.items()
            if key not in {"package_digest", "actual_digest"}
        }
    if isinstance(value, list):
        return [_without_tree_digests(item) for item in value]
    return value


async def test_read_endpoints_do_not_depend_on_wire_version(tmp_path: Path) -> None:
    v1_root = _install(tmp_path, "shapes", wire_version="v1")
    v2_root = _install(tmp_path, "shapes", wire_version="v2")

    for builder, args in (
        (build_package_catalog, ()),
        (build_package_detail, ("fixtures", "shapes")),
        (build_version_profile, ("fixtures", "shapes", "0.1.0")),
    ):
        left = _without_tree_digests(builder(str(v1_root), *args).model_dump(mode="json"))
        right = _without_tree_digests(builder(str(v2_root), *args).model_dump(mode="json"))
        assert left == right, f"{builder.__name__} 的响应随 wire 版本变化"

    groups_left = _without_tree_digests(
        (await build_package_groups(str(v1_root))).model_dump(mode="json")
    )
    groups_right = _without_tree_digests(
        (await build_package_groups(str(v2_root))).model_dump(mode="json")
    )
    assert groups_left == groups_right, "build_package_groups 的响应随 wire 版本变化"

    profile = build_version_profile(str(v2_root), "fixtures", "shapes", "0.1.0")
    entry = profile.operations[0].entry
    assert entry is not None
    # 读接口只回包内相对路径，不泄漏安装根或版本段。
    assert entry.path == "operations/render.py"
    assert profile.skills[0].body.path == "skills/guide/SKILL.md"
