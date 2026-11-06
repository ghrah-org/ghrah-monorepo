# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""多技能（``members.skills``）与 v1 两段式定义的解析、扫描与捕获。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ghrah.ability.loading import ability_roots as roots
from ghrah.ability.loading.ability_scan import scan_ability_definitions
from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.loading.manifest_fields import ManifestValidationError
from ghrah.ability.loading.skill_context import load_skill_catalog
from ghrah.ability.versions import capture_ability_version

V1_YAML = """\
format: ghrah.ability-definition
schema_version: '1'
identity:
  namespace: acme
  name: demo
  version: '1.0.0'
title: Demo
description: 多技能包。
members:
  operations:
    ping:
      title: Ping
      description: 探活。
      entry: operations/ping.py
      input_schema:
        type: object
        properties: {host: {type: string}}
        required: [host]
        additionalProperties: false
      output_schema:
        type: object
        properties: {ok: {type: boolean}}
        required: [ok]
        additionalProperties: false
      tags: [probe]
      effects: [net:request]
      permissions: {net_access: true}
      execution: {mode: request, timeout_ms: 5000, cancellation: unsupported,
                  on_revoke: drain, retry: never}
  skills:
    alpha:
      title: Alpha
      description: 第一个技能。
      tags: [a]
      body: {path: skills/alpha/SKILL.md, media_type: text/markdown}
    beta:
      title: Beta
      description: 第二个技能。
      tags: [b]
      body: {path: skills/beta/SKILL.md, media_type: text/markdown}
      references:
        guide: {path: skills/beta/references/guide.md, media_type: text/markdown}
      scripts:
        run: {path: skills/beta/scripts/run.py, media_type: text/x-python}
"""


def _package(tmp_path: Path) -> Path:
    """落一个含 1 个 operation、2 个 skill 的 v1 包。"""
    root = tmp_path / "demo"
    (root / "operations").mkdir(parents=True)
    (root / "operations" / "ping.py").write_text("print(1)\n", encoding="utf-8")
    for key in ("alpha", "beta"):
        body = root / "skills" / key / "SKILL.md"
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_text(
            f"---\nname: {key}\ndescription: {key} 技能。\n---\n\n# {key}\n",
            encoding="utf-8",
            newline="\n",
        )
    (root / "skills" / "beta" / "references").mkdir(parents=True)
    (root / "skills" / "beta" / "references" / "guide.md").write_text(
        "# guide\n", encoding="utf-8", newline="\n"
    )
    (root / "skills" / "beta" / "scripts").mkdir(parents=True)
    (root / "skills" / "beta" / "scripts" / "run.py").write_text(
        "print('run')\n", encoding="utf-8", newline="\n"
    )
    (root / "ability.yaml").write_text(V1_YAML, encoding="utf-8", newline="\n")
    return root


def test_v1_definition_parses_members(tmp_path: Path) -> None:
    """v1 ``members`` 展开成运行时成员表，字段一个不丢。"""
    definition = parse_definition(V1_YAML, source="demo")

    assert definition.schema_version == "1"
    assert definition.container_name == "demo"
    operation = definition.operations["ping"]
    assert operation.tags == ["probe"]
    assert operation.permissions == {"net_access": True}
    assert operation.effects == ["net:request"]
    assert operation.input_schema["required"] == ["host"]
    assert operation.execution.timeout_ms == 5000
    assert operation.entry is not None and operation.entry.path == "operations/ping.py"
    assert set(definition.skills) == {"alpha", "beta"}


def test_v1_defaults_fill_missing_execution_and_schema() -> None:
    """缺 ``execution`` / schema 时补可执行的缺省值，而不是拒绝整包。"""
    raw = V1_YAML.replace(
        "      execution: {mode: request, timeout_ms: 5000, cancellation: unsupported,\n"
        "                  on_revoke: drain, retry: never}\n",
        "",
    ).replace(
        "      input_schema:\n"
        "        type: object\n"
        "        properties: {host: {type: string}}\n"
        "        required: [host]\n"
        "        additionalProperties: false\n",
        "",
    )

    definition = parse_definition(raw, source="demo")

    operation = definition.operations["ping"]
    assert operation.execution.timeout_ms == 60_000
    assert operation.input_schema["type"] == "object"
    assert operation.input_schema["additionalProperties"] is False


def test_scan_lists_both_skills_under_the_package(tmp_path: Path) -> None:
    """目录面按包分层：一个包下两个技能各自带正文、标签与附件。"""
    root = _package(tmp_path)

    result = scan_ability_definitions((roots.path_root(tmp_path),))

    (container,) = result.containers
    assert container.name == "demo"
    assert [skill.key for skill in container.skills] == ["alpha", "beta"]
    assert [skill.catalog_name for skill in container.skills] == ["demo/alpha", "demo/beta"]
    beta = container.skills[1]
    assert beta.body_path == "skills/beta/SKILL.md"
    assert [item.path for item in beta.references] == ["skills/beta/references/guide.md"]
    assert [item.path for item in beta.scripts] == ["skills/beta/scripts/run.py"]
    assert beta.media_type == "text/markdown"
    assert container.operations[0].definition.permissions == {"net_access": True}
    assert root.is_dir()


