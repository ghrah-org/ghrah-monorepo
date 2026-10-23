# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""技能文件系统 Provider：技能存储目录池的持有者。

对应 DSH 的 skill-filesystem Provider——把「技能存在哪里」收敛到一处：池的构成
（路径 / rank / source）在构造时确定，之后 ``scan()`` / ``get_skill()`` /
``read_skill()`` / ``query_fingerprints()`` / ``initialize()`` 全部从池里取根
路径，**调用方不再传 root 参数**。

三条边界：

- **构造不碰文件系统**：provider 只是池的持有者，建目录是显式动作
  （:meth:`SkillAbilityProvider.ensure_roots` /
  :meth:`SkillAbilityProvider.initialize`）；
- **不缓存**：每次调用都重新扫盘，热插拔自然生效（与「不做内存缓存」的决定一致）；
- **不做监视**：目录变更监视（DSH 用 Chokidar）属后续能力，本版没有。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ghrah.skills.document import read_skill_document
from ghrah.skills.errors import SkillError, SkillNotFoundError
from ghrah.skills.initialize import initialize_skills
from ghrah.skills.models import (
    SkillDocument,
    SkillFingerprint,
    SkillInitResult,
    SkillRoot,
    SkillScanResult,
    SkillSummary,
)
from ghrah.skills.query import query_fingerprints
from ghrah.skills.roots import default_pool, ensure_roots, pool_from_root
from ghrah.skills.scan import scan_skills

__all__ = ["SkillAbilityProvider", "default_provider"]


@dataclass(frozen=True)
class SkillAbilityProvider:
    """技能存储目录池的持有者——唯一决定「技能存在哪里」的地方。

    构造时给出池；不想显式给池就用 :func:`default_provider`（默认
    ``~/.ghrah/skill-ability``），或 :meth:`from_root` 由配置驱动的单根池
    （如 subject 的 ``SkillConfig.root``）。
    """

    pool: tuple[SkillRoot, ...]
    """技能目录池；rank 越小优先级越高。构造后不可变。"""

    def __post_init__(self) -> None:
        if not self.pool:
            raise SkillError("技能目录池不能为空（否则扫描会静默返回空清单）")

    @classmethod
    def from_root(cls, root: str | Path) -> SkillAbilityProvider:
        """由单个根目录构造（rank / source 取默认值，见 ``roots``）。"""
        return cls(pool=pool_from_root(root))

    def scan(self) -> SkillScanResult:
        """扫描整个池，返回技能清单与问题清单（只读，不建目录、不写 lock）。"""
        return scan_skills(self.pool)

    def get_skill(self, full_name: str) -> SkillSummary:
        """按 ``full_name`` **精确查表**取单个技能（不对字符串做切分）。

        只按清单查表；扫描期问题请用 :meth:`scan`。

        Raises:
            SkillNotFoundError: 池中没有这个 ``full_name``。
        """
        for skill in self.scan().skills:
            if skill.full_name == full_name:
                return skill
        raise SkillNotFoundError(f"技能不在目录池中: {full_name!r}")

    def read_skill(self, full_name: str) -> SkillDocument:
        """读取指定技能的文档（SKILL.md 原文 + 路径）；只读，不建目录、不写 lock。

        定位与解析口径和 :meth:`scan` 完全一致（同一份清单索引）：扫描期有问题的
        技能不在清单里，因此**读不到**——不会绕过校验直接按目录名拼路径读文件。

        Raises:
            SkillNotFoundError: 池中没有这个 ``full_name``（含"存在但扫描期有问题"
                的情况，此时原因见 :meth:`scan` 的 ``issues``）。
            SkillParseError: 命中了但 SKILL.md 读不出来（被删/无权限/非 UTF-8）。
        """
        return read_skill_document(self.get_skill(full_name))

    def query_fingerprints(self) -> tuple[SkillFingerprint, ...]:
        """查询整个池的指纹状态（只读；坏 lock 记进对应条目的 ``lock_issue``）。"""
        return query_fingerprints(self.pool)

    def get_fingerprint(self, full_name: str) -> SkillFingerprint:
        """按 ``full_name`` 查询单个技能的指纹状态。

        Raises:
            SkillNotFoundError: 池中没有这个 ``full_name``。
        """
        for entry in self.query_fingerprints():
            if entry.full_name == full_name:
                return entry
        raise SkillNotFoundError(f"技能不在目录池中: {full_name!r}")

    def ensure_roots(self) -> tuple[Path, ...]:
        """确保池中每个目录存在（缺失则逐级创建），幂等。"""
        return ensure_roots(self.pool)

    def initialize(self, *, ensure_dirs: bool = True) -> SkillInitResult:
        """装配期初始化：确保目录 → 扫描 → 补齐缺失的 lock（幂等，不覆盖既有）。"""
        return initialize_skills(self.pool, ensure_dirs=ensure_dirs)


def default_provider() -> SkillAbilityProvider:
    """默认 provider：池为 ``default_pool()``（``~/.ghrah/skill-ability``）。

    便捷入口，**不读任何配置**——根目录由配置决定时，请用
    :meth:`SkillAbilityProvider.from_root`，让「根在哪」由调用方显式给出。
    """
    return SkillAbilityProvider(pool=default_pool())
