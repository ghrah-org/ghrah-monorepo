# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""模板 → ``AgentAssemblySpec`` 的渲染器（纯函数，无 IO）。

这是「模板只做创建时填充」的唯一翻译点。输入是模板的**申请**与已授予事实，
输出是端口装配输入 + 待写入的授权计划 + 检查报告。

三条口径来自现有代码，不靠猜：

1. **成员全名 = ``<package>.<operation_key>``**。依据是 Core 的启动核定
   ``_resolved_operation``（``ability/access/startup.py:1118-1127``）把
   ``package`` 与 ``member`` 拼成 ``load_ability`` 的入参；因此
   ``members[]`` 只承载**操作成员**。
2. **技能与脚本不进 ``members[]``**：它们由授权行的 ``axes`` 表达，地址写法是
   ``skill/<key>`` / ``skill/<key>/scripts/<key>``（``MemberAxes`` 与
   ``facts.py``）。把它们塞进 ``members[]`` 会让 Core 去 ``load_ability``
   一个加载不到的名字。
3. **未授予的申请不静默丢弃**：留在报告的 ``not_granted`` / ``missing_packages``
   里，供检查面展示；这正是「模板申请 vs 当前授权」的差异。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ghrah.protocol.views.core import (
    AgentAssemblyConfig,
    AgentAssemblyContext,
    AgentAssemblyLimits,
    AgentAssemblyMember,
    AgentAssemblyModel,
    AgentAssemblyPermission,
    AgentAssemblyPrompt,
    AgentAssemblySpec,
    AgentMemberAxes,
    AgentMemberPermissions,
    AgentSurfaceSpec,
)

from ghrah.subject.template_store.models import AgentTemplatePathRequest, AgentTemplateRecord

__all__ = [
    "GrantedPackage",
    "GrantPlan",
    "RenderError",
    "RenderReport",
    "RenderedAssembly",
    "render_assembly",
]


