# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""CoreClusterRegistry — cluster = CoreUnit 实例（进程内注册表）。

取代旧 ``ClusterTransportManager``（per-cluster WS transport 句柄管理）：
``ensure_cluster(cluster_id)`` 在 Ouroboros ctx 上**运行时挂载**一个
CoreUnit 实例（``ctx.plugin(mount_unit(unit))``，热插拔 API 的首个真实
消费者）；``shutdown_cluster`` → ``fiber.dispose()``。多集群 = 多实例
（CoreUnit 不向宿主 provide 全局服务——supervisor 等每实例状态由实例
自持，多实例挂载天然隔离，无同名服务冲突）。

``CoreUnitHandle`` duck-type 现有 ``ClusterHandle`` 消费面
（spawn_agent/list_agents/terminate_agent/is_connected），命令经
``unit.handle_command`` 进程内直达 CoreUnit。manifest_ref 的解析/物化
由 CoreUnit 内部完成（对齐 Core 独立库 runner 范式，不经 wire DTO 往返）；
subject 仅注入 ManifestStore（``CoreUnitConfig.manifest_store``）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ghrah.protocol.types import (
    ListAgentsPayload,
    SendMessagePayload,
    SpawnAgentPayload,
    TerminateAgentPayload,
)

from ghrah.subject.errors import StableError
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.runtime.ouroboros_bridge import mount_unit, wait_active
from ghrah.subject.unit.base import CommandContext

if TYPE_CHECKING:
    from ouroboros import Context, Fiber  # type: ignore[import-untyped]

    from ghrah.subject.config import SubjectConfig

__all__ = [
    "CoreClusterRegistry",
    "CoreUnitHandle",
    "default_core_unit_factory",
]

logger = logging.getLogger(__name__)

_INTERNAL_CMD_CTX = CommandContext.internal()


