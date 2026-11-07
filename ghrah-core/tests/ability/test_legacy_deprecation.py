# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力来源只接受 v1 的配置值。"""

from __future__ import annotations

import pytest

from ghrah.ability.assembly import AbilityAssembly, AbilityAssemblyError
from ghrah.ability.source import BuiltinAbilitySource


def test_legacy_selection_is_rejected():
    with pytest.raises(AbilityAssemblyError, match="only builtin"):
        AbilityAssembly.select("legacy")


def test_builtin_selection_uses_v1_source():
    assert isinstance(AbilityAssembly.select("builtin").source, BuiltinAbilitySource)
