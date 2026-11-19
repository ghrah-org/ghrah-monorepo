# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Workspace 挂载语义验收。

验收要点：
1. 零污染：真实 git 仓注册后 .git/config 字节级不变、无新增 commit、
   ref/工作树不变、无 marker；
2. 解挂保全：register → destroy 后目录与 .git 原样、cwd 授权解除；
3. create 语义：纯目录（无 git/marker），destroy 后目录保留；
4. fail-closed：不存在目录 / git 类型 / 非 file:// locator 一律拒绝；
5. 幂等：同 locator 重复注册返回同一 workspace；
6. store 迁移：git 记录 retag 为 plain，reload 不炸；
7. bootstrap：全新 root 上 bootstrap_default_project 成功。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ghrah.subject.sandbox.executor import SandboxExecutor
from ghrah.subject.sandbox.workspace import WorkspaceManager
from ghrah.subject.workspace import WorkspaceProviderError, path_to_locator

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


class _Sandbox:
    def __init__(self, root: str) -> None:
        self.executor = SandboxExecutor(workspace_root=root)

    async def __aenter__(self) -> SandboxExecutor:
        await self.executor.start()
        return self.executor

    async def __aexit__(self, *exc: object) -> None:
        await self.executor.stop()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return result.stdout


def _make_real_repo(tmp_path: Path) -> tuple[Path, bytes, str, str, list[str]]:
    """构造真实 git 仓（含 1 commit + WIP 文件），返回注册前快照证据。"""
    repo = tmp_path / "ghrah-repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    (repo / "README.md").write_text("# real repo\n")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.email=owner@user.local",
        "-c",
        "user.name=owner",
        "commit",
        "-m",
        "user commit",
    )
    (repo / "wip.txt").write_text("user WIP\n")

    config_bytes = (repo / ".git" / "config").read_bytes()
    refs = _git(repo, "for-each-ref")
    reflog = _git(repo, "reflog")
    tree = sorted(p.name for p in repo.iterdir())
    return repo, config_bytes, refs, reflog, tree


@requires_git
class TestZeroPollution:
    async def test_register_real_git_repo_byte_identical(self, tmp_path: Path) -> None:
        repo, config_before, refs_before, reflog_before, tree_before = _make_real_repo(tmp_path)
        root = str(tmp_path / "subject-root")
        db = str(tmp_path / "workspaces.db")

        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = await manager.register_workspace(path_to_locator(str(repo)), name="ghrah")
            # 注册成功 + 授权生效（cwd 可解析）
            assert ws.path == str(repo)
            sandbox._resolve_cwd(str(repo))

            # 零污染断言：.git/config 字节级、refs、reflog、工作树、无 marker
            assert (repo / ".git" / "config").read_bytes() == config_before
            assert _git(repo, "for-each-ref") == refs_before
            assert _git(repo, "reflog") == reflog_before
            assert sorted(p.name for p in repo.iterdir()) == tree_before
            assert not (repo / ".ghrah-workspace").exists()
            assert not (repo / ".git" / "ghrah-workspace").exists()
            await manager.stop()

        # 无新增 commit（重跑 for-each-ref 幂等）
        assert _git(repo, "for-each-ref") == refs_before


@requires_git
class TestUnmountPreservation:
    async def test_register_then_destroy_preserves_repo(self, tmp_path: Path) -> None:
        repo, _config, _refs, _reflog, tree_before = _make_real_repo(tmp_path)
        root = str(tmp_path / "subject-root")
        db = str(tmp_path / "workspaces.db")

        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = await manager.register_workspace(path_to_locator(str(repo)))
            wid = ws.record.workspace_id
            await manager.unregister_workspace(wid)

            # 目录与 .git 原样保留
            assert repo.is_dir()
            assert (repo / ".git").is_dir()
            assert (repo / "wip.txt").read_text() == "user WIP\n"
            assert sorted(p.name for p in repo.iterdir()) == tree_before
            # 注册关系已移除
            assert manager.get_record(wid) is None
            # cwd 授权解除
            with pytest.raises(ValueError):
                sandbox._resolve_cwd(str(repo))
            await manager.stop()

    async def test_register_then_unregister_by_id(self, tmp_path: Path) -> None:
        repo = tmp_path / "plain-dir"
        repo.mkdir()
        (repo / "data.txt").write_text("keep")
        root = str(tmp_path / "subject-root")
        db = str(tmp_path / "workspaces.db")

        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = await manager.register_workspace(path_to_locator(str(repo)))
            await manager.unregister_workspace(ws.record.workspace_id)
            assert manager.get_workspace_by_id(ws.record.workspace_id) is None
            assert (repo / "data.txt").read_text() == "keep"
            await manager.stop()


class TestCreateSemantics:
    async def test_create_is_plain_dir_and_destroy_preserves(self, tmp_path: Path) -> None:
        root = str(tmp_path / "subject-root")
        db = str(tmp_path / "workspaces.db")

        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = await manager.create_workspace("agent-a")
            # 纯目录：无 .git、无 marker
            assert ws.path == str(Path(root) / "agent-a")
            assert (Path(root) / "agent-a").is_dir()
            assert not (Path(root) / "agent-a" / ".git").exists()
            assert not (Path(root) / "agent-a" / ".ghrah-workspace").exists()

            await manager.destroy_workspace("agent-a")
            # create 建的目录 destroy 后同样保留（rmtree 全域退出）
            assert (Path(root) / "agent-a").is_dir()
            assert manager.get_workspace("agent-a") is None
            await manager.stop()


