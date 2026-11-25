# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""按 Project Root 懒路由 Task/Room 持久化的兼容门面。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ghrah.subject.material.state_store import MaterialStateStore
from ghrah.subject.project.errors import ProjectArchivedError
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.room.chat_action_store import ChatActionStore
from ghrah.subject.room.models import RoomLogRecord, RoomRecord
from ghrah.subject.room.store import RoomStore
from ghrah.subject.task.models import TaskRecord
from ghrah.subject.task.store import TaskStore

ProjectRoots = Callable[[], Awaitable[dict[str, str]]]


class ProjectScopedMaterialStateStore:
    """业务数据按已认领的 Project Root 懒加载，并参与项目生命周期。"""

    def __init__(self, roots: ProjectRoots) -> None:
        self._roots = roots
        self._stores: dict[str, MaterialStateStore] = {}
        self._frozen: set[str] = set()
        self._lock = asyncio.Lock()

    async def store(self, project_id: str) -> MaterialStateStore:
        """返回唯一连接；找不到项目时拒绝回落到全局数据库。"""
        async with self._lock:
            if project_id in self._frozen:
                raise ProjectArchivedError(project_id)
            locator = (await self._roots()).get(project_id)
            if not locator:
                raise ValueError(f"project root unavailable: {project_id}")
            paths = ProjectPaths.from_locator(locator)
            paths.validate_owner(project_id)
            if project_id not in self._stores:
                store = MaterialStateStore(paths.material_state_db_path, project_id)
                await store.start()
                self._stores[project_id] = store
            return self._stores[project_id]

    async def stop(self) -> None:
        """关闭所有已打开的项目库。"""
        async with self._lock:
            for store in self._stores.values():
                await store.stop()
            self._stores.clear()

    async def close_project(self, project_id: str) -> None:
        """项目归档时关闭连接并冻结后续访问。"""
        async with self._lock:
            self._frozen.add(project_id)
            store = self._stores.pop(project_id, None)
            if store is not None:
                await store.stop()

    async def restore_project(self, project_id: str) -> None:
        """恢复项目后惰性重开连接。"""
        async with self._lock:
            self._frozen.discard(project_id)

    async def evict_project(self, project_id: str) -> None:
        """永久删除后移除连接与缓存。"""
        await self.close_project(project_id)
        async with self._lock:
            self._frozen.discard(project_id)


