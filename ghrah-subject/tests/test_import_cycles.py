# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""从冷入口导入 Subject 子模块时，验证其依赖边界。"""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("entry", "unloaded"),
    [
        ("agents.creation", "project"),
        ("unit", "runtime"),
        ("unit.base", "runtime"),
        ("units.shipped_abilities", "runtime"),
    ],
)
def test_child_import_without_subject_preloading(entry: str, unloaded: str) -> None:
    """保留根包的搜索路径，跳过公共导出的预加载。"""
    code = """
import importlib
import importlib.util
import sys

import ghrah

spec = importlib.util.find_spec("ghrah.subject")
assert spec is not None
subject = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ghrah.subject = subject

importlib.import_module(f"ghrah.subject.{sys.argv[1]}")
assert f"ghrah.subject.{sys.argv[2]}" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code, entry, unloaded],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_service_key_keeps_a_single_public_type() -> None:
    """旧导入路径和新模块公开同一个类型。"""
    from ghrah.subject import SubjectServiceKey
    from ghrah.subject.runtime.service_keys import SubjectServiceKey as RuntimeKey
    from ghrah.subject.service_key import SubjectServiceKey as Key
    from ghrah.subject.unit import SubjectServiceKey as UnitKey

    assert SubjectServiceKey is RuntimeKey is UnitKey is Key
