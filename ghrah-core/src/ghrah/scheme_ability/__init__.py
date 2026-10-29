# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Ability scheme：能力定义面、加载投影面、执行面与权限面的统一入口。

本包把「能力」收敛成一种范式：一个能力包 = 一份 ``ability.yaml``（定义面）+
一个 ``operations/<成员键>.py``（实现面）。运行时没有注册表、没有缓存——
需要时扫盘，投影出 ``AbilityManifest`` 这个 view。

内部按职责分四个主题子包：

- :mod:`ghrah.scheme_ability.loading` —— 读盘、校验、投影。对外的查询 API
  （``load_ability_definitions`` / ``builtin_handlers`` / ``create_ability`` …）在这。
  扫描根由目录池 ``ability_roots`` 给出：内置树（rank 0）+ 用户目录
  ``~/.ghrah/skill-ability``（rank 100），同名容器由 rank 小者胜出。
- :mod:`ghrah.scheme_ability.execution` —— 执行上下文、执行器、Hook 控制流、能力实例化。
- :mod:`ghrah.scheme_ability.permissions` —— 审批闸门（HITL）、命令安全分级、路径约束。
- :mod:`ghrah.scheme_ability.ability` —— **只放能力包本身**，不含任何加载逻辑。

顶层（本文件）不承载逻辑：只维护一张「公开名字 → 归属模块」的索引表，让外部代码
可以 ``from ghrah.scheme_ability import AbilityDefinition, load_ability_definitions``，
而不必记住每个名字落在哪个子模块。子包仍是名字的**唯一归属地**，本文件不定义实现，
``__all__`` 也直接由该表派生——新增名字只改一处，不产生第二份清单。

