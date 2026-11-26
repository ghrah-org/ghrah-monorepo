# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.core import AgentRuntimeView, TurnAbortView, TurnRollbackView
from ouroboros import Context
from starlette.websockets import WebSocket

from ghrah.subject.config import RecoveryConfig, SubjectConfig
from ghrah.subject.project.models import AgentRecord, make_project_record
from ghrah.subject.runtime.port_transport import PortClusterHandle
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    CORE_PORT_SERVICE,
    OBSERVER_ENDPOINT,
    PROJECT_STORE,
)
from ghrah.subject.units import mount_builtin_units


async def test_observer_turn_commands_cross_the_production_dispatcher(tmp_path: Path, monkeypatch):
    config = SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        recovery_slice=RecoveryConfig(enabled=False, reconcile_on_start=False),
    )
    project = make_project_record(name="control", project_root_locator=str(tmp_path / "project"))
    agent = AgentRecord(agent_id="worker", project_id=project.project_id, name="Worker")
    project = project.model_copy(update={"agents": [agent]})
    scope = {"project_id": project.project_id, "agent_id": agent.agent_id}
    captured = []
    dispatch = PortClusterHandle.dispatch

    async def capture(handle, command, payload):
        captured.append((command, payload))
        return await dispatch(handle, command, payload)

    monkeypatch.setattr(PortClusterHandle, "dispatch", capture)

    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        await ctx.get(PROJECT_STORE.name).upsert(project)
        cluster = ctx.get(CORE_CLUSTER_REGISTRY.name)
        ports = ctx.get(CORE_PORT_SERVICE.name)
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()
        websocket = WebSocket(
            {"type": "websocket", "query_string": b"", "headers": []},
            incoming.get,
            outgoing.put,
        )
        await incoming.put({"type": "websocket.connect"})
        server = asyncio.create_task(endpoint._server.handle_connection(websocket))

        async def recv():
            frame = await asyncio.wait_for(outgoing.get(), timeout=3)
            assert frame["type"] == "websocket.send", frame
            return json.loads(frame["text"])

        async def command(command_type, fields):
            await incoming.put(
                {
                    "type": "websocket.receive",
                    "text": json.dumps(
                        {
                            "type": command_type,
                            "request_id": "request",
                            "payload": {**scope, **fields},
                        }
                    ),
                }
            )
            wire = await recv()
            assert wire["type"] == "command_result", wire
            assert wire["request_id"] == wire["payload"]["request_id"] == "request"
            return wire["payload"]

        try:
            assert (await asyncio.wait_for(outgoing.get(), timeout=3))["type"] == "websocket.accept"
            assert (await recv())["request_id"] == "connect"

            missing = await command("turn_abort", {})
            assert not missing["success"] and missing["error"] == "agent_offline"
            assert not cluster.has_cluster(project.project_id)
            assert not ports.has_project(project.project_id)
            invalid = await command("turn_rollback", {"turn_id": "turn", "dry_run": False})
            assert not invalid["success"] and invalid["error"] == "invalid_request"
            invalid = await command("turn_rollback", {"turn_id": "turn", "target": "node"})
            assert not invalid["success"] and invalid["error"] == "invalid_rollback_target"
            assert captured == []

            await cluster.ensure_cluster(
                project.project_id,
                project_id=project.project_id,
                project_root_locator=project.project_root_locator,
            )
            agent_runtime = AsyncMock(
                return_value=AgentRuntimeView(agent_id=agent.agent_id, availability="offline")
            )
            rollback = AsyncMock()
            abort = AsyncMock(
                return_value=TurnAbortView(turn_id="turn", status="closed", already_terminal=True)
            )
            fake = SimpleNamespace(
                agents=SimpleNamespace(runtime=agent_runtime),
                turns=SimpleNamespace(abort=abort, rollback=rollback),
                aclose=AsyncMock(),
            )
            monkeypatch.setitem(ports._ports, project.project_id, fake)
            assert (await command("turn_abort", {}))["error"] == "agent_offline"
            abort.assert_not_awaited()

            agent_runtime.return_value = AgentRuntimeView(
                agent_id=agent.agent_id, availability="online"
            )
            closed = await command("turn_abort", {})
            assert (
                closed["success"] and TurnAbortView.model_validate(closed["data"]).already_terminal
            )
            abort.assert_awaited_once_with(
                agent_id=agent.agent_id, turn_id=None, session_id=None, reason=""
            )
            assert captured[-1][1]["agent_name"] == agent.name

            rollback.side_effect = ApiError(
                Problem.make(
                    ProblemCode.FAILED_PRECONDITION,
                    capability="turns.rollback",
                    detail="rollback_target_not_usable",
                    details={"suggested_node_ids": ["node"]},
                )
            )
            failed = await command("turn_rollback", {"turn_id": "turn"})
            assert not failed["success"] and failed["error"] == "rollback_target_not_usable"
            assert failed["details"] == {"suggested_node_ids": ["node"]}
            assert captured[-1][1]["dry_run"] is False
            rollback.assert_awaited_once_with(
                agent_id=agent.agent_id, turn_id="turn", target="base", node_id=None
            )

            rollback.side_effect = None
            rollback.return_value = TurnRollbackView(
                turn_id="turn",
                session_id="session",
                status="rolled_back",
                closing_kind="completed",
                target="base",
                target_node_id="node",
                branch_id="branch",
                already_applied=True,
                rolled_back_at="2026-11-25T00:00:00Z",
            )
            result = await command("turn_rollback", {"turn_id": "turn"})
            assert (
                result["success"]
                and TurnRollbackView.model_validate(result["data"]).already_applied
            )

            async def stop_after_probe(**kwargs):
                await cluster.shutdown_cluster(project.project_id)
                return AgentRuntimeView(agent_id=agent.agent_id, availability="online")

            agent_runtime.side_effect = stop_after_probe
            assert (await command("turn_abort", {}))["error"] == "agent_offline"
            assert not cluster.has_cluster(project.project_id)
            abort.assert_awaited_once()
        finally:
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(server, timeout=3)
