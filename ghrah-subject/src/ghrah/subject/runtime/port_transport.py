# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``CoreClusterRegistryService`` 的端口实现：运行时归 Core，Subject 只转发。

Subject 不再自己创建/挂载 CoreUnit 实例。所有对「某个 Agent 的运行时操作」
都转成 ``CORE_PORT`` 的能力调用：端口内部持有单元生命周期，Subject 只保留
cluster 标识到项目标识的映射。

``entity_context`` 接受的是历史遗留的操作字典；这里把它拆成能力调用，
返回值统一是协议视图的 ``model_dump``——与观察端/WS 契约同源，不再有
第二套形状。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ghrah.protocol.ports.keys import CORE_PORT
from ghrah.protocol.views.core import AgentSpawnSpec, BranchListView

logger = logging.getLogger(__name__)

__all__ = ["PortClusterTransport"]


@dataclass(frozen=True)
class _ClusterEntry:
    """cluster → 项目 的登记项。"""

    project_id: str
    project_root_locator: str


class _Progress:
    """``MessageManager.get_progress`` 的最小替身：只暴露 ``delivery``。"""

    def __init__(self, delivery: Any) -> None:
        self.delivery = delivery


class _Published:
    """``MessageManager.send*`` 的最小替身：只暴露 ``message_id``。"""

    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


