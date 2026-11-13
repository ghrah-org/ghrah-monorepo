# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""注入 key：容器键是字符串，这里是它们的唯一真相。"""

from __future__ import annotations

__all__ = ["CORE_PORT", "SUBJECT_PORT"]

CORE_PORT = "core.port"
"""Core 端口在容器中的键。"""

SUBJECT_PORT = "subject.port"
"""Subject 端口在容器中的键。"""
