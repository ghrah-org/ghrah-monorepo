# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``python -m ghrah.ability.tools.cli`` 的模块入口（转调 :func:`cli.main`）。"""

from __future__ import annotations

import sys

from ghrah.ability.tools.cli import main

if __name__ == "__main__":
    sys.exit(main())
