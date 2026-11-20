# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""RoomStore 单测：CRUD / 乐观锁 / seq 单点分配 / append-only 日志 / 级联删除。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ghrah.subject.room.models import (
    RoomRecord,
    make_room_log_record,
    make_room_record,
)
from ghrah.subject.room.store import ConcurrentModificationError, RoomStore


async def _started(tmp_path: Path) -> RoomStore:
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()
    return store


def _data(result: dict) -> dict:
    assert result["success"], result
    return result["data"]


async def test_room_crud_roundtrip(tmp_path: Path) -> None:
    store = await _started(tmp_path)
    try:
        record = make_room_record(project_id="proj-1", name="architecture")
        await store.upsert(record)

        loaded = await store.get(record.room_id)
        assert loaded is not None
        assert loaded.name == "architecture"
        assert loaded.project_id == "proj-1"
        assert loaded.status == "active"
        assert loaded.members == []
        assert loaded.seq_watermark == 0
        assert loaded.version == 1
        assert loaded.archived_at is None

        assert await store.list(project_id="proj-1") == [record]
        assert await store.list(project_id="proj-other") == []
        assert await store.list() == [record]
    finally:
        await store.stop()


async def test_update_optimistic_lock(tmp_path: Path) -> None:
    store = await _started(tmp_path)
    try:
        record = make_room_record(project_id="p", name="n")
        await store.upsert(record)

        def rename(r: RoomRecord) -> RoomRecord:
            return r.model_copy(update={"name": "renamed"})

        updated = await store.update(record.room_id, expected_version=None, mutator=rename)
        assert updated is not None
        assert updated.name == "renamed"
        assert updated.version == 2

        with pytest.raises(ConcurrentModificationError):
            await store.update(record.room_id, expected_version=1, mutator=rename)

        missing = await store.update("nope", expected_version=None, mutator=rename)
        assert missing is None
    finally:
        await store.stop()


async def test_append_log_allocates_monotonic_seq(tmp_path: Path) -> None:
    store = await _started(tmp_path)
    try:
        room = make_room_record(project_id="p", name="n")
        await store.upsert(room)

        seqs = []
        for i in range(5):
            result = await store.append_log(
                room.room_id,
                lambda seq, i=i: make_room_log_record(
                    room_id=room.room_id,
                    seq=seq,
                    author="architect",
                    author_type="agent",
                    data={"message": f"m{i}"},
                ),
            )
            assert result is not None
            record, updated_room = result
            seqs.append(record.seq)
            assert updated_room.seq_watermark == record.seq
            assert record.data == {"message": f"m{i}"}

        assert seqs == [1, 2, 3, 4, 5]

        loaded = await store.get(room.room_id)
        assert loaded is not None
        assert loaded.seq_watermark == 5

        missing = await store.append_log(
            "nope",
            lambda seq: make_room_log_record(
                room_id="nope", seq=seq, author="a", author_type="agent"
            ),
        )
        assert missing is None
    finally:
        await store.stop()


async def test_get_log_since_and_limit(tmp_path: Path) -> None:
    store = await _started(tmp_path)
    try:
        room = make_room_record(project_id="p", name="n")
        await store.upsert(room)
        for i in range(10):
            result = await store.append_log(
                room.room_id,
                lambda seq, i=i: make_room_log_record(
                    room_id=room.room_id,
                    seq=seq,
                    author="a",
                    author_type="agent",
                    data={"i": i},
                ),
            )
            assert result is not None

        full = await store.get_log(room.room_id)
        assert [e.seq for e in full] == list(range(1, 11))

        since = await store.get_log(room.room_id, since_seq=7)
        assert [e.seq for e in since] == [8, 9, 10]

        tail = await store.get_log(room.room_id, limit=3)
        assert [e.seq for e in tail] == [8, 9, 10]

        empty = await store.get_log("nope")
        assert empty == []
    finally:
        await store.stop()


