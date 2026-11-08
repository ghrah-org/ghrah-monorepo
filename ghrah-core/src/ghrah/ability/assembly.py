# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""v1 能力装配及旧配置值的诊断。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

from ghrah.ability.source import BuiltinAbilitySource
from ghrah.manifest.protocols import ManifestStoreProtocol


class AbilityAssemblyError(ValueError):
    """能力配置或入口不可用。"""


class AbilitySourceMode(StrEnum):
    """当前部署支持的能力来源。"""

    BUILTIN = "builtin"

    @classmethod
    def parse(cls, value: str) -> AbilitySourceMode:
        """保留 builtin 配置值，明确拒绝旧来源。"""
        normalized = str(value).strip().lower()
        if normalized != cls.BUILTIN:
            raise AbilityAssemblyError(
                f"unknown ability source mode: {value!r}; only builtin is supported"
            )
        return cls.BUILTIN


@dataclass(frozen=True)
class AbilityAssembly:
    """持有同一份 v1 声明与运行时包来源。"""

    source: BuiltinAbilitySource

    @classmethod
    def select(
        cls,
        mode: str,
        *,
        agent_store: ManifestStoreProtocol | None = None,
        install_root: str | None = None,
    ) -> AbilityAssembly:
        """验证配置值并构造来源。"""
        AbilitySourceMode.parse(mode)
        return cls(BuiltinAbilitySource(agent_store=agent_store, install_root=install_root))

    @staticmethod
    def refuse(entry: str, detail: str) -> NoReturn:
        """拒绝已经退役的装配入口。"""
        raise AbilityAssemblyError(f"{entry} is unavailable in builtin mode: {detail}")
