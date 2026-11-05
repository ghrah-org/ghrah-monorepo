# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import bridge_command, mount_unit
from ghrah.subject.runtime.service_keys import (
    WORKSPACE_MANAGER,
    WORKSPACE_SERVICE,
)
from ghrah.subject.sandbox.executor import SandboxExecutor
from ghrah.subject.units.sandbox import SandboxUnit
from ghrah.subject.units.workspace import WorkspaceUnit
from ghrah.subject.workspace import path_to_locator

if TYPE_CHECKING:
    from ouroboros import Fiber  # type: ignore[import-untyped]


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
    )


def _mount(ctx: Context, config: SubjectConfig) -> tuple[WorkspaceUnit, Fiber]:
    """挂 sandbox（依赖）+ workspace，返回 (unit, fiber)。"""

    ctx.plugin(mount_unit(SandboxUnit(config)))
    unit = WorkspaceUnit(config)
    fiber = ctx.plugin(mount_unit(unit))
    return unit, fiber


class _ProjectManagerStub:
    async def handle_command(self, command: str, payload: dict[str, object]) -> dict[str, object]:
        assert command == "project_get"
        return {
            "success": True,
            "data": {
                "project": {
                    "project_id": "p1",
                    "agents": [
                        {
                            "project_id": "p1",
                            "agent_id": "a1",
                            "name": "agent-a",
                            "cluster_id": "c1",
                        }
                    ],
                }
            },
        }


def _agent_payload() -> dict[str, str]:
    return {"project_id": "p1", "agent_id": "a1", "agent_name": "agent-a"}


async def test_workspace_unit_registers_typed_service_and_missing_workspace_result(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    async with Context() as ctx:
        unit, fiber = _mount(ctx, config)
        await wait_active(fiber)

        service = ctx.get(WORKSPACE_SERVICE.name)
        existing_path = Path(service.root_path) / "agent-existing"
        existing_path.mkdir()

        assert service is unit.service
        assert service.resolve_agent_path("agent-existing") == str(existing_path)
        assert service.resolve_agent_path("missing") is None

        result = await bridge_command(ctx, "workspace_status", {"agent_name": "missing"})
        assert result["success"] is False
        assert result["error"] == "project_id required"

        # 已退役版本能力命令：静态拒绝（不经 agent 解析，无需 project_id）
        for command in ("workspace_snapshot", "workspace_rollback", "workspace_diff"):
            retired = await bridge_command(ctx, command, {"agent_name": "agent-a"})
            assert retired["success"] is False
            assert "capability_not_supported" in retired["error"]


async def test_core_lifecycle_events_drive_workspace_create_destroy(
    tmp_path: Path,
) -> None:
    """core: 域生命周期事件驱动 workspace 建立/销毁（死接线回归）。"""

    config = _config(tmp_path)

    async with Context() as ctx:
        unit, fiber = _mount(ctx, config)
        await wait_active(fiber)

        # CoreUnit 只以 core: 前缀发射 agent 生命周期事件
        ctx.emit(
            "core:agent_spawned",
            {"name": "agent-a", "project_id": "p1", "agent_id": "a1"},
        )
        for _ in range(200):
            if unit.manager.get_workspace("a1") is not None:
                break
            await asyncio.sleep(0.01)
        assert unit.manager.get_workspace("a1") is not None

        ctx.emit(
            "core:agent_terminated",
            {"name": "agent-a", "project_id": "p1", "agent_id": "a1"},
        )
        for _ in range(200):
            if unit.manager.get_workspace("a1") is None:
                break
            await asyncio.sleep(0.01)
        assert unit.manager.get_workspace("a1") is None

        # 无 agent_id 的事件（不满足归属契约）不触发任何 workspace 操作
        ctx.emit("core:agent_spawned", {"name": "agent-a"})
        await asyncio.sleep(0.05)
        assert unit.manager.get_workspace("a1") is None


async def test_workspace_register_get_list_commands(tmp_path: Path) -> None:
    """workspace_register/get/list 3 新命令。"""

    config = _config(tmp_path)

    async with Context() as ctx:
        _, fiber = _mount(ctx, config)
        await wait_active(fiber)
        ctx.provide("project_manager", _ProjectManagerStub())

        created = await bridge_command(ctx, "create_workspace", _agent_payload())
        assert created["success"] is True

        # register：把一个已存在的目录登记为 plain workspace
        data_dir = os.path.join(config.sandbox.workspace_root, "data-dir")
        os.makedirs(data_dir)
        reg = await bridge_command(
            ctx,
            "workspace_register",
            {"locator": path_to_locator(data_dir), "name": "data", "provider_type": "plain"},
        )
        assert reg["success"] is True
        wid = reg["data"]["workspace_id"]
        assert reg["data"]["provider_type"] == "plain"

        # get：按 workspace_id 取记录
        got = await bridge_command(ctx, "workspace_get", {"workspace_id": wid})
        assert got["success"] is True
        assert got["data"]["workspace_id"] == wid
        assert got["data"]["name"] == "data"

        # get：未知 workspace_id 失败
        miss = await bridge_command(ctx, "workspace_get", {"workspace_id": "nope"})
        assert miss["success"] is False

        # list：无过滤含 agent-a 与 data
        lst = await bridge_command(ctx, "workspace_list", {})
        assert lst["success"] is True
        ids = {w["workspace_id"] for w in lst["data"]["workspaces"]}
        assert wid in ids

        # list：按 plain 过滤仍含两者（挂载语义下 create 的默认 workspace 也是 plain）
        lst_plain = await bridge_command(ctx, "workspace_list", {"provider_type": "plain"})
        plain_ids = {w["workspace_id"] for w in lst_plain["data"]["workspaces"]}
        assert wid in plain_ids


async def test_workspace_on_ouroboros_mount_route_dispose_remount(
    tmp_path: Path,
) -> None:
    """试点场景并入：挂载/服务可见/命令路由/卸载回退/重挂恢复闭环。"""

    config = _config(tmp_path)

    async with Context() as ctx:
        sandbox_fiber = ctx.plugin(mount_unit(SandboxUnit(config)))
        await wait_active(sandbox_fiber)
        assert isinstance(ctx.get("sandbox_executor"), SandboxExecutor)

        ws_fiber = ctx.plugin(mount_unit(WorkspaceUnit(config)))
        await wait_active(ws_fiber)
        ctx.provide("project_manager", _ProjectManagerStub())

        assert ctx.get(WORKSPACE_SERVICE.name) is not None
        assert ctx.get(WORKSPACE_MANAGER.name) is not None

        created = await bridge_command(ctx, "create_workspace", _agent_payload())
        assert created["success"] is True
        assert created["data"]["agent_name"] == "agent-a"
        assert "path" in created["data"]

        await ws_fiber.dispose()
        await asyncio.sleep(0.05)

        assert ctx.get(WORKSPACE_SERVICE.name, strict=False) is None
        assert ctx.get(WORKSPACE_MANAGER.name, strict=False) is None

        gone = await bridge_command(ctx, "workspace_status", _agent_payload())
        assert gone == {"success": False, "error": "Unknown command: workspace_status"}

        ws_fiber2 = ctx.plugin(mount_unit(WorkspaceUnit(config)))
        await wait_active(ws_fiber2)

        assert ctx.get(WORKSPACE_SERVICE.name) is not None
        recovered = await bridge_command(ctx, "workspace_status", _agent_payload())
        assert recovered["success"] is True
        assert recovered["data"]["agent_id"] == "a1"
        # status 降级为存在性/可写性
        assert recovered["data"]["exists"] is True
        assert recovered["data"]["writable"] is True

        await ws_fiber2.dispose()
