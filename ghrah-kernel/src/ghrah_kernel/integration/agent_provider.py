# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ActorAgent 的受限消息入口；Context 写入完全交给 Core。

Core 只在显式装配 Agent 时导入，默认微内核安装不依赖认知包。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..context import ProviderContext
from ..provider import ProviderFault, ProviderResult
from .identity import StaticIdentityConfig

if TYPE_CHECKING:
    from ghrah.agents.base import ActorAgent
    from ghrah.context.manager import ContextManager
    from ghrah.context.persistence.sqlite_backend import SqliteBackend

AGENT_SCHEMES = [
    {
        "name": "agent",
        "contract": "ghrah.agent-chat/v0",
        "actions": ["receive", "status", "history", "close_resources"],
    }
]
CHAT_ACTIONS = frozenset({"receive", "status", "history"})


def validate_configuration(configuration: dict) -> None:
    """在创建子进程前核定受信配置，不允许请求选择模型或数据库。"""
    if set(configuration) != {
        "provider",
        "identities",
        "administrator",
        "principal",
        "namespace",
        "database",
        "model",
    }:
        raise ValueError("Agent 配置字段不完整或含未知字段")
    identities = StaticIdentityConfig.from_dict(configuration["identities"])
    identities.bind(configuration["principal"], None)
    identities.bind(configuration["administrator"], None)
    if not any(x.namespace_id == configuration["namespace"] for x in identities.namespaces):
        raise ValueError("Agent 配置引用未知命名空间")
    database = configuration["database"]
    if not isinstance(database, str) or not Path(database).is_absolute():
        raise ValueError("Context 数据库必须为显式绝对路径")
    model = configuration["model"]
    if not isinstance(model, dict) or set(model) != {"kind", "responses", "delay_seconds"}:
        raise ValueError("必须显式声明模型配置")
    if model["kind"] != "scripted":
        raise ValueError("当前宿主只接受显式 scripted 模型")
    if (
        not isinstance(model["responses"], list)
        or not 1 <= len(model["responses"]) <= 64
        or any(not isinstance(x, str) or len(x.encode()) > 8192 for x in model["responses"])
        or type(model["delay_seconds"]) not in (int, float)
        or not 0 <= model["delay_seconds"] <= 30
    ):
        raise ValueError("脚本模型的响应或延时超出限制")


class ScriptedModel:
    """显式离线模型：走真实 Agent 推理链，响应序列来自受信配置。"""

    model = "scripted"

    def __init__(self, configuration: dict) -> None:
        self.responses = tuple(configuration["responses"])
        self.delay = configuration["delay_seconds"]
        self.calls = 0

    def configure_tools(self, tools: list[dict]) -> None:
        """纯文本宿主拒绝装入任何函数工具。"""
        if tools:
            raise ValueError("纯文本宿主未声明函数工具")

    def apply_model_overrides(self, overrides: Any) -> None:
        """脚本模型不接受运行期参数覆盖。"""
        if overrides is not None:
            raise ValueError("脚本模型不接受参数覆盖")

    async def generate(self, messages: list[Any], tools: list[dict] | None = None) -> Any:
        """返回 Core 的真实响应对象，保留正常迭代与 Conversation hook。"""
        from ghrah.chat.content import TextBlock
        from ghrah.chat.format import LLMResponse

        self.configure_tools(tools or [])
        if self.calls >= len(self.responses):
            raise ValueError("脚本模型响应已用完")
        index = self.calls
        self.calls += 1
        await asyncio.sleep(self.delay)
        return LLMResponse(content_blocks=[TextBlock(text=self.responses[index])])


@dataclass
class AgentResources:
    """宿主持有的唯一 Actor、Context 与数据库连接。"""

    actor: ActorAgent
    context: ContextManager
    backend: SqliteBackend
    model: ScriptedModel


async def create_resources(configuration: dict, run_id: str) -> AgentResources:
    """显式依赖注入；先恢复已有检查点，避免初始化覆盖持久历史。"""
    from ghrah.ability.builtin.core.operations.conversation import ConversationAbility
    from ghrah.ability.execution.executor import LocalAbilityExecutor
    from ghrah.agents.base import ActorAgent
    from ghrah.chat.factory import ChatMessageFactory
    from ghrah.context.manager import ContextManager
    from ghrah.context.persistence.sqlite_backend import SqliteBackend
    from ghrah.types.config_types import AgentConfig

    validate_configuration(configuration)
    principal = configuration["principal"]
    backend = SqliteBackend(Path(configuration["database"]), run_id=run_id)
    await backend.connect()
    try:
        context = ContextManager(
            principal, persistence=backend, auto_persist=False, message_factory=ChatMessageFactory()
        )
        if await backend.has_checkpoint(principal):
            await context.restore(principal)
        model = ScriptedModel(configuration["model"])
        actor = ActorAgent(
            AgentConfig(name=principal, agent_id=principal),
            context_manager=context,
            ability_executor=LocalAbilityExecutor(principal),
            context_manager_factory=lambda: context,
            llm_factory=lambda _: model,
        )
        actor.register_ability(ConversationAbility())
        await context.persist()
        return AgentResources(actor, context, backend, model)
    except BaseException:
        await backend.close()
        raise


