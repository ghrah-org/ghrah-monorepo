# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""skill 发现与初始化体系的错误类型。

skill 是与 ability / manifest **并列的独立体系**：本模块的错误不被
``ghrah.scheme_ability`` 复用，本包也不 import 它。
"""

from __future__ import annotations


class SkillError(Exception):
    """skill 体系错误的基类。"""


class SkillParseError(SkillError):
    """SKILL.md 不合法：无 frontmatter / YAML 非法 / 缺必填字段 / 字段格式错。"""


class SkillLockError(SkillError):
    """``ability.lock.yaml`` 读取、解析或写入失败。"""


class SkillNotFoundError(SkillError):
    """按 ``full_name`` 查表未命中：该技能不在目录池中。"""