class ProjectScopedTaskStore:
    """TaskStore duck-type facade；新数据按 project_id 进入 Project Root。"""

    def __init__(self, legacy_db_path: str, roots: ProjectRoots) -> None:
        self._legacy = TaskStore(legacy_db_path)
        self._roots = roots
        self._stores: dict[str, TaskStore] = {}
        self._task_projects: dict[str, str] = {}
        self._known_roots: dict[str, str] = {}
        self._frozen_projects: set[str] = set()

    def register_project_root(self, project_id: str, locator: str) -> None:
        """Seed a root while ProjectUnit itself is still starting."""
        if locator:
            self._known_roots[project_id] = locator

    async def _resolved_roots(self) -> dict[str, str]:
        self._known_roots.update(await self._roots())
        return self._known_roots

    async def start(self) -> None:
        await self._legacy.start()

    async def stop(self) -> None:
        for store in self._stores.values():
            await store.stop()
        self._stores.clear()
        await self._legacy.stop()

    async def _project_store(self, project_id: str) -> TaskStore:
        if project_id in self._frozen_projects:
            raise ProjectArchivedError(project_id)
        roots = await self._resolved_roots()
        locator = roots.get(project_id)
        if not locator:
            return self._legacy
        store = self._stores.get(project_id)
        if store is None:
            store = TaskStore(ProjectPaths.from_locator(locator).task_db_path)
            await store.start()
            self._stores[project_id] = store
        return store

    async def _all_stores(self) -> list[TaskStore]:
        for project_id in await self._resolved_roots():
            if project_id in self._frozen_projects:
                continue
            await self._project_store(project_id)
        return [self._legacy, *self._stores.values()]

    async def close_project(self, project_id: str) -> None:
        """Freeze a Project Root and close its open TaskStore handle."""

        if project_id in self._frozen_projects:
            return
        store = self._stores.get(project_id)
        temporary = False
        if store is None:
            locator = (await self._resolved_roots()).get(project_id)
            if locator:
                store = TaskStore(ProjectPaths.from_locator(locator).task_db_path)
                await store.start()
                temporary = True
        if store is not None:
            try:
                for task in await store.list_for_migration(project_id):
                    self._task_projects[task.task_id] = project_id
            finally:
                if temporary:
                    await store.stop()
        self._frozen_projects.add(project_id)
        opened = self._stores.pop(project_id, None)
        if opened is not None:
            await opened.stop()

    async def restore_project(self, project_id: str) -> None:
        """Unfreeze a Root; the TaskStore is reopened lazily on next access."""

        self._frozen_projects.discard(project_id)

    async def evict_project(self, project_id: str) -> None:
        """Drop all cached routing state after permanent Project deletion."""

        await self.close_project(project_id)
        self._known_roots.pop(project_id, None)
        self._frozen_projects.discard(project_id)
        self._task_projects = {
            task_id: owner for task_id, owner in self._task_projects.items() if owner != project_id
        }

    async def _locate(self, task_id: str) -> TaskStore | None:
        project_id = self._task_projects.get(task_id)
        if project_id is not None:
            return await self._project_store(project_id)
        for store in await self._all_stores():
            record = await store.get(task_id, include_deleted=True)
            if record is not None:
                self._task_projects[task_id] = record.project_id
                return store
        return None

    async def upsert(self, task: TaskRecord) -> None:
        store = await self._project_store(task.project_id)
        await store.upsert(task)
        self._task_projects[task.task_id] = task.project_id

    async def get(self, task_id: str, *, include_deleted: bool = False) -> TaskRecord | None:
        store = await self._locate(task_id)
        return None if store is None else await store.get(task_id, include_deleted=include_deleted)

    async def update(self, task_id: str, **kwargs: Any) -> TaskRecord | None:
        store = await self._locate(task_id)
        return None if store is None else await store.update(task_id, **kwargs)

    async def soft_delete(self, task_id: str) -> bool:
        store = await self._locate(task_id)
        return False if store is None else await store.soft_delete(task_id)

    async def list(self, **kwargs: Any) -> list[TaskRecord]:
        project_id = kwargs.get("project_id")
        if project_id is not None:
            return await (await self._project_store(project_id)).list(**kwargs)
        limit = int(kwargs.get("limit", 100))
        records: list[TaskRecord] = []
        for store in await self._all_stores():
            records.extend(await store.list(**{**kwargs, "limit": limit}))
        records.sort(key=lambda r: r.created_at)
        return records[:limit]

    async def list_all_active(self) -> list[TaskRecord]:
        records: list[TaskRecord] = []
        for store in await self._all_stores():
            records.extend(await store.list_all_active())
        return records

    async def count_dependents(self, task_id: str) -> int:
        return sum([await s.count_dependents(task_id) for s in await self._all_stores()])

    async def count_children(self, parent_id: str) -> int:
        return sum([await s.count_children(parent_id) for s in await self._all_stores()])

    async def reassign_project_id(self, old_id: str, new_id: str, **kwargs: Any) -> int:
        count = 0
        for store in await self._all_stores():
            count += await store.reassign_project_id(old_id, new_id, **kwargs)
        await self.migrate_project(new_id)
        return count

    async def migrate_project(self, project_id: str) -> int:
        target = await self._project_store(project_id)
        if target is self._legacy:
            return 0
        records = await self._legacy.list_for_migration(project_id)
        for record in records:
            await target.upsert(record)
            self._task_projects[record.task_id] = project_id
        if records:
            await self._legacy.delete_project_records(project_id)
        return len(records)

    async def migrate_all(self) -> int:
        return sum(
            [
                await self.migrate_project(pid)
                for pid in await self._resolved_roots()
                if pid not in self._frozen_projects
            ]
        )


