# SPDX-License-Identifier: Apache-2.0
"""启动核定：把模板与显式策略算成目标事实，再以 CAS 写进关系库。

启动时只做一件事：把“这个 Agent 想要什么（模板）经部署方显式策略变换后应该
得到什么”算成确定的成员集合，与关系库现有事实比较，只写实际变化。运行期
读取只认关系库，不重新读取模板或策略。

约束：

- **单一写方**：只经 ``AbilityStore.write_grant`` / ``write_revoke`` 落库；
- **幂等**：相同模板、相同策略、相同库状态重复执行不产生新历史；
- **CAS 重试**：并发写入以 ``expected_revision`` 冲突即重读重算，不盲目覆盖；
- **失败即失败**：策略引用本次启动未解析的包、目标成员不存在时直接报错，
  不降级成“模板自动授予”。
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ghrah.protocol.ability_tree import RevokeAbilityRequest

from ghrah.ability.access.facts import (
    GrantFacts,
    skill_scope_facts,
    whole_container_facts,
)
from ghrah.ability.access.policy import GrantPolicy, GrantRule
from ghrah.ability.access.store import AbilityAccessStore, AccessError, AccessIdentity

__all__ = [
    "GrantInstance",
    "GrantReconcileError",
    "ReconciliationResult",
    "apply_grants",
    "desired_grants",
]

logger = logging.getLogger(__name__)

DEFAULT_CAS_ATTEMPTS = 3
"""CAS 冲突后的最大重试次数；超出即失败，不无限重试。"""


class GrantReconcileError(ValueError):
    """启动核定拒绝：策略或模板无法与本次启动的实例集合对齐。"""


@dataclass(frozen=True)
class GrantInstance:
    """本次启动可授予的一个能力包实例。

    Attributes:
        alias: Agent 内的能力别名（工具名限定与展示用）。
        package: 包身份全名（``namespace`` 或 ``namespace.name``）。
        instance_id: 关系库里的实例标识。
        version_digest: 已提交版本摘要。
        definition: 版本定义（整包展开与成员校验的唯一依据）。
        allowed_roots: 文件类成员的允许根；必须落在实例根内。
    """

    alias: str
    package: str
    instance_id: str
    version_digest: str
    definition: dict[str, Any]
    allowed_roots: tuple[str, ...] = ()

    @property
    def facts(self) -> GrantFacts:
        """整包授予事实（脚本可见、执行默认不授予）。"""
        return whole_container_facts(self.definition)


@dataclass(frozen=True)
class ReconciliationResult:
    """一次启动核定的结果摘要。

    Attributes:
        revision: 核定完成后的 Agent 关系修订。
        granted: 实际写入过获授变更的 alias。
        revoked: 实际撤销的 alias。
        desired: 核定得到的目标事实（按 alias）。
    """

    revision: int
    granted: tuple[str, ...]
    revoked: tuple[str, ...]
    desired: dict[str, GrantFacts]


def desired_grants(
    *,
    instances: Collection[GrantInstance],
    template: Mapping[str, Collection[str]],
    policy: GrantPolicy | None,
    agent: str,
) -> dict[str, GrantFacts]:
    """计算本次启动的目标获授事实。

    Args:
        instances: 本次启动已解析的包实例（含整包展开所需定义）。
        template: 模板结果：``alias -> 申请的 Operation 键``；由 manifest
            的 ref/type 解析得到。
        policy: 部署方的启动策略；None 表示不配置（目标只由模板模式决定）。
        agent: 正在启动的 Agent 标识。

    Returns:
        ``alias -> 授予事实``；空事实不出现在结果里。

    Raises:
        GrantReconcileError: 模板或策略引用了未解析的包/成员。
    """
    catalog = _instance_catalog(instances)
    if policy is None:
        # 未配置策略 = 不授予：模板只表达申请，不自行生效。
        return {}
    desired = {} if policy.mode == "replace" else _template_facts(catalog, template)
    for rule in policy.for_agent(agent):
        instance = _instance_for_package(catalog, rule.package)
        target = rule.member
        if rule.effect == "revoke":
            if target is None:
                desired.pop(instance.alias, None)
            else:
                facts = desired.get(instance.alias, GrantFacts())
                desired[instance.alias] = facts.without_address(target)
                if desired[instance.alias].empty:
                    desired.pop(instance.alias)
            continue
        addition = _rule_facts(instance, rule)
        desired[instance.alias] = desired.get(instance.alias, GrantFacts()).union(addition)
    return {alias: facts for alias, facts in sorted(desired.items()) if not facts.empty}


async def apply_grants(
    store: AbilityAccessStore,
    actor: AccessIdentity,
    agent: str,
    desired: Mapping[str, GrantFacts],
    *,
    instances: Collection[GrantInstance],
    policy_digest: str | None = None,
    max_attempts: int = DEFAULT_CAS_ATTEMPTS,
) -> ReconciliationResult:
    """把目标事实幂等地写进关系库。

    只有与当前事实不同的 alias 才会产生写入；不在目标集合里的现有获授关系会被
    撤销。写入冲突时重读当前事实并重试，不盲目覆盖并发写入。

    Args:
        store: 关系库写入口。
        actor: 部署操作员身份；``actor.source`` 记入来源。
        agent: 正在启动的 Agent 标识。
        desired: :func:`desired_grants` 的结果。
        instances: 与 ``desired`` 对齐的实例集合。
        policy_digest: 本次核定依据的策略摘要，写入变更历史。
        max_attempts: CAS 冲突后的最大尝试次数。

    Returns:
        核定结果摘要。

    Raises:
        AccessError: 冲突重试耗尽、授予内容非法等。
    """
    catalog = _instance_catalog(instances)
    await store.ensure_agent(actor, agent)
    for attempt in range(1, max_attempts + 1):
        records = {record.alias: record for record in await store.grants(actor, agent)}
        revision = await store.agent_revision(actor, agent)
        granted: list[str] = []
        revoked: list[str] = []
        try:
            for alias in sorted(desired):
                instance = catalog[alias]
                if alias in records and _matches(records[alias], desired[alias], instance):
                    continue
                revision = await store.write_grant(
                    actor,
                    agent,
                    desired[alias].to_request(
                        alias=alias,
                        instance_id=instance.instance_id,
                        expected_revision=revision,
                        allowed_roots=instance.allowed_roots,
                        policy=policy_digest,
                    ),
                )
                granted.append(alias)
            for alias in sorted(set(records) - set(desired)):
                revision = await store.write_revoke(
                    actor,
                    agent,
                    RevokeAbilityRequest(alias=alias, expected_revision=revision),
                )
                revoked.append(alias)
            return ReconciliationResult(revision, tuple(granted), tuple(revoked), dict(desired))
        except AccessError as error:
            if error.code != "conflict" or attempt == max_attempts:
                raise
            logger.warning(
                "启动核定遇到并发修订冲突，重读后重试（%d/%d）：agent=%s",
                attempt,
                max_attempts,
                agent,
            )
    raise AssertionError("unreachable")  # pragma: no cover - 循环保证返回或抛错


def _instance_catalog(
    instances: Collection[GrantInstance],
) -> dict[str, GrantInstance]:
    """按 alias 建索引，并拒绝 alias 或包名重复。"""
    catalog: dict[str, GrantInstance] = {}
    packages: set[str] = set()
    for instance in instances:
        if instance.alias in catalog:
            raise GrantReconcileError(f"实例别名重复：{instance.alias}")
        if instance.package in packages:
            raise GrantReconcileError(f"同一包出现多个实例：{instance.package}")
        catalog[instance.alias] = instance
        packages.add(instance.package)
    return catalog


def _template_facts(
    catalog: Mapping[str, GrantInstance], template: Mapping[str, Collection[str]]
) -> dict[str, GrantFacts]:
    """把模板申请折算成授予事实；只授予模板点名的 Operation。"""
    result: dict[str, GrantFacts] = {}
    for alias, members in template.items():
        instance = catalog.get(alias)
        if instance is None:
            raise GrantReconcileError(f"模板引用了本次启动未解析的包实例：{alias}")
        exposed = {key for key, item in instance.definition["operations"].items() if item["expose"]}
        unknown = set(members) - exposed
        if unknown:
            raise GrantReconcileError(f"模板申请的 Operation 不可暴露：{alias}/{sorted(unknown)}")
        if not members:
            continue
        result[alias] = GrantFacts(
            members=frozenset(members),
            content=frozenset(),
            axes={f"operations/{key}": (True, True) for key in members},
        )
    return result


def _instance_for_package(catalog: Mapping[str, GrantInstance], package: str) -> GrantInstance:
    """按包身份定位本次启动的实例；未解析即拒绝，不回退到模板。"""
    for instance in catalog.values():
        if instance.package == package:
            return instance
    raise GrantReconcileError(f"策略引用了本次启动未解析的包：{package}")


def _rule_facts(instance: GrantInstance, rule: GrantRule) -> GrantFacts:
    """把一条授予规则折算成授予事实，并校验目标成员存在。"""
    definition = instance.definition
    if rule.operation is not None:
        operation = definition["operations"].get(rule.operation)
        if operation is None or not operation["expose"]:
            raise GrantReconcileError(
                f"策略授予的 Operation 不可暴露：{instance.package}/{rule.operation}"
            )
        key = rule.operation
        return GrantFacts(
            members=frozenset({key}),
            content=frozenset(),
            axes={f"operations/{key}": (rule.visible, rule.available)},
        )
    if rule.script is not None:
        skill = definition["skills"].get(rule.skill)
        if skill is None or rule.script not in (skill.get("scripts") or {}):
            raise GrantReconcileError(
                f"策略授予的脚本不存在：{instance.package}/{rule.skill}/{rule.script}"
            )
        address = f"skill/{rule.skill}/scripts/{rule.script}"
        return GrantFacts(
            members=frozenset(),
            content=frozenset({address}),
            axes={address: (True, rule.available)},
        )
    if rule.skill is not None:
        try:
            return skill_scope_facts(definition, rule.skill)
        except ValueError as error:
            raise GrantReconcileError(
                f"策略授予的 skill 不存在：{instance.package}/{rule.skill}"
            ) from error
    return instance.facts


def _matches(record: Any, target: GrantFacts, instance: GrantInstance) -> bool:
    """判断关系行与目标事实是否一致（含实例、成员、轴与允许根）。"""
    if record.instance_id != instance.instance_id:
        return False
    if sorted(record.members) != sorted(target.members):
        return False
    if sorted(record.content) != sorted(target.content):
        return False
    if record.axes != target.normalized_axes():
        return False
    return sorted(record.roots) == _resolved_roots(instance.allowed_roots)


def _resolved_roots(roots: tuple[str, ...]) -> list[str]:
    """把允许根解析为绝对路径并去重排序，与关系库存储口径一致。"""
    return sorted({str(Path(root).resolve()) for root in roots})
