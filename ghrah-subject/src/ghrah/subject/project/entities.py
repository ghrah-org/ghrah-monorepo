"""AgentEntity 的 Subject 适配：复用 ProjectStore、Core 和项目消息权威。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ghrah.abilities.registry import AbilityRegistry
from ghrah.agents.entity import AgentEntity
from ghrah.communication.mailbox_reader import MailboxReader
from ghrah.context.persistence import serialize_node
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.session_runtime import SessionRuntime
from ghrah.protocol.payloads.entity import (
    AgentEntityCreatePayload,
    AgentEntityPayload,
    AgentIdentity,
    AgentRuntimeSnapshot,
    AgentSnapshot,
)

from ghrah.subject.errors import StableError
from ghrah.subject.project.models import AgentSpec, ProjectRecord, ProjectStatus
from ghrah.subject.project.paths import ProjectPaths

if TYPE_CHECKING:
    from ghrah.subject.project.manager import ProjectManager


class AgentEntityManager:
    """取得长期实体句柄；操作均重新校验身份并读取现有权威，不保存另一份实体。"""

    def __init__(self, projects: ProjectManager) -> None:
        self._projects = projects

    def get(self, project_id: str, agent_id: str) -> AgentEntity:
        """绑定稳定身份；实体是否存在由每次操作验证。"""
        return AgentEntity(AgentIdentity(project_id=project_id, agent_id=agent_id), self)

    @staticmethod
    def available_ability_types() -> list[str]:
        """返回当前注册的类型目录，供离线实体配置使用。"""
        return sorted(AbilityRegistry.list_types())

    async def create(self, request: AgentEntityCreatePayload) -> AgentEntity:
        """经原 ProjectManager 创建持久规格，显式选择初始启停状态。"""
        async with self._projects._lifecycle_lock(request.project_id):
            project, error = await self._projects._get_active_project(request.project_id)
            if error or project is None:
                raise StableError("project_unavailable", str(error))
            if request.start and project.status != ProjectStatus.ACTIVE:
                raise StableError("project_not_active")
            result = await self._projects._handle_add_agent(
                {
                    "project_id": request.project_id,
                    "agent": {
                        "name": request.name,
                        "cluster_id": request.cluster_id,
                        "manifest_ref": request.manifest_ref,
                        "system_prompt": request.system_prompt,
                        "abilities": request.ability_types,
                        "desired_state": "running" if request.start else "stopped",
                    },
                },
            )
        if not result.get("success"):
            raise StableError("agent_create_failed", str(result.get("error")))
        return self.get(request.project_id, result["data"]["agent_id"])

    async def list(self, project_id: str) -> list[dict[str, Any]]:
        """列出持久实体及当前运行状态，不加载全部上下文和消息正文。"""
        project, error = await self._projects._get_active_project(project_id)
        if error or project is None:
            raise StableError("project_unavailable", str(error))
        return [
            {
                "identity": {"project_id": project_id, "agent_id": agent.agent_id},
                "specification": agent.model_dump(mode="json"),
                "specification_revision": project.version,
                "runtime": AgentRuntimeSnapshot.model_validate(
                    {
                        k: v
                        for k, v in self._runtime(project, agent).items()
                        if k != "loaded_abilities"
                    }
                ).model_dump(mode="json"),
            }
            for agent in project.agents
            if agent.agent_id
        ]

    async def _load(self, identity: AgentIdentity) -> tuple[ProjectRecord, AgentSpec]:
        project, error = await self._projects._get_active_project(identity.project_id)
        if error or project is None:
            raise StableError("project_unavailable", str(error))
        agent = next((a for a in project.agents if a.agent_id == identity.agent_id), None)
        if agent is None:
            raise StableError("agent_not_found")
        return project, agent

    def _handle(self, project: ProjectRecord, agent: AgentSpec) -> Any | None:
        try:
            handle = self._projects._cluster_transport.get_handle(agent.cluster_id)
        except KeyError:
            return None
        if handle.project_id != project.project_id:
            raise StableError("cluster_project_mismatch")
        return handle if handle.is_connected else None

    def _runtime(self, project: ProjectRecord, agent: AgentSpec) -> dict[str, Any]:
        handle = self._handle(project, agent)
        if handle is None:
            return {
                "availability": "offline",
                "incarnation_id": None,
                "busy": None,
                "loaded_abilities": None,
            }
        try:
            return handle.get_entity_runtime(agent.agent_id)
        except Exception as exc:
            return {
                "availability": "unknown",
                "incarnation_id": None,
                "busy": None,
                "loaded_abilities": None,
                "error": str(exc),
            }

    @staticmethod
    def _abilities(agent: AgentSpec, runtime: dict[str, Any]) -> dict[str, Any]:
        return {
            "configured_types": agent.abilities,
            "available_types": AgentEntityManager.available_ability_types(),
            "manifest_ref": agent.manifest_ref,
            "loaded_names": runtime.get("loaded_abilities"),
            "runtime_availability": runtime["availability"],
        }

    async def _context(
        self, project: ProjectRecord, agent: AgentSpec, operation: dict[str, Any]
    ) -> dict[str, Any]:
        runtime = self._runtime(project, agent)
        if runtime["availability"] == "online":
            handle = self._handle(project, agent)
            return await handle.entity_context(agent.agent_id, operation)
        if operation["type"] not in {"context.list_sessions", "context.get_history"}:
            raise StableError("agent_offline")
        path = ProjectPaths.from_locator(project.project_root_locator).action_chain_db_path
        if not path.exists():
            if operation["type"] == "context.get_history":
                raise StableError("session_not_found")
            return {"active_session_id": None, "sessions": [], "branches": []}
        async with SqliteBackend(path, read_only=True) as backend:
            if operation["type"] == "context.list_sessions":
                return await backend.load_session_catalog(agent.agent_id)
            checkpoint = await backend.load_checkpoint(agent.agent_id)
        session_id = operation["session_id"]
        session = (
            next((s for s in checkpoint.sessions if s.session_id == session_id), None)
            if checkpoint is not None
            else None
        )
        if session is None:
            raise StableError("session_not_found")
        session_runtime = SessionRuntime.restore(
            session=session,
            branches=[b for b in checkpoint.branches if b.session_id == session_id],
            nodes=[n for n in checkpoint.nodes if n.session_id == session_id],
        )
        nodes = session_runtime.get_history(
            branch_id=operation["branch_id"], limit=operation["limit"]
        )
        return {
            "session_id": session_id,
            "branch_id": operation["branch_id"],
            "nodes": [serialize_node(node) for node in nodes],
        }

    async def _mailbox(
        self, project: ProjectRecord, agent: AgentSpec, operation: dict[str, Any]
    ) -> dict[str, Any]:
        identity = AgentIdentity(project_id=project.project_id, agent_id=agent.agent_id)
        path = ProjectPaths.from_locator(project.project_root_locator).action_chain_db_path
        if not path.exists():
            if operation["type"] == "mailbox.get_message":
                raise StableError("message_not_found")
            if operation["type"] == "mailbox.summary":
                return {"availability": "empty", "inbox_counts": {}, "outbox_count": 0}
            return {"items": [], "next_before_seq": None}
        async with MailboxReader(path, identity) as reader:
            if operation["type"] == "mailbox.summary":
                return await reader.summary()
            if operation["type"] == "mailbox.get_message":
                return await reader.get_message(operation["message_id"])
            return await reader.list_messages(
                inbox=operation["type"] == "mailbox.list_inbox",
                limit=operation["limit"],
                before_seq=operation["before_seq"],
            )

    async def _snapshot(self, project: ProjectRecord, agent: AgentSpec) -> dict[str, Any]:
        runtime = self._runtime(project, agent)
        try:
            catalog = await self._context(project, agent, {"type": "context.list_sessions"})
            context = {
                "availability": "available",
                "active_session_id": catalog["active_session_id"],
                "session_count": len(catalog["sessions"]),
                "branch_count": len(catalog["branches"]),
            }
        except Exception as exc:
            context = {"availability": "unknown", "error": str(exc)}
        try:
            mailbox = await self._mailbox(project, agent, {"type": "mailbox.summary"})
        except Exception as exc:
            mailbox = {"availability": "unknown", "error": str(exc)}
        return AgentSnapshot(
            identity=AgentIdentity(project_id=project.project_id, agent_id=agent.agent_id),
            specification=agent.model_dump(mode="json"),
            specification_revision=project.version,
            runtime={k: v for k, v in runtime.items() if k != "loaded_abilities"},
            context=context,
            mailbox=mailbox,
            abilities=self._abilities(agent, runtime),
            observed_at=datetime.now(UTC).isoformat(),
        ).model_dump(mode="json")

    async def _save(
        self, project: ProjectRecord, agent: AgentSpec, changes: dict[str, Any]
    ) -> tuple[ProjectRecord, AgentSpec]:
        updated = agent.model_copy(update=changes)
        record = await self._projects._store.update(
            project.project_id,
            project.version,
            lambda r: r.model_copy(
                update={
                    "agents": [updated if a.agent_id == agent.agent_id else a for a in r.agents]
                }
            ),
        )
        await self._projects._emit("project_updated", record)
        return record, updated

    async def execute(self, request: AgentEntityPayload) -> dict[str, Any]:
        """对同一项目串行执行实体操作，与既有生命周期命令共用锁。"""
        identity = AgentIdentity(project_id=request.project_id, agent_id=request.agent_id)
        async with self._projects._lifecycle_lock(identity.project_id):
            project, agent = await self._load(identity)
            op = request.operation.model_dump(mode="json")
            kind = op["type"]
            if kind == "snapshot":
                return await self._snapshot(project, agent)
            if kind == "abilities.list":
                return self._abilities(agent, self._runtime(project, agent))
            if kind.startswith("context."):
                if kind in {"context.create_session", "context.activate_session"} and (
                    project.status != ProjectStatus.ACTIVE or agent.desired_state != "running"
                ):
                    raise StableError("agent_not_running")
                return await self._context(project, agent, op)
            if kind in {"mailbox.list_inbox", "mailbox.list_outbox", "mailbox.get_message"}:
                return await self._mailbox(project, agent, op)
            if kind in {"mailbox.retry", "mailbox.resume"}:
                if project.status != ProjectStatus.ACTIVE or agent.desired_state != "running":
                    raise StableError("agent_not_running")
                if self._runtime(project, agent)["availability"] != "online":
                    raise StableError("agent_offline")
                mm = self._projects._cluster_transport.get_message_manager(project.project_id)
                if mm.project_id != project.project_id:
                    raise StableError("mailbox_project_mismatch")
                try:
                    progress = await mm.get_progress(op["delivery_id"])
                except KeyError as exc:
                    raise StableError("delivery_not_found") from exc
                if progress.delivery.recipient_id != agent.agent_id:
                    raise StableError("delivery_not_found")
                if kind == "mailbox.retry":
                    await mm.retry(op["delivery_id"])
                else:
                    await mm.resume_delivery(op["delivery_id"])
                return {"delivery": asdict((await mm.get_progress(op["delivery_id"])).delivery)}
            expected = op.get("expected_revision")
            if expected is not None and expected != project.version:
                raise StableError("agent_specification_conflict")
            runtime = self._runtime(project, agent)
            if runtime["availability"] == "unknown":
                raise StableError("agent_runtime_unavailable", runtime.get("error", ""))
            incarnation = op.get("expected_incarnation_id")
            if incarnation is not None and incarnation != runtime.get("incarnation_id"):
                raise StableError("agent_incarnation_conflict")
            if kind == "abilities.configure":
                if agent.desired_state != "stopped" or runtime["availability"] != "offline":
                    raise StableError("agent_must_be_stopped")
                if agent.manifest_ref:
                    raise StableError("abilities_managed_by_manifest")
                names = op["ability_types"]
                if len(set(names)) != len(names) or any(not AbilityRegistry.has(n) for n in names):
                    raise StableError("invalid_ability_types")
                project, agent = await self._save(project, agent, {"abilities": names})
                return await self._snapshot(project, agent)
            if kind == "start":
                if project.status != ProjectStatus.ACTIVE:
                    raise StableError("project_not_active")
                if agent.desired_state != "running":
                    project, agent = await self._save(project, agent, {"desired_state": "running"})
                if runtime["availability"] == "offline":
                    error = await self._projects._ensure_agent_spawned(
                        agent, project.project_id, project.project_root_locator
                    )
                    await self._projects.mark_agent_runtime(
                        project.project_id, agent.agent_id, error
                    )
                    if error:
                        raise StableError("agent_start_failed", error)
            elif kind == "stop":
                # 先保存停止意图，崩溃或终止失败后协调器也不会重新启动它。
                if agent.desired_state != "stopped":
                    project, agent = await self._save(project, agent, {"desired_state": "stopped"})
                if runtime["availability"] == "online":
                    try:
                        result = await self._handle(project, agent).terminate_agent(
                            agent.agent_id, agent.name
                        )
                    except Exception as exc:
                        raise StableError("agent_stop_failed", str(exc)) from exc
                    if not result.get("success"):
                        raise StableError("agent_stop_failed", str(result.get("error")))
                project, agent = await self._load(identity)
                await self._save(project, agent, {"runtime_status": "stopped", "runtime_error": ""})
            else:
                raise StableError("unsupported_entity_operation")
            return await self._snapshot(*await self._load(identity))
