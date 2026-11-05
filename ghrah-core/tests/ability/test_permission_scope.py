# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""有效权限范围：声明合并与范围外兜底的确定语义。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ghrah.ability.permissions.scope import (
    EffectivePermission,
    resolve_effective_permission,
)
from ghrah.manifest.types import PermissionFlags


def test_undeclared_scope_is_none_not_empty(tmp_path: Path) -> None:
    """未声明范围是 ``None``（沙盒内不限制），与“声明了空集合”区分。"""
    scope = resolve_effective_permission(PermissionFlags(), workspace_root=str(tmp_path))
    assert scope.allowed_paths is None
    assert scope.allowed_commands is None
    assert scope.unbounded_paths and scope.unbounded_commands


def test_path_scopes_intersect_between_declaration_layers(tmp_path: Path) -> None:
    """包声明与模板声明取交集：更窄的一层生效。"""
    package = PermissionFlags(allowed_paths=["{{workspace}}"])
    request = PermissionFlags(allowed_paths=["./src"])
    scope = resolve_effective_permission(package, request, workspace_root=str(tmp_path))
    assert scope.allowed_paths == (str((tmp_path / "src").resolve()),)


def test_single_layer_scope_is_kept(tmp_path: Path) -> None:
    """只有一层声明时直接采用，不做无意义的求交。"""
    request = PermissionFlags(allowed_paths=["./src"])
    scope = resolve_effective_permission(PermissionFlags(), request, workspace_root=str(tmp_path))
    assert scope.allowed_paths == (str((tmp_path / "src").resolve()),)


def test_disjoint_path_scopes_become_empty_tuple(tmp_path: Path) -> None:
    """两层声明无交集时得到空范围：不自动批准任何路径。"""
    package = PermissionFlags(allowed_paths=["./src"])
    request = PermissionFlags(allowed_paths=["./tests"])
    scope = resolve_effective_permission(package, request, workspace_root=str(tmp_path))
    assert scope.allowed_paths == ()
    assert not scope.unbounded_paths


def test_denied_paths_union_and_templates(tmp_path: Path) -> None:
    """拒绝范围取并集，模板变量按工作区展开。"""
    package = PermissionFlags(denied_paths=["{{workspace}}/secrets"])
    request = PermissionFlags(denied_paths=["./private"])
    scope = resolve_effective_permission(package, request, workspace_root=str(tmp_path))
    assert scope.denied_paths == (
        str((tmp_path / "secrets").resolve()),
        str((tmp_path / "private").resolve()),
    )


def test_command_scopes_intersect_with_two_segment_entries() -> None:
    """命令范围取交集，保留更窄的“命令 + 子命令”条目。"""
    package = PermissionFlags(allowed_commands=["git", "ls"])
    request = PermissionFlags(allowed_commands=["git status"])
    scope = resolve_effective_permission(package, request)
    assert scope.allowed_commands == ("git status",)


def test_disjoint_command_scopes_become_empty_tuple() -> None:
    """命令范围无交集时得到空范围。"""
    package = PermissionFlags(allowed_commands=["git"])
    request = PermissionFlags(allowed_commands=["npm test"])
    scope = resolve_effective_permission(package, request)
    assert scope.allowed_commands == ()


def test_denied_commands_union() -> None:
    """拒绝命令取并集。"""
    package = PermissionFlags(denied_commands=["rm -rf"])
    request = PermissionFlags(denied_commands=["git push"])
    scope = resolve_effective_permission(package, request)
    assert scope.denied_commands == ("rm -rf", "git push")


def test_boolean_rules_and_read_only_implies_no_write() -> None:
    """只读与审批取 OR，权限位不能由模板扩大。"""
    package = PermissionFlags(fs_write=True, require_hitl=True)
    request = PermissionFlags(fs_read_only=True, shell_access=True)
    scope = resolve_effective_permission(package, request)
    assert scope.require_hitl is True
    assert scope.fs_read_only is True
    assert scope.fs_write is False
    assert scope.shell_access is False


def test_unbounded_properties() -> None:
    """范围属性的直观语义。"""
    bounded = EffectivePermission(allowed_paths=(), allowed_commands=())
    assert not bounded.unbounded_paths and not bounded.unbounded_commands


def test_template_cannot_add_undeclared_permission_bits() -> None:
    scope = resolve_effective_permission(
        PermissionFlags(), PermissionFlags(fs_write=True, shell_access=True, net_access=True)
    )
    assert not scope.fs_write and not scope.shell_access and not scope.net_access


def test_omitted_permission_bits_inherit_and_explicit_false_narrows() -> None:
    declaration = PermissionFlags(fs_write=True, shell_access=True, net_access=True)
    inherited = resolve_effective_permission(declaration, PermissionFlags(require_hitl=True))
    assert inherited.fs_write and inherited.shell_access and inherited.net_access
    narrowed = resolve_effective_permission(
        declaration, PermissionFlags(fs_write=False, shell_access=False, net_access=False)
    )
    assert not narrowed.fs_write and not narrowed.shell_access and not narrowed.net_access


@pytest.mark.parametrize("invalid", ["planner", "", "ABCDEF0123456789ABCDEF0123456789AB"])
def test_policy_agent_cannot_be_a_display_name(invalid):
    from ghrah.ability.access import GrantRule

    with pytest.raises(ValueError):
        GrantRule(package="ghrah.fs", agent=invalid)
