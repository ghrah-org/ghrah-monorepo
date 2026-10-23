# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Supervisor Actor：Agent 生命周期管理和消息路由入口。

SupervisorActor 是整个多 Agent 系统的中心编排者：
- 持有 AgentRegistry 和 MessageRouter
- 管理 Agent 的创建和销毁
- 提供消息路由和广播的入口
- 支持显式 Ability 注册，默认注册基础 Ability 组合
- 支持 Session 管理（创建、切换、列表、归档、删除）
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ghrah.agents.builder import AgentBuilder
from ghrah.communication.errors import (
    AgentNotFoundError,
    RegistryError,
)
from ghrah.communication.message_manager import MessageKind, MessageManager
from ghrah.communication.registry import AgentRegistry
from ghrah.communication.router import MessageRouter
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.ability_protocol import AbilityProtocol
from ghrah.core.exceptions import CommunicationTimeoutError
from ghrah.core.message import AgentMessage, MessageType
from ghrah.types.config_types import AgentConfig

if TYPE_CHECKING:
    from ghrah.core.event_publisher import EventPublisher

logger = logging.getLogger(__name__)

# [Cluster Context] 注入段截断上限（prompt 固定开销守恒）
_MAX_CONTEXT_MEMBERS = 20
_MAX_CONTEXT_DESCRIPTION = 200

# 内核物化保留参数：这些构造参数承载 hook /
# checker / runner 实质，只允许内核物化路径（materialize / unit 物化器）
# 构造，命令路径（register_ability params）传入即拒绝——params 直通
# 构造器曾是 hook 注入通道（fs/command 类 builtin 的构造函数接受
# ``hooks`` 参数）。
_KERNEL_MATERIALIZATION_PARAMS: frozenset[str] = frozenset(
    {
        "hooks",
        "permission_checker",
        "command_checker",
        "command_runner",
    }
)


