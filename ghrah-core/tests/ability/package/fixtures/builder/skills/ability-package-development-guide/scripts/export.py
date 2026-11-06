# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""自举 fixture 的委托脚本：验证 scripts 只登记为内容资源、不生成 Operation。"""

from __future__ import annotations

import json
import sys


def main() -> None:
    """按脚本结果信封输出一个成功结果。"""
    payload = {"ok": True, "data": {"guide": "ability-package-development-guide"}}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
