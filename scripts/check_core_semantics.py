# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 语义基线门禁：Core 运行时面不得新增 Subject 语义。

这是第二阶段（Core 适配）的防回归闸门，把「Core 不持有 Subject 语义」从一句
原则变成**可度量、只降不升**的基线：

1. 硬规则：``ghrah-core/src`` 不得 import ``ghrah.subject``（依赖方向）。
2. 基线：现存的 `project_id` / `room_id(s)` / `manifest_ref` 等 Subject 语义出现
   次数记入 ``scripts/core-semantics-baseline.json``；只允许下降，收紧后再用
   ``--update`` 重写基线。
3. 新增即失败：基线里没有的（符号, 文件）组合出现次数 > 0，一律判为新增泄漏。

为什么是基线而不是直接禁止：迁移是分轮的（见
``dev-docs/analysis/2026-11-14-core-adaptation-change-scope.md``），存量必须能
逐轮下降，不能一夜清空；基线让「每轮只降不升」可被 CI 校验。

用法::

    python scripts/check_core_semantics.py --check     # 门禁（CI / 本地）
    python scripts/check_core_semantics.py --update    # 存量下降后收紧基线
    python scripts/check_core_semantics.py --summary   # 打印当前分布

退出码：0 = 通过；1 = 有违规（新增泄漏或 import 方向违规）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "ghrah-core" / "src"
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "core-semantics-baseline.json"
BASELINE_SCHEMA = 1

#: Subject 语义符号 → 正则。名称只用于报告与基线键。
SYMBOLS: dict[str, str] = {
    "project_id": r"\bproject_id\b",
    "project": r"\bproject\b",
    "room_id(s)": r"\broom_ids?\b",
    "room_projector": r"\broom_projector\b",
    "scope_ref": r"\bscope_ref\b",
    "external_ref": r"\bexternal_ref\b",
    "manifest_ref": r"\bmanifest_ref\b",
    "manifest_store": r"\bmanifest_store\b",
    "shell_cwd_provider": r"\bshell_cwd_provider\b",
    "SubjectCommandBridge": r"SubjectCommandBridge",
}

#: 硬规则：Core 源码不得出现的依赖方向（正则，逐行匹配）。
FORBIDDEN_IMPORTS: tuple[tuple[str, str], ...] = (
    ("ghrah.subject import", r"^\s*(?:from|import)\s+ghrah\.subject\b"),
)


def scan(root: Path) -> dict[str, dict[str, int]]:
    """统计 ``root`` 下每个文件命中的符号次数。

    Returns:
        ``{符号名: {相对路径: 次数}}``，只保留次数 > 0 的条目。
    """
    compiled = {name: re.compile(pattern) for name, pattern in SYMBOLS.items()}
    counts: dict[str, dict[str, int]] = {name: {} for name in SYMBOLS}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root).as_posix()
        for name, pattern in compiled.items():
            n = len(pattern.findall(text))
            if n:
                counts[name][rel] = n
    return {name: files for name, files in counts.items() if files}


def scan_forbidden_imports(root: Path) -> list[str]:
    """检查硬规则，返回违规定位（``相对路径:行号:规则``）。"""
    compiled = [(label, re.compile(pattern)) for label, pattern in FORBIDDEN_IMPORTS]
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for label, pattern in compiled:
                if pattern.search(line):
                    violations.append(f"{rel}:{lineno}: {label}")
    return violations


def load_baseline(baseline_path: Path) -> dict[str, dict[str, int]]:
    """读取基线；文件缺失视为空基线（即当前一切都算新增）。"""
    if not baseline_path.is_file():
        return {}
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    counts = data.get("counts", {})
    if not isinstance(counts, dict):
        raise SystemExit(f"基线格式错误（counts 不是对象）: {baseline_path}")
    return counts


def write_baseline(baseline_path: Path, counts: dict[str, dict[str, int]]) -> None:
    """把当前分布写成基线（键排序，便于 diff）。"""
    payload = {
        "schema": BASELINE_SCHEMA,
        "generated_by": "scripts/check_core_semantics.py --update",
        "note": (
            "Core 运行时面的 Subject 语义存量基线。只降不升：新增泄漏请先迁移，"
            "不要直接 --update。下降后可用 --update 收紧。"
        ),
        "symbols": SYMBOLS,
        "counts": {name: dict(sorted(files.items())) for name, files in sorted(counts.items())},
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check(root: Path, baseline_path: Path) -> dict[str, list[str]]:
    """执行门禁，返回 ``{"violations": [...], "reductions": [...]}``。"""
    current = scan(root)
    baseline = load_baseline(baseline_path)
    violations: list[str] = []
    reductions: list[str] = []

    symbols = sorted(set(current) | set(baseline))
    for name in symbols:
        base_files = baseline.get(name, {})
        cur_files = current.get(name, {})
        for rel in sorted(set(base_files) | set(cur_files)):
            before = base_files.get(rel, 0)
            now = cur_files.get(rel, 0)
            if now > before:
                tag = "新增" if before == 0 else "增加"
                violations.append(
                    f"{name:22s} {rel}: 基线 {before} → 当前 {now}（{tag} {now - before}）"
                )
            elif now < before:
                reductions.append(
                    f"{name:22s} {rel}: 基线 {before} → 当前 {now}（-{before - now}）"
                )

    violations.extend(f"import 方向违规 {item}" for item in scan_forbidden_imports(root))
    return {"violations": violations, "reductions": reductions}


def _totals(counts: dict[str, dict[str, int]]) -> dict[str, int]:
    return {name: sum(files.values()) for name, files in counts.items()}


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="检查是否有新增泄漏（默认）")
    group.add_argument("--update", action="store_true", help="用当前分布重写基线")
    group.add_argument("--summary", action="store_true", help="只打印当前分布")
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT, help="被扫描的源码根（默认 ghrah-core/src）"
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="基线文件（默认 scripts/core-semantics-baseline.json）",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    baseline_path = args.baseline.resolve()
    if not root.is_dir():
        print(f"扫描根不存在: {root}", file=sys.stderr)
        return 2

    current = scan(root)
    totals = _totals(current)
    print(f"Core 语义扫描：{root}")
    print("当前分布：" + "，".join(f"{name} {n}" for name, n in sorted(totals.items())))

    if args.update:
        write_baseline(baseline_path, current)
        print(f"已写入基线: {baseline_path}")
        return 0

    if args.summary:
        baseline = load_baseline(baseline_path)
        base_totals = _totals(baseline)
        for name in sorted(set(totals) | set(base_totals)):
            print(
                f"  {name:22s} 基线 {base_totals.get(name, 0):4d} → 当前 {totals.get(name, 0):4d}"
            )
        return 0

    result = check(root, baseline_path)
    if result["reductions"]:
        print(f"\n较基线下降 {len(result['reductions'])} 处（可用 --update 收紧基线）：")
        for item in result["reductions"]:
            print(f"  {item}")
    if result["violations"]:
        print(f"\n违规 {len(result['violations'])} 处（Core 不得新增 Subject 语义）：")
        for item in result["violations"]:
            print(f"  {item}")
        print("\n修复方式：把该语义迁回 Subject / 边缘适配层，而不是 --update 抬基线。")
        return 1
    print("\n通过：无新增 Subject 语义，import 方向正确。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
