# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""把端口的 ``AgentAssemblySpec`` 解析成 spawn 需要的 ``ResolvedAgent``。

这是装配输入 v3 的落点：旧链路读 YAML 模板再解析，新链路直接
吃 Subject 渲染好的规格——配置来自 ``spec.config``，成员来自 ``spec.members``。

口径要点：

- **成员权限按「未申请」处理**：``AgentMemberPermissions`` 里为 ``None`` 的维度
  不写进 ``PermissionFlags``，让 ``model_fields_set`` 保留下来，交给
  ``resolve_effective_permission`` 沿用包声明上限；写成默认值等于把「未申请」
  误判成「显式收紧」。
- **控制成员不由规格传递**：``conversation`` / ``end_task`` 这类由 Core 隐式补齐，
  出现在 ``members`` 里说明装配方算错了成员集合，直接拒绝。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ghrah.protocol.views.core import AgentAssemblySpec, AgentMemberPermissions

from ghrah.ability.access.startup import CONTROL_HANDLERS
from ghrah.ability.source import BuiltinAbilitySource
from ghrah.core.config.builders import build_model_overrides_from_dict, build_window_from_dict
from ghrah.manifest.resolver import ResolvedAbility, ResolvedAgent
from ghrah.manifest.types import PermissionFlags, SurfaceConfig
from ghrah.types.config_types import AgentConfig, ContextConfig, ModelOverrides, WindowConfig

__all__ = [
    "AssemblyError",
    "config_from_snapshot",
    "config_to_snapshot",
    "resolve_assembly",
    "resolve_members",
    "surface_from_snapshot",
]

_PERMISSION_FIELDS = (
    "require_hitl",
    "fs_read_only",
    "fs_write",
    "net_access",
    "shell_access",
    "allowed_paths",
    "denied_paths",
    "allowed_commands",
    "denied_commands",
)


class AssemblyError(ValueError):
    """装配输入不可解析（成员缺失、控制成员越界、快照模式带配置等）。"""


def _permissions(request: AgentMemberPermissions | None) -> PermissionFlags | None:
    """把线上的成员申请折成 ``PermissionFlags``，只保留显式给出的维度。

    ``None`` 表示「该成员没有任何 L2 申请」；显式给出的布尔/列表才进入
    ``model_fields_set``，从而在 ``resolve_effective_permission`` 里被当作收紧项。
    """
    if request is None:
        return None
    provided = {
        name: value for name in _PERMISSION_FIELDS if (value := getattr(request, name)) is not None
    }
    if not provided:
        return None
    return PermissionFlags(**provided)


def _build_context(spec: AgentAssemblySpec) -> ContextConfig | None:
    """模板的持久化三键 + Subject 补齐的 ``root_dir``/``run_id``。"""
    config = spec.config
    if config is None or config.context is None or config.context.persistence is None:
        return None
    raw: dict[str, Any] = dict(config.context.persistence)
    return ContextConfig(
        persistence_type=raw.get("type"),
        persistence_root_dir=raw.get("root_dir"),
        persistence_compress=raw.get("compress", True),
        auto_persist=raw.get("auto_persist", False),
        persistence_run_id=raw.get("run_id"),
    )


def _build_config(spec: AgentAssemblySpec, workspace_root: str | None) -> AgentConfig:
    """从规格的 ``config`` 段构造 ``AgentConfig``。"""
    config = spec.config
    if config is None:  # pragma: no cover - 由 AgentAssemblySpec 的校验保证
        raise AssemblyError("assembly spec without config cannot be created")
    window = build_window_from_dict(config.context.window) if config.context else None
    overrides = (
        build_model_overrides_from_dict(config.model.model_overrides)
        if config.model.model_overrides
        else None
    )
    return AgentConfig(
        name=spec.name,
        agent_id=spec.agent_id,
        endpoint_name=config.model.endpoint_name,
        description=config.description,
        system_prompt=config.prompt.system_prompt,
        max_iterations=config.limits.max_iterations,
        communication_timeout=config.limits.communication_timeout,
        window=window,
        context=_build_context(spec),
        model_overrides=overrides,
        workspace_root=workspace_root,
    )


def _resolve_member(
    source: BuiltinAbilitySource,
    member: str,
    request: AgentMemberPermissions | None,
) -> ResolvedAbility:
    """加载一个成员并把 L2 申请合并到包声明上。"""
    try:
        manifest = source.load_ability(member)
    except Exception as error:  # noqa: BLE001 — 统一折成装配错误
        raise AssemblyError(f"member not resolvable: {member}: {error}") from error
    handler = getattr(manifest.implementation, "handler", None)
    if handler in CONTROL_HANDLERS and manifest.full_name.startswith("ghrah.core."):
        raise AssemblyError(f"control member must not be passed explicitly: {manifest.full_name}")
    declared = manifest.metadata.permissions
    requested = _permissions(request)
    return ResolvedAbility(
        ability_name=manifest.full_name,
        tool_schema=manifest.tool,
        permissions=declared.merge(requested),
        implementation=manifest.implementation,
        hooks=manifest.hooks,
        requested_permissions=requested,
        declared_permissions=declared,
    )