class AgentProvider:
    """入口核权、严格文本 DTO 与生命周期；不实现第二条 Context 写路径。"""

    def __init__(self, configuration: dict, resources: AgentResources, run_id: str) -> None:
        validate_configuration(configuration)
        self.identities = StaticIdentityConfig.from_dict(configuration["identities"])
        self.configuration = configuration
        self.resources = resources
        self.run_id = run_id
        self.lock = asyncio.Lock()
        self.close_lock = asyncio.Lock()
        self.closing = False
        self.closed = False
        self.close_error: BaseException | None = None

    def open(self, context: ProviderContext, path: str) -> dict:
        """仅打开此实例；挂载可见不等于有权调用。"""
        if path != "/":
            raise ProviderFault("NOT_FOUND", "Agent 没有该资源", effect="NO_EFFECT")
        try:
            grant = self.identities.grant(
                context.principal, self.configuration["namespace"], "agent"
            )
        except ValueError as exc:
            raise ProviderFault("ACCESS_DENIED", str(exc), effect="NO_EFFECT") from exc
        actions = sorted(CHAT_ACTIONS.intersection(grant["allowed_actions"]))
        if not actions:
            raise ProviderFault("ACCESS_DENIED", "没有 Agent 动作许可", effect="NO_EFFECT")
        grant["allowed_actions"] = actions
        return {
            "result": "opened",
            "provider_object_id": "agent",
            "actions": actions,
            "grant": grant,
        }

    def _authorize(self, context: ProviderContext, action: str) -> None:
        if context.provider_object_id != "agent":
            raise ProviderFault("ACCESS_DENIED", "需要 Agent 资源句柄", effect="NO_EFFECT")
        try:
            grant = self.identities.grant(
                context.principal, self.configuration["namespace"], "agent"
            )
        except ValueError as exc:
            raise ProviderFault("ACCESS_DENIED", str(exc), effect="NO_EFFECT") from exc
        if action not in CHAT_ACTIONS or action not in grant["allowed_actions"]:
            raise ProviderFault("ACCESS_DENIED", "没有该动作许可", effect="NO_EFFECT")

    async def invoke(self, context: ProviderContext, action: str, arguments: Any) -> ProviderResult:
        """业务输入不能指定主体、接收者、命令、模型或 Context 标识。"""
        if action == "close_resources":
            if (
                context.provider_object_id != "root"
                or context.principal != self.configuration["administrator"]
            ):
                raise ProviderFault("ACCESS_DENIED", "需要受信管理根", effect="NO_EFFECT")
            if arguments != {}:
                raise ProviderFault("INVALID_ARGUMENT", "关闭不接受参数", effect="NO_EFFECT")
            await self.aclose()
            return ProviderResult({"closed": True, "run_id": self.run_id}, "EFFECT_OCCURRED")
        self._authorize(context, action)
        if not isinstance(arguments, dict):
            raise ProviderFault("INVALID_ARGUMENT", "参数必须是对象", effect="NO_EFFECT")
        if action == "receive":
            if (
                set(arguments) != {"type", "text"}
                or arguments["type"] != "chat"
                or not isinstance(arguments["text"], str)
                or not 1 <= len(arguments["text"].encode()) <= 8192
            ):
                raise ProviderFault("INVALID_ARGUMENT", "只接受有界 CHAT 文本", effect="NO_EFFECT")
        elif action == "status" and arguments:
            raise ProviderFault("INVALID_ARGUMENT", "状态不接受参数", effect="NO_EFFECT")
        elif action == "history" and (
            set(arguments) != {"limit"}
            or type(arguments["limit"]) is not int
            or not 1 <= arguments["limit"] <= 16
        ):
            raise ProviderFault("INVALID_ARGUMENT", "历史需要 1..16 的 limit", effect="NO_EFFECT")
        async with self.lock:
            if self.closing:
                raise ProviderFault("UNAVAILABLE", "Agent 正在关闭", effect="NO_EFFECT")
            actor = self.resources.actor
            directory = actor.get_entity_context()
            if action == "status":
                return ProviderResult(
                    {
                        "principal": self.configuration["principal"],
                        "run_id": self.run_id,
                        "pid": os.getpid(),
                        "parent_pid": os.getppid(),
                        "busy": actor.busy,
                        "model_calls": self.resources.model.calls,
                        "context": directory,
                    },
                    "NO_EFFECT",
                )
            if action == "history":
                session = next(
                    x
                    for x in directory["sessions"]
                    if x["session_id"] == directory["active_session_id"]
                )
                return ProviderResult(
                    actor.get_entity_history(
                        session["session_id"], session["active_branch_id"], arguments["limit"]
                    ),
                    "NO_EFFECT",
                )
            from ghrah.core.message import AgentMessage, MessageType

            message = AgentMessage(
                sender=context.principal,
                recipient=self.configuration["principal"],
                content=arguments["text"],
                type=MessageType.CHAT,
            )
            response = await actor.receive(message)
            # 显式持久化屏障：落库失败传播给调用者，不能把后台错误当作成功。
            await self.resources.context.persist()
            return ProviderResult(
                {
                    "id": response.id,
                    "input_id": message.id,
                    "reply_to": response.reply_to,
                    "sender": response.sender,
                    "recipient": response.recipient,
                    "type": response.type.value,
                    "content": response.content,
                },
                "EFFECT_OCCURRED",
            )

    async def aclose(self) -> None:
        """停止并等待 Runner，完成唯一写侧的检查点，再关闭 sqlite。"""
        async with self.close_lock:
            if self.closed:
                if self.close_error is not None:
                    raise self.close_error
                return
            self.closing = True
            await self.resources.actor.stop_receiving()
            async with self.lock:
                try:
                    await self.resources.context.persist()
                except BaseException as exc:
                    self.close_error = exc
                    raise
                finally:
                    try:
                        await self.resources.backend.close()
                    except BaseException as exc:
                        self.close_error = exc
                        raise
                    finally:
                        self.closed = True
