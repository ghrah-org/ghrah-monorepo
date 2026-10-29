# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ListAbilitiesAbility：发现当前部署的全部 ability（只读自省面）。

**skill-ability 也是 ability**：本能力一次查询覆盖全部来源，行内用 ``kind``
区分归属——

- ``core-ability``：静态档案面（包内内置 yaml + 接线时的文件系统 ManifestStore），
  逐行标注 handler 是否已注册（``in_registry``）；
- ``skill-ability``：skill 目录池的扫描结果（经装配期注入的
  ``SkillAbilityProvider``；未接线则不出行）。

没有 ``source`` 参数：默认就是全查，唯一需要区分的是行归属（``kind``）。

``diff`` 显式兜出两面差异——包内 yaml / 加载白名单 / 注册表这三层清单靠人工对齐，
本能力把断链点变成运行时可查询的事实。

数据规模控制：列表层只给 full_name + 一句话 description（截断），详情层
（tool schema / permissions / implementation 路由 / skill 目录与入口哨兵）按
``full_name`` 对单个 ability 拉取，避免一次全量塞入。详情里的 ``fingerprint``
只含**入口哨兵**（定义面 + 正文）与已登记的整树摘要；整树裁决是昂贵动作，
需显式调 ``SkillAbilityProvider.verify()``，不在发现面自动触发。

接线语义（零隐式）：ManifestStore 与 SkillAbilityProvider 都经装配期注入；
未接线时 ``sources`` 明确说明哪一面缺席，不静默当作空集。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.scheme_ability.execution.base import Ability
from ghrah.scheme_ability.loading.skill_errors import SkillNotFoundError
from ghrah.scheme_ability.loading.skill_models import SkillFingerprint
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme_ability.execution.context import AbilityExecutionContext
    from ghrah.scheme_ability.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/list_abilities.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {}
HOOKS = {}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = (
    "List the abilities available in this deployment (read-only). Every row is marked "
    "kind='core-ability' (declared manifests + registry) or kind='skill-ability' (skill "
    "catalog). Pass full_name for a single entry's detail."
)
TAGS = [
    "core",
    "discovery",
]
# --- catalogue 声明结束

__all__ = ["ListAbilitiesAbility"]

logger = logging.getLogger(__name__)

_MAX_ABILITIES = 100
_MAX_DESCRIPTION_CHARS = 200

KIND_CORE = "core-ability"
"""静态档案 + 注册表来源的行标记。"""
KIND_SKILL = "skill-ability"
"""skill 目录池来源的行标记。"""


class ListAbilitiesInput(BaseModel):
    """list_abilities 工具入参契约（extra=forbid）。"""

    model_config = {"extra": "forbid"}

    full_name: str | None = Field(
        default=None,
        description=(
            "Optional full name of a single ability to inspect in detail "
            "(e.g. 'ghrah.fs.read_file' or 'research'). Omit to list everything."
        ),
    )


