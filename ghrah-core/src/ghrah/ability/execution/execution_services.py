# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""执行期服务表：一次调用除参数与身份以外的全部依赖。

服务表是执行上下文的唯一形态。新增一种依赖时，做法是**加一个服务键、改
装配处**，而不是给调用对象加属性；这样业务字段不会随着功能演进不断膨胀。

本模块同时提供 :func:`build_execution_services`：所有执行入口（Agent 直接
执行、LLM tool-call、Hook、显式 ``execute_ability`` 命令）都经它组装服务表，
避免各入口各拼一份、语义悄悄漂移。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from ghrah.ability.execution.loop_data import LoopData

if TYPE_CHECKING:
    from ghrah.core.supervisor_protocol import SupervisorProtocol
    from ghrah.core.window_protocol import ContextManagerProtocol

__all__ = [
    "ABILITY_CONTENT",
    "CONTEXT_MANAGER",
    "LOOP",
    "MANIFEST_STORE",
    "MCP_RUNTIME",
    "MESSAGE_FACTORY",
    "SKILL_PROVIDER",
    "SUPERVISOR",
    "ExecutionServices",
    "ServiceKey",
    "build_execution_services",
]


@dataclass(frozen=True)
class ServiceKey[T]:
    """Typed key for values stored in ExecutionServices."""

    name: str
    service_type: type[Any] | tuple[type[Any], ...] = object


class ExecutionServices:
    """Mutable registry for services used during ability execution."""

    def __init__(
        self,
        initial: Mapping[ServiceKey[Any] | str, Any] | None = None,
    ) -> None:
        self._services: dict[str, Any] = {}
        if initial is not None:
            for key, value in initial.items():
                self.set(key, value)

    def get[T](self, key: ServiceKey[T], default: T | None = None) -> T | None:
        """Return a service by key, or default if it has not been set."""

        return cast(T | None, self._services.get(key.name, default))

    def require[T](self, key: ServiceKey[T]) -> T:
        """Return a required service or raise a clear error."""

        if key.name not in self._services or self._services[key.name] is None:
            raise RuntimeError(f"Required execution service '{key.name}' is not set.")
        return cast(T, self._services[key.name])

    def set[T](self, key: ServiceKey[T] | str, value: T) -> None:
        """Set a service by typed key or raw string name."""

        name = key.name if isinstance(key, ServiceKey) else key
        self._services[name] = value

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow snapshot of registered services."""

        return dict(self._services)


CONTEXT_MANAGER: ServiceKey[ContextManagerProtocol] = ServiceKey("context_manager")
SUPERVISOR: ServiceKey[SupervisorProtocol] = ServiceKey("supervisor")
# Agent 循环数据（llm_response / response / 上次动作结果）；
# 只由执行器以快照方式提供，不进调用对象。
LOOP: ServiceKey[LoopData] = ServiceKey("loop", LoopData)
# duck-typed ManifestStoreProtocol（load_agent/list_agents）；
# None = 未接线（manifest 类工具须显式 FAILURE 指路，绝不猜测）。
MANIFEST_STORE = ServiceKey[Any]("manifest_store")
# duck-typed SkillAbilityProvider（scan/get_skill/query_fingerprints）；
# None = 未接线（skill 类工具须显式说明缺席，绝不猜测目录）。
SKILL_PROVIDER = ServiceKey[Any]("skill_provider")
# duck-typed McpRuntime（activate/deactivate/call/close）；
# None = 未接线（MCP 类工具须显式 FAILURE 指路，绝不猜测可用性）。
MCP_RUNTIME = ServiceKey[Any]("mcp_runtime")
# 获授内容读取服务（正文/附件）；None = 未接线（内容类工具显式报缺席）。
ABILITY_CONTENT = ServiceKey[Any]("ability_content")
# duck-typed MessageFactory（策略构造消息用）；None = 未接线。
MESSAGE_FACTORY = ServiceKey[Any]("message_factory")


def build_execution_services(
    *,
    context_manager: ContextManagerProtocol | None = None,
    supervisor: SupervisorProtocol | None = None,
    loop: LoopData | None = None,
    manifest_store: Any = None,
    skill_provider: Any = None,
    mcp_runtime: Any = None,
    ability_content: Any = None,
    message_factory: Any = None,
) -> ExecutionServices:
    """按统一形状组装一次执行的服务表。

    所有执行入口共用这个函数：漏传的依赖保持“未接线”语义（``get`` 为
    ``None``），由使用方显式报错，而不是靠某个入口悄悄补默认对象。

    Args:
        context_manager: 上下文管理器（消息、状态、持久化）。
        supervisor: 集群 supervisor（集群类成员与 manifest 查询用）。
        loop: 本次执行的循环数据快照。
        manifest_store: Agent 声明存储。
        skill_provider: 旧 Skill 读取器（仅旧模式接线）。
        mcp_runtime: MCP 运行时。
        ability_content: 获授内容读取服务。
        message_factory: 消息构造器。

    Returns:
        已装配的服务表。
    """

    services = ExecutionServices()
    if context_manager is not None:
        services.set(CONTEXT_MANAGER, context_manager)
    if supervisor is not None:
        services.set(SUPERVISOR, supervisor)
    if loop is not None:
        services.set(LOOP, loop)
    if manifest_store is not None:
        services.set(MANIFEST_STORE, manifest_store)
    if skill_provider is not None:
        services.set(SKILL_PROVIDER, skill_provider)
    if mcp_runtime is not None:
        services.set(MCP_RUNTIME, mcp_runtime)
    if ability_content is not None:
        services.set(ABILITY_CONTENT, ability_content)
    if message_factory is not None:
        services.set(MESSAGE_FACTORY, message_factory)
    return services
