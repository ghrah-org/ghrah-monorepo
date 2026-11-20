# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""模板渲染器：申请 → 装配输入 + 授权计划的翻译口径。

钉住四件事：只写已授予成员、成员全名是 ``<package>.<operation>``、技能/脚本只
走 axes、路径申请落成 L2 权限且外部根要拦。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ghrah.subject.agents.render import GrantedPackage, RenderError, render_assembly
from ghrah.subject.template_store.models import AgentTemplateConfig

_WORKSPACES = {"default": "/ws", "shared": "/shared"}


def _template(**overrides: object) -> AgentTemplateConfig:
    payload: dict[str, object] = {
        "metadata": {"namespace": "writing", "name": "weekly", "title": "周报"},
        "model": {"endpoint_name": "default-llm", "model_overrides": {"temperature": 0.3}},
        "prompt": {"system_prompt": "先列事实"},
        "context_policy": {"max_iterations": -1, "communication_timeout": 120},
        "available_members": [
            {
                "namespace": "ghrah",
                "name": "fs",
                "member": "ghrah.fs.read_file",
                "type": "operation",
                "version": None,
                "ability_type": "kernel",
            },
            {
                "namespace": "ghrah",
                "name": "fs",
                "member": "ghrah.fs.write_file",
                "type": "operation",
                "version": None,
                "ability_type": "kernel",
            },
            {
                "namespace": "ghrah",
                "name": "fs",
                "member": "ghrah.fs.style",
                "type": "skill",
                "version": None,
                "ability_type": "kernel",
            },
        ],
        "permission_request": {"auto_approve": ["ghrah.fs.read_file"]},
        "path_request": [
            {
                "scope": "package",
                "package": "ghrah.fs",
                "workspace_role": "default",
                "subpath": "docs",
                "access": "write",
                "deny": False,
            }
        ],
    }
    payload.update(overrides)
    return AgentTemplateConfig.model_validate(payload)


def _granted(**overrides: object) -> GrantedPackage:
    payload: dict[str, object] = {
        "package": "ghrah.fs",
        "alias": "fs",
        "instance_id": "i" * 32,
        "operations": ("read_file", "write_file"),
        "skills": ("style",),
    }
    payload.update(overrides)
    return GrantedPackage(**payload)  # type: ignore[arg-type]


def test_only_granted_members_enter_the_spec() -> None:
    rendered = render_assembly(
        _template(),
        agent_name="weekly",
        workspaces=_WORKSPACES,
        granted=[_granted(operations=("read_file",))],
    )

    names = [item.member for item in rendered.spec.members]
    assert names == ["ghrah.fs.read_file", "ghrah.fs.style"]
    assert rendered.report.not_granted == ("ghrah.fs.write_file",)


def test_initial_bindings_mark_only_the_selected_members() -> None:
    """创建时选中的初始绑定写进成员轴：未选中的成员仍可见、只是不常驻。"""
    rendered = render_assembly(
        _template(),
        agent_name="weekly",
        workspaces=_WORKSPACES,
        granted=[_granted()],
        initial_bindings=("ghrah.fs.read_file",),
    )

    axes = {item.member: item.axes for item in rendered.spec.members}
    assert axes["ghrah.fs.read_file"].available is True
    assert axes["ghrah.fs.write_file"].available is False
    assert axes["ghrah.fs.write_file"].visible is True


def test_unknown_initial_binding_fails_closed() -> None:
    """初始绑定只能选这次真正装上的成员；写错名字要报错而不是静默忽略。"""
    with pytest.raises(RenderError) as error:
        render_assembly(
            _template(),
            agent_name="weekly",
            workspaces=_WORKSPACES,
            granted=[_granted()],
            initial_bindings=("ghrah.fs.ghost",),
        )
    assert error.value.code == "invalid_binding"
    assert "ghrah.fs.ghost" in error.value.detail


def test_missing_package_is_reported_not_fatal() -> None:
    rendered = render_assembly(
        _template(),
        agent_name="weekly",
        workspaces=_WORKSPACES,
        granted=[],
    )

    assert rendered.report.missing_packages == ("ghrah.fs",)
    assert rendered.spec.members == []
    assert rendered.grants == ()


def test_config_carries_limits_prompt_and_model() -> None:
    rendered = render_assembly(
        _template(), agent_name="weekly", workspaces=_WORKSPACES, granted=[_granted()]
    )

    config = rendered.spec.config
    assert config is not None
    assert config.model.endpoint_name == "default-llm"
    assert config.model.model_overrides == {"temperature": 0.3}
    assert config.prompt.system_prompt == "先列事实"
    assert config.limits.max_iterations == -1
    assert config.limits.communication_timeout == 120
    assert config.mcp_refs == []
    assert config.permission is not None
    assert config.permission.auto_approve == ["ghrah.fs.read_file"]


