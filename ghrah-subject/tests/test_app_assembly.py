# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""装配层测试：assemble_subject（coexistence/full）+ observer app 取回 +
reconcile 触发/禁用分支。

full 用例走**真实** CoreUnit 默认工厂（bootstrap → ensure_cluster("default")
→ 进程内挂载 CoreUnit → 空集群 list_agents）——不 mock Core，验证合装配
端到端成立（无 LLM 调用：不 spawn agent）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ability_testutil import put_agent_template
from ghrah.ability.access import GrantPolicy
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import ProjectConfig, RecoveryConfig, SubjectConfig
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.runtime.service_keys import RECONCILIATION_SERVICE
from ghrah.subject.server.app import create_app


def _config(
    tmp_path: Path,
    *,
    recovery_enabled: bool = True,
    reconcile_on_start: bool = True,
) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        ability_grant_policy=GrantPolicy(mode="merge"),
        recovery_slice=RecoveryConfig(
            enabled=recovery_enabled,
            reconcile_on_start=reconcile_on_start,
            bootstrap_default_project=True,
        ),
        project_slice=ProjectConfig(
            bootstrap_workspace_locator=str(tmp_path / "ws/projects/default"),
            default_root_locator_template=str(tmp_path / "projects/{project_id}"),
        ),
    )


@asynccontextmanager
async def _assemble(
    tmp_path: Path,
    *,
    profile: str = "full",
    recovery_enabled: bool = True,
    reconcile_on_start: bool = True,
) -> AsyncIterator[Context]:
    config = _config(
        tmp_path,
        recovery_enabled=recovery_enabled,
        reconcile_on_start=reconcile_on_start,
    )
    async with Context() as ctx:
        await assemble_subject(ctx, config, profile=profile)
        yield ctx


class TestAssembleSubject:
    async def test_coexistence_all_fibers_active(self, tmp_path: Path) -> None:
        async with _assemble(tmp_path, profile="coexistence") as ctx:
            # coexistence 不含 recovery/project/observer/registry
            assert ctx.get(RECONCILIATION_SERVICE.name, strict=False) is None

    async def test_full_all_fibers_active_and_observer_app_retrievable(
        self, tmp_path: Path
    ) -> None:
        async with _assemble(tmp_path, profile="full") as ctx:
            app = create_app(ctx=ctx)
            assert any(getattr(route, "path", None) == "/ws" for route in app.routes)
            health = next(r for r in app.routes if getattr(r, "path", None) == "/health")
            assert health is not None

    async def test_full_reconcile_bootstrap_triggers_real_core_mount(self, tmp_path: Path) -> None:
        """reconcile bootstrap → ensure_cluster → 真实 CoreUnit 进程内挂载。"""
        async with _assemble(tmp_path, profile="full") as ctx:
            svc = ctx.get(RECONCILIATION_SERVICE.name)
            report = await svc.reconcile_status()
            assert report is not None
            assert report.bootstrap is True
            assert report.success

            # registry 懒挂载了真实 CoreUnit（supervisor 实例自持，不经 ctx 服务）
            registry = ctx.get("core_cluster_registry")
            assert registry.has_cluster("default")
            handle = registry.get_handle("default")
            assert handle.is_connected
            assert handle._unit.supervisor is not None

            # 聚合裁决 D-C：spawn 的 agent 链落 Core sqlite（core_db_path），
            # 与 ledger 读侧投影同源（persistence_factory 注入验证）
            project_result = await ctx.get("project_manager").handle_command("project_list", {})
            project = project_result["data"]["projects"][0]
            spawn = await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": project["project_id"],
                    "cluster_id": "default",
                    "config": {"name": "ledger-probe"},
                    "manifest_ref": put_agent_template(ctx.get("manifest_store")),
                },
            )
            assert spawn["success"], spawn.get("error")
            supervisor = handle._unit.supervisor
            actor = supervisor._registry.get_info("ledger-probe").actor_handle
            assert actor._context_manager.persistence is not None
            action_chain_db_path = ProjectPaths.from_locator(
                project["project_root_locator"]
            ).action_chain_db_path
            assert str(actor._context_manager.persistence.db_path) == str(action_chain_db_path)

            # 读侧经 Core 端口连通（不再直连 sqlite）。P2a 新契约：spawn 即
            # connect + 首次 persist——根节点（system prompt 快照）与 agents
            # 行当场落库（非旧「首次 save_node 才登记」语义）
            from ghrah.core.wiring import build_core_port

            port = build_core_port(
                resolve_db_path=lambda project_id: action_chain_db_path,
            ).project(project["project_id"])
            try:
                catalog = await port.sessions.catalog(agent_id=spawn["data"]["agent_id"])
                session_id = catalog.active_session_id
                branch_id = next(
                    item.branch_id for item in catalog.sessions if item.session_id == session_id
                )
                history = await port.sessions.history(
                    agent_id=spawn["data"]["agent_id"],
                    session_id=session_id,
                    branch_id=branch_id,
                )
                assert len(history.nodes) == 1
                assert history.nodes[0].parent_id is None
            finally:
                await port.aclose()

    async def test_reconcile_disabled_leaves_no_report(self, tmp_path: Path) -> None:
        async with _assemble(
            tmp_path,
            profile="full",
            recovery_enabled=False,
            reconcile_on_start=False,
        ) as ctx:
            svc = ctx.get(RECONCILIATION_SERVICE.name)
            assert await svc.reconcile_status() is None
            registry = ctx.get("core_cluster_registry")
            assert not registry.has_cluster("default")

    async def test_third_party_allowlist_empty_mounts_nothing(self, tmp_path: Path) -> None:
        from ghrah.subject.runtime.third_party import mount_third_party_units

        config = _config(tmp_path)
        config.enabled_third_party_units = []
        async with Context() as ctx:
            fibers = await mount_third_party_units(ctx, config)
            assert fibers == {}


class TestRequestInjection:
    async def test_adapter_injects_request_and_session_into_payload_copy(
        self, tmp_path: Path
    ) -> None:
        """router 适配器把 request_id/session_id 合并进 payload 副本（不改原 dict）。"""
        from ghrah.protocol.types import CommandType, Message

        from ghrah.subject.server.connection_manager import ConnectionManager
        from ghrah.subject.server.event_bus import EventBus
        from ghrah.subject.server.router import ObserverRouter
        from ghrah.subject.units.websocket_observer_endpoint import (
            _EngineDispatchAdapter,
        )

        seen: list[tuple[str, dict[str, Any]]] = []

        async def task_create_handler(payload: dict[str, Any]) -> dict[str, Any]:
            seen.append(("task_create", dict(payload)))
            return {"success": True, "data": None}

        original = {"title": "t"}
        async with Context() as ctx:
            ctx.on(f"command/{CommandType.LIST_AGENTS.value}", task_create_handler)
            adapter = _EngineDispatchAdapter(ctx)
            router = ObserverRouter(
                ConnectionManager(),
                EventBus(ConnectionManager()),
                engine=adapter,  # type: ignore[arg-type]
            )

            result = await router.handle_command(
                Message(
                    type=CommandType.LIST_AGENTS.value,
                    payload=original,
                    request_id="req-9",
                ),
                session_id="session-9",
            )
            assert result is not None
            assert result.payload.success is True
            assert seen[-1][1]["request_id"] == "req-9"
            assert seen[-1][1]["session_id"] == "session-9"
            # 原 dict 未被修改
            assert original == {"title": "t"}
