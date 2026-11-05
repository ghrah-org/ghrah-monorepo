# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ExecutionServices tests：执行期服务表。"""

from __future__ import annotations

import pytest

from ghrah.ability.execution.execution_services import (
    CONTEXT_MANAGER,
    LOOP,
    SUPERVISOR,
    ExecutionServices,
    build_execution_services,
)
from ghrah.ability.execution.loop_data import LoopData


def test_get_set_and_require() -> None:
    context_manager = object()
    services = ExecutionServices({"supervisor": "sup"})
    services.set(CONTEXT_MANAGER, context_manager)

    assert services.get(SUPERVISOR) == "sup"
    assert services.require(CONTEXT_MANAGER) is context_manager


def test_require_missing_raises_clear_error() -> None:
    services = ExecutionServices()

    with pytest.raises(RuntimeError, match="context_manager"):
        services.require(CONTEXT_MANAGER)


def test_snapshot_is_shallow_copy() -> None:
    services = ExecutionServices({"supervisor": "sup"})

    snapshot = services.snapshot()
    snapshot["supervisor"] = "changed"

    assert services.get(SUPERVISOR) == "sup"


def test_build_execution_services_wires_loop_and_skips_unwired() -> None:
    loop = LoopData({"llm_response": "hi"})
    services = build_execution_services(context_manager=object(), loop=loop)

    assert services.require(LOOP) is loop
    assert services.get(SUPERVISOR) is None


def test_build_execution_services_does_not_wire_none() -> None:
    """漏传的依赖保持“未接线”语义（get 为 None），不补默认对象。"""
    services = build_execution_services()

    assert services.snapshot() == {}
    assert services.get(LOOP) is None
