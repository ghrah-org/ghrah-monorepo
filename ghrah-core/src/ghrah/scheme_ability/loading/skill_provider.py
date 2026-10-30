# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""技能文件系统 Provider：目录池的持有者 + 面向"技能包"的便捷门面。

**不再有独立的扫描实现**：技能包就是"声明了 ``skills`` 成员的能力包"，
扫描口径唯一收敛在 :mod:`ghrah.scheme_ability.loading.ability_scan`（一次扫盘同时得到
定义、正文位置、指纹状态与登记问题）。本模块只做三件事：

- 持有目录池（构造时确定路径 / rank / source，调用方不再传 root）；
- 把：meth:`scan` 的扁平结果按 ``full_name`` 精确查表（不对字符串切分）；
- 把写盘动作转成显式接口：:meth:`ensure_roots` / :meth:`initialize` /
  :meth:`relock`——扫描本身从不写盘。

指纹仍分两个层级：``AbilitySkill.fingerprint``（随扫描产出，只读定义面与正文）
是入口哨兵；:meth:`verify` 是整树校验（昂贵，读遍整包内容，仅显式调用）。
"""

from __future__ import annotations

from pathlib import Path

from ghrah.scheme_ability.loading.ability_roots import (
    AbilityRoot,
    default_pool,
    ensure_roots,
    path_root,
)
from ghrah.scheme_ability.loading.ability_scan import (
    AbilityScanResult,
    AbilitySkill,
    ensure_locks,
    relock_skill,
    scan_ability_definitions,
    verify_skill,
)
from ghrah.scheme_ability.loading.skill_context import read_skill_document
from ghrah.scheme_ability.loading.skill_errors import SkillError, SkillNotFoundError
from ghrah.scheme_ability.loading.skill_models import (
    BundleVerification,
    LockRecord,
    SkillFingerprint,
    SkillInitResult,
)

__all__ = ["SkillAbilityProvider", "default_provider"]


class SkillAbilityProvider:
    """目录池的持有者——唯一决定「技能包存在哪里」的地方。

    构造时给出池；不想显式给池就用 :func:`default_provider`（默认池：内置能力树
    + ``~/.ghrah/skill-ability``），或 :meth:`from_root` 由配置驱动的单根池
    （如 subject 的 ``SkillConfig.root``）。

    三条边界：构造不碰文件系统；每次调用都重新扫盘（无缓存，热插拔自然生效）；
    不做目录监视（后续能力）。
    """

    def __init__(self, pool: tuple[AbilityRoot, ...]) -> None:
        """固定目录池。

        Args:
            pool: 目录池，rank 越小优先级越高。

        Raises:
            SkillError: 池为空（否则扫描会静默返回空清单，调用方无从判断是"没装"
                还是"没配"）。
        """
        if not pool:
            raise SkillError("技能目录池不能为空（否则扫描会静默返回空清单）")
        self._pool: tuple[AbilityRoot, ...] = tuple(pool)

    @property
    def pool(self) -> tuple[AbilityRoot, ...]:
        """本 provider 的目录池（只读）。"""
        return self._pool

    @classmethod
    def from_root(cls, root: str | Path) -> SkillAbilityProvider:
        """由单个根目录构造（rank / source 取用户目录的默认值）。

        Args:
            root: 技能包根目录。

        Returns:
            持有单根池的 provider。
        """
        return cls(pool=(path_root(root),))

    def scan(self) -> AbilityScanResult:
        """扫描整个池（只读，不建目录、不写 lock）。

        Returns:
            :class:`AbilityScanResult`：``skills`` 为全部技能包成员（含指纹状态），
            ``issues`` / ``lock_issues`` 为扫描期与登记面的问题。
        """
        return scan_ability_definitions(self._pool)

    def get_skill(self, full_name: str) -> AbilitySkill:
        """按名字取单个技能：全名精确优先，其次成员键，最后容器名。

        三个名字都认，是因为同一个技能在不同场合被叫成不同的名字：定义面与去重
        用全名（``<容器点分身份>.<成员键>``），模型从目录里看到的是成员键，而只包
        一个技能的包里两者与容器名往往相同。**先精确后宽松、宽松处要求唯一**——
        两个技能撞名字时报"不唯一"，不猜。

        Args:
            full_name: 技能成员全名、成员键或容器名。

        Returns:
            命中的技能成员。

        Raises:
            SkillNotFoundError: 池中没有这个技能，或该名字命中了多个技能。
        """
        skills = self.scan().skills
        for skill in skills:
            if skill.full_name == full_name:
                return skill
        hits = [skill for skill in skills if skill.catalog_name == full_name]
        if not hits:
            hits = [skill for skill in skills if skill.container == full_name]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise SkillNotFoundError(f"技能名 {full_name!r} 命中了 {len(hits)} 个技能，请改用全名")
        raise SkillNotFoundError(f"技能不在目录池中: {full_name!r}")

    def read_skill(self, full_name: str) -> object:
        """读取指定技能的正文（原文 + 路径）；只读，不建目录、不写 lock。

        定位口径与 :meth:`scan` 完全一致：扫描期有问题的技能不在清单里，因此
        读不到——不会绕过校验直接按目录名拼路径读文件。

        Args:
            full_name: 技能成员全名。

        Returns:
            :class:`ghrah.scheme_ability.loading.skill_models.SkillDocument`。

        Raises:
            SkillNotFoundError: 池中没有这个 ``full_name``。
            SkillParseError: 命中了但正文读不出来（被删 / 无权限 / 非 UTF-8）。
        """
        return read_skill_document(self.get_skill(full_name))

    def query_fingerprints(self) -> tuple[SkillFingerprint, ...]:
        """查询整个池的指纹状态（只读；坏 lock 记进对应条目的 ``lock_issue``）。

        Returns:
            按全名升序的指纹对象；非文件系统上的成员（包资源）不参与。
        """
        return tuple(
            skill.fingerprint for skill in self.scan().skills if skill.fingerprint is not None
        )

    def get_fingerprint(self, full_name: str) -> SkillFingerprint:
        """按 ``full_name`` 查询单个技能的指纹状态。

        本方法经 ``get_skill()`` 发起一次新的目录池扫描，再复用该次扫描
        已算出的入口指纹；不会额外执行整包 ``verify()``。连续调用
        ``get_skill()`` 和本方法会扫描两次，并非共享前一次结果。

        Args:
            full_name: 技能成员全名。

        Returns:
            该成员的指纹对象。

        Raises:
            SkillNotFoundError: 池中没有这个 ``full_name``，或成员不在文件系统上。
        """
        skill = self.get_skill(full_name)
        if skill.fingerprint is None:
            raise SkillNotFoundError(f"技能不在文件系统上，无指纹: {full_name!r}")
        return skill.fingerprint

    def ensure_roots(self) -> tuple[Path, ...]:
        """确保池中每个真实目录存在（缺失则逐级创建），幂等。

        Returns:
            已确保存在的目录元组（包资源根不在其中）。
        """
        return ensure_roots(self._pool)

    def initialize(self, *, ensure_dirs: bool = True) -> SkillInitResult:
        """装配期初始化：确保目录 → 扫描 → 补齐缺失的 lock（幂等、不覆盖既有）。

        整个流程只扫一次盘：``ensure_locks`` 直接复用扫描结果判断"哪些成员缺
        lock"，只有真正要写的成员才会再走一次整树摘要。

        Args:
            ensure_dirs: 是否先创建缺失的用户目录（False 时只扫描与补登记）。

        Returns:
            :class:`SkillInitResult`：扫描结果 + 每个成员的登记处置 + 登记期问题。
        """
        if ensure_dirs:
            ensure_roots(self._pool)
        scan = scan_ability_definitions(self._pool)
        locks, lock_issues = ensure_locks(scan)
        return SkillInitResult(scan=scan, locks=locks, lock_issues=lock_issues)

    def verify(self, full_name: str) -> BundleVerification:
        """**整树校验**单个技能：内容维度与快照维度分开裁决。

        昂贵——读遍整包内容（受条目数 / 总字节上限保护，超限则降级为无法裁决）。
        因此不做监视、不在发现面自动调用，只由显式动作触发。

        Args:
            full_name: 技能成员全名。

        Returns:
            :class:`BundleVerification`。

        Raises:
            SkillNotFoundError: 池中没有这个 ``full_name``。
            SkillLockError: 成员不在文件系统上。
        """
        return verify_skill(self.get_skill(full_name))

    def relock(self, full_name: str, *, recorded_at: str | None = None) -> LockRecord:
        """**显式重建**单个技能的 lock：重新整树观测并覆盖登记文件。

        这是**唯一**允许覆盖既有 lock 的路径（``initialize()`` 永远不覆盖），用于
        旧 schema 迁移与"确认漂移后重新登记"。默认不隐式调用。

        Args:
            full_name: 技能成员全名。
            recorded_at: 登记时刻；None 取当前 UTC（不改写传入值，便于复算/测试）。

        Returns:
            重建后的登记记录（已从盘上读回校验）。

        Raises:
            SkillNotFoundError: 池中没有这个 ``full_name``。
            SkillLockError: 写盘或读回校验失败。
        """
        return relock_skill(self.get_skill(full_name), recorded_at=recorded_at)


def default_provider() -> SkillAbilityProvider:
    """默认 provider：池为 :func:`ghrah.scheme_ability.loading.ability_roots.default_pool`。

    便捷入口，**不读任何配置**——根目录由配置决定时请用
    :meth:`SkillAbilityProvider.from_root`，让「根在哪」由调用方显式给出。

    Returns:
        持有默认池的 provider。
    """
    return SkillAbilityProvider(pool=default_pool())
