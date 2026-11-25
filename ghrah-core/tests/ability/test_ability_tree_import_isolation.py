# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""本地能力接线在缺少可选运行时依赖时的导入隔离。"""

from __future__ import annotations

import subprocess
import sys


def test_local_granted_content_imports_without_scheme_or_kernel() -> None:
    """本地生产接线不依赖两个可选运行时，也不在导入时构造 Provider。"""
    code = """
import sys
sys.modules['ghrah.scheme'] = None
sys.modules['ghrah_kernel'] = None
from ghrah.ability.local_content import LocalGrantedContentProvider
from ghrah.ability.builtin.core.operations.load_skill import LoadSkillAbility
assert LoadSkillAbility().name == 'load_skill'
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
