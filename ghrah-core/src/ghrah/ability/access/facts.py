# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""获授事实的纯形状：整包展开、请求折算与成员轴规范化。

这一层不读写关系库，只回答“一次授予具体包含哪些成员、每个成员的可见/可用
事实是什么”。启动核定器与关系库写入口共用同一份展开规则，避免“策略算出的
集合”和“grant 写下的集合”各按一套语义解释。

成员地址统一写法：Operation 为 ``operations/<key>``，内容成员为
``skill/<skill>[/assets|references|scripts/<key>]``。脚本执行权默认不授予，
必须由显式轴给出；skill 只有可见轴（可见即可读）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ghrah.protocol.ability_tree import (
    GrantAbilityRequest,
    MemberAxes,
    SkillReadScope,
    TreeKey,
)

__all__ = [
    "GrantFacts",
    "request_facts",
    "skill_scope_facts",
    "stored_facts",
    "whole_container_facts",
]

SCRIPT_MARKER = "/scripts/"
"""内容地址里标记脚本角色的片段；脚本可见不等于可执行。"""


@dataclass(frozen=True)
class GrantFacts:
    """一次授予的规范化事实集合。

    Attributes:
        members: Operation 键集合。
        content: 内容成员地址集合。
        axes: 成员地址 → ``(visible, available)``；未列出的地址按默认值处理。
    """

    members: frozenset[str] = frozenset()
    content: frozenset[str] = frozenset()
    axes: dict[str, tuple[bool, bool]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = (self.operation_addresses() | self.content) - set(self.axes)
        if missing:
            raise ValueError(f"成员轴缺失：{sorted(missing)}")

    def operation_addresses(self) -> frozenset[str]:
        """Operation 成员的规范地址集合。

        Returns:
            ``operations/<key>`` 形式的地址。
        """
        return frozenset(f"operations/{key}" for key in self.members)

    def addresses(self) -> frozenset[str]:
        """全部获授成员的规范地址集合。

        Returns:
            Operation 与内容成员的地址并集。
        """
        return self.operation_addresses() | self.content

    @property
    def empty(self) -> bool:
        """是否没有任何获授成员。"""
        return not self.members and not self.content

    def normalized_axes(self) -> dict[str, dict[str, bool]]:
        """按地址排序的显式轴事实，可直接写入关系库。

        Returns:
            ``{成员地址: {"visible": bool, "available": bool}}``。
        """
        return {
            address: {"visible": self.axes[address][0], "available": self.axes[address][1]}
            for address in sorted(self.addresses())
        }

    def with_member(self, key: str, *, visible: bool = True, available: bool = True) -> GrantFacts:
        """加入一个 Operation 成员并给出两轴事实。

        Args:
            key: Operation 键。
            visible: 是否进入可见面。
            available: 是否可执行。

        Returns:
            新的授予事实。
        """
        return GrantFacts(
            members=self.members | {key},
            content=self.content,
            axes={**self.axes, f"operations/{key}": (visible, available)},
        )

    def without_address(self, address: str) -> GrantFacts:
        """移除一个成员地址；skill 地址按前缀移除其下全部内容。

        Args:
            address: 规范成员地址。

        Returns:
            新的授予事实；地址不存在时原样返回。
        """
        if address.startswith("operations/"):
            key = address.removeprefix("operations/")
            if key not in self.members:
                return self
            axes = {item: value for item, value in self.axes.items() if item != address}
            return GrantFacts(self.members - {key}, self.content, axes)
        removed = {
            item for item in self.content if item == address or item.startswith(address + "/")
        }
        if not removed:
            return self
        axes = {item: value for item, value in self.axes.items() if item not in removed}
        return GrantFacts(self.members, self.content - removed, axes)

    def union(self, other: GrantFacts) -> GrantFacts:
        """合并两份授予事实；同地址的轴以 ``other`` 为准。

        Args:
            other: 后应用的事实。

        Returns:
            合并后的授予事实。
        """
        return GrantFacts(
            members=self.members | other.members,
            content=self.content | other.content,
            axes={**self.axes, **other.axes},
        )

    def to_request(
        self,
        *,
        alias: TreeKey,
        instance_id: str,
        expected_revision: int,
        allowed_roots: tuple[str, ...] = (),
        policy: str | None = None,
    ) -> GrantAbilityRequest:
        """把事实折算成关系库写入口的请求形状。

        Args:
            alias: Agent 内的能力别名。
            instance_id: 实例标识。
            expected_revision: 期望的关系修订（CAS）。
            allowed_roots: 文件类成员的允许根；必须落在实例根内。
            policy: 本次授予依据的策略摘要。

        Returns:
            可直接提交给 ``AbilityStore.grant`` 的请求。
        """
        return GrantAbilityRequest(
            alias=alias,
            instance_id=instance_id,
            members=sorted(self.members),
            allowed_roots=list(allowed_roots),
            skills=self._skill_scopes(),
            axes=[
                MemberAxes(
                    member=address, visible=self.axes[address][0], available=self.axes[address][1]
                )
                for address in sorted(self.addresses())
            ],
            policy=policy,
            expected_revision=expected_revision,
        )

    def _skill_scopes(self) -> list[SkillReadScope]:
        """按 skill 汇总内容成员，生成显式读范围（空列表表示不授予该类）。

        Returns:
            按 skill 键排序的读范围；不含技能的授予为空。
        """
        scopes: list[SkillReadScope] = []
        for key in sorted({address.split("/")[1] for address in self.content}):
            prefix = f"skill/{key}"
            roles = {"assets": [], "references": [], "scripts": []}
            for address in sorted(self.content):
                if not address.startswith(f"{prefix}/"):
                    continue
                parts = address.split("/")
                if len(parts) >= 4 and parts[2] in roles:
                    roles[parts[2]].append(parts[3])
            scopes.append(
                SkillReadScope(
                    key=key,
                    body=prefix in self.content,
                    assets=roles["assets"],
                    references=roles["references"],
                    scripts=roles["scripts"],
                )
            )
        return scopes


def whole_container_facts(definition: dict[str, Any]) -> GrantFacts:
    """整包展开：全部可暴露 Operation + skill 正文/附件/脚本可见。

    整包授予不授予脚本执行；脚本可见即正文可读，执行权要另给显式轴。

    Args:
        definition: 版本定义（``AbilityVersion.definition()`` 的字典形式）。

    Returns:
        整包授予事实。
    """
    members = frozenset(key for key, item in definition["operations"].items() if item["expose"])
    content: set[str] = set()
    for key, skill in definition["skills"].items():
        prefix = f"skill/{key}"
        content.add(prefix)
        for role in ("assets", "references", "scripts"):
            for item in skill.get(role) or {}:
                content.add(f"{prefix}/{role}/{item}")
    axes: dict[str, tuple[bool, bool]] = {}
    for key in members:
        axes[f"operations/{key}"] = (True, True)
    for address in content:
        axes[address] = (True, SCRIPT_MARKER not in address)
    return GrantFacts(members, frozenset(content), axes)


def skill_scope_facts(definition: dict[str, Any], key: str) -> GrantFacts:
    """单个 skill 的完整读范围：正文 + 全部登记资源 + 脚本可见。

    Args:
        definition: 版本定义。
        key: skill 键。

    Returns:
        该 skill 的授予事实；脚本执行默认不授予。

    Raises:
        ValueError: skill 不在定义里。
    """
    skill = definition["skills"].get(key)
    if skill is None:
        raise ValueError(f"Skill 不存在：{key}")
    prefix = f"skill/{key}"
    content = {prefix}
    for role in ("assets", "references", "scripts"):
        content.update(f"{prefix}/{role}/{item}" for item in skill.get(role) or {})
    axes = {address: (True, SCRIPT_MARKER not in address) for address in content}
    return GrantFacts(frozenset(), frozenset(content), axes)


def stored_facts(
    members: list[str], content: list[str], axes: dict[str, dict[str, bool]]
) -> GrantFacts:
    """把关系库里的一行还原为授予事实；旧行缺失的轴按默认值补齐。

    Args:
        members: 授权行里的 Operation 键。
        content: 授权行里的内容成员地址。
        axes: 授权行里的显式轴（可能为空或只覆盖部分成员）。

    Returns:
        补齐轴后的授予事实。
    """
    defaults: dict[str, tuple[bool, bool]] = {f"operations/{key}": (True, True) for key in members}
    for address in content:
        defaults[address] = (True, SCRIPT_MARKER not in address)
    merged: dict[str, tuple[bool, bool]] = {}
    for address in defaults:
        fact = axes.get(address)
        if fact is None:
            merged[address] = defaults[address]
        else:
            merged[address] = (
                bool(fact.get("visible", True)),
                bool(fact.get("available", True)),
            )
    return GrantFacts(frozenset(members), frozenset(content), merged)


def request_facts(definition: dict[str, Any], change: GrantAbilityRequest) -> GrantFacts:
    """把一次获授请求折算成事实集合，并校验成员轴组合。

    Args:
        definition: 版本定义。
        change: 获授变更请求。

    Returns:
        规范化授予事实。

    Raises:
        ValueError: 目标不存在、轴组合不可表达或成员重复。
    """
    members = _request_members(definition, change)
    content = _request_content(definition, change)
    axes = _default_axes(members, content)
    for entry in change.axes:
        _apply_axis(axes, entry)
    return GrantFacts(frozenset(members), frozenset(content), axes)


def _request_members(definition: dict[str, Any], change: GrantAbilityRequest) -> set[str]:
    """请求覆盖的 Operation 键；整包取当时全部可暴露成员。"""
    if change.whole_container:
        return {key for key, item in definition["operations"].items() if item["expose"]}
    return set(change.members)


def _request_content(definition: dict[str, Any], change: GrantAbilityRequest) -> set[str]:
    """请求覆盖的内容成员地址；脚本只进入可见清单。"""
    content: set[str] = set()
    scopes = list(change.skills)
    if change.skill is not None:
        scopes.append(change.skill)
    if change.whole_container and definition["skills"]:
        scopes = [SkillReadScope(key=key) for key in definition["skills"]]
    seen: set[str] = set()
    for scope in scopes:
        if scope.key in seen:
            raise ValueError("Skill 读范围重复")
        seen.add(scope.key)
        skill = definition["skills"].get(scope.key)
        if skill is None:
            raise ValueError(f"Skill 不存在：{scope.key}")
        prefix = f"skill/{scope.key}"
        if scope.body:
            content.add(prefix)
        for role in ("assets", "references", "scripts"):
            resources = skill.get(role) or {}
            selected = getattr(scope, role)
            selected = sorted(resources) if selected is None else selected
            if not set(selected) <= set(resources):
                raise ValueError(f"Skill 资源不存在：{prefix}/{role}")
            content.update(f"{prefix}/{role}/{key}" for key in selected)
    return content


def _default_axes(members: set[str], content: set[str]) -> dict[str, tuple[bool, bool]]:
    """按成员种类给出默认两轴：Operation 可用，脚本默认不可执行。"""
    axes: dict[str, tuple[bool, bool]] = {f"operations/{key}": (True, True) for key in members}
    for address in content:
        axes[address] = (True, SCRIPT_MARKER not in address)
    return axes


def _apply_axis(axes: dict[str, tuple[bool, bool]], entry: MemberAxes) -> None:
    """把显式轴写入事实，并拒绝不存在的成员与不可表达的组合。"""
    if entry.member not in axes:
        raise ValueError(f"成员轴不在本次授予范围内：{entry.member}")
    if entry.member.startswith("skill/"):
        if not entry.visible:
            raise ValueError("skill 没有可见性轴，只能授予或不授予")
        if SCRIPT_MARKER not in entry.member and not entry.available:
            raise ValueError("skill 正文/附件始终可读，不支持 available=false")
    if not entry.visible and entry.available:
        raise ValueError("不可见成员不能同时可用")
    axes[entry.member] = (entry.visible, entry.available)
