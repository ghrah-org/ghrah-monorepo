# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""导入边界：``ghrah.subject`` 的公共导出是惰性绑定。

最小宿主入口只依赖 ``server.*`` 等轻量子模块，不得因为父包初始化而加载
Ouroboros 装配栈、``runtime.assembly`` 或旧 ``units``。这三条是 runtime 切换
的边界约束；导入重量用独立解释器进程断言，避免受本进程已有 import 污染。
"""

from __future__ import annotations

import subprocess
import sys


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )


def test_subject_package_import_stays_lazy() -> None:
    """``import ghrah.subject`` 只建立绑定，不加载装配栈。"""
    code = """
import sys
import ghrah.subject
assert "ouroboros" not in sys.modules, "ghrah.subject 导入时不应加载 Ouroboros"
assert "ghrah.subject.runtime.assembly" not in sys.modules
from ghrah.subject import SubjectConfig
assert SubjectConfig.__name__ == "SubjectConfig"
"""
    cp = _run(code)
    assert cp.returncode == 0, cp.stderr


def test_light_submodule_import_skips_old_stack() -> None:
    """轻量子模块（server 面）不得拉起旧装配栈与 units。"""
    code = """
import sys
from ghrah.subject.server.connection_manager import ConnectionManager
assert ConnectionManager is not None
forbidden = [m for m in ("ouroboros", "ghrah.subject.runtime.assembly") if m in sys.modules]
assert not forbidden, forbidden
units = [m for m in sys.modules if m.startswith("ghrah.subject.units")]
assert not units, units
"""
    cp = _run(code)
    assert cp.returncode == 0, cp.stderr


def test_public_names_still_resolve() -> None:
    """惰性化不能破坏原有公共 API：``__all__`` 里每个名字都能解析。"""
    code = """
import ghrah.subject
missing = [name for name in ghrah.subject.__all__ if not hasattr(ghrah.subject, name)]
assert not missing, missing
"""
    cp = _run(code)
    assert cp.returncode == 0, cp.stderr
