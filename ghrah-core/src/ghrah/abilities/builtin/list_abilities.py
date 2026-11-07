# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""旧 Ability 档案与注册表的只读查询。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.abilities.base import Ability
from ghrah.ability.execution.execution_services import MANIFEST_STORE
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.abilities.hooks import Hook
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.invocation import OperationCall

__all__ = ["ListAbilitiesAbility"]

logger = logging.getLogger(__name__)

_MAX_ABILITIES = 100
_MAX_DESCRIPTION_CHARS = 200

KIND_CORE = "core-ability"
"""静态档案 + 注册表来源的行标记。"""


class ListAbilitiesInput(BaseModel):
    """list_abilities 工具入参契约（extra=forbid）。"""

    model_config = {"extra": "forbid"}

    full_name: str | None = Field(
        default=None,
        description=(
            "Optional full name of a single ability to inspect in detail "
            "(e.g. 'ghrah.fs.read_file' or 'send'). Omit to list everything."
        ),
    )


class ListAbilitiesAbility(Ability):
    """查询能力档案以及它们与注册表的差异。"""

    @property
    def name(self) -> str:
        return "list_abilities"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "list_abilities",
                "description": (
                    "List the abilities available in this deployment "
                    "(read-only). Results describe declared manifests and registered handlers. "
                    "Pass full_name for one entry's detail."
                ),
                "parameters": ListAbilitiesInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "list_abilities(full_name: str | None = None) -> dict: list "
            "available abilities; pass "
            "full_name for one entry's detail"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        tool_args = call.arguments
        full_name = tool_args.get("full_name")

        if full_name:
            return self._read_single(services, str(full_name))
        return self._list_all(services)

    # ----------------------------------------------------------------
    # 列表 / 详情
    # ----------------------------------------------------------------

    def _list_all(self, services: ExecutionServices) -> ActionResult:
        """列出能力档案和注册表差异。"""
        registry = self._registry_handlers()
        manifests = self._all_manifests(services)

        rows: list[dict[str, Any]] = []
        for full_name in sorted(manifests.keys())[:_MAX_ABILITIES]:
            manifest = manifests[full_name]
            handler = manifest.implementation.handler
            rows.append(
                {
                    "full_name": full_name,
                    "kind": KIND_CORE,
                    "description": manifest.metadata.description[:_MAX_DESCRIPTION_CHARS],
                    "handler": handler,
                    "in_registry": handler in registry,
                }
            )

        diff = self._diff(registry, manifests)
        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={
                "abilities": rows,
                # registry-only 项（无档案的已注册 handler）不只是 diff，也是能力
                "count": len(rows) + len(diff["registered_without_manifest"]),
                "sources": self._sources_description(services),
                "truncated": len(manifests) > _MAX_ABILITIES,
                "diff": diff,
            },
        )

    def _read_single(self, services: ExecutionServices, full_name: str) -> ActionResult:
        """按全名查档案，或按处理器名称查注册表。"""
        registry = self._registry_handlers()
        manifest = self._load_manifest(services, full_name)
        if manifest is None:
            if full_name in registry:
                return ActionResult(
                    outcome=ActionOutcome.SUCCESS,
                    data={
                        "full_name": full_name,
                        "kind": KIND_CORE,
                        "description": "",
                        "handler": full_name,
                        "in_registry": True,
                        "note": "registry-only: registered handler without a manifest",
                    },
                )
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Ability not found: '{full_name}'"},
            )

        handler = manifest.implementation.handler
        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={
                "full_name": manifest.full_name,
                "kind": KIND_CORE,
                "description": manifest.metadata.description[:_MAX_DESCRIPTION_CHARS],
                "tool": manifest.tool.to_openai_tool() if manifest.tool is not None else None,
                "permissions": manifest.metadata.permissions.model_dump(exclude_defaults=True),
                "implementation": manifest.implementation.model_dump(exclude_none=True),
                "handler": handler,
                "in_registry": handler in registry,
            },
        )

    # ----------------------------------------------------------------
    # 两个数据来源
    # ----------------------------------------------------------------

    @staticmethod
    def _registry_handlers() -> set[str]:
        """registry 面：已注册 handler 名全集（可实例化面）。"""
        from ghrah.abilities.registry import AbilityRegistry

        return set(AbilityRegistry.list_types())

    def _all_manifests(self, services: ExecutionServices) -> dict[str, Any]:
        """core 档案来源：包内内置 + 文件系统 store（接线时，损坏项跳过记 warning）。"""
        from ghrah.manifest.builtins import load_all_builtin_manifests

        manifests: dict[str, Any] = dict(load_all_builtin_manifests())

        store = services.get(MANIFEST_STORE)
        if store is None:
            return manifests
        try:
            fs_names = list(store.list_abilities())
        except Exception:
            return manifests
        for full_name in fs_names:
            if full_name in manifests:
                continue
            manifest = self._try_load(store, full_name)
            if manifest is not None:
                manifests[full_name] = manifest
        return manifests

    def _load_manifest(self, services: ExecutionServices, full_name: str) -> Any | None:
        """单条档案：优先文件系统 store（自定义覆盖），回落包内内置。"""
        from ghrah.manifest.builtins import load_all_builtin_manifests

        store = services.get(MANIFEST_STORE)
        if store is not None:
            manifest = self._try_load(store, full_name)
            if manifest is not None:
                return manifest
        return load_all_builtin_manifests().get(full_name)

    @staticmethod
    def _try_load(store: Any, full_name: str) -> Any | None:
        """从 store 读单条档案；不存在/损坏返回 None（列表层不阻断）。"""
        try:
            return store.load_ability(full_name)
        except Exception:
            return None

    # ----------------------------------------------------------------
    # 说明与差异
    # ----------------------------------------------------------------

    @staticmethod
    def _sources_description(services: ExecutionServices) -> dict[str, Any]:
        """sources 段：本次实际覆盖了哪些来源、哪些缺席及原因。"""
        description: dict[str, Any] = {
            "manifests_builtin_package": True,
            "manifests_fs_store": services.get(MANIFEST_STORE) is not None,
        }
        if services.get(MANIFEST_STORE) is None:
            description["note"] = (
                "ManifestStore not wired — filesystem ability definitions are "
                "not included (assembly-time injection, not implicit)"
            )
        return description

    @staticmethod
    def _diff(registry: set[str], manifests: dict[str, Any]) -> dict[str, list[str]]:
        """两面差异：已注册无档案 / 有档案的 handler 未注册。"""
        manifest_handlers = {
            m.implementation.handler for m in manifests.values() if m.implementation.handler
        }
        registered_without_manifest = sorted(registry - manifest_handlers)
        manifest_without_registered = sorted(
            full_name
            for full_name, m in manifests.items()
            if m.implementation.handler and m.implementation.handler not in registry
        )
        return {
            "registered_without_manifest": registered_without_manifest,
            "manifest_without_registered": manifest_without_registered,
        }