def resolve_assembly(
    spec: AgentAssemblySpec,
    source: BuiltinAbilitySource,
    *,
    workspace_root: str | None = None,
) -> ResolvedAgent:
    """把装配输入解析成可 spawn 的 ``ResolvedAgent``。

    Args:
        spec: 端口装配输入；恢复模式（``agent_id`` 非空）不应走到这里，
            恢复直接从 Core 的实例快照读配置。
        source: 显式选择的能力来源，成员定义与包声明上限都从它取。
        workspace_root: 本项目工作区绝对根；写入 ``AgentConfig`` 供权限解析使用。

    Returns:
        配置 + 逐成员能力 + 工具表面。

    Raises:
        AssemblyError: 成员解析失败、控制成员越界、或恢复模式误入本函数。
    """
    if spec.agent_id:
        raise AssemblyError("recovery mode must read the Core snapshot, not resolve a spec")
    config = _build_config(spec, workspace_root)
    abilities = resolve_members(source, [item.model_dump() for item in spec.members])
    surface = (
        SurfaceConfig.model_validate(spec.config.surface.model_dump())
        if spec.config is not None and spec.config.surface is not None
        else None
    )
    return ResolvedAgent(config=config, abilities=abilities, surface=surface)


def resolve_members(
    source: BuiltinAbilitySource,
    members: list[dict[str, Any]],
) -> list[ResolvedAbility]:
    """按快照里的成员申请重建能力列表（恢复路径用；成员定义来自能力来源）。

    Args:
        source: 能力来源；成员定义从它加载，**不从 Subject 的模板读**。
        members: 快照里冻结的成员申请，元素形如
            ``{"member": str, "permissions": dict | None, "axes": dict | None}``。

    Returns:
        与请求顺序一致的 ``ResolvedAbility`` 列表。
    """
    resolved: list[ResolvedAbility] = []
    for item in members:
        member = str(item.get("member") or "")
        if not member:
            raise AssemblyError("member entry without a name in snapshot")
        raw = item.get("permissions")
        request = AgentMemberPermissions.model_validate(raw) if raw is not None else None
        resolved.append(_resolve_member(source, member, request))
    return resolved


def config_to_snapshot(config: AgentConfig) -> dict[str, Any]:
    """``AgentConfig`` → 可 JSON 序列化的快照形态（嵌套 dataclass 一并展开）。"""
    return {
        "name": config.name,
        "agent_id": config.agent_id,
        "endpoint_name": config.endpoint_name,
        "description": config.description,
        "system_prompt": config.system_prompt,
        "max_iterations": config.max_iterations,
        "communication_timeout": config.communication_timeout,
        "window": asdict(config.window) if config.window is not None else None,
        "context": asdict(config.context) if config.context is not None else None,
        "model_overrides": (
            asdict(config.model_overrides) if config.model_overrides is not None else None
        ),
        "workspace_root": config.workspace_root,
        "cluster_context_injection": config.cluster_context_injection,
        "environment_context_injection": config.environment_context_injection,
    }


def config_from_snapshot(data: dict[str, Any]) -> AgentConfig:
    """快照形态 → ``AgentConfig``；缺失的嵌套段按未声明处理。"""
    window = data.get("window")
    context = data.get("context")
    overrides = data.get("model_overrides")
    return AgentConfig(
        name=str(data.get("name") or ""),
        agent_id=str(data.get("agent_id") or ""),
        endpoint_name=data.get("endpoint_name"),
        description=str(data.get("description") or ""),
        system_prompt=str(data.get("system_prompt") or ""),
        max_iterations=int(data.get("max_iterations", 10)),
        communication_timeout=float(data.get("communication_timeout", 300.0)),
        window=WindowConfig(**window) if isinstance(window, dict) else None,
        context=ContextConfig(**context) if isinstance(context, dict) else None,
        model_overrides=ModelOverrides(**overrides) if isinstance(overrides, dict) else None,
        workspace_root=data.get("workspace_root"),
        cluster_context_injection=bool(data.get("cluster_context_injection", False)),
        environment_context_injection=bool(data.get("environment_context_injection", False)),
    )


def surface_from_snapshot(data: dict[str, Any] | None) -> SurfaceConfig | None:
    """快照里的表面覆盖 → ``SurfaceConfig``；``None`` 表示未覆盖。"""
    return SurfaceConfig.model_validate(data) if data is not None else None