def test_catalog_qualifies_multi_skill_names(tmp_path: Path) -> None:
    """技能目录用 ``包/技能`` 命名，且能按该名字读回正文。"""
    _package(tmp_path)

    catalog = load_skill_catalog((roots.path_root(tmp_path),))

    assert catalog.names == ("demo/alpha", "demo/beta")
    document = catalog.read("demo/beta")
    assert "# beta" in document.content


def test_capture_includes_every_skill_and_attachment(tmp_path: Path) -> None:
    """内容捕获覆盖全部技能；Operation 源码不入内容清单。"""
    root = _package(tmp_path)

    version = capture_ability_version(
        (root / "ability.yaml").read_text(encoding="utf-8"), root=root
    )
    members = set(json.loads(version.manifest_json))

    # Operation 源码不是内容成员。版本身份另行覆盖整棵目录，不写进这份清单。
    assert {"skill/alpha", "skill/beta"} <= members
    assert "operations/ping/script" not in members
    assert "skill/beta/references/guide" in members
    assert "skill/beta/scripts/run" in members


def test_single_skill_packages_keep_bare_catalog_name(tmp_path: Path) -> None:
    """单技能包沿用裸键命名，不制造路径前缀。"""
    root = _package(tmp_path)
    data = yaml.safe_load((root / "ability.yaml").read_text(encoding="utf-8"))
    data["members"]["skills"] = {"alpha": data["members"]["skills"]["alpha"]}
    (root / "ability.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8", newline="\n"
    )

    catalog = load_skill_catalog((roots.path_root(tmp_path),))

    assert catalog.names == ("alpha",)


def test_unknown_member_field_is_rejected() -> None:
    """v1 成员表同样拒绝未知字段（不做隐式吸收）。"""
    raw = V1_YAML.replace("      tags: [a]\n", "      tags: [a]\n      mystery: true\n")

    with pytest.raises(ManifestValidationError, match="mystery"):
        parse_definition(raw, source="demo")


def test_v1_operation_errors_field_is_rejected() -> None:
    """``errors`` 不进 v1，声明即拒绝（解析端缺省 ``{}``）。"""
    raw = V1_YAML.replace(
        "      tags: [probe]\n", "      tags: [probe]\n      errors: {timeout: {code: 1}}\n"
    )

    with pytest.raises(ManifestValidationError, match="errors"):
        parse_definition(raw, source="demo")


def test_v1_skills_list_shorthand_is_rejected() -> None:
    """``members.skills`` 只接受映射；列表简写（每项自带 name）是旁路，收口拒绝。"""
    data = yaml.safe_load(V1_YAML)
    data["members"]["skills"] = [
        {"name": "alpha", **data["members"]["skills"]["alpha"]},
    ]
    raw = yaml.safe_dump(data, allow_unicode=True)

    with pytest.raises(ManifestValidationError, match="members.skills 必须是映射"):
        parse_definition(raw, source="demo")


def test_v1_identity_version_must_be_semver_core() -> None:
    """v1 ``identity.version`` 必须是 SemVer core 三段；前缀/预发布/元数据都拒绝。"""
    for bad in ("v1.0.0", "1.0.0-rc.1", "1.0.0+build", "1.0", "01.0.0", "1.0.0.0"):
        raw = V1_YAML.replace("  version: '1.0.0'", f"  version: '{bad}'")
        with pytest.raises(ManifestValidationError, match="SemVer"):
            parse_definition(raw, source="demo")


def test_v1_identity_version_semver_edge_lengths_pass() -> None:
    """每段 9 位、含零段（``0.0.0``）与九位数都合法；边界不是隐式拒绝。"""
    for good in ("0.0.0", "0.1.1", "123456789.0.999999999"):
        raw = V1_YAML.replace("  version: '1.0.0'", f"  version: '{good}'")
        definition = parse_definition(raw, source="demo")
        assert definition.identity.version == good


async def test_grant_view_carries_every_skill(tmp_path: Path) -> None:
    """授权视图给出完整 skills 清单；单技能快捷字段只是第一个。"""
    from ghrah.protocol.ability_tree import GrantAbilityRequest, SkillReadScope

    from ghrah.ability.access import AbilityAccessStore, AccessIdentity

    root = _package(tmp_path)
    version = capture_ability_version(
        (root / "ability.yaml").read_text(encoding="utf-8"), root=root
    )
    admin = AccessIdentity("project", "admin", "control", True)
    store = await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"admin"}),
        runs=lambda agent: "alice-run" if agent == "alice" else None,
    )
    try:
        await store.commit_version(admin, version)
        await store.select_instance(admin, "demo", version.digest, root)
        await store.register_agent(admin, "alice")
        view = await store.grant(
            admin,
            "alice",
            GrantAbilityRequest(
                alias="demos",
                instance_id="demo",
                skills=[SkillReadScope(key="alpha"), SkillReadScope(key="beta")],
                expected_revision=0,
            ),
        )
        node = view.abilities[0]
        assert [skill.key for skill in node.skills] == ["alpha", "beta"]
        assert node.skill is not None and node.skill.key == "alpha"
        assert node.skills[1].references[0].member == "skill/beta/references/guide"
    finally:
        await store.close()
