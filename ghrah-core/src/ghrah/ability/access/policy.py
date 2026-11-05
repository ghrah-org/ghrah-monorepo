# SPDX-License-Identifier: Apache-2.0
"""启动期授权策略的显式形状：模板之外只允许被明确覆盖、扩充或移除。

这一层只表达**部署方想要什么**，不产生生效授权：策略在 Agent 启动时由核定器
转成目标集合，diff 后经关系库写入才算生效。因此本模块只做三件事：

- 定义扁平的规则与整体模式（``replace`` / ``merge``）；
- 拒绝歧义表达（同一目标两套轴、script 没有所属 skill、给 skill 表达
  “不可用”这类不存在的轴）；
- 给出确定性顺序与摘要，供幂等应用与审计引用。

规则的目标从上到下递进：包、Operation、skill、script。包级规则不带成员键，
整包展开在核定与关系库写入时完成；skill 只有可见性（可见即可读），
Operation 与 script 才有可见性与可用性两轴。
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from ghrah.protocol.ability_tree import TreeId, TreeKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ghrah.ability.access.identity import validate_agent_id

__all__ = ["GrantMode", "GrantEffect", "GrantPolicy", "GrantRule"]

GrantMode = Literal["replace", "merge"]
"""整体模式：``replace`` 用策略集合替换模板结果；``merge`` 在模板结果上增删。"""

GrantEffect = Literal["grant", "revoke"]
"""单条规则的效果：授予或移除。应用顺序固定为先移除后授予。"""

# 包身份全名：namespace 或 namespace.name（与 AbilityIdentity.full_name 对齐）。
_PACKAGE_PATTERN = r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)?$"


class GrantRule(BaseModel):
    """一条启动期授权规则；扁平表达，不嵌套目标对象。

    Attributes:
        package: 包身份全名（``namespace`` 或 ``namespace.name``）。
        agent: 只对某个 Agent 生效；None 表示对本次启动的所有 Agent 生效。
        operation: 目标 Operation 键；None 表示不针对单个 Operation。
        skill: 目标 skill 键；None 表示不针对单个 skill。
        script: 目标 script 键，必须同时给出 ``skill``。
        effect: 授予还是移除。
        visible: 是否进入模型可见面。
        available: 是否可执行；skill 没有这一轴。
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    package: str = Field(pattern=_PACKAGE_PATTERN)
    agent: TreeId | None = None
    operation: TreeKey | None = None
    skill: TreeKey | None = None
    script: TreeKey | None = None
    effect: GrantEffect = "grant"
    visible: bool = True
    available: bool = True

    @field_validator("agent")
    @classmethod
    def _stable_agent(cls, value: str | None) -> str | None:
        return validate_agent_id(value) if value is not None else None

    @model_validator(mode="after")
    def _single_target(self) -> GrantRule:
        """一条规则只指向一个授权目标，且目标组合必须可解释。"""
        if self.operation is not None and (self.skill is not None or self.script is not None):
            raise ValueError("一条规则只能指向一个授权目标")
        if self.script is not None and self.skill is None:
            raise ValueError("script 目标必须同时给出所属 skill")
        if self.script is None and self.skill is not None and not self.available:
            raise ValueError("skill 只有可见性，不支持 available=false")
        if not self.visible and self.available:
            raise ValueError("不可见的成员不能同时可用")
        if self.member is None and not (self.visible and self.available):
            raise ValueError("包级规则不表达成员轴；请对 Operation 或脚本单独授予")
        if self.member is not None and self.member.startswith("skill/") and not self.visible:
            raise ValueError("内容成员不可见等同于不授予；请改用移除规则")
        if self.effect == "revoke" and not (self.visible and self.available):
            raise ValueError("移除规则不表达可见性；需要保留请改为授予")
        return self

    @property
    def member(self) -> str | None:
        """目标成员的规范地址；包级规则没有成员地址。

        Returns:
            ``operations/<key>``、``skill/<key>``、
            ``skill/<key>/scripts/<script>`` 或 None（整包）。
        """
        if self.operation is not None:
            return f"operations/{self.operation}"
        if self.script is not None:
            return f"skill/{self.skill}/scripts/{self.script}"
        if self.skill is not None:
            return f"skill/{self.skill}"
        return None