**惰性求值**：导入期不拉起任何子模块。``import ghrah.scheme_ability`` 零成本；只有真正
访问 ``AbilityDefinition`` 这类名字时，才 ``import_module`` 对应子模块并就地缓存。
这样既避免了「父包 ``__init__`` 早于子模块执行」造成的初始化环，也让
``from ghrah.scheme_ability.execution.base import Ability`` 这类深路径导入不受顶层影响。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_MODULE_OF: dict[str, str] = {
    # ---- 定义面：ability.yaml 的类型化契约 ----
    "AbilityDefinition": "loading.ability_schema",
    "AbilityIdentity": "loading.ability_schema",
    "ExecutionSpec": "loading.ability_schema",
    "OperationDefinition": "loading.ability_schema",
    "OperationImplementation": "loading.ability_schema",
    "ProviderRequirement": "loading.ability_schema",
    "SCHEMA_VERSION": "loading.ability_schema",
    "SkillBody": "loading.ability_schema",
    "SkillDefinition": "loading.ability_schema",
    "DEFINITION_FILE_NAME": "loading.ability_schema",
    "load_yaml": "loading.ability_schema",
    "parse_definition": "loading.ability_schema",
    # ---- 目录池：能力包在哪、谁优先 ----
    "AbilityRoot": "loading.ability_roots",
    "BUILTIN_ROOT_RANK": "loading.ability_roots",
    "USER_ROOT_RANK": "loading.ability_roots",
    "builtin_pool": "loading.ability_roots",
    "builtin_root": "loading.ability_roots",
    "default_pool": "loading.ability_roots",
    "default_user_root": "loading.ability_roots",
    "ensure_roots": "loading.ability_roots",
    "path_root": "loading.ability_roots",
    "user_root": "loading.ability_roots",
    # ---- 目录面扫描：全池的定义与 skill 成员 ----
    "AbilityAsset": "loading.ability_scan",
    "AbilityContainer": "loading.ability_scan",
    "AbilityEntry": "loading.ability_scan",
    "AbilityOperation": "loading.ability_scan",
    "AbilityScanIssue": "loading.ability_scan",
    "AbilityScanResult": "loading.ability_scan",
    "AbilitySkill": "loading.ability_scan",
    "load_ability_skills": "loading.ability_scan",
    "scan_abilities": "loading.ability_scan",
    "scan_ability_definitions": "loading.ability_scan",
    # ---- 加载与查询：扫盘、投影、实例化 ----
    "ABILITY_DEFINITION_FILE": "loading.ability_catalog",
    "DEFINITION_SCHEMA_VERSION": "loading.ability_catalog",
    "REGISTRY_ONLY_OPERATIONS": "loading.ability_catalog",
    "ability_class": "loading.ability_catalog",
    "builtin_full_names": "loading.ability_catalog",
    "builtin_handlers": "loading.ability_catalog",
    "create_ability": "loading.ability_catalog",
    "has_handler": "loading.ability_catalog",
    "load_ability_definitions": "loading.ability_catalog",
    "load_all_builtin_manifests": "loading.ability_catalog",
    "load_builtin_manifest": "loading.ability_catalog",
    "load_definition": "loading.ability_catalog",
    "operation_containers": "loading.ability_catalog",
    "CatalogReport": "loading.catalog",
    "scan_catalog": "loading.catalog",
    "ToolEntry": "loading.tool_list",
    "ToolList": "loading.tool_list",
    "load_tool_list": "loading.tool_list",
    "CATALOG_DESCRIPTION_MAX_CHARS": "loading.skill_context",
    "SkillCatalog": "loading.skill_context",
    "SkillCatalogEntry": "loading.skill_context",
    "SkillContextError": "loading.skill_context",
    "SkillDocument": "loading.skill_context",
    "catalog_description": "loading.skill_context",
    "digest_skill_catalog": "loading.skill_context",
    "load_skill_catalog": "loading.skill_context",
    "read_skill_body": "loading.skill_context",
    "read_skill_text": "loading.skill_context",
    "render_skill_catalog": "loading.skill_context",
    # ---- 技能包面：扫描与登记已并入能力扫盘（原 ghrah.skills 的实现）----
    "read_skill_document": "loading.skill_context",
    "SkillPackageDocument": "loading.skill_models",
    "SkillInitResult": "loading.skill_models",
    "LockOutcome": "loading.skill_models",
    "LockRecord": "loading.skill_models",
    "read_lock_record": "loading.skill_lock",
    "create_lock": "loading.skill_lock",
    "replace_lock": "loading.skill_lock",
    "SkillFingerprint": "loading.skill_models",
    "BundleVerification": "loading.skill_models",
    "build_manifest": "loading.skill_fingerprint",
    "ensure_locks": "loading.ability_scan",
    "relock_skill": "loading.ability_scan",
    "verify_skill": "loading.ability_scan",
    "SkillAbilityProvider": "loading.skill_provider",
    "default_provider": "loading.skill_provider",
    # ---- 错误 ----
    "SkillError": "loading.skill_errors",
    "SkillLockError": "loading.skill_errors",
    "SkillNotFoundError": "loading.skill_errors",
    "SkillParseError": "loading.skill_errors",
    # ---- 运行时 view：投影目标与工具面形状 ----
    "AbilityHooks": "loading.ability_view",
    "AbilityManifest": "loading.ability_view",
    "AbilityMetadata": "loading.ability_view",
    "HookDef": "loading.manifest_fields",
    "ImplementationDef": "loading.manifest_fields",
    "PermissionFlags": "loading.manifest_fields",
    "SUPPORTED_VERSIONS": "loading.manifest_fields",
    "ToolParameter": "loading.manifest_fields",
    "ToolSchema": "loading.manifest_fields",
    "BuiltinManifestStore": "loading.manifest_io",
    "ManifestStoreProtocol": "loading.manifest_io",
    "parse_ability_manifest": "loading.manifest_io",
    "parse_agent_manifest": "loading.manifest_io",
    "validate_manifest": "loading.manifest_io",
    # ---- 错误 ----
    "DuplicateManifestError": "loading.manifest_fields",
    "ManifestError": "loading.manifest_fields",
    "ManifestNotFoundError": "loading.manifest_fields",
    "ManifestValidationError": "loading.manifest_fields",
    "ManifestVersionError": "loading.manifest_fields",
    "AbilityError": "errors",
    "AbilityNotFoundError": "errors",
    # ---- agent 侧：编排单元的定义与解析 ----
    "AbilityRef": "agent_manifest",
    "AgentHooks": "agent_manifest",
    "AgentManifest": "agent_manifest",
    "AgentMetadata": "agent_manifest",
    "ContextOverrides": "agent_manifest",
    "ModelConfig": "agent_manifest",
    "PersistenceOverrides": "agent_manifest",
    "WindowOverrides": "agent_manifest",
    "ManifestResolver": "agent_resolver",
    "ResolvedAbility": "agent_resolver",
    "ResolvedAgent": "agent_resolver",
    # ---- 能力类型与路径工具 ----
    "CLUSTER_ABILITY_TYPES": "kinds",
    "FS_ABILITY_TYPES": "kinds",
    "ABILITY_PATH_SPECS": "paths",
    "AbilityPathSpec": "paths",
    "extract_paths": "paths",
    "is_subpath": "paths",
    "resolve_fs_permission_paths": "paths",
    "resolve_relative_path": "paths",
    # ---- 执行 ----
    "Ability": "execution.base",
    "ActionOutcome": "execution.base",
    "ActionResult": "execution.base",
    "instantiate_resolved_abilities": "execution.ability_build",
    "AbilityExecutionContext": "execution.context",
    "COT_CONTENT": "execution.execution_data",
    "CUMULATIVE_TOKEN_USAGE": "execution.execution_data",
    "DataKey": "execution.execution_data",
    "ExecutionData": "execution.execution_data",
    "FINAL_RESPONSE": "execution.execution_data",
    "LLM_RESPONSE": "execution.execution_data",
    "TOOL_ARGS": "execution.execution_data",
    "ExecutionFrame": "execution.execution_frame",
    "AGENT_NAME": "execution.execution_services",
    "ExecutionServices": "execution.execution_services",
    "ServiceKey": "execution.execution_services",
    "AbilityExecutor": "execution.executor",
    "HITLWaitResult": "execution.executor",
    "HitlOutcome": "execution.executor",
    "LocalAbilityExecutor": "execution.executor",
    "HookContext": "execution.hook_context",
    "HookRunner": "execution.hook_runner",
    "HookListView": "execution.hook_store",
    "HookStore": "execution.hook_store",
    "Hook": "execution.hooks",
    "HookPoint": "execution.hooks",
    "HookResult": "execution.hooks",
    "HookScope": "execution.hooks",
    "AbilityInvocation": "execution.invocation",
    # ---- 权限 ----
    "CommandApprovalHook": "permissions.command_safety",
    "CommandSafetyCategory": "permissions.command_safety",
    "CommandSafetyChecker": "permissions.command_safety",
    "CommandSafetyVerdict": "permissions.command_safety",
    "DEFAULT_DANGEROUS_COMMANDS": "permissions.command_safety",
    "DEFAULT_DANGEROUS_SUB_COMMANDS": "permissions.command_safety",
    "DEFAULT_SAFE_COMMANDS": "permissions.command_safety",
    "DEFAULT_SAFE_SUB_COMMANDS": "permissions.command_safety",
    "AccessApprovalHook": "permissions.fs_permissions",
    "FSPermissionChecker": "permissions.fs_permissions",
    "WriteApprovalHook": "permissions.fs_permissions",
    "HITLApprovalHook": "permissions.hitl",
    "HITLRequiredAbility": "permissions.hitl",
}
"""公开名字 → 归属模块（相对本包）。这是本包唯一的导出清单。"""

