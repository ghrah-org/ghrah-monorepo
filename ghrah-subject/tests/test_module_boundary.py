# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""S2 模块边界 ratchet：`ghrah-subject` 不得依赖 `ghrah-core`。

背景（见 ``plans/planning/core-subject-rest-api.md`` §10）：目标是**模块化单体**——
不拆服务、不分进程，只取消 import 并把跨模块通信改为 API。进程内没有物理边界，
所以**这条测试就是那道墙**（§10.1）。

做法是 **ratchet（只降不升）**，不是「一次断言为零」：

- 现有违规记进 ``module_boundary_baseline.json``；
- 新增违规 → 失败；
- 违规减少但基线没同步下调 → 也失败（强制把基线往下推，防止它烂在原地）。

更新基线：

.. code-block:: powershell

    $env:MODULE_BOUNDARY_UPDATE = "1"; uv run pytest ghrah-subject/tests/test_module_boundary.py -q

已知覆盖缺口：本测试只检查 **静态 import**。``importlib.import_module`` /
``__import__`` / 字符串拼模块名抓不到——这是有意的取舍（AST 可解释、零误报），
动态导入需要靠 code review 拦住。
"""

from __future__ import annotations

import ast
import json
import os
from collections import Counter
from pathlib import Path

import pytest

__all__ = ["test_baseline_is_not_stale", "test_no_new_core_imports"]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "ghrah" / "subject"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
BASELINE = Path(__file__).resolve().parent / "module_boundary_baseline.json"

UPDATE_ENV = "MODULE_BOUNDARY_UPDATE"
PYPROJECT_KEY = "pyproject.toml::ghrah-core"

#: 被禁止的 Core 命名空间前缀（D5/§10.4 的目标：这些必须归零）。
#:
#: 注意 ``ghrah.ability`` 会同时命中 Core 的 ``ghrah.abilities``——
#: 两者都属于 Core，命中是期望行为。
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "ghrah.core",
    "ghrah.context",
    "ghrah.communication",
    "ghrah.ability",
    "ghrah.agents",
    "ghrah.manifest",
    "ghrah.types",
)


def _is_forbidden(module: str) -> bool:
    """判断模块名是否落在被禁止的 Core 命名空间内。

    Args:
        module: 完整模块名（如 ``ghrah.context.node``）

    Returns:
        是否被禁止
    """
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)


def _iter_source_files() -> list[Path]:
    """列出 Subject 包内全部 .py 源文件。

    Returns:
        按路径排序的源文件列表
    """
    if not PACKAGE_ROOT.is_dir():
        raise FileNotFoundError(f"package root missing: {PACKAGE_ROOT}")
    return sorted(path for path in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _imported_modules(path: Path) -> set[str]:
    """解析一个源文件的**绝对** import 模块名。

    相对 import（``from . import x``）属于包内引用，直接跳过。

    Args:
        path: 源文件路径

    Returns:
        该文件导入的绝对模块名集合
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _pyproject_requires_core() -> bool:
    """判断 ``ghrah-subject`` 的依赖列表是否仍声明 ``ghrah-core``。

    Returns:
        是否仍依赖 ``ghrah-core``
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    return '"ghrah-core' in text or "'ghrah-core" in text


def collect_violations() -> dict[str, int]:
    """统计当前全部边界违规。

    Returns:
        ``{"<相对路径>::<模块>": 次数}``；``pyproject`` 依赖记为单独的键
    """
    counts: Counter[str] = Counter()
    for path in _iter_source_files():
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        for module in _imported_modules(path):
            if _is_forbidden(module):
                counts[f"{relative}::{module}"] += 1
    if _pyproject_requires_core():
        counts[PYPROJECT_KEY] += 1
    return dict(sorted(counts.items()))


def _load_baseline() -> dict[str, int]:
    """读取基线文件。

    Returns:
        基线中的违规计数

    Raises:
        FileNotFoundError: 基线不存在（需先用 ``MODULE_BOUNDARY_UPDATE=1`` 生成）
        ValueError: 基线格式不合法
    """
    if not BASELINE.is_file():
        raise FileNotFoundError(
            f"missing baseline: {BASELINE}\n"
            f"生成：$env:{UPDATE_ENV}='1'; uv run pytest {Path(__file__).name} -q",
        )
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{BASELINE}: baseline must be a JSON object")
    violations = raw.get("violations")
    if not isinstance(violations, dict):
        raise ValueError(f"{BASELINE}: missing object field 'violations'")
    result: dict[str, int] = {}
    for key, value in violations.items():
        if not isinstance(key, str) or not isinstance(value, int):
            raise ValueError(f"{BASELINE}: violations must map str -> int (got {key!r})")
        result[key] = value
    return result


def _save_baseline(violations: dict[str, int]) -> None:
    """写入基线文件（按 key 排序、LF 结尾）。

    Args:
        violations: 当前违规计数
    """
    payload = {
        "note": (
            "S2 模块边界 ratchet 基线：ghrah-subject 不得依赖 ghrah-core。"
            "只降不升；减少后用 MODULE_BOUNDARY_UPDATE=1 下调。"
            "见 plans/planning/core-subject-rest-api.md §10。"
        ),
        "forbidden_prefixes": list(FORBIDDEN_PREFIXES),
        "violations": dict(sorted(violations.items())),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    BASELINE.write_text(text + "\n", encoding="utf-8")


def test_no_new_core_imports() -> None:
    """不得新增对 Core 命名空间的 import（S2）。"""
    current = collect_violations()
    if os.environ.get(UPDATE_ENV) == "1":
        _save_baseline(current)
        pytest.skip(f"baseline updated: {len(current)} violation(s)")

    baseline = _load_baseline()
    regressions: list[str] = []
    for key, count in current.items():
        allowed = baseline.get(key, 0)
        if count > allowed:
            regressions.append(f"{key}: {count} > baseline {allowed}")

    assert not regressions, (
        "S2 违规新增（ghrah-subject 不得 import ghrah-core 命名空间）：\n  "
        + "\n  ".join(regressions)
        + "\n\n修复方向见 plans/planning/core-subject-rest-api.md §10.3/§10.4：\n"
        "  - 权威/状态  → 走端口能力（CorePort / SubjectPort）\n"
        "  - DTO/纯函数 → 搬 ghrah-protocol，不要 API 化\n"
    )


def test_baseline_is_not_stale() -> None:
    """基线必须跟随实际违规下降（否则 ratchet 会烂在原地）。"""
    if os.environ.get(UPDATE_ENV) == "1":
        pytest.skip("baseline update mode")

    current = collect_violations()
    baseline = _load_baseline()
    stale = {
        key: (allowed, current.get(key, 0))
        for key, allowed in baseline.items()
        if current.get(key, 0) < allowed
    }

    assert not stale, (
        "基线偏大，请下调（违规已减少）：\n  "
        + "\n  ".join(
            f"{key}: baseline {allowed} -> actual {actual}"
            for key, (allowed, actual) in sorted(stale.items())
        )
        + "\n\n下调：$env:MODULE_BOUNDARY_UPDATE='1'; uv run pytest "
        + f"{Path(__file__).name} -q"
    )


def test_baseline_covers_known_core_namespaces() -> None:
    """基线声明的禁止前缀必须覆盖 §10.4 清单（防止有人悄悄放宽范围）。"""
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    declared = raw.get("forbidden_prefixes")
    assert isinstance(declared, list), "baseline must declare forbidden_prefixes"
    assert set(FORBIDDEN_PREFIXES) == set(declared), (
        "禁止前缀与基线不一致：改动 FORBIDDEN_PREFIXES 时必须同步更新基线，"
        "避免「放宽范围让测试变绿」"
    )
