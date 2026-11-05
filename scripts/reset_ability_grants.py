# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""离线重建授权前的显式维护入口；默认只列范围，--apply 时先备份整库。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ghrah.ability.access.maintenance import inspect_ability_domain, reset_ability_domain


def main(argv: list[str] | None = None) -> int:
    """列出 Ability 表；显式应用时要求相关 Agent 已停机及唯一备份路径。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--apply", action="store_true", help="确认相关实例已停机后应用")
    args = parser.parse_args(argv)
    tables = inspect_ability_domain(args.database)
    print(
        json.dumps({"database": str(args.database), "ability_tables": tables}, ensure_ascii=False)
    )
    if not tables:
        print("未发现 Ability 域，无需清理；没有创建备份。")
        return 0
    if args.apply:
        if args.backup is None:
            parser.error("--apply 必须指定尚不存在的 --backup 文件")
        reset_ability_domain(args.database, args.backup)
        print("Ability 域已清理；下次打开使用稳定 Agent ID，须显式重新授予。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
