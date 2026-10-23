# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""技能目录池：默认根目录与目录创建。

与仓内其他用户级目录保持一致（``~/.ghrah/manifests``、``~/.ghrah/data``、
``~/.ghrah/projects``），技能根目录取 ``~/.ghrah/skill-ability``，并在
初始化阶段自动创建。

目录池的形态对齐 DSH：多个根目录各带一个 rank，rank 越小优先级越高，
同名 skill 由优先级高者胜出。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ghrah.skills.models import SkillRoot

__all__ = [
    "DEFAULT_SKILL_ROOT_RANK",
    "DEFAULT_SKILL_ROOT_SOURCE",
    "default_pool",
    "default_skill_root",
    "ensure_roots",
    "pool_from_root",
]

DEFAULT_SKILL_ROOT_RANK: int = 100
"""默认技能目录的优先级。

当前池中只有这一项，即最高优先级。未来新增目录时取**更大**的 rank
（例如项目内目录若需更高优先级，才取更小的值）。
"""

DEFAULT_SKILL_ROOT_SOURCE: str = "skill-ability"
"""默认技能目录的来源标签（写入 lock 的 ``provenance.source``）。"""


def default_skill_root() -> Path:
    """默认技能根目录：``~/.ghrah/skill-ability``。"""
    return Path.home() / ".ghrah" / "skill-ability"


def pool_from_root(root: str | Path) -> tuple[SkillRoot, ...]:
    """由单个根目录构造目录池（用默认 rank / source）。

    配置驱动的根路径（如 subject 的 ``SkillConfig.root``）经此进入扫描，
    保证 rank / source 语义只在 ``roots`` 一处定义。
    """
    return (
        SkillRoot(
            path=Path(root),
            rank=DEFAULT_SKILL_ROOT_RANK,
            source=DEFAULT_SKILL_ROOT_SOURCE,
        ),
    )


def default_pool() -> tuple[SkillRoot, ...]:
    """默认技能目录池——当前只包含 :func:`default_skill_root` 一个目录。"""
    return pool_from_root(default_skill_root())


def ensure_roots(pool: Sequence[SkillRoot]) -> tuple[Path, ...]:
    """确保目录池中每个目录存在（缺失则逐级创建），幂等。

    Returns:
        已确保存在的目录，顺序与入参一致。
    """
    ensured: list[Path] = []
    for root in pool:
        root.path.mkdir(parents=True, exist_ok=True)
        ensured.append(root.path)
    return tuple(ensured)
