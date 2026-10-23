# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""skill 初始化：确保目录 → 扫描 → 补齐缺失的 ``ability.lock.yaml``。

装配期的调用点是 subject 侧的 ``SkillUnit.init()``（对齐 ``ManifestStoreUnit``）：
每次装配做一次检查，第一次安装时把缺失的 lock 建出来。

**幂等**：已存在的 lock 一律不覆盖（版本累加属于后续能力）；已存在但无法
解析的 lock 也不覆盖，只记一条 issue，避免损坏既有登记结果。
"""

from __future__ import annotations

from collections.abc import Sequence

from ghrah.skills.errors import SkillLockError
from ghrah.skills.lock import (
    LOCK_FILE_NAME,
    build_lock,
    create_lock,
    fingerprint_skill,
    read_lock,
)
from ghrah.skills.models import (
    LockOutcome,
    ScanIssue,
    SkillInitResult,
    SkillRoot,
)
from ghrah.skills.roots import ensure_roots
from ghrah.skills.scan import scan_skills

__all__ = ["initialize_skills"]


def initialize_skills(
    pool: Sequence[SkillRoot],
    *,
    ensure_dirs: bool = True,
) -> SkillInitResult:
    """初始化技能目录池。

    Args:
        pool: 技能目录池（根路径由 provider 决定，本函数自己不认默认目录）。
        ensure_dirs: 是否确保目录存在（缺失则创建）；False 时目录缺失只记 issue。

    Returns:
        :class:`SkillInitResult`：扫描结果 + 每个 skill 的 lock 处置 + lock 期问题。
    """
    if ensure_dirs:
        ensure_roots(pool)

    scan = scan_skills(pool)
    locks: list[LockOutcome] = []
    lock_issues: list[ScanIssue] = []

    for skill in scan.skills:
        lock_path = skill.skill_dir / LOCK_FILE_NAME

        if lock_path.exists():
            try:
                read_lock(lock_path)
            except SkillLockError as exc:
                lock_issues.append(
                    ScanIssue(path=lock_path, reason=f"lock 存在但不可解析，保留原文件: {exc}")
                )
                locks.append(
                    LockOutcome(
                        full_name=skill.full_name,
                        path=lock_path,
                        created=False,
                        reason="已存在但不可解析（未覆盖）",
                    )
                )
                continue
            locks.append(
                LockOutcome(
                    full_name=skill.full_name,
                    path=lock_path,
                    created=False,
                    reason="已存在（未覆盖）",
                )
            )
            continue

        try:
            data = build_lock(
                full_name=skill.full_name,
                source=skill.source,
                fingerprint=fingerprint_skill(skill.skill_dir),
                frontmatter=skill.frontmatter,
            )
            created = create_lock(lock_path, data)
        except (SkillLockError, OSError) as exc:
            lock_issues.append(ScanIssue(path=lock_path, reason=str(exc)))
            locks.append(
                LockOutcome(
                    full_name=skill.full_name, path=lock_path, created=False, reason=str(exc)
                )
            )
            continue

        locks.append(
            LockOutcome(
                full_name=skill.full_name,
                path=lock_path,
                created=created,
                reason="首次生成" if created else "已存在（未覆盖）",
            )
        )

    return SkillInitResult(scan=scan, locks=tuple(locks), lock_issues=tuple(lock_issues))
