# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""WorkspaceManager 挂载语义验收：workspace_id 键控 + store 恢复 + start 回填
+ agent_name 兼容桥 + 注册 fail-closed。

契约用例：
- 重启后从 store 恢复；
- store 未就绪时 create_workspace 仅 mkdir + 内存登记，start() 时回填 store；
- destroy_workspace ≡ unregister_workspace（store 软删 + 授权解除，目录保留）；
- register_workspace 零物理操作 + 幂等 + fail-closed（不存在目录/git 类型拒绝）；
- workspace_id 一等查询接口。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ghrah.subject.sandbox.executor import SandboxExecutor
from ghrah.subject.sandbox.workspace import WorkspaceManager
from ghrah.subject.workspace import WorkspaceProviderError, path_to_locator


class _Sandbox:
    def __init__(self, root: str) -> None:
        self.executor = SandboxExecutor(workspace_root=root)

    async def __aenter__(self) -> SandboxExecutor:
        await self.executor.start()
        return self.executor

    async def __aexit__(self, *exc: object) -> None:
        await self.executor.stop()


def _sandbox_root(tmp_path: Path) -> str:
    return str(tmp_path / "root")


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "workspaces.db")


# ─── 1. 重启后从 store 恢复 ───


class TestRestartRestoreFromStore:
    async def test_restart_reloads_workspaces(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = await manager.create_workspace("agent-a")
            assert manager.get_workspace("agent-a") is ws
            await manager.stop()

        async with _Sandbox(root) as sandbox2:
            manager2 = WorkspaceManager(root_path=root, sandbox=sandbox2, db_path=db)
            await manager2.start()
            # 从 store 恢复：agent-a 仍在索引中
            assert manager2.get_workspace("agent-a") is not None
            assert manager2.list_workspaces() == ["agent-a"]
            # workspace_id 一等查询
            wid = manager2.get_workspace("agent-a").record.workspace_id  # type: ignore[union-attr]
            assert manager2.get_workspace_by_id(wid) is not None
            await manager2.stop()

    async def test_destroy_persists_soft_delete(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            await manager.create_workspace("agent-x")
            await manager.destroy_workspace("agent-x")
            await manager.stop()

        async with _Sandbox(root) as sandbox2:
            manager2 = WorkspaceManager(root_path=root, sandbox=sandbox2, db_path=db)
            await manager2.start()
            assert manager2.get_workspace("agent-x") is None
            assert manager2.list_workspaces() == []
            await manager2.stop()

    async def test_legacy_git_record_retagged_on_start(self, tmp_path: Path) -> None:
        """存量 git provider 记录在 store.start() retag 为 plain，reload 不炸。"""
        from ghrah.subject.workspace import WorkspaceRecord, WorkspaceStore

        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        os.makedirs(root, exist_ok=True)
        store = WorkspaceStore(db)
        await store.start()
        await store.upsert(
            WorkspaceRecord(
                name="legacy-agent",
                provider_type="git",
                locator=path_to_locator(os.path.join(root, "legacy-agent")),
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


# ─── 2. store 未就绪退化路径（create → start 回填） ───


class TestStoreNotStartedDegradation:
    async def test_create_workspace_without_start(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            # 未 start() 直接 create：降级为仅 mkdir + 内存登记
            ws = await manager.create_workspace("agent-no-start")
            assert os.path.isdir(ws.path)
            # 挂载语义：纯目录，无 marker、无 git
            assert not os.path.exists(os.path.join(ws.path, ".ghrah-workspace"))
            assert not os.path.exists(os.path.join(ws.path, ".git"))
            assert manager.get_workspace("agent-no-start") is ws
            # store 未就绪 → 未登记
            assert manager.get_record(ws.record.workspace_id) is not None  # 内存有
            # 后续 start() 回填 store（无 marker，靠内存记录）
            await manager.start()
            assert manager.get_workspace("agent-no-start") is not None
            await manager.stop()

    async def test_create_then_start_persists_via_backfill(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            ws = await manager.create_workspace("agent-late")
            wid = ws.record.workspace_id
            await manager.start()  # store 启动 + 内存记录回填
            await manager.stop()

        async with _Sandbox(root) as sandbox2:
            manager2 = WorkspaceManager(root_path=root, sandbox=sandbox2, db_path=db)
            await manager2.start()
            # 经回填后，store 应有该记录 → 重启仍恢复
            restored = manager2.get_workspace_by_id(wid)
            assert restored is not None
            await manager2.stop()


# ─── 3. destroy = unregister（目录保留） ───


class TestDestroyPreservesDirectory:
    async def test_destroy_keeps_physical_directory(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = await manager.create_workspace("agent-keep")
            ws_path = ws.path
            f = os.path.join(ws_path, "artifact.txt")
            with open(f, "w") as fh:
                fh.write("persist")
            await manager.destroy_workspace("agent-keep")
            # 解挂后：目录与内容原样保留
            assert os.path.isdir(ws_path)
            assert os.path.isfile(f)
            assert manager.get_workspace("agent-keep") is None
            # 注：默认目录位于 workspace_root 内，cwd 允许由 root 边界承担，
            # external 授权解除的断言见 mount_semantics 外部 guest 场景。
            await manager.stop()

    async def test_destroy_nonexistent_agent_noop(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox)
            await manager.start()
            await manager.destroy_workspace("nonexistent-agent")
            await manager.stop()


# ─── 4. register_workspace 挂载语义 ───


class TestRegisterMountSemantics:
    async def test_register_existing_dir_zero_physical_writes(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        guest = tmp_path / "guest-dir"
        guest.mkdir()
        (guest / "user-file.txt").write_text("user content", newline="\n")
        before = sorted(p.name for p in guest.iterdir())

        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = await manager.register_workspace(path_to_locator(str(guest)))
            assert ws.path == str(guest)
            assert ws.record.provider_type == "plain"
            # 零物理操作：目录清单不变、无 marker、无 git
            assert sorted(p.name for p in guest.iterdir()) == before
            assert not (guest / ".ghrah-workspace").exists()
            assert not (guest / ".git").exists()
            await manager.stop()

    async def test_register_missing_dir_fails_closed(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox)
            await manager.start()
            with pytest.raises(WorkspaceProviderError, match="does not exist"):
                await manager.register_workspace(path_to_locator(str(tmp_path / "nope")))
            await manager.stop()

    async def test_register_git_provider_rejected(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        guest = tmp_path / "repo"
        guest.mkdir()
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox)
            await manager.start()
            with pytest.raises(WorkspaceProviderError, match="git"):
                await manager.register_workspace(path_to_locator(str(guest)), provider_type="git")
            await manager.stop()

    async def test_register_same_locator_idempotent(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        guest = tmp_path / "dup-dir"
        guest.mkdir()
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws1 = await manager.register_workspace(path_to_locator(str(guest)))
            ws2 = await manager.register_workspace(path_to_locator(str(guest)))
            assert ws1 is ws2
            records = [r for r in manager.list_records() if r.locator == ws1.record.locator]
            assert len(records) == 1
            await manager.stop()

    async def test_register_restored_after_restart(self, tmp_path: Path) -> None:
        """register 登记经 store 持久化，重启后恢复（store 是唯一注册真相）。"""
        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        guest = tmp_path / "persist-dir"
        guest.mkdir()
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws = await manager.register_workspace(path_to_locator(str(guest)))
            wid = ws.record.workspace_id
            await manager.stop()

        async with _Sandbox(root) as sandbox2:
            manager2 = WorkspaceManager(root_path=root, sandbox=sandbox2, db_path=db)
            await manager2.start()
            assert manager2.get_workspace_by_id(wid) is not None
            await manager2.stop()


# ─── 5. 无 db_path 纯内存模式 ───


class TestInMemoryMode:
    async def test_no_db_path_works_in_memory(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox)
            await manager.start()
            ws = await manager.create_workspace("agent-mem")
            assert manager.get_workspace("agent-mem") is ws
            assert "agent-mem" in manager.list_workspaces()
            await manager.destroy_workspace("agent-mem")
            assert manager.get_workspace("agent-mem") is None
            await manager.stop()


# ─── 6. workspace_id 一等查询 ───


class TestWorkspaceIdFirstClass:
    async def test_get_by_id_and_list_records(self, tmp_path: Path) -> None:
        root = _sandbox_root(tmp_path)
        db = _db(tmp_path)
        async with _Sandbox(root) as sandbox:
            manager = WorkspaceManager(root_path=root, sandbox=sandbox, db_path=db)
            await manager.start()
            ws1 = await manager.create_workspace("a1")
            ws2 = await manager.create_workspace("a2")
            assert manager.get_workspace_by_id(ws1.record.workspace_id) is ws1
            ids = {r.workspace_id for r in manager.list_records()}
            assert {ws1.record.workspace_id, ws2.record.workspace_id} <= ids
            await manager.stop()
