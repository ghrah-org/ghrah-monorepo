# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Protocol

from ghrah.manifest.ability import AbilityManifest
from ghrah.manifest.agent import AgentManifest

__all__ = ["AbilityCatalogPort", "ManifestStoreProtocol"]


class AbilityCatalogPort(Protocol):
    """显式选择的内置能力来源，只返回定义投影，不签发执行许可。"""

    def load_builtin(self, ability_type: str) -> AbilityManifest:
        """按受支持类型读取能力；缺失或不支持时明确拒绝。"""
        ...

    def load_ability(self, full_name: str) -> AbilityManifest:
        """根据 full_name 加载 AbilityManifest。"""
        ...

    def load_package_members(self, full_name: str) -> list[AbilityManifest]:
        """按**包全名**取该包全部可授予成员（工具 + 技能）。

        与 :meth:`load_ability` 的分工：后者取单个成员，本方法用于「一个 ref 即整包」。
        不是包名（或包里没有成员）时抛 ``ManifestNotFoundError``，调用方据此区分
        「成员写错」与「包名指向整包」。
        """
        ...


class ManifestStoreProtocol(Protocol):
    """Agent 模板存储接口。"""

    def load_agent(self, full_name: str) -> AgentManifest:
        """根据 full_name 加载 AgentManifest。"""
        ...

    def list_agents(self, namespace: str | None = None) -> list[str]:
        """列出所有 agent full_name，可选按 namespace 过滤。"""
        ...
