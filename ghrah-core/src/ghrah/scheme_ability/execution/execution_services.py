# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Typed service registry for ability execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

if TYPE_CHECKING:
    from ghrah.core.supervisor_protocol import SupervisorProtocol
    from ghrah.core.window_protocol import ContextManagerProtocol

__all__ = [
    "AGENT_NAME",
    "CONTEXT_MANAGER",
    "MANIFEST_STORE",
    "MCP_RUNTIME",
    "SKILL_PROVIDER",
    "SUPERVISOR",
    "ExecutionServices",
    "ServiceKey",
]

T = TypeVar("T")


@dataclass(frozen=True)
class ServiceKey(Generic[T]):
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

    def get(self, key: ServiceKey[T], default: T | None = None) -> T | None:
        """Return a service by key, or default if it has not been set."""

        return cast(T | None, self._services.get(key.name, default))

    def require(self, key: ServiceKey[T]) -> T:
        """Return a required service or raise a clear error."""

        if key.name not in self._services or self._services[key.name] is None:
            raise RuntimeError(f"Required execution service '{key.name}' is not set.")
        return cast(T, self._services[key.name])

    def set(self, key: ServiceKey[T] | str, value: T) -> None:
        """Set a service by typed key or raw string name."""

        name = key.name if isinstance(key, ServiceKey) else key
        self._services[name] = value

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow snapshot of registered services."""

        return dict(self._services)


CONTEXT_MANAGER: ServiceKey[ContextManagerProtocol] = ServiceKey("context_manager")
SUPERVISOR: ServiceKey[SupervisorProtocol] = ServiceKey("supervisor")
AGENT_NAME = ServiceKey[str]("agent_name", str)
# duck-typed ManifestStoreProtocol（load_agent/list_agents）；
# None = 未接线（manifest 类工具须显式 FAILURE 指路，绝不猜测）。
MANIFEST_STORE = ServiceKey[Any]("manifest_store")
# duck-typed SkillAbilityProvider（scan/get_skill/query_fingerprints）；
# None = 未接线（skill 类工具须显式说明缺席，绝不猜测目录）。
SKILL_PROVIDER = ServiceKey[Any]("skill_provider")
# duck-typed McpRuntime（activate/deactivate/call/close）；
# None = 未接线（MCP 类工具须显式 FAILURE 指路，绝不猜测可用性）。
MCP_RUNTIME = ServiceKey[Any]("mcp_runtime")
