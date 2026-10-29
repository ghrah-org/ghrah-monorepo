# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""模型工具面（``load_tool_list``）测试。

钉住「只读 yaml → OpenAI tool 列表」这条契约：

- 工具名取成员键（不是全名），``parameters`` 与 ``list_abilities`` 展示同一形状；
- ``expose: false`` 的成员不进列表（如 ``conversation``）；
- 纯技能包（只有 ``skills``）不贡献工具，也不报错；
- 跨容器撞名时先入者胜出，后到的记一条问题——不静默覆盖；
- 扫描根缺失只产问题，不让整轮装载失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghrah.scheme_ability.loading import ability_roots as roots
from ghrah.scheme_ability.loading.tool_list import load_tool_list


def _operation(
    key: str,
    *,
    expose: bool | None = None,
    description: str | None = None,
) -> dict:
    """造一个 operation 成员声明。

    Args:
        key: ``operations`` 表里的键（也就是工具名）。
        expose: ``expose`` 显式取值；None 表示按 schema 默认（True）。
        description: 成员说明；None 取默认文案。

    Returns:
        可直接 ``yaml.safe_dump`` 的成员映射。
    """
    member: dict[str, object] = {
        "title": f"{key} 标题",
        "description": description or f"{key} 说明。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目标路径"}},
            "required": ["path"],
        },
        "output_schema": {"type": "object", "properties": {}},
        "implementation": {"provider_slot": "backend", "operation": key},
        "execution": {
            "mode": "request",
            "timeout_ms": 1000,
            "cancellation": "unsupported",
            "on_revoke": "drain",
            "retry": "never",
        },
    }
    if expose is not None:
        member["expose"] = expose
    return member


def _skill(key: str) -> dict:
    """造一个 skill 成员声明（只为验证"技能包不出工具"）。"""
    return {
        "title": f"{key} 标题",
        "description": f"{key} 技能说明。",
        "body": {"path": "skills/SKILL.md", "media_type": "text/markdown"},
    }


def _definition(
    name: str,
    *,
    namespace: str | None = None,
    operations: dict[str, dict] | None = None,
    skills: dict[str, dict] | None = None,
) -> dict:
    """造一份合法的 ``ability.yaml`` 内容。

    Args:
        name: 包目录名（= 容器名）。
        namespace: ``identity.namespace``（单段）；None 取 ``name``。
        operations: ``operations`` 表；None 表示不声明。
        skills: ``skills`` 表；None 表示不声明。

    Returns:
        可直接 ``yaml.safe_dump`` 的映射。
    """
    payload: dict[str, object] = {
        "format": "ghrah.ability-definition",
        "schema_version": "0.1-draft",
        "identity": {"namespace": namespace or name, "version": "0.1.0"},
        "title": name,
        "description": f"{name} 容器的说明。",
    }
    if operations is not None:
        payload["provider_requirements"] = {"backend": {"contract": "ghrah.test.v1"}}
        payload["operations"] = operations
    if skills is not None:
        payload["skills"] = skills
    return payload


def _write(root: Path, name: str, payload: dict, *, body: bool = False) -> Path:
    """在池根目录下落一个能力包。

    Args:
        root: 池根目录（调用方保证已建）。
        name: 包目录名。
        payload: ``ability.yaml`` 内容。
        body: 是否写 ``skills/SKILL.md``。

    Returns:
        新建的包目录。
    """
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "ability.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    if body:
        skill_dir = package / "skills"
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8", newline="\n")
    return package


class TestToolListShape:
    def test_one_tool_per_operation_sorted_by_name(self, tmp_path: Path) -> None:
        """成员键即工具名，列表按名字升序，归属可反查。"""
        _write(
            tmp_path,
            "fs",
            _definition(
                "fs",
                operations={
                    "write_file": _operation("write_file"),
                    "read_file": _operation("read_file"),
                },
            ),
        )
        pool = (roots.path_root(tmp_path, source="path"),)

        tool_list = load_tool_list(pool)

        assert tool_list.names == ("read_file", "write_file")
        assert [tool["function"]["name"] for tool in tool_list.tools] == [
            "read_file",
            "write_file",
        ]
        entry = tool_list.get("read_file")
        assert entry is not None
        assert entry.container == "fs"
        assert entry.operation == "read_file"
        assert entry.full_name == "fs.read_file"
        assert entry.root.source == "path"

    def test_tool_payload_is_openai_function_shape(self, tmp_path: Path) -> None:
        """工具体是 ``{"type": "function", "function": {...}}``，参数来自 input_schema。"""
        _write(
            tmp_path,
            "fs",
            _definition(
                "fs", operations={"read_file": _operation("read_file", description="读文件。")}
            ),
        )

        tool = load_tool_list((roots.path_root(tmp_path, source="path"),)).tools[0]

        assert tool["type"] == "function"
        function = tool["function"]
        assert function["name"] == "read_file"
        assert function["description"] == "读文件。"
        assert function["parameters"]["required"] == ["path"]
        assert function["parameters"]["properties"]["path"]["type"] == "string"

    def test_expose_false_is_hidden(self, tmp_path: Path) -> None:
        """``expose: false`` 的成员不进工具面（conversation 的口径）。"""
        _write(
            tmp_path,
            "core",
            _definition(
                "core",
                operations={
                    "whoami": _operation("whoami"),
                    "conversation": _operation("conversation", expose=False),
                },
            ),
        )

        tool_list = load_tool_list((roots.path_root(tmp_path, source="path"),))

        assert tool_list.names == ("whoami",)
        assert tool_list.get("conversation") is None

    def test_skill_only_package_contributes_no_tool(self, tmp_path: Path) -> None:
        """只有 skills 的包不出工具，也不报错。"""
        _write(
            tmp_path,
            "research",
            _definition("research", skills={"research": _skill("research")}),
            body=True,
        )

        tool_list = load_tool_list((roots.path_root(tmp_path, source="path"),))

        assert tool_list.tools == ()
        assert tool_list.issues == ()

    def test_default_pool_covers_builtin_and_user_roots(self) -> None:
        """默认池包含内置树与用户目录，且内置优先。"""
        tool_list = load_tool_list()

        labels = [root.source for root in tool_list.roots]
        assert labels[0] == "builtin"
        assert "skill-ability" in labels
        assert tool_list.issues == ()


