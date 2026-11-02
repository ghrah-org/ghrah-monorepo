# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Skill 初始化 built-in Subject unit。

对齐 ``ManifestStoreUnit`` 的形态：装配期在 ``init()`` 里做**一次**初始化检查——
经 ``SkillAbilityProvider``（根路径取自 ``SubjectConfig.skill.root``）确保目录
存在、扫描第一层 skill 包、为缺失 ``ability.lock.yaml`` 的 skill 首次生成该文件；
之后不再触碰它（无 ``start()``、不发布服务、不订阅事件）。

技能清单的消费方是 Core 侧的上下文注入，那一层按需重新扫盘（支持热插拔），
因此本 unit 不缓存清单，只保证目录与 lock 就绪。

能力来源装配模式为 ``builtin`` 时本 unit 显式跳过：新模式不读这个目录，
装配期把它「准备好」只会制造新旧混装的假象。
"""

from __future__ import annotations

import logging
from typing import Any

from ghrah.ability.assembly import AbilitySourceMode
from ghrah.skills import SkillAbilityProvider  # type: ignore[import-untyped]

from ghrah.subject.config import SubjectConfig
from ghrah.subject.unit.base import SubjectUnit, UnitMeta

__all__ = ["SkillUnit"]

logger = logging.getLogger(__name__)


class SkillUnit(SubjectUnit):
    """Owns skill root bootstrap and first-time ``ability.lock.yaml`` creation."""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._meta = UnitMeta(name="skill")

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def init(self, ctx: Any) -> None:
        """装配期一次初始化（幂等：已存在的 lock 一律不覆盖）。

        能力来源为 ``builtin`` 时不再初始化旧 Skill 根：新模式不读这个目录，
        在装配期创建它只会让「旧目录已就绪」与「新模式没有读取器」同时成立
        （旧初始化器与新读取器交叉）。此处显式跳过并记录原因。

        Raises:
            OSError: 技能根目录无法创建。fail-loud——目录是 skill 体系的硬前提，
                与 ``ManifestStoreUnit.init`` 里无保护的 ``ensure_dirs()`` 同等对待。
        """
        del ctx  # 无服务发布、无路由绑定；参数保留以匹配 SubjectUnit 接口
        if AbilitySourceMode.parse(self._config.ability_source) is AbilitySourceMode.BUILTIN:
            logger.warning(
                "Skill root initialization skipped: ability_source=builtin does not read "
                "the legacy skill root (root=%s)",
                self._config.skill.root,
            )
            return
        provider = SkillAbilityProvider.from_root(self._config.skill.root)
        result = provider.initialize()
        created = sum(1 for outcome in result.locks if outcome.created)
        logger.info(
            "Skill root initialized (root=%s, skills=%d, locks_created=%d, locks_kept=%d)",
            provider.pool[0].path,
            len(result.scan.skills),
            created,
            len(result.locks) - created,
        )
        for issue in (*result.scan.issues, *result.lock_issues):
            logger.warning("Skill issue at %s: %s", issue.path, issue.reason)
