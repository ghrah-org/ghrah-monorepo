# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``SKILL.md`` frontmatter 的唯一解析点：名字与说明的作者是正文本身。

``ability.yaml``（``schema_version: '1'``）是**给系统看的索引**——它只记正文位置与
媒体类型，不承载技能的名字和说明。这两个字段仍然住在正文头部::

    ---
    name: reporting-guide
    description: 使用获准模板和写作指南整理报告。
    ---

    # 报告编写
    ...

历史格式（``skills.<key>.title / description``）仍可读：正文有 frontmatter 时以它
为准，没有时退回定义面声明的值。扫描、捕获都从这里取同一份判定，避免"扫描读一份、
捕获读另一份"。

本模块只依赖 YAML 解析与 skill 错误类型，不依赖扫描侧，因此可以被扫描安全引用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ghrah.ability.loading.ability_schema import load_yaml
from ghrah.ability.loading.skill_errors import SkillParseError

__all__ = [
    "SkillFrontmatter",
    "parse_skill_frontmatter",
    "read_skill_frontmatter",
]

_FENCE_LINE: str = "---\n"
"""frontmatter 围栏行（含换行）：正文必须以它开头才算带 frontmatter。"""

_REQUIRED_FIELDS: tuple[str, ...] = ("name", "description")
"""必填且必须为非空字符串的两个字段。"""


@dataclass(frozen=True)
class SkillFrontmatter:
    """正文头部声明的一行名字与说明。"""

    name: str
    description: str


def parse_skill_frontmatter(text: str, *, source: str = "SKILL.md") -> SkillFrontmatter | None:
    """解析正文头部的 YAML frontmatter。

    没有 frontmatter 不等于坏文件——历史包允许把名字与说明写在定义面。因此
    "缺席"返回 None，"存在但不合法"抛错，两者不能混为一谈。

    Args:
        text: 正文全文（UTF-8 已解码）。
        source: 报错用的来源标签（通常是正文路径）。

    Returns:
        :class:`SkillFrontmatter`；正文没有 frontmatter 时为 None。

    Raises:
        SkillParseError: frontmatter 未闭合、不是映射、或缺少非空的 name/description。
    """
    if not text.startswith(_FENCE_LINE):
        return None
    parts = text.split(_FENCE_LINE, 2)
    if len(parts) != 3:
        raise SkillParseError(f"{source}: frontmatter 未闭合")
    metadata: Any = load_yaml(parts[1])
    if not isinstance(metadata, dict):
        raise SkillParseError(f"{source}: frontmatter 不是映射")
    values: dict[str, str] = {}
    for field in _REQUIRED_FIELDS:
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SkillParseError(f"{source}: frontmatter 必须包含非空的 {field}")
        values[field] = value.strip()
    return SkillFrontmatter(**values)


def read_skill_frontmatter(
    path: str | Path, *, source: str | None = None
) -> SkillFrontmatter | None:
    """从磁盘读正文并解析 frontmatter（读不出来时让异常照常上抛）。

    Args:
        path: 正文文件路径。
        source: 报错用的来源标签；None 时用路径本身。

    Returns:
        :class:`SkillFrontmatter`；正文没有 frontmatter 时为 None。

    Raises:
        OSError: 文件读不出来。
        UnicodeDecodeError: 正文不是 UTF-8。
        SkillParseError: frontmatter 存在但不合法。
    """
    body = Path(path)
    return parse_skill_frontmatter(
        body.read_text(encoding="utf-8"),
        source=source or str(body),
    )
