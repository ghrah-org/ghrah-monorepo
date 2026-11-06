# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""生成器原型与 fixture 的验收测试。

fixtures 是**持久产物**：``ability.yaml`` 的 ``members`` 由生成器写入并随源码
提交；测试一律先用 tmp_path 复制再动盘，不得改写 fixture 原件。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest
import yaml

from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.package.generate import (
    BuildError,
    IndexMismatchError,
    build,
    check,
)
from ghrah.ability.package.paths import PathRuleError, check_relative_path
from ghrah.ability.versions import capture_ability_version

FIXTURES = Path(__file__).parent / "fixtures"
PLAIN_SOURCE = """\
from pydantic import BaseModel, ConfigDict


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: str


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


TITLE = "Plain"
SUMMARY = "plain"
TAGS: list[str] = []
PERMISSIONS: dict[str, object] = {}
EFFECTS: list[str] = []
TIMEOUT_MS = 1000
"""


def _copy(name: str, tmp_path: Path) -> Path:
    """把 fixture 复制到临时目录（不动原件）。"""
    root = tmp_path / name
    shutil.copytree(FIXTURES / name, root)
    return root


def _minimal(tmp_path: Path, name: str = "pkg") -> Path:
    """落一个最小合法源包：1 Operation + 1 Skill。"""
    root = tmp_path / name
    (root / "operations").mkdir(parents=True)
    (root / "skills" / "s1").mkdir(parents=True)
    (root / "ability.yaml").write_text(
        "format: ghrah.ability-definition\n"
        "schema_version: '1'\n"
        "identity: {namespace: fixtures, name: pkg, version: 0.1.0}\n"
        "title: Pkg\n"
        "description: pkg\n"
        "members: {}\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "operations" / "plain.py").write_text(PLAIN_SOURCE, encoding="utf-8", newline="\n")
    (root / "skills" / "s1" / "SKILL.md").write_text(
        "---\nname: s1\ndescription: d\n---\n\nbody\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def _diff(expected: object, actual: object, path: str = "") -> list[str]:
    """递归比较两个 JSON 值，返回可读差异列表。"""
    if type(expected) is not type(actual):
        return [f"{path}: 类型 {type(expected).__name__} != {type(actual).__name__}"]
    if isinstance(expected, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                differences.append(f"{path}/{key}: 多余 {actual[key]!r}")
            elif key not in actual:
                differences.append(f"{path}/{key}: 缺失（期望 {expected[key]!r}）")
            else:
                differences.extend(_diff(expected[key], actual[key], f"{path}/{key}"))
        return differences
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return [f"{path}: 长度 {len(expected)} != {len(actual)}"]
        differences = []
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            differences.extend(_diff(left, right, f"{path}[{index}]"))
        return differences
    if expected != actual:
        return [f"{path}: {expected!r} != {actual!r}"]
    return []


@pytest.mark.parametrize("name", ["reporting", "multi-skill", "builder"])
def test_fixture_build_is_idempotent(name: str, tmp_path: Path) -> None:
    """同一 fixture 连续两次生成，字节相同。"""
    root = _copy(name, tmp_path)
    first = build(root)
    assert not first.changed, "fixture 原件应已与索引一致（仓库提交前已 build）"
    second = build(root)
    assert not second.changed
    assert second.index_digest == first.index_digest
    assert (root / "ability.yaml").read_text(encoding="utf-8") == first.rendered


@pytest.mark.parametrize("name", ["reporting", "multi-skill", "builder"])
def test_fixture_check_passes(name: str, tmp_path: Path) -> None:
    """``--check`` 在索引最新时通过。"""
    root = _copy(name, tmp_path)
    build(root)
    check(root)


def test_check_fails_after_summary_change(tmp_path: Path) -> None:
    """改动一处 SUMMARY 后不重新生成，``--check`` 失败并列出差异。"""
    root = _copy("reporting", tmp_path)
    build(root)
    script = root / "operations" / "render.py"
    script.write_text(
        script.read_text(encoding="utf-8").replace("按模板渲染一份报告。", "改成别的说明。"),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(IndexMismatchError) as caught:
        check(root)
    differences = caught.value.differences
    assert any(line.startswith("+") and "改成别的说明" in line for line in differences)
    assert any(line.startswith("-") and "按模板渲染一份报告" in line for line in differences)


def test_generated_index_matches_expected_definition(tmp_path: Path) -> None:
    """生成索引与冻结的旧模型基线逐字段一致（不靠抽查）。"""
    root = _copy("reporting", tmp_path)
    build(root)
    definition = parse_definition(
        (root / "ability.yaml").read_text(encoding="utf-8"), source="reporting"
    )
    actual = definition.model_dump(mode="json")
    expected = json.loads((root / "expected-definition.json").read_text(encoding="utf-8"))
    differences = _diff(expected, actual)
    assert not differences, "\n".join(differences)
    operation = definition.operations["render"]
    assert operation.entry is not None
    assert operation.entry.path == "operations/render.py"
    assert operation.input_schema["properties"]["template"]["description"] == "模板名（assets 键）"
    assert definition.skills["reporting-guide"].title == "Reporting Guide"


def test_capture_preserves_v1_metadata_title(tmp_path: Path) -> None:
    """捕获期读取 ``metadata.title`` / ``metadata.tags``，不覆盖为 name。"""
    root = _copy("reporting", tmp_path)
    build(root)
    version = capture_ability_version(
        (root / "ability.yaml").read_text(encoding="utf-8"), root=root
    )
    skill = version.definition().skills["reporting-guide"]
    assert skill.title == "Reporting Guide"
    assert skill.tags == ["reporting"]


def test_multi_skill_fixture_carries_two_skills(tmp_path: Path) -> None:
    """``multi-skill`` 生成 ``members.skills`` 两个键、路径与标题正确。"""
    root = _copy("multi-skill", tmp_path)
    result = build(root)
    data = yaml.safe_load(result.rendered)
    skills = data["members"]["skills"]
    assert set(skills) == {"reporting-guide", "review-guide"}
    assert skills["reporting-guide"]["body"]["path"] == "skills/reporting-guide/SKILL.md"
    assert skills["review-guide"]["title"] == "review-guide"


def test_builder_fixture_members_match_frontmatter(tmp_path: Path) -> None:
    """``builder`` 的 ``members`` 与 SKILL.md frontmatter 逐字段一致。"""
    root = _copy("builder", tmp_path)
    result = build(root)
    data = yaml.safe_load(result.rendered)
    members = data["members"]
    assert members["operations"] == {}
    (skill,) = members["skills"].values()
    assert skill["title"] == "Ability 包开发指南"
    assert skill["description"].startswith("定义一个 Ability 包的标准结构")
    assert skill["tags"] == ["ability", "guide", "package"]
    assert set(members["skills"]) == {"ability-package-development-guide"}
    assert "export" in skill["scripts"]
    assert skill["scripts"]["export"]["media_type"] == "text/x-python"


def test_scripts_are_content_not_operations(tmp_path: Path) -> None:
    """``skills/<name>/scripts/`` 只登记为内容资源，不生成 Operation。"""
    root = _copy("builder", tmp_path)
    build(root)
    definition = parse_definition(
        (root / "ability.yaml").read_text(encoding="utf-8"), source="builder"
    )
    assert definition.operations == {}
    assert "export" in definition.skills["ability-package-development-guide"].scripts


def test_reserved_tool_name_is_rejected(tmp_path: Path) -> None:
    """保留名（``run_script`` 等）在生成期拒绝。"""
    root = _copy("reporting", tmp_path)
    shutil.move(root / "operations" / "render.py", root / "operations" / "run_script.py")
    with pytest.raises(BuildError, match="保留名"):
        build(root)


def test_absolute_path_in_permissions_is_rejected(tmp_path: Path) -> None:
    """``permissions.allowed_paths`` 出现绝对路径即生成失败。"""
    root = _copy("reporting", tmp_path)
    script = root / "operations" / "render.py"
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "PERMISSIONS: dict[str, object] = {}",
            'PERMISSIONS: dict[str, object] = {"allowed_paths": ["/etc"]}',
        ),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="相对路径"):
        build(root)


def test_permissions_value_type_is_validated(tmp_path: Path) -> None:
    """``permissions`` 的值类型错误必须变成可读的生成错误。"""
    root = _minimal(tmp_path)
    (root / "operations" / "plain.py").write_text(
        PLAIN_SOURCE.replace(
            "PERMISSIONS: dict[str, object] = {}",
            'PERMISSIONS: dict[str, object] = {"allowed_paths": 3}',
        ),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="list\\[str\\]"):
        build(root)


def test_missing_output_fails(tmp_path: Path) -> None:
    """缺 ``Output`` 时生成失败，不写空对象占位。"""
    root = _minimal(tmp_path)
    source = PLAIN_SOURCE.replace(
        'class Output(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n\n', ""
    )
    (root / "operations" / "plain.py").write_text(source, encoding="utf-8", newline="\n")
    with pytest.raises(BuildError, match="Output"):
        build(root)


def test_generated_index_has_no_host_artifacts(tmp_path: Path) -> None:
    """生成索引不含宿主绝对路径、时间戳或平台分隔符。"""
    root = _copy("reporting", tmp_path)
    result = build(root)
    for artifact in ("/home/", "C:\\", str(root.resolve()), "\\"):
        assert artifact not in result.rendered


def test_unknown_top_level_file_is_allowed(tmp_path: Path) -> None:
    """包根的非成员文件可以存在，只是不进索引。"""
    root = _minimal(tmp_path)
    (root / "CHANGELOG.md").write_text("x\n", encoding="utf-8", newline="\n")
    build(root)


@pytest.mark.parametrize(
    "name", ["SKILL.md", "references", "assets", "scripts", "ability.lock.yaml"]
)
def test_old_layout_entries_are_rejected(name: str, tmp_path: Path) -> None:
    """旧布局根条目与源包 lock 在生成期拒绝。"""
    root = _minimal(tmp_path)
    target = root / name
    if name == "ability.lock.yaml":
        target.write_text("schema: 2\n", encoding="utf-8", newline="\n")
    elif name == "SKILL.md":
        target.write_text("---\n", encoding="utf-8", newline="\n")
    else:
        target.mkdir()
        (target / "x.md").write_text("x\n", encoding="utf-8", newline="\n")
    with pytest.raises(BuildError, match="旧布局|安装记录"):
        build(root)


def test_duplicate_keys_are_rejected(tmp_path: Path) -> None:
    """头段与 frontmatter 的重复键都拒绝。"""
    root = _minimal(tmp_path)
    header = (root / "ability.yaml").read_text(encoding="utf-8")
    (root / "ability.yaml").write_text(
        header.replace("title: Pkg\n", "title: Pkg\ntitle: Other\n"),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="重复键"):
        build(root)

    root = _minimal(tmp_path, "frontmatter")
    body = root / "skills" / "s1" / "SKILL.md"
    body.write_text(
        "---\nname: s1\ndescription: a\ndescription: b\n---\n\nbody\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="重复键"):
        build(root)


def test_merge_key_is_rejected(tmp_path: Path) -> None:
    """``<<`` merge key 在头段与 frontmatter 中拒绝。"""
    root = _minimal(tmp_path)
    (root / "ability.yaml").write_text(
        "format: ghrah.ability-definition\n"
        "schema_version: '1'\n"
        "identity:\n"
        "  <<: {namespace: fixtures, name: pkg, version: 0.1.0}\n"
        "title: Pkg\n"
        "description: pkg\n"
        "members: {}\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="merge key"):
        build(root)


def test_origin_and_identity_are_validated(tmp_path: Path) -> None:
    """``origin`` 取值与 ``identity`` 未知键在生成期拒绝。"""
    root = _minimal(tmp_path)
    header = (root / "ability.yaml").read_text(encoding="utf-8")
    (root / "ability.yaml").write_text(
        header.replace("schema_version: '1'\n", "schema_version: '1'\norigin: bogus\n"),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="origin"):
        build(root)

    root = _minimal(tmp_path, "identity")
    (root / "ability.yaml").write_text(
        (root / "ability.yaml")
        .read_text(encoding="utf-8")
        .replace(
            "identity: {namespace: fixtures, name: pkg, version: 0.1.0}",
            "identity: {namespace: fixtures, name: pkg, version: 0.1.0, extra: x}",
        ),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="identity 未知字段"):
        build(root)


def test_crlf_and_bom_are_rejected(tmp_path: Path) -> None:
    """文本源 CRLF 与 BOM 在生成期硬失败。"""
    root = _minimal(tmp_path)
    body = root / "skills" / "s1" / "SKILL.md"
    body.write_bytes(b"---\r\nname: s1\r\ndescription: d\r\n---\r\n\r\nbody\r\n")
    with pytest.raises(BuildError, match="CR/CRLF"):
        build(root)

    root = _minimal(tmp_path, "bom")
    body = root / "skills" / "s1" / "SKILL.md"
    body.write_bytes(b"\xef\xbb\xbf---\nname: s1\ndescription: d\n---\n\nbody\n")
    with pytest.raises(BuildError, match="BOM"):
        build(root)


def test_nested_schema_keeps_defs_and_descriptions(tmp_path: Path) -> None:
    """嵌套模型保留 ``$defs`` 与属性描述，``$ref`` 不悬空。"""
    root = _minimal(tmp_path)
    (root / "operations" / "plain.py").unlink()
    (root / "operations" / "nested.py").write_text(
        """\
from pydantic import BaseModel, ConfigDict, Field


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="item name")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: Item = Field(description="the item")


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


TITLE = "Nested"
SUMMARY = "nested"
TAGS: list[str] = []
PERMISSIONS: dict[str, object] = {}
EFFECTS: list[str] = []
TIMEOUT_MS = 1000
""",
        encoding="utf-8",
        newline="\n",
    )
    result = build(root)
    data = yaml.safe_load(result.rendered)
    schema = data["members"]["operations"]["nested"]["input_schema"]
    assert "$defs" in schema
    assert schema["properties"]["item"]["$ref"] == "#/$defs/Item"
    assert schema["$defs"]["Item"]["properties"]["name"]["description"] == "item name"
    assert schema["properties"]["item"]["description"] == "the item"


def test_additional_properties_must_be_strict(tmp_path: Path) -> None:
    """模型没有 ``extra='forbid'`` 时拒绝，不伪造索引。"""
    root = _minimal(tmp_path)
    (root / "operations" / "plain.py").write_text(
        PLAIN_SOURCE.replace(
            'class Input(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n\n',
            "class Input(BaseModel):\n",
        ),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="additionalProperties"):
        build(root)


def test_resource_keys_are_sorted(tmp_path: Path) -> None:
    """资源表按键升序，不按磁盘路径顺序。"""
    root = _minimal(tmp_path)
    references = root / "skills" / "s1" / "references"
    references.mkdir()
    (references / "a").mkdir()
    (references / "a-c.md").write_text("x\n", encoding="utf-8", newline="\n")
    (references / "a" / "b.md").write_text("y\n", encoding="utf-8", newline="\n")
    result = build(root)
    data = yaml.safe_load(result.rendered)
    assert list(data["members"]["skills"]["s1"]["references"]) == ["a-b", "a-c"]


def test_shared_helper_module_is_importable(tmp_path: Path) -> None:
    """``operations/_shared.py`` 可以被同目录成员导入。"""
    root = _minimal(tmp_path)
    (root / "operations" / "_shared.py").write_text(
        "TITLE_HELPER = '来自 helper'\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "operations" / "plain.py").write_text(
        "from _shared import TITLE_HELPER\n"
        + PLAIN_SOURCE.replace('TITLE = "Plain"', "TITLE = TITLE_HELPER"),
        encoding="utf-8",
        newline="\n",
    )
    result = build(root)
    data = yaml.safe_load(result.rendered)
    assert data["members"]["operations"]["plain"]["title"] == "来自 helper"


class TestPathRules:
    """路径规则的纯函数与生成期测试。"""

    @pytest.mark.parametrize(
        ("path", "reason"),
        [
            ("a\\b", "反斜杠"),
            ("/abs", "绝对"),
            ("C:/x", "盘符"),
            ("a/../b", "段"),
            ("a/b.txt ", "结尾"),
            ("a/b.txt.", "结尾"),
            ("a/CON.txt", "保留名"),
            ("a<b", "禁用字符"),
            ("a/b:c.txt", "禁用字符"),
            ("a:b.txt", "盘符"),
        ],
    )
    def test_invalid_paths_rejected(self, path: str, reason: str) -> None:
        with pytest.raises(PathRuleError, match=reason):
            check_relative_path(path, what="test")

    def test_nfc_normalization_returned(self) -> None:
        composed = "café.txt"
        decomposed = "cafe\u0301.txt"
        assert check_relative_path(decomposed, what="test") == composed

    def test_casefold_conflict_rejected(self, tmp_path: Path) -> None:
        root = _minimal(tmp_path)
        references = root / "skills" / "s1" / "references"
        references.mkdir()
        (references / "A.md").write_text("a\n", encoding="utf-8", newline="\n")
        (references / "a.md").write_text("b\n", encoding="utf-8", newline="\n")
        if len(list(references.iterdir())) < 2:
            pytest.skip("文件系统大小写不敏感")
        with pytest.raises(BuildError, match="路径冲突"):
            build(root)

    def test_nfc_conflict_rejected(self, tmp_path: Path) -> None:
        root = _minimal(tmp_path)
        references = root / "skills" / "s1" / "references"
        references.mkdir()
        (references / unicodedata.normalize("NFC", "café.md")).write_text(
            "n\n", encoding="utf-8", newline="\n"
        )
        (references / unicodedata.normalize("NFD", "café.md")).write_text(
            "d\n", encoding="utf-8", newline="\n"
        )
        if len(list(references.iterdir())) < 2:
            pytest.skip("文件系统会归一 Unicode")
        with pytest.raises(BuildError, match="路径冲突"):
            build(root)

    def test_long_relative_path_rejected(self) -> None:
        long_path = "/".join(["segmentabcdefghij"] * 10) + "/x.md"
        with pytest.raises(PathRuleError, match="180"):
            check_relative_path(long_path, what="test")

    def test_symlink_rejected(self, tmp_path: Path) -> None:
        root = _minimal(tmp_path)
        references = root / "skills" / "s1" / "references"
        references.mkdir()
        try:
            (references / "link.md").symlink_to(root / "skills" / "s1" / "SKILL.md")
        except OSError:
            pytest.skip("当前环境不能创建符号链接")
        with pytest.raises(BuildError, match="符号链接"):
            build(root)


def test_cli_entrypoint_check(tmp_path: Path) -> None:
    """CLI 入口：``python -m ghrah.ability.tools.build --check``。"""
    root = _copy("builder", tmp_path)
    build(root)
    result = subprocess.run(
        [sys.executable, "-m", "ghrah.ability.tools.build", str(root), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok (unchanged)" in result.stdout


def test_cli_entrypoint_detects_drift(tmp_path: Path) -> None:
    """CLI ``--check`` 在索引过期时非零退出并输出 diff。"""
    root = _copy("builder", tmp_path)
    build(root)
    body = root / "skills" / "ability-package-development-guide" / "SKILL.md"
    body.write_text(
        body.read_text(encoding="utf-8").replace(
            "description: 定义一个 Ability 包的标准结构",
            "description: 已过期的说明",
        ),
        encoding="utf-8",
        newline="\n",
    )
    result = subprocess.run(
        [sys.executable, "-m", "ghrah.ability.tools.build", str(root), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 1
    assert "mismatch: +" in result.stderr
    assert "已过期的说明" in result.stderr


def test_cli_rejects_missing_output(tmp_path: Path) -> None:
    """CLI 把缺 ``Output`` 写成 stderr 错误，并以非零退出。"""
    root = _minimal(tmp_path)
    source = PLAIN_SOURCE.replace(
        'class Output(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n\n', ""
    )
    (root / "operations" / "plain.py").write_text(source, encoding="utf-8", newline="\n")
    result = subprocess.run(
        [sys.executable, "-m", "ghrah.ability.tools.build", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 1
    assert "error:" in result.stderr
    assert "Output" in result.stderr


def test_builtin_package_can_be_rebuilt(tmp_path: Path) -> None:
    """内置规范包可用 ``--builtin`` 重建并通过 ``--check``。"""
    source = Path(__file__).parents[3] / "src" / "ghrah" / "ability" / "builtin" / "ability-builder"
    root = tmp_path / "ability-builder"
    shutil.copytree(source, root)
    first = build(root, builtin=True)
    assert not first.changed, "内置包应已是生成器规范格式"
    check(root, builtin=True)
    with pytest.raises(BuildError, match="ghrah"):
        build(root)

    body = root / "skills" / "ability-package-development-guide" / "SKILL.md"
    body.write_text(
        body.read_text(encoding="utf-8").replace("定义一个 Ability 包的标准结构", "已过期的说明"),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(IndexMismatchError):
        check(root, builtin=True)


def test_prototype_not_imported_by_runtime() -> None:
    """运行时可用包加载器，构建/import 探测仍只允许作者侧工具引用。"""
    source = Path(__file__).parents[3] / "src" / "ghrah"
    allowed = {
        (source / "ability" / "tools").resolve(),
    }
    hits: list[str] = []
    for path in source.rglob("*.py"):
        resolved = path.resolve()
        if path.name in {"generate.py", "introspect.py"}:
            continue
        if any(resolved.is_relative_to(prefix) for prefix in allowed):
            continue
        if any(
            module in path.read_text(encoding="utf-8")
            for module in ("ghrah.ability.package.generate", "ghrah.ability.package.introspect")
        ):
            hits.append(str(path))
    assert not hits, f"运行路径 import 了原型模块: {hits}"


def test_empty_member_set_is_rejected(tmp_path: Path) -> None:
    """operations 与 skills 合计至少一个成员。"""
    root = _copy("builder", tmp_path)
    shutil.rmtree(root / "skills")
    with pytest.raises(BuildError, match="至少要有一个成员"):
        build(root)
