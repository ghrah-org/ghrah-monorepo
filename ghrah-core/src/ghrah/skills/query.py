# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""skill 指纹查询：当前 SKILL.md 与 lock 登记值的对比。

**只读**：不建目录、不写 lock、不修改任何文件。漂移本身的处置（权限复核、
版本累加）属于后续能力，本模块只负责把事实查出来。

两个入口的严格程度与既有风格一致：

- :func:`query_fingerprint`（单技能）**严格**：坏 lock 直接抛错，对齐
  :func:`ghrah.skills.scan.parse_skill_file`；
- :func:`query_fingerprints`（整池）**收集式**：单个坏 lock 记进该条目的
  ``lock_issue`` 并继续，对齐 :func:`ghrah.skills.scan.scan_skills`。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ghrah.skills.errors import SkillLockError, SkillParseError
from ghrah.skills.lock import LOCK_FILE_NAME, fingerprint_skill, read_fingerprint
from ghrah.skills.models import SkillFingerprint, SkillRoot
from ghrah.skills.scan import SKILL_FILE_NAME, SKILL_NAME_RE, scan_skills

__all__ = ["query_fingerprint", "query_fingerprints"]


def query_fingerprint(skill_dir: Path) -> SkillFingerprint:
    """查询单个技能目录的指纹状态（当前值 vs 登记值）。

    Args:
        skill_dir: 技能包目录；**其目录名即 full_name**（与扫描口径一致）。

    Returns:
        :class:`SkillFingerprint`。尚无 lock 时 ``recorded`` 为 None、``drifted``
        为 False（「未登记」不等于「内容变过」）。

    Raises:
        SkillParseError: 目录名格式非法，或 SKILL.md 不存在/不可读。
        SkillLockError: 存在 lock 但不可解析、或其中没有可用的指纹值。
    """
    full_name = skill_dir.name
    if not SKILL_NAME_RE.match(full_name):
        raise SkillParseError(f"包目录名格式非法（full_name 应为 kebab-case）: {full_name!r}")
    skill_md = skill_dir / SKILL_FILE_NAME
    if not skill_md.is_file():
        raise SkillParseError(f"SKILL.md 不存在: {skill_md}")

    lock_path = skill_dir / LOCK_FILE_NAME
    return SkillFingerprint(
        full_name=full_name,
        skill_dir=skill_dir,
        lock_path=lock_path,
        current=fingerprint_skill(skill_dir),
        recorded=read_fingerprint(lock_path) if lock_path.exists() else None,
    )


def query_fingerprints(pool: Sequence[SkillRoot]) -> tuple[SkillFingerprint, ...]:
    """查询整个技能目录池的指纹状态（只读；顺序同 :func:`scan_skills`）。

    扫描期问题（目录缺失、SKILL.md 非法、full_name 重复等）不在本结果里——需要
    时直接调 :func:`ghrah.skills.scan.scan_skills`；本函数只把「lock 存在但读不出
    指纹」记进对应条目的 ``lock_issue``，不因单个坏 lock 中断整轮查询。

    Args:
        pool: 技能目录池（根路径由 provider 决定，本函数自己不认默认目录）。
    """
    results: list[SkillFingerprint] = []
    for skill in scan_skills(pool).skills:
        lock_path = skill.skill_dir / LOCK_FILE_NAME
        recorded: str | None = None
        lock_issue: str | None = None
        if lock_path.exists():
            try:
                recorded = read_fingerprint(lock_path)
            except SkillLockError as exc:
                lock_issue = str(exc)
        results.append(
            SkillFingerprint(
                full_name=skill.full_name,
                skill_dir=skill.skill_dir,
                lock_path=lock_path,
                current=fingerprint_skill(skill.skill_dir),
                recorded=recorded,
                lock_issue=lock_issue,
            )
        )
    return tuple(results)
