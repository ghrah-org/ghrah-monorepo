# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""装配输入 v3、启动回执与配置读面的契约测试。

覆盖三件事：新建/恢复两种模式的形状约束、``limits`` 必填（Core 快照是唯一基准，
端口不得靠默认值兜底）、以及 M0 已删字段确实回不来。
"""

import pytest
from pydantic import ValidationError

from ghrah.protocol.views.core import (
    AgentAssemblySpec,
    AgentConfigView,
    AgentMemberPermissions,
    AgentStartView,
)

_CONFIG = {
    "model": {"endpoint_name": "default-llm"},
    "prompt": {"system_prompt": "do the thing"},
    "limits": {"max_iterations": -1, "communication_timeout": 300},
}

_MEMBERS = [{"member": "ghrah.fs.read_file"}]


def _spec(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"name": "weekly", "config": _CONFIG, "members": _MEMBERS}
    payload.update(overrides)
    return payload


def test_create_mode_requires_config_and_accepts_members() -> None:
    spec = AgentAssemblySpec.model_validate(_spec())
    assert spec.agent_id == ""
    assert spec.config is not None
    assert spec.members[0].member == "ghrah.fs.read_file"


def test_recovery_mode_carries_only_agent_id() -> None:
    spec = AgentAssemblySpec.model_validate({"name": "weekly", "agent_id": "a" * 32})
    assert spec.config is None
    assert spec.members == []


def test_recovery_mode_rejects_config() -> None:
    with pytest.raises(ValidationError, match="must not carry config"):
        AgentAssemblySpec.model_validate({"name": "weekly", "agent_id": "a", "config": _CONFIG})


def test_recovery_mode_rejects_members() -> None:
    with pytest.raises(ValidationError, match="must not carry members"):
        AgentAssemblySpec.model_validate({"name": "weekly", "agent_id": "a", "members": _MEMBERS})


def test_create_mode_without_config_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requires config"):
        AgentAssemblySpec.model_validate({"name": "weekly"})


def test_limits_is_required() -> None:
    config = {key: value for key, value in _CONFIG.items() if key != "limits"}
    with pytest.raises(ValidationError):
        AgentAssemblySpec.model_validate({"name": "weekly", "config": config})


def test_max_iterations_is_unbounded_upward_but_not_below_minus_one() -> None:
    config = {**_CONFIG, "limits": {"max_iterations": 100000, "communication_timeout": 1}}
    assert AgentAssemblySpec.model_validate(_spec(config=config)).config is not None
    config = {**_CONFIG, "limits": {"max_iterations": -2, "communication_timeout": 1}}
    with pytest.raises(ValidationError):
        AgentAssemblySpec.model_validate(_spec(config=config))


@pytest.mark.parametrize(
    "extra",
    [
        {"external_ref": "x"},
        {"scope_ref": "x"},
        {"assembly_digest": "x"},
        {"desired_state": "running"},
        {"path_policy": {}},
    ],
)
def test_m0_removed_fields_stay_rejected(extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentAssemblySpec.model_validate({**_spec(), **extra})


def test_unknown_config_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentAssemblySpec.model_validate(
            _spec(config={**_CONFIG, "endpoint_name": "flat-wrong-shaped"})
        )


def test_member_permissions_null_means_not_requested() -> None:
    """未申请的维度保持 None，Core 据此沿用包声明上限而不是收紧。"""
    requested = AgentMemberPermissions.model_validate({"fs_write": True})
    assert requested.model_fields_set == {"fs_write"}
    assert requested.model_dump()["fs_write"] is True
    assert requested.model_dump()["shell_access"] is None

    untouched = AgentMemberPermissions()
    assert untouched.model_fields_set == set()
    assert all(value is None for value in untouched.model_dump().values())


def test_member_name_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        AgentAssemblySpec.model_validate(_spec(members=[{"member": ""}]))


@pytest.mark.parametrize("mode", ["created", "resumed"])
def test_start_view_accepts_both_recovery_modes(mode: str) -> None:
    view = AgentStartView(agent_id="a" * 32, name="weekly", recovery_mode=mode)
    assert view.recovery_mode == mode


def test_start_view_rejects_unknown_recovery_mode() -> None:
    with pytest.raises(ValidationError):
        AgentStartView(agent_id="a", recovery_mode="rebooted")


def test_config_view_has_no_project_identity() -> None:
    assert "project_id" not in AgentConfigView.model_fields
    view = AgentConfigView(agent_id="a" * 32)
    assert view.core_only is None
    assert view.context.max_iterations == -1
    assert view.ability.grants == []


def test_config_view_rejects_project_id() -> None:
    with pytest.raises(ValidationError):
        AgentConfigView.model_validate({"agent_id": "a", "project_id": "p"})
