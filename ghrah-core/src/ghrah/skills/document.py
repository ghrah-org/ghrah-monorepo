# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SKILL.md 正文读取（按需加载）。

目录摘要只承载「名字 + 一句话描述」并且每轮注入上下文，**正文不在注入物里**：
需要正文的消费方（如 ``load_skill`` 能力）按 ``full_name`` 走到这里按需读盘。

只读、无缓存（与 provider 的「不缓存」一致，热插拔自然生效）：每次调用重新
按清单索引定位并读文件。
"""

from __future__ import annotations

from ghrah.skills.errors import SkillParseError
from ghrah.skills.models import SkillDocument, SkillSummary
from ghrah.skills.scan import SKILL_FILE_NAME

__all__ = ["read_skill_document"]


def read_skill_document(skill: SkillSummary) -> SkillDocument:
    """读取单个技能的 SKILL.md 原文（逐字，不剥离 frontmatter、不裁剪）。

    入参是扫描产出的 :class:`SkillSummary`——定位口径与 :func:`scan_skills`
    完全一致（同一份清单索引），因此不会绕过校验去按目录名拼路径读文件。

    Args:
        skill: 已扫描出的技能元信息。

    Returns:
        :class:`SkillDocument`：``content`` 为完整原文，``path`` / ``skill_dir``
        为磁盘位置。

    Raises:
        SkillParseError: SKILL.md 读不出来（扫描后被人删掉、无读权限、
            或不是 UTF-8 文本）。
    """
    path = skill.skill_dir / SKILL_FILE_NAME
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillParseError(f"读取技能文档失败: {path} ({exc})") from exc
    return SkillDocument(
        full_name=skill.full_name,
        skill_dir=skill.skill_dir,
        path=path,
        content=content,
    )