class SupervisorActor:
    """监控和管理所有 Agent 的生命周期。

    作为多 Agent 系统的中心入口，负责：
    - Agent 的创建（spawn）和销毁（terminate）
    - 消息路由委托
    - 广播消息
    - 健康检查

    能力语义（fail-closed，零隐式）：
    - ``abilities`` 显式非空列表 → 照常注册；
    - ``[]`` → raise（僵尸闸门：零能力 agent 无法回复消息，
      ConversationDoneHook 永不触发，只会烧满 max_iterations）；
    - ``None`` 且已配置 ``default_abilities`` → 注入配置集
      （部署方显式声明；unknown 名 fail-closed 抛 RegistryError）；
    - ``None`` 且未配置 → raise，错误指路 manifest_ref 或
      default_abilities。绝不静默注入任何隐式默认集。

    用法:
        # 创建 Supervisor
        supervisor = SupervisorActor()

        # 注册 Agent
        config = AgentConfig(name="planner", description="任务规划")
        await supervisor.spawn_agent(config, abilities=[ConversationAbility()])

        # 发送消息
        response = await supervisor.send("planner", "设计一个方案")

        # 广播
        responses = await supervisor.broadcast("大家好")
    """

    def __init__(
        self,
        default_timeout: float = 300.0,
        cluster_id: str | None = None,
        event_publisher: EventPublisher | None = None,
        room_bridge: Any | None = None,
        default_abilities: list[str] | None = None,
        max_cluster_members: int = 20,
        manifest_store: Any | None = None,
        skill_provider: Any | None = None,
        llm_factory: Callable[[AgentConfig], Any] | None = None,
        message_manager: MessageManager | None = None,
    ) -> None:
        """初始化 Supervisor。

        Args:
            default_timeout: Agent 间通信默认超时（秒）
            cluster_id: 集群标识
            event_publisher: 事件发布器
            room_bridge: Room 桥（duck-typed）
            default_abilities: spawn 未显式 abilities（None）时注入的
                能力名列表——部署方的显式声明（可审计），非隐式兜底；
                unknown 名在 spawn 期 fail-closed 抛 RegistryError。
            max_cluster_members: 集群成员上限（spawn 时超限 raise，
                硬阻断，防止集群宽度爆炸）。
            manifest_store: ManifestStore 服务（duck-typed），供
                spawn/query_manifests 等 manifest 类工具经
                AbilityExecutionContext.services 显式接线；
                None = 未接线（对应工具显式 FAILURE 指路）。
            skill_provider: SkillAbilityProvider（duck-typed），供 skill 面消费
                （list_abilities 的 skill 清单、上下文技能目录摘要注入）；
                None = 未接线（skill 面显式说明缺席）。
            message_manager: 显式注入已打开的项目消息服务，须启用固定会话路由；
                生命周期由装配方管理，未配置时 agent 间发送拒绝。
            llm_factory: 集群级 LLM 工厂默认（``(AgentConfig) ->
                LLMProtocol``；None = AgentBuilder 内建 agentconf 默认）。
                spawn_agent 的 per-spawn kwarg 可覆盖；集群类工具
                （spawn_agent ability）继承此默认——否则经工具创建的
                agent 将回落 agentconf，破坏部署方注入的 LLM 出口。
        """
        if message_manager is not None and not message_manager.session_routing:
            raise ValueError("Supervisor messaging requires session_routing=True")
        self.message_manager = message_manager
        self._event_publisher = event_publisher
        self._cluster_id = cluster_id
        # RoomBridgeProtocol | None（send 工具用；duck-typed，避免反向依赖）
        self.room_bridge = room_bridge
        # duck-typed ManifestStoreProtocol | None（装配期显式注入）
        self.manifest_store = manifest_store
        # duck-typed SkillAbilityProvider | None（装配期显式注入）；消费方：
        # list_abilities 的 skill 面 + ContextManager 的技能目录摘要注入
        self.skill_provider = skill_provider
        # 集群级 LLM 工厂默认（spawn per-spawn kwarg 覆盖；工具路径继承）
        self._llm_factory = llm_factory
        self._registry = AgentRegistry()
        self._router = MessageRouter(self._registry, default_timeout=default_timeout)
        if message_manager is not None and room_bridge is not None:

            async def project_room(message: Any) -> None:
                rooms = message.metadata.get("room_ids") or [message.metadata.get("room_id")]
                for room_id in rooms:
                    if not room_id:
                        continue
                    result = await room_bridge.append_room_log(
                        room_id,
                        author=message.sender_id.removeprefix("human:")
                        if message.external_sender
                        else message.sender_id,
                        data={"mm_message_id": message.message_id},
                        author_type="human" if message.external_sender else "agent",
                    )
                    if not result.get("success"):
                        raise RuntimeError(result.get("error") or "Room projection failed")

            message_manager.configure_room_projector(project_room)
        self._default_ability_names = (
            list(default_abilities) if default_abilities is not None else None
        )
        self._max_cluster_members = max_cluster_members
        logger.info(
            f"SupervisorActor initialized"
            f"{' cluster_id=' + cluster_id if cluster_id else ''}"
            f" default_timeout={default_timeout}"
            f" max_cluster_members={max_cluster_members}"
        )

    async def spawn_agent(
        self,
        config: AgentConfig,
        abilities: list[AbilityProtocol] | None = None,
        *,
        persistence_factory: Callable[[AgentConfig], Any] | None = None,
        llm_factory: Callable[[AgentConfig], Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """创建并注册一个 Agent，返回 agent name。

        创建 Agent 实例并注入自身引用，使 Agent 可以通过
        Supervisor 与其他 Agent 通信。

        Ability 注册策略（fail-closed 四态，零隐式）：
        1. 显式非空列表（manifest 物化/程序化）→ 照常注册
        2. ``[]`` → raise（僵尸闸门：零能力 agent 无法协作）
        3. ``None`` 且已配置 ``default_abilities`` → 注入配置集
           （unknown 名 fail-closed 抛 RegistryError）
        4. ``None`` 且未配置 → raise，指路 manifest_ref/default_abilities

        Args:
            config: Agent 配置
            abilities: Ability 列表。None 表示走 default_abilities 配置集
                （未配置则 raise）；空列表视为装配错误 raise。
            persistence_factory: 可选的 per-agent 持久化后端工厂
                （透传 AgentBuilder.from_config；None 走 Core 内建默认）。
            llm_factory: 可选的 per-agent LLM 工厂
                （透传 AgentBuilder.from_config；None = 集群级默认
                llm_factory，仍未配置则走 Core 内建 agentconf 默认）。
                注入即全责。
            tags: 可选的 manifest AgentDef 标签透传（注册进 AgentInfo，
                供集群身份注入段展示；无 manifest 路径不传）。

        Returns:
            Agent 名称

        Raises:
            RegistryError: 同名 Agent 已注册、能力语义非法
                （空列表/未配置默认集/unknown 能力名）、或集群成员超限
        """
        if self.message_manager is not None:
            if (
                not self.message_manager.allow_persisted_agents
                and config.effective_agent_id not in self.message_manager.agent_ids
            ):
                raise RegistryError("Agent ID is not declared in MessageManager")
            if persistence_factory is None:

                def persistence_factory(config: AgentConfig) -> SqliteBackend:
                    return SqliteBackend(self.message_manager.db_path)

        if self._registry.exists(config.name):
            raise RegistryError(f"Agent already registered: {config.name}")

        if self._max_cluster_members > 0 and len(self._registry) >= self._max_cluster_members:
            raise RegistryError(
                f"Cluster member limit reached: {len(self._registry)}/"
                f"{self._max_cluster_members} — terminate an agent or raise "
                f"max_cluster_members before spawning '{config.name}'"
            )

        supervisor_handle = self

        # 能力语义 fail-closed 判定（四态收敛，单一 chokepoint）
        if abilities is not None and len(abilities) == 0:
            raise RegistryError(
                f"Refusing to spawn agent '{config.name}' with zero abilities — "
                f"an agent with no ConversationAbility can never reply and will "
                f"only burn max_iterations. Pass a non-empty abilities list, "
                f"or omit abilities to use configured default_abilities, "
                f"or spawn via manifest_ref."
            )
        if abilities is None:
            if self._default_ability_names is None:
                raise RegistryError(
                    f"Agent '{config.name}' spawned without explicit abilities and "
                    f"no default_abilities configured — pass a non-empty abilities "
                    f"list, configure SupervisorActor(default_abilities=[...]), "
                    f"or spawn via manifest_ref. Refusing implicit defaults."
                )
            from ghrah.abilities.registry import AbilityRegistry

            abilities = []
            for ability_type in self._default_ability_names:
                try:
                    abilities.append(AbilityRegistry.create(ability_type))
                except KeyError as exc:
                    raise RegistryError(
                        f"default_abilities contains unknown ability type "
                        f"'{ability_type}' — assembly error must fail at "
                        f"spawn time, not silently skip (agent: {config.name})"
                    ) from exc
            logger.info(
                "Supervisor injected %d configured default abilities for agent: %s — abilities=%s",
                len(abilities),
                config.name,
                self._default_ability_names,
            )
        else:
            logger.info(
                "Supervisor registered %d user-provided abilities for agent: %s — abilities=%s",
                len(abilities),
                config.name,
                [a.name for a in abilities],
            )

        # 通过 AgentBuilder 创建 ActorAgent（llm_factory per-spawn 覆盖
        # 集群级默认；两者皆 None 走 AgentBuilder 内建 agentconf）
        actor_handle = AgentBuilder.from_config(
            config=config,
            abilities=abilities,
            supervisor=supervisor_handle,
            event_publisher=self._event_publisher,
            persistence_factory=persistence_factory,
            llm_factory=llm_factory if llm_factory is not None else self._llm_factory,
        )

        # 恢复必须发生在 registry 可见之前。旧实现先注册再无条件 persist，
        # 会把同名 agent 的历史链删除并以新根节点覆盖。现在先 connect，
        # 有快照则 restore；仅无快照时初始化。任一步失败均 fail closed。
        try:
            if self.message_manager is not None:
                backend = actor_handle._context_manager.persistence
                if (
                    not isinstance(backend, SqliteBackend)
                    or backend.db_path.resolve() != self.message_manager.db_path
                ):
                    raise ValueError("Persistent messaging requires same-file SqliteBackend")
            recovery_mode = await self._prepare_agent_persistence(
                actor_handle, config.effective_agent_id
            )
        except Exception as exc:
            await self._close_agent_persistence(actor_handle, config.name)
            raise RegistryError(
                f"Agent persistence recovery failed for '{config.name}': {exc}"
            ) from exc

        registered = False
        try:
            self._registry.register(
                name=config.name,
                config=config,
                actor_handle=actor_handle,
                tags=tags,
            )
            registered = True
            if self.message_manager is not None:
                await self.message_manager.refresh_agent_ids()
                await self.message_manager.bind_atomic(actor_handle)
        except Exception:
            if registered:
                self._registry.unregister(config.name)
            await self._close_agent_persistence(actor_handle, config.name)
            raise

        # 供 CoreUnit 回执/事件和恢复报告读取；不参与业务身份。
        setattr(actor_handle, "_recovery_mode", recovery_mode)

        logger.info(f"Supervisor spawned agent: {config.name}")
        return config.name

    async def _prepare_agent_persistence(self, actor_handle: Any, name: str) -> str:
        """在 agent 注册前恢复或初始化持久化上下文。

        - duck-typed connect：仅 SqliteBackend 等带连接语义的后端需要
          （InMemory/JsonFile 无此方法则跳过）；
        - 已有 checkpoint：restore-first，恢复失败直接阻断 spawn；
        - 无 checkpoint：首次 persist，初始化 Session/Root/Branch。

        Returns:
            ``memory`` / ``restored`` / ``initialized``。
        """
        cm = getattr(actor_handle, "_context_manager", None)
        backend = getattr(cm, "persistence", None) if cm is not None else None
        if cm is None or backend is None:
            return "memory"
        if hasattr(backend, "connect"):
            await backend.connect()
        checkpoint = await backend.load_checkpoint(name)
        if checkpoint is not None:
            await cm.restore(name)
            logger.info("Supervisor restored persisted context for agent: %s", name)
            return "restored"
        await cm.persist()
        logger.info("Supervisor initialized persisted context for agent: %s", name)
        return "initialized"

    async def _close_agent_persistence(self, actor_handle: Any, name: str) -> None:
        """仅关闭 backend，不写入；用于 spawn/restore 失败清理半成品。"""
        cm = getattr(actor_handle, "_context_manager", None)
        backend = getattr(cm, "persistence", None) if cm is not None else None
        if backend is not None and hasattr(backend, "close"):
            try:
                await backend.close()
            except Exception:
                logger.warning("Supervisor: close failed agent '%s' backend 失败", name)

    def recovery_mode(self, name: str) -> str:
        """返回当前运行实例的恢复方式。"""
        info = self._registry.get_info(name)
        return str(getattr(info.actor_handle, "_recovery_mode", "memory"))

    def incarnation_id(self, name: str) -> str:
        """返回本次成功注册生成的运行实例 ID。"""
        return self._registry.get_info(name).incarnation_id

    async def _release_agent_persistence(self, actor_handle: Any, name: str) -> None:
        """terminate 前写最终 checkpoint、flush 并关闭写侧连接。"""
        cm = getattr(actor_handle, "_context_manager", None)
        backend = getattr(cm, "persistence", None) if cm is not None else None
        if cm is not None and backend is not None:
            try:
                # 完整 persist 同步 chain_meta/state/messages/sessions；仅等待
                # auto-persist node 无法形成可恢复 checkpoint。
                await cm.persist()
            except Exception:
                logger.exception("Supervisor: final checkpoint agent '%s' 失败", name)
        if cm is not None:
            try:
                await cm.wait_for_persist()
            except Exception:
                logger.warning(f"Supervisor: flush agent '{name}' persist tasks 失败")
        if backend is not None and hasattr(backend, "close"):
            try:
                await backend.close()
            except Exception:
                logger.warning(f"Supervisor: close agent '{name}' persistence 失败")

    async def terminate_agent(self, name: str) -> None:
        """注销并终止 Agent。

        从注册中心移除 Agent。

        Args:
            name: Agent 名称

        Raises:
            AgentNotFoundError: 如果 Agent 未注册
        """
        info = self._registry.get_info(name)
        if self.message_manager is not None:
            await self.message_manager.unbind(info.config.effective_agent_id)
        await info.actor_handle.stop_receiving()
        await self._release_agent_persistence(info.actor_handle, name)

        self._registry.unregister(name)

        logger.info(f"Supervisor terminated agent: {name}")

    async def list_agents(self) -> list[dict[str, Any]]:
        """列出所有活跃 Agent 信息。

        Returns:
            Agent 信息字典列表
        """
        agents = self._registry.list_agents()
        return [info.to_dict() for info in agents]

    def get_cluster_context(self) -> dict[str, Any]:
        """返回集群上下文（供 [Cluster Context] 注入段渲染）。

        成员/描述截断上限：注入段是 prompt 上下文的固定开销，
        超大集群取前 N 个成员即失去可读性，截断是诚实行为
        （完整清单 agent 可经 query_agents 获取）。

        Returns:
            ``{"cluster_id": str, "members": [{"name", "description", "tags"}]}``
        """
        members = []
        for info in self._registry.list_agents()[:_MAX_CONTEXT_MEMBERS]:
            members.append(
                {
                    "name": info.name,
                    "description": (info.config.description or "")[:_MAX_CONTEXT_DESCRIPTION],
                    "tags": list(info.tags),
                }
            )
        return {
            "cluster_id": self._cluster_id or "",
            "members": members,
        }

    async def prepare_message(
        self,
        *,
        targets: Collection[str],
        content: str,
        sender: str,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """在 agent 当前持久迭代中准备请求，提交成功后才允许投递。"""
        manager = self.message_manager
        if manager is None:
            raise RuntimeError("MessageManager is required for agent communication")
        source = self._registry.get_info(sender).config.effective_agent_id
        if isinstance(targets, str):
            raise ValueError("targets must be a collection")
        recipients = []
        for target in targets:
            recipients.append(
                self._registry.get_info(target).config.effective_agent_id
                if self._registry.exists(target)
                else target
            )
        draft = await manager.prepare_send(
            recipient_ids=recipients,
            content=content,
            kind=MessageKind.REQUEST,
            idempotency_key=idempotency_key,
            expected_sender_id=source,
            metadata=metadata,
        )
        return {
            "message_id": draft.message_id,
            "recipients": list(draft.recipient_ids),
            "status": "prepared",
            "mode": "persistent",
            "recipient_names": {
                self._registry.get_info(target).config.effective_agent_id: target
                for target in targets
                if self._registry.exists(target)
            },
        }

    def validate_router_sender(self, sender: str) -> None:
        """拒绝 agent 使用 Router 第二发送路径；外部用户输入仍为独立入口。"""
        known = (
            self._registry.exists(sender)
            or any(
                info.config.effective_agent_id == sender for info in self._registry.list_agents()
            )
            or (self.message_manager is not None and sender in self.message_manager.agent_ids)
        )
        if known:
            raise RuntimeError(
                "Agent Router communication was removed; use prepare_message inside "
                "an atomic delivery or MessageManager.send from an external caller"
            )

    async def route_message(
        self, message: AgentMessage, timeout: float | None = None
    ) -> AgentMessage:
        """路由消息到目标 Agent。

        超时优先级：
        1. 显式传入的 timeout 参数
        2. 目标 Agent 配置的 communication_timeout
        3. Supervisor 的 default_timeout

        Args:
            message: 要路由的消息
            timeout: 超时时间（秒），None 使用目标 Agent 配置或默认值，-1 表示无限等待

        Returns:
            目标 Agent 的回复
        """
        if self.message_manager is None:
            self.validate_router_sender(message.sender)
            effective_timeout = self._resolve_timeout(message.recipient, timeout)
            return await self._router.route(message, timeout=effective_timeout)
        if message.recipient == "*":
            responses = await self.broadcast(
                message.content,
                sender=message.sender,
                msg_type=message.type,
                metadata=message.metadata,
            )
            if not responses:
                return message
            return AgentMessage(
                sender=responses[0]["responder"],
                recipient=message.sender,
                content=responses[0]["content"],
                type=MessageType.RESULT,
                reply_to=message.id,
            )
        request = await self._accept_input(message)
        effective_timeout = self._resolve_timeout(message.recipient, timeout)
        try:
            replies = await self.message_manager.wait_replies(
                request.message_id, timeout=effective_timeout
            )
        except TimeoutError as exc:
            raise CommunicationTimeoutError(
                message.sender, message.recipient, effective_timeout
            ) from exc
        result = replies[0]
        return AgentMessage.create_reply(
            message, result.content, content_blocks=result.metadata.get("content_blocks")
        )

    async def _accept_input(self, message: AgentMessage) -> Any:
        manager = self.message_manager
        target = message.recipient
        target_id = (
            self._registry.get_info(target).config.effective_agent_id
            if self._registry.exists(target)
            else target
        )
        source = next(
            (
                info
                for info in self._registry.list_agents()
                if message.sender in (info.name, info.config.effective_agent_id)
            ),
            None,
        )
        meta = {
            **message.metadata,
            "message_type": message.type.value,
            "content_blocks": message.content_blocks,
            "return_reply": True,
        }
        key = str(meta.pop("request_id", "")) or message.id
        if source or message.sender in manager.agent_ids:
            return await manager.send(
                sender_id=source.config.effective_agent_id if source else message.sender,
                recipient_ids=(target_id,),
                content=message.content,
                kind=MessageKind.REQUEST,
                idempotency_key=f"ingress:{key}",
                metadata=meta,
            )
        return await manager.send_external(
            sender=message.sender,
            recipient_ids=(target_id,),
            content=message.content,
            idempotency_key=f"ingress:{key}",
            metadata=meta,
        )

    def _resolve_timeout(self, target: str, timeout: float | None) -> float:
        """解析有效的通信超时时间。

        超时优先级：
        1. 显式传入的 timeout 参数（非 None 时直接使用）
        2. 目标 Agent 配置的 communication_timeout
        3. Router 的 default_timeout

        Args:
            target: 目标 Agent 名称
            timeout: 显式传入的超时参数，None 表示使用 Agent 配置或默认值

        Returns:
            有效的超时时间（秒），-1 表示无限等待
        """
        if timeout is not None:
            return timeout
        try:
            info = self._registry.get_info(target)
            return info.config.communication_timeout
        except AgentNotFoundError:
            return self._router._default_timeout

    async def send(
        self,
        target: str,
        content: str,
        sender: str = "user",
        msg_type: MessageType = MessageType.CHAT,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """发送普通输入或外部 agent 请求，持久受理后返回对应回复。

        超时优先级：
        1. 显式传入的 timeout 参数
        2. 目标 Agent 配置的 communication_timeout
        3. Router 的 default_timeout

        Args:
            target: 目标 Agent 名称
            content: 消息内容
            sender: 发送者标识
            msg_type: 消息类型
            timeout: 超时时间（秒），None 使用默认值，-1 表示无限等待
            metadata: 扩展元数据（如 Room 投递的 room_id；随消息入链
                messages_delta，供回复归属推导消费）

        Returns:
            回复文本内容
        """
        manager = self.message_manager
        if manager is None:
            self.validate_router_sender(sender)
            response = await self._router.send_and_wait(
                target=target,
                content=content,
                sender=sender,
                msg_type=msg_type,
                timeout=self._resolve_timeout(target, timeout),
                metadata=metadata,
            )
            return response.content
        message = AgentMessage(
            sender=sender,
            recipient=target,
            content=content,
            type=msg_type,
            metadata=dict(metadata or {}),
        )
        return (await self.route_message(message, timeout=timeout)).content

    async def broadcast(
        self,
        content: str,
        sender: str = "user",
        msg_type: MessageType = MessageType.BROADCAST,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """将普通外部输入广播到所有 Agent；agent 内部广播使用持久准备入口。

        Args:
            content: 消息内容
            sender: 发送者标识
            msg_type: 消息类型

        Returns:
            所有 Agent 回复列表，每项形如
            ``{"responder": <agent_name>, "content": <回复内容>}``。
            ``responder`` 即广播实际到达者（router.broadcast 返回的
            AgentMessage 经 create_reply 令 sender=原 recipient）。
        """
        if self.message_manager is None:
            self.validate_router_sender(sender)
            message = AgentMessage(sender=sender, recipient="*", content=content, type=msg_type)
            responses = await self._router.broadcast(message)
            return [{"responder": r.sender, "content": r.content} for r in responses]
        infos = [
            info
            for info in self._registry.list_agents()
            if sender not in (info.name, info.config.effective_agent_id)
        ]
        if not infos:
            return []
        manager = self.message_manager
        recipients = tuple(info.config.effective_agent_id for info in infos)
        source = next(
            (
                info
                for info in self._registry.list_agents()
                if sender in (info.name, info.config.effective_agent_id)
            ),
            None,
        )
        meta = {**(metadata or {}), "message_type": msg_type.value, "return_reply": True}
        key = f"broadcast:{uuid4().hex}"
        if source or sender in manager.agent_ids:
            request = await manager.send(
                sender_id=source.config.effective_agent_id if source else sender,
                recipient_ids=recipients,
                content=content,
                kind=MessageKind.REQUEST,
                idempotency_key=key,
                metadata=meta,
            )
        else:
            request = await manager.send_external(
                sender=sender,
                recipient_ids=recipients,
                content=content,
                idempotency_key=key,
                metadata=meta,
            )
        replies = await manager.wait_replies(request.message_id, tolerate_errors=True)
        by_sender = {reply.sender_id: reply for reply in replies}
        return [
            {"responder": info.name, "content": by_sender[info.config.effective_agent_id].content}
            for info in infos
            if info.config.effective_agent_id in by_sender
        ]

    async def delegate(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        timeout: float | None = None,
    ) -> str:
        """通过持久消息委托并返回回复，保留原同步调用接口。

        超时优先级：
        1. 显式传入的 timeout 参数
        2. 目标 Agent 配置的 communication_timeout
        3. Supervisor 的 default_timeout

        Args:
            from_agent: 委托方 Agent 名称
            to_agent: 被委托方 Agent 名称
            content: 委托内容
            timeout: 超时时间（秒），None 使用目标 Agent 配置或默认值，-1 表示无限等待

        Returns:
            被委托方的回复内容

        Raises:
            AgentNotFoundError: 任一 Agent 未注册
        """
        if not self._registry.exists(from_agent):
            raise AgentNotFoundError(from_agent)
        if not self._registry.exists(to_agent):
            raise AgentNotFoundError(to_agent)

        return await self.send(
            to_agent, content, sender=from_agent, msg_type=MessageType.COMMAND, timeout=timeout
        )

    async def register_ability_for_agent(
        self,
        agent_name: str,
        ability_type: str,
        ability_params: dict[str, Any] | None = None,
    ) -> str:
        """为指定 Agent 注册 Ability（通过 AbilityRegistry 动态创建）。

        通过类型名从 AbilityRegistry 查找 Ability 类并实例化，
        然后注册到目标 Agent。这使得外部调用方不再需要直接实例化 Ability，
        而是通过 Supervisor 在 Core 侧完成。

        构造参数守卫：``params`` 直通构造器是
        hook 注入通道（fs/command 类 builtin 的构造函数本就接受
        ``hooks``/``permission_checker`` 等参数），内核物化参数只允许
        内核物化路径（materialize/unit 物化器）构造——命令路径传入即拒绝。

        Args:
            agent_name: Agent 名称
            ability_type: Ability 类型名（在 AbilityRegistry 中注册的）
            ability_params: Ability 构造参数

        Returns:
            注册的 Ability 名称

        Raises:
            AgentNotFoundError: Agent 未注册
            KeyError: Ability 类型未注册
            ValueError: params 含内核物化保留键（hooks 等）
        """
        from ghrah.abilities.registry import AbilityRegistry

        info = self._registry.get_info(agent_name)
        params = ability_params or {}

        forbidden = _KERNEL_MATERIALIZATION_PARAMS & set(params)
        if forbidden:
            raise ValueError(
                f"Ability params for '{ability_type}' cannot include kernel "
                f"materialization keys {sorted(forbidden)}; only kernel "
                f"materialization paths may construct these"
            )

        # 通过 AbilityRegistry 创建实例
        ability = AbilityRegistry.create(ability_type, **params)

        # 注册到 Agent
        result = info.actor_handle.register_ability(ability)
        logger.info(
            f"Supervisor registered ability '{ability_type}' for agent '{agent_name}' via registry"
        )
        return result

    async def register_ability_instance_for_agent(
        self,
        agent_name: str,
        ability: Any,
    ) -> str:
        """为指定 Agent 注册已实例化的 Ability。

        command_proxy 等经 Core 侧物化器（descriptor 校验 + 强制闸门 +
        HITL 包装）构造的能力实例走本通道——不经 AbilityRegistry 的
        params 工厂（物化器对构造面负责，注册面只负责落到 Agent）。

        Args:
            agent_name: Agent 名称
            ability: 已构造的 Ability 实例

        Returns:
            注册的 Ability 名称

        Raises:
            AgentNotFoundError: Agent 未注册
        """
        info = self._registry.get_info(agent_name)
        result = info.actor_handle.register_ability(ability)
        logger.info(
            f"Supervisor registered ability '{getattr(ability, 'name', '<unnamed>')}' "
            f"for agent '{agent_name}' via instance channel"
        )
        return result

    async def get_agent_handle(self, agent_name: str) -> Any:
        """获取 Agent 的 actor handle。

        供挂载宿主（如 CoreUnit）使用，用于直接调用 Agent 方法
        （如 receive_hitl_response）。

        Args:
            agent_name: Agent 名称

        Returns:
            Agent 实例引用

        Raises:
            AgentNotFoundError: Agent 未注册
        """
        info = self._registry.get_info(agent_name)
        return info.actor_handle

    async def create_session(
        self,
        agent_name: str,
        name: str | None = None,
        origin_session_id: str | None = None,
        origin_node_id: str | None = None,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为指定 Agent 创建新 session。

        Args:
            agent_name: Agent 名称
            name: 可选的展示名（缺省按 Agent 内序号分配 Session N）
            origin_session_id: 可选的来源 Session ID
            origin_node_id: 可选的来源节点 ID
            system_prompt: 可选的系统提示词
            metadata: 可选的元数据

        Returns:
            新 session 信息字典

        Raises:
            AgentNotFoundError: Agent 未注册
        """
        handle = await self.get_agent_handle(agent_name)
        session = await handle.create_session(
            name=name,
            origin_session_id=origin_session_id,
            origin_node_id=origin_node_id,
            system_prompt=system_prompt,
            metadata=metadata,
        )
        return handle._session_info(session)

    async def activate_session(self, agent_name: str, session_id: str) -> dict[str, Any]:
        """激活指定 Agent 的独立 Root Session。

        Args:
            agent_name: Agent 名称
            session_id: 要切换到的 session ID

        Returns:
            切换后的 session 信息字典

        Raises:
            AgentNotFoundError: Agent 未注册
        """
        handle = await self.get_agent_handle(agent_name)
        return await handle.activate_session(session_id)

    async def reset_agent(self, agent_name: str) -> dict[str, Any]:
        """重置指定 Agent：新起默认 Session/main Branch 并激活。

        就地重置（保留旧 Session、持久化后端与已恢复历史）；新 Session
        实体经既有 session_created/session_activated 事件广播。

        Args:
            agent_name: Agent 名称

        Returns:
            新激活 session 的信息字典（session_id/root_node_id/active_branch_id 等）

        Raises:
            AgentNotFoundError: Agent 未注册
            RuntimeError: Agent 驱动循环进行中（拒绝并发 reset）
        """
        handle = await self.get_agent_handle(agent_name)
        return await handle.reset()

    async def list_sessions(self, agent_name: str) -> dict[str, Any]:
        """列出指定 Agent 的所有 session。

        Args:
            agent_name: Agent 名称

        Returns:
            含 active_session_id 与 sessions 列表的结果字典

        Raises:
            AgentNotFoundError: Agent 未注册
        """
        handle = await self.get_agent_handle(agent_name)
        return handle.list_sessions_result()

    async def archive_session(self, agent_name: str, session_id: str) -> None:
        """归档指定 Agent 的 session。

        Args:
            agent_name: Agent 名称
            session_id: 要归档的 session ID

        Raises:
            AgentNotFoundError: Agent 未注册
        """
        handle = await self.get_agent_handle(agent_name)
        await handle.archive_session(session_id)

    async def delete_session(self, agent_name: str, session_id: str) -> None:
        """删除指定 Agent 的 session。不能删除当前活跃的 session。

        Args:
            agent_name: Agent 名称
            session_id: 要删除的 session ID

        Raises:
            AgentNotFoundError: Agent 未注册
        """
        handle = await self.get_agent_handle(agent_name)
        await handle.delete_session(session_id)

    async def create_branch(
        self,
        agent_name: str,
        session_id: str,
        name: str,
        from_node_id: str | None = None,
        parent_branch_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为 Session 创建 Branch，不隐式激活。"""
        handle = await self.get_agent_handle(agent_name)
        return await handle.create_branch(
            session_id=session_id,
            name=name,
            from_node_id=from_node_id,
            parent_branch_id=parent_branch_id,
            metadata=metadata,
        )

    async def activate_branch(
        self, agent_name: str, session_id: str, branch_id: str
    ) -> dict[str, Any]:
        """激活当前 Session 内的 Branch。"""
        handle = await self.get_agent_handle(agent_name)
        return await handle.activate_branch(session_id, branch_id)

    async def list_branches(self, agent_name: str, session_id: str) -> dict[str, Any]:
        """列出 Session 内 Branch。

        Returns:
            含 session_id、active_branch_id 与 branches 列表的结果字典
        """
        handle = await self.get_agent_handle(agent_name)
        return handle.list_branches_result(session_id)

    async def archive_branch(self, agent_name: str, session_id: str, branch_id: str) -> None:
        """归档非运行 Branch。"""
        handle = await self.get_agent_handle(agent_name)
        await handle.archive_branch(session_id, branch_id)

    async def delete_branch(self, agent_name: str, session_id: str, branch_id: str) -> None:
        """删除非运行 Branch。"""
        handle = await self.get_agent_handle(agent_name)
        await handle.delete_branch(session_id, branch_id)

    async def health_check(self) -> dict[str, bool]:
        """检查所有 Agent 的健康状态。

        通过调用每个 Agent 的 get_state 方法验证其可用性。

        Returns:
            {agent_name: is_healthy} 映射
        """
        results: dict[str, bool] = {}
        agents = self._registry.list_agents()

        for info in agents:
            try:
                # 尝试调用 Agent 的 get_state 方法验证其可用性
                info.actor_handle.get_state()
                results[info.name] = True
            except Exception as e:
                logger.warning(f"Health check failed for {info.name}: {e}")
                results[info.name] = False

        return results

    async def shutdown(self) -> None:
        """终止所有已注册 Agent（供 CoreUnit.stop 等挂载宿主调用）。

        语义对齐 server/router.py 的 ``_handle_shutdown_cluster``：
        遍历 registry 逐个 terminate，单个失败仅记日志不中断整体。
        幂等，可重复调用安全（空 registry 时为无操作）。
        """
        agents = await self.list_agents()
        for agent_info in agents:
            name = agent_info.get("name", "")
            if name:
                try:
                    await self.terminate_agent(name)
                except Exception:
                    logger.warning(f"Failed to terminate agent '{name}' during shutdown")
        logger.info(f"Supervisor shutdown ({len(agents)} agents terminated)")
