# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``python -m ghrah.ability.tools.build`` 的模块入口（转调 :mod:`build` 的 main）。"""

from __future__ import annotations

import sys

from ghrah.ability.tools.build import main

if __name__ == "__main__":
    sys.exit(main())
