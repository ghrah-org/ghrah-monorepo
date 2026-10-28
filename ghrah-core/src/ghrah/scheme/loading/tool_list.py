# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""模型工具面装载：只读 ``ability.yaml``，产出 OpenAI ``tool_list``。

与 :mod:`ghrah.scheme.loading.ability_catalog` 的分工（为什么要两个模块）：

- ``ability_catalog`` 产的是**运行时 view**——要 import 实现脚本、读
  ``PERMISSIONS`` / ``HOOKS`` / ``SUMMARY`` / ``TAGS``，因此只有能解析实现包时可用；
- 本模块产的是**纯定义读数**——不 import 任何实现脚本，只解析 yaml。于是
  "这个部署里模型能看到哪些工具、每个工具的参数契约是什么"可以在装载运行环境
  之前回答，用户级能力包（无实现脚本）也照样能出工具面。

用途（模型上下文装载，不限于模型调用）：

- 装配前注入工具面：``tools`` 直接喂给 LLM 客户端；
- 外部展示与审计：列出每个工具的容器归属与参数契约；
- 反查：``entries`` 保留「工具名 → 容器 + 成员键」，不靠拆名字猜路径。

口径与定义面一致，不另立规则：

- 工具名取成员键（``read_file``），不是全名；
- ``description`` 取成员的 ``description``（工具面文案，不是目录摘要 ``SUMMARY``）；
- ``parameters`` 由成员的 ``input_schema`` 经
  :func:`~ghrah.scheme.loading.ability_catalog.build_tool_schema` 组装，与
  ``list_abilities`` 展示同一形状；
- ``expose: false`` 的成员不进列表（如 ``conversation``）。

每次调用都重新扫盘（无缓存）：外部新增/修改在下一次调用即生效。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ghrah.scheme.loading.ability_catalog import build_tool_schema
from ghrah.scheme.loading.ability_roots import AbilityRoot, default_pool
from ghrah.scheme.loading.ability_scan import AbilityScanIssue, scan_ability_definitions

__all__ = ["ToolEntry", "ToolList", "load_tool_list"]


@dataclass(frozen=True)
class ToolEntry:
    """一个进入工具面的成员：工具 dict + 它在定义面的归属。"""

    container: str
    """所属容器名（模型可见的工具只按成员键命名，归属靠这里反查）。"""

    operation: str
    """成员键（= 工具名）。"""

    name: str
    """工具名，与 ``operation`` 同值；单独留字段便于客户端直接取。"""

    tool: Mapping[str, Any]
    """OpenAI function-calling 形状的工具体。"""

    root: AbilityRoot
    """来源根：出问题时能说清是哪个目录提供的。"""

    @property
    def full_name(self) -> str:
        """定义面全名 ``<容器>.<成员键>``（如 ``fs.read_file``）。"""
        return f"{self.container}.{self.operation}"


@dataclass(frozen=True)
class ToolList:
    """一轮工具面装载的结果：工具列表 + 归属 + 问题。"""

    roots: tuple[AbilityRoot, ...]
    """本轮的目录池（已按优先级排序），外部可据此说明"扫了哪些路径"。"""

    tools: tuple[Mapping[str, Any], ...]
    """按工具名升序的工具体，可直接作为 ``tools`` 参数发给模型。"""

    entries: tuple[ToolEntry, ...]
    """与 ``tools`` 同序的归属记录。"""

    issues: tuple[AbilityScanIssue, ...]
    """扫描期问题（根缺失、定义非法、正文缺失、工具名重复…），收集式。"""

    @property
    def names(self) -> tuple[str, ...]:
        """工具名列表，顺序与 ``tools`` 一致（供白名单/差分比对）。"""
        return tuple(entry.name for entry in self.entries)

    def get(self, name: str) -> ToolEntry | None:
        """按工具名反查归属。

        Args:
            name: 工具名（成员键）。

        Returns:
            命中的 :class:`ToolEntry`；未知名字返回 None——不按名字猜容器。
        """
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None


def load_tool_list(pool: Sequence[AbilityRoot] | None = None) -> ToolList:
    """扫目录池，产出模型工具面。

    Args:
        pool: 目录池；None 表示用 :func:`default_pool`（内置树 rank 0 +
            用户目录 ``~/.ghrah/skill-ability`` rank 100）。

    Returns:
        :class:`ToolList`。问题收集式：单个包定义非法不影响其余包出工具。

    同名工具只保留优先级高者：目录池已按 rank 去重容器名，因此跨容器撞名时
    先入列表者胜出，后到的记一条问题而不是静默覆盖。
    """
    roots = tuple(pool) if pool is not None else default_pool()
    scan = scan_ability_definitions(roots)
    entries: list[ToolEntry] = []
    tools: list[Mapping[str, Any]] = []
    issues = list(scan.issues)
    seen: dict[str, ToolEntry] = {}
    for entry in scan.entries:
        for key, operation in entry.definition.operations.items():
            schema = build_tool_schema(key, operation)
            if schema is None:
                continue
            holder = seen.get(key)
            if holder is not None:
                issues.append(
                    AbilityScanIssue(
                        path=f"{entry.root.label}/{entry.container}",
                        reason=(
                            f"重复的工具名 {key!r}：已由容器 {holder.container!r} 提供，本条被忽略"
                        ),
                        container=entry.container,
                    )
                )
                continue
            tool = schema.to_openai_tool()
            item = ToolEntry(
                container=entry.container,
                operation=key,
                name=key,
                tool=tool,
                root=entry.root,
            )
            seen[key] = item
            entries.append(item)
            tools.append(tool)

    order = sorted(range(len(entries)), key=lambda index: entries[index].name)
    return ToolList(
        roots=roots,
        tools=tuple(tools[index] for index in order),
        entries=tuple(entries[index] for index in order),
        issues=tuple(issues),
    )
