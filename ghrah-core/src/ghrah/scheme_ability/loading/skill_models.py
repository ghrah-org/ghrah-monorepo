# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""skill 发现与初始化的数据结构。

命名与职责对齐 DSH 的 skill 体系（``SkillSummary`` / ``SkillCandidate``）：
本模块的 :class:`SkillSummary` 相当于其 ``SkillSummary``，只承载"目录摘要"
需要的元信息，不含 SKILL.md 正文（正文按需加载属于后续能力）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型引用：fingerprint 依赖 scan，scan 依赖本模块，运行时不导入以免成环
    from ghrah.scheme_ability.loading.ability_scan import AbilityScanIssue, AbilityScanResult
    from ghrah.scheme_ability.loading.skill_fingerprint import BundleManifest, ManifestChange

__all__ = [
    "BundleVerification",
    "LockOutcome",
    "LockRecord",
    "SkillDocument",
    "SkillFingerprint",
    "SkillInitResult",
]


@dataclass(frozen=True)
class LockOutcome:
    """单个 skill 的 lock 初始化结果。"""

    full_name: str
    path: Path
    created: bool
    reason: str = ""


@dataclass(frozen=True)
class LockRecord:
    """lock 里的整树登记记录（:func:`ghrah.scheme_ability.loading.skill_lock.read_lock_record`
    的产物）。

    ``manifest`` 是唯一权威观测；``content_digest`` / ``snapshot_digest`` / ``entry``
    都是它的派生投影，因此都能从 ``manifest`` 复算。
    """

    schema: int
    full_name: str
    source: str
    algorithm: str
    """manifest 输入格式版本（如 ``sha256-tree-v1``）；改格式时递增，避免误判成内容漂移。"""

    content_digest: str | None
    """内容摘要（结构 + 内容，不含 mtime）：跨机器 / 跨重装可复现。降级时为 None。"""

    snapshot_digest: str | None
    """快照摘要（结构 + 内容 + mtime）：本机状态，跨机器不可比。降级时为 None。"""

    recorded_at: str | None
    """登记时刻（RFC 3339 UTC）；不参与任何摘要。"""

    manifest: BundleManifest
    """整树观测（降级时 ``entries`` 为空且 :attr:`BundleManifest.degraded` 有值）。"""

    entry: str | None
    """入口哨兵（定义面 + 正文的派生摘要）；入口文件缺失或降级时为 None。"""


@dataclass(frozen=True)
class SkillFingerprint:
    """入口哨兵（定义面 + 正文）的当前值 vs 登记值，外加整树登记记录。

    **只覆盖入口文件**：它便宜（两次读盘），专供发现面的快速判断；整树裁决必须用
    :class:`BundleVerification`（``provider.verify()``），两者口径不同，不要混用。

    ``recorded`` 为 None 表示**尚无 lock**，或 lock 降级到无法派生出入口哨兵
    （后者见 ``lock_issue``）；``drifted`` 只在两者都存在且不同时为 True——
    「未登记」与「内容变过」是两件事。
    """

    full_name: str
    skill_dir: Path
    lock_path: Path
    current: str | None
    """入口哨兵当前值（``sha256-entry-v1:<hex>``）。

    None 表示入口文件读不出来（扫描后消失 / 无权限），原因见 ``lock_issue``。
    """
    recorded: str | None
    """入口哨兵登记值（从 lock 的 manifest 派生）；None 见类文档。"""
    record: LockRecord | None = None
    """整树登记记录；None = 尚无 lock（或 lock 不可用，见 ``lock_issue``）。"""
    lock_issue: str | None = None
    """不可用或不可裁决的原因（此时 ``recorded`` / ``record`` 可能为 None）。

    两种来源：lock 读不出来（坏文件 / schema 不符），此时单技能查询直接抛
    :class:`SkillLockError`、只有整池查询会填本字段；或 lock 是**降级**记录
    （登记时观测不完整），此时 lock 本身可读、但派生不出入口哨兵。
    """

    @property
    def drifted(self) -> bool:
        """入口哨兵自登记以来是否变过（**仅覆盖定义面 + 正文**）。

        未登记、或当前值读不出来时恒为 False（「不知道」不报成「变了」）。
        """
        return (
            self.recorded is not None and self.current is not None and self.recorded != self.current
        )


