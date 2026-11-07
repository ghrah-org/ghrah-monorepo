# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Manifest store built-in Subject unit."""

from __future__ import annotations

import logging
from typing import Any

from ghrah.subject.config import SubjectConfig
from ghrah.subject.manifest_store.service import handle_manifest_command
from ghrah.subject.manifest_store.store import ManifestStore
from ghrah.subject.runtime.service_keys import MANIFEST_STORE
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units._commands import MANIFEST_COMMANDS
from ghrah.subject.units._helpers import (
    manifest_command_to_event,
    manifest_result_to_event_payload,
)

__all__ = ["ManifestStoreUnit"]

logger = logging.getLogger(__name__)


class ManifestStoreUnit(SubjectUnit):
    """维护 Agent 模板库与命令路由。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._store: ManifestStore | None = None
        self._meta = UnitMeta(
            name="manifest_store",
            routes=RouteSpec(commands=MANIFEST_COMMANDS),
            provides=frozenset({MANIFEST_STORE}),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def service(self) -> ManifestStore:
        if self._store is None:
            raise RuntimeError("ManifestStoreUnit has not been initialized.")
        return self._store

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        self._store = ManifestStore(self._config.manifest.manifest_root)
        self._store.ensure_dirs()
        ctx.provide(MANIFEST_STORE.name, self._store)

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        result = handle_manifest_command(
            command,
            payload,
            self.service,
            ability_install_root=self._config.ability.install_root,
        )
        if result.get("success"):
            await self._emit_manifest_change(command, payload, result)
        return result

    async def _emit_manifest_change(
        self,
        command: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        ctx = self._require_context()
        event_type = manifest_command_to_event(command, payload)
        if event_type is None:
            return

        ctx.emit(f"event/{event_type}", manifest_result_to_event_payload(result))

    def _require_context(self) -> Any:
        if self._ctx is None:
            raise RuntimeError("ManifestStoreUnit has not been initialized.")
        return self._ctx
