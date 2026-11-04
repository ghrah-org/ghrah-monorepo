# SPDX-License-Identifier: Apache-2.0
"""启动期授权策略形状：目标递进、默认值、确定性顺序与歧义拒绝。"""

from __future__ import annotations

import pytest

from ghrah.ability.access.policy import GrantPolicy, GrantRule


def test_empty_policy_means_only_template_driven_grants() -> None:
    """空策略是合法结果，也是“未配置”与“配置为空”的共同起点。"""
    policy = GrantPolicy()
    assert policy.mode == "merge"
    assert policy.rules == ()
    assert policy.ordered() == ()
    assert policy.for_agent("alice") == ()


def test_rule_defaults_are_visible_and_available() -> None:
    """包级规则缺省为一次普通授予：可见且可用。"""
    rule = GrantRule(package="acme.tools")
    assert rule.effect == "grant"
    assert rule.agent is None
    assert rule.member is None
    assert rule.visible is True and rule.available is True


@pytest.mark.parametrize(
    ("kwargs", "member"),
    [
        ({"operation": "read_file"}, "operations/read_file"),
        ({"skill": "guide"}, "skill/guide"),
        ({"skill": "guide", "script": "run"}, "skill/guide/scripts/run"),
    ],
)
def test_rule_member_address_follows_target_level(kwargs, member) -> None:
    """成员地址按目标层级递进，供关系库物化与审计引用。"""
    assert GrantRule(package="fs", **kwargs).member == member


def test_ordered_puts_revocations_first_then_grants_by_target() -> None:
    """应用顺序固定：先移除后授予；组内按 Agent、包与目标排序。"""
    policy = GrantPolicy(
        rules=(
            GrantRule(package="fs", skill="beta", effect="revoke"),
            GrantRule(package="fs", operation="write_file"),
            GrantRule(package="fs", skill="alpha", script="run"),
            GrantRule(package="fs", operation="read_file", agent="alice"),
            GrantRule(package="acme.tools"),
        )
    )
    assert [(rule.effect, rule.agent, rule.package, rule.member) for rule in policy.ordered()] == [
        ("revoke", None, "fs", "skill/beta"),
        ("grant", None, "acme.tools", None),
        ("grant", None, "fs", "operations/write_file"),
        ("grant", None, "fs", "skill/alpha/scripts/run"),
        ("grant", "alice", "fs", "operations/read_file"),
    ]


def test_ordered_is_independent_of_input_order_and_dedupes() -> None:
    """同一语义的策略换序或重复后仍给出同一序列与摘要。"""
    rules = (
        GrantRule(package="fs", operation="read_file"),
        GrantRule(package="fs", skill="guide", visible=True),
    )
    forward = GrantPolicy(rules=rules)
    backward = GrantPolicy(rules=(rules[1], rules[0], rules[0]))
    assert forward.ordered() == backward.ordered()
    assert forward.digest == backward.digest


def test_for_agent_keeps_global_rules_and_orders_them() -> None:
    """Agent 级规则只对指定 Agent 生效；全局规则对所有 Agent 生效。"""
    policy = GrantPolicy(
        rules=(
            GrantRule(package="fs", agent="bob"),
            GrantRule(package="fs", agent="alice"),
            GrantRule(package="acme.tools"),
        )
    )
    assert [rule.agent for rule in policy.for_agent("alice")] == [None, "alice"]
    assert [rule.agent for rule in policy.for_agent("bob")] == [None, "bob"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"package": "fs", "operation": "read_file", "skill": "guide"},
        {"package": "fs", "script": "run"},
        {"package": "fs", "skill": "guide", "available": False},
        {"package": "fs", "operation": "read_file", "visible": False},
        {
            "package": "fs",
            "operation": "read_file",
            "effect": "revoke",
            "visible": False,
            "available": False,
        },
        {"package": "fs", "available": False},
        {"package": "fs", "visible": False},
        {"package": "fs", "skill": "guide", "script": "run", "visible": False, "available": False},
    ],
)
def test_ambiguous_or_impossible_rules_are_rejected(kwargs) -> None:
    """一条规则只能表达一个可解释的目标与轴组合。"""
    with pytest.raises(ValueError):
        GrantRule(**kwargs)


@pytest.mark.parametrize("package", ["FS", "9fs", "fs..tools", "fs.", ""])
def test_package_identity_must_be_namespace_or_namespace_name(package) -> None:
    """包身份沿用 AbilityIdentity.full_name 的两段写法。"""
    with pytest.raises(ValueError):
        GrantRule(package=package)


def test_conflicting_rules_for_same_target_are_rejected() -> None:
    """同一目标与效果不能给出两套轴，否则应用结果取决于顺序。"""
    with pytest.raises(ValueError, match="互相冲突"):
        GrantPolicy(
            rules=(
                GrantRule(package="fs", operation="read_file"),
                GrantRule(package="fs", operation="read_file", visible=False, available=False),
            )
        )


def test_revoke_and_grant_for_same_target_keep_grant_last() -> None:
    """移除后再授予是确定的：同一目标同时出现时授予在后。"""
    policy = GrantPolicy(
        rules=(
            GrantRule(package="fs", operation="read_file", effect="revoke"),
            GrantRule(package="fs", operation="read_file"),
        )
    )
    assert [rule.effect for rule in policy.ordered()] == ["revoke", "grant"]


def test_from_json_parses_and_rejects_unknown_fields() -> None:
    """配置侧 JSON 是唯一输入形状；未知字段不静默忽略。"""
    policy = GrantPolicy.from_json(
        '{"mode": "replace", "rules": [{"package": "fs", "operation": "read_file"}]}'
    )
    assert policy.mode == "replace"
    assert policy.rules[0].member == "operations/read_file"
    with pytest.raises(ValueError, match="授权策略无效"):
        GrantPolicy.from_json('{"rules": [{"package": "fs", "unknown": true}]}')
    with pytest.raises(ValueError, match="授权策略无效"):
        GrantPolicy.from_json('{"rules": [{"package": "fs"}], "unknown": 1}')


def test_digest_changes_when_axes_change() -> None:
    """摘要区分可见性与可用性：降级授予不是同一份策略。"""
    base = GrantPolicy(rules=(GrantRule(package="fs", operation="read_file"),))
    degraded = GrantPolicy(rules=(GrantRule(package="fs", operation="read_file", available=False),))
    assert base.digest.startswith("sha256:")
    assert base.digest != degraded.digest
