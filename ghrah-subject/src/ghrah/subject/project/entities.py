# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""AgentEntity 的 Subject 适配：复用 ProjectStore、Core 和项目消息权威。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
from ghrah.protocol.payloads.project import AgentCreateSpecSchema

from ghrah.subject.errors import StableError
from ghrah.subject.project.models import AgentRecord, ProjectRecord, ProjectStatus
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

    def available_ability_types(self) -> list[str]:
        """返回允许通过旧类型接口配置的能力目录。"""
        return []

    @property
    def _agentconf_db_path(self) -> str | None:
        """AgentConf 库路径（与 SubjectConfig / 能力管理 REST 同源）。

        ProjectManager 未注入时为 None，``AgentsConfig`` 回落缺省库（旧行为）。
        """
        return getattr(self._projects, "agentconf_db_path", None)

    def model_catalog(self) -> dict[str, Any]:
        """只返回既有模型配置名称，不向观察端传输凭据。"""
        from agentconf import AgentsConfig

        try:
            with AgentsConfig(self._agentconf_db_path) as config:
                return {
                    "model_config_names": sorted(
                        a.name for a in config.list_model_endpoints() if a.model_type == "llm"
                    )
                }
        except Exception:
            return {"model_config_names": [], "model_catalog_error": "model_catalog_unavailable"}

    def validate_model_name(self, name: str) -> None:
        """校验 agentconf 的完整配置引用；不发送模型请求。"""
        from agentconf import AgentsConfig

        try:
            with AgentsConfig(self._agentconf_db_path) as config:
                config.resolve_model_endpoint(name)
        except Exception as exc:
            raise StableError(
                "model_configuration_invalid", f"Cannot resolve model configuration: {name}"
            ) from exc

    async def create(self, request: AgentEntityCreatePayload) -> AgentEntity:
        """按模板创建 Agent；配置不再经实体面传入（权威在 Core 快照）。"""
        if request.start:
            project, error = await self._projects._get_active_project(request.project_id)
            if error or project is None:
                raise StableError("project_unavailable", str(error))
            if project.status != ProjectStatus.ACTIVE:
                raise StableError("project_not_active")
        result = await self._projects.create_agent(
            request.project_id,
            AgentCreateSpecSchema(
                name=request.name,
                template_id=request.template_id,
                revision=request.revision,
                desired_state="running" if request.start else "stopped",
            ),
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
                        for k, v in (await self._runtime(project, agent)).items()
                        if k not in {"agent_id", "loaded_abilities"}
                    }
                ).model_dump(mode="json"),
            }
            for agent in project.agents
            if agent.agent_id
        ]

    async def _load(self, identity: AgentIdentity) -> tuple[ProjectRecord, AgentRecord]:
        project, error = await self._projects._get_active_project(identity.project_id)
        if error or project is None:
            raise StableError("project_unavailable", str(error))
        agent = next((a for a in project.agents if a.agent_id == identity.agent_id), None)
        if agent is None:
            raise StableError("agent_not_found")
        return project, agent

    def _handle(self, project: ProjectRecord, agent: AgentRecord) -> Any | None:
        try:
            handle = self._projects._cluster_transport.get_handle(project.project_id)
        except KeyError:
            return None
        if handle.project_id != project.project_id:
            raise StableError("agent_project_mismatch")
        return handle if handle.is_connected else None

    async def _runtime(self, project: ProjectRecord, agent: AgentRecord) -> dict[str, Any]:
        handle = self._handle(project, agent)
        if handle is None:
            return {
                "availability": "offline",
                "incarnation_id": None,
                "busy": None,
                "loaded_abilities": None,
            }
        try:
            return await handle.get_entity_runtime(agent.agent_id)
        except Exception as exc:
            return {
                "availability": "unknown",
                "incarnation_id": None,
                "busy": None,
                "loaded_abilities": None,
                "error": str(exc),
            }

    def _abilities(self, agent: AgentRecord, runtime: dict[str, Any]) -> dict[str, Any]:
        return {
            **self.model_catalog(),
            "available_types": self.available_ability_types(),
            "origin": agent.origin.model_dump(mode="json") if agent.origin else None,
            "loaded_names": runtime.get("loaded_abilities"),
            "runtime_availability": runtime["availability"],
        }

    async def _context(
        self, project: ProjectRecord, agent: AgentRecord, operation: dict[str, Any]
    ) -> dict[str, Any]:
        runtime = await self._runtime(project, agent)
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
            checkpoint = await backend.load_checkpoint(
                agent.agent_id, snapshot_payloads="boundaries"
            )
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
        self, project: ProjectRecord, agent: AgentRecord, operation: dict[str, Any]
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

    async def _snapshot(self, project: ProjectRecord, agent: AgentRecord) -> dict[str, Any]:
        runtime = await self._runtime(project, agent)
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
            # 运行时快照只保留 AgentRuntimeSnapshot 的字段：
            # ``agent_id`` 已由 identity 承载，``loaded_abilities`` 单独投影。
            runtime={k: v for k, v in runtime.items() if k not in {"agent_id", "loaded_abilities"}},
            context=context,
            mailbox=mailbox,
            abilities=self._abilities(agent, runtime),
            observed_at=datetime.now(UTC).isoformat(),
        ).model_dump(mode="json")

    async def _save(
        self, project: ProjectRecord, agent: AgentRecord, changes: dict[str, Any]
    ) -> tuple[ProjectRecord, AgentRecord]:
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

    async def update_persisted_spec(
        self,
        project_id: str,
        agent_id: str,
        *,
        expected_revision: int,
        presentation: dict[str, Any] | None = None,
    ) -> tuple[ProjectRecord, AgentRecord]:
        """保存记录的展示字段；端口/提示词等配置不再由 Subject 持有。

        模型与提示词的权威在 Core 的实例快照，改它们需要 Core 侧的能力，
        这里如实拒绝而不是复制一份到 Subject。
        """
        async with self._projects.lifecycle_lock(project_id):
            project, agent = await self._load(
                AgentIdentity(project_id=project_id, agent_id=agent_id)
            )
            if project.version != expected_revision:
                raise StableError("agent_specification_conflict")
            if presentation is None:
                raise StableError("agent_config_immutable", "配置由 Core 实例快照承载")
            current = agent.presentation.model_dump()
            merged = {**current, **presentation}
            changes = {"presentation": agent.presentation.model_copy(update=merged)}
            return await self._save(project, agent, changes)

    async def execute(self, request: AgentEntityPayload) -> dict[str, Any]:
        """对同一项目串行执行实体操作，与既有生命周期命令共用锁。"""
        identity = AgentIdentity(project_id=request.project_id, agent_id=request.agent_id)
        async with self._projects.lifecycle_lock(identity.project_id):
            project, agent = await self._load(identity)
            op = request.operation.model_dump(mode="json")
            kind = op["type"]
            if kind == "snapshot":
                return await self._snapshot(project, agent)
            if kind == "abilities.list":
                return self._abilities(agent, await self._runtime(project, agent))
            if kind.startswith("context."):
                if kind in {"context.create_session", "context.activate_session"} and (
                    project.status != ProjectStatus.ACTIVE or agent.desired_state != "running"
                ):
                    raise StableError("agent_not_running")
                return await self._context(project, agent, op)
            if kind in {"mailbox.list_inbox", "mailbox.list_outbox", "mailbox.get_message"}:
                return await self._mailbox(project, agent, op)
            if kind in {"mailbox.retry", "mailbox.resume", "mailbox.cancel"}:
                if kind != "mailbox.cancel":
                    if project.status != ProjectStatus.ACTIVE or agent.desired_state != "running":
                        raise StableError("agent_not_running")
                    if (await self._runtime(project, agent))["availability"] != "online":
                        raise StableError("agent_offline")
                # 消息管理器按项目取端口实例，归属恒等，不再做项目一致性校验。
                mm = self._projects._cluster_transport.get_message_manager(project.project_id)
                from ghrah.protocol.errors import ApiError

                try:
                    progress = await mm.get_progress(op["delivery_id"], agent_id=agent.agent_id)
                except ApiError as exc:
                    raise StableError("delivery_not_found") from exc
                if progress.delivery.recipient_id != agent.agent_id:
                    raise StableError("delivery_not_found")
                if kind == "mailbox.retry":
                    await mm.retry(op["delivery_id"], agent_id=agent.agent_id)
                elif kind == "mailbox.cancel":
                    try:
                        await mm.cancel(op["delivery_id"], agent_id=agent.agent_id)
                    except ApiError as exc:
                        reason = (exc.problem.details or {}).get("reason")
                        if reason == "delivery_not_cancelable":
                            raise StableError(reason) from exc
                        raise
                else:
                    await mm.resume(op["delivery_id"], agent_id=agent.agent_id)
                final = await mm.get_progress(op["delivery_id"], agent_id=agent.agent_id)
                return {"delivery": final.delivery.model_dump(mode="json")}
            expected = op.get("expected_revision")
            if expected is not None and expected != project.version:
                raise StableError("agent_specification_conflict")
            runtime = await self._runtime(project, agent)
            if runtime["availability"] == "unknown":
                raise StableError("agent_runtime_unavailable", runtime.get("error", ""))
            incarnation = op.get("expected_incarnation_id")
            if incarnation is not None and incarnation != runtime.get("incarnation_id"):
                raise StableError("agent_incarnation_conflict")
            if kind == "model.configure":
                # 配置权威在 Core 的实例快照：这里只把「改模型」这件事交给 Core，
                # 由它落库并按需重启；Subject 不再持有模型与提示词。
                try:
                    result = await self._handle(project, agent).update_model(
                        agent.agent_id,
                        op["endpoint_name"],
                        op.get("model_overrides"),
                    )
                except Exception as exc:
                    raise StableError("agent_config_update_failed", str(exc)) from exc
                if not result.get("success"):
                    raise StableError("agent_config_update_failed", str(result.get("error") or ""))
                project, agent = await self._load(identity)
                await self._save(
                    project,
                    agent,
                    {"runtime_status": "running", "last_error": ""},
                )
                return await self._snapshot(*await self._load(identity))
            if kind == "abilities.configure":
                raise StableError(
                    "abilities_managed_by_access_store",
                    "Use ability grants and tool selection to configure this Agent",
                )
            if kind == "start":
                if project.status != ProjectStatus.ACTIVE:
                    raise StableError("project_not_active")
                if agent.desired_state != "running":
                    project, agent = await self._save(project, agent, {"desired_state": "running"})
                if runtime["availability"] == "offline":
                    error = await self._projects._ensure_agent_spawned(
                        agent, project.project_id, project.project_root_locator, validate_model=True
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
                        result = await self._handle(project, agent).stop_assembly(agent.agent_id)
                    except Exception as exc:
                        raise StableError("agent_stop_failed", str(exc)) from exc
                    if not result.get("success"):
                        raise StableError("agent_stop_failed", str(result.get("error")))
                project, agent = await self._load(identity)
                await self._save(
                    project,
                    agent,
                    {"runtime_status": "stopped", "last_error": ""},
                )
            else:
                raise StableError("unsupported_entity_operation")
            return await self._snapshot(*await self._load(identity))
