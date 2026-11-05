# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""legacy 装配模式的废弃边界：仍可运行，但明确不在权限治理范围内。"""

from __future__ import annotations

import logging

from ghrah.ability.assembly import AbilityAssembly


def test_legacy_select_warns_deprecated_and_out_of_permission_governance(caplog):
    """选择 legacy 时留下明确废弃告警，并点名权限治理只在 builtin 生效。"""
    with caplog.at_level(logging.WARNING, logger="ghrah.ability.assembly"):
        assembly = AbilityAssembly.select("legacy")

    assert assembly.is_legacy
    messages = [record.getMessage() for record in caplog.records]
    deprecations = [message for message in messages if "已废弃" in message]
    assert deprecations, messages
    warning = deprecations[0]
    assert "权限治理" in warning and "builtin" in warning


def test_builtin_select_has_no_deprecation_warning(caplog):
    """builtin 是受治理路径，不产生废弃告警。"""
    with caplog.at_level(logging.WARNING, logger="ghrah.ability.assembly"):
        assembly = AbilityAssembly.select("builtin")

    assert assembly.is_builtin
    assert not [record for record in caplog.records if "已废弃" in record.getMessage()]