class RenderError(ValueError):
    """模板还不足以创建 Agent（缺模型、空提示词、workspace 角色解析不到）。

    ``code`` 是稳定的领域码，供 REST 映射：``assembly_not_ready`` /
    ``missing_workspace`` / ``invalid_path``。
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class GrantedPackage:
    """一个已授予包的事实；由调用方从端口的能力视图读出，渲染器不自己查。"""

    package: str
    alias: str
    instance_id: str
    operations: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    scripts: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class GrantPlan:
    """一次待写入的授权事实（调用方转成 ``GrantAbilityRequest``）。

    ``operations``/``skills``/``scripts`` 是授予集合；``axes`` 是各成员的
    可见/可用显式事实（地址写法见模块 docstring）；``allowed_roots`` 是硬沙盒根。
    """

    package: str
    alias: str
    instance_id: str
    operations: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    scripts: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    axes: Mapping[str, tuple[bool, bool]] = field(default_factory=dict)
    allowed_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderReport:
    """渲染时的差异清单；不影响 ``spec`` 的合法性，供检查面展示。

    这里**不判定外部根**：v3 的路径申请用 ``workspace_role + subpath`` 表达，
    解析结果必然落在已登记 workspace 内；「绝对根落在所有 workspace 之外」只出现
    在旧 Manifest 的导入路径上（迁移脚本负责 ``requires_path_review``）。
    """

    missing_packages: tuple[str, ...] = ()
    not_granted: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderedAssembly:
    """渲染结果：装配输入 + 授权计划 + 报告。"""

    spec: AgentAssemblySpec
    grants: tuple[GrantPlan, ...]
    report: RenderReport


def _resolve_roots(
    requests: list[AgentTemplatePathRequest],
    workspaces: Mapping[str, str],
) -> list[tuple[AgentTemplatePathRequest, str]]:
    """把 ``workspace_role + subpath`` 解析成绝对根；任何一条失败即 fail-closed。"""
    resolved: list[tuple[AgentTemplatePathRequest, str]] = []
    for item in requests:
        root = workspaces.get(item.workspace_role)
        if root is None:
            raise RenderError("missing_workspace", item.workspace_role)
        base = root.rstrip("/\\")
        subpath = item.subpath.strip()
        if subpath in {"", "."}:
            absolute = base
        else:
            if subpath.startswith(("/", "\\")) or ".." in subpath.split("/"):
                raise RenderError("invalid_path", f"{item.workspace_role}:{item.subpath}")
            absolute = f"{base}/{subpath.strip('/')}"
        resolved.append((item, absolute))
    return resolved


def _applicable(
    requests: list[tuple[AgentTemplatePathRequest, str]],
    *,
    package: str,
) -> list[tuple[AgentTemplatePathRequest, str]]:
    """挑出作用于该包成员的路径申请（agent 级对所有包生效）。"""
    return [
        (item, root) for item, root in requests if item.scope == "agent" or item.package == package
    ]


def _member_permissions(
    requests: list[tuple[AgentTemplatePathRequest, str]],
    *,
    package: str,
) -> AgentMemberPermissions | None:
    """把一个成员的路径申请折成 L2 权限；无申请时返回 ``None``（沿用包声明）。"""
    applicable = _applicable(requests, package=package)
    if not applicable:
        return None
    allowed = [root for item, root in applicable if not item.deny]
    denied = [root for item, root in applicable if item.deny]
    writes = any(not item.deny and item.access == "write" for item, _ in applicable)
    return AgentMemberPermissions(
        allowed_paths=allowed or None,
        denied_paths=denied or None,
        fs_write=True if writes else None,
    )


def _package_axes(
    requested_operations: list[tuple[str, bool, bool]],
    requested_skills: list[tuple[str, bool, list[tuple[str, bool, bool]]]],
) -> dict[str, tuple[bool, bool]]:
    """把申请的可见/可用折成 axes 地址表；脚本的 ``exec`` 进入 available。"""
    axes: dict[str, tuple[bool, bool]] = {}
    for key, visible, available in requested_operations:
        axes[f"operations/{key}"] = (visible, available)
    for key, visible, scripts in requested_skills:
        axes[f"skill/{key}"] = (visible, visible)
        for script, script_visible, script_exec in scripts:
            axes[f"skill/{key}/scripts/{script}"] = (script_visible, script_exec)
    return axes


def _config(template: AgentTemplateRecord) -> AgentAssemblyConfig:
    """模板五组 → 装配输入的 config 段。"""
    if not template.model.endpoint_name:
        raise RenderError("assembly_not_ready", "model.endpoint_name is required")
    prompt = template.prompt.system_prompt
    if not prompt.strip():
        raise RenderError("assembly_not_ready", "prompt.system_prompt is empty")
    policy = template.context_policy
    persistence = policy.overrides.persistence if policy.overrides is not None else None
    context = None
    if policy.window is not None or persistence is not None:
        context = AgentAssemblyContext(
            window=policy.window,
            persistence=(
                {
                    "type": persistence.type,
                    "compress": persistence.compress,
                    "auto_persist": persistence.auto_persist,
                }
                if persistence is not None
                else None
            ),
        )
    request = template.permission_request
    return AgentAssemblyConfig(
        description=template.metadata.description,
        model=AgentAssemblyModel(
            endpoint_name=template.model.endpoint_name,
            model_overrides=template.model.model_overrides,
        ),
        prompt=AgentAssemblyPrompt(system_prompt=prompt),
        limits=AgentAssemblyLimits(
            max_iterations=policy.max_iterations,
            communication_timeout=policy.communication_timeout,
        ),
        context=context,
        surface=AgentSurfaceSpec.model_validate(template.ability_request.surface.model_dump()),
        mcp_refs=list(template.ability_request.mcp_refs),
        permission=AgentAssemblyPermission(
            auto_approve=list(request.auto_approve),
            require_approval_by_default=request.require_approval_by_default,
            safe_extra_commands=list(request.safe_extra_commands),
            safe_extra_sub_commands=request.safe_extra_sub_commands,
        ),
    )


def render_assembly(
    template: AgentTemplateRecord,
    *,
    agent_name: str,
    workspaces: Mapping[str, str],
    granted: list[GrantedPackage],
) -> RenderedAssembly:
    """把模板渲染成装配输入 + 授权计划。

    Args:
        template: 指定 revision 的模板记录（调用方按 ``revision`` 读取）。
        agent_name: 项目内唯一的实例名（不是模板名）。
        workspaces: ``workspace_role`` → 绝对根；缺失即 ``missing_workspace``。
        granted: 该项目当前的已授予包事实（来自端口能力视图）。

    Returns:
        装配输入、逐包授权计划与差异报告。

    Raises:
        RenderError: 模板尚不足以创建（``assembly_not_ready``）、workspace 角色
            解析不到（``missing_workspace``），或路径非法（``invalid_path``）。
    """
    config = _config(template)
    resolved_paths = _resolve_roots(list(template.path_request), workspaces)

    granted_by_name = {item.package: item for item in granted}
    members: list[AgentAssemblyMember] = []
    grants: list[GrantPlan] = []
    missing: list[str] = []
    not_granted: list[str] = []

    for request in template.ability_request.packages:
        fact = granted_by_name.get(request.package)
        if fact is None:
            missing.append(request.package)
            continue
        granted_ops = set(fact.operations)
        if request.whole_container:
            selected_ops = sorted(granted_ops)
            requested_ops: list[tuple[str, bool, bool]] = []
        else:
            requested_ops = [
                (item.key, item.visible, item.available) for item in request.operations
            ]
            selected_ops = []
            for key, _, _ in requested_ops:
                if key in granted_ops:
                    selected_ops.append(key)
                else:
                    not_granted.append(f"{request.package}.{key}")
        # 技能与脚本不进 members[]，只进授权行的 axes（见模块 docstring）。
        requested_skills = [
            (item.key, item.visible, [(s.key, s.visible, s.exec) for s in item.scripts])
            for item in request.skills
        ]
        axes = _package_axes(requested_ops, requested_skills)
        applicable = _applicable(resolved_paths, package=request.package)
        allowed_roots = tuple(dict.fromkeys(root for item, root in applicable if not item.deny))
        permissions = _member_permissions(resolved_paths, package=request.package)
        for key in selected_ops:
            members.append(
                AgentAssemblyMember(
                    member=f"{request.package}.{key}",
                    permissions=permissions,
                    axes=AgentMemberAxes(
                        visible=axes.get(f"operations/{key}", (True, True))[0],
                        available=axes.get(f"operations/{key}", (True, True))[1],
                    ),
                )
            )
        grants.append(
            GrantPlan(
                package=request.package,
                alias=fact.alias,
                instance_id=fact.instance_id,
                operations=tuple(selected_ops),
                skills=tuple(item.key for item in request.skills),
                scripts={item.key: tuple(s.key for s in item.scripts) for item in request.skills},
                axes=axes,
                allowed_roots=allowed_roots,
            )
        )

    spec = AgentAssemblySpec(
        name=agent_name,
        config=config,
        members=members,
    )
    return RenderedAssembly(
        spec=spec,
        grants=tuple(grants),
        report=RenderReport(
            missing_packages=tuple(missing),
            not_granted=tuple(not_granted),
        ),
    )