class TestToolListIssues:
    def test_duplicate_tool_name_keeps_first_and_reports(self, tmp_path: Path) -> None:
        """跨容器撞名：先入者胜出，后到的记一条问题（不静默覆盖）。"""
        _write(
            tmp_path,
            "alpha",
            _definition("alpha", operations={"read_file": _operation("read_file")}),
        )
        _write(
            tmp_path, "beta", _definition("beta", operations={"read_file": _operation("read_file")})
        )

        tool_list = load_tool_list((roots.path_root(tmp_path, source="path"),))

        assert tool_list.names == ("read_file",)
        assert tool_list.get("read_file") is not None
        assert tool_list.get("read_file").container == "alpha"
        assert len(tool_list.issues) == 1
        assert "beta" in str(tool_list.issues[0])
        assert "alpha" in str(tool_list.issues[0])

    def test_missing_required_root_reports_issue(self, tmp_path: Path) -> None:
        """必需根不存在：记问题，其余根照常出工具（收集式失败）。"""
        good = tmp_path / "good"
        good.mkdir()
        _write(good, "fs", _definition("fs", operations={"read_file": _operation("read_file")}))
        pool = (
            roots.path_root(good, source="path"),
            roots.path_root(tmp_path / "absent", source="path-absent", required=True),
        )

        tool_list = load_tool_list(pool)

        assert tool_list.names == ("read_file",)
        assert len(tool_list.issues) == 1
        assert tool_list.issues[0].container is None

    def test_invalid_definition_is_reported_not_raised(self, tmp_path: Path) -> None:
        """定义非法：该包不产工具，问题收进列表。"""
        package = tmp_path / "broken"
        package.mkdir()
        (package / "ability.yaml").write_text("format: nope\n", encoding="utf-8", newline="\n")

        tool_list = load_tool_list((roots.path_root(tmp_path, source="path"),))

        assert tool_list.tools == ()
        assert len(tool_list.issues) == 1


class TestToolListEmpty:
    def test_empty_pool_returns_nothing(self) -> None:
        """空池：没有工具、没有问题，也不抛。"""
        tool_list = load_tool_list(())

        assert tool_list.tools == ()
        assert tool_list.entries == ()
        assert tool_list.roots == ()
        assert tool_list.issues == ()

    def test_unknown_name_returns_none(self, tmp_path: Path) -> None:
        """未知工具名返回 None——不按名字猜容器。"""
        _write(tmp_path, "fs", _definition("fs", operations={"read_file": _operation("read_file")}))

        tool_list = load_tool_list((roots.path_root(tmp_path, source="path"),))

        assert tool_list.get("nope") is None


class TestToolListMatchesRuntime:
    def test_declared_face_equals_bound_tool_face(self) -> None:
        """内置树的 yaml 工具面必须与运行时绑定面一致。

        两边一旦漂移，模型会看到「声明了但绑不上」的工具（或反之）。这条用真实
        内置树比对：名字集合相同、每个工具的说明相同。
        """
        from ghrah.scheme_ability.loading.ability_catalog import load_all_builtin_manifests

        runtime = {
            manifest.tool.name: manifest.tool
            for manifest in load_all_builtin_manifests().values()
            if manifest.tool is not None
        }
        declared = load_tool_list()

        assert set(declared.names) == set(runtime)
        for name, schema in runtime.items():
            entry = declared.get(name)
            assert entry is not None
            assert entry.tool["function"]["description"] == schema.description


if __name__ == "__main__":  # pragma: no cover - 便于单文件运行
    pytest.main([__file__])
