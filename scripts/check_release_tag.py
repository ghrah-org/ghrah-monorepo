#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""校验包级发布 tag 的版本与目标包声明完全一致。"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "py/ghrah-core": ("python", ROOT / "ghrah-core" / "pyproject.toml"),
    "py/ghrah-plugin": ("python", ROOT / "ghrah-plugin" / "pyproject.toml"),
    "py/ghrah-protocol": ("python", ROOT / "ghrah-protocol" / "pyproject.toml"),
    "py/ghrah-subject": ("python", ROOT / "ghrah-subject" / "pyproject.toml"),
    "py/ghrah-taskstore": ("python", ROOT / "ghrah-taskstore" / "pyproject.toml"),
    "npm/ghrah-protocol": (
        "node",
        ROOT / "ghrah-observer-webui" / "packages" / "protocol" / "package.json",
    ),
    "npm/ghrah-observer-core": (
        "node",
        ROOT / "ghrah-observer-webui" / "packages" / "observer-core" / "package.json",
    ),
    "vscode/ghrah-vscode-extension": (
        "node",
        ROOT / "ghrah-observer-webui" / "vscode-extension" / "package.json",
    ),
}
PEP440_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:a|b|rc)?[0-9]*(?:\.post[0-9]+)?$")
SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _declared_version(kind: str, path: Path) -> str:
    if kind == "python":
        with path.open("rb") as stream:
            return str(tomllib.load(stream)["project"]["version"])
    return str(json.loads(path.read_text(encoding="utf-8"))["version"])


def main() -> int:
    """验证目标、版本语法与清单版本，失败时返回非零。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    kind, path = TARGETS[args.target]
    pattern = PEP440_PATTERN if kind == "python" else SEMVER_PATTERN
    if not pattern.fullmatch(args.version):
        parser.error(f"{args.target} 的版本语法无效：{args.version}")

    declared = _declared_version(kind, path)
    if declared != args.version:
        parser.error(
            f"tag 版本与包清单不一致：target={args.target}, tag={args.version}, declared={declared}"
        )
    print(f"release tag validated: {args.target}@{args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
