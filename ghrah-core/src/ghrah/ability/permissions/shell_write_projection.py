# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""把 LOCAL_EFFECTS 写入投影成与文件工具相同的路径检查。

bash-classify 对 mkdir/touch/git add 等常把目标放在 positionals，重定向才填
write_paths。本模块按小命令表收集写（及 cp/mv 的读）路径，相对
working_dir 或 workspace_root 解析，再交给 FSPermissionChecker。
无法投影时返回 None，由调用方保持 HITL。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bash_classify import Classification

if TYPE_CHECKING:
    from ghrah.ability.permissions.fs_permissions import FSPermissionChecker

_GLOB = re.compile(r"[*?[]")
_IMPLICIT_CWD_RULES = frozenset(
    {
        "git.commit",
        "git.add",
        "pnpm.install",
        "uv.sync",
        "npm.install",
        "npm.ci",
    }
)
_ALL_POSITIONALS_WRITE = frozenset({"mkdir", "touch", "tee"})


class WriteProjection:
    """投影结果为 safe、hitl 或 dangerous。"""

    __slots__ = ("kind", "reason")

    def __init__(self, kind: str, reason: str) -> None:
        self.kind = kind
        self.reason = reason


def try_workspace_write_approval(
    expression: Any,
    *,
    fs_checker: FSPermissionChecker | None,
    workspace_root: str | None,
    working_dir: str | None,
) -> WriteProjection | None:
    """工作区内可写则 safe；越界 dangerous；待审批 hitl；无法投影则 None。"""
    if fs_checker is None or not workspace_root:
        return None
    if getattr(expression, "sensitive_paths", None):
        return None
    if not _segments_are_local_or_readonly(expression):
        return None

    try:
        cwd = Path(working_dir or workspace_root).expanduser().resolve()
    except OSError:
        return None

    writes: list[Path] = []
    reads: list[Path] = []
    for command in getattr(expression, "commands", ()) or ():
        argv = list(getattr(command, "argv", ()) or ())
        base = Path(argv[0]).name.lower() if argv else ""
        classification = getattr(command, "classification", None)
        if classification is Classification.READONLY:
            continue
        if classification is not Classification.LOCAL_EFFECTS:
            return None
        segment_writes, segment_reads, ok = _paths_for_segment(command, base, cwd)
        if not ok:
            return None
        writes.extend(segment_writes)
        reads.extend(segment_reads)

    if not writes:
        return None

    pending = None
    for operation, paths in (("read", reads), ("write", writes)):
        for path in paths:
            verdict = _merge_access(*fs_checker.check_access(str(path), operation), path)
            if verdict is not None:
                if verdict.kind == "dangerous":
                    return verdict
                pending = pending or verdict
    return pending or WriteProjection("safe", "Workspace write within granted paths")


def _segments_are_local_or_readonly(expression: Any) -> bool:
    saw_write = False
    for command in getattr(expression, "commands", ()) or ():
        classification = getattr(command, "classification", None)
        if classification is Classification.LOCAL_EFFECTS:
            saw_write = True
            continue
        if classification is Classification.READONLY:
            continue
        return False
    return saw_write


def _paths_for_segment(
    command: Any,
    base: str,
    cwd: Path,
) -> tuple[list[Path], list[Path], bool]:
    positionals = [str(item) for item in (getattr(command, "positionals", None) or ())]
    if any(_has_glob(item) for item in positionals):
        return [], [], False
    rule = str(getattr(command, "matched_rule", None) or "")

    writes: list[Path] = []
    reads: list[Path] = []
    for raw in getattr(command, "write_paths", None) or ():
        if _has_glob(str(raw)):
            return [], [], False
        writes.append(_resolve(cwd, str(raw)))

    if base in _ALL_POSITIONALS_WRITE:
        if not positionals and not writes:
            return [], [], False
        writes.extend(_resolve(cwd, item) for item in positionals)
        return writes, reads, True

    if base in {"cp", "mv"}:
        if len(positionals) < 2:
            return [], [], False
        reads.extend(_resolve(cwd, item) for item in positionals[:-1])
        writes.append(_resolve(cwd, positionals[-1]))
        return writes, reads, True

    if rule == "git.add" or rule.startswith("git.add"):
        if positionals:
            writes.extend(_resolve(cwd, item) for item in positionals)
        else:
            writes.append(cwd)
        return writes, reads, True

    implicit = rule in _IMPLICIT_CWD_RULES or rule.startswith(("git.commit", "git.worktree"))
    if implicit:
        if rule.startswith("git.worktree"):
            if not positionals:
                return [], [], False
            writes.extend(_resolve(cwd, item) for item in positionals)
            return writes, reads, True
        writes.append(cwd)
        return writes, reads, True

    if writes:
        return writes, reads, True
    return [], [], False


def _has_glob(text: str) -> bool:
    return _GLOB.search(text) is not None


def _resolve(cwd: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (cwd / path).resolve()


def _merge_access(allowed: bool, status: str | None, path: Path) -> WriteProjection | None:
    if allowed and status is None:
        return None
    if allowed and status == "pending":
        return WriteProjection("hitl", f"Workspace write requires approval: {path}")
    return WriteProjection("dangerous", f"Workspace write denied: {path}")
