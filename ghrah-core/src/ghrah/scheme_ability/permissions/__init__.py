# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""权限主题：审批闸门、命令安全分类、路径约束。

本包只判定「这次调用是否被允许、是否需要人工批准」，不做执行；
执行侧的接线在 ``ghrah.scheme_ability.execution``。
"""

from __future__ import annotations
