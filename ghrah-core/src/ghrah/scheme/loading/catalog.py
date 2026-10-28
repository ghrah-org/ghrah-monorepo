# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""对外目录扫描：一次拿到「能力定义面 + 运行时面 + skill 面」，供外层框架调用。

**这不是第二权威**：它不定义任何新事实，只是把两处既有权威的读数并成一份报告——
定义面、成员与 skill 都取 ``ability_scan`` 的同一次目录池扫描（内置树 + 用户目录），
运行时形态取 ``ability_catalog`` 的投影 view。各模型的形状与字段都由各自权威模块
持有，本模块不复制、不解释。

用途（不限于模型调用）：

- 外部展示：列出当前可用的能力、成员与技能，并说明扫了哪些路径；
- 工具面注入：取各能力的 ``tool`` schema；
- 装配前体检：把扫描问题（根缺失、定义非法、正文缺失、容器重名…）一次收齐。

每次调用都重新扫盘（无缓存）：外部新增/修改在下一次调用即生效。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ghrah.scheme.loading.ability_catalog import (
    builtin_handlers,
    load_all_builtin_manifests,
)
from ghrah.scheme.loading.ability_roots import AbilityRoot
from ghrah.scheme.loading.ability_scan import (
    AbilityContainer,
    AbilitySkill,
    scan_ability_definitions,
)
from ghrah.scheme.loading.ability_schema import AbilityDefinition
from ghrah.scheme.loading.ability_view import AbilityManifest

__all__ = ["CatalogReport", "scan_catalog"]


@dataclass(frozen=True)
class CatalogReport:
    """一轮对外目录扫描的结果：各面读数 + 扫描问题。"""

    abilities: Mapping[str, AbilityManifest]
    """能力定义面的运行时 view，键为全名（``ghrah.fs.read_file``）。

    与 ``load_all_builtin_manifests()`` 同源同形——**这里不做二次加工**。
    外部包没有实现脚本，不产出 view（它们的声明见 :attr:`containers`）。
    """

    handlers: tuple[str, ...]
    """可解析的 handler 名全集（含 registry-only 成员）。"""

    roots: tuple[AbilityRoot, ...] = ()
    """本轮扫过的目录池（按优先级升序）。

    外部可据此说明"能力包是从哪几个路径找来的"，也是排查"某能力为什么没出现"
    的第一现场。
    """

    containers: tuple[AbilityContainer, ...] = ()
    """命中的能力容器（内置树 + 用户目录），按容器名升序。

    每项即 :func:`~ghrah.scheme.loading.ability_scan.scan_abilities` 的元素：
    名字、描述、三类成员与自身问题都在里面，本报告不再另设扁平字段。
    """

    skills: tuple[AbilitySkill, ...] = ()
    """全部技能成员（扁平清单），按全名升序。

    与 :attr:`containers` 里的 ``skills`` 同源同形——这里是给"只想拿技能清单"
    的调用方的扁平投影，不另做筛选；登记状态见各成员的 ``fingerprint``。
    """

    issues: tuple[str, ...] = field(default=())
    """扫描问题（``AbilityScanIssue`` 的可读串），按扫描顺序排列。"""

    @property
    def definitions(self) -> Mapping[str, AbilityDefinition]:
        """``{容器名: 定义}`` 视图（全部根，按优先级去重后的胜出者）。

        Returns:
            容器名到定义面的映射。
        """
        return {item.name: item.definition for item in self.containers}


def scan_catalog(*, pool: Sequence[AbilityRoot] | None = None) -> CatalogReport:
    """扫描目录池，返回一份合并报告。

    Args:
        pool: 要扫的目录池；None 取默认池（内置树 + ``~/.ghrah/skill-ability``）。
            只影响 ``roots`` / ``containers`` / ``skills`` 与问题清单；
            ``abilities`` / ``handlers`` 恒取内置树的实现面读数——实现脚本只能落在
            可 import 的包树里，换根也不会让外部包产出运行时 view。

    Returns:
        :class:`CatalogReport`。扫描问题全部收进 ``issues``，不因单个坏包丢掉整份
        报告（内置树定义非法时仍会抛出，因为那时"有哪些能力"本身不可信）。

    Raises:
        AbilityDefinitionError: 内置树的定义面非法——此时报告不可信，直接失败。
    """
    scan = scan_ability_definitions(pool)
    return CatalogReport(
        abilities=load_all_builtin_manifests(),
        handlers=builtin_handlers(),
        roots=scan.roots,
        containers=scan.containers,
        skills=scan.skills,
        issues=tuple(str(issue) for issue in scan.issues),
    )
