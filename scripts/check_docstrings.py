# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""docstring 规范检查：文件与函数是否都写清了"干嘛的 + 参数 + 返回值"。

规范（与 AGENTS.md 的"公开函数带类型标注与中文 docstring"配套）：

- **每个文件**开头有模块级 docstring，说明本模块负责什么、边界在哪；
- **每个函数/方法**有 docstring，且：
  - 有参数（不含 ``self`` / ``cls``）→ 含 ``Args:`` 段，逐参说明含义；
  - 有返回值（返回标注不是 None）→ 含 ``Returns:`` 段；
  - 会抛异常 → 建议含 ``Raises:`` 段（本检查不强制）。

例外：零参数的 ``@property`` 与 ``__str__`` / ``__repr__`` 这类"把自身读成值"的
访问器，一句摘要就是在描述返回值，不再强制 ``Returns:``——否则每个属性都要多写
一行同义重复。

用法::

    uv run python scripts/check_docstrings.py                 # 只报缺口，退出码 0/1
    uv run python scripts/check_docstrings.py --path ghrah-core/src/ghrah/skills
    uv run python scripts/check_docstrings.py --changed       # 只查本次改动的文件
    uv run python scripts/check_docstrings.py --summary       # 只出每目录计数

``--path`` 可重复；默认扫 Python 各包的 ``src``。``--changed`` 走 git 列出工作树里
新增/修改的 ``.py``（即"本次增量"），存量债不参与——对应"只管增量代码"的口径。

测试文件的**测试函数**允许只写一句场景说明（pytest fixture 参数不要求 ``Args:``）
——用 ``--allow-test-functions`` 放宽这一条。
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PATHS = (
    REPO_ROOT / "ghrah-core/src",
    REPO_ROOT / "ghrah-plugin/src",
    REPO_ROOT / "ghrah-protocol/src",
    REPO_ROOT / "ghrah-subject/src",
    REPO_ROOT / "ghrah-taskstore/src",
)


def _returns_something(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """该函数是否有返回值（用于决定要不要要求 ``Returns:``）。

    Args:
        node: 被检查的函数/异步函数 AST 节点。

    Returns:
        返回标注存在且不是 ``None`` 时为 True；无标注按"不要求"处理。
    """
    annotation = node.returns
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return False
    return not (isinstance(annotation, ast.Name) and annotation.id == "None")


def _is_generator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断是否生成器函数（体内出现 ``yield`` / ``yield from``）。

    Args:
        node: 被检查的函数 AST 节点。

    Returns:
        是生成器时为 True——此时规范段落是 ``Yields:`` 而不是 ``Returns:``。
    """
    return any(isinstance(inner, (ast.Yield, ast.YieldFrom)) for inner in ast.walk(node))


def _is_value_property(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断是否"把自身读成值"的零参数访问器（属性或 ``__str__`` 之类）。

    这类函数的 docstring 一句摘要就在描述返回值，再要求 ``Returns:`` 只是同义
    重复；而带参数的属性访问器仍按普通函数处理。

    Args:
        node: 被检查的函数 AST 节点。

    Returns:
        零参数且带 ``@property``，或零参数的 ``__str__`` / ``__repr__`` 时为 True。
    """
    params = [arg.arg for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs]
    if [name for name in params if name not in ("self", "cls")]:
        return False
    decorators = {
        getattr(dec, "id", None) or getattr(getattr(dec, "attr", None), "id", None)
        for dec in node.decorator_list
    }
    return "property" in decorators or node.name in ("__str__", "__repr__")


def _is_test_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断是否 pytest 测试函数（名字以 ``test_`` 开头）。

    Args:
        node: 被检查的函数 AST 节点。

    Returns:
        是测试函数时为 True（放宽档下只要求一句场景说明）。
    """
    return node.name.startswith("test_")


def check_file(path: Path, *, allow_test_functions: bool) -> list[str]:
    """检查单个文件，返回问题清单（空清单表示合规）。

    Args:
        path: 待检查的 ``.py`` 文件。
        allow_test_functions: 测试函数是否放宽为"一句场景说明即可"。

    Returns:
        人类可读的问题描述列表；语法错误也作为一条返回。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"{path}: 语法错误，无法检查（{exc}）"]

    problems: list[str] = []
    if ast.get_docstring(tree) is None:
        problems.append(f"{path}: 【模块】缺 docstring")

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        where = f"{path}:{node.lineno}: {node.name}()"
        doc = ast.get_docstring(node)
        relaxed = allow_test_functions and _is_test_function(node)
        if doc is None:
            problems.append(f"{where} 【缺 docstring】")
            continue
        if relaxed:
            continue
        params = [a.arg for a in node.args.args + node.args.kwonlyargs]
        params = [name for name in params if name not in ("self", "cls")]
        if params and "Args:" not in doc:
            problems.append(f"{where} 【缺 Args:】参数 {params}")
        if _is_generator(node):
            # 生成器产出值，规范段落是 Yields:（Returns: 也算写了说明，不苛求）
            if "Yields:" not in doc and "Returns:" not in doc:
                problems.append(f"{where} 【缺 Yields:】")
        elif _returns_something(node) and "Returns:" not in doc and not _is_value_property(node):
            problems.append(f"{where} 【缺 Returns:】")
    return problems


