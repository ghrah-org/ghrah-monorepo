# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""模型技能面装载：目录注入文案 + 正文按需读取。

对齐 DSH「两段式注入」：

- **第一段（目录）**：把技能清单（名字 + 一行 description）渲染成一条
  ``<system-reminder>`` 消息，每轮注入，让模型知道当前有哪些技能可用；
- **第二段（正文）**：正文不进注入物，模型选定后再按名字读盘（``load_skill``）。

与 ``ability.lock.yaml`` 的**注册指纹是两件事**：

- 注册指纹（``skill_lock``）：正文文件的 SHA-256，管"内容变没变"，
  用于权限复核 / 漂移判断；
- 目录摘要（本模块 :func:`digest_skill_catalog`）：**给模型看的那段文字**的
  SHA-256，只管"清单文案变没变"——决定注入文案口径（首次 / 已变化）。

命名口径与 operations 一致：**目录里显示的就是成员键**（``research``），
不是 ``<容器>.<成员键>`` 的限定名——模型要拿这个名字去调加载入口，显示别的
名字会让它调不动。限定名（``AbilitySkill.full_name``）只用于去重与问题定位。

本模块只做两件事：纯渲染（不读盘）与按定位器读正文（无缓存）。扫描与优先级
由 :mod:`ghrah.scheme.loading.ability_scan` 负责，本模块不重复实现。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from typing import Protocol, runtime_checkable

from ghrah.core._base_error import ActorAgentError
from ghrah.scheme.loading.ability_roots import AbilityRoot, default_pool
from ghrah.scheme.loading.ability_scan import (
    AbilityScanIssue,
    AbilitySkill,
    scan_ability_definitions,
)

__all__ = [
    "CATALOG_DESCRIPTION_MAX_CHARS",
    "SkillCatalog",
    "SkillCatalogEntry",
    "SkillContextError",
    "SkillDocument",
    "catalog_description",
    "digest_skill_catalog",
    "load_skill_catalog",
    "read_skill_body",
    "read_skill_document",
    "read_skill_text",
    "render_skill_catalog",
]

CATALOG_DESCRIPTION_MAX_CHARS: int = 500
"""单条 description 进目录前的截断长度（对齐 DSH 的 catalogDescription）。"""

_HEADER_FIRST = (
    "A skill is a reusable set of task-specific instructions. "
    "The following skills are available in this session:"
)
_HEADER_CHANGED = (
    "The available skill catalog changed. This complete catalog replaces "
    "every earlier available-skills list in this session:"
)
_FOOTER = (
    "If a task clearly matches a skill's description, apply the approach it "
    "describes. This catalog contains summaries only — skill bodies are not "
    "included in this message."
)
_FOOTER_EMPTY = "No skills are currently available. Do not use names from earlier skill catalogs."


class SkillContextError(ActorAgentError):
    """技能面装载失败：正文读不出来（被删、无读权限、或不是 UTF-8 文本）。"""


@runtime_checkable
class SkillCatalogEntry(Protocol):
    """能进目录清单的最小成员形状。

    只要求两个属性，因此 scheme 的 :class:`AbilitySkill` 与 ``skill_models``
    的 ``SkillSummary`` 都能直接喂给渲染函数，不必为两种模型各写一份。
    """

    @property
    def catalog_name(self) -> str:
        """目录里显示的名字（= 调用方要传给加载入口的名字）。"""

    @property
    def description(self) -> str:
        """一行描述（目录摘要文本）。"""


def catalog_description(text: str, max_length: int = CATALOG_DESCRIPTION_MAX_CHARS) -> str:
    """目录里的单行 description：空白折叠 + 超长截断（以 ``...`` 收尾）。

    Args:
        text: 原始描述，可能含换行与多余空白。
        max_length: 截断长度上限，默认 :data:`CATALOG_DESCRIPTION_MAX_CHARS`。

    Returns:
        单行文本；超长时保留前 ``max_length - 3`` 个字符再接 ``...``。
    """
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3] + "..."


