# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""业务数据源经真实 Subject 命令路由落库、幂等、分页与生命周期验证。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from ghrah.protocol.payloads.material import MaterialStateRegisterPayload
from ouroboros import Context

from ghrah.subject.config import ProjectConfig, SubjectConfig
from ghrah.subject.material.state_store import MaterialStateStore
from ghrah.subject.room.models import make_room_log_record
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.units import mount_builtin_units


@contextmanager
def _sqlite(path: Path) -> Iterator[sqlite3.Connection]:
    """成功时提交并**总是关闭**连接（Windows 上未关闭会锁住项目目录删除）。"""
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _config(root: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(root / "workspace"),
        db_path=str(root / "subject.db"),
        manifest_root=str(root / "manifests"),
        project_slice=ProjectConfig(
            default_root_locator_template=str(root / "projects/{project_id}")
        ),
    )


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result["success"], result
    return result["data"]


def _registration(project_id: str) -> dict[str, Any]:
    properties = {
        "quantity": {"type": "integer", "minimum": 1, "title": "需求数量"},
        "customer": {"type": "string", "maxLength": 120, "title": "客户名称"},
    }
    return {
        "project_id": project_id,
        "material_id": "requirement",
        "material_version": "1",
        "title": "需求核对",
        "initial_data": {"quantity": 24, "customer": ""},
        "data_schema": {
            "type": "object",
            "properties": properties,
            "required": ["quantity", "customer"],
            "additionalProperties": False,
        },
        "patch_schema": {"type": "object", "properties": properties},
    }


async def _append(ctx: Context, room_id: str, patch: Any, author_type: str = "agent") -> Any:
    return await ctx.get("room_store").append_log(
        room_id,
        lambda seq: make_room_log_record(
            room_id=room_id,
            seq=seq,
            author="test-agent",
            author_type=author_type,
            data={"message": f"```requirement.patch\n{json.dumps(patch)}\n```"},
        ),
    )


async def test_material_state_persists_receipts_and_freezes_with_project(tmp_path: Path) -> None:
    config = _config(tmp_path)
    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        project = _data(await bridge_command(ctx, "project_create", {"name": "persistent"}))[
            "project"
        ]
        pid = project["project_id"]
        room = _data(await bridge_command(ctx, "room_create", {"project_id": pid, "name": "对话"}))[
            "room"
        ]
        rid = room["room_id"]
        empty = _data(await bridge_command(ctx, "material_state_get", {"project_id": pid}))
        assert empty == {"state": None, "applications": [], "has_more": False}
        envelope = _data(
            await bridge_command(
                ctx,
                "material_state_get",
                {
                    "project_id": pid,
                    "request_id": "request",
                    "session_id": "session",
                },
            )
        )
        assert envelope == empty
        extra = await bridge_command(
            ctx, "material_state_get", {"project_id": pid, "unknown": True}
        )
        assert not extra["success"]
        # A proposal made before this data source was bound is not initial data.
        await _append(ctx, rid, {"quantity": 999, "customer": "未绑定时的旧提案"})
        registration = _registration(pid)
        initial = _data(await bridge_command(ctx, "material_state_register", registration))
        assert initial["state"]["revision"] == 0
        assert initial["state"]["data"] == {"quantity": 24, "customer": ""}
        good, _ = await _append(ctx, rid, {"quantity": 50, "customer": "多拉"})
        bad, _ = await _append(ctx, rid, {"quantity": 0, "customer": "不应写入"})
        await _append(ctx, rid, {"quantity": 999}, "human")
        noop, _ = await _append(ctx, rid, {"quantity": 50})
        await _append(ctx, rid, {"undeclared": True})
        more, pages = True, 0
        while more:
            result = _data(
                await bridge_command(
                    ctx,
                    "material_state_sync",
                    {
                        "project_id": pid,
                        "room_id": rid,
                        "limit": 2,
                    },
                )
            )
            more = result["has_more"]
            pages += 1
        assert pages == 3
        assert result["state"]["data"] == {"quantity": 50, "customer": "多拉"}
        assert result["state"]["revision"] == 1
        events = {item["room_log_id"]: item for item in result["applications"]}
        assert events[good.id]["status"] == "applied"
        assert events[good.id]["fields"][0]["label"] == "需求数量"
        assert events[bad.id]["status"] == "rejected"
        assert events[noop.id]["status"] == "unchanged"
        repeated = _data(
            await bridge_command(ctx, "material_state_sync", {"project_id": pid, "room_id": rid})
        )
        assert repeated == result
        assert _data(await bridge_command(ctx, "material_state_register", registration)) == result
        conflict = await bridge_command(
            ctx, "material_state_register", {**registration, "material_version": "2"}
        )
        assert conflict["error"] == "material_state_contract_conflict"
        changed = {**registration, "initial_data": {"quantity": 30, "customer": ""}}
        assert not (await bridge_command(ctx, "material_state_register", changed))["success"]
        project = _data(
            await bridge_command(
                ctx,
                "project_archive",
                {
                    "project_id": pid,
                    "expected_version": project["version"],
                },
            )
        )["project"]
        assert (await bridge_command(ctx, "material_state_get", {"project_id": pid}))[
            "error"
        ] == "project_archived"

    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        assert (await bridge_command(ctx, "material_state_get", {"project_id": pid}))[
            "error"
        ] == "project_archived"
        project = _data(
            await bridge_command(
                ctx,
                "project_restore",
                {
                    "project_id": pid,
                    "expected_version": project["version"],
                },
            )
        )["project"]
        restored = _data(await bridge_command(ctx, "material_state_get", {"project_id": pid}))
        assert restored == result
        assert _data(await bridge_command(ctx, "material_state_register", registration)) == restored
        db = tmp_path / "projects" / pid / "db/material-state.sqlite3"
        with _sqlite(db) as connection:
            assert (
                connection.execute("SELECT count(*) FROM material_patch_events").fetchone()[0] == 4
            )
        _data(
            await bridge_command(
                ctx,
                "project_delete",
                {
                    "project_id": pid,
                    "expected_version": project["version"],
                    "cascade_rooms": True,
                },
            )
        )
        assert not db.exists()
        assert not (await bridge_command(ctx, "material_state_get", {"project_id": pid}))["success"]