def changed_files() -> list[Path]:
    """列出工作树里新增/修改的 ``.py``（"本次增量"）。

    用 ``git status --porcelain`` 取已跟踪的改动（``M``/``A``/``R``）与未跟踪文件
    （``??``）；删除项（``D``）跳过，因为它们不需要 docstring。

    Returns:
        现存且位于本仓内的 ``.py`` 文件路径列表；git 不可用时返回空列表。
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        print("git 不可用：--changed 退化为「无待查文件」", file=sys.stderr)
        return []

    files: list[Path] = []
    for line in out.splitlines():
        status, _, raw = line[:2], line[2:3], line[3:]
        if status.strip() == "D":
            continue
        path = (REPO_ROOT / raw.strip().strip('"')).resolve()
        if path.suffix == ".py" and path.is_file():
            files.append(path)
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    """入口：解析参数、遍历目标、打印缺口，返回进程退出码。

    Args:
        argv: 命令行参数（默认取 ``sys.argv[1:]``）。

    Returns:
        ``0`` 表示无缺口；``1`` 表示存在缺口（便于接进 CI/门禁）。
    """
    parser = argparse.ArgumentParser(description="docstring 规范检查")
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        help="待检查目录或文件（可重复；默认扫各包 src）",
    )
    parser.add_argument(
        "--changed",
        action="store_true",
        help="只查工作树里新增/修改的文件（增量口径）",
    )
    parser.add_argument("--summary", action="store_true", help="只输出每目录计数")
    parser.add_argument(
        "--allow-test-functions",
        action="store_true",
        help="测试函数放宽为只需一句场景说明",
    )
    args = parser.parse_args(argv)

    if args.changed:
        files = changed_files()
    else:
        targets: list[Path] = args.path or list(DEFAULT_PATHS)
        files = []
        for target in targets:
            if target.is_file():
                files.append(target)
            elif target.is_dir():
                files.extend(
                    p for p in sorted(target.rglob("*.py")) if "__pycache__" not in p.parts
                )
            else:
                print(f"跳过不存在的路径：{target}", file=sys.stderr)

    counter: Counter[str] = Counter()
    total = 0
    for path in files:
        problems = check_file(path, allow_test_functions=args.allow_test_functions)
        total += len(problems)
        if problems and not args.summary:
            for item in problems:
                print(item)
        if problems:
            top = path.relative_to(REPO_ROOT).parts[:2] if path.is_relative_to(REPO_ROOT) else ()
            counter["/".join(top)] += len(problems)

    if args.summary or counter:
        print(f"\n共检查 {len(files)} 个文件，缺口 {total} 处")
        for area, count in counter.most_common():
            print(f"  {count:4d}  {area}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