class TestFailClosed:
    async def test_register_missing_dir_rejected(self, tmp_path: Path) -> None:
        root = str(tmp_path / "subject-root")
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox)
            await manager.start()
            with pytest.raises(WorkspaceProviderError, match="does not exist"):
                await manager.register_workspace(path_to_locator(str(tmp_path / "missing")))
            await manager.stop()

    async def test_register_git_provider_rejected_with_guidance(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        root = str(tmp_path / "subject-root")
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox)
            await manager.start()
            with pytest.raises(WorkspaceProviderError) as exc_info:
                await manager.register_workspace(path_to_locator(str(repo)), provider_type="git")
            # 错误文案含迁移指引
            assert "git" in str(exc_info.value)
            assert "shadow-git" in str(exc_info.value)
            # 目录未被触碰
            assert list(repo.iterdir()) == []
            await manager.stop()

    async def test_register_unknown_provider_rejected(self, tmp_path: Path) -> None:
        d = tmp_path / "dir"
        d.mkdir()
        root = str(tmp_path / "subject-root")
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox)
            await manager.start()
            with pytest.raises(WorkspaceProviderError, match="Unknown workspace provider"):
                await manager.register_workspace(path_to_locator(str(d)), provider_type="nfs")
            await manager.stop()

    async def test_register_non_file_locator_rejected(self, tmp_path: Path) -> None:
        root = str(tmp_path / "subject-root")
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox)
            await manager.start()
            with pytest.raises(WorkspaceProviderError, match="file"):
                await manager.register_workspace("nfs://host/share")
            await manager.stop()


class TestIdempotentRegistration:
    async def test_same_locator_returns_same_workspace(self, tmp_path: Path) -> None:
        guest = tmp_path / "guest"
        guest.mkdir()
        root = str(tmp_path / "subject-root")
        db = str(tmp_path / "workspaces.db")

        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws1 = await manager.register_workspace(path_to_locator(str(guest)), name="one")
            ws2 = await manager.register_workspace(path_to_locator(str(guest)), name="two")
            assert ws1 is ws2
            assert ws1.record.name == "one"
            await manager.stop()

    async def test_reregister_after_restart_returns_same_id(self, tmp_path: Path) -> None:
        guest = tmp_path / "guest"
        guest.mkdir()
        root = str(tmp_path / "subject-root")
        db = str(tmp_path / "workspaces.db")

        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = await manager.register_workspace(path_to_locator(str(guest)))
            await manager.stop()

        async with _Sandbox(root) as sandbox2:
            manager2 = WorkspaceManager(root_path=root, sandbox=sandbox2, db_path=db)
            await manager2.start()
            ws2 = await manager2.register_workspace(path_to_locator(str(guest)))
            assert ws2.record.workspace_id == ws.record.workspace_id
            await manager2.stop()


class TestStoreLegacyRetag:
    async def test_git_records_retagged_and_reloadable(self, tmp_path: Path) -> None:
        from ghrah.subject.workspace import WorkspaceRecord, WorkspaceStore

        root = str(tmp_path / "subject-root")
        os_root = Path(root)
        (os_root / "legacy-agent").mkdir(parents=True)
        db = str(tmp_path / "workspaces.db")

        store = WorkspaceStore(db)
        await store.start()
        await store.upsert(
            WorkspaceRecord(
                name="legacy-agent",
                provider_type="git",
                locator=path_to_locator(str(os_root / "legacy-agent")),
            )
        )
        await store.stop()

        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = manager.get_workspace("legacy-agent")
            assert ws is not None
            assert ws.record.provider_type == "plain"
            await manager.stop()


class TestBootstrapDefaultProject:
    async def test_bootstrap_on_fresh_root(self, tmp_path: Path) -> None:
        """全新 root 上 bootstrap_default_project 成功（makedirs 前置生效）。"""
        from ghrah.subject.project.manager import ProjectManager
        from ghrah.subject.project.store import ProjectStore

        root = str(tmp_path / "subject-root")
        bootstrap_dir = str(Path(root) / "projects" / "default")
        db = str(tmp_path / "subject.db")

        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            project_store = ProjectStore(str(tmp_path / "projects.db"))
            await project_store.start()
            try:

                class _NoopTransport:
                    async def ensure_cluster(self, cluster_id, *, project_id, project_root_locator):
                        return None

                    def has_cluster(self, cluster_id):
                        return False

                    async def shutdown_cluster(self, cluster_id):
                        return None

                project_mgr = ProjectManager(
                    store=project_store,
                    workspace_mgr=manager,
                    task_mgr=object(),
                    cluster_registry=_NoopTransport(),
                    bootstrap_workspace_locator=bootstrap_dir,
                    default_root_locator_template=str(tmp_path / "roots/{project_id}"),
                )
                project = await project_mgr.bootstrap_default_project()
                assert project.name == "default"
                assert len(project.workspaces) == 1
                # bootstrap workspace 目录已建并登记
                assert Path(bootstrap_dir).is_dir()
                records = manager.list_records_by_provider()
                assert any(r.locator == path_to_locator(bootstrap_dir) for r in records)
            finally:
                await project_store.stop()
            await manager.stop()
