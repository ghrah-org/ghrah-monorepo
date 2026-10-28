# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""每个场景独立创建运行单元，回收任务与观察者。"""

from collections.abc import AsyncIterator

import pytest
from ghrah_scheme_support import Harness, make_harness


@pytest.fixture
async def scheme_harness() -> AsyncIterator[Harness]:
    """结束前释放可控门并检查没有残余任务。"""
    harness = make_harness()
    yield harness
    for provider in (harness.a, harness.b):
        for gate in (provider.before, provider.after):
            if gate is not None:
                gate.set()
    for gate in (harness.policy.authorize_gate, harness.policy.admit_gate):
        if gate is not None:
            gate.set()
    report = await harness.runtime.manager.close()
    assert report.complete
    diagnostics = harness.runtime.manager.diagnostics()
    assert diagnostics["inflight"] == 0
    assert diagnostics["resource_refs"] == 0
    assert diagnostics["invocation_refs"] == 0