def test_missing_endpoint_is_not_ready() -> None:
    template = _template(model={"endpoint_name": None})
    with pytest.raises(RenderError) as error:
        render_assembly(template, agent_name="weekly", workspaces=_WORKSPACES, granted=[_granted()])
    assert error.value.code == "assembly_not_ready"


def test_empty_prompt_is_not_ready() -> None:
    template = _template(prompt={"system_prompt": "   "})
    with pytest.raises(RenderError) as error:
        render_assembly(template, agent_name="weekly", workspaces=_WORKSPACES, granted=[_granted()])
    assert error.value.code == "assembly_not_ready"


def test_unknown_workspace_role_fails_closed() -> None:
    template = _template(
        path_request=[
            {
                "scope": "agent",
                "workspace_role": "ghost",
                "subpath": ".",
                "access": "read",
                "deny": False,
            }
        ]
    )
    with pytest.raises(RenderError) as error:
        render_assembly(template, agent_name="weekly", workspaces=_WORKSPACES, granted=[_granted()])
    assert error.value.code == "missing_workspace"


def test_path_request_becomes_member_permissions() -> None:
    rendered = render_assembly(
        _template(), agent_name="weekly", workspaces=_WORKSPACES, granted=[_granted()]
    )

    permissions = rendered.spec.members[0].permissions
    assert permissions is not None
    assert permissions.allowed_paths == ["/ws/docs"]
    assert permissions.fs_write is True
    assert permissions.denied_paths is None
    assert rendered.grants[0].allowed_roots == ("/ws/docs",)


def test_deny_request_goes_to_denied_paths() -> None:
    template = _template(
        path_request=[
            {
                "scope": "agent",
                "workspace_role": "default",
                "subpath": "secrets",
                "access": "read",
                "deny": True,
            }
        ]
    )
    rendered = render_assembly(
        template, agent_name="weekly", workspaces=_WORKSPACES, granted=[_granted()]
    )

    permissions = rendered.spec.members[0].permissions
    assert permissions is not None
    assert permissions.denied_paths == ["/ws/secrets"]
    assert permissions.allowed_paths is None
    assert rendered.grants[0].allowed_roots == ()


@pytest.mark.parametrize("subpath", ["/etc", "../outside", "docs/../../etc"])
def test_subpath_cannot_escape_workspace(subpath: str) -> None:
    """``subpath`` 只能是 workspace 内相对路径；模板层已拒绝，渲染器不再放行。"""
    with pytest.raises(ValidationError):
        _template(
            path_request=[
                {
                    "scope": "agent",
                    "workspace_role": "default",
                    "subpath": subpath,
                    "access": "read",
                    "deny": False,
                }
            ]
        )


def test_explicit_members_pass_through_without_grant_view() -> None:
    """没有授权视图时，模板里逐个列出的成员原样交给 Core（落库不再有整包）。"""
    rendered = render_assembly(
        _template(), agent_name="weekly", workspaces=_WORKSPACES, granted=None
    )

    assert [item.member for item in rendered.spec.members] == [
        "ghrah.fs.read_file",
        "ghrah.fs.write_file",
        "ghrah.fs.style",
    ]
    assert rendered.report.not_granted == ()


def test_skills_enter_members_without_axes() -> None:
    rendered = render_assembly(
        _template(), agent_name="weekly", workspaces=_WORKSPACES, granted=[_granted()]
    )

    axes = rendered.grants[0].axes
    assert axes["skill/style"] == (True, True)
    # 模板不再携带脚本申请：脚本授权回到定义面默认。
    assert "skill/style/scripts/lint" not in axes
    skills = [item for item in rendered.spec.members if item.type == "skill"]
    assert [item.member for item in skills] == ["ghrah.fs.style"]
    assert rendered.grants[0].skills == ("style",)
    assert rendered.grants[0].scripts == {"style": ()}


def test_skill_only_package_renders_without_operations() -> None:
    """纯技能包（没有 operation）也要能渲染：技能绑定独立于 operation 循环。"""
    rendered = render_assembly(
        _template(
            available_members=[
                {
                    "namespace": "ghrah",
                    "name": "fs",
                    "member": "ghrah.fs.style",
                    "type": "skill",
                    "ability_type": "kernel",
                }
            ]
        ),
        agent_name="weekly",
        workspaces=_WORKSPACES,
        granted=[_granted(operations=(), skills=("style",))],
    )
    skills = [item for item in rendered.spec.members if item.type == "skill"]
    assert [item.member for item in skills] == ["ghrah.fs.style"]
    assert skills[0].axes.available is True


def test_packages_payload_is_no_longer_a_storage_shape() -> None:
    """落库只认成员数组：旧的包级入参在模型层被拒。"""
    with pytest.raises(ValidationError):
        _template(available_members={"packages": [{"package": "ghrah.fs"}]})
