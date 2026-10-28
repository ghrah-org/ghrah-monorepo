# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""无网络的持久消息演示：A → B/C，C 离线，重启后消费并回复。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from ghrah.agents.base import ActorAgent
from ghrah.agents.builder import AgentBuilder
from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.communication import MessageKind, MessageManager
from ghrah.core.config import AgentConfig
from ghrah.scheme.ability.core.operations.conversation import ConversationAbility


class DemoLLM:
    """只返回固定文本，避免演示依赖模型服务。"""

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def model(self) -> str:
        """返回演示模型标识。"""
        return "message-demo"

    async def generate(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        """为真实 agent loop 提供最终文本。"""
        return LLMResponse(content_blocks=[TextBlock(text=self._text)])

    def configure_tools(self, tools: list[dict[str, Any]]) -> None:
        """演示不调用工具。"""

    def apply_model_overrides(self, overrides: Any) -> None:
        """演示不使用模型覆盖。"""


def make_agent(agent_id: str, text: str) -> ActorAgent:
    """创建使用稳定 ID 和独立上下文的真实 agent。"""
    return AgentBuilder.from_config(
        AgentConfig(name=f"demo-{agent_id}", agent_id=agent_id),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: DemoLLM(text),
    )


async def main(db_path: Path) -> None:
    """展示在线、离线、重启后的投递与回复查询。"""
    async with MessageManager(db_path, project_id="demo", agent_ids=("a", "b", "c")) as mm:
        b = make_agent("b", "B 检查完成")
        mm.bind("b", b.receive)
        request = await mm.send(
            sender_id="a",
            recipient_ids=("b", "c"),
            content="请检查设计",
            kind=MessageKind.REQUEST,
            idempotency_key="demo-inspect-v1",
        )
        await mm.dispatch_pending()
        print("请求：", request.message_id)
        print(
            "第一轮：",
            [(d.recipient_id, d.state.value) for d in await mm.get_deliveries(request.message_id)],
        )

    async with MessageManager(db_path, project_id="demo", agent_ids=("a", "b", "c")) as mm:
        a = make_agent("a", "收到回复")
        c = make_agent("c", "C 检查完成")
        mm.bind("a", a.receive)
        mm.bind("c", c.receive)
        await mm.dispatch_pending()
        print(
            "恢复后：",
            [(d.recipient_id, d.state.value) for d in await mm.get_deliveries(request.message_id)],
        )
        print(
            "持久回复：",
            [(r.sender_id, r.content) for r in await mm.get_replies(request.message_id)],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="专用消息 SQLite 文件路径")
    args = parser.parse_args()
    asyncio.run(main(args.db))
