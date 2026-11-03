# SPDX-License-Identifier: Apache-2.0
"""生成区调试：扫描会写出什么、磁盘上的 `members` 是否已同步。

只读：不写任何文件（要写就用 ``python -m ghrah.ability.build <包目录>``）。

运行（仓库根）：``uv run python ghrah-core/examples/ability_debug/02_build.py``
"""

from __future__ import annotations

import json
from pathlib import Path

from ghrah.ability.build import scan_members, write_members

REPO = Path(__file__).resolve().parents[3]
PACKAGES = {
    "内置 fs": REPO / "ghrah-core/src/ghrah/ability/builtin/fs",
    "示例 reporting": REPO / "ghrah-core/examples/ability_content/reporting",
}


def main() -> None:
    """对两个真实包做一次 dry-run 生成检查。"""
    for label, package in PACKAGES.items():
        print(f"== {label}: {package}")
        if not (package / "ability.yaml").is_file():
            print("   ！（缺 ability.yaml，跳过）\n")
            continue
        members = scan_members(package)
        print("   scan_members →")
        print("   " + json.dumps(members, ensure_ascii=False, indent=2).replace("\n", "\n   "))

        result = write_members(package, dry_run=True)
        state = "需要重建" if result.changed else "已同步"
        print(f"   write_members(dry_run) → {state}（未写入磁盘）\n")


if __name__ == "__main__":
    main()