class CoreUnitHandle:
    """单 cluster 的 Core 接入门面（ClusterHandle duck-type，进程内直达）。

    is_connected = 实例已挂载且未被 shutdown；关闭后命令返回 failure dict
    （对齐旧 transport 断连时的回执语义，消费者只看 ``success``）。
    """

    def __init__(
        self,
        registry: CoreClusterRegistry,
        cluster_id: str,
        project_id: str,
        project_root_locator: str,
        unit: Any,
    ) -> None:
        self._registry = registry
        self._cluster_id = cluster_id
        self._project_id = project_id
        self._project_root_locator = project_root_locator
        self._unit = unit
        self._alive = True

    @property
    def cluster_id(self) -> str:
        return self._cluster_id

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def project_root_locator(self) -> str:
        return self._project_root_locator

    @property
    def is_connected(self) -> bool:
        """该 cluster 的 CoreUnit 实例是否仍在运行。"""
        return self._alive

    async def spawn_agent(self, payload: SpawnAgentPayload) -> dict[str, Any]:
        """经进程内 CoreUnit 发 spawn_agent（manifest_ref 由 CoreUnit 内部解析）。"""
        if not self._alive:
            return {"success": False, "error": f"cluster '{self._cluster_id}' is shut down"}
        if payload.project_id != self._project_id:
            return {"success": False, "error": "cluster_project_mismatch"}
        if payload.cluster_id and payload.cluster_id != self._cluster_id:
            return {"success": False, "error": "cluster_id_mismatch"}
        return await self._dispatch("spawn_agent", payload.model_dump(mode="json"))

    async def list_agents(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """经进程内 CoreUnit 发 list_agents，返回 agent 信息列表。"""
        if not self._alive:
            return []
        requested_project = project_id or self._project_id
        if requested_project != self._project_id:
            return []
        result = await self._dispatch(
            "list_agents",
            ListAgentsPayload(project_id=requested_project).model_dump(mode="json"),
        )
        if not result.get("success"):
            return []
        data = result.get("data")
        if isinstance(data, list):
            return [a for a in data if isinstance(a, dict)]
        if isinstance(data, dict):
            agents = data.get("agents")
            if isinstance(agents, list):
                return [a for a in agents if isinstance(a, dict)]
        return []

    async def terminate_agent(self, agent_id: str, agent_name: str) -> dict[str, Any]:
        """经进程内 CoreUnit 发 terminate_agent，返回回执 dict。"""
        if not self._alive:
            return {"success": False, "error": f"cluster '{self._cluster_id}' is shut down"}
        payload = TerminateAgentPayload(
            project_id=self._project_id,
            agent_id=agent_id,
            name=agent_name,
        ).model_dump(mode="json")
        return await self._dispatch("terminate_agent", payload)

    async def send_message(self, payload: SendMessagePayload) -> dict[str, Any]:
        """把消息定向发给本 cluster 的 CoreUnit，避免全局命令路由串群。"""
        if not self._alive:
            return {"success": False, "error": f"cluster '{self._cluster_id}' is shut down"}
        if payload.project_id != self._project_id:
            return {"success": False, "error": "cluster_project_mismatch"}
        return await self._dispatch("send_message", payload.model_dump(mode="json"))

    async def dispatch(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """向本 cluster CoreUnit 定向派发内部命令。"""
        if not self._alive:
            return {"success": False, "error": f"cluster '{self._cluster_id}' is shut down"}
        payload_project_id = str(payload.get("project_id") or "")
        if payload_project_id and payload_project_id != self._project_id:
            return {"success": False, "error": "cluster_project_mismatch"}
        return await self._dispatch(command, payload)

    async def shutdown(self) -> None:
        """关闭该 cluster（dispose CoreUnit fiber，幂等）。"""
        await self._registry.shutdown_cluster(self._cluster_id)

    async def _dispatch(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._unit.handle_command(command, payload, _INTERNAL_CMD_CTX)
        if not isinstance(result, dict):
            return {"success": False, "error": f"Unexpected result for {command}"}
        return result


class _ClusterEntry:
    """一个已挂载 cluster 的登记项。"""

    __slots__ = ("fiber", "handle", "project_id", "project_root_locator", "unit")

    def __init__(
        self,
        unit: Any,
        fiber: Fiber,
        handle: CoreUnitHandle,
        project_id: str,
        project_root_locator: str,
    ) -> None:
        self.unit = unit
        self.fiber = fiber
        self.handle = handle
        self.project_id = project_id
        self.project_root_locator = project_root_locator


class CoreClusterRegistry:
    """cluster → CoreUnit 实例注册表（ensure/get/shutdown，幂等）。

    ``unit_factory: (cluster_id, project_id, project_root_locator) -> unit``
    （duck-typed，默认 ``default_core_unit_factory``；测试注入假工厂）。
    """

    def __init__(
        self,
        *,
        unit_factory: Callable[[str, str, str], Any],
    ) -> None:
        self._unit_factory = unit_factory
        self._ctx: Context | None = None
        self._clusters: dict[str, _ClusterEntry] = {}
        # per-cluster 互斥：ensure/shutdown 的检查-挂载-登记窗口含多个 await
        # （wait_active 最长 10s），无锁时并发调用会双挂载 CoreUnit（双 fiber
        # 指向同一 project sqlite），必须串行化。
        self._locks: dict[str, asyncio.Lock] = {}

    def bind(self, ctx: Context) -> None:
        """绑定宿主 ctx（unit.init 时调用；运行时挂载经它 ctx.plugin）。"""
        self._ctx = ctx

    @property
    def cluster_ids(self) -> list[str]:
        return list(self._clusters)

    def _lock_for(self, cluster_id: str) -> asyncio.Lock:
        """取/建 per-cluster 锁（事件循环内同步完成，无竞态窗口）。"""
        lock = self._locks.get(cluster_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cluster_id] = lock
        return lock

    async def ensure_cluster(
        self,
        cluster_id: str,
        *,
        project_id: str,
        project_root_locator: str,
    ) -> CoreUnitHandle:
        """幂等挂载/取某 cluster 的 CoreUnit 实例（per-cluster 互斥）。"""
        if not project_id:
            raise ValueError("project_id required")
        if not project_root_locator:
            raise ValueError("project_root_locator required")
        async with self._lock_for(cluster_id):
            entry = self._clusters.get(cluster_id)
            if entry is not None:
                if (
                    entry.project_id != project_id
                    or entry.project_root_locator != project_root_locator
                ):
                    raise StableError(
                        "cluster_owner_conflict",
                        f"{cluster_id} belongs to project {entry.project_id}",
                    )
                return entry.handle
            if self._ctx is None:
                raise ValueError(
                    "cluster_mount_failed: CoreClusterRegistry has not been bound to a Context."
                )

            fiber: Fiber | None = None
            try:
                unit = self._unit_factory(cluster_id, project_id, project_root_locator)
                prepare = getattr(self._unit_factory, "prepare", None)
                if callable(prepare):
                    await prepare(unit, cluster_id, project_id, project_root_locator)
                fiber = self._ctx.plugin(mount_unit(unit))
                await wait_active(fiber, timeout=10.0)
            except BaseException as exc:
                if fiber is not None:
                    # 半挂载回收：不留无主 fiber（泄漏 + 双写同一 project sqlite）。
                    with contextlib.suppress(Exception):
                        await fiber.dispose()
                release = getattr(self._unit_factory, "release", None)
                if callable(release):
                    await release(cluster_id)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise ValueError(f"cluster_mount_failed: {cluster_id}: {exc}") from exc
            handle = CoreUnitHandle(self, cluster_id, project_id, project_root_locator, unit)
            self._clusters[cluster_id] = _ClusterEntry(
                unit, fiber, handle, project_id, project_root_locator
            )
            logger.info("CoreClusterRegistry: mounted cluster '%s'", cluster_id)
            return handle

    def get_handle(self, cluster_id: str) -> CoreUnitHandle:
        """取已挂载 handle；不存在 raise KeyError。"""
        entry = self._clusters.get(cluster_id)
        if entry is None:
            raise KeyError(cluster_id)
        return entry.handle

    def has_cluster(self, cluster_id: str) -> bool:
        return cluster_id in self._clusters

    def get_message_manager(self, project_id: str) -> Any:
        """取得项目共享的 Core 消息权威，禁止另开第二个管理器。"""
        for entry in self._clusters.values():
            if entry.project_id == project_id:
                manager = getattr(getattr(entry.unit, "_config", None), "message_manager", None)
                if manager is not None:
                    return manager
        raise KeyError(project_id)

    async def shutdown_cluster(self, cluster_id: str) -> None:
        """dispose 某 cluster 的 fiber（handle 失效，幂等；与 ensure 互斥）。"""
        async with self._lock_for(cluster_id):
            entry = self._clusters.pop(cluster_id, None)
            if entry is None:
                return
            entry.handle._alive = False
            try:
                await entry.fiber.dispose()
            finally:
                release = getattr(self._unit_factory, "release", None)
                if callable(release):
                    await release(cluster_id)
        logger.info("CoreClusterRegistry: disposed cluster '%s'", cluster_id)

    async def stop(self) -> None:
        """关闭全部 cluster（unit.stop 时调用，幂等）。"""
        for cluster_id in list(self._clusters):
            await self.shutdown_cluster(cluster_id)


def default_core_unit_factory(
    config: SubjectConfig,
    manifest_store: Any = None,
    llm_factory: Callable[[Any], Any] | None = None,
    room_projector: Callable[[Any], Awaitable[None]] | None = None,
) -> Callable[[str, str, str], Any]:
    """生产工厂：(cluster_id, project_id, root_locator) → CoreUnit。

    persistence_factory 注入 Project Root 内的 chain sqlite（经
    ProjectPaths 定位）——agent 链真相源永远落在目标 Project Root；
    空 locator 直接拒绝（不再回退实例级全局库）。

    ``manifest_store`` 注入 CoreUnitConfig.manifest_store，供 CoreUnit 内部
    解析 manifest_ref spawn（对齐 Core 独立库 runner 范式）。由
    ``CoreClusterRegistryUnit.init`` 从 ctx 取 ``MANIFEST_STORE`` 后传入
    （挂载顺序保证 store 先于 CoreUnit，见 mount_builtin_units）。

     ``config.skill.root`` 注入 CoreUnitConfig.skill_root：Core 侧据此构造
     SkillAbilityProvider（skill 清单 + 上下文目录摘要）。建目录与补齐 lock
     仍由 SkillUnit 在装配期完成——Core 只读，不重复初始化。

    ``llm_factory`` 为可选的 per-cluster LLM 工厂注入（透传
    CoreUnitConfig.llm_factory；None = Core 内建默认 agentconf 解析）。
    部署方显式声明 LLM 出口的程序化接缝——SubjectConfig 暂不暴露用户级
    字段（零隐式；测试/嵌入方经 CoreClusterRegistryUnit(unit_factory=...)
    或本参数注入）。
    """
    from ghrah.communication import MessageManager
    from ghrah.context.persistence.sqlite_backend import (  # type: ignore[import-untyped]
        SqliteBackend,
    )
    from ghrah.core.unit import (  # type: ignore[import-untyped]
        CoreUnitConfig,
        create_core_unit,
    )

    managers: dict[str, tuple[str, MessageManager, set[str]]] = {}
    memberships: dict[str, str] = {}
    manager_lock = asyncio.Lock()

    async def prepare(unit: Any, cluster_id: str, project_id: str, locator: str) -> None:
        async with manager_lock:
            existing = managers.get(project_id)
            if existing and existing[0] != locator:
                raise ValueError("Project message database owner conflict")
            if existing is None:
                path = ProjectPaths.from_locator(locator).action_chain_db_path
                async with SqliteBackend(path) as backend:
                    await backend.load_checkpoint("bootstrap")
                manager = MessageManager(
                    path,
                    project_id=project_id,
                    agent_ids=(),
                    session_routing=True,
                    allow_persisted_agents=True,
                )
                await manager.open()
                if room_projector is not None:
                    manager.configure_room_projector(room_projector)
                existing = (locator, manager, set())
                managers[project_id] = existing
            existing[2].add(cluster_id)
            memberships[cluster_id] = project_id
            unit._config = replace(unit._config, message_manager=existing[1])

    async def release(cluster_id: str) -> None:
        async with manager_lock:
            project_id = memberships.pop(cluster_id, None)
            if project_id is None:
                return
            entry = managers[project_id]
            entry[2].discard(cluster_id)
            if not entry[2]:
                await entry[1].close()
                managers.pop(project_id)

    def factory(cluster_id: str, project_id: str, project_root_locator: str) -> Any:
        if not project_root_locator:
            raise ValueError("project_root_locator required")
        core_db_path = str(ProjectPaths.from_locator(project_root_locator).action_chain_db_path)

        def persistence_factory(agent_config: Any) -> Any:
            return SqliteBackend(db_path=core_db_path)

        core_config = CoreUnitConfig(
            cluster_id=cluster_id,
            project_id=project_id,
            hitl_timeout=config.core.command_timeout,
            workspace_root=config.workspace_root,
            auto_approve_abilities=tuple(config.hitl_policy.auto_approve_abilities),
            require_approval_by_default=config.hitl_policy.require_approval_by_default,
            safe_extra_commands=tuple(config.hitl_policy.safe_extra_commands),
            safe_extra_sub_commands=tuple(
                (base, tuple(subs))
                for base, subs in config.hitl_policy.safe_extra_sub_commands.items()
            ),
            hitl_consecutive_timeout_limit=config.hitl_policy.hitl_consecutive_timeout_limit,
            hitl_degraded_timeout=config.hitl_policy.hitl_degraded_timeout,
            environment_injection=config.hitl_policy.environment_injection,
            persistence_factory=persistence_factory,
            manifest_store=manifest_store,
            # skill-ability：根目录由 SubjectConfig 单一决定（skill slice），
            # Core 侧只按此根构造 provider 读 skill（建目录/lock 由 SkillUnit
            # 在装配期完成，Core 不重复初始化）。
            skill_root=config.skill.root,
            llm_factory=llm_factory,
            # 部署方显式声明（fail-closed 兼容通道）：空配置 → None →
            # 无 abilities 快速 spawn 在 core 侧 raise 指路 manifest_ref。
            default_abilities=tuple(config.default_spawn_abilities) or None,
        )
        return create_core_unit(core_config)

    factory.prepare = prepare
    factory.release = release
    return factory
