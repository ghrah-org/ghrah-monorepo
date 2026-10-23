# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""技能目录摘要：渲染成可注入上下文的消息文本，并给出内容摘要。

对齐 DSH「两段式注入」的第一段（目录注入）：把技能清单（``full_name`` +
一行 description）渲染成一条 ``<system-reminder>`` 消息，追加到消息队列末尾，
让模型知道当前有哪些技能可用。

与 ``ability.lock.yaml`` 的**注册指纹是两件事**：

- 注册指纹（``ghrah.skills.lock``）：SKILL.md 字节的 SHA-256，管"内容变没变"，
  用于权限复核 / 漂移判断；
- 目录摘要（本模块 ``digest_catalog``）：**给模型看的那段文字**的 SHA-256，
  只管"清单文案变没变"——决定注入文案口径（首次 / 已变化）与变化日志。

本模块是纯函数模块：不读盘、不碰上下文、无状态。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from ghrah.skills.models import SkillSummary

__all__ = [
    "CATALOG_DESCRIPTION_MAX_CHARS",
    "catalog_description",
    "digest_catalog",
    "render_catalog_message",
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


def catalog_description(text: str, max_length: int = CATALOG_DESCRIPTION_MAX_CHARS) -> str:
    """目录里的单行 description：空白折叠 + 超长截断（以 ``...`` 收尾）。"""
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3] + "..."


def digest_catalog(skills: Sequence[SkillSummary]) -> str:
    """目录摘要的 SHA-256：只取 ``full_name`` + 截断后的 description。

    技能正文（SKILL.md 字节）变化**不**改本摘要，只有清单文案变化才改。
    """
    canonical = "\n".join(
        json.dumps([skill.full_name, catalog_description(skill.description)])
        for skill in sorted(skills, key=lambda item: item.full_name)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_catalog_message(skills: Sequence[SkillSummary], *, changed: bool) -> str:
    """渲染目录摘要消息（``<system-reminder>`` 包裹，供追加到消息队列末尾）。

    Args:
        skills: 技能清单；空清单表示目录已清空，同样要告知模型旧名字失效。
        changed: 本次清单是否与上一次注入不同——不同用"完整替换"口径，
            相同用介绍口径（文案稳定，利于前缀缓存）。
            空清单恒用"完整替换"口径（对齐 DSH 的空目录文案）。
    """
    entries = "\n".join(
        f"- `{skill.full_name}`: {_escape_text(catalog_description(skill.description))}"
        for skill in sorted(skills, key=lambda item: item.full_name)
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


def _escape_text(text: str) -> str:
    """转义目录文本，避免 description 里的尖括号破坏 ``<system-reminder>`` 边界。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
