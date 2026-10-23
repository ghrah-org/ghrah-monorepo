# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""skill 发现与初始化的数据结构。

命名与职责对齐 DSH 的 skill 体系（``SkillSummary`` / ``SkillCandidate``）：
本模块的 :class:`SkillSummary` 相当于其 ``SkillSummary``，只承载"目录摘要"
需要的元信息，不含 SKILL.md 正文（正文按需加载属于后续能力）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "LockOutcome",
    "ScanIssue",
    "SkillDocument",
    "SkillInitResult",
    "SkillRoot",
    "SkillScanResult",
    "SkillSummary",
]


@dataclass(frozen=True)
class SkillRoot:
    """技能目录池中的一项。

    ``rank`` 越小优先级越高：同一个 full_name 由 rank 最小者胜出
    （与 DSH skill-filesystem Provider 的 rank 语义一致）。
    """

    path: Path
    rank: int
    source: str


@dataclass(frozen=True)
class SkillSummary:
    """单个 skill 的元信息（目录摘要口径，不含正文）。

    ``full_name`` 是技能身份，取自**包目录名**：``<包名>-<技能名>``（无包名时
    等于技能名）。跨包重名靠它区分，它也是给模型看的名字；框架侧只做**精确
    查表**，不对字符串做切分——``-`` 同时是包名与技能名的内部分隔符，切分本身
    有歧义。``name`` 是 SKILL.md frontmatter 声明的技能名，与目录名**允许不同**
    （身份只认目录名，``name`` 只是技能文档自称的名字）。
    """

    full_name: str
    name: str
    description: str
    source: str
    rank: int
    skill_dir: Path
    frontmatter: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """SKILL.md frontmatter 的原始键值。

    本版**只消费** ``name`` / ``description``；``permission`` / ``requires`` /
    ``implementation`` / ``bind_strategy`` 原样保留、不解释语义，供后续能力
    （权限核定、实现路由）使用，避免过早固化它们的含义。
    """


@dataclass(frozen=True)
class ScanIssue:
    """扫描期的一条问题记录（收集式：不因单条问题中断整轮扫描）。"""

    path: Path
    reason: str


@dataclass(frozen=True)
class SkillScanResult:
    """一轮扫描的结果。"""

    skills: tuple[SkillSummary, ...]
    """按 full_name 升序排列（确定性：跨文件系统、跨机器结果一致）。"""

    issues: tuple[ScanIssue, ...]
    """扫描期问题：目录缺失、SKILL.md 非法、同一个 full_name 被忽略等。"""


@dataclass(frozen=True)
class LockOutcome:
    """单个 skill 的 lock 初始化结果。"""

    full_name: str
    path: Path
    created: bool
    reason: str = ""


@dataclass(frozen=True)
class SkillFingerprint:
    """单个 skill 的指纹查询结果（当前值 vs lock 登记值）。

    ``recorded`` 为 None 表示**尚无 lock**（未登记）；``drifted`` 只在两者都存在
    且不同时为 True——「未登记」与「内容变过」是两件事，不要混。
    """

    full_name: str
    skill_dir: Path
    lock_path: Path
    current: str
    """当前 SKILL.md 字节的 ``sha256:<hex>``。"""
    recorded: str | None
    """lock 里登记的指纹；None = 尚无 lock（或 lock 不可用，见 ``lock_issue``）。"""
    lock_issue: str | None = None
    """lock 存在但读不出指纹时的原因（此时 ``recorded`` 为 None）。

    由 :func:`ghrah.skills.query.query_fingerprints` 填充；单技能查询
    :func:`ghrah.skills.query.query_fingerprint` 遇到坏 lock 直接抛
    :class:`SkillLockError`，不会返回带 ``lock_issue`` 的结果。
    """

    @property
    def drifted(self) -> bool:
        """内容自登记以来是否变过。未登记时恒为 False。"""
        return self.recorded is not None and self.recorded != self.current


@dataclass(frozen=True)
class SkillInitResult:
    """一次初始化的总结果。"""

    scan: SkillScanResult
    """扫描结果（含扫描期问题）。"""

    locks: tuple[LockOutcome, ...]
    """每个 skill 的 lock 处置结果。"""

    lock_issues: tuple[ScanIssue, ...]
    """lock 阶段的独立问题集合（扫描期问题见 ``scan.issues``）。"""


@dataclass(frozen=True)
class SkillDocument:
    """单个 skill 的文档读取结果：SKILL.md 原文 + 路径。

    ``content`` 是文件的**完整原文**（含 frontmatter 块），逐字返回——不剥离
    frontmatter、不裁剪、不解释（对齐 ``read_file`` 的"读到什么给什么"口径）；
    正文怎么用由消费方决定。
    """

    full_name: str
    skill_dir: Path
    path: Path
    """SKILL.md 的路径（由 ``skill_dir`` 与 ``SKILL_FILE_NAME`` 拼出）。"""
    content: str
    """SKILL.md 完整原文（UTF-8 解码）。"""
