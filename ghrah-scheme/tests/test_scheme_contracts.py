# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""模型与预算的拒绝场景，防止无界配置及契约漂移。"""

import pytest
from ghrah_scheme_support import SPEC, Arguments, Output
from pydantic import ValidationError

from ghrah.scheme import (
    ActionSpec,
    Authorization,
    Principal,
    RuntimeLimits,
    SchemeModel,
    SchemeSpec,
)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, 0.5])
def test_count_limits_reject_invalid_values(value: object) -> None:
    with pytest.raises(ValueError):
        RuntimeLimits(max_inflight=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_time_limits_reject_invalid_values(value: object) -> None:
    with pytest.raises(ValueError):
        RuntimeLimits(default_timeout=value)  # type: ignore[arg-type]


def test_schemes_reject_duplicate_actions_and_bad_identity() -> None:
    with pytest.raises(ValueError):
        SchemeSpec("ability", "1", (SPEC.actions[0], SPEC.actions[0]))
    for name in ("", "../ability", "Ability", "a/b"):
        with pytest.raises(ValueError):
            SchemeSpec(name, "1", SPEC.actions)
    with pytest.raises(ValueError):
        SchemeSpec("ability", "", SPEC.actions)


def test_action_models_cannot_relax_validation() -> None:
    class Loose(SchemeModel):
        model_config = {"extra": "allow"}

    with pytest.raises(ValueError):
        ActionSpec("read", Loose, Output)


def test_model_strictness_and_unknown_fields() -> None:
    for value in ({"value": True}, {"value": "ok", "principal": "Admin"}):
        with pytest.raises(ValidationError):
            Arguments.model_validate(value)


def test_authorization_requires_basis_and_principal_requires_scope() -> None:
    with pytest.raises(ValueError):
        Authorization(True)
    with pytest.raises(ValueError):
        Principal("", "a")
    with pytest.raises(ValueError):
        Principal("Alice", "")
