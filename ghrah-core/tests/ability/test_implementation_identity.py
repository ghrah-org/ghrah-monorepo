# SPDX-License-Identifier: Apache-2.0
"""实现标识：每个后端固定自己的版本号，不共用文件切片摘要。

v1 的内容包没有 Operation，实现标识固定为 ``content-only:v1``；外部源包的
Operation 由安装后的进程内 Provider 从包内 ``operations/*.py`` 加载，实现标识
随该目录的代码变化（P1 只登记，不执行）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ghrah.ability.loading.ability_roots import builtin_root
from ghrah.ability.versions import (
    capture_ability_version,
    implementation_identity,
    loaded_file_implementation,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "ability_content" / "reporting"
"""示例包：v1 单 Skill 内容包，``members.operations`` 为空。"""

EXTERNAL_SOURCE = Path(__file__).resolve().parent / "package" / "fixtures" / "multi-skill"
"""外部源包 fixture：1 个 Operation 与 2 个 Skill；复制到临时目录后 capture。"""


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


def test_example_content_package_uses_content_identity() -> None:
    """示例包只有内容成员：实现标识是 ``content-only:v1``，不是文件后端版本。"""
    version = capture_ability_version(
        (EXAMPLE / "ability.yaml").read_text(encoding="utf-8"), root=EXAMPLE
    )

    assert not version.definition().operations
    assert version.implementation_digest == "content-only:v1"
    assert implementation_identity(version.definition(), root=EXAMPLE) == "content-only:v1"


def test_external_source_package_identity_follows_operation_code(tmp_path: Path) -> None:
    """外部源包的 Operation 由进程内 Provider 加载：实现标识随包内代码变化。"""
    copy = tmp_path / "multi-skill"
    shutil.copytree(EXTERNAL_SOURCE, copy)
    before = capture_ability_version((copy / "ability.yaml").read_text(encoding="utf-8"), root=copy)
    assert set(before.definition().operations) == {"render"}
    assert before.implementation_digest != loaded_file_implementation()
    assert implementation_identity(before.definition(), root=copy) == before.implementation_digest

    operation = copy / "operations" / "render.py"
    operation.write_text(
        operation.read_text(encoding="utf-8") + "\n# implementation drift\n", encoding="utf-8"
    )
    after = capture_ability_version((copy / "ability.yaml").read_text(encoding="utf-8"), root=copy)

    assert after.implementation_digest != before.implementation_digest
    assert after.digest != before.digest


def test_content_only_package_uses_content_identity() -> None:
    root = _package_root("ability-builder")
    version = capture_ability_version(
        (root / "ability.yaml").read_text(encoding="utf-8"), root=root
    )

    assert version.implementation_digest == "content-only:v1"
