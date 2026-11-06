# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""成员存在性校验：定义面 ``members.operations`` 键与 ``operations/*.py`` 双向一一对应。

两条约束都必须在装配期成立，不能拖到第一次调用才暴露：

1. **声明了但没有实现脚本**——此前只在投影 import 时报「无法装载实现脚本」，
   看不出是缺文件还是脚本本身坏了；
2. **有实现脚本但没有声明**——此前完全静默：脚本不会被任何投影引用，
   是一份不会被加载、也不会被发现的能力实现。

本用例用临时内置根（换一套内置能力树）构造两个方向的缺口，断言
``load_ability_definitions()`` 与装配入口都直接失败。

v1 以生成索引 ``members`` 为成员权威：正常走生成器的包不会出现「有脚本未声明」；
但索引是手写 YAML，漏声明仍可能发生，装载期的双向校验因此保留。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from ghrah.ability.assembly import AbilityAssembly
from ghrah.ability.loading import ability_catalog
from ghrah.ability.loading import ability_roots as roots
from ghrah.ability.loading.manifest_fields import ManifestValidationError


def _write_container(
    root: Path,
    name: str,
    *,
    declared: Sequence[str],
    scripts: Sequence[str],
) -> Path:
    """在临时内置根下造一个容器：声明面与实现脚本可各自指定。

    Args:
        root: 充当内置树根的目录。
        name: 容器名（也是成员键前缀）。
        declared: ``ability.yaml`` 的 ``members.operations`` 键。
        scripts: 实际写入 ``operations/`` 的脚本名（不含 ``.py``）。

    Returns:
        新建的容器目录。
    """
    container = root / name
    operations = container / "operations"
    operations.mkdir(parents=True)
    definition = {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": {"namespace": "ghrah", "name": name, "version": "0.1.0"},
        "title": name,
        "description": f"{name} 容器。",
        "members": {
            "operations": {
                key: {
                    "title": key,
                    "description": f"{key} 成员。",
                    "entry": f"operations/{key}.py",
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
                }
                for key in declared
            }
        },
    }
    (container / "ability.yaml").write_text(
        yaml.safe_dump(definition, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    (operations / "__init__.py").write_text("", encoding="utf-8", newline="\n")
    for key in scripts:
        (operations / f"{key}.py").write_text(
            "from ghrah.ability.execution.base import Ability\n"
            "\n"
            "\n"
            f"class {key.title()}Ability(Ability):\n"
            f'    """测试实现：{key}。"""\n',
            encoding="utf-8",
            newline="\n",
        )
    return container


@pytest.fixture
def temp_builtin_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把内置扫描根换到临时目录（等价于换一套内置能力树）。

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest 的补丁器。

    Returns:
        临时内置根。
    """
    monkeypatch.setattr(
        roots,
        "builtin_root",
        lambda: roots.path_root(
            tmp_path,
            rank=roots.BUILTIN_ROOT_RANK,
            source=roots.BUILTIN_ROOT_SOURCE,
            required=True,
        ),
    )
    return tmp_path


def test_declared_member_without_script_fails(temp_builtin_root: Path) -> None:
    """声明了但没有 ``operations/<键>.py``：装载即失败，并指名缺哪个成员。"""
    _write_container(
        temp_builtin_root,
        "alpha",
        declared=["alpha_op", "ghost_op"],
        scripts=["alpha_op"],
    )

    with pytest.raises(ManifestValidationError, match="声明了但没有实现脚本") as excinfo:
        ability_catalog.load_ability_definitions()

    assert "ghost_op" in str(excinfo.value)


def test_script_without_declaration_fails(temp_builtin_root: Path) -> None:
    """有脚本但未在定义面声明：不能再静默存在，装载即失败。

    v1 由生成器写 ``members``，正常流程不会产出这种包；但手写索引漏声明时，
    装载期仍必须报出，不能把一份永远不会被加载的实现留在树里。
    """
    _write_container(
        temp_builtin_root,
        "alpha",
        declared=["alpha_op"],
        scripts=["alpha_op", "orphan_op"],
    )

    with pytest.raises(ManifestValidationError, match="有实现脚本但没有声明") as excinfo:
        ability_catalog.load_ability_definitions()

    assert "orphan_op" in str(excinfo.value)


def test_both_directions_are_reported_together(temp_builtin_root: Path) -> None:
    """同一次装载把两个方向的缺口一起报出，便于一轮看全。"""
    _write_container(
        temp_builtin_root,
        "alpha",
        declared=["alpha_op", "ghost_op"],
        scripts=["alpha_op", "orphan_op"],
    )

    with pytest.raises(ManifestValidationError) as excinfo:
        ability_catalog.load_ability_definitions()

    message = str(excinfo.value)
    assert "ghost_op" in message
    assert "orphan_op" in message


def test_assembly_fails_instead_of_deferring_to_first_spawn(
    temp_builtin_root: Path,
) -> None:
    """装配选择 builtin 时就失败，不把缺口拖到第一次 spawn。"""
    _write_container(
        temp_builtin_root,
        "alpha",
        declared=["alpha_op"],
        scripts=["alpha_op", "orphan_op"],
    )

    with pytest.raises(ManifestValidationError, match="有实现脚本但没有声明"):
        AbilityAssembly.select("builtin")


def test_real_builtin_tree_members_match() -> None:
    """真实内置树的声明面与实现脚本一一对应（当前状态回归）。"""
    definitions = ability_catalog.load_ability_definitions()

    assert "read_file" in definitions["fs"].operations
