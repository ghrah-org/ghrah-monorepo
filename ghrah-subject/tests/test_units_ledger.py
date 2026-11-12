# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""LedgerUnit v2 稳定 Session/Branch 路由测试。"""

from pathlib import Path

from ghrah.context.persistence import ContextChanges
from ghrah.context.persistence.sqlite_backend import SqliteBackend  # type: ignore[import-untyped]
from ghrah.context.session_runtime import SessionRuntime
from ghrah.core.wiring import build_core_port
from ghrah.protocol.ports.keys import CORE_PORT
from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.runtime.ouroboros_bridge import bridge_command, mount_unit
from ghrah.subject.runtime.service_keys import PROJECT_MANAGER
from ghrah.subject.units.ledger import LedgerUnit


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
    )


async def _seed(path: Path, agent_id: str, marker: str) -> SessionRuntime:
    runtime = SessionRuntime.create(agent_name=agent_id)
    runtime.commit_node(ability_names=[marker], agent_state={"marker": marker})
    backend = SqliteBackend(db_path=path)
    await backend.connect()
    await backend.apply_changes(
        ContextChanges(
            agent_name=agent_id,
            active_session_id=runtime.session.session_id,
            sessions=(runtime.session,),
            branches=tuple(runtime.branches.values()),
            nodes=tuple(runtime.chain.nodes),
        )
    )
    await backend.close()
    return runtime


class FakeProjectManager:
    def __init__(self, projects: dict[str, dict[str, object]]) -> None:
        self.projects = projects

    async def handle_command(self, command: str, payload: dict[str, object]):
        assert command == "project_get"
        project_id = str(payload["project_id"])
        project = self.projects.get(project_id)
        return {"success": project is not None, "data": {"project": project} if project else {}}


def _provide_core(ctx, resolve_db_path) -> None:
    """注入真实 Core 端口（测试允许 import Core，src 不允许）。"""
    ctx.provide(CORE_PORT, build_core_port(resolve_db_path=resolve_db_path))


async def test_ledger_unit_reads_explicit_session_branch(tmp_path: Path) -> None:
    project_id = "project-a"
    agent_id = "agent-a-id"
    paths = ProjectPaths.from_locator((tmp_path / "project-a-root").as_uri())
    paths.action_chain_db_path.parent.mkdir(parents=True, exist_ok=True)
    runtime = await _seed(paths.action_chain_db_path, agent_id, "tool-a")
    projects = {
        project_id: {
            "project_id": project_id,
            "project_root_locator": paths.root.as_uri(),
            "agents": [{"name": "agent-a", "agent_id": agent_id, "cluster_id": "default"}],
        }
    }
    ledger = LedgerUnit(_config(tmp_path))
    async with Context() as ctx:
        ctx.provide(PROJECT_MANAGER.name, FakeProjectManager(projects))
        _provide_core(ctx, lambda project_id: paths.action_chain_db_path)
        fiber = ctx.plugin(mount_unit(ledger))
        await wait_active(fiber)
        payload = {
            "project_id": project_id,
            "agent_id": agent_id,
            "agent_name": "agent-a",
            "session_id": runtime.session.session_id,
            "branch_id": runtime.active_branch.branch_id,
        }
        result = await bridge_command(ctx, "get_chain_history", payload)
        assert result["success"] is True
        assert result["data"]["session_id"] == runtime.session.session_id
        assert result["data"]["branch_id"] == runtime.active_branch.branch_id
        assert [node["id"] for node in result["data"]["nodes"]] == [
            node.id for node in runtime.get_history()
        ]
        missing = await bridge_command(ctx, "get_chain_history", {})
        assert missing["success"] is False


async def test_same_agent_name_isolated_by_project_and_stable_id(tmp_path: Path) -> None:
    projects: dict[str, dict[str, object]] = {}
    runtimes: dict[str, SessionRuntime] = {}
    db_paths: dict[str, Path] = {}
    for project_id, agent_id in (("project-a", "agent-a-id"), ("project-b", "agent-b-id")):
        paths = ProjectPaths.from_locator((tmp_path / project_id).as_uri())
        paths.action_chain_db_path.parent.mkdir(parents=True, exist_ok=True)
        db_paths[project_id] = paths.action_chain_db_path
        runtimes[project_id] = await _seed(paths.action_chain_db_path, agent_id, project_id)
        projects[project_id] = {
            "project_id": project_id,
            "project_root_locator": paths.root.as_uri(),
            "agents": [{"name": "shared", "agent_id": agent_id, "cluster_id": "default"}],
        }
    ledger = LedgerUnit(_config(tmp_path))
    async with Context() as ctx:
        ctx.provide(PROJECT_MANAGER.name, FakeProjectManager(projects))
        _provide_core(ctx, lambda project_id: db_paths[project_id])
        fiber = ctx.plugin(mount_unit(ledger))
        await wait_active(fiber)
        for project_id, agent_id in (("project-a", "agent-a-id"), ("project-b", "agent-b-id")):
            runtime = runtimes[project_id]
            result = await bridge_command(
                ctx,
                "get_chain_history",
                {
                    "project_id": project_id,
                    "agent_id": agent_id,
                    "agent_name": "shared",
                    "session_id": runtime.session.session_id,
                    "branch_id": runtime.active_branch.branch_id,
                },
            )
            assert result["success"] is True
            assert result["data"]["nodes"][-1]["ability_names"] == [project_id]
        cross = await bridge_command(
            ctx,
            "get_chain_history",
            {
                "project_id": "project-a",
                "agent_id": "agent-b-id",
                "agent_name": "shared",
                "session_id": "irrelevant",
                "branch_id": "irrelevant",
            },
        )
        assert cross["success"] is False
        assert cross["error"] == "agent_project_mismatch"
