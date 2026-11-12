# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from ghrah.subject.project.paths import (
    ProjectPaths,
    canonical_file_locator,
    paths_overlap,
    validate_project_path_boundaries,
)


def test_canonical_file_locator_requires_absolute_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        canonical_file_locator("relative/project")


def test_project_paths_initialize_and_safe_rollback(tmp_path: Path) -> None:
    paths = ProjectPaths.from_locator(str(tmp_path / "project"))
    receipt = paths.initialize("p1")
    assert paths.marker_path.is_file()
    assert paths.task_db_path.parent.is_dir()
    assert paths.agent_manifest_dir.is_dir()
    assert paths.attachments_dir.is_dir()
    paths.rollback_initialize(receipt)
    assert not paths.root.exists()


def test_initialize_rejects_non_empty_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "owned.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="must be empty"):
        ProjectPaths.from_locator(str(root)).initialize("p1")
    assert (root / "owned.txt").read_text(encoding="utf-8") == "keep"


def test_overlap_detects_equal_and_nested(tmp_path: Path) -> None:
    root = canonical_file_locator(str(tmp_path / "root"))
    nested = canonical_file_locator(str(tmp_path / "root" / "nested"))
    sibling = canonical_file_locator(str(tmp_path / "sibling"))
    assert paths_overlap(root, root)
    assert paths_overlap(root, nested)
    assert not paths_overlap(root, sibling)


def test_root_cannot_overlap_workspace(tmp_path: Path) -> None:
    root = canonical_file_locator(str(tmp_path / "root"))
    workspace = canonical_file_locator(str(tmp_path / "root" / "workspace"))
    with pytest.raises(ValueError, match="writable Workspace"):
        validate_project_path_boundaries(
            root_locator=root,
            existing_root_locators=[],
            workspace_locators=[workspace],
        )