class GrantPolicy(BaseModel):
    """部署方的启动期授权策略；未配置（None）等于不授予任何能力。

    Attributes:
        mode: ``replace`` 表示策略集合就是目标集合；``merge`` 表示在模板
            结果上先移除再授予。
        rules: 扁平规则序列；顺序不影响结果，重复且一致的规则会被去重。
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    mode: GrantMode = "merge"
    rules: tuple[GrantRule, ...] = ()

    @model_validator(mode="after")
    def _conflict_free(self) -> GrantPolicy:
        """同一目标与效果只能有一套轴；重复但一致的规则允许出现。"""
        seen: dict[tuple[str | None, str, str | None, str], GrantRule] = {}
        for rule in self.rules:
            key = (rule.agent, rule.package, rule.member, rule.effect)
            previous = seen.get(key)
            if previous is not None and previous != rule:
                raise ValueError("同一目标与效果的规则给出了互相冲突的成员轴")
            seen[key] = rule
        return self

    @classmethod
    def from_json(cls, raw: str) -> GrantPolicy:
        """从部署配置里的 JSON 文本解析策略；非法内容直接报错，不猜测。

        Args:
            raw: JSON 文本，形如
                ``{"mode": "merge", "rules": [{"package": "fs"}]}``。

        Returns:
            校验通过的策略。

        Raises:
            ValueError: 文本不是 JSON 或不符合策略形状。
        """
        try:
            return cls.model_validate_json(raw)
        except ValueError as exc:
            raise ValueError(f"授权策略无效：{exc}") from None

    def for_agent(self, agent: str) -> tuple[GrantRule, ...]:
        """按 Agent 过滤并给出确定性顺序。

        Args:
            agent: 正在启动的 Agent 标识。

        Returns:
            先移除后授予、组内按目标排序的规则；``agent`` 为空的规则对所有
            Agent 生效。
        """
        return tuple(rule for rule in self.ordered() if rule.agent is None or rule.agent == agent)

    def ordered(self) -> tuple[GrantRule, ...]:
        """给出确定性顺序：先 revoke 后 grant，组内按目标排序并去重。

        Returns:
            规范化后的规则序列；同一输入永远得到同一序列。
        """
        unique: dict[tuple[str | None, str, str | None, str], GrantRule] = {}
        for rule in self.rules:
            unique.setdefault((rule.agent, rule.package, rule.member, rule.effect), rule)
        revoked = sorted(
            (rule for rule in unique.values() if rule.effect == "revoke"), key=_rule_order
        )
        granted = sorted(
            (rule for rule in unique.values() if rule.effect == "grant"), key=_rule_order
        )
        return (*revoked, *granted)

    @property
    def digest(self) -> str:
        """策略内容的确定性摘要，写入变更历史以回答“基于哪条策略”。

        Returns:
            ``sha256:<hex>``；规则顺序不同但语义相同的策略摘要相同。
        """
        payload = {
            "mode": self.mode,
            "rules": [rule.model_dump(exclude_none=True) for rule in self.ordered()],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _rule_order(rule: GrantRule) -> tuple[str, str, int, str, str]:
    """确定性排序键：Agent、包、目标层级、目标名、脚本名。

    Args:
        rule: 待排序的规则。

    Returns:
        可直接比较的元组；层级序保证包规则在前、Operation/skill 居中、
        script 殿后。
    """
    level = 0 if rule.member is None else (2 if rule.script is not None else 1)
    return (
        rule.agent or "",
        rule.package,
        level,
        rule.operation or rule.skill or "",
        rule.script or "",
    )
