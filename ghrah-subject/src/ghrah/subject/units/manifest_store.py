# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Manifest store built-in Subject unit."""

from __future__ import annotations

import logging
from typing import Any

from ghrah.scheme.loading.ability_catalog import load_all_builtin_manifests
from ghrah.scheme.loading.manifest_fields import PermissionFlags

from ghrah.subject.config import SubjectConfig
from ghrah.subject.manifest_store.builtins import ensure_builtins
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


class _ManifestPermissionIndexImpl:
    """Dynamic manifest permission index backed by a ManifestStore."""

    def __init__(self, store: ManifestStore) -> None:
        self._store = store
        self._index: dict[str, PermissionFlags] = {}
        self.rebuild()

    def rebuild(self) -> None:
        permissions: dict[str, PermissionFlags] = {}

        for manifest in load_all_builtin_manifests().values():
            handler = manifest.implementation.handler
            if handler:
                permissions[handler] = manifest.metadata.permissions

        try:
            ability_names = self._store.list_abilities()
        except Exception:
            logger.warning("Failed to list abilities from ManifestStore")
            ability_names = []

        for full_name in ability_names:
            try:
                manifest = self._store.get_ability(full_name)
            except Exception:
                logger.warning("Failed to load manifest: %s", full_name)
                continue
            handler = manifest.implementation.handler
            if handler:
                permissions[handler] = manifest.metadata.permissions

        self._index = permissions
        logger.info(
            "Loaded manifest permissions for %d abilities: %s",
            len(permissions),
            list(permissions.keys()),
        )

    def get_permissions(self) -> dict[str, PermissionFlags]:
        return dict(self._index)


class ManifestStoreUnit(SubjectUnit):
    """Owns manifest storage, manifest command routes, and permission indexing."""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._store: ManifestStore | None = None
        self._index: _ManifestPermissionIndexImpl | None = None
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

    @property
    def permission_index(self) -> _ManifestPermissionIndexImpl:
        if self._index is None:
            raise RuntimeError("ManifestStoreUnit has not been initialized.")
        return self._index

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        self._store = ManifestStore(self._config.manifest.manifest_root)
        self._store.ensure_dirs()
        ensure_builtins(self._store)
        self._index = _ManifestPermissionIndexImpl(self._store)
        ctx.provide(MANIFEST_STORE.name, self._store)

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        result = handle_manifest_command(command, payload, self.service)
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

        self.refresh_permission_index()
        ctx.emit(f"event/{event_type}", manifest_result_to_event_payload(result))

    def refresh_permission_index(self) -> None:
        self.permission_index.rebuild()

    def _require_context(self) -> Any:
        if self._ctx is None:
            raise RuntimeError("ManifestStoreUnit has not been initialized.")
        return self._ctx