class ListAbilitiesAbility(Ability):
    """发现当前全部 ability：core-ability + skill-ability（只读）。

    输出经 ``data`` 返回：

    - 无 ``full_name``：``{"abilities": [...], "skills": [...], "count",
      "sources", "diff", "truncated"}``——``abilities`` 是 core-ability 行
      （full_name / kind / description / handler / in_registry），``skills`` 是
      skill-ability 行（full_name / kind / description / root）；
    - 有 ``full_name``：单条详情 ``{"full_name", "kind", "description", ...}``，
      core 面含 tool schema / permissions / implementation，skill 面含目录与指纹。
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
                    "List the abilities available in this deployment "
                    "(read-only). Every row is marked kind='core-ability' "
                    "(declared manifests + registry) or kind='skill-ability' "
                    "(skill catalog). Pass full_name for one entry's detail."
                ),
                "parameters": ListAbilitiesInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "list_abilities(full_name: str | None = None) -> dict: list "
            "available abilities (kind: core-ability | skill-ability); pass "
            "full_name for one entry's detail"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        tool_args = context.tool_args or {}
        full_name = tool_args.get("full_name")

        if full_name:
            return self._read_single(context, str(full_name))
        return self._list_all(context)

    # ----------------------------------------------------------------
    # 列表 / 详情
    # ----------------------------------------------------------------

    def _list_all(self, context: AbilityExecutionContext) -> ActionResult:
        """全量列表：core-ability 行 + skill-ability 行 + 差异段。"""
        registry = self._registry_handlers()
        manifests = self._all_manifests(context)
        skills_all = self._all_skills(context)
        skills = skills_all[:_MAX_ABILITIES]

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
                "skills": skills,
                # registry-only 项（无档案的已注册 handler）不只是 diff，也是能力
                "count": len(rows) + len(skills) + len(diff["registered_without_manifest"]),
                "sources": self._sources_description(context),
                "truncated": len(manifests) > _MAX_ABILITIES or len(skills_all) > _MAX_ABILITIES,
                "diff": diff,
            },
        )

    def _read_single(self, context: AbilityExecutionContext, full_name: str) -> ActionResult:
        """单条详情：core 面查档案，未命中再查 skill 面，都未命中才 FAILURE。"""
        registry = self._registry_handlers()
        manifest = self._load_manifest(context, full_name)
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
            skill_result = self._read_skill(context, full_name)
            if skill_result is not None:
                return skill_result
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
        from ghrah.scheme_ability.loading.ability_catalog import builtin_handlers

        return set(builtin_handlers())

    def _all_manifests(self, context: AbilityExecutionContext) -> dict[str, Any]:
        """core 档案来源：包内内置 + 文件系统 store（接线时，损坏项跳过记 warning）。"""
        from ghrah.scheme_ability.loading.ability_catalog import load_all_builtin_manifests

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
        from ghrah.scheme_ability.loading.ability_catalog import load_all_builtin_manifests

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
    # skill-ability 来源（装配期注入 provider，未接线即不出行）
    # ----------------------------------------------------------------

    @staticmethod
    def _all_skills(context: AbilityExecutionContext) -> list[dict[str, Any]]:
        """skill-ability 行：经 ``context.skill_provider`` 扫描 skill 目录池。

        未接线（provider 为 None）返回空表，缺席原因由
        :meth:`_sources_description` 显式说明——与 ManifestStore 同一口径。
        扫描问题（SKILL.md 损坏、full_name 冲突等）不阻断本列表：坏项由
        provider 的 ``scan().issues`` 承载，这里只列可用项。
        """
        provider = context.skill_provider
        if provider is None:
            return []
        try:
            scan = provider.scan()
        except Exception:
            logger.warning("skill scan failed — skill-ability rows omitted", exc_info=True)
            return []
        return [
            {
                "full_name": skill.full_name,
                "kind": KIND_SKILL,
                "description": skill.description[:_MAX_DESCRIPTION_CHARS],
                "root": skill.source,
            }
            for skill in scan.skills
        ]

    @staticmethod
    def _read_skill(context: AbilityExecutionContext, full_name: str) -> ActionResult | None:
        """单条 skill-ability 详情（含指纹状态）；不在 skill 池中返回 None。

        返回 None = 该 full_name 不是 skill-ability，交由调用方判定未命中
        （core 面先查，skill 面后查）。
        """
        provider = context.skill_provider
        if provider is None:
            return None
        try:
            skill = provider.get_skill(full_name)
        except SkillNotFoundError:
            return None
        except Exception:
            logger.warning("skill detail lookup failed for %r", full_name, exc_info=True)
            return None

        data: dict[str, Any] = {
            "full_name": skill.full_name,
            "kind": KIND_SKILL,
            "description": skill.description[:_MAX_DESCRIPTION_CHARS],
            "root": skill.source,
            "skill_dir": str(skill.skill_dir),
            # 新格式的元数据全在 ability.yaml 里（旧的 SKILL.md frontmatter 已不存在），
            # 因此详情直接摊开声明面字段，不再伪装一个 frontmatter 映射。
            "title": skill.title,
            "tags": list(skill.tags),
            "body_path": skill.body_path,
            "media_type": skill.media_type,
        }
        try:
            fingerprint = provider.get_fingerprint(full_name)
        except Exception:
            logger.warning("skill fingerprint lookup failed for %r", full_name, exc_info=True)
        else:
            data["fingerprint"] = {
                # 入口哨兵：只覆盖定义面 + 正文（两次读盘），供发现面廉价判断；
                # 整树裁决不在这里算——必须显式调 provider.verify()。
                "entry": {
                    "current": fingerprint.current,
                    "recorded": fingerprint.recorded,
                    "drifted": fingerprint.drifted,
                },
                "record": ListAbilitiesAbility._fingerprint_record(fingerprint),
                "lock_issue": fingerprint.lock_issue,
            }
        return ActionResult(outcome=ActionOutcome.SUCCESS, data=data)

    @staticmethod
    def _fingerprint_record(fingerprint: SkillFingerprint) -> dict[str, Any] | None:
        """整树登记记录（只读 lock，不重算整树）；无 lock 时为 None。

        ``content_digest`` 跨机器可比，``snapshot_digest`` 只反映本机状态；
        降级（观测不完整）时两枚摘要为 None 且 ``degraded`` 说明原因。
        """
        record = fingerprint.record
        if record is None:
            return None
        degrade = record.manifest.degraded
        return {
            "algorithm": record.algorithm,
            "content_digest": record.content_digest,
            "snapshot_digest": record.snapshot_digest,
            "recorded_at": record.recorded_at,
            "entries": record.manifest.entry_count,
            "total_bytes": record.manifest.total_bytes,
            "degraded": degrade.to_mapping() if degrade is not None else None,
        }

    # ----------------------------------------------------------------
    # 说明与差异
    # ----------------------------------------------------------------

    @staticmethod
    def _sources_description(context: AbilityExecutionContext) -> dict[str, Any]:
        """sources 段：本次实际覆盖了哪些来源、哪些缺席及原因。"""
        description: dict[str, Any] = {
            "manifests_builtin_package": True,
            "manifests_fs_store": context.manifest_store is not None,
            "skill_ability": context.skill_provider is not None,
        }
        if context.manifest_store is None:
            description["note"] = (
                "ManifestStore not wired — filesystem ability definitions are "
                "not included (assembly-time injection, not implicit)"
            )
        if context.skill_provider is None:
            description["skills_absent_reason"] = (
                "SkillAbilityProvider not wired — skill-ability entries are "
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
