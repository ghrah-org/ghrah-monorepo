# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from ghrah.context import ContextManager
from ghrah.protocol.views.core import DeliveryAcceptanceView
from ouroboros import Context

from ghrah.subject.config import RecoveryConfig, SubjectConfig
from ghrah.subject.project.models import AgentRecord, make_project_record
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.runtime.service_keys import CORE_PORT_SERVICE, PROJECT_STORE
from ghrah.subject.units import mount_builtin_units


async def test_room_send_projects_real_core_delivery_receipts(tmp_path: Path) -> None:
    config = SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        recovery_slice=RecoveryConfig(enabled=False, reconcile_on_start=False),
    )
    project = make_project_record(name="room test", project_root_locator=str(tmp_path / "project"))
    worker = AgentRecord(agent_id="worker", project_id=project.project_id, name="Worker")
    project = project.model_copy(update={"agents": [worker]})
    paths = ProjectPaths.from_locator(project.project_root_locator)
    paths.initialize(project.project_id)

    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        await ctx.get(PROJECT_STORE.name).upsert(project)
        created = await bridge_command(
            ctx, "room_create", {"project_id": project.project_id, "name": "test"}
        )
        assert created["success"], created
        room_id = created["data"]["room"]["room_id"]
        joined = await bridge_command(
            ctx,
            "room_join",
            {"room_id": room_id, "subject": worker.agent_id, "subject_type": "agent"},
        )
        assert joined["success"], joined

        ports = ctx.get(CORE_PORT_SERVICE.name)
        ports.remember_locator(project.project_id, project.project_root_locator)
        registry = ports.for_project(project.project_id)._registry
        await registry.ensure()
        messages = registry.message_manager()
        async with messages.context_backend(None) as backend:
            chain = ContextManager(worker.agent_id, persistence=backend)
            await chain.persist()
            turn_id = await chain.open_turn()
        await messages.refresh_agent_ids()

        payload = {"room_id": room_id, "author": "reader", "author_type": "human"}
        queued = await bridge_command(
            ctx, "room_send", {**payload, "data": {"message": "queued", "request_id": "queue"}}
        )
        assert queued["success"], queued
        assert queued["data"]["entry"]["data"]["message"] == "queued"
        (delivery,) = map(DeliveryAcceptanceView.model_validate, queued["data"]["deliveries"])
        assert delivery.policy == "queue"
        assert delivery.turn_id is None
        assert not delivery.policy_applied
        assert delivery.queue_position == 1

        inject = {
            **payload,
            "delivery_policy": "inject",
            "data": {"message": "injected", "request_id": "inject"},
        }
        accepted = await bridge_command(ctx, "room_send", inject)
        assert accepted["success"], accepted
        (delivery,) = map(DeliveryAcceptanceView.model_validate, accepted["data"]["deliveries"])
        assert delivery.policy == "inject"
        assert delivery.policy_applied
        assert delivery.turn_id == turn_id
        assert delivery.queue_position == 0

        replay = await bridge_command(ctx, "room_send", inject)
        assert replay == accepted
        assert (await bridge_command(ctx, "room_send", {**inject, "delivery_policy": "queue"}))[
            "error"
        ] == "idempotency_conflict"
        assert (
            await bridge_command(
                ctx, "room_send", {**inject, "author": worker.agent_id, "author_type": "agent"}
            )
        )["error"] == "delivery_policy_not_allowed"
