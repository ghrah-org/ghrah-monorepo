# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ListAbilitiesAbility：发现当前部署的全部 ability（只读自省面）。

回答「现在到底有哪些 ability」——覆盖两个正交视角并显式给出差异：

- **registry 面**：AbilityRegistry 已注册的 handler 全集（可实例化面，
  ``ghrah.abilities`` import 时由 ``_register_builtin_abilities`` 填充）；
- **manifest 面**：有静态档案的定义全集——包内内置 yaml
  （``load_all_builtin_manifests``）+ 文件系统 ManifestStore 接线时的
  自定义定义；
- **diff**：两面差异显式列出——现状三层清单（包内 yaml / 加载白名单 /
  注册表）靠人工对齐，本能力把断链点变成运行时可查询的事实。

数据规模控制：列表层只给 full_name + 一句话 description（截断），
详情层（tool schema / permissions / implementation 路由）按
``full_name`` 参数对单个 ability 拉取，避免一次全量塞入。

接线语义（零隐式）：ManifestStore 经 AbilityExecutionContext.services
装配期注入——未接线时只返回 registry 面 + 包内内置面，``sources``
里明确说明文件系统定义面缺席，不静默当作空集。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.abilities.base import Ability
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.abilities.context import AbilityExecutionContext
    from ghrah.abilities.hooks import Hook

__all__ = ["ListAbilitiesAbility"]

_MAX_ABILITIES = 100
_MAX_DESCRIPTION_CHARS = 200


class ListAbilitiesInput(BaseModel):
    """list_abilities 工具入参契约（extra=forbid）。"""

    model_config = {"extra": "forbid"}

    full_name: str | None = Field(
        default=None,
        description=(
            "Optional full name of a single ability to inspect in detail "
            "(e.g. 'ghrah.fs.read_file'). Omit to list all abilities."
        ),
    )
    source: str = Field(
        default="all",
        description=(
            "Which source to query: 'manifests' (statically declared "
            "definitions), 'registry' (instantiable handlers), or 'all' "
            "(both, with an explicit diff)."
        ),
    )


class ListAbilitiesAbility(Ability):
    """发现当前全部 ability：manifest 面 + registry 面 + 差异。

    输出经 ``data`` 返回：

    - 无 ``full_name``：``{"abilities": [...], "count", "sources",
      "diff"?}``——列表项含 full_name / description / handler /
      in_registry 四个字段（行以 manifest 面为主键）；
    - 有 ``full_name``：单条详情 ``{"full_name", "description", "tool",
      "permissions", "implementation", "in_registry"}``。
    """

    @property
    def name(self) -> str:
        return "list_abilities"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "list_abilities",
                "description": (
                    "Discover all abilities available in this deployment "
                    "(read-only). Lists every statically declared ability "
                    "definition (builtin package + wired ManifestStore), "
                    "marks whether each is registered as an instantiable "
                    "handler, and reports source mismatches explicitly. "
                    "Pass full_name to get one ability's detail (tool "
                    "schema, permissions, implementation route)."
                ),
                "parameters": ListAbilitiesInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "list_abilities(full_name: str | None = None, "
            "source: str = 'all') -> dict: discover all available "
            "abilities and where each is declared"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        tool_args = context.tool_args or {}
        full_name = tool_args.get("full_name")
        source = tool_args.get("source", "all")

        if full_name:
            return self._read_single(context, str(full_name))
        if source not in ("all", "registry", "manifests"):
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Invalid source: {source!r} (use all/registry/manifests)"},
            )
        return self._list_all(context, source)

    # ----------------------------------------------------------------
    # 列表 / 详情
    # ----------------------------------------------------------------

    def _list_all(self, context: AbilityExecutionContext, source: str) -> ActionResult:
        """全集列表：manifest 面为行主键，registry 面逐行标注 + diff 兜出。"""
        registry = self._registry_handlers() if source != "manifests" else {}
        manifests = self._all_manifests(context) if source != "registry" else {}

        rows: list[dict[str, Any]] = []
        for full_name in sorted(manifests.keys())[:_MAX_ABILITIES]:
            manifest = manifests[full_name]
            handler = manifest.implementation.handler
            row: dict[str, Any] = {
                "full_name": full_name,
                "description": manifest.metadata.description[:_MAX_DESCRIPTION_CHARS],
            }
            if source != "manifests":
                row["handler"] = handler
                row["in_registry"] = handler in registry
            rows.append(row)

        data: dict[str, Any] = {
            "abilities": rows,
            "count": len(rows),
            "sources": self._sources_description(context, source),
            "truncated": len(manifests) > _MAX_ABILITIES,
        }
        if source == "all":
            diff = self._diff(registry, manifests)
            # registry-only 项不是 manifest 行，在 diff 段完整给出（不合成假名）
            data["diff"] = diff
            data["count"] = len(rows) + len(diff["registered_without_manifest"])
        return ActionResult(outcome=ActionOutcome.SUCCESS, data=data)

    def _read_single(self, context: AbilityExecutionContext, full_name: str) -> ActionResult:
        """单条详情：档案（含 tool schema）+ 注册状态。"""
        registry = self._registry_handlers()
        manifest = self._load_manifest(context, full_name)
        if manifest is None:
            if full_name in registry:
                return ActionResult(
                    outcome=ActionOutcome.SUCCESS,
                    data={
                        "full_name": full_name,
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
                "description": manifest.metadata.description[:_MAX_DESCRIPTION_CHARS],
                "tool": manifest.tool.to_openai_tool() if manifest.tool is not None else None,
                "permissions": manifest.metadata.permissions.model_dump(exclude_defaults=True),
                "implementation": manifest.implementation.model_dump(exclude_none=True),
                "handler": handler,
                "in_registry": handler in registry,
            },
        )

    # ----------------------------------------------------------------
    # 两个数据面
    # ----------------------------------------------------------------

    @staticmethod
    def _registry_handlers() -> set[str]:
        """registry 面：已注册 handler 名全集（可实例化面）。"""
        from ghrah.abilities.registry import AbilityRegistry

        return set(AbilityRegistry.list_types())

    def _all_manifests(self, context: AbilityExecutionContext) -> dict[str, Any]:
        """manifest 面全集：包内内置 + 文件系统 store（接线时，损坏项跳过记 warning）。"""
        from ghrah.manifest.builtins import load_all_builtin_manifests

        manifests: dict[str, Any] = dict(load_all_builtin_manifests())

        store = context.manifest_store
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

    def _load_manifest(self, context: AbilityExecutionContext, full_name: str) -> Any | None:
        """单条档案：优先文件系统 store（自定义覆盖），回落包内内置。"""
        from ghrah.manifest.builtins import load_all_builtin_manifests

        store = context.manifest_store
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
    def _sources_description(context: AbilityExecutionContext, source: str) -> dict[str, Any]:
        """sources 段：本次查询覆盖了哪些面、哪些面缺席及原因。"""
        description: dict[str, Any] = {
            "query": source,
            "manifests_builtin_package": True,
            "manifests_fs_store": context.manifest_store is not None,
            "registry": source != "manifests",
        }
        if context.manifest_store is None:
            description["note"] = (
                "ManifestStore not wired — filesystem ability definitions "
                "are not included (assembly-time injection, not implicit)"
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
