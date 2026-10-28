# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""CoreClusterRegistry unit 测试（假 CoreUnit 工厂，无真实 Core）。

覆盖：装配与服务注册、ensure/get/shutdown 幂等、spawn 物化透传
（manifest_ref 展开为具体 abilities）、dispose 后 handle 失效、
二次 ensure 同实例。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ghrah.protocol.types import AgentConfigPayload, SpawnAgentPayload
from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active

from ghrah.subject.config import SubjectConfig
from ghrah.subject.core_cluster.registry import (
    CoreClusterRegistry,
)
from ghrah.subject.manifest_store.store import ManifestStore
from ghrah.subject.runtime.ouroboros_bridge import mount_unit
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    MANIFEST_STORE,
    SANDBOX_EXECUTOR,
    WORKSPACE_SERVICE,
)
from ghrah.subject.sandbox.executor import SandboxExecutor
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit

AGENT_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: ghrah
  name: coder
  description: A coder agent
model:
  endpoint_name: default
system_prompt: You are a coder.
max_iterations: 20
abilities:
  - type: read_file
    permissions:
      require_hitl: true
      allowed_paths:
        - "{{workspace}}/src"
  - type: conversation
"""

PROJECT_ID = "project-1"
PROJECT_ROOT = "file:///tmp/project-1"


class _FakeCoreUnit(SubjectUnit):
    """假 CoreUnit：记录命令并返回成功回执。"""

    def __init__(self, cluster_id: str) -> None:
        self.cluster_id = cluster_id
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.stopped = False
        self._meta = UnitMeta(
            name=f"fake-core-{cluster_id}",
            routes=RouteSpec(commands=frozenset()),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def stop(self) -> None:
        self.stopped = True

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        self.commands.append((command, dict(payload)))
        if command == "list_agents":
            return {"success": True, "data": {"agents": [{"name": "coder"}]}}
        if command == "spawn_agent":
            return {"success": True, "data": {"name": payload.get("config", {}).get("name", "")}}
        return {"success": True, "data": {}}


class _FlakyStartCoreUnit(_FakeCoreUnit):
    """按共享开关在 start 抛错的假 CoreUnit（验证挂载失败回收与重试）。

    失败开关由工厂持有（只失败第一次挂载），重试创建的新实例可正常启动。
    """

    def __init__(self, cluster_id: str, fail: dict[str, bool]) -> None:
        super().__init__(cluster_id)
        self._fail = fail
        self.start_attempts = 0

    async def start(self) -> None:
        self.start_attempts += 1
        if self._fail.get("on"):
            self._fail["on"] = False
            raise RuntimeError("boom")


class _FakeWorkspaceService:
    def __init__(self, workspace_root: str) -> None:
        self._workspace_root = workspace_root

    @property
    def root_path(self) -> str:
        return self._workspace_root

    async def create_workspace(self, agent_name: str) -> Any:
        return None

    def resolve_agent_path(self, agent_name: str) -> str | None:
        return str(Path(self._workspace_root) / agent_name)

    def resolve_agent_default_path(self, agent_name: str) -> str | None:
        return self.resolve_agent_path(agent_name)

    def get_workspace_record(self, workspace_id: str) -> Any | None:
        return None


def _manifest_store(tmp_path: Path) -> ManifestStore:
    store = ManifestStore(tmp_path / "manifests")
    store.ensure_dirs()
    store.put_agent("ghrah.coder", AGENT_YAML)
    return store


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
    )


async def _boot(
    tmp_path: Path,
    *,
    unit_factory: Any = None,
) -> tuple[Context, Any, CoreClusterRegistry, dict[str, _FakeCoreUnit]]:
    """挂载 registry unit（假工厂），返回 ctx/unit/registry/创建的假 units。"""
    created: dict[str, _FakeCoreUnit] = []

    def factory(cluster_id: str, project_id: str, project_root_locator: str) -> _FakeCoreUnit:
        unit = _FakeCoreUnit(cluster_id)
        created.append(unit)
        return unit

    config = _config(tmp_path)
    unit = CoreClusterRegistryUnit(config, unit_factory=unit_factory or factory)
    workspace = _FakeWorkspaceService(str(tmp_path / "workspace"))

    ctx = await Context().__aenter__()
    try:
        ctx.provide(MANIFEST_STORE.name, _manifest_store(tmp_path))
        ctx.provide(WORKSPACE_SERVICE.name, workspace)
        ctx.provide(SANDBOX_EXECUTOR.name, SandboxExecutor(str(tmp_path / "workspace")))

        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)
        registry = ctx.get(CORE_CLUSTER_REGISTRY.name)
        assert registry is unit.service
        return ctx, unit, registry, created
    except BaseException:
        await ctx.__aexit__(None, None, None)
        raise


class TestCoreClusterRegistryUnit:
    async def test_meta_shape(self, tmp_path: Path) -> None:
        unit = CoreClusterRegistryUnit(_config(tmp_path))
        assert unit.meta.name == "core_cluster_registry"
        assert {"spawn_agent", "list_agents", "send_message"} <= unit.meta.routes.commands
        assert {k.name for k in unit.meta.requires} == {
            "manifest_store",
            "workspace_service",
            "sandbox_executor",
        }

    async def test_ensure_cluster_mounts_and_is_idempotent(self, tmp_path: Path) -> None:
        ctx, _, registry, created = await _boot(tmp_path)

        def by_id(cluster_id: str) -> _FakeCoreUnit:
            return next(u for u in created if u.cluster_id == cluster_id)

        try:
            assert not registry.has_cluster("default")
            handle = await registry.ensure_cluster(
                "default", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            assert handle.is_connected
            assert registry.has_cluster("default")
            assert by_id("default").stopped is False

            # 幂等：同 cluster 返回同一 handle、不新建实例
            again = await registry.ensure_cluster(
                "default", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            assert again is handle
            assert len(created) == 1

            # get_handle 一致；未知 cluster KeyError
            assert registry.get_handle("default") is handle
            with pytest.raises(KeyError):
                registry.get_handle("nope")
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_spawn_manifest_ref_passthrough_to_core_unit(self, tmp_path: Path) -> None:
        ctx, _, registry, created = await _boot(tmp_path)

        def by_id(cluster_id: str) -> _FakeCoreUnit:
            return next(u for u in created if u.cluster_id == cluster_id)

        try:
            handle = await registry.ensure_cluster(
                "default", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            payload = SpawnAgentPayload(
                project_id=PROJECT_ID,
                cluster_id="default",
                config=AgentConfigPayload(name="coder-1", agent_id="agent-1", system_prompt=""),
                manifest_ref="ghrah.coder",
            )
            result = await handle.spawn_agent(payload)
            assert result["success"], result.get("error")

            # manifest_ref 原样直通 CoreUnit（解析由 CoreUnit 内部完成，
            # 物化器已下沉 Core，handle 不再展开）
            command, sent = by_id("default").commands[-1]
            assert command == "spawn_agent"
            assert sent["manifest_ref"] == "ghrah.coder"
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_list_and_terminate_shapes(self, tmp_path: Path) -> None:
        ctx, _, registry, _ = await _boot(tmp_path)
        try:
            handle = await registry.ensure_cluster(
                "default", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            agents = await handle.list_agents()
            assert agents == [{"name": "coder"}]
            result = await handle.terminate_agent("agent-1", "coder")
            assert result["success"]
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_shutdown_invalidates_handle_and_ensure_remounts(self, tmp_path: Path) -> None:
        ctx, _, registry, created = await _boot(tmp_path)

        def by_id(cluster_id: str) -> _FakeCoreUnit:
            return next(u for u in created if u.cluster_id == cluster_id)

        try:
            handle = await registry.ensure_cluster(
                "default", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            await registry.shutdown_cluster("default")
            assert not handle.is_connected
            assert by_id("default").stopped is True
            assert not registry.has_cluster("default")

            # 关闭后命令走失败回执（不抛）
            spawn_result = await handle.spawn_agent(
                SpawnAgentPayload(
                    project_id=PROJECT_ID,
                    cluster_id="default",
                    config=AgentConfigPayload(name="x", agent_id="agent-x", system_prompt=""),
                )
            )
            assert spawn_result["success"] is False
            assert await handle.list_agents() == []

            # 重挂：新实例
            new_handle = await registry.ensure_cluster(
                "default", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            assert new_handle is not handle
            assert new_handle.is_connected
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_cluster_cannot_be_reused_by_another_project(self, tmp_path: Path) -> None:
        ctx, _, registry, _ = await _boot(tmp_path)
        try:
            await registry.ensure_cluster(
                "shared", project_id="project-a", project_root_locator="file:///tmp/a"
            )
            with pytest.raises(ValueError, match="cluster_owner_conflict"):
                await registry.ensure_cluster(
                    "shared",
                    project_id="project-b",
                    project_root_locator="file:///tmp/b",
                )
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_unit_stop_disposes_all_clusters(self, tmp_path: Path) -> None:
        ctx, unit, registry, created = await _boot(tmp_path)
        try:
            await registry.ensure_cluster(
                "default", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            await registry.ensure_cluster(
                "secondary", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            assert len(created) == 2
        finally:
            await unit.stop()
            assert not registry.has_cluster("default")
            assert not registry.has_cluster("secondary")
            assert all(u.stopped for u in created)
            await ctx.__aexit__(None, None, None)

    async def test_concurrent_ensure_mounts_single_instance(self, tmp_path: Path) -> None:
        """回归：并发 ensure 同一 cluster 只挂载一个 CoreUnit 实例。"""
        import asyncio

        ctx, _, registry, created = await _boot(tmp_path)
        try:
            handles = await asyncio.gather(
                *[
                    registry.ensure_cluster(
                        "race", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
                    )
                    for _ in range(5)
                ]
            )
            assert len(created) == 1
            assert all(h is handles[0] for h in handles)
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_mount_failure_recycled_and_retry_succeeds(self, tmp_path: Path) -> None:
        """回归：挂载失败回收 fiber、返回稳定错误码，重试可成功。"""
        created: list[_FlakyStartCoreUnit] = []
        fail = {"on": True}

        def flaky_factory(
            cluster_id: str, project_id: str, project_root_locator: str
        ) -> _FlakyStartCoreUnit:
            unit = _FlakyStartCoreUnit(cluster_id, fail)
            created.append(unit)
            return unit

        ctx, _, registry, _ = await _boot(tmp_path, unit_factory=flaky_factory)
        try:
            with pytest.raises(ValueError, match="cluster_mount_failed"):
                await registry.ensure_cluster(
                    "flaky", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
                )
            # 半挂载不留在注册表
            assert not registry.has_cluster("flaky")

            handle = await registry.ensure_cluster(
                "flaky", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            assert handle.is_connected
            # 失败实例已被放弃，重试创建新实例并成功
            assert len(created) == 2
            assert created[0].start_attempts == 1
            assert created[1].start_attempts == 1
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)


class _ProjectManagerStub:
    def __init__(self, project: dict[str, Any]) -> None:
        self._project = project

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert command == "project_get"
        return {"success": True, "data": {"project": self._project}}


class TestProjectRuntimeStateGuards:
    """回归：非 ACTIVE Project 拒绝会复活 cluster 的命令；读/终止免挂载。"""

    @staticmethod
    def _project(status: str) -> dict[str, Any]:
        return {
            "project_id": PROJECT_ID,
            "version": 3,
            "status": status,
            "archived_at": None,
            "deleted_at": None,
            "cluster_ids": ["c1"],
            "project_root_locator": PROJECT_ROOT,
            "agents": [
                {
                    "agent_id": "agent-1",
                    "name": "coder",
                    "cluster_id": "c1",
                    "system_prompt": "",
                    "abilities": ["conversation"],
                }
            ],
        }

    async def _boot_guard(
        self, tmp_path: Path, project: dict[str, Any]
    ) -> tuple[Context, Any, CoreClusterRegistry, list[_FakeCoreUnit]]:
        ctx, unit, registry, created = await _boot(tmp_path)
        ctx.provide("project_manager", _ProjectManagerStub(project))
        return ctx, unit, registry, created

    async def test_stopped_project_rejects_runtime_reviving_commands(self, tmp_path: Path) -> None:
        ctx, unit, registry, created = await self._boot_guard(tmp_path, self._project("stopped"))
        try:
            cases = [
                ("spawn_agent", {"project_id": PROJECT_ID, "config": {"name": "x"}}),
                (
                    "send_message",
                    {
                        "project_id": PROJECT_ID,
                        "agent_id": "agent-1",
                        "target": "coder",
                        "content": "hi",
                    },
                ),
                ("broadcast_message", {"project_id": PROJECT_ID}),
                (
                    "delegate",
                    {
                        "project_id": PROJECT_ID,
                        "from_agent_id": "agent-1",
                        "to_agent_id": "agent-1",
                    },
                ),
                (
                    "session_create",
                    {"project_id": PROJECT_ID, "agent_id": "agent-1"},
                ),
            ]
            for command, payload in cases:
                result = await unit.handle_command(command, payload, CommandContext.internal())
                assert result["success"] is False, command
                assert result["error"] == "project_not_active", (command, result["error"])
            # cluster 全程未被惰性挂载
            assert created == []
            assert not registry.has_cluster("c1")
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_stopped_project_reads_and_terminate_without_mount(self, tmp_path: Path) -> None:
        ctx, unit, registry, created = await self._boot_guard(tmp_path, self._project("stopped"))
        try:
            listed = await unit.handle_command(
                "list_agents", {"project_id": PROJECT_ID}, CommandContext.internal()
            )
            assert listed["success"]
            assert listed["data"]["agents"][0]["runtime_state"] == "stopped"

            info = await unit.handle_command(
                "get_agent_info",
                {"project_id": PROJECT_ID, "agent_id": "agent-1", "name": "coder"},
                CommandContext.internal(),
            )
            assert info["success"]
            assert info["data"]["runtime_state"] == "stopped"
            assert info["data"]["agent_id"] == "agent-1"
            # 免挂载兜底无 runtime 用量，显式置 None（前端跳过覆盖）
            assert info["data"]["context_usage"] is None

            sessions = await unit.handle_command(
                "session_list",
                {"project_id": PROJECT_ID, "agent_id": "agent-1"},
                CommandContext.internal(),
            )
            assert sessions["success"]
            assert sessions["data"]["sessions"] == []

            terminated = await unit.handle_command(
                "terminate_agent",
                {"project_id": PROJECT_ID, "agent_id": "agent-1", "name": "coder"},
                CommandContext.internal(),
            )
            assert terminated["success"]

            # 读与终止均不挂载 cluster
            assert created == []
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)


class TestSpawnRetryAndDiagnostics:
    """并发 spawn 乐观锁重试 + 运行诊断持久化回归。"""

    @staticmethod
    def _active_project() -> dict[str, Any]:
        return {
            "project_id": PROJECT_ID,
            "version": 1,
            "status": "active",
            "archived_at": None,
            "deleted_at": None,
            "cluster_ids": ["c1"],
            "project_root_locator": PROJECT_ROOT,
            "agents": [],
        }

    async def test_spawn_retries_on_version_conflict(self, tmp_path: Path) -> None:
        """首个 add_agent 因陈旧 version 冲突 → 重取快照重试成功。"""
        ctx, unit, registry, created = await _boot(tmp_path)
        project = self._active_project()
        calls: list[dict[str, Any]] = []

        class RetryManagerStub:
            async def handle_command(self, command: str, payload: dict[str, Any]):
                assert command == "project_get" or command == "project_add_agent"
                if command == "project_get":
                    # 每次 get 都推进 version（模拟并发写入者）
                    project["version"] += 1
                    return {"success": True, "data": {"project": dict(project)}}
                calls.append(payload)
                if len(calls) == 1:
                    return {
                        "success": False,
                        "data": None,
                        "error": (
                            f"Project {PROJECT_ID} modified: expected version "
                            f"{payload['expected_version']}, got 99"
                        ),
                    }
                return {
                    "success": True,
                    "data": {
                        "agent_name": "coder",
                        "agent_id": "agent-9",
                        "runtime_pending": False,
                        "runtime_error": None,
                    },
                }

        ctx.provide("project_manager", RetryManagerStub())
        try:
            payload = {
                "project_id": PROJECT_ID,
                "config": {"name": "coder", "agent_id": "agent-9"},
            }
            result = await unit.handle_command("spawn_agent", payload, CommandContext.internal())
            assert result["success"], result.get("error")
            assert result["data"]["agent_id"] == "agent-9"
            assert len(calls) == 2  # 第一次冲突、重试成功
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_spawn_returns_stable_code_after_retry_exhausted(self, tmp_path: Path) -> None:
        ctx, unit, registry, created = await _boot(tmp_path)
        project = self._active_project()

        class AlwaysConflictStub:
            async def handle_command(self, command: str, payload: dict[str, Any]):
                if command == "project_get":
                    project["version"] += 1
                    return {"success": True, "data": {"project": dict(project)}}
                return {
                    "success": False,
                    "data": None,
                    "error": (
                        f"Project {PROJECT_ID} modified: expected version "
                        f"{payload['expected_version']}, got 99"
                    ),
                }

        ctx.provide("project_manager", AlwaysConflictStub())
        try:
            result = await unit.handle_command(
                "spawn_agent",
                {"project_id": PROJECT_ID, "config": {"name": "coder"}},
                CommandContext.internal(),
            )
            assert result["success"] is False
            assert result["error"] == "project_version_conflict"
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)


async def test_default_factory_rejects_empty_locator(tmp_path: Path) -> None:
    """生产工厂空 project_root_locator 直接拒绝（无全局库回退）。"""
    from ghrah.subject.core_cluster.registry import default_core_unit_factory

    factory = default_core_unit_factory(_config(tmp_path))
    with pytest.raises(ValueError, match="project_root_locator required"):
        factory("c1", "p1", "")


class TestAgentCompactContextRouting:
    """agent_compact_context 命令路由：ACTIVE 校验/agent 解析/cluster ensure/回执透传。"""

    async def test_route_included_in_unit_meta(self, tmp_path: Path) -> None:
        """路由集合注册：未知命令拒绝守卫放行 agent_compact_context。"""
        unit = CoreClusterRegistryUnit(_config(tmp_path))
        assert "agent_compact_context" in unit.meta.routes.commands

    async def test_stopped_project_rejects_compact_without_mount(self, tmp_path: Path) -> None:
        """非 ACTIVE Project 拒绝且不惰性挂载（与 send_message 同语义）。"""
        ctx, unit, registry, created = await self._boot_with_project(
            tmp_path, project=self._project("stopped")
        )
        try:
            result = await unit.handle_command(
                "agent_compact_context",
                {"project_id": PROJECT_ID, "agent_id": "agent-1", "agent_name": "coder"},
                CommandContext.internal(),
            )
            assert result["success"] is False
            assert result["error"] == "project_not_active"
            assert created == []
            assert not registry.has_cluster("c1")
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_active_project_routes_to_core_with_identity(self, tmp_path: Path) -> None:
        """ACTIVE Project：agent 解析 → cluster ensure → 命令透传 CoreUnit，
        成功回执附带 identity 四件套（回执透传契约）。"""
        ctx, unit, registry, created = await self._boot_with_project(
            tmp_path, project=self._project("active")
        )
        try:
            result = await unit.handle_command(
                "agent_compact_context",
                {"project_id": PROJECT_ID, "agent_id": "agent-1", "agent_name": "coder"},
                CommandContext.internal(),
            )
            assert result["success"], result.get("error")
            data = result["data"]
            assert data["project_id"] == PROJECT_ID
            assert data["agent_id"] == "agent-1"
            assert data["agent_name"] == "coder"
            assert data["cluster_id"] == "c1"

            # cluster 按需挂载一次，命令名与载荷字段口径透传
            assert registry.has_cluster("c1")
            command, sent = created[0].commands[0]
            assert command == "agent_compact_context"
            assert sent["agent_name"] == "coder"
            assert sent["agent_id"] == "agent-1"
            assert sent["project_id"] == PROJECT_ID
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_unknown_agent_rejected(self, tmp_path: Path) -> None:
        """agent 不在 Project agents 中 → agent 解析错误。"""
        ctx, unit, registry, created = await self._boot_with_project(
            tmp_path, project=self._project("active")
        )
        try:
            result = await unit.handle_command(
                "agent_compact_context",
                {"project_id": PROJECT_ID, "agent_id": "agent-x", "agent_name": "ghost"},
                CommandContext.internal(),
            )
            assert result["success"] is False
            assert "agent not found" in str(result["error"])
            assert created == []
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_identity_mismatch_rejected(self, tmp_path: Path) -> None:
        """agent_id 对但 agent_name 不匹配 → 拒绝（agent_identity_mismatch）。"""
        ctx, unit, registry, created = await self._boot_with_project(
            tmp_path, project=self._project("active")
        )
        try:
            result = await unit.handle_command(
                "agent_compact_context",
                {"project_id": PROJECT_ID, "agent_id": "agent-1", "agent_name": "ghost"},
                CommandContext.internal(),
            )
            assert result["success"] is False
            assert result["error"] == "agent_identity_mismatch"
            assert created == []
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_missing_agent_id_rejected(self, tmp_path: Path) -> None:
        """无 agent_id → 解析拒绝。"""
        ctx, unit, registry, created = await self._boot_with_project(
            tmp_path, project=self._project("active")
        )
        try:
            result = await unit.handle_command(
                "agent_compact_context",
                {"project_id": PROJECT_ID, "agent_name": "coder"},
                CommandContext.internal(),
            )
            assert result["success"] is False
            assert result["error"] == "agent_id required"
            assert created == []
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    @staticmethod
    def _project(status: str) -> dict[str, Any]:
        return {
            "project_id": PROJECT_ID,
            "version": 3,
            "status": status,
            "archived_at": None,
            "deleted_at": None,
            "cluster_ids": ["c1"],
            "project_root_locator": PROJECT_ROOT,
            "agents": [
                {
                    "agent_id": "agent-1",
                    "name": "coder",
                    "cluster_id": "c1",
                    "system_prompt": "",
                    "abilities": ["conversation"],
                }
            ],
        }

    async def _boot_with_project(
        self, tmp_path: Path, *, project: dict[str, Any]
    ) -> tuple[Context, Any, CoreClusterRegistry, list[_FakeCoreUnit]]:
        ctx, unit, registry, created = await _boot(tmp_path)
        ctx.provide("project_manager", _ProjectManagerStub(project))
        return ctx, unit, registry, created


class TestAgentResetRouting:
    """agent_reset 命令路由（转发面）。"""

    async def test_route_included_in_unit_meta(self, tmp_path: Path) -> None:
        """路由集合注册：未知命令拒绝守卫放行 agent_reset。"""
        unit = CoreClusterRegistryUnit(_config(tmp_path))
        assert "agent_reset" in unit.meta.routes.commands

    async def test_stopped_project_rejects_reset_without_mount(self, tmp_path: Path) -> None:
        """非 ACTIVE Project 拒绝且不惰性挂载（与 send_message 同语义）。"""
        ctx, unit, registry, created = await self._boot_with_project(
            tmp_path, project=self._project("stopped")
        )
        try:
            result = await unit.handle_command(
                "agent_reset",
                {"project_id": PROJECT_ID, "agent_id": "agent-1"},
                CommandContext.internal(),
            )
            assert result["success"] is False
            assert result["error"] == "project_not_active"
            assert created == []
            assert not registry.has_cluster("c1")
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_active_project_routes_to_core_without_name_injection(
        self, tmp_path: Path
    ) -> None:
        """ACTIVE Project：agent 解析 → cluster ensure → 命令透传 CoreUnit。

        agent_reset 以 project_id + agent_id 稳定寻址：透传载荷不注入
        可重名 name 字段；成功回执附带 identity 四件套。
        """
        ctx, unit, registry, created = await self._boot_with_project(
            tmp_path, project=self._project("active")
        )
        try:
            result = await unit.handle_command(
                "agent_reset",
                {"project_id": PROJECT_ID, "agent_id": "agent-1"},
                CommandContext.internal(),
            )
            assert result["success"], result.get("error")
            data = result["data"]
            assert data["project_id"] == PROJECT_ID
            assert data["agent_id"] == "agent-1"
            assert data["agent_name"] == "coder"
            assert data["cluster_id"] == "c1"

            assert registry.has_cluster("c1")
            command, sent = created[0].commands[0]
            assert command == "agent_reset"
            assert sent["project_id"] == PROJECT_ID
            assert sent["agent_id"] == "agent-1"
            # 不注入 name 类字段（AgentResetPayload 无 name）
            assert "agent_name" not in sent
            assert "name" not in sent
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_unknown_agent_rejected(self, tmp_path: Path) -> None:
        """agent 不在 Project agents 中 → agent 解析错误。"""
        ctx, unit, registry, created = await self._boot_with_project(
            tmp_path, project=self._project("active")
        )
        try:
            result = await unit.handle_command(
                "agent_reset",
                {"project_id": PROJECT_ID, "agent_id": "agent-x"},
                CommandContext.internal(),
            )
            assert result["success"] is False
            assert "agent not found" in str(result["error"])
            assert created == []
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    async def test_missing_agent_id_rejected(self, tmp_path: Path) -> None:
        """无 agent_id → 解析拒绝。"""
        ctx, unit, registry, created = await self._boot_with_project(
            tmp_path, project=self._project("active")
        )
        try:
            result = await unit.handle_command(
                "agent_reset",
                {"project_id": PROJECT_ID},
                CommandContext.internal(),
            )
            assert result["success"] is False
            assert result["error"] == "agent_id required"
            assert created == []
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)

    @staticmethod
    def _project(status: str) -> dict[str, Any]:
        return {
            "project_id": PROJECT_ID,
            "version": 3,
            "status": status,
            "archived_at": None,
            "deleted_at": None,
            "cluster_ids": ["c1"],
            "project_root_locator": PROJECT_ROOT,
            "agents": [
                {
                    "agent_id": "agent-1",
                    "name": "coder",
                    "cluster_id": "c1",
                    "system_prompt": "",
                    "abilities": ["conversation"],
                }
            ],
        }

    async def _boot_with_project(
        self, tmp_path: Path, *, project: dict[str, Any]
    ) -> tuple[Context, Any, CoreClusterRegistry, list[_FakeCoreUnit]]:
        ctx, unit, registry, created = await _boot(tmp_path)
        ctx.provide("project_manager", _ProjectManagerStub(project))
        return ctx, unit, registry, created


class TestRealCoreUnitMultiCluster:
    """回归：多集群 = 多真实 CoreUnit 实例共存。

    历史盲区：本文件其他用例全部走 _FakeCoreUnit 工厂，从不触达真实
    CoreUnit.init——provide("supervisor") 时代第二个实例挂载即
    ServiceConflictError（Ouroboros 同名服务全局唯一）。provide 删除后
    每实例状态自持，多实例天然隔离。
    """

    async def test_two_real_core_units_coexist_and_isolate(self, tmp_path: Path) -> None:
        from ghrah.core.unit import CoreUnitConfig, create_core_unit

        def factory(cluster_id: str, project_id: str, project_root_locator: str) -> Any:
            return create_core_unit(
                CoreUnitConfig(
                    cluster_id=cluster_id,
                    project_id=project_id,
                    # fail-closed：无 abilities spawn 须显式声明默认集
                    default_abilities=("conversation", "end_task"),
                )
            )

        config = _config(tmp_path)
        unit = CoreClusterRegistryUnit(config, unit_factory=factory)
        workspace = _FakeWorkspaceService(str(tmp_path / "workspace"))

        ctx = await Context().__aenter__()
        try:
            ctx.provide(MANIFEST_STORE.name, _manifest_store(tmp_path))
            ctx.provide(WORKSPACE_SERVICE.name, workspace)
            ctx.provide(SANDBOX_EXECUTOR.name, SandboxExecutor(str(tmp_path / "workspace")))

            fiber = ctx.plugin(mount_unit(unit))
            await wait_active(fiber)
            registry = ctx.get(CORE_CLUSTER_REGISTRY.name)

            # 两个真实 CoreUnit 实例挂载不冲突（provide 删除前必炸）
            handle_a = await registry.ensure_cluster(
                "alpha", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            handle_b = await registry.ensure_cluster(
                "beta", project_id=PROJECT_ID, project_root_locator=PROJECT_ROOT
            )
            assert handle_a.is_connected and handle_b.is_connected

            spawn_a = await handle_a.spawn_agent(
                SpawnAgentPayload(
                    project_id=PROJECT_ID,
                    cluster_id="alpha",
                    config=AgentConfigPayload(
                        name="agent-a", agent_id="agent-a-id", system_prompt="x"
                    ),
                )
            )
            assert spawn_a["success"], spawn_a.get("error")
            spawn_b = await handle_b.spawn_agent(
                SpawnAgentPayload(
                    project_id=PROJECT_ID,
                    cluster_id="beta",
                    config=AgentConfigPayload(
                        name="agent-b", agent_id="agent-b-id", system_prompt="x"
                    ),
                )
            )
            assert spawn_b["success"], spawn_b.get("error")

            # agent 列表按实例隔离
            assert [a["name"] for a in await handle_a.list_agents()] == ["agent-a"]
            assert [a["name"] for a in await handle_b.list_agents()] == ["agent-b"]
        finally:
            await registry.stop()
            await ctx.__aexit__(None, None, None)
