"""长期 Agent 实体：固定身份，组合受限端口，由宿主适配现有权威。

实体不持有 ActorAgent 或数据库连接。每次操作按稳定身份解析当前规格和运行实例，
因此同一个句柄可跨越停止、重启；Core 不依赖 Subject。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ghrah.protocol.payloads.entity import AgentEntityPayload, AgentIdentity, AgentSnapshot


class AgentEntityBackend(Protocol):
    """由宿主实现实体权限、持久规格和运行端口的组合。"""

    async def execute(self, request: AgentEntityPayload) -> dict[str, Any]:
        """执行一个已绑定身份、已通过协议验证的操作。"""
        ...


@dataclass(frozen=True)
class _EntityPort:
    identity: AgentIdentity
    _backend: AgentEntityBackend

    async def _call(self, operation: str, **params: Any) -> dict[str, Any]:
        request = AgentEntityPayload.model_validate(
            {**self.identity.model_dump(), "operation": {"type": operation, **params}}
        )
        return await self._backend.execute(request)


class AgentContext(_EntityPort):
    """当前 Agent 的会话集合与历史入口。"""

    async def list_sessions(self) -> dict[str, Any]:
        """查询会话、分支和当前会话；离线时读取持久历史。"""
        return await self._call("context.list_sessions")

    async def get_history(
        self, session_id: str, branch_id: str, *, limit: int = 100
    ) -> dict[str, Any]:
        """读取明确会话/分支的已提交历史。"""
        return await self._call(
            "context.get_history", session_id=session_id, branch_id=branch_id, limit=limit
        )

    async def create_session(
        self, *, name: str | None = None, system_prompt: str | None = None
    ) -> dict[str, Any]:
        """在在线空闲实例上创建会话。"""
        return await self._call("context.create_session", name=name, system_prompt=system_prompt)

    async def activate_session(self, session_id: str) -> dict[str, Any]:
        """激活属于此实体的会话。"""
        return await self._call("context.activate_session", session_id=session_id)


class AgentMailbox(_EntityPort):
    """按当前实体身份限定的项目 MessageManager 入口。"""

    async def list_inbox(self, *, limit: int = 50, before_seq: int | None = None) -> dict[str, Any]:
        """按接收者身份列出消息及独立投递状态。"""
        return await self._call("mailbox.list_inbox", limit=limit, before_seq=before_seq)

    async def list_outbox(
        self, *, limit: int = 50, before_seq: int | None = None
    ) -> dict[str, Any]:
        """按发送者身份列出消息及各接收者的投递状态。"""
        return await self._call("mailbox.list_outbox", limit=limit, before_seq=before_seq)

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """读取此实体参与的消息，拒绝其他实体的私有消息。"""
        return await self._call("mailbox.get_message", message_id=message_id)

    async def retry(self, delivery_id: str) -> dict[str, Any]:
        """显式重试本实体的一条投递，沿用 MM 的重试规则。"""
        return await self._call("mailbox.retry", delivery_id=delivery_id)

    async def resume(self, delivery_id: str) -> dict[str, Any]:
        """显式恢复本实体的一条中断投递。"""
        return await self._call("mailbox.resume", delivery_id=delivery_id)


class AgentAbilities(_EntityPort):
    """当前 Agent 的期望能力与运行能力入口。"""

    async def list(self) -> dict[str, Any]:
        """读取配置来源与当前实际装载能力。"""
        return await self._call("abilities.list")

    async def configure(
        self, ability_types: list[str], *, expected_revision: int
    ) -> dict[str, Any]:
        """为停止的实体保存显式能力列表。"""
        return await self._call(
            "abilities.configure", ability_types=ability_types, expected_revision=expected_revision
        )


class AgentEntity(_EntityPort):
    """以稳定身份管理 Context、Mailbox、Ability 和可启停运行实例的主体。"""

    @property
    def context(self) -> AgentContext:
        """返回绑定同一身份的上下文端口。"""
        return AgentContext(self.identity, self._backend)

    @property
    def mailbox(self) -> AgentMailbox:
        """返回绑定同一身份的消息端口。"""
        return AgentMailbox(self.identity, self._backend)

    @property
    def abilities(self) -> AgentAbilities:
        """返回绑定同一身份的能力端口。"""
        return AgentAbilities(self.identity, self._backend)

    async def snapshot(self) -> AgentSnapshot:
        """读取实体规格及各子系统的当前观测，不缓存第二份事实。"""
        return AgentSnapshot.model_validate(await self._call("snapshot"))

    async def start(self, *, expected_revision: int | None = None) -> dict[str, Any]:
        """显式启动运行实例，复用实体身份和历史。"""
        return await self._call("start", expected_revision=expected_revision)

    async def stop(
        self, *, expected_revision: int | None = None, expected_incarnation_id: str | None = None
    ) -> dict[str, Any]:
        """停止运行实例并持久声明停止意图，保留实体。"""
        return await self._call(
            "stop",
            expected_revision=expected_revision,
            expected_incarnation_id=expected_incarnation_id,
        )
