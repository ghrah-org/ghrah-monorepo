# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project/Recovery units 集成测试（Ouroboros 形态）：coexistence 工厂 +
fake CoreClusterRegistry（假 CoreUnit 工厂）+ ProjectUnit/RecoveryUnit 挂载，
验证装配、project_create 命令经 ctx.serial 路由、reconcile bootstrap。

与旧基建形态的差异：mount 不自动触发 reconcile（reconcile 由装配层末尾触发）；
本文件显式调 ``svc.reconcile()`` 验证 bootstrap 语义。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ouroboros import Context, Fiber  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active

from ghrah.subject.config import RecoveryConfig, SubjectConfig
from ghrah.subject.core_cluster.registry import CoreClusterRegistry
from ghrah.subject.runtime.ouroboros_bridge import bridge_command, mount_unit
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    PROJECT_MANAGER,
    RECONCILIATION_SERVICE,
)
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units import mount_builtin_units
from ghrah.subject.units.project import ProjectUnit
from ghrah.subject.units.recovery import RecoveryUnit
from ghrah.subject.workspace.locator import path_to_locator


class _FakeCoreUnit(SubjectUnit):
    """假 CoreUnit：记录命令并返回成功回执（spawn 返回 name）。"""

    def __init__(self, cluster_id: str) -> None:
        self.cluster_id = cluster_id
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self._meta = UnitMeta(
            name=f"fake-core-{cluster_id}",
            routes=RouteSpec(commands=frozenset()),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        self.commands.append((command, dict(payload)))
        if command == "list_agents":
            return {"success": True, "data": {"agents": []}}
        if command == "spawn_agent":
            name = payload.get("config", {}).get("name", "")
            return {"success": True, "data": {"name": name}}
        return {"success": True, "data": {}}


def _fake_registry() -> CoreClusterRegistry:
    def factory(cluster_id: str, project_id: str, project_root_locator: str) -> _FakeCoreUnit:
        return _FakeCoreUnit(cluster_id)

    return CoreClusterRegistry(unit_factory=factory)


def _config(tmp_path: Path, *, recovery_enabled: bool = True) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        recovery_slice=RecoveryConfig(
            enabled=recovery_enabled,
            reconcile_on_start=recovery_enabled,
            bootstrap_default_project=True,
        ),
    )


@asynccontextmanager
async def _boot(
    tmp_path: Path, *, recovery_enabled: bool = True
) -> AsyncIterator[tuple[Context, dict[str, Fiber], CoreClusterRegistry]]:
    config = _config(tmp_path, recovery_enabled=recovery_enabled)
    async with Context() as ctx:
        # fake cluster registry：root provide（core_cluster_registry unit 的假件）
        registry = _fake_registry()
        registry.bind(ctx)
        ctx.provide(CORE_CLUSTER_REGISTRY.name, registry)

        fibers = await mount_builtin_units(ctx, config, profile="coexistence")
        # project/recovery 逐个挂载等 ACTIVE（对齐工厂顺序语义）
        fibers["project"] = ctx.plugin(mount_unit(ProjectUnit(config)))
        await wait_active(fibers["project"])
        fibers["recovery"] = ctx.plugin(mount_unit(RecoveryUnit(config)))
        await wait_active(fibers["recovery"])
        try:
            yield ctx, fibers, registry
        finally:
            await registry.stop()


class TestUnitsAssembly:
    async def test_units_registered_and_started(self, tmp_path: Path) -> None:
        async with _boot(tmp_path) as (_, fibers, _):
            assert "project" in fibers
            assert "recovery" in fibers
            assert fibers["project"].state.value == "active"
            assert fibers["recovery"].state.value == "active"

    async def test_reconcile_bootstrap_creates_default_project(self, tmp_path: Path) -> None:
        async with _boot(tmp_path) as (ctx, _, _):
            svc = ctx.get(RECONCILIATION_SERVICE.name)
            # 显式触发首启 reconcile（自动触发归装配层）：无 workspace → bootstrap
            report = await svc.reconcile()
            assert report.success
            assert report.bootstrap is True
            assert report.tasks_migrated == 0  # 无 sentinel task
            # default project 已创建
            project_mgr = ctx.get(PROJECT_MANAGER.name)
            listed = await project_mgr.handle_command("project_list", {})
            assert listed["success"]
            assert listed["data"]["count"] == 1
            assert listed["data"]["projects"][0]["name"] == "default"

    async def test_project_create_command_via_dispatcher(self, tmp_path: Path) -> None:
        async with _boot(tmp_path) as (ctx, _, _):
            # 挂载语义：project_create 登记已有目录，不预创建（fail-closed）
            (tmp_path / "p2").mkdir()
            result = await bridge_command(
                ctx,
                "project_create",
                {
                    "name": "P2",
                    "writable_workspaces": [
                        {
                            "locator": path_to_locator(str(tmp_path / "p2")),
                            "name": "default",
                            "default_for_agents": True,
                        }
                    ],
                },
            )
            assert result["success"], result.get("error")
            assert result["data"]["project"]["name"] == "P2"

    async def test_reconcile_now_command(self, tmp_path: Path) -> None:
        async with _boot(tmp_path) as (ctx, _, _):
            result = await bridge_command(ctx, "reconcile_now", {"subject_id": "default"})
            assert result["success"]
            assert "projects_reconciled" in result["data"]

    async def test_reconcile_status_command(self, tmp_path: Path) -> None:
        async with _boot(tmp_path) as (ctx, _, _):
            # 先触发一次 reconcile
            await bridge_command(ctx, "reconcile_now", {})
            result = await bridge_command(ctx, "reconcile_status", {})
            assert result["success"]
            assert result["data"] is not None

    async def test_recovery_disabled_skips_reconcile(self, tmp_path: Path) -> None:
        async with _boot(tmp_path, recovery_enabled=False) as (ctx, _, _):
            svc = ctx.get(RECONCILIATION_SERVICE.name)
            status = await svc.reconcile_status()
            # mount 不自动 reconcile（reconcile 由装配层触发）→ 无痕迹
            assert status is None
