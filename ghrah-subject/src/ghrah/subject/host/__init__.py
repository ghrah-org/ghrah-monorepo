# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""独立微内核的系统宿主；不装配旧 Subject 运行时。"""

from .kernel_process import Bootstrap

__all__ = ["Bootstrap", "main"]


def main() -> None:
    """启动系统宿主。"""
    from .app import main as run

    run()
