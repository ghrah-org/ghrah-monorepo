# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Room Filter built-in Subject unit（能力结果 → RoomLog 显式 Filter）。

白名单配置驱动：**零隐式行为**——白名单（配置驱动）之外 / 无 room 上下文的
能力结果一律不落 Room。

数据流（主源 ``core:action_chain_updated``，驱动循环每迭代每节点发布
全量序列化 node）：

1. 投递（RoomUnit deliver）带 ``metadata.room_id`` → CoreUnit
   ``send_message`` → ``AgentMessage.metadata`` → receive 侧合入用户
   ChatMessage → ``commit_iteration`` 落入链节点 ``messages_delta``；
2. 本 unit 订阅 ``core:action_chain_updated``，对 node 内命中白名单且
   ``outcome == "success"`` 的能力结果：
   - ``send`` 跳过（send ability 自带 RoomLog 落账，防双记）；
   - 其余白名单能力：从 ``messages_delta`` 或节点 ``delivery_context``
     显式解析 room 上下文
     （缺失不落——宁缺毋滥），经 ``room_send``（author_type=agent）
     走完整校验 + seq 分配 + 落账 + 广播（该路径不投递，作者即发送者）。

去重：per-node id LRU（同一节点事件重放幂等；回滚重提交产生新节点 id）。
"""

from __future__ import annotations

import logging
from typing import Any

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.service_keys import ROOM_MANAGER
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta

__all__ = ["RoomFilterUnit"]

logger = logging.getLogger(__name__)

_SEEN_MAX = 4096


class RoomFilterUnit(SubjectUnit):
    """Subscribes ``core:action_chain_updated`` → whitelist filter → room_send."""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._room_manager: Any | None = None
        # dict 充当 LRU set（插入序 = 时间序，超限淘汰最老）
        self._seen_nodes: dict[str, None] = {}
        self._meta = UnitMeta(
            name="room_filter",
            requires=frozenset({ROOM_MANAGER}),
            provides=frozenset(),
            routes=RouteSpec(commands=frozenset()),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        self._room_manager = ctx.get(ROOM_MANAGER.name)
        if self._ctx is not None:
            self._ctx.on("core:action_chain_updated", self._on_chain_updated)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "data": None,
            "error": "room_filter has no commands",
        }

    # ─── filter 本体 ───

    async def _on_chain_updated(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        node = payload.get("node")
        if not isinstance(node, dict) or not node.get("id"):
            return
        agent_name = str(payload.get("agent_name") or node.get("agent_name") or "")
        if not agent_name:
            return
        agent_identity = self._resolve_agent_identity(node) or agent_name

        if (node.get("metadata") or {}).get("delivery_context", {}).get("message_manager"):
            # 持久输入的 Room 回复由 MM outbox 投影，禁止事件驱动第二写路径。
            return
        node_id = str(node["id"])
        if node_id in self._seen_nodes:
            return
        self._seen_nodes[node_id] = None
        if len(self._seen_nodes) > _SEEN_MAX:
            oldest = next(iter(self._seen_nodes))
            del self._seen_nodes[oldest]

        room_id = self._resolve_room_id(node)
        if room_id is None:
            # 无 room 上下文 → 不落（显式 Filter 契约）
            return

        action_results = node.get("action_results") or []
        if not isinstance(action_results, list):
            return
        for item in action_results:
            if not isinstance(item, dict):
                continue
            ability_name = str(item.get("ability_name") or "")
            action_result = item.get("action_result") or {}
            if not isinstance(action_result, dict):
                continue
            if action_result.get("outcome") != "success":
                continue
            if ability_name == "send":
                # send ability 自带 RoomLog 落账，跳过防双记
                continue
            if ability_name not in self._config.room_filter.abilities:
                continue
            content = self._extract_content(action_result)
            if not content:
                continue
            await self._append_to_room(room_id, agent_identity, ability_name, node_id, content)

    @staticmethod
    def _resolve_room_id(node: dict[str, Any]) -> str | None:
        """从 node.messages_delta 显式解析 room 上下文。

        投递的用户消息携带 ``metadata.room_id``（receive 侧合入）。一次
        receive 若经历多轮 tool call，后续节点没有 user delta，因此 Core 还会
        把同一归属写入 ``node.metadata.delivery_context``。
        两处均缺失 → None（不落账）。
        """
        delta = node.get("messages_delta")
        if not isinstance(delta, list):
            return None
        for message in delta:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            metadata = message.get("metadata")
            if not isinstance(metadata, dict):
                continue
            room_id = metadata.get("room_id")
            if isinstance(room_id, str) and room_id:
                return room_id
        node_metadata = node.get("metadata")
        if not isinstance(node_metadata, dict):
            return None
        delivery_context = node_metadata.get("delivery_context")
        if not isinstance(delivery_context, dict):
            return None
        room_id = delivery_context.get("room_id")
        if isinstance(room_id, str) and room_id:
            return room_id
        return None

    @staticmethod
    def _resolve_agent_identity(node: dict[str, Any]) -> str | None:
        """读取本次投递使用的 project-scoped 稳定 Agent ID。

        ``RoomFilterUnit`` 直接调用 ``RoomManager``，不会经过 ``RoomUnit`` 的
        显示名归一化边界。因此新 Room 已用稳定 ID 保存成员时，回写 author
        必须沿用 delivery_context.agent_id；旧节点没有该字段时由调用方回退
        到 agent_name，保持历史兼容。
        """
        node_metadata = node.get("metadata")
        if not isinstance(node_metadata, dict):
            return None
        delivery_context = node_metadata.get("delivery_context")
        if not isinstance(delivery_context, dict):
            return None
        agent_id = delivery_context.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
        return None

    @staticmethod
    def _extract_content(action_result: dict[str, Any]) -> str:
        data = action_result.get("data")
        if not isinstance(data, dict):
            return ""
        for key in ("response", "content", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    async def _append_to_room(
        self,
        room_id: str,
        agent_name: str,
        ability_name: str,
        node_id: str,
        content: str,
    ) -> None:
        if self._room_manager is None:
            return
        result = await self._room_manager.handle_command(
            "room_send",
            {
                "room_id": room_id,
                "author": agent_name,
                "author_type": "agent",
                "data": {
                    "message": content,
                    "via": "chain_filter",
                    "ability": ability_name,
                    "node_id": node_id,
                },
            },
        )
        if result.get("success"):
            logger.info(
                "RoomFilter: agent=%s ability=%s node=%s → room=%s",
                agent_name,
                ability_name,
                node_id,
                room_id,
            )
        else:
            logger.warning(
                "RoomFilter: append rejected (agent=%s room=%s): %s",
                agent_name,
                room_id,
                result.get("error"),
            )
