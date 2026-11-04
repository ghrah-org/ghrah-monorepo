# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""显式装配模式：一个选择同时决定能力目录、工具实例与内容读取服务。

为什么需要这一层：同一个 Agent 的能力事实分散在三处——Agent／Ability 声明
从哪读、工具实例由谁实例化、Skill 正文与附件由谁读。三处若各配各的，就会
出现「声明走新来源、执行落旧注册表」或「运行新工具、读取旧目录」这类半切换
状态。本模块把三者收束到同一个 ``AbilitySourceMode``：

``legacy``
    保持现有运行方式：``ghrah.manifest.materialize`` 从旧注册表
    ``ghrah.abilities`` 实例化工具，``ghrah.skills.SkillAbilityProvider``
    读取 Skill 目录。
``builtin``
    三者都来自新版 ``ghrah.ability``：``BuiltinAbilitySource`` 提供声明与工具
    实例，本地获授内容读取适配（尚未接线）提供 Skill 正文与附件。

选择 ``builtin`` 后，解析失败直接报错：不回退旧注册表，不回退旧 Skill 读取器，
也不接受只属于旧模式的可写入口（直接传 wire 能力定义、默认能力集、运行期
注册／注销能力）。这些入口由 :meth:`AbilityAssembly.refuse` 显式拒绝。

本模块只做「选择」与「按模式取件」：不持有目录、不启动 Agent、不签发许可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, NoReturn

from ghrah.ability.source import BuiltinAbilitySource
from ghrah.core.ability_protocol import AbilityProtocol
from ghrah.manifest.resolver import ManifestResolver, ResolvedAbility

__all__ = [
    "AbilityAssembly",
    "AbilityAssemblyError",
    "AbilitySourceMode",
]


class AbilityAssemblyError(ValueError):
    """装配模式与所选入口冲突（新旧混装或模式值非法）。"""


class AbilitySourceMode(StrEnum):
    """能力来源装配模式。

    Attributes:
        LEGACY: 现有运行方式（旧注册表 + 旧 Skill 读取器）。
        BUILTIN: 新版 ``ghrah.ability`` 来源（新声明、新工具实例、新读取服务）。
    """

    LEGACY = "legacy"
    BUILTIN = "builtin"

    @classmethod
    def parse(cls, value: str | AbilitySourceMode) -> AbilitySourceMode:
        """把配置值收敛为模式枚举；非法值直接报错，绝不猜测。

        Args:
            value: 配置里的模式值（大小写与首尾空白被忽略）。

        Returns:
            对应的模式。

        Raises:
            AbilityAssemblyError: 值不是已声明的模式。
        """
        if isinstance(value, cls):
            return value
        raw = str(value).strip().lower()
        try:
            return cls(raw)
        except ValueError:
            known = ", ".join(mode.value for mode in cls)
            raise AbilityAssemblyError(
                f"unknown ability source mode: {value!r} (expected one of: {known})"
            ) from None


def _legacy_skill_provider(root: str | None) -> Any:
    """构造旧 Skill 读取器（仅 legacy 模式使用）。

    Args:
        root: 旧 Skill 目录根；None／空串表示不接线。

    Returns:
        ``ghrah.skills.SkillAbilityProvider`` 或 None。
    """
    if not root:
        return None
    from ghrah.skills import SkillAbilityProvider

    return SkillAbilityProvider.from_root(root)