__all__ = sorted(_MODULE_OF)
"""包级公开面：由 :data:`_MODULE_OF` 派生，避免手工维护第二份清单。"""

_SUBPACKAGES = ("ability", "execution", "loading", "permissions")
"""本包的主题子包名（``import ghrah.scheme_ability`` 之后可直接点出）。"""

_PLAIN_MODULES = ("agent_manifest", "agent_resolver", "errors", "kinds", "paths")
"""跨主题的单文件模块（不归属任一子包，故平铺在包根）。"""


def __getattr__(name: str) -> Any:
    """按需加载并返回包级公开名字（PEP 562）。

    场景：外部代码 ``from ghrah.scheme_ability import AbilityDefinition`` 或
    ``ghrah.scheme_ability.loading`` 时，把加载延迟到这一刻，避免导入期拉起整棵子模块树。

    Args:
        name: 被访问的属性名。

    Returns:
        子模块里的同名对象；对子包 / 单文件模块名则返回模块对象本身。

    Raises:
        AttributeError: 名字不在索引里（拼错，或不属于本包的公开面）。
    """
    suffix = _MODULE_OF.get(name)
    if suffix is not None:
        value = getattr(import_module(f"{__name__}.{suffix}"), name)
        globals()[name] = value  # 缓存：后续访问走正常属性查找
        return value
    if name in _SUBPACKAGES or name in _PLAIN_MODULES:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """列出可访问的名字，让交互式补全覆盖惰性索引。

    Returns:
        已加载的全局名字与索引名字的并集（排序后）。
    """
    return sorted({*globals(), *_MODULE_OF, *_SUBPACKAGES, *_PLAIN_MODULES})