async def test_log_page_uses_room_scope_and_drains_incremental_pages(tmp_path: Path) -> None:
    path = tmp_path / "rooms.sqlite3"
    reader = RoomStore(path)
    assert await reader.get_log_page("missing", project_id="p") is None
    assert not path.exists()
    store = RoomStore(path)
    await store.start()
    try:
        room = make_room_record(project_id="p", name="Room")
        await store.upsert(room)
        for _ in range(7):
            await store.append_log(
                room.room_id,
                lambda seq: make_room_log_record(
                    room_id=room.room_id,
                    seq=seq,
                    author="user",
                    author_type="human",
                ),
            )
        assert await reader.get_log_page(room.room_id, project_id="foreign") is None
        tail = await reader.get_log_page(room.room_id, project_id="p", limit=2)
        assert tail is not None
        assert [entry.seq for entry in tail.entries] == [6, 7]
        assert tail.has_more and tail.next_before_seq == 6
        earlier = await reader.get_log_page(
            room.room_id, project_id="p", before_seq=tail.next_before_seq, limit=2
        )
        assert earlier is not None
        assert [entry.seq for entry in earlier.entries] == [4, 5]

        cursor = 0
        sequences: list[int] = []
        while True:
            page = await reader.get_log_page(
                room.room_id, project_id="p", since_seq=cursor, limit=2
            )
            assert page is not None and page.latest_seq == 7
            assert page.next_since_seq is not None and page.next_since_seq > cursor
            assert page.next_before_seq is None
            cursor = page.next_since_seq
            sequences.extend(entry.seq for entry in page.entries)
            if not page.has_more:
                break
        assert sequences == list(range(1, 8))
        empty = await reader.get_log_page(room.room_id, project_id="p", since_seq=cursor, limit=2)
        assert empty is not None
        assert empty.entries == []
        assert empty.next_since_seq == empty.latest_seq == cursor
    finally:
        await store.stop()


async def test_delete_cascades_logs_and_guards(tmp_path: Path) -> None:
    store = await _started(tmp_path)
    try:
        room = make_room_record(project_id="p", name="n")
        await store.upsert(room)
        result = await store.append_log(
            room.room_id,
            lambda seq: make_room_log_record(
                room_id=room.room_id, seq=seq, author="a", author_type="agent"
            ),
        )
        assert result is not None
        assert await store.count_logs(room.room_id) == 1

        with pytest.raises(ConcurrentModificationError):
            await store.delete(room.room_id, expected_version=99)
        assert await store.count_logs(room.room_id) == 1

        assert await store.delete(room.room_id, expected_version=room.version) is True
        assert await store.delete(room.room_id) is False
        assert await store.count_logs(room.room_id) == 0
        assert await store.get(room.room_id) is None
    finally:
        await store.stop()


async def test_start_migrates_pre_archive_room_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-rooms.db"
    db = sqlite3.connect(db_path)
    db.execute(
        """
        CREATE TABLE subject_rooms (
            room_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            members TEXT NOT NULL DEFAULT '[]',
            seq_watermark INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    db.commit()
    db.close()

    store = RoomStore(db_path)
    await store.start()
    try:
        room = make_room_record(project_id="legacy-project", name="legacy")
        await store.upsert(room)
        loaded = await store.get(room.room_id)
        assert loaded is not None
        assert loaded.archived_at is None
    finally:
        await store.stop()

    db = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(subject_rooms)")}
        assert "archived_at" in columns
    finally:
        db.close()


async def test_start_rebuilds_reference_index_for_existing_room_logs(tmp_path: Path) -> None:
    """重开存量 Room 库时补建引用索引并保留日志和房间水位。"""
    path = tmp_path / "rooms.db"
    store = await _started(tmp_path)
    try:
        room = make_room_record(project_id="project", name="room")
        await store.upsert(room)
        appended = await store.append_log(
            room.room_id,
            lambda seq: make_room_log_record(
                room_id=room.room_id, seq=seq, author="agent", author_type="agent"
            ),
        )
        assert appended is not None
        entry, updated_room = appended
    finally:
        await store.stop()

    with closing(sqlite3.connect(path)) as db:
        db.execute("DROP INDEX IF EXISTS idx_subject_room_log_reference")

    for _ in range(2):
        store = await _started(tmp_path)
        try:
            assert await store.get_log_by_id(room.room_id, entry.id) == entry
            assert await store.get(room.room_id) == updated_room
        finally:
            await store.stop()
        with closing(sqlite3.connect(path)) as db:
            columns = db.execute("PRAGMA index_info('idx_subject_room_log_reference')")
            assert [row[2] for row in columns] == ["room_id", "id"]
