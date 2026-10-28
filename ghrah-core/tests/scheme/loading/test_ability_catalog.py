# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""扫盘式能力目录（``ability_catalog``）测试。

覆盖两条要求：

1. **热插拔**：查询永远反映磁盘 —— 不持有 import 期快照、不做缓存；
2. **对外 API**：外层框架可直接调用（不必经模型工具面），
   即 ``load_ability_definitions`` / ``load_all_builtin_manifests`` /
   ``builtin_handlers`` / ``has_handler`` / ``ability_class`` / ``create_ability``。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghrah.scheme.loading import ability_catalog as catalog
from ghrah.scheme.loading import ability_roots as ability_roots

DEFINITION = {
    "format": "ghrah.ability-definition",
    "schema_version": "0.1-draft",
    "identity": {"namespace": "ghrah", "name": "{name}", "version": "0.1.0"},
    "title": "{name}",
    "description": "{name} 容器。",
    "provider_requirements": {"{name}_backend": {"contract": "ghrah.test.v1"}},
    "operations": {
        "{name}_op": {
            "title": "Op",
            "description": "一个测试成员。",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {}},
            "implementation": {"provider_slot": "{name}_backend", "operation": "{name}_op"},
            "execution": {
                "mode": "request",
                "timeout_ms": 1000,
                "cancellation": "unsupported",
                "on_revoke": "drain",
                "retry": "never",
            },
        }
    },
}