class ProjectScopedRoomStore:
    """RoomStore duck-type facade；Room 与日志按 project_id 进入 Project Root。"""

    def __init__(self, legacy_db_path: str, roots: ProjectRoots) -> None:
        self._legacy = RoomStore(legacy_db_path)
        self._roots = roots
        self._stores: dict[str, RoomStore] = {}
        self._room_projects: dict[str, str] = {}
        self._known_roots: dict[str, str] = {}
        self._frozen_projects: set[str] = set()

    def register_project_root(self, project_id: str, locator: str) -> None:
        if locator:
            self._known_roots[project_id] = locator

    async def _resolved_roots(self) -> dict[str, str]:
        self._known_roots.update(await self._roots())
        return self._known_roots

    async def start(self) -> None:
        await self._legacy.start()

    async def stop(self) -> None:
        for store in self._stores.values():
            await store.stop()
        self._stores.clear()
        await self._legacy.stop()

    async def _project_store(self, project_id: str) -> RoomStore:
        if project_id in self._frozen_projects:
            raise ProjectArchivedError(project_id)
        roots = await self._resolved_roots()
        locator = roots.get(project_id)
        if not locator:
            return self._legacy
        store = self._stores.get(project_id)
        if store is None:
            store = RoomStore(ProjectPaths.from_locator(locator).room_db_path)
            await store.start()
            self._stores[project_id] = store
        return store

    async def _all_stores(self) -> list[RoomStore]:
        for project_id in await self._resolved_roots():
            if project_id in self._frozen_projects:
                continue
            await self._project_store(project_id)
        return [self._legacy, *self._stores.values()]

    async def close_project(self, project_id: str) -> None:
        """Freeze a Project Root and close its open RoomStore handle."""

        if project_id in self._frozen_projects:
            return
        store = self._stores.get(project_id)
        temporary = False
        if store is None:
            locator = (await self._resolved_roots()).get(project_id)
            if locator:
                store = RoomStore(ProjectPaths.from_locator(locator).room_db_path)
                await store.start()
                temporary = True
        if store is not None:
            try:
                for room in await store.list(project_id=project_id):
                    self._room_projects[room.room_id] = project_id
            finally:
                if temporary:
                    await store.stop()
        self._frozen_projects.add(project_id)
        opened = self._stores.pop(project_id, None)
        if opened is not None:
            await opened.stop()

    async def restore_project(self, project_id: str) -> None:
        self._frozen_projects.discard(project_id)

    async def evict_project(self, project_id: str) -> None:
        await self.close_project(project_id)
        self._known_roots.pop(project_id, None)
        self._frozen_projects.discard(project_id)
        self._room_projects = {
            room_id: owner for room_id, owner in self._room_projects.items() if owner != project_id
        }

    async def count_project_records(self, project_id: str) -> int:
        """Lifecycle-only Room count, including a frozen archived Project Root."""

        roots = await self._resolved_roots()
        locator = roots.get(project_id)
        if not locator:
            return len(await self._legacy.list(project_id=project_id))
        existing = self._stores.get(project_id)
        if existing is not None:
            return len(await existing.list(project_id=project_id))
        store = RoomStore(ProjectPaths.from_locator(locator).room_db_path)
        await store.start()
        try:
            return len(await store.list(project_id=project_id))
        finally:
            await store.stop()

    async def _locate(self, room_id: str) -> RoomStore | None:
        project_id = self._room_projects.get(room_id)
        if project_id is not None:
            return await self._project_store(project_id)
        for store in await self._all_stores():
            room = await store.get(room_id)
            if room is not None:
                self._room_projects[room_id] = room.project_id
                return store
        return None

    async def upsert(self, room: RoomRecord) -> None:
        await (await self._project_store(room.project_id)).upsert(room)
        self._room_projects[room.room_id] = room.project_id

    async def get(self, room_id: str) -> RoomRecord | None:
        store = await self._locate(room_id)
        return None if store is None else await store.get(room_id)

    async def update(self, room_id: str, **kwargs: Any) -> RoomRecord | None:
        store = await self._locate(room_id)
        return None if store is None else await store.update(room_id, **kwargs)

    async def list(self, **kwargs: Any) -> list[RoomRecord]:
        project_id = kwargs.get("project_id")
        if project_id is not None:
            return await (await self._project_store(project_id)).list(**kwargs)
        records: list[RoomRecord] = []
        for store in await self._all_stores():
            records.extend(await store.list(**kwargs))
        records.sort(key=lambda r: r.created_at)
        return records

    async def delete(self, room_id: str, **kwargs: Any) -> bool:
        store = await self._locate(room_id)
        return False if store is None else await store.delete(room_id, **kwargs)

    async def count_logs(self, room_id: str) -> int:
        store = await self._locate(room_id)
        return 0 if store is None else await store.count_logs(room_id)

    async def get_log_by_id(self, room_id: str, entry_id: str) -> RoomLogRecord | None:
        """从项目 Room store 查询已有消息投影。"""
        store = await self._locate(room_id)
        return None if store is None else await store.get_log_by_id(room_id, entry_id)

    async def append_log(self, room_id: str, record_factory: Any) -> Any:
        store = await self._locate(room_id)
        return None if store is None else await store.append_log(room_id, record_factory)

    async def get_log(self, room_id: str, **kwargs: Any) -> list[RoomLogRecord]:
        store = await self._locate(room_id)
        return [] if store is None else await store.get_log(room_id, **kwargs)

    async def get_log_page(self, room_id: str, **kwargs: Any) -> list[RoomLogRecord]:
        """按升序有界读取日志，不使用聊天窗口的尾部截取语义。"""
        store = await self._locate(room_id)
        return [] if store is None else await store.get_log_page(room_id, **kwargs)

    async def migrate_project(self, project_id: str) -> int:
        target = await self._project_store(project_id)
        if target is self._legacy:
            return 0
        rooms = await self._legacy.list(project_id=project_id)
        for room in rooms:
            logs = await self._legacy.get_log(room.room_id, limit=2_147_483_647)
            empty = room.model_copy(update={"seq_watermark": 0})
            await target.upsert(empty)
            for log in logs:
                await target.append_log(room.room_id, lambda _seq, item=log: item)
            await target.upsert(room)
            self._room_projects[room.room_id] = project_id
            await self._legacy.delete(room.room_id)
        return len(rooms)

    async def migrate_all(self) -> int:
        return sum(
            [
                await self.migrate_project(pid)
                for pid in await self._resolved_roots()
                if pid not in self._frozen_projects
            ]
        )


