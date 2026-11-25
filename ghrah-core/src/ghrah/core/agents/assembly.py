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
- **控制成员照常解析**：``conversation`` / ``end_task`` 由启动核定分到 control
  通道（不进授权账本、不受授权投影裁剪），但必须真正进装配结果——模板选了它们，
  Agent 才有对应的循环步骤。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from ghrah.protocol.views.core import AgentAssemblySpec, AgentMemberPermissions

from ghrah.ability.access.startup import CONTROL_HANDLERS
from ghrah.ability.member import AbilityType, Member, MemberAxes, MemberError, MemberType
from ghrah.ability.source import BuiltinAbilitySource
from ghrah.core.config.builders import build_model_overrides_from_dict, build_window_from_dict
from ghrah.manifest.resolver import ResolvedAbility, ResolvedAgent
from ghrah.manifest.types import PermissionFlags, SurfaceConfig
from ghrah.types.config_types import AgentConfig, ContextConfig, ModelOverrides, WindowConfig

__all__ = [
    "AssemblyError",
    "SEARCH_TOOLS_MEMBER",
    "config_from_snapshot",
    "config_to_snapshot",
    "member_axes_for_creation",
    "member_axes_from_spec",
    "normalize_snapshot_members",
    "parse_spec_member",
    "resolve_assembly",
    "resolve_members",
    "surface_from_snapshot",
]

SEARCH_TOOLS_MEMBER: str = "ghrah.core.search_tools"
"""内置发现入口成员全名。

实例创建/恢复时它的两轴固定为「不可见但绑定」：必须常驻模型上下文
（否则模型没有发现入口），但不进 ``search_tools`` 自己的发现结果。
"""


def member_axes_for_creation(member: Member) -> MemberAxes:
    """返回实例创建时落快照的成员轴。

    ``search_tools`` 固定为 ``visible=false, bind=true``；其余成员沿用装配
    输入给出的轴（可见性与绑定相互独立）。
    """
    if member.member == SEARCH_TOOLS_MEMBER:
        return MemberAxes(visible=False, bind=True)
    return MemberAxes(visible=member.axes.visible, bind=member.axes.bind)


