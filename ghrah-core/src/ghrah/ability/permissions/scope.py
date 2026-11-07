# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""有效权限范围：把声明合并成检查器与执行接纳共用的确定形状。

这里是 2026-11-05 权限裁决的实现层：

- ``allowed_*`` 是**自动批准范围**，不是权限本身；
- ``denied_*`` 是硬拒绝，永不进入审批；
- ``require_hitl`` 只决定范围外的兜底是审批还是拒绝；
- 两个声明层（包内 ``PERMISSIONS`` 与 Agent 模板的 ``permissions``）合并时
  只能收紧：路径与命令范围取交集，拒绝范围取并集，审批与只读取 OR，写入/网络/shell 权限取 AND
  合并，``fs_read_only`` 生效时强制 ``fs_write`` 为假。

范围解析发生在启动物化阶段一次，之后的执行只读结果，不在调用路径重算声明。
``allowed_*`` 留空表示“未声明范围”：沙盒内不限制，与“声明了一个空集合”
（交集为空）是两种不同的事实，必须分开表达。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ghrah.ability.paths import is_subpath, resolve_fs_permission_paths
from ghrah.ability.permissions.command_scope import (
    command_entry_covers,
    command_in_scope,
    normalize_command_entry,
)
from ghrah.manifest.types import PermissionFlags

__all__ = [
    "EffectivePermission",
    "command_entry_covers",
    "command_in_scope",
    "normalize_command_entry",
    "resolve_effective_permission",
]


@dataclass(frozen=True)
class EffectivePermission:
    """一个成员的有效权限范围；``None`` 范围表示未声明（不限制）。

    Attributes:
        require_hitl: 范围外是否转人工审批；为假时范围外直接拒绝。
        fs_read_only: 只读要求；生效时 ``fs_write`` 强制为假。
        fs_write: 写能力申请。
        net_access: 网络能力申请。
        shell_access: 命令执行能力申请。
        allowed_paths: 自动批准的绝对路径根；``None`` 表示未声明范围。
        denied_paths: 硬拒绝的绝对路径根。
        allowed_commands: 自动批准的命令；``None`` 表示未声明范围。
        denied_commands: 硬拒绝的命令。
        sandbox_roots: 部署方指定的文件硬边界，缺省时使用工作区根。
    """

    require_hitl: bool = False
    fs_read_only: bool = False
    fs_write: bool = False
    net_access: bool = False
    shell_access: bool = False
    allowed_paths: tuple[str, ...] | None = None
    denied_paths: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] | None = None
    denied_commands: tuple[str, ...] = ()
    sandbox_roots: tuple[str, ...] | None = None

    @property
    def unbounded_paths(self) -> bool:
        """是否未声明路径范围（沙盒内不限制）。"""
        return self.allowed_paths is None

    @property
    def unbounded_commands(self) -> bool:
        """是否未声明命令范围。"""
        return self.allowed_commands is None