class ProjectScopedChatActionStore:
    """Routes chat actions to a project-local SQLite database."""

    def __init__(self, roots: ProjectRoots) -> None:
        self._roots = roots
        self._stores: dict[str, ChatActionStore] = {}

    async def start(self) -> None:
        for project_id in await self._roots():
            await self.store(project_id)

    async def stop(self) -> None:
        for store in self._stores.values():
            await store.stop()
        self._stores.clear()

    async def close_project(self, project_id: str) -> None:
        """Close a project-local ChatAction database when the project archives."""

        store = self._stores.pop(project_id, None)
        if store is not None:
            await store.stop()

    async def restore_project(self, project_id: str) -> None:
        """Reopen lazily after project restore; no eager database access."""

        return None

    async def evict_project(self, project_id: str) -> None:
        """Drop cached state after permanent project deletion."""

        await self.close_project(project_id)

    async def store(self, project_id: str) -> ChatActionStore:
        roots = await self._roots()
        locator = roots.get(project_id)
        if not locator:
            raise ValueError(f"project root unavailable: {project_id}")
        store = self._stores.get(project_id)
        if store is None:
            store = ChatActionStore(ProjectPaths.from_locator(locator).chat_action_db_path)
            await store.start()
            self._stores[project_id] = store
        return store
