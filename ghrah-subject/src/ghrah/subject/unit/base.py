# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Base contracts for Subject runtime units."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ghrah.subject.service_key import SubjectServiceKey

__all__ = [
    "CommandContext",
    "CommandSource",
    "RouteSpec",
    "SubjectServiceKey",
    "SubjectUnit",
    "UnitMeta",
]


@dataclass(frozen=True)
class RouteSpec:
    """Commands and events handled by a unit."""

    commands: frozenset[str] = field(default_factory=frozenset)
    long_running_commands: frozenset[str] = field(default_factory=frozenset)
    events: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class UnitMeta:
    """Static metadata declared by a Subject unit."""

    name: str
    version: str = "0.1.0"
    requires: frozenset[SubjectServiceKey[Any]] = field(default_factory=frozenset)
    provides: frozenset[SubjectServiceKey[Any]] = field(default_factory=frozenset)
    routes: RouteSpec = field(default_factory=RouteSpec)
    provides_capabilities: bool = False


class CommandSource(StrEnum):
    """Origin of a command dispatched to a Subject unit."""

    CORE = "core"
    OBSERVER = "observer"
    INTERNAL = "internal"


@dataclass(frozen=True)
class CommandContext:
    """Metadata attached to a command invocation."""

    source: CommandSource
    request_id: str | None = None
    session_id: str | None = None
    timeout: float | None = None

    @classmethod
    def core(
        cls,
        request_id: str | None,
        *,
        timeout: float | None = None,
    ) -> CommandContext:
        """Create context for a command received from Core."""

        return cls(source=CommandSource.CORE, request_id=request_id, timeout=timeout)

    @classmethod
    def observer(
        cls,
        request_id: str | None,
        *,
        session_id: str | None,
        timeout: float | None = None,
    ) -> CommandContext:
        """Create context for a command received from Observer."""

        return cls(
            source=CommandSource.OBSERVER,
            request_id=request_id,
            session_id=session_id,
            timeout=timeout,
        )

    @classmethod
    def internal(cls) -> CommandContext:
        """Create context for an internal runtime command."""

        return cls(source=CommandSource.INTERNAL)


class SubjectUnit(ABC):
    """Base class for Subject runtime units."""

    @property
    @abstractmethod
    def meta(self) -> UnitMeta:
        """Return static metadata for this unit."""

    async def init(self, ctx: Any) -> None:
        """Initialize the unit with its runtime context."""

    async def start(self) -> None:
        """Start the unit. Implementations should be idempotent."""

    async def stop(self) -> None:
        """Stop the unit. Implementations should be idempotent."""

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        """Handle a command routed to this unit."""

        return {
            "success": False,
            "error": f"Command '{command}' is not implemented by unit '{self.meta.name}'.",
        }

    async def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Handle an event routed to this unit."""