@dataclass(frozen=True)
class BundleVerification:
    """整树校验结果：内容维度与快照维度**分开裁决**。

    ``content_drifted`` / ``snapshot_drifted`` 为 None 表示**无法裁决**（未登记，
    或登记时降级）——「不知道」与「没变」是两件事，不要混。
    """

    full_name: str
    skill_dir: Path
    lock_path: Path
    record: LockRecord | None
    """登记记录；None = 尚无 lock（见 ``lock_issue``）。"""

    current: BundleManifest | None
    """本次现算的整树观测；降级时 ``complete`` 为 False 且两枚摘要为 None。"""

    changes: tuple[ManifestChange, ...] = ()
    """记录值 → 当前值的逐条差异（含仅 mtime 变化的条目）。"""

    lock_issue: str | None = None
    """lock 不可用（坏文件 / 旧 schema 需迁移）时的原因。"""

    @property
    def content_drifted(self) -> bool | None:
        """结构 + 内容是否变了（跨机器可比的裁决）；None = 无法裁决。"""
        return self._compare("content_digest")

    @property
    def snapshot_drifted(self) -> bool | None:
        """内容 + mtime 是否变了（本机状态裁决）；None = 无法裁决。"""
        return self._compare("snapshot_digest")

    @property
    def max_mtime_ns(self) -> int | None:
        """整包最新修改时间（逐文件 mtime 最大值，派生值，不参与摘要）。"""
        return self.current.max_mtime_ns if self.current is not None else None

    def _compare(self, field: str) -> bool | None:
        if self.record is None or self.current is None:
            return None
        recorded = getattr(self.record, field)
        current = getattr(self.current, field)
        if recorded is None or current is None:
            return None
        return recorded != current


@dataclass(frozen=True)
class SkillInitResult:
    """一次初始化的总结果。"""

    scan: AbilityScanResult
    """扫描结果（含扫描期问题与登记面问题）。

    与 :func:`ghrah.scheme_ability.loading.ability_scan.scan_ability_definitions` 同一个
    类型——初始化不再有自己的扫描口径，只是"扫描 + 补登记"。
    """

    locks: tuple[LockOutcome, ...]
    """每个 skill 的 lock 处置结果。"""

    lock_issues: tuple[AbilityScanIssue, ...]
    """登记阶段的独立问题集合（扫描期问题见 ``scan.issues`` / ``scan.lock_issues``）。"""


@dataclass(frozen=True)
class SkillDocument:
    """单个能力包的正文读取结果：正文原文 + 路径。

    ``content`` 是正文文件的**完整原文**，逐字返回——不裁剪、不解释。
    新格式的正文位置由定义面声明（本版为 ``skills/SKILL.md``），其中**不再有
    frontmatter**（``name`` / ``description`` 已迁到 ``ability.yaml``）；旧格式
    仍含 frontmatter 块。

    正文路径有三种落点，全部由本对象如实表达：容器在磁盘上时 ``path`` 是真实
    路径；容器来自包资源（装成 wheel 的内置容器）时 ``path`` 为 None，正文靠
    ``body_path`` 标识，``skill_dir`` 同样为 None——**不拿目录拼接猜一个不存在的
    路径**。
    """

    full_name: str
    skill_dir: Path | None
    """容器目录；来自包资源时为 None。"""

    path: Path | None
    """正文文件的磁盘路径；容器不在文件系统上时为 None。"""

    body_path: str
    """定义面声明的正文相对路径（``skills/SKILL.md``）——`path` 为 None 时
    调用方回显与报错都用它。"""

    content: str
    """正文完整原文（UTF-8 解码）。"""


SkillPackageDocument = SkillDocument
"""``SkillDocument`` 的无歧义别名。

定义面另有一个同名类 :class:`ghrah.scheme_ability.loading.skill_context.SkillDocument`
（带容器 / 成员键 / 媒体类型），两者形状不同。门面导出时用本别名代表「技能包
面的正文读数」，避免同名遮蔽。
"""