def resolve_effective_permission(
    declaration: PermissionFlags,
    request: PermissionFlags | None = None,
    *,
    workspace_root: str | None = None,
) -> EffectivePermission:
    """把包声明与 Agent 模板声明合并成有效权限范围。

    Args:
        declaration: 包内 ``PERMISSIONS``（能力实现的申请值）。
        request: Agent 模板对本次实例的申请；``None`` 表示未声明。
        workspace_root: 工作区绝对路径；用于展开 ``{{workspace}}`` 与 ``"."``。

    Returns:
        合并后的有效范围；两层路径/命令范围有交集为空时返回空元组
        （即“声明了但什么都不自动批准”），与未声明范围的 ``None`` 区分。
    """
    layers = [declaration] if request is None else [declaration, request]
    template_vars = {"workspace": workspace_root} if workspace_root else {}

    resolved: list[tuple[list[str] | None, list[str] | None]] = []
    for layer in layers:
        allowed, denied = resolve_fs_permission_paths(
            list(layer.allowed_paths) or None,
            list(layer.denied_paths) or None,
            template_vars,
            workspace_root or ".",
        )
        resolved.append(
            (
                _normalize_paths(allowed, workspace_root),
                _normalize_paths(denied, workspace_root),
            )
        )

    allowed_paths = _intersect_path_layers([item[0] for item in resolved])
    denied_paths = _union_layers([item[1] for item in resolved])

    allowed_commands = _intersect_command_layers([list(layer.allowed_commands) for layer in layers])
    denied_commands = _union_layers([list(layer.denied_commands) for layer in layers])

    require_hitl = any(layer.require_hitl for layer in layers)
    fs_read_only = any(layer.fs_read_only for layer in layers)

    def permitted(name: str) -> bool:
        ceiling = bool(getattr(declaration, name))
        if request is None or name not in request.model_fields_set:
            return ceiling
        return ceiling and bool(getattr(request, name))

    fs_write = permitted("fs_write")
    net_access = permitted("net_access")
    shell_access = permitted("shell_access")
    if fs_read_only:
        fs_write = False

    return EffectivePermission(
        require_hitl=require_hitl,
        fs_read_only=fs_read_only,
        fs_write=fs_write,
        net_access=net_access,
        shell_access=shell_access,
        allowed_paths=allowed_paths,
        denied_paths=tuple(denied_paths or ()),
        allowed_commands=allowed_commands,
        denied_commands=tuple(denied_commands or ()),
    )


def _normalize_paths(paths: list[str] | None, workspace: str | None) -> list[str] | None:
    """把声明路径解析成绝对路径字符串；相对路径以工作区为基准。

    ``None`` / 空列表原样返回 ``None``——未声明范围与声明了空集合是两种事实。
    """
    if not paths:
        return None
    base = Path(workspace) if workspace else None
    resolved: list[str] = []
    for item in paths:
        path = Path(item)
        if not path.is_absolute() and base is not None:
            path = base / path
        resolved.append(str(path.resolve()))
    return resolved


def _intersect_path_layers(layers: list[list[str] | None]) -> tuple[str, ...] | None:
    """多个路径范围层取交集；全部未声明时返回 ``None``。"""
    declared = [layer for layer in layers if layer is not None]
    if not declared:
        return None
    result = list(declared[0])
    for layer in declared[1:]:
        result = _intersect_paths(result, layer)
    return tuple(result)


def _intersect_paths(left: list[str], right: list[str]) -> list[str]:
    """两个绝对路径根集合的交集：保留同时落在两侧某个根内的条目。"""
    candidates = [*left, *right]
    kept: list[str] = []
    for candidate in candidates:
        inside_left = any(is_subpath(candidate, root) for root in left)
        inside_right = any(is_subpath(candidate, root) for root in right)
        if inside_left and inside_right and candidate not in kept:
            kept.append(candidate)
    return sorted(kept)


def _union_layers(layers: list[list[str] | None]) -> list[str] | None:
    """多个范围层取并集；全部为空时返回 ``None``。"""
    merged: list[str] = []
    for layer in layers:
        if not layer:
            continue
        for item in layer:
            if item not in merged:
                merged.append(item)
    return merged or None


def _intersect_command_layers(layers: list[list[str]]) -> tuple[str, ...] | None:
    """多个命令范围层取交集；全部为空时返回 ``None``（未声明范围）。"""
    declared = [[item for item in layer if item.strip()] for layer in layers]
    declared = [layer for layer in declared if layer]
    if not declared:
        return None
    result = list(declared[0])
    for layer in declared[1:]:
        result = _intersect_commands(result, layer)
    return tuple(result)


def _intersect_commands(left: list[str], right: list[str]) -> list[str]:
    """两个命令范围集合的交集：保留更窄的那一条。"""
    kept: list[str] = []
    for left_entry in left:
        left_base, left_sub = normalize_command_entry(left_entry)
        for right_entry in right:
            right_base, right_sub = normalize_command_entry(right_entry)
            if left_base != right_base:
                continue
            if left_sub == right_sub:
                narrowed = left_entry
            elif left_sub is None:
                narrowed = right_entry
            elif right_sub is None:
                narrowed = left_entry
            else:
                continue
            if narrowed not in kept:
                kept.append(narrowed)
    return sorted(kept)