def digest_skill_catalog(skills: Sequence[SkillCatalogEntry]) -> str:
    """目录摘要的 SHA-256：只取名字 + 截断后的 description。

    正文变化**不**改本摘要，只有清单文案变化才改——因此它可以安全地用来判断
    "要不要重新注入目录"，而不会被正文编辑误触发。

    Args:
        skills: 本次清单（顺序无关，内部按名字排序后取摘要）。

    Returns:
        十六进制摘要串。
    """
    canonical = "\n".join(
        json.dumps([skill.catalog_name, catalog_description(skill.description)])
        for skill in sorted(skills, key=lambda item: item.catalog_name)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_skill_catalog(skills: Sequence[SkillCatalogEntry], *, changed: bool) -> str:
    """渲染目录摘要消息（``<system-reminder>`` 包裹，供追加到消息队列末尾）。

    Args:
        skills: 技能清单；空清单表示目录已清空，同样要告知模型旧名字失效。
        changed: 本次清单是否与上一次注入不同——不同用"完整替换"口径，相同用
            介绍口径（文案稳定，利于前缀缓存）。空清单恒用"完整替换"口径。

    Returns:
        可直接作为一条 user 消息追加的文本。
    """
    entries = "\n".join(
        f"- `{skill.catalog_name}`: {_escape_text(catalog_description(skill.description))}"
        for skill in sorted(skills, key=lambda item: item.catalog_name)
    )
    header = _HEADER_CHANGED if (changed or not skills) else _HEADER_FIRST
    footer = _FOOTER_EMPTY if not skills else _FOOTER
    return (
        "<system-reminder>\n"
        f"{header}\n\n"
        "<available_skills>\n"
        f"{entries}\n"
        "</available_skills>\n\n"
        f"{footer}\n"
        "</system-reminder>"
    )


def read_skill_text(locator: Traversable) -> str:
    """读正文原文（UTF-8，逐字，不裁剪、不解释）。

    这是技能面唯一的读盘实现：scheme 的成员读数与 ``skill_models`` 的包读数都
    走这里，避免两处各写一遍编码与异常包装。

    Args:
        locator: 正文定位器（``pathlib.Path`` 或 importlib 的 ``Traversable``）。

    Returns:
        文件全文。

    Raises:
        SkillContextError: 读不出来（被删、无读权限、或不是 UTF-8 文本）。
    """
    try:
        return locator.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillContextError(f"读取技能文档失败: {locator} ({exc})") from exc


@dataclass(frozen=True)
class SkillDocument:
    """一个技能成员的正文读数（逐字原文 + 它的定义面归属）。"""

    full_name: str
    """成员限定名（``<容器点分身份>.<成员键>``），用于去重与问题定位。"""

    name: str
    """目录名（成员键），即调用方传给加载入口的名字。"""

    container: str
    """所属容器名。"""

    key: str
    """成员键。"""

    path: str
    """正文位置的可读串（问题定位与日志用）。"""

    media_type: str
    """正文媒体类型（``text/markdown`` 等），由定义面声明。"""

    content: str
    """正文全文。"""

    source: str
    """来源根标签（``builtin`` / ``skill-ability`` …）。"""

    rank: int
    """来源根优先级，越小越优先。"""


def read_skill_body(skill: AbilitySkill) -> SkillDocument:
    """按成员读数读正文（只读、无缓存，热插拔自然生效）。

    入参必须是扫描产出的 :class:`~ghrah.scheme.loading.ability_scan.AbilitySkill`
    ——定位口径与扫描一致，因此不会绕过校验按目录名拼路径读文件。

    Args:
        skill: 已扫描出的成员读数。

    Returns:
        :class:`SkillDocument`。

    Raises:
        SkillContextError: 正文读不出来。
    """
    content = read_skill_text(skill.body_locator)
    return SkillDocument(
        full_name=skill.full_name,
        name=skill.catalog_name,
        container=skill.container,
        key=skill.key,
        path=f"{skill.body_locator}",
        media_type=skill.media_type,
        content=content,
        source=skill.source,
        rank=skill.rank,
    )


@dataclass(frozen=True)
class SkillCatalog:
    """一轮技能面装载的结果：清单 + 目录文案 + 正文按需读取。"""

    roots: tuple[AbilityRoot, ...]
    """本轮的目录池（已按优先级排序）。"""

    skills: tuple[AbilitySkill, ...]
    """全部技能成员，按全名升序。"""

    issues: tuple[AbilityScanIssue, ...]
    """扫描期问题（根缺失、定义非法、正文不存在…），收集式。"""

    @property
    def digest(self) -> str:
        """目录摘要，用于判断"要不要重新注入"。"""
        return digest_skill_catalog(self.skills)

    @property
    def names(self) -> tuple[str, ...]:
        """目录名列表（供白名单/差分比对）。"""
        return tuple(skill.catalog_name for skill in self.skills)

    def render(self, *, changed: bool = False) -> str:
        """渲染目录注入文本。

        Args:
            changed: 与上一次注入相比清单是否变化。

        Returns:
            ``<system-reminder>`` 包裹的目录文本。
        """
        return render_skill_catalog(self.skills, changed=changed)

    def get(self, name: str) -> AbilitySkill | None:
        """按目录名反查成员；未知名字返回 None（不猜路径）。

        Args:
            name: 目录名（成员键）。

        Returns:
            命中的 :class:`AbilitySkill`；未知名字返回 None。
        """
        for skill in self.skills:
            if skill.catalog_name == name:
                return skill
        return None

    def read(self, name: str) -> SkillDocument:
        """按目录名读正文。

        Args:
            name: 目录名（成员键，如 ``research``）。

        Returns:
            :class:`SkillDocument`。

        Raises:
            SkillContextError: 名字未知，或正文读不出来。
        """
        skill = self.get(name)
        if skill is None:
            raise SkillContextError(f"未知的技能成员: {name!r}（不按名字猜路径）")
        return read_skill_body(skill)


def load_skill_catalog(pool: Sequence[AbilityRoot] | None = None) -> SkillCatalog:
    """扫目录池，产出模型技能面（目录文案 + 正文读取入口）。

    Args:
        pool: 目录池；None 表示用 :func:`default_pool`（内置树 rank 0 +
            用户目录 ``~/.ghrah/skill-ability`` rank 100）。

    Returns:
        :class:`SkillCatalog`。问题收集式：单个包定义非法不影响其余包。

    注意：只有"声明了 ``skills`` 成员"的包才贡献技能；纯 operations 包
    （如 ``fs``）不会出现在技能面里。
    """
    roots = tuple(pool) if pool is not None else default_pool()
    scan = scan_ability_definitions(roots)
    return SkillCatalog(roots=roots, skills=scan.skills, issues=scan.issues)


def _escape_text(text: str) -> str:
    """转义目录文本，避免 description 里的尖括号破坏 ``<system-reminder>`` 边界。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def read_skill_document(skill: object) -> object:
    """按技能包读数读正文（**技能包面**，与 :func:`read_skill_body` 同一份读盘实现）。

    两面的差别只在入参形状：定义面拿 :class:`AbilitySkill`（带
    ``body_locator`` / ``body_path``），技能包面拿扫描产出的 ``SkillSummary``
    （带 ``body`` 与 ``skill_dir``）。读盘、错误归一、不缓存这三件事完全一致，
    因此都收敛到 :func:`read_skill_text`。

    **路径只认声明**：能拿到 ``body_path`` 时按它拼（定义面声明的正文位置），
    拿不到才退回 ``body``。框架自己不拼 ``skills/SKILL.md``——否则声明写着 A 而
    读取拿到 B，是一类无法从返回值察觉的错误。

    Args:
        skill: 技能包读数，需有 ``body``（正文定位器）、``skill_dir``、
            ``full_name``；定义面读数另有 ``body_path``。

    Returns:
        ``ghrah.scheme.loading.skill_models.SkillDocument``（逐字正文 + 包位置）。

    Raises:
        SkillParseError: 正文读不出来（扫描后被人删掉、无读权限、或不是 UTF-8）。
    """
    # 惰性 import：skill_models 依赖 fingerprint/scan，模块级引入会与扫描成环。
    from ghrah.scheme.loading.skill_errors import SkillParseError
    from ghrah.scheme.loading.skill_models import SkillDocument as PackageDocument

    skill_dir = getattr(skill, "skill_dir", None)
    body_path = getattr(skill, "body_path", "") or ""
    body = getattr(skill, "body", None)
    if skill_dir is not None and body_path:
        path = skill_dir.joinpath(*body_path.split("/"))
    else:
        path = body
    if path is None:
        raise SkillParseError(
            f"技能 {getattr(skill, 'full_name', '?')!r} 没有可读的正文路径"
            f"（容器来自包资源，或在文件系统上但未声明 body.path）"
        )
    try:
        content = read_skill_text(path)
    except SkillContextError as exc:
        raise SkillParseError(str(exc)) from exc
    return PackageDocument(
        full_name=skill.full_name,  # type: ignore[attr-defined]
        skill_dir=skill_dir,
        path=path,
        body_path=body_path or path.name,
        content=content,
    )