class PortMailbox:
    """按项目访问投递的端口替身（替代直接持有 ``MessageManager``）。

    只实现 Subject 实际用到的成员，其余一律不暴露——避免又开出一条
    绕过能力层的旁路。
    """

    def __init__(self, port: Any, project_id: str) -> None:
        self._port = port
        self.project_id = project_id

    async def get_progress(self, delivery_id: str, *, agent_id: str) -> _Progress:
        """读取一条投递的当前状态。"""
        view = await self._port.project(self.project_id).deliveries.get(
            agent_id=agent_id,
            delivery_id=delivery_id,
        )
        return _Progress(view)

    async def retry(self, delivery_id: str, *, agent_id: str) -> None:
        """重试一条投递。"""
        await self._port.project(self.project_id).deliveries.retry(
            agent_id=agent_id,
            delivery_id=delivery_id,
        )

    async def resume(self, delivery_id: str, *, agent_id: str) -> None:
        """恢复一条投递。"""
        await self._port.project(self.project_id).deliveries.resume(
            agent_id=agent_id,
            delivery_id=delivery_id,
        )

    async def send_external(
        self,
        *,
        sender: str,
        recipient_ids: Any,
        content: str,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> _Published:
        """以外部（人）身份发布消息：转 ``mailbox.publish_external``。"""
        view = await self._port.project(self.project_id).mailbox.publish_external(
            sender=sender,
            recipient_ids=[str(item) for item in recipient_ids],
            content=content,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        return _Published(view.message_id)

    async def send(
        self,
        *,
        sender_id: str,
        recipient_ids: Any = (),
        content: str,
        kind: Any = None,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> _Published:
        """发布通知类消息：转 ``mailbox.publish_notification``。"""
        raw_kind = getattr(kind, "value", kind)
        view = await self._port.project(self.project_id).mailbox.publish_notification(
            sender_id=sender_id,
            content=content,
            kind=str(raw_kind or "notification"),
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        return _Published(view.message_id)

    async def recover_room_projections(self) -> None:
        """补齐 Room 投影日志：转 ``mailbox.recover_room_projections``。"""
        await self._port.project(self.project_id).mailbox.recover_room_projections()


class PortClusterHandle:
    """单个 cluster 的门面：每个方法都转成一次 Core 能力调用。"""

    def __init__(self, transport: PortClusterTransport, cluster_id: str) -> None:
        self._transport = transport
        self._cluster_id = cluster_id

    @property
    def cluster_id(self) -> str:
        """本 handle 对应的 cluster 标识。"""
        return self._cluster_id

    @property
    def _entry(self) -> _ClusterEntry:
        return self._transport._entry(self._cluster_id)

    @property
    def _port(self) -> Any:
        """绑定到本项目的作用域端口。"""
        return self._transport.port.project(self._entry.project_id)

    @property
    def project_id(self) -> str:
        """cluster 所属项目。"""
        return self._entry.project_id

    @property
    def is_connected(self) -> bool:
        """cluster 是否仍被注册（Core 单元生命周期由端口负责）。"""
        return self._transport.has_cluster(self._cluster_id)

    async def spawn_agent(self, payload: Any) -> dict[str, Any]:
        """创建 Agent：转 ``agents.create``。"""
        if not self.is_connected:
            return {"success": False, "error": f"cluster '{self._cluster_id}' is shut down"}
        if str(getattr(payload, "project_id", "")) != self.project_id:
            return {"success": False, "error": "cluster_project_mismatch"}
        config = getattr(payload, "config", None)
        spec = AgentSpawnSpec(
            name=getattr(config, "name", "") or "",
            agent_id=getattr(config, "agent_id", "") or "",
            manifest_ref=getattr(payload, "manifest_ref", "") or "",
            endpoint_name=getattr(config, "endpoint_name", None),
            description=getattr(config, "description", "") or "",
            system_prompt=getattr(config, "system_prompt", "") or "",
            max_iterations=int(getattr(config, "max_iterations", 10) or 10),
            communication_timeout=float(getattr(config, "communication_timeout", 300.0) or 300.0),
            window=getattr(config, "window", None),
            context=getattr(config, "context", None),
            model_overrides=getattr(config, "model_overrides", None),
            validate_model=bool(getattr(payload, "validate_model", False)),
        )
        agent_id = await self._port.agents.create(spec=spec)
        return {"success": True, "data": {"agent_id": agent_id}}

    async def get_entity_runtime(self, agent_id: str) -> dict[str, Any]:
        """查询执行实例：转 ``agents.runtime``。"""
        if not self.is_connected:
            return {
                "availability": "offline",
                "incarnation_id": None,
                "busy": None,
                "loaded_abilities": None,
            }
        view = await self._port.agents.runtime(agent_id=agent_id)
        return view.model_dump(mode="json")

    async def entity_context(self, agent_id: str, operation: dict[str, Any]) -> dict[str, Any]:
        """把操作字典拆成能力调用（返回协议视图）。"""
        if not self.is_connected:
            raise ValueError("agent_offline")
        return await self._dispatch_operation(agent_id, operation)

    async def _dispatch_operation(self, agent_id: str, operation: dict[str, Any]) -> Any:
        port = self._port
        kind = str(operation.get("type") or "")
        if kind == "context.list_sessions":
            return (await port.sessions.catalog(agent_id=agent_id)).model_dump(mode="json")
        if kind == "context.get_history":
            view = await port.sessions.history(
                agent_id=agent_id,
                session_id=operation["session_id"],
                branch_id=operation["branch_id"],
                limit=operation.get("limit", -1),
            )
            return view.model_dump(mode="json")
        if kind == "context.create_session":
            session_id = await port.sessions.create(
                agent_id=agent_id,
                name=operation.get("name"),
            )
            return {"session_id": session_id}
        if kind == "context.activate_session":
            await port.sessions.activate(
                agent_id=agent_id,
                session_id=operation["session_id"],
            )
            return {"session_id": operation["session_id"]}
        if kind == "mailbox.summary":
            return (await port.mailbox.summary(agent_id=agent_id)).model_dump(mode="json")
        if kind == "mailbox.list_inbox":
            page = await port.mailbox.list(
                agent_id=agent_id,
                inbox=True,
                limit=int(operation.get("limit", 50)),
                cursor=operation.get("cursor"),
            )
            return page.model_dump(mode="json")
        if kind == "mailbox.list_outbox":
            page = await port.mailbox.list(
                agent_id=agent_id,
                inbox=False,
                limit=int(operation.get("limit", 50)),
                cursor=operation.get("cursor"),
            )
            return page.model_dump(mode="json")
        if kind == "mailbox.get_message":
            view = await port.mailbox.get(
                agent_id=agent_id,
                message_id=operation["message_id"],
            )
            return view.model_dump(mode="json")
        if kind == "mailbox.retry":
            await port.deliveries.retry(
                agent_id=agent_id,
                delivery_id=operation["delivery_id"],
            )
            return {"delivery_id": operation["delivery_id"]}
        if kind == "mailbox.resume":
            await port.deliveries.resume(
                agent_id=agent_id,
                delivery_id=operation["delivery_id"],
            )
            return {"delivery_id": operation["delivery_id"]}
        raise ValueError(f"unsupported_operation: {kind}")

    async def list_agents(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """列出运行时 Agent：转 ``agents.list`` + ``agents.get``。"""
        if not self.is_connected:
            return []
        requested = project_id or self.project_id
        if requested != self.project_id:
            return []
        page = await self._port.agents.list()
        views: list[dict[str, Any]] = []
        for agent_id in page.items:
            try:
                view = await self._port.agents.get(agent_id=agent_id)
            except Exception:  # pragma: no cover - 并发删除
                logger.warning("list_agents: agent %s vanished", agent_id)
                continue
            views.append(view.model_dump(mode="json"))
        return views

    async def terminate_agent(self, agent_id: str, agent_name: str) -> dict[str, Any]:
        """终止 Agent：转 ``agents.delete``。"""
        del agent_name
        if not self.is_connected:
            return {"success": False, "error": f"cluster '{self._cluster_id}' is shut down"}
        await self._port.agents.delete(agent_id=agent_id)
        return {"success": True, "data": None}

    async def send_message(self, payload: Any) -> dict[str, Any]:
        """把消息定向发给指定 Agent：转 ``messages.append``。"""
        if not self.is_connected:
            return {"success": False, "error": f"cluster '{self._cluster_id}' is shut down"}
        if str(getattr(payload, "project_id", "")) != self.project_id:
            return {"success": False, "error": "cluster_project_mismatch"}
        message_id = await self._port.messages.append(
            agent_id=payload.agent_id,
            target=payload.target,
            content=payload.content,
            metadata=getattr(payload, "metadata", None),
        )
        return {"success": True, "data": {"message_id": message_id}}

    async def dispatch(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """把 Core 命令名映射成能力调用（新旧命令表并存期间的主干入口）。"""
        if not self.is_connected:
            return {
                "success": False,
                "data": None,
                "error": f"cluster '{self._cluster_id}' is shut down",
            }
        try:
            data = await self._dispatch_command(command, payload)
        except Exception as exc:  # noqa: BLE001 - 与旧路由一致：命令失败转 receipt
            logger.warning("dispatch %s failed: %s", command, exc)
            return {"success": False, "data": None, "error": str(exc)}
        return {"success": True, "data": data, "error": None}

    async def _dispatch_command(self, command: str, payload: dict[str, Any]) -> Any:
        """命令 → 能力的唯一映射表。"""
        port = self._port
        agent_id = str(payload.get("agent_id") or "")

        if command == "get_agent_info":
            view = await port.agents.get(agent_id=agent_id)
            return view.model_dump(mode="json")
        if command == "agent_compact_context":
            await port.agents.compact(agent_id=agent_id)
            return {"agent_id": agent_id}
        if command == "agent_reset":
            await port.agents.reset(agent_id=agent_id)
            return {"agent_id": agent_id}
        if command == "terminate_agent":
            await port.agents.delete(agent_id=agent_id)
            return {"agent_id": agent_id}

        if command == "session_create":
            session_id = await port.sessions.create(
                agent_id=agent_id,
                name=payload.get("name"),
                system_prompt=payload.get("system_prompt"),
            )
            return {"session_id": session_id}
        if command == "session_activate":
            await port.sessions.activate(
                agent_id=agent_id,
                session_id=str(payload.get("session_id") or ""),
            )
            return {"session_id": payload.get("session_id")}
        if command == "session_list":
            view = await port.sessions.catalog(agent_id=agent_id)
            return view.model_dump(mode="json")
        if command == "session_archive":
            await port.sessions.archive(
                agent_id=agent_id,
                session_id=str(payload.get("session_id") or ""),
            )
            return {"session_id": payload.get("session_id")}
        if command == "session_delete":
            await port.sessions.delete(
                agent_id=agent_id,
                session_id=str(payload.get("session_id") or ""),
            )
            return {"session_id": payload.get("session_id")}

        if command == "branch_create":
            branch_id = await port.branches.create(
                agent_id=agent_id,
                session_id=str(payload.get("session_id") or ""),
                name=payload.get("name"),
            )
            return {"branch_id": branch_id}
        if command == "branch_activate":
            await port.branches.activate(
                agent_id=agent_id,
                branch_id=str(payload.get("branch_id") or ""),
            )
            return {"branch_id": payload.get("branch_id")}
        if command == "branch_list":
            branches = await port.branches.list(
                agent_id=agent_id,
                session_id=str(payload.get("session_id") or ""),
            )
            return BranchListView(branches=branches).model_dump(mode="json")
        if command == "branch_archive":
            await port.branches.archive(
                agent_id=agent_id,
                branch_id=str(payload.get("branch_id") or ""),
            )
            return {"branch_id": payload.get("branch_id")}
        if command == "branch_delete":
            await port.branches.delete(
                agent_id=agent_id,
                branch_id=str(payload.get("branch_id") or ""),
            )
            return {"branch_id": payload.get("branch_id")}

        if command == "send_message":
            message_id = await port.messages.append(
                agent_id=agent_id,
                target=str(payload.get("target") or ""),
                content=str(payload.get("content") or ""),
                metadata=payload.get("metadata"),
            )
            return {"message_id": message_id}

        if command in {"register_ability", "unregister_ability"}:
            return await self._dispatch_ability(command, agent_id, payload)

        raise ValueError(f"unsupported_command: {command}")

    async def _dispatch_ability(self, command: str, agent_id: str, payload: dict[str, Any]) -> Any:
        """能力注册/注销：历史命令名，落到 grant/revoke。"""
        from ghrah.protocol.ability_tree import GrantAbilityRequest, RevokeAbilityRequest

        port = self._port
        if command == "register_ability":
            change = GrantAbilityRequest.model_validate(
                {
                    "alias": payload.get("alias"),
                    "instance_id": payload.get("instance_id") or "builtin",
                    "members": payload.get("members") or [],
                    "config": payload.get("config") or {},
                    "expected_revision": int(payload.get("expected_revision") or 0),
                }
            )
            result = await port.abilities.grant(agent_id=agent_id, change=change)
            return result.model_dump(mode="json")
        revoke = RevokeAbilityRequest.model_validate(
            {
                "alias": payload.get("alias"),
                "members": payload.get("members") or [],
                "expected_revision": int(payload.get("expected_revision") or 0),
            }
        )
        result = await port.abilities.revoke(agent_id=agent_id, change=revoke)
        return result.model_dump(mode="json")


class PortClusterTransport:
    """``CoreClusterRegistryService`` 的端口实现。"""

    def __init__(self, port: Any) -> None:
        self._port = port
        self._clusters: dict[str, _ClusterEntry] = {}
        self._locators: dict[str, str] = {}

    @property
    def port(self) -> Any:
        """底层 Core 端口。"""
        return self._port

    def _entry(self, cluster_id: str) -> _ClusterEntry:
        entry = self._clusters.get(cluster_id)
        if entry is None:
            raise KeyError(cluster_id)
        return entry

    async def ensure_cluster(
        self,
        cluster_id: str,
        *,
        project_id: str,
        project_root_locator: str,
    ) -> PortClusterHandle:
        """登记 cluster（Core 单元由端口按需启动，这里不创建实例）。"""
        existing = self._clusters.get(cluster_id)
        if existing is not None and existing.project_id != project_id:
            raise ValueError("cluster_project_conflict")
        self._clusters[cluster_id] = _ClusterEntry(
            project_id=project_id,
            project_root_locator=project_root_locator,
        )
        self._locators[project_id] = project_root_locator
        return PortClusterHandle(self, cluster_id)

    def remember_locator(self, project_id: str, locator: str) -> None:
        """登记项目根定位符（供 Core 侧库路径解析）。"""
        if project_id and locator:
            self._locators[project_id] = locator

    def locator_for(self, project_id: str) -> str | None:
        """按项目取已登记的根定位符。"""
        return self._locators.get(project_id)

    def get_handle(self, cluster_id: str) -> PortClusterHandle:
        """取已登记的 handle；不存在 raise ``KeyError``。"""
        self._entry(cluster_id)
        return PortClusterHandle(self, cluster_id)

    def get_message_manager(self, project_id: str) -> PortMailbox:
        """返回按项目的投递访问替身（不再外泄 ``MessageManager``）。"""
        return PortMailbox(self._port, project_id)

    def has_cluster(self, cluster_id: str) -> bool:
        """cluster 是否已登记。"""
        return cluster_id in self._clusters

    def cluster_ids(self) -> list[str]:
        """已登记的 cluster 标识列表。"""
        return sorted(self._clusters)

    async def shutdown_cluster(self, cluster_id: str) -> None:
        """注销 cluster；Core 单元生命周期由端口自行收口。"""
        self._clusters.pop(cluster_id, None)

    async def stop(self) -> None:
        """注销全部 cluster。"""
        self._clusters.clear()


def build_port_transport(transport_ctx: Any) -> PortClusterTransport:
    """从 Ouroboros ctx 取 ``CORE_PORT`` 并构造端口传输层。

    Args:
        transport_ctx: 提供 ``get`` 的运行时上下文

    Returns:
        端口传输层实例
    """
    return PortClusterTransport(transport_ctx.get(CORE_PORT))
