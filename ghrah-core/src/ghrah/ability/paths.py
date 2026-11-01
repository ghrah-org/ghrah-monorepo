# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Operation 的真实路径约束与受信装配时的权限路径解析。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def is_subpath(path: str | Path, parent: str | Path) -> bool:
    """检查 path 是否在 parent 目录下（严格前缀匹配，防止 /tmp/data 匹配 /tmp/database）。

    使用 pathlib.Path.resolve() 解析符号链接，并用 is_relative_to() 做子路径判断，
    正确处理根目录场景（如 parent="/"）。

    Args:
        path: 待检查的路径
        parent: 父目录路径

    Returns:
        True 如果 path 是 parent 或 parent 的子路径
    """
    path_resolved = Path(os.path.normcase(Path(path).resolve()))
    parent_resolved = Path(os.path.normcase(Path(parent).resolve()))
    if parent_resolved == path_resolved:
        return True
    return path_resolved.is_relative_to(parent_resolved)


def resolve_fs_permission_paths(
    allowed_paths: list[str] | None,
    denied_paths: list[str] | None,
    template_vars: Mapping[str, str],
    workspace: str,
) -> tuple[list[str] | None, list[str] | None]:
    """解析 FS 权限路径：模板变量展开 + "." → workspace 重写。

    供本地实例化路径（runner）与分布式 spawn params 生成路径共用，避免两处
    各自重复实现模板展开（``{{workspace}}``/``{{persistence_dir}}``/``{{session_id}}``）
    与相对路径 ``"."`` 重写而漂移。

    Args:
        allowed_paths: 原始 allowed_paths（可能含模板变量或 "."），None 表示未配置
        denied_paths: 原始 denied_paths，None 表示未配置
        template_vars: 模板变量映射，如 ``{"workspace": "/tmp/ws", ...}``
        workspace: 工作区绝对路径，用于将 "." 重写为实际工作区

    Returns:
        ``(resolved_allowed, resolved_denied)`` 元组：
        - allowed 为空列表或 None 时返回 None（保持 FSPermissionChecker 的
          "未配置白名单"语义）
        - denied 为空列表或 None 时返回 None
    """

    def _resolve(paths: list[str] | None) -> list[str] | None:
        if not paths:
            return None
        resolved = [_expand(p, template_vars) for p in paths]
        return [workspace if p == "." else p for p in resolved]

    return _resolve(allowed_paths), _resolve(denied_paths)


def _expand(raw: str, template_vars: Mapping[str, str]) -> str:
    """展开字符串中的 ``{{var}}`` 模板变量。"""
    result = raw
    for key, value in template_vars.items():
        result = result.replace("{{" + key + "}}", value)
    return result


__all__ = ["is_subpath", "resolve_fs_permission_paths"]
