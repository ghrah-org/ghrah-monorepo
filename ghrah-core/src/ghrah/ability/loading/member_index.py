# SPDX-License-Identifier: Apache-2.0
"""能力包目录 → ``members`` 的原地扫描（纯读，不写盘、不 import 包内代码）。

这一层只回答一个问题：**这个目录里有哪些可展示/可执行的成员**。它不关心定义面
是否合法、也不写任何文件——因此扫描层（``ability_scan``）与构建层（``build``）
都依赖它，而不是彼此依赖。

依赖方向（由下往上）：

```
ability_schema / skill_frontmatter
        ▲
member_index  ──►  ability_scan（目录面） / build（生成区与安装期写入）
```
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from ghrah.ability.loading.ability_schema import (
    SKILL_BODY_DIRS,
    SKILL_FILE_NAME,
    media_type_for_path,
)
from ghrah.ability.loading.manifest_fields import ManifestValidationError

__all__ = ["scan_members"]

_META_NAMES: frozenset[str] = frozenset({"EFFECTS", "SUMMARY", "TITLE"})
"""从实现脚本里只读这几个模块级字面量；其余声明与工厂无关。"""

_SKIPPED_DIR_NAMES: frozenset[str] = frozenset({"__pycache__", "node_modules", ".git"})
"""附件扫描时整棵剪枝的目录名（缓存与元数据不是包内容）。"""


def scan_members(package: str | Path, *, body_path: str | None = None) -> dict[str, Any]:
    """扫描一个能力包目录，产出 ````members`` 区`` 的字典。

    只读目录结构与实现脚本的模块级字面量，不 import、不执行任何包内代码。
    类别缺席时不写入空键（``operations`` 一个都没有就整块省略），使生成区只表达
    "盘上确实有什么"。

    Args:
        package: 能力包根目录（含 ``ability.yaml`` 的目录）。
        body_path: 定义面已声明的正文路径（相对包根）；给了就优先按它定位，
            使别名正文（``skill/GUIDE.md``）不会被约定位置判断漏掉。

    Returns:
        ``{"operations": {...}, "skill": {...}, "references": [...], "assets": [...]}``
        的字典；全空时为 ``{}``。

    Raises:
        ManifestValidationError: 某个实现脚本不是合法 Python（AST 解析失败）。
    """
    root = Path(package)
    members: dict[str, Any] = {}

    operations = _scan_operations(root)
    if operations:
        members["operations"] = operations

    skill = _scan_skill(root, body_path)
    if skill is not None:
        members["skill"] = skill
        # 附件属于技能：参照系是正文所在目录，不是包根——正文写在 ``skill/`` 时
        # 附件就在 ``skill/assets``、``skill/references``，不跨目录猜。
        base = PurePosixPath(skill["body"]).parent
        for role in ("references", "assets"):
            resources = _scan_resources(root, base, role)
            if resources:
                members[role] = resources
    # 没有正文就没有附件归属：目录存在也只当普通子目录，不报错也不写进生成区。

    return members


def _scan_operations(root: Path) -> dict[str, dict[str, Any]]:
    """枚举 ``operations/*.py``，按文件名主干做成员键。

    ``title`` 取脚本的 ``TITLE``，缺席时由成员键生成；``description`` 取
    ``SUMMARY``，缺席时退化为标题（目录展示为空串不如退化为可见的名字，且不退化为
    猜测性描述）；``effects`` 取 ``EFFECTS`` 列表，缺席为空。
    """
    directory = root / "operations"
    if not directory.is_dir():
        return {}

    operations: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        literals = _module_literals(path)
        key = path.stem
        summary = literals.get("SUMMARY")
        raw_title = literals.get("TITLE")
        title = raw_title if isinstance(raw_title, str) and raw_title.strip() else _title_of(key)
        description = summary if isinstance(summary, str) and summary.strip() else title
        raw_effects = literals.get("EFFECTS")
        effects = (
            [str(item) for item in raw_effects] if isinstance(raw_effects, (list, tuple)) else []
        )
        operations[key] = {
            "title": title.strip(),
            "description": description.strip(),
            "entry": f"operations/{path.name}",
            "effects": effects,
        }
    return operations


def _scan_skill(root: Path, declared: str | None = None) -> dict[str, str] | None:
    """定位技能正文（定义面声明的路径优先，其次 ``skill/``、``skills/``）。

    只记位置与媒体类型：名字与说明住在正文 frontmatter，由扫描 / 捕获读取
    （:mod:`ghrah.ability.loading.skill_frontmatter`），索引里不抄第二份。
    """
    candidates: list[Path] = []
    if declared is not None:
        candidates.append(root.joinpath(*PurePosixPath(declared).parts))
    candidates.extend(root / name / SKILL_FILE_NAME for name in SKILL_BODY_DIRS)
    for body in candidates:
        if not body.is_file():
            continue
        relative = body.relative_to(root)
        return {
            "body": relative.as_posix(),
            "media_type": "text/markdown",
        }
    return None


def _scan_resources(root: Path, base: PurePosixPath, role: str) -> list[dict[str, str]]:
    """枚举技能目录下的 ``references/`` 或 ``assets/``：没有这个目录就是没有。

    Args:
        root: 能力包根目录。
        base: 技能正文所在目录（相对包根；包根为 ``.``）。
        role: ``references`` 或 ``assets``。

    Returns:
        按包内相对路径排序的 ``{path, media_type, digest}`` 列表；目录缺席时为空。
    """
    directory = root.joinpath(*base.parts) / role
    if not directory.is_dir():
        return []

    resources: list[dict[str, str]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(part.startswith(".") or part in _SKIPPED_DIR_NAMES for part in parts):
            continue
        relative = "/".join(parts)
        resources.append(
            {
                "path": relative,
                "media_type": media_type_for_path(relative),
                "digest": _digest(path),
            }
        )
    return resources


def _module_literals(path: Path) -> dict[str, Any]:
    """用 AST 读取实现脚本的模块级字面量（不执行代码）。

    Args:
        path: 实现脚本路径。

    Returns:
        ``{名字: 字面量}``，只含 :data:`_META_NAMES` 里出现且可静态求值的项。

    Raises:
        ManifestValidationError: 脚本不是合法 Python。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise ManifestValidationError(f"{path}: 实现脚本不是合法 Python：{exc}") from exc

    found: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in _META_NAMES:
            continue
        try:
            found[target.id] = ast.literal_eval(node.value)
        except ValueError:
            # 非字面量（函数调用、变量引用）：生成区不猜，留空由作者补。
            continue
    return found


def _title_of(key: str) -> str:
    """把成员键摊成一行的默认标题（``list_directory`` → ``List Directory``）。"""
    return key.replace("_", " ").replace("-", " ").title()


def _digest(path: Path) -> str:
    """文件内容的 ``sha256:`` 摘要。"""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
