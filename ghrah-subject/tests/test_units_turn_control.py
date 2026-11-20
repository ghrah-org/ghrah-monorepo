# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""通过实际 full profile 和 CorePort 验证 Observer 只读路由。"""

from pathlib import Path

import httpx2
from ghrah.context import ContextManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ouroboros import Context

from ghrah.subject.config import RecoveryConfig, SubjectConfig
from ghrah.subject.project.models import AgentRecord, make_project_record
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    OBSERVER_ENDPOINT,
    PROJECT_STORE,
)
from ghrah.subject.units import mount_builtin_units


def _config(tmp_path: Path, *, enabled: bool = True) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        observer_http_api_enabled=enabled,
        observer_http_api_token="test-token",
        recovery_slice=RecoveryConfig(enabled=False, reconcile_on_start=False),
    )


async def test_reads_committed_history_without_starting_core(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    project = make_project_record(name="sample", project_root_locator=str(tmp_path / "project"))
    agent = AgentRecord(agent_id="agent-a", project_id=project.project_id, name="display name")
    other = AgentRecord(agent_id="agent-b", project_id=project.project_id, name="other")
    project = project.model_copy(update={"agents": [agent, other]})
    paths = ProjectPaths.from_locator(project.project_root_locator)
    paths.initialize(project.project_id)
    prefix = f"/api/v1/projects/{project.project_id}/agents/{agent.agent_id}"

    async with SqliteBackend(paths.action_chain_db_path) as backend:
        chain = ContextManager(agent.agent_id, persistence=backend)
        await chain.persist()
        session_id = chain.active_session_id
        branch_id = chain.active_head.created_on_branch_id
        for _ in range(3):
            chain.begin_iteration()
            chain.commit_iteration()
        await chain.persist()
        turn_id = await chain.open_turn()
        opened = await chain.open_hitl_request(
            promise_id="approval",
            ability_name="test",
            tool_call_id="call",
            tool_call={},
            reason="review",
            options={},
            deadline_at=None,
        )
        assert opened.persisted

    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        store = ctx.get(PROJECT_STORE.name)
        await store.upsert(project)
        app = ctx.get(OBSERVER_ENDPOINT.name).app
        transport = httpx2.ASGITransport(app=app, client=("192.0.2.10", 50000))
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get(f"{prefix}/sessions")
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "access_denied"
            client.headers["Authorization"] = "Bearer test-token"

            catalog = await client.get(f"{prefix}/sessions")
            assert catalog.status_code == 200
            assert catalog.json()["active_session_id"] == session_id

            branches_url = f"{prefix}/sessions/{session_id}/branches"
            branches = await client.get(branches_url)
            assert branches.status_code == 200
            assert [branch["branch_id"] for branch in branches.json()["branches"]] == [branch_id]

            async def reject_full_checkpoint(*args, **kwargs):
                raise AssertionError("bounded history must not restore the full Agent checkpoint")

            with monkeypatch.context() as patch:
                patch.setattr(SqliteBackend, "load_checkpoint", reject_full_checkpoint)
                nodes = await client.get(f"{branches_url}/{branch_id}/nodes", params={"limit": 2})
            assert nodes.status_code == 200
            assert [node["id"] for node in nodes.json()["nodes"]] == [
                chain.active_head.parent_id,
                chain.active_head.id,
            ]
            assert (await client.get(f"{branches_url}/unknown/nodes")).status_code == 404

            turns = await client.get(
                f"{prefix}/turns", params={"session_id": session_id, "status": "waiting_hitl"}
            )
            assert turns.status_code == 200
            assert [item["turn_id"] for item in turns.json()["items"]] == [turn_id]
            detail = await client.get(f"{prefix}/turns/{turn_id}")
            assert detail.status_code == 200
            assert detail.json()["nodes"] == []
            card = detail.json()["turn"]
            assert card["turn_index"] == 1
            assert "injections" not in card
            assert card["stats"]["steps"] == card["stats"]["node_count"] == 0
            assert card["stats"]["cache_hit_rate"] is None
            other_prefix = prefix.replace(f"/{agent.agent_id}", f"/{other.agent_id}")
            assert (await client.get(f"{other_prefix}/turns/{turn_id}")).status_code == 404
            pending = await client.get(f"{prefix}/hitl/pending", params={"session_id": session_id})
            assert pending.status_code == 200
            assert pending.json()["items"][0]["turn_id"] == turn_id

            queue = await client.get(f"{prefix}/deliveries/queue")
            assert queue.status_code == 200
            assert queue.json() == {
                "agent_id": agent.agent_id,
                "session_id": None,
                "entries": [],
                "open_turn_id": None,
            }
            assert (await client.get(f"{prefix}/turns?limit=201")).status_code == 400
            foreign_agent = prefix.replace(f"/{agent.agent_id}", "/agent-c")
            assert (await client.get(f"{foreign_agent}/turns")).status_code == 404
            missing_project = prefix.replace(f"/{project.project_id}", "/missing")
            assert (await client.get(f"{missing_project}/turns")).status_code == 404

            await store.archive(project.project_id, expected_version=project.version)
            archived = await client.get(f"{prefix}/sessions")
            assert archived.status_code == 409
            assert archived.json()["error"]["code"] == "project_archived"

        assert not ctx.get(CORE_CLUSTER_REGISTRY.name).has_cluster(project.project_id)
        assert not Path(f"{paths.action_chain_db_path}.owner").exists()


async def test_http_gate_precedes_project_lookup(tmp_path: Path) -> None:
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path, enabled=False), profile="full")
        app = ctx.get(OBSERVER_ENDPOINT.name).app
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            result = await client.get("/api/v1/projects/missing/agents/missing/turns")
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "capability_unavailable"