@dataclass(frozen=True)
class AbilityAssembly:
    """一个 Agent 装配使用的能力来源。

    Attributes:
        mode: 已选定的装配模式。
        agent_store: Agent 声明存储（duck-typed ``ManifestStoreProtocol``）；
            None 表示未接线（按模式需要时显式报错）。
        source: 已固定下来的新来源实例：选择新模式时在装配点构造一次，
            之后目录、工具实例、声明解析都读同一份快照；legacy 模式为 None。
    """

    mode: AbilitySourceMode
    agent_store: Any = None
    source: BuiltinAbilitySource | None = field(default=None, repr=False, compare=False)

    @classmethod
    def select(cls, mode: str | AbilitySourceMode, *, agent_store: Any = None) -> AbilityAssembly:
        """在装配点显式选择模式。

        新来源在此**一次**构造：内置树解析失败会在这里抛出，不会拖到第一次
        spawn 才暴露，也不会中途改用另一份目录。

        Args:
            mode: ``legacy`` 或 ``builtin``。
            agent_store: Agent 声明存储。

        Returns:
            选定模式的装配对象。

        Raises:
            AbilityAssemblyError: 模式值非法。
        """
        parsed = AbilitySourceMode.parse(mode)
        source = (
            BuiltinAbilitySource(agent_store=agent_store)
            if parsed is AbilitySourceMode.BUILTIN
            else None
        )
        return cls(mode=parsed, agent_store=agent_store, source=source)

    @property
    def is_builtin(self) -> bool:
        """是否选择新版来源。"""
        return self.mode is AbilitySourceMode.BUILTIN

    @property
    def is_legacy(self) -> bool:
        """是否保持现有运行方式。"""
        return self.mode is AbilitySourceMode.LEGACY

    def require_store(self) -> Any:
        """取 Agent 声明存储；未接线时按模式报错。

        Returns:
            已注入的存储对象。

        Raises:
            AbilityAssemblyError: 未注入 ``agent_store``。
        """
        if self.agent_store is None:
            raise AbilityAssemblyError(
                f"ability source '{self.mode.value}' requires an agent manifest store; "
                f"none was injected at assembly time"
            )
        return self.agent_store

    def load_agent(self, full_name: str) -> Any:
        """按模式读取 Agent 声明。

        Args:
            full_name: Agent 全名。

        Returns:
            解析后的 ``AgentManifest``。

        Raises:
            AbilityAssemblyError: 未注入存储。
        """
        if self.source is not None:
            return self.source.load_agent(full_name)
        return self.require_store().load_agent(full_name)

    def manifest_resolver(self) -> ManifestResolver:
        """按模式创建版本 2 声明解析器（ref／type 同源）。

        Returns:
            legacy: 旧内置目录为兜底的解析器；builtin: ref 与 type 都读新来源的
            解析器（未知成员直接报错，不回退旧注册表）。
        """
        if self.source is not None:
            return self.source.resolver()
        return ManifestResolver(self.require_store())

    def instantiate(
        self,
        resolved_abilities: list[ResolvedAbility],
        *,
        workspace_root: str | None,
        auto_approve_abilities: tuple[str, ...] = (),
        require_approval_by_default: bool = True,
        command_runner: Any = None,
        safe_extra_commands: tuple[str, ...] = (),
        safe_extra_sub_commands: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> list[AbilityProtocol]:
        """按模式把已解析成员实例化为工具。

        Args:
            resolved_abilities: 解析器产出的成员列表。
            workspace_root: 权限模板基准与路径解析基准（None = 不限制）。
            auto_approve_abilities: HITL 运行时覆盖层（强制放行白名单）。
            require_approval_by_default: 未声明审批要求时的兜底策略。
            command_runner: ``execute_command`` 的命令执行器注入。
            safe_extra_commands: 部署方追加的基础命令 safe 白名单。
            safe_extra_sub_commands: 部署方追加的子命令 safe 白名单。

        Returns:
            已实例化的工具列表。
        """
        if self.source is not None:
            from ghrah.ability.execution.ability_build import (
                instantiate_resolved_abilities as build,
            )
        else:
            from ghrah.manifest.materialize import instantiate_resolved_abilities as build
        return build(
            resolved_abilities,
            workspace_root=workspace_root,
            auto_approve_abilities=auto_approve_abilities,
            require_approval_by_default=require_approval_by_default,
            command_runner=command_runner,
            safe_extra_commands=safe_extra_commands,
            safe_extra_sub_commands=safe_extra_sub_commands,
        )

    def skill_provider(self, root: str | None) -> Any:
        """按模式给出 Skill 读取服务。

        legacy 模式按根目录构造旧读取器；builtin 模式当前没有本地获授内容
        读取适配（尚未接线），因此**不构造**旧读取器，返回 None 由消费方
        显式说明缺席——同一 Agent 不允许一边用新来源一边读旧目录。

        Args:
            root: 配置里的 Skill 根目录。

        Returns:
            读取服务或 None。
        """
        if self.source is not None:
            return None
        return _legacy_skill_provider(root)

    def refuse(self, entry: str, detail: str) -> NoReturn:
        """拒绝只属于另一模式的入口，不做静默降级。

        Args:
            entry: 入口名（如 ``direct-abilities``）。
            detail: 拒绝理由与可选替代路径。

        Raises:
            AbilityAssemblyError: 总是抛出。
        """
        raise AbilityAssemblyError(
            f"'{entry}' is not available under the '{self.mode.value}' ability source: {detail}"
        )
