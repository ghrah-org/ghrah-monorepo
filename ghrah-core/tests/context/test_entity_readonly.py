"""离线实体上下文查询不创建、迁移或修改权威。"""

import sqlite3
from pathlib import Path

import pytest

from ghrah.context.persistence.sqlite_backend import SqliteBackend


async def test_readonly_does_not_create_database(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        async with SqliteBackend(path, read_only=True):
            pass
    assert not path.exists()


async def test_readonly_empty_and_legacy_databases(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite"
    with sqlite3.connect(path):
        pass
    async with SqliteBackend(path, read_only=True) as backend:
        assert await backend.load_checkpoint("a") is None
        assert await backend.load_session_catalog("a") == {
            "active_session_id": None,
            "sessions": [],
            "branches": [],
        }
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
        db.execute("CREATE TABLE sessions (legacy TEXT)")
    with pytest.raises(RuntimeError, match="Unsupported context database schema"):
        async with SqliteBackend(path, read_only=True):
            pass
