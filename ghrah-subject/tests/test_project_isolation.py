# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ghrah.subject.project.isolation import (
    IsolationContext,
    apply_isolation,
    resolve_project_roots,
    validate_path_grants_non_overlapping,
    validate_workspace_locators_non_nested,
)
from ghrah.subject.project.models import (
    AgentSpec,
    IsolationSpec,
    PathGrant,
    ProjectRecord,
    WorkspaceMount,
)
from ghrah.subject.workspace.models import WorkspaceRecord
from ghrah.subject.workspace.providers.base import WorkspaceCaps, WorkspaceProvider
from ghrah.subject.workspace.providers.plain import PlainWorkspaceProvider
from ghrah.subject.workspace.registry import ProviderRegistry

# ─── helpers ───


def _registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register(PlainWorkspaceProvider())
    return reg


def _record(workspace_id: str, ws_path: str, provider_type: str = "plain") -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_id=workspace_id,
        name=workspace_id,
        provider_type=provider_type,
        locator="file://" + ws_path,
        subject_id="default",
    )


class _NonFsProvider(WorkspaceProvider):
    """非 FILESYSTEM_BACKED 占位 provider（模拟未来 nfs:// 等）。"""

    provider_type: str = "nfs"
    capabilities: WorkspaceCaps = WorkspaceCaps(0)

    async def init(self, record: WorkspaceRecord) -> None:  # pragma: no cover
        raise NotImplementedError

    async def adopt(self, locator: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def status(self, record: WorkspaceRecord) -> None:  # pragma: no cover
        raise NotImplementedError

    async def destroy(self, record: WorkspaceRecord) -> None:  # pragma: no cover
        raise NotImplementedError


# ─── resolve_project_roots ───


class TestResolveProjectRoots:
    def test_filesystem_backed_resolved(self, tmp_path: Path) -> None:
        ws_dir = str(tmp_path / "ws1")
        os.makedirs(ws_dir)
        reg = _registry()
        records = {"ws-1": _record("ws-1", ws_dir)}
        mounts = [WorkspaceMount(workspace_id="ws-1", default_for_agents=True)]
        roots = resolve_project_roots(mounts, reg, records)
        assert roots == [ws_dir]

    def test_non_filesystem_skipped(self, tmp_path: Path) -> None:
        reg = _registry()
        reg.register(_NonFsProvider())
        records = {"ws-nfs": _record("ws-nfs", str(tmp_path / "nfs"), provider_type="nfs")}
        mounts = [WorkspaceMount(workspace_id="ws-nfs")]
        assert resolve_project_roots(mounts, reg, records) == []

    def test_missing_record_skipped(self, tmp_path: Path) -> None:
        reg = _registry()
        mounts = [WorkspaceMount(workspace_id="ws-missing")]
        assert resolve_project_roots(mounts, reg, {}) == []

    def test_unknown_provider_type_skipped(self, tmp_path: Path) -> None:
        reg = _registry()
        records = {"ws-1": _record("ws-1", str(tmp_path / "ws1"), provider_type="unknown")}
        mounts = [WorkspaceMount(workspace_id="ws-1")]
        assert resolve_project_roots(mounts, reg, records) == []

    def test_dedup_preserves_order(self, tmp_path: Path) -> None:
        ws_a = str(tmp_path / "a")
        ws_b = str(tmp_path / "b")
        os.makedirs(ws_a)
        os.makedirs(ws_b)
        reg = _registry()
        records = {
            "ws-a": _record("ws-a", ws_a),
            "ws-b": _record("ws-b", ws_b),
        }
        mounts = [
            WorkspaceMount(workspace_id="ws-a"),
            WorkspaceMount(workspace_id="ws-b"),
            WorkspaceMount(workspace_id="ws-a"),  # 重复挂载同一 workspace
        ]
        roots = resolve_project_roots(mounts, reg, records)
        assert roots == [ws_a, ws_b]


# ─── apply_isolation ───


class TestApplyIsolation:
    async def test_returns_context(self, tmp_path: Path) -> None:
        ws_dir = str(tmp_path / "ws1")
        os.makedirs(ws_dir)
        reg = _registry()
        records = {"ws-1": _record("ws-1", ws_dir)}
        grants = {
            "a1": [PathGrant(workspace_id="ws-1", subpath="plans/")],
        }
        project = ProjectRecord(
            project_id="p1",
            name="P1",
            workspaces=[WorkspaceMount(workspace_id="ws-1", default_for_agents=True)],
            isolation=IsolationSpec(agent_path_grants=grants),
        )
        ctx = await apply_isolation(project, reg, records)
        assert isinstance(ctx, IsolationContext)
        assert ctx.project_id == "p1"
        assert ctx.workspace_roots == [ws_dir]
        assert ctx.agent_path_grants == grants
        assert ctx.isolation is project.isolation

    async def test_empty_workspaces(self, tmp_path: Path) -> None:
        reg = _registry()
        project = ProjectRecord(project_id="p1", name="P1")
        ctx = await apply_isolation(project, reg, {})
        assert ctx.workspace_roots == []
        assert ctx.agent_path_grants == {}


# ─── validate_path_grants_non_overlapping ───


class TestValidatePathGrants:
    def _agent(self, grants: list[PathGrant]) -> AgentSpec:
        return AgentSpec(name="a1", cluster_id="c1", path_grants=grants)

    def test_valid_non_overlapping(self) -> None:
        workspaces = [WorkspaceMount(workspace_id="ws-1")]
        agent = self._agent(
            [
                PathGrant(workspace_id="ws-1", subpath="plans/"),
                PathGrant(workspace_id="ws-1", subpath="frontend/"),
            ]
        )
        validate_path_grants_non_overlapping(agent, workspaces)  # no raise

    def test_root_grant_alone_valid(self) -> None:
        workspaces = [WorkspaceMount(workspace_id="ws-1")]
        agent = self._agent([PathGrant(workspace_id="ws-1", subpath=".")])
        validate_path_grants_non_overlapping(agent, workspaces)  # no raise

    def test_unknown_workspace_id(self) -> None:
        workspaces = [WorkspaceMount(workspace_id="ws-1")]
        agent = self._agent([PathGrant(workspace_id="ws-99", subpath=".")])
        with pytest.raises(ValueError, match="unknown workspace_id"):
            validate_path_grants_non_overlapping(agent, workspaces)

    def test_root_overlaps_subpath(self) -> None:
        workspaces = [WorkspaceMount(workspace_id="ws-1")]
        agent = self._agent(
            [
                PathGrant(workspace_id="ws-1", subpath="."),
                PathGrant(workspace_id="ws-1", subpath="plans/"),
            ]
        )
        with pytest.raises(ValueError, match="overlaps"):
            validate_path_grants_non_overlapping(agent, workspaces)

    def test_nested_subpaths(self) -> None:
        workspaces = [WorkspaceMount(workspace_id="ws-1")]
        agent = self._agent(
            [
                PathGrant(workspace_id="ws-1", subpath="src"),
                PathGrant(workspace_id="ws-1", subpath="src/core"),
            ]
        )
        with pytest.raises(ValueError, match="overlap"):
            validate_path_grants_non_overlapping(agent, workspaces)

    def test_grants_in_different_workspaces_independent(self) -> None:
        workspaces = [WorkspaceMount(workspace_id="ws-1"), WorkspaceMount(workspace_id="ws-2")]
        # 同名 subpath 在不同 workspace 不冲突
        agent = self._agent(
            [
                PathGrant(workspace_id="ws-1", subpath="."),
                PathGrant(workspace_id="ws-2", subpath="."),
            ]
        )
        validate_path_grants_non_overlapping(agent, workspaces)  # no raise

    def test_subpath_normalization(self) -> None:
        # "plans/" 与 "plans" 规范化后相同
        workspaces = [WorkspaceMount(workspace_id="ws-1")]
        agent = self._agent(
            [
                PathGrant(workspace_id="ws-1", subpath="plans/"),
                PathGrant(workspace_id="ws-1", subpath="plans"),
            ]
        )
        with pytest.raises(ValueError, match="overlap"):
            validate_path_grants_non_overlapping(agent, workspaces)


# ─── validate_workspace_locators_non_nested ───


class TestValidateLocators:
    def test_independent_valid(self, tmp_path: Path) -> None:
        a = str(tmp_path / "a")
        b = str(tmp_path / "b")
        os.makedirs(a)
        os.makedirs(b)
        validate_workspace_locators_non_nested(["file://" + a, "file://" + b])  # no raise

    def test_nested_raises(self, tmp_path: Path) -> None:
        parent = str(tmp_path / "parent")
        child = str(tmp_path / "parent" / "child")
        os.makedirs(child)
        with pytest.raises(ValueError, match="nest"):
            validate_workspace_locators_non_nested(["file://" + parent, "file://" + child])

    def test_same_path_raises(self, tmp_path: Path) -> None:
        a = str(tmp_path / "a")
        os.makedirs(a)
        with pytest.raises(ValueError, match="nest"):
            validate_workspace_locators_non_nested(["file://" + a, "file://" + a])

    def test_trailing_slash_normalized(self, tmp_path: Path) -> None:
        a = str(tmp_path / "a")
        os.makedirs(a)
        # 带尾斜杠 vs 不带应判为同一路径
        with pytest.raises(ValueError, match="nest"):
            validate_workspace_locators_non_nested(["file://" + a, "file://" + a + "/"])

    def test_non_file_locator_skipped(self) -> None:
        # 非 file:// locator 不参与文件系统嵌套校验，与 file:// 共存不报错
        validate_workspace_locators_non_nested(["nfs://host/share", "file:///abs/path"])  # no raise

    def test_mixed_locators_message_references_actual_nested_pair(self, tmp_path: Path) -> None:
        # 过滤掉非 file:// locator 后，索引偏移不应导致报错消息引用错位 locator
        parent = str(tmp_path / "parent")
        child = str(tmp_path / "parent" / "child")
        os.makedirs(child)
        parent_loc = "file://" + parent
        child_loc = "file://" + child
        nfs_loc = "nfs://host/share"
        with pytest.raises(ValueError, match="nest") as exc_info:
            validate_workspace_locators_non_nested([nfs_loc, parent_loc, child_loc])
        msg = str(exc_info.value)
        assert repr(parent_loc) in msg
        assert repr(child_loc) in msg
        assert nfs_loc not in msg
