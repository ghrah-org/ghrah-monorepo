# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 语义基线门禁自身的测试。

门禁脚本在 ``scripts/check_core_semantics.py``：它把「Core 不持有 Subject 语义」
变成只降不升的基线。这里验证三件事——当前树通过、新增泄漏被拦住、import 方向
违规被拦住——以及 ``--update`` 写出的基线可回读。
"""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_core_semantics.py"


def _gate() -> dict:
    """以脚本形态加载门禁模块（不执行 ``main``）。"""
    return runpy.run_path(str(SCRIPT))


def test_gate_passes_on_current_core_tree() -> None:
    """与 CI 同一条命令：当前 ``ghrah-core/src`` 不得有新增泄漏。"""
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_gate_detects_new_symbol_occurrence(tmp_path: Path) -> None:
    """基线里没有的符号出现即失败（新增泄漏）。"""
    gate = _gate()
    root = tmp_path / "src"
    (root / "ghrah" / "core").mkdir(parents=True)
    (root / "ghrah" / "core" / "leak.py").write_text(
        "def payload() -> dict[str, str]:\n    return {'project_id': 'p'}\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"schema": 1, "counts": {}}), encoding="utf-8")

    result = gate["check"](root=root, baseline_path=baseline)

    assert result["violations"]
    assert any("project_id" in item and "新增" in item for item in result["violations"])


def test_gate_detects_forbidden_import_direction(tmp_path: Path) -> None:
    """Core 源码 import Subject 属于依赖方向违规。"""
    gate = _gate()
    root = tmp_path / "src"
    (root / "ghrah" / "core").mkdir(parents=True)
    (root / "ghrah" / "core" / "bad.py").write_text(
        "from ghrah.subject.units import something\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"schema": 1, "counts": {}}), encoding="utf-8")

    result = gate["check"](root=root, baseline_path=baseline)

    assert any("import 方向违规" in item for item in result["violations"])


def test_gate_update_then_check_roundtrips(tmp_path: Path) -> None:
    """``--update`` 写出的基线可被再次读取，且同树立刻通过。"""
    gate = _gate()
    root = tmp_path / "src"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("value = {'room_id': 'r'}\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"

    assert gate["main"](["--update", "--root", str(root), "--baseline", str(baseline)]) == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["counts"]["room_id(s)"]["pkg/mod.py"] == 1

    assert gate["check"](root=root, baseline_path=baseline)["violations"] == []

    # 再增长一处即为违规（只降不升）
    (root / "pkg" / "mod.py").write_text(
        "x = {'room_id': 'r'}\ny = {'room_ids': []}\n", encoding="utf-8"
    )
    violations = gate["check"](root=root, baseline_path=baseline)["violations"]
    assert any("room_id(s)" in item and "增加" in item for item in violations)
