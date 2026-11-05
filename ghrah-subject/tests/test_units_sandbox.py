# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import mount_unit
from ghrah.subject.runtime.service_keys import SANDBOX_EXECUTOR
from ghrah.subject.units.sandbox import SandboxUnit


async def test_sandbox_unit_lifecycle_and_service_registration(tmp_path: Path) -> None:
    config = SubjectConfig(workspace_root=str(tmp_path / "workspace"))
    unit = SandboxUnit(config)

    async with Context() as ctx:
        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)

        service = ctx.get(SANDBOX_EXECUTOR.name)
        assert service is unit.service
        assert service.workspace_root == str(tmp_path / "workspace")
        assert service.config.default_timeout == config.sandbox.default_timeout

        await fiber.dispose()
        assert ctx.get(SANDBOX_EXECUTOR.name, strict=False) is None
