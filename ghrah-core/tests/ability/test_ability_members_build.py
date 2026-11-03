# SPDX-License-Identifier: Apache-2.0
"""``members`` 生成区的扫描写回与 v1 定义解析（不执行包内代码）。"""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from ghrah.ability.build import GENERATED_MARKER, scan_members, write_members
from ghrah.ability.loading.ability_roots import path_root
from ghrah.ability.loading.ability_scan import scan_ability_definitions
from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.loading.manifest_fields import ManifestValidationError
from ghrah.ability.loading.skill_context import load_skill_catalog

AUTHORED = """\
# 作者手写，工具不改
format: ghrah.ability-definition
schema_version: '1'
identity: {namespace: acme, name: reporting, version: '1.4.0'}
title: Reporting
description: 生成周报与月报，按模板渲染。
"""

INSPECT = 'SUMMARY = "检查数据源是否可用"\n'
RENDER = 'SUMMARY = "按模板渲染报告"\nEFFECTS = ["filesystem:write"]\n'
SKILL_BODY = """\
---
name: reporting-guide
description: 使用获准模板和写作指南整理报告。
---

# 报告编写
"""


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "reporting"
    (root / "operations").mkdir(parents=True)
    (root / "skill" / "references").mkdir(parents=True)
    (root / "skill" / "assets").mkdir()
    (root / "ability.yaml").write_text(AUTHORED, encoding="utf-8")
    (root / "operations" / "__init__.py").write_text("", encoding="utf-8")
    (root / "operations" / "inspect.py").write_text(INSPECT, encoding="utf-8")
    (root / "operations" / "render.py").write_text(RENDER, encoding="utf-8")
    (root / "skill" / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    (root / "skill" / "references" / "style-guide.md").write_text("# 风格\n", encoding="utf-8")
    (root / "skill" / "assets" / "template.bin").write_bytes(b"\x00\x01")
    return root


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_scan_members_indexes_directory_conventions(tmp_path):
    root = _package(tmp_path)
    members = scan_members(root)
    assert set(members["operations"]) == {"inspect", "render"}
    assert members["operations"]["render"] == {
        "title": "Render",
        "description": "按模板渲染报告",
        "entry": "operations/render.py",
        "effects": ["filesystem:write"],
    }
    assert members["skill"] == {
        "body": "skill/SKILL.md",
        "media_type": "text/markdown",
    }
    assert [item["path"] for item in members["references"]] == ["skill/references/style-guide.md"]
    assert members["references"][0]["digest"] == _sha256(
        root / "skill" / "references" / "style-guide.md"
    )
    assert [item["path"] for item in members["assets"]] == ["skill/assets/template.bin"]


def test_build_writes_generated_block_and_keeps_authored_part(tmp_path):
    root = _package(tmp_path)
    result = write_members(root)

    text = result.path.read_text(encoding="utf-8")
    assert result.changed
    assert text.startswith(AUTHORED)
    assert GENERATED_MARKER in text
    definition = parse_definition(text, source="built")
    assert definition.schema_version == "1"
    assert definition.identity.full_name == "acme.reporting"

    render = definition.operations["render"]
    assert render.title == "Render"
    assert render.effects == ["filesystem:write"]
    assert render.implementation.provider_slot == "script"
    assert render.script is not None
    assert render.script.path == "operations/render.py"
    assert render.script.media_type == "text/x-python"
    assert definition.provider_requirements["script"].contract == "ghrah.script.v1"

    ((skill_key, skill),) = definition.skills.items()
    assert skill_key == "reporting"
    # 名字与说明不进索引：作者是正文 frontmatter，由扫描 / 捕获读取。
    assert skill.title == ""
    assert skill.description == ""
    assert skill.body.path == "skill/SKILL.md"
    assert skill.references["style-guide"].expected_digest == _sha256(
        root / "skill" / "references" / "style-guide.md"
    )
    assert skill.assets["template"].path == "skill/assets/template.bin"


def test_build_is_idempotent(tmp_path):
    root = _package(tmp_path)
    write_members(root)
    first = (root / "ability.yaml").read_text(encoding="utf-8")

    second = write_members(root)
    assert not second.changed
    assert (root / "ability.yaml").read_text(encoding="utf-8") == first


def test_dry_run_reports_change_without_writing(tmp_path):
    root = _package(tmp_path)
    write_members(root)
    before = (root / "ability.yaml").read_text(encoding="utf-8")

    (root / "operations" / "inspect.py").write_text(
        'TITLE = "数据源体检"\n' + INSPECT, encoding="utf-8"
    )
    result = write_members(root, dry_run=True)
    assert result.changed
    assert (root / "ability.yaml").read_text(encoding="utf-8") == before


def test_v1_parses_authored_only_definition(tmp_path):
    definition = parse_definition(AUTHORED, source="authored")
    assert definition.container_name == "reporting"
    assert definition.operations == {}
    assert definition.skills == {}
    assert definition.provider_requirements == {}


def test_build_refuses_legacy_definition(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "ability.yaml").write_text(
        "format: ghrah.ability-definition\nschema_version: 0.1-draft\n"
        "identity: {namespace: ghrah, name: fs, version: 0.1.0}\n"
        "title: Filesystem\ndescription: Read files.\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestValidationError, match="schema_version"):
        write_members(root)


def test_attachments_are_omitted_without_skill_body(tmp_path):
    root = _package(tmp_path)
    (root / "skill" / "SKILL.md").unlink()
    result = write_members(root)
    # 附件归属技能：没有正文就没有归属，目录存在也不写进生成区（也不报错）。
    assert "skill" not in result.members
    assert "references" not in result.members
    assert "assets" not in result.members


def test_duplicate_attachment_stems_are_rejected(tmp_path):
    root = _package(tmp_path)
    (root / "skill" / "references" / "nested").mkdir()
    (root / "skill" / "references" / "nested" / "style-guide.md").write_text(
        "# 另一份\n", encoding="utf-8"
    )
    with pytest.raises(ManifestValidationError, match="成员键冲突"):
        write_members(root)


def test_historical_skills_directory_is_still_discovered(tmp_path):
    root = tmp_path / "nested"
    (root / "skills" / "references").mkdir(parents=True)
    (root / "ability.yaml").write_text(AUTHORED, encoding="utf-8")
    (root / "skills" / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    (root / "skills" / "references" / "style.md").write_text("# 风格", encoding="utf-8")

    members = scan_members(root)
    assert members["skill"]["body"] == "skills/SKILL.md"
    assert [item["path"] for item in members["references"]] == ["skills/references/style.md"]
    assert "assets" not in members


def test_attachments_are_optional(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "ability.yaml").write_text(AUTHORED, encoding="utf-8")
    (root / "skill").mkdir()
    (root / "skill" / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")

    members = scan_members(root)
    assert set(members) == {"skill"}


def test_scanned_container_exposes_v1_members(tmp_path):
    root = _package(tmp_path)
    write_members(root)
    ((container,),) = (scan_ability_definitions((path_root(tmp_path),)).containers,)
    assert container.name == "reporting"
    assert container.operation_count == 2
    assert container.skill_count == 1
    assert container.skills[0].body_path == "skill/SKILL.md"
    # 目录里的名字与说明来自 SKILL.md 的 frontmatter，而不是 YAML 索引。
    assert container.skills[0].title == "reporting-guide"
    assert container.skills[0].description == "使用获准模板和写作指南整理报告。"
    assert [item.effects for item in container.operations] == [(), ("filesystem:write",)]


def test_scan_falls_back_when_skill_has_no_frontmatter(tmp_path):
    root = _package(tmp_path)
    (root / "skill" / "SKILL.md").write_text("# 只有正文\n", encoding="utf-8")
    write_members(root)
    ((container,),) = (scan_ability_definitions((path_root(tmp_path),)).containers,)
    # 正文没有 frontmatter 时退回定义面的标题与说明，而不是给模型一行空白。
    assert container.skills[0].title == "Reporting"
    assert container.skills[0].description == "生成周报与月报，按模板渲染。"


def test_scan_layer_does_not_import_build():
    """分层：扫描层（ability_scan）不依赖构建层（build）。"""
    code = (
        "import sys; import ghrah.ability.loading.ability_scan; "
        "print('ghrah.ability.build' in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == "False"


def test_catalog_merges_lock_issues(tmp_path):
    """技能目录带上登记面问题：provider 能看到的漂移，catalog 也要能看到。"""
    root = tmp_path / "reporting"
    (root / "skill").mkdir(parents=True)
    (root / "ability.yaml").write_text(AUTHORED, encoding="utf-8")
    (root / "skill" / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    (root / "ability.lock.yaml").write_text("这不是映射\n", encoding="utf-8")

    catalog = load_skill_catalog((path_root(tmp_path),))
    assert catalog.names == ("reporting",)
    assert any("lock" in issue.path.lower() or "登记" in issue.reason for issue in catalog.issues)


def test_bare_skill_directory_is_still_discovered(tmp_path):
    """没有 ability.yaml 也能被扫到：目录面以磁盘为准，成员从 SKILL.md/operations 合成。"""
    root = tmp_path / "bare"
    (root / "skill").mkdir(parents=True)
    (root / "skill" / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")

    result = scan_ability_definitions((path_root(tmp_path),))
    (container,) = result.containers
    assert container.name == "bare"
    assert container.synthesized is True
    assert container.skills[0].title == "reporting-guide"
    assert "缺少 ability.yaml" in " ".join(issue.reason for issue in container.issues)


def test_legacy_definition_still_yields_members(tmp_path):
    """旧格式（0.1-draft）解析失败不再等于丢包：成员按磁盘补出来，说明退回旧 yaml。"""
    root = tmp_path / "legacy"
    (root / "skills").mkdir(parents=True)
    (root / "ability.yaml").write_text(
        "format: ghrah.ability-definition\n"
        "schema_version: 0.1-draft\n"
        "identity: {namespace: legacy, version: 0.1.0}\n"
        "title: 旧包\n"
        "description: 旧格式里写的说明。\n"
        "skills:\n"
        "  legacy: {title: 旧包, description: 旧格式里写的说明。}\n",
        encoding="utf-8",
    )
    (root / "skills" / "SKILL.md").write_text("# 正文\n", encoding="utf-8")

    (container,) = scan_ability_definitions((path_root(tmp_path),)).containers
    assert container.synthesized is True
    assert container.skills[0].description == "旧格式里写的说明。"


def test_skill_on_disk_wins_over_stale_definition(tmp_path):
    """技能面以 SKILL.md 为准：定义面漏声明也照样进目录，并提示重建生成区。"""
    root = tmp_path / "reporting"
    (root / "skill").mkdir(parents=True)
    (root / "ability.yaml").write_text(AUTHORED, encoding="utf-8")
    (root / "skill" / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")

    (container,) = scan_ability_definitions((path_root(tmp_path),)).containers
    assert container.synthesized is False
    assert [item.key for item in container.skills] == ["reporting"]
    assert any("定义面未声明技能" in issue.reason for issue in container.issues)


def test_missing_declared_body_is_repaired_from_disk(tmp_path):
    """声明的正文路径不存在、但盘上有 SKILL.md：用盘上那份并记一条问题。"""
    root = tmp_path / "reporting"
    (root / "skill").mkdir(parents=True)
    (root / "skill" / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    (root / "ability.yaml").write_text(
        AUTHORED + "\nmembers:\n  skill: {body: skill/OLD.md, media_type: text/markdown}\n",
        encoding="utf-8",
    )

    (container,) = scan_ability_definitions((path_root(tmp_path),)).containers
    assert container.skills[0].body_path == "skill/SKILL.md"
    assert any("已按磁盘 skill/SKILL.md 修正" in issue.reason for issue in container.issues)


def test_migrate_rewrites_legacy_definition(tmp_path):
    """--migrate：旧格式按可推导字段重写成 v1，说明与身份保留。"""
    root = tmp_path / "legacy"
    (root / "skills").mkdir(parents=True)
    (root / "ability.yaml").write_text(
        "format: ghrah.ability-definition\n"
        "schema_version: 0.1-draft\n"
        "identity: {namespace: legacy, version: 0.3.0}\n"
        "title: 旧标题\n"
        "description: 旧说明。\n"
        "skills:\n"
        "  legacy: {title: 旧标题, description: 旧说明。}\n",
        encoding="utf-8",
    )
    (root / "skills" / "SKILL.md").write_text("# 正文\n", encoding="utf-8")

    result = write_members(root, migrate=True)
    text = result.path.read_text(encoding="utf-8")
    assert "schema_version: '1'" in text
    assert "title: 旧标题" in text
    assert "version: 0.3.0" in text

    (container,) = scan_ability_definitions((path_root(tmp_path),)).containers
    assert container.synthesized is False
    assert container.issues == ()


def test_bootstrap_creates_definition_then_scan_is_clean(tmp_path):
    """补齐定义后，同一个包不再是合成视图（下一步与 lock 一致）。"""
    root = tmp_path / "bare"
    (root / "skill").mkdir(parents=True)
    (root / "skill" / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")

    result = write_members(root, bootstrap=True)
    assert result.changed is True
    assert "schema_version: '1'" in result.path.read_text(encoding="utf-8")

    (container,) = scan_ability_definitions((path_root(tmp_path),)).containers
    assert container.synthesized is False
    assert container.issues == ()
    # 再跑一次是幂等的：定义已经在盘上，不再重写。
    assert write_members(root).changed is False


def test_initialize_generates_members_next_to_lock(tmp_path):
    """安装 / 初次扫描期补齐生成区：与 lock 同一个生命周期点、同一个落点。"""
    from ghrah.ability.loading.skill_provider import SkillAbilityProvider

    root = _package(tmp_path)
    assert "members:" not in (root / "ability.yaml").read_text(encoding="utf-8")

    SkillAbilityProvider.from_root(tmp_path).initialize()

    text = (root / "ability.yaml").read_text(encoding="utf-8")
    assert text.startswith(AUTHORED)
    assert "members:" in text
    assert (root / "ability.lock.yaml").is_file()


def test_tool_schema_comes_from_implementation_binding():
    """工具面统一由实现的 ``bind_tool()`` 定义，定义面不再承载参数 schema。"""
    from ghrah.ability.loading.ability_catalog import load_all_builtin_manifests

    manifests = load_all_builtin_manifests()
    read_file = manifests["ghrah.fs.read_file"]
    assert read_file.tool is not None
    assert set(read_file.tool.parameters) == {"file_path", "encoding", "offset", "limit"}
    assert read_file.tool.parameters["file_path"].required is True
    # 实现不绑定工具（bind_tool() 返回 None）→ 该成员不进工具面。
    assert manifests["ghrah.core.conversation"].tool is None


def test_frontmatter_parse_rejects_incomplete_block():
    from ghrah.ability.loading.skill_errors import SkillParseError
    from ghrah.ability.loading.skill_frontmatter import parse_skill_frontmatter

    assert parse_skill_frontmatter("# 无 frontmatter") is None
    unclosed = """---
name: x"""
    with pytest.raises(SkillParseError, match="未闭合"):
        parse_skill_frontmatter(unclosed)
    missing_description = """---
name: x
---

# 正文"""
    with pytest.raises(SkillParseError, match="description"):
        parse_skill_frontmatter(missing_description)