async def test_sync_catches_up_beyond_chat_window_and_is_project_scoped(tmp_path: Path) -> None:
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="full")
        projects = [
            _data(await bridge_command(ctx, "project_create", {"name": name}))["project"]
            for name in ("first", "second")
        ]
        ids = [p["project_id"] for p in projects]
        rid = _data(
            await bridge_command(ctx, "room_create", {"project_id": ids[0], "name": "room"})
        )["room"]["room_id"]
        for pid in ids:
            _data(await bridge_command(ctx, "material_state_register", _registration(pid)))
        await _append(ctx, rid, {"customer": "第一页的修改"})
        for quantity in range(1, 211):
            await _append(ctx, rid, {"quantity": quantity})
        request = {"project_id": ids[0], "room_id": rid}
        first = _data(await bridge_command(ctx, "material_state_sync", request))
        assert first["has_more"]
        # Two clients catching up simultaneously must not double-apply anything.
        results = await asyncio.gather(
            *[bridge_command(ctx, "material_state_sync", request) for _ in range(2)]
        )
        for result in results:
            state = _data(result)["state"]
            assert state["data"] == {"quantity": 210, "customer": "第一页的修改"}
            assert state["revision"] == 211
        untouched = _data(await bridge_command(ctx, "material_state_get", {"project_id": ids[1]}))
        assert untouched["state"]["data"] == {"quantity": 24, "customer": ""}
        assert not (
            await bridge_command(ctx, "material_state_sync", {"project_id": ids[1], "room_id": rid})
        )["success"]


async def test_registration_rejects_bad_data_and_external_schema_refs(tmp_path: Path) -> None:
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="full")
        pid = _data(await bridge_command(ctx, "project_create", {"name": "invalid"}))["project"][
            "project_id"
        ]
        request = _registration(pid)
        request["initial_data"]["quantity"] = 0
        assert not (await bridge_command(ctx, "material_state_register", request))["success"]
        request = _registration(pid)
        request["data_schema"]["properties"]["quantity"] = {
            "$ref": "https://example.invalid/schema.json"
        }
        assert not (await bridge_command(ctx, "material_state_register", request))["success"]
        assert (
            _data(await bridge_command(ctx, "material_state_get", {"project_id": pid}))["state"]
            is None
        )


async def test_legacy_database_upgrade_preserves_values_and_does_not_replay_history(
    tmp_path: Path,
) -> None:
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="full")
        project = _data(await bridge_command(ctx, "project_create", {"name": "legacy"}))["project"]
        pid = project["project_id"]
        rid = _data(await bridge_command(ctx, "room_create", {"project_id": pid, "name": "room"}))[
            "room"
        ]["room_id"]
        await _append(ctx, rid, {"quantity": 30})
        old, _ = await _append(ctx, rid, {"quantity": 50})
        await _append(ctx, rid, {"customer": "迁移后的新消息"})
        request = _registration(pid)
        contract = {key: request[key] for key in ("data_schema", "patch_schema")}
        db_path = tmp_path / "projects" / pid / "db/material-state.sqlite3"
        with _sqlite(db_path) as db:
            db.executescript("""
                CREATE TABLE material_state (
                    project_id TEXT PRIMARY KEY, material_id TEXT, material_version TEXT,
                    title TEXT, data_json TEXT, contract_json TEXT,
                    revision INTEGER, updated_at TEXT
                );
                CREATE TABLE material_patch_events (
                    event_id TEXT PRIMARY KEY, room_log_id TEXT UNIQUE, patch_json TEXT,
                    status TEXT, revision INTEGER, error TEXT, created_at TEXT
                );
            """)
            db.execute(
                "INSERT INTO material_state VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    "requirement",
                    "1",
                    "需求核对",
                    json.dumps({"quantity": 50, "customer": ""}),
                    json.dumps(contract),
                    2,
                    "2026-11-17T00:00:00Z",
                ),
            )
            db.execute(
                "INSERT INTO material_patch_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "old-event",
                    old.id,
                    '{"quantity":50}',
                    "applied",
                    2,
                    "",
                    "2026-11-17T00:00:00Z",
                ),
            )
        registered = _data(await bridge_command(ctx, "material_state_register", request))
        assert registered["state"]["data"]["quantity"] == 50
        result = _data(
            await bridge_command(ctx, "material_state_sync", {"project_id": pid, "room_id": rid})
        )
        assert result["state"]["data"] == {"quantity": 50, "customer": "迁移后的新消息"}
        assert result["state"]["revision"] == 3
        assert len(result["applications"]) == 2
        assert (
            _data(
                await bridge_command(
                    ctx, "material_state_sync", {"project_id": pid, "room_id": rid}
                )
            )
            == result
        )