def _write_container(root: Path, name: str) -> Path:
    """在给定目录下造一个最小能力容器（定义面 + 实现脚本）。

    用于构造「磁盘上出现/消失一个容器」的场景，不依赖真实包树。

    Args:
        root: 容器所在目录（相当于 ``ability/``）。
        name: 容器名（同时用作成员键前缀）。

    Returns:
        新建的容器目录。
    """
    container = root / name
    operations = container / "operations"
    operations.mkdir(parents=True)
    (container / "ability.yaml").write_text(
        yaml.safe_dump(
            yaml.safe_load(yaml.safe_dump(DEFINITION).replace("{name}", name)),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    (operations / "__init__.py").write_text("", encoding="utf-8", newline="\n")
    (operations / f"{name}_op.py").write_text(
        "from ghrah.scheme.execution.base import Ability\n"
        "\n"
        "\n"
        f"class {name.title()}OpAbility(Ability):\n"
        f'    """测试实现。"""\n'
        "\n"
        "    @property\n"
        "    def name(self) -> str:\n"
        f'        return "{name}_op"\n',
        encoding="utf-8",
        newline="\n",
    )
    return container


@pytest.fixture
def temp_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把内置扫描根换到临时目录（等价于换一套内置能力树）。

    「内置根在哪」现在由目录池 ``ability_roots.builtin_root()`` 给出，因此补丁
    打在那一处：``ability_catalog.builtin_pool()`` 每次都重新取，换根即生效。

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest 的补丁器，用于替换 ``ability_roots.builtin_root``。

    Returns:
        已写入 ``alpha`` 容器的临时扫描根。
    """
    _write_container(tmp_path, "alpha")
    monkeypatch.setattr(
        ability_roots,
        "builtin_root",
        lambda: ability_roots.path_root(
            tmp_path,
            rank=ability_roots.BUILTIN_ROOT_RANK,
            source=ability_roots.BUILTIN_ROOT_SOURCE,
            required=True,
        ),
    )
    return tmp_path


class TestHotPlug:
    """查询永远反映磁盘：无缓存、无 import 期快照。

    **边界（如实记录）**：热插拔对**定义面**成立——新增/修改/删除容器的
    ``ability.yaml``，下一次查询即反映到定义、handler 名单与 view。但**实现面**
    是按点分包路径 import 的（``ghrah.scheme.ability.<容器>.operations.<成员>``），
    因此新能力的实现脚本必须落在可 import 的包树里，见
    :meth:`test_projection_requires_importable_module`。
    """

    def test_new_container_visible_on_next_query(self, temp_package: Path) -> None:
        """新增容器在下一次查询即生效（定义 + handler 名单）。"""
        assert catalog.load_ability_definitions().keys() == {"alpha"}

        _write_container(temp_package, "beta")

        assert catalog.load_ability_definitions().keys() == {"alpha", "beta"}
        assert catalog.builtin_handlers() == ("alpha_op", "beta_op")
        assert catalog.has_handler("beta_op")

    def test_edited_definition_visible_on_next_query(self, temp_package: Path) -> None:
        """改动定义面文本在下一次查询即生效（无缓存、无快照）。"""
        before = catalog.load_ability_definitions()["alpha"]
        assert before.description == "alpha 容器。"

        path = temp_package / "alpha" / "ability.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("alpha 容器。", "改过的说明。"),
            encoding="utf-8",
            newline="\n",
        )

        assert catalog.load_ability_definitions()["alpha"].description == "改过的说明。"

    def test_removed_container_disappears(self, temp_package: Path) -> None:
        """删掉定义面后，该容器的成员从 handler 名单消失。"""
        assert "alpha_op" in catalog.builtin_handlers()

        (temp_package / "alpha" / "ability.yaml").unlink()

        assert catalog.builtin_handlers() == ()

    def test_projection_requires_importable_module(self, temp_package: Path) -> None:
        """实现面按点分包路径 import：包外的容器解析不出实现类。

        这不是缓存问题，而是"实现脚本必须在可 import 的包树里"这一约束的显式化
        ——错误信息会指名缺哪个模块，不静默跳过。
        """
        from ghrah.scheme.loading.manifest_fields import ManifestValidationError

        with pytest.raises(ManifestValidationError, match="无法装载实现脚本"):
            catalog.load_all_builtin_manifests()

    def test_no_import_time_snapshot_symbol(self) -> None:
        """import 期快照（``_BUILTIN_YAML_NAMES``）已删除：名单只能现算。"""
        assert not hasattr(catalog, "_BUILTIN_YAML_NAMES")

    def test_scan_results_are_independent_objects(self, temp_package: Path) -> None:
        """每次返回新对象：调用方拿到的映射不会被后续扫描改写。"""
        first = catalog.load_ability_definitions()
        second = catalog.load_ability_definitions()

        assert first == second
        assert first is not second


class TestScanApi:
    """对外 API：外层框架直接可调，不经模型工具面。"""

    def test_builtin_handlers_includes_registry_only(self) -> None:
        """``send`` 是设计上的 registry-only 成员：不进工具面，但必须可解析。"""
        handlers = catalog.builtin_handlers()

        assert "send" in handlers
        assert "ghrah.cluster.send" not in catalog.load_all_builtin_manifests()
        assert handlers == tuple(sorted(handlers))

    def test_handler_and_full_name_universes_differ_by_registry_only(self) -> None:
        """handler 全集与声明面全集只差 registry-only 成员（``send``）。"""
        handlers = set(catalog.builtin_handlers())
        declared = {name.rsplit(".", 1)[1] for name in catalog.load_all_builtin_manifests()}

        assert declared - handlers == set()
        assert handlers - declared == {"send"}

    def test_create_ability_resolves_by_scan(self) -> None:
        """``create_ability`` 无需任何注册即可解出实现类。"""
        ability = catalog.create_ability("conversation")

        assert type(ability).__name__ == "ConversationAbility"

    def test_unknown_handler_raises(self) -> None:
        """未知 handler 报 ManifestValidationError（fail-closed，不静默）。"""
        from ghrah.scheme.loading.manifest_fields import ManifestValidationError

        with pytest.raises(ManifestValidationError, match="未知的 ability handler"):
            catalog.create_ability("no_such_handler")

    def test_ability_class_is_the_script_class(self) -> None:
        """``ability_class`` 取到的就是实现脚本里那个类（不实例化）。"""
        cls = catalog.ability_class("read_file")

        assert cls.__name__ == "ReadFileAbility"

    def test_c_class_yaml_loader_has_a_fallback(self) -> None:
        """C loader 与纯 Python loader 语义一致（后者只是慢）。"""
        raw = "a: 1\nb: [1, 2]\n"

        assert catalog.load_yaml(raw) == {"a": 1, "b": [1, 2]}
