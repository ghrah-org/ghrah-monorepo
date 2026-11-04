# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project Root 路径解析、布局初始化与跨资源边界校验。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ghrah.subject.workspace.locator import locator_to_path, path_to_locator

__all__ = [
    "ProjectPaths",
    "ProjectRootInit",
    "canonical_file_locator",
    "paths_overlap",
    "validate_project_path_boundaries",
]


def canonical_file_locator(value: str) -> str:
    """把裸路径或 file locator 规范化为本机绝对 ``file://`` locator。"""
    raw = value.strip()
    if not raw:
        raise ValueError("project_root_locator required")
    path = locator_to_path(raw) if "://" in raw else raw
    expanded = Path(os.path.expanduser(path))
    if not expanded.is_absolute():
        raise ValueError(f"Project Root must be an absolute path: {value!r}")
    return path_to_locator(str(expanded.resolve(strict=False)))


def _canonical_path(locator: str) -> Path:
    return Path(locator_to_path(canonical_file_locator(locator)))


def paths_overlap(a_locator: str, b_locator: str) -> bool:
    """两个本地 locator 是否相同或存在父子嵌套。"""
    a = _canonical_path(a_locator)
    b = _canonical_path(b_locator)
    return a == b or a in b.parents or b in a.parents


def validate_project_path_boundaries(
    *,
    root_locator: str,
    existing_root_locators: list[str],
    workspace_locators: list[str],
) -> None:
    """校验新 Root 不与任何 Root/文件系统 Workspace 重叠。"""
    for existing in existing_root_locators:
        if existing and paths_overlap(root_locator, existing):
            raise ValueError(
                f"Project Roots overlap: {root_locator!r} vs {existing!r}. "
                "Choose an exclusive, non-nested Project Root."
            )
    for workspace in workspace_locators:
        try:
            overlap = paths_overlap(root_locator, workspace)
        except ValueError:
            continue
        if overlap:
            raise ValueError(
                f"Project Root overlaps a writable Workspace: {root_locator!r} vs {workspace!r}."
            )


@dataclass(frozen=True)
class ProjectRootInit:
    """Root 初始化回执，仅用于安全补偿本次创建的资源。"""

    root_created: bool
    marker_created: bool
    created_dirs: tuple[Path, ...]


@dataclass(frozen=True)
class ProjectPaths:
    """从唯一 Project Root 派生内部持久化位置。"""

    root_locator: str
    root: Path

    @classmethod
    def from_locator(cls, locator: str) -> ProjectPaths:
        canonical = canonical_file_locator(locator)
        return cls(root_locator=canonical, root=Path(locator_to_path(canonical)))

    @property
    def marker_path(self) -> Path:
        return self.root / ".ghrah-project.json"

    @property
    def db_dir(self) -> Path:
        return self.root / "db"

    @property
    def task_db_path(self) -> Path:
        return self.db_dir / "tasks.sqlite3"

    @property
    def room_db_path(self) -> Path:
        return self.db_dir / "rooms.sqlite3"

    @property
    def action_chain_db_path(self) -> Path:
        return self.db_dir / "action-chains.sqlite3"

    @property
    def manifest_dir(self) -> Path:
        return self.root / "manifests"

    @property
    def agent_manifest_dir(self) -> Path:
        return self.manifest_dir / "agents"

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    def initialize(self, project_id: str) -> ProjectRootInit:
        """认领空 Root，创建固定布局和 owner marker。"""
        root_created = not self.root.exists()
        if self.root.exists():
            if not self.root.is_dir():
                raise ValueError(f"Project Root is not a directory: {self.root}")
            entries = list(self.root.iterdir())
            if entries:
                try:
                    marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
                except OSError, ValueError:
                    marker = {}
                if marker.get("project_id") == project_id:
                    return ProjectRootInit(False, False, ())
                raise ValueError(f"Project Root must be empty: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)

        created_dirs: list[Path] = []
        try:
            for directory in (
                self.db_dir,
                self.manifest_dir,
                self.agent_manifest_dir,
                self.memory_dir,
                self.artifacts_dir,
            ):
                if not directory.exists():
                    directory.mkdir(parents=True, exist_ok=True)
                    created_dirs.append(directory)

            self.marker_path.write_text(
                json.dumps({"project_id": project_id, "schema_version": 1}) + "\n",
                encoding="utf-8",
            )
            return ProjectRootInit(
                root_created=root_created,
                marker_created=True,
                created_dirs=tuple(created_dirs),
            )
        except Exception:
            self.rollback_initialize(
                ProjectRootInit(
                    root_created=root_created,
                    marker_created=self.marker_path.exists(),
                    created_dirs=tuple(created_dirs),
                )
            )
            raise

    def rollback_initialize(self, receipt: ProjectRootInit) -> None:
        """只删除本次初始化创建且仍为空的 marker/目录，绝不递归删除。"""
        if receipt.marker_created:
            try:
                self.marker_path.unlink(missing_ok=True)
            except OSError:
                pass
        for directory in reversed(receipt.created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        if receipt.root_created:
            try:
                self.root.rmdir()
            except OSError:
                pass

    def validate_owner(self, project_id: str) -> None:
        """Reject roots without an exact Subject ownership marker."""
        try:
            marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Project Root marker is missing or invalid: {self.marker_path}"
            ) from exc
        if marker.get("project_id") != project_id:
            raise ValueError(f"Project Root marker owner mismatch: {self.marker_path}")

    def purge(self, project_id: str) -> None:
        """Delete an explicitly requested Project Root after owner verification."""
        self.validate_owner(project_id)
        shutil.rmtree(self.root)
