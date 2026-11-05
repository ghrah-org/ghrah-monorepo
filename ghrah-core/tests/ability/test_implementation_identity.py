# SPDX-License-Identifier: Apache-2.0
"""实现标识：每个后端固定自己的版本号，不共用文件切片摘要。"""

from __future__ import annotations

import shutil
from pathlib import Path

from ghrah.ability.loading.ability_roots import builtin_root
from ghrah.ability.versions import (
    capture_ability_version,
    implementation_identity,
    loaded_file_implementation,
)


def _package_root(package: str) -> Path:
    root = builtin_root().path
    assert root is not None
    return root / package


def _capture(package: str) -> tuple[str, str]:
    root = _package_root(package)
    version = capture_ability_version(
        (root / "ability.yaml").read_text(encoding="utf-8"), root=root
    )
    return version.implementation_digest, version.digest


def test_file_backend_keeps_process_locked_identity() -> None:
    implementation, _ = _capture("fs")

    assert implementation == loaded_file_implementation()


def test_each_non_file_backend_has_its_own_identity() -> None:
    implementations = {
        package: _capture(package)[0] for package in ("shell", "cluster", "core", "mcp")
    }

    # 四个非文件后端各自一个实现标识，且都不再冒充文件后端的版本号
    assert len(set(implementations.values())) == len(implementations)
    assert loaded_file_implementation() not in implementations.values()


def test_backend_identity_changes_with_its_operation_code(tmp_path: Path) -> None:
    copy = tmp_path / "shell"
    shutil.copytree(_package_root("shell"), copy)
    before = capture_ability_version(
        (copy / "ability.yaml").read_text(encoding="utf-8"), root=copy
    ).implementation_digest

    operation = copy / "operations" / "execute_command.py"
    operation.write_text(
        operation.read_text(encoding="utf-8") + "\n# implementation drift\n", encoding="utf-8"
    )
    after = capture_ability_version(
        (copy / "ability.yaml").read_text(encoding="utf-8"), root=copy
    ).implementation_digest

    assert after != before


def test_identity_of_another_backend_does_not_follow_shell_code(tmp_path: Path) -> None:
    shell_copy = tmp_path / "shell"
    shutil.copytree(_package_root("shell"), shell_copy)
    before_cluster = _capture("cluster")[0]

    operation = shell_copy / "operations" / "execute_command.py"
    operation.write_text(
        operation.read_text(encoding="utf-8") + "\n# shell drift\n", encoding="utf-8"
    )
    after_cluster = _capture("cluster")[0]

    assert after_cluster == before_cluster


def test_package_with_file_members_stays_on_file_backend_identity() -> None:
    # 含文件成员时，文件动作的可执行性依赖与进程锁定实现同一版本号；
    # 包里的其他（尚未实现的）后端由内容清单与定义面表达。
    root = Path(__file__).resolve().parents[2] / "examples" / "ability_content" / "reporting"
    version = capture_ability_version(
        (root / "ability.yaml").read_text(encoding="utf-8"), root=root
    )

    assert version.implementation_digest == loaded_file_implementation()
    definition = version.definition()
    assert implementation_identity(definition, root=root) == loaded_file_implementation()


def test_content_only_package_uses_content_identity() -> None:
    root = _package_root("ability-builder")
    version = capture_ability_version(
        (root / "ability.yaml").read_text(encoding="utf-8"), root=root
    )

    assert version.implementation_digest == "content-only:v1"