async def test_failed_transaction_rolls_back_data_receipt_and_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MaterialStateStore(tmp_path / "state.sqlite3", "p")
    await store.start()
    try:
        await store.register(MaterialStateRegisterPayload.model_validate(_registration("p")))
        entry = make_room_log_record(
            room_id="r",
            seq=1,
            author="agent",
            author_type="agent",
            data={"message": '```requirement.patch\n{"quantity":50}\n```'},
        )
        connection = store._connection()
        execute = connection.execute

        def fail_receipt(sql: str, *args: Any, **kwargs: Any) -> Any:
            if sql.startswith("INSERT INTO material_patch_events"):
                raise sqlite3.OperationalError("simulated write failure")
            return execute(sql, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(connection, "execute", fail_receipt)
            with pytest.raises(sqlite3.OperationalError, match="write failure"):
                await store.sync("r", [entry])
        result = await store.get()
        assert result["state"]["data"]["quantity"] == 24
        assert result["state"]["revision"] == 0
        assert result["applications"] == []
        assert await store.cursor("r") == 0
        retried = await store.sync("r", [entry])
        assert retried["state"]["data"]["quantity"] == 50
        assert len(retried["applications"]) == 1
    finally:
        await store.stop()


async def test_sync_reads_message_authority_and_does_not_skip_missing_references(
    tmp_path: Path,
) -> None:
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="full")
        pid = _data(await bridge_command(ctx, "project_create", {"name": "mm-authority"}))[
            "project"
        ]["project_id"]
        rid = _data(await bridge_command(ctx, "room_create", {"project_id": pid, "name": "room"}))[
            "room"
        ]["room_id"]
        _data(await bridge_command(ctx, "material_state_register", _registration(pid)))
        mm_path = tmp_path / "projects" / pid / "db/action-chains.sqlite3"
        with _sqlite(mm_path) as db:
            db.executescript("""
                CREATE TABLE mm_messages (message_id TEXT PRIMARY KEY, sender_id TEXT,
                                          content TEXT, metadata TEXT, reply_to TEXT);
                CREATE TABLE mm_deliveries (delivery_id TEXT, message_id TEXT,
                    recipient_id TEXT, state TEXT, error TEXT, blocked_reason TEXT, seq INTEGER);
            """)
            db.execute(
                "INSERT INTO mm_messages VALUES (?, ?, ?, ?, NULL)",
                (
                    "message-1",
                    "agent",
                    '```requirement.patch\n{"quantity":50}\n```',
                    json.dumps({"room_ids": [rid]}),
                ),
            )
        for seq in (1, 2):
            await ctx.get("room_store").append_log(
                rid,
                lambda assigned, index=seq: make_room_log_record(
                    room_id=rid,
                    seq=assigned,
                    author="agent",
                    author_type="agent",
                    data={"mm_message_id": f"message-{index}"},
                ),
            )
        request = {"project_id": pid, "room_id": rid, "limit": 1}
        first = _data(await bridge_command(ctx, "material_state_sync", request))
        assert first["state"]["data"]["quantity"] == 50
        missing = await bridge_command(ctx, "material_state_sync", request)
        assert not missing["success"]
        with _sqlite(tmp_path / "projects" / pid / "db/material-state.sqlite3") as db:
            assert db.execute("SELECT seq FROM material_sync_cursors").fetchone()[0] == 1
        with _sqlite(mm_path) as db:
            db.execute(
                "INSERT INTO mm_messages VALUES (?, ?, ?, ?, NULL)",
                (
                    "message-2",
                    "agent",
                    '```requirement.patch\n{"quantity":60}\n```',
                    json.dumps({"room_ids": [rid]}),
                ),
            )
        final = _data(await bridge_command(ctx, "material_state_sync", request))
        assert final["state"]["data"]["quantity"] == 60
        assert final["state"]["revision"] == 2
        records = await ctx.get("room_store").get_log(rid)
        assert all("message" not in record.data for record in records)
