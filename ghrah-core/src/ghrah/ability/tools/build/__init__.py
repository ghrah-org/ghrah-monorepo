# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``python -m ghrah.ability.tools.build <dir> [--check] [--builtin]``。

``--check`` 只校验不写盘，索引与声明不一致时以非零退出并输出统一 diff；
``--builtin`` 允许内置包的 ``ghrah`` 命名空间。后续正式命令的 CLI 薄层归
``ghrah-toolchain``。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ghrah.ability.package.generate import BuildError, IndexMismatchError, build, check

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """CLI 入口；返回进程退出码。"""
    parser = argparse.ArgumentParser(
        prog="ghrah.ability.tools.build",
        description="按包内声明生成 ability.yaml 的 members 段",
    )
    parser.add_argument("directory", type=Path, help="源包根目录（含 ability.yaml）")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验不写盘；索引与声明不一致时非零退出并输出 diff",
    )
    parser.add_argument(
        "--builtin",
        action="store_true",
        help="允许内置包的 ghrah 命名空间",
    )
    args = parser.parse_args(argv)
    root: Path = args.directory
    if not root.is_dir():
        print(f"error: {root} 不是目录", file=sys.stderr)
        return 2
    try:
        result = (
            check(root, builtin=args.builtin) if args.check else build(root, builtin=args.builtin)
        )
    except IndexMismatchError as exc:
        for difference in exc.differences:
            print(f"mismatch: {difference}", file=sys.stderr)
        return 1
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    status = "written" if result.changed else "ok (unchanged)"
    print(f"{root}: {status} index_digest={result.index_digest}")
    return 0
