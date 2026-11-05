# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Skill 脚本运行器。脚本不是 Operation，只通过 ``execute_run_script`` 调用。"""

from ghrah.ability.scripts.runner import execute_run_script

__all__ = ["execute_run_script"]