def normalize_snapshot_members(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规范化准备写入快照的成员申请（引用态 wire 形状）。

    仅改写 ``search_tools`` 的 ``axes``（保持 wire 的 ``visible`` /
    ``available`` 键名），其余成员原样保留。
    """
    normalized: list[dict[str, Any]] = []
    for item in items:
        entry = dict(item)
        if str(entry.get("member") or "") == SEARCH_TOOLS_MEMBER:
            axes = dict(entry.get("axes") or {})
            axes["visible"] = False
            if "bind" in axes:
                axes["bind"] = True
            else:
                axes["available"] = True
            entry["axes"] = axes
        normalized.append(entry)
    return normalized


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


def _is_control_member(manifest: Any) -> bool:
    """控制成员（conversation / end_task 这类）由装配边界自己加，不接受显式请求。"""
    handler = getattr(manifest.implementation, "handler", None)
    return handler in CONTROL_HANDLERS and str(manifest.full_name).startswith("ghrah.core.")


def _resolve_member(
    source: BuiltinAbilitySource,
    member: str,
    request: AgentMemberPermissions | None,
    *,
    version: str | None = None,
) -> ResolvedAbility:
    """加载一个成员并把 L2 申请合并到包声明上。

    控制成员（``conversation`` / ``end_task``）与普通成员一样在这里解析：启动核定
    会把它们分到 control 通道（不写授权账本），所以模板里选中它们必须真正进
    装配结果，不能在这里丢掉。

    Args:
        member: 成员全名（``<package>.<key>``）。
        version: 能力件版本；内核件为 ``None``。多版本并存时由它决定读哪一层目录。
    """
    try:
        manifest = source.load_ability(member, version=version)
    except Exception as error:  # noqa: BLE001 — 统一折成装配错误
        # 单层身份包（安装槽 <ns>/<name>/，identity 省略 name）在能力来源里按
        # 旧两段名登记；三段规范名先按原样试，失败再回退一次。
        parts = member.split(".")
        fallback = f"{parts[0]}.{parts[-1]}" if len(parts) == 3 and parts[0] == parts[1] else None
        if fallback is None or fallback == member:
            raise AssemblyError(f"member not resolvable: {member}: {error}") from error
        try:
            manifest = source.load_ability(fallback, version=version)
        except Exception as retry_error:  # noqa: BLE001
            raise AssemblyError(f"member not resolvable: {member}: {retry_error}") from retry_error
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
    items = [item.model_dump() for item in spec.members]
    abilities = resolve_members(source, items)
    surface = (
        SurfaceConfig.model_validate(spec.config.surface.model_dump())
        if spec.config is not None and spec.config.surface is not None
        else None
    )
    member_axes = member_axes_from_spec(items)
    return ResolvedAgent(
        config=config, abilities=abilities, surface=surface, member_axes=member_axes
    )


def member_axes_from_spec(items: list[dict[str, Any]]) -> dict[str, MemberAxes]:
    """从装配规格/快照成员里抽出「成员全名 → 两轴」。

    只收集**显式给了 axes** 的条目：手写规格（测试/旧调用方）不带轴时返回空
    映射，装配层据此走"全部可见成员常驻"的旧口径；Subject 渲染出的规格每条都
    带轴，于是绑定集合完全由创建时的选择决定。

    整包条目（``whole_container``）在装配前已展开，不应出现在快照里；真出现时
    跳过，避免用包名当成员名建轴。

    ``search_tools`` 的轴统一规范为「不可见但绑定」（见
    :func:`member_axes_for_creation`）：只要该条目落在已提供轴的规格里，
    就不沿用调用方给出的值。

    Args:
        items: 装配输入或实例快照里的成员数组。

    Returns:
        成员全名到轴的映射；未显式提供轴的规格返回空映射。
    """
    if not any(item.get("axes") for item in items):
        return {}
    axes: dict[str, MemberAxes] = {}
    for item in items:
        if bool(item.get("whole_container")):
            continue
        try:
            entity = parse_spec_member(item)
        except AssemblyError:
            continue
        axes[entity.member] = member_axes_for_creation(entity)
    return axes


def parse_spec_member(item: dict[str, Any]) -> Member:
    """把装配规格里的一条成员申请解析成 Core 的成员实体（引用态）。

    规格来自 Subject（``AgentAssemblyMember`` 的字典形态）：只带成员全名、类型、
    版本与两条轴。包身份由全名切分，跨字段约束（类型、版本层、轴一致性）在这里
    统一校验——``Member`` 是 Core 内部实体，不进入 wire 契约。

    Args:
        item: 规格里的一条成员申请。

    Returns:
        已校验的成员实体；``data`` 为空（引用态）。

    Raises:
        AssemblyError: 全名缺失，或类型/版本层/轴不成立。
    """
    full_name = str(item.get("member") or "")
    if not full_name:
        raise AssemblyError("member entry without a name in snapshot")
    raw_axes = item.get("axes") or {}
    raw_type = str(item.get("ability_type") or "external")
    version = item.get("version")
    # wire 默认 ability_type 是 kernel，而带版本的成员必然来自安装根：显式版本
    # 优先于默认标记，避免「kernel + version」这种自相矛盾的申请被当成非法。
    if raw_type == "kernel" and version is not None:
        raw_type = "external"
    try:
        return Member.reference(
            member=full_name,
            type=cast("MemberType", str(item.get("type") or "operation")),
            version=version,
            ability_type=cast("AbilityType", raw_type),
            visible=bool(raw_axes.get("visible", True)),
            bind=bool(raw_axes.get("available", raw_axes.get("bind", True))),
        )
    except MemberError as error:
        raise AssemblyError(f"invalid member entry {full_name!r}: {error}") from error


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
        raw = item.get("permissions")
        request = AgentMemberPermissions.model_validate(raw) if raw is not None else None
        if bool(item.get("whole_container")):
            # 「整包」：按包名展开成全部成员（控制成员也进，由启动核定分流）。
            package = str(item.get("member") or "")
            if not package:
                raise AssemblyError("member entry without a name in snapshot")
            try:
                manifests = source.load_package_members(package)
            except Exception as error:  # noqa: BLE001 — 统一折成装配错误
                raise AssemblyError(f"package not resolvable: {package}: {error}") from error
            for manifest in manifests:
                resolved.append(_resolve_member(source, manifest.full_name, request))
            continue
        # 入参先过 Core 的成员实体：全名/类型/版本层/轴在这里统一校验一次。
        entity = parse_spec_member(item)
        resolved.append(_resolve_member(source, entity.member, request))
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
