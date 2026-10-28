# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""功能级全链路测试：内存持久化 + 对话 Agent。

覆盖从初始化创建到 restore 重建的完整链路（全部使用预设字符串，无 mock 内部方法）：

1. AgentBuilder 构造带 InMemoryBackend 持久化的 Agent（注册 ConversationAbility）
2. 经 llm_factory 注入脚本化假 LLM，首次 receive 触发 _ensure_llm 惰性初始化
3. 收到预设用户消息 → 推理 → 返回预设回复
4. auto_persist 将根节点与会话节点增量落库
5. 从同一后端 restore 重建 ContextManager，校验消息/链节点可重建
"""

from __future__ import annotations

from _helpers import ScriptedLLM

from ghrah.agents.builder import AgentBuilder
from ghrah.context.manager import ContextManager
from ghrah.context.persistence.memory import InMemoryBackend
from ghrah.core.config import AgentConfig, ContextConfig
from ghrah.core.message import AgentMessage, MessageType
from ghrah.scheme.ability.core.operations.conversation import ConversationAbility

AGENT_NAME = "test-agent"
AGENT_ID = "agent-fullchain-1"
SYSTEM_PROMPT = "You are a test agent."
USER_INPUT = "预设输入"
PRESET_REPLY = "预设回复-A"


async def test_conversation_fullchain_memory_persist_and_restore() -> None:
    """初始化创建 → 收消息 → 预设回复 → 落库 → restore 重建。"""
    # 1. 构造配置与脚本化 LLM
    config = AgentConfig(
        name=AGENT_NAME,
        agent_id=AGENT_ID,
        system_prompt=SYSTEM_PROMPT,
        context=ContextConfig(persistence_type="memory", auto_persist=True),
    )
    fake_llm = ScriptedLLM(replies=[PRESET_REPLY])

    # 2. 初始化创建（真实 ConversationAbility + llm_factory 注入）
    agent = AgentBuilder.from_config(
        config,
        abilities=[ConversationAbility()],
        llm_factory=lambda _config: fake_llm,
    )

    assert agent.get_abilities() == ["conversation"]
    backend = agent._context_manager.persistence
    assert isinstance(backend, InMemoryBackend)
    assert agent._context_manager.auto_persist is True

    # 构造期根节点应已调度落库
    await agent._context_manager.wait_for_persist()
    assert await backend.load_checkpoint(AGENT_ID) is not None

    # 3. 收到预设消息 → 推理 → 预设回复
    message = AgentMessage(
        sender="user",
        recipient=AGENT_NAME,
        content=USER_INPUT,
        type=MessageType.CHAT,
    )
    reply = await agent.receive(message)

    assert reply.type == MessageType.RESULT
    assert reply.content == PRESET_REPLY
    assert reply.reply_to == message.id
    assert len(fake_llm.calls) == 1
    assert any(m.text == USER_INPUT for m in fake_llm.calls[0])

    # 4. 落库校验
    await agent._context_manager.wait_for_persist()

    checkpoint = await backend.load_checkpoint(AGENT_ID)
    assert checkpoint is not None
    assert len(checkpoint.nodes) >= 2

    cm = agent._context_manager
    roles = [m.role for m in cm.message_store.current_messages]
    texts = [m.text for m in cm.message_store.current_messages]
    assert roles == ["system", "user", "ai"]
    assert texts == [SYSTEM_PROMPT, USER_INPUT, PRESET_REPLY]
    assert cm.active_head.ability_names == ["conversation"]

    # 5. 从同一后端 restore 重建
    restored = ContextManager(agent_name=AGENT_ID, persistence=backend)
    await restored.restore(AGENT_ID)

    assert restored.active_head is not None
    assert [m.text for m in restored.message_store.current_messages] == texts
    assert len(restored.get_history()) == len(cm.get_history())
