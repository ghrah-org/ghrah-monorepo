# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 模板 Store：DDL/component、CRUD、CAS、归档、快照与写入规范化。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ghrah.subject.project.store import ProjectStore
from ghrah.subject.template_store import (
    AgentTemplateConfig,
    AgentTemplateService,
    TemplateConflictError,
    TemplateIdentityConflictError,
    TemplateNotFoundError,
    TemplateStore,
)
from ghrah.subject.template_store.models import snapshot_digest

DB_FILE = "subject.db"


def _config(**payload: Any) -> AgentTemplateConfig:
    """构造规范化配置；``payload`` 覆盖 metadata 与各组。"""
    metadata = {"namespace": "writing", "name": "weekly_report"}
    metadata.update(payload.pop("metadata", {}))
    return AgentTemplateConfig.model_validate({"metadata": metadata, **payload})


@contextmanager
def _inspect(path: Path) -> Iterator[sqlite3.Connection]:
    """只读检查同库 schema（独立同步连接）。"""
    db = sqlite3.connect(str(path))
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[TemplateStore]:
    opened = TemplateStore(tmp_path / DB_FILE)
    await opened.start()
    yield opened
    await opened.stop()


@pytest.fixture
async def service(store: TemplateStore) -> AsyncIterator[AgentTemplateService]:
    yield AgentTemplateService(store)


# ─── DDL / component ───


async def test_ddl_self_creates_version_table_without_project_store(tmp_path: Path) -> None:
    """coexistence profile 不挂 ProjectUnit：版本表必须由本 store 自建。"""
    path = tmp_path / DB_FILE
    opened = TemplateStore(path)
    await opened.start()
    await opened.stop()

    with _inspect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        columns = [row[1] for row in db.execute("PRAGMA table_info(agent_templates)")]
        versions = {
            row[0]: row[1]
            for row in db.execute("SELECT component, version FROM subject_schema_versions")
        }

    assert {"subject_schema_versions", "agent_templates", "agent_template_revisions"} <= tables
    assert {
        "uq_agent_templates_identity",
        "idx_agent_templates_namespace",
        "idx_agent_templates_archived",
    } <= indexes
    assert len(columns) == 18
    assert versions == {"agent_template_store": 1}


async def test_component_versions_coexist_with_project_store(tmp_path: Path) -> None:
    """与 ProjectStore 共库：模板 store 不推动 project_store 的版本（保持 v6）。"""
    path = tmp_path / DB_FILE
    projects = ProjectStore(path)
    await projects.start()
    templates = TemplateStore(path)
    await templates.start()
    try:
        with _inspect(path) as db:
            versions = {
                row[0]: row[1]
                for row in db.execute("SELECT component, version FROM subject_schema_versions")
            }
        assert versions["project_store"] == 6
        assert versions["agent_template_store"] == 1
    finally:
        await templates.stop()
        await projects.stop()


async def test_component_version_newer_than_code_fails_fast(tmp_path: Path) -> None:
    path = tmp_path / DB_FILE
    opened = TemplateStore(path)
    await opened.start()
    await opened.stop()
    with _inspect(path) as db:
        db.execute(
            "UPDATE subject_schema_versions SET version = 2 WHERE component = ?",
            ("agent_template_store",),
        )
        db.commit()

    newer = TemplateStore(path)
    with pytest.raises(RuntimeError, match="newer than supported"):
        await newer.start()


# ─── CRUD 与默认值 ───


async def test_create_applies_defaults_and_full_snapshot(store: TemplateStore) -> None:
    record = await store.create(_config())
    assert len(record.template_id) == 32
    assert record.revision == 1
    assert record.archived_at is None
    # T0 v3.1：max_iterations 默认 -1（关闭限制）；communication_timeout 默认 300
    assert record.context_policy.max_iterations == -1
    assert record.context_policy.communication_timeout == 300
    assert record.model.endpoint_name is None
    assert record.prompt.system_prompt == ""
    assert record.permission_request.require_approval_by_default is True
    assert record.metadata.tags == []
    assert record.path_request == []
    # 快照 = 规范化配置对象；不含服务端字段
    snapshot = await store.get_revision(record.template_id, 1)
    assert snapshot is not None
    assert set(snapshot) == {
        "metadata",
        "model",
        "prompt",
        "context_policy",
        "ability_request",
        "permission_request",
        "path_request",
    }
    assert snapshot["context_policy"]["max_iterations"] == -1
    assert snapshot["model"] == {"endpoint_name": None, "model_overrides": None}


async def test_get_by_identity_and_list_filters(store: TemplateStore) -> None:
    first = await store.create(_config(metadata={"tags": ["ops"]}))
    await store.create(
        _config(metadata={"name": "reviewer", "tags": ["writing"], "deprecated": True})
    )
    assert (await store.get_by_identity("writing", "weekly_report")) is not None
    assert (await store.get_by_identity("writing", "missing")) is None

    assert len(await store.list()) == 2
    assert len(await store.list(namespace="writing")) == 2
    assert [r.metadata.name for r in await store.list(name="reviewer")] == ["reviewer"]
    assert [r.template_id for r in await store.list(tag="ops")] == [first.template_id]
    assert [r.metadata.name for r in await store.list(deprecated=True)] == ["reviewer"]
    assert len(await store.list(deprecated=False)) == 1


async def test_update_bumps_revision_and_writes_snapshot(store: TemplateStore) -> None:
    created = await store.create(_config())
    updated = await store.update(
        created.template_id,
        1,
        _config(
            prompt={"system_prompt": "先列事实"}, metadata={"title": "周报", "tags": ["b", "a"]}
        ),
    )
    assert updated.revision == 2
    assert updated.prompt.system_prompt == "先列事实"
    assert updated.metadata.title == "周报"
    assert updated.metadata.tags == ["a", "b"]
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at

    history = await store.history(created.template_id)
    assert [item.revision for item in history] == [1, 2]
    assert history[1].digest == snapshot_digest(
        json.dumps(
            (await store.get_revision(created.template_id, 2)),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )


# ─── CAS 与身份冲突 ───


async def test_cas_conflict_reports_current_revision(store: TemplateStore) -> None:
    created = await store.create(_config())
    await store.update(created.template_id, 1, _config(prompt={"system_prompt": "v2"}))
    with pytest.raises(TemplateConflictError) as error:
        await store.update(created.template_id, 1, _config(prompt={"system_prompt": "stale"}))
    assert error.value.current_revision == 2
    # 冲突不写快照
    assert [item.revision for item in await store.history(created.template_id)] == [1, 2]


async def test_update_missing_template_is_not_found(store: TemplateStore) -> None:
    with pytest.raises(TemplateNotFoundError):
        await store.update("missing", 1, _config())


async def test_identity_conflict_until_archived(store: TemplateStore) -> None:
    created = await store.create(_config())
    with pytest.raises(TemplateIdentityConflictError):
        await store.create(_config(metadata={"title": "重名"}))
    archived = await store.archive(created.template_id, 1)
    assert archived.archived_at is not None
    # 部分唯一索引只约束 archived_at IS NULL：归档后可再建同名模板
    recreated = await store.create(_config())
    assert recreated.template_id != created.template_id


# ─── 归档语义 ───


async def test_archive_only_sets_archived_at(store: TemplateStore) -> None:
    created = await store.create(_config())
    await store.update(created.template_id, 1, _config(prompt={"system_prompt": "v2"}))
    before = await store.history(created.template_id)

    archived = await store.archive(created.template_id, 2)
    assert archived.archived_at is not None
    assert archived.revision == 2
    assert archived.metadata.name == created.metadata.name
    # 不改 revision、不写快照
    assert await store.history(created.template_id) == before
    # 归档后仍可按 id 读到（template_origin 悬空可读）
    assert (await store.get(created.template_id)) is not None
    assert await store.get(created.template_id, include_archived=False) is None
    # 列表默认排除归档
    assert await store.list() == []
    assert [r.template_id for r in await store.list(include_archived=True)] == [created.template_id]


async def test_archive_is_idempotent_and_blocks_edits(store: TemplateStore) -> None:
    created = await store.create(_config())
    first = await store.archive(created.template_id, 1)
    second = await store.archive(created.template_id, 1)
    assert first.archived_at == second.archived_at
    with pytest.raises(TemplateConflictError) as error:
        await store.archive(created.template_id, 2)
    assert error.value.current_revision == 1

    with pytest.raises(TemplateConflictError) as edit:
        await store.update(created.template_id, 1, _config(prompt={"system_prompt": "x"}))
    assert edit.value.archived is True
    assert edit.value.current_revision == 1


# ─── 写入规范化 ───


async def test_normalizes_lists_and_member_order(store: TemplateStore) -> None:
    record = await store.create(
        _config(
            metadata={"tags": ["b", "a", "b"]},
            ability_request={
                "surface": {"mode": "static", "static": ["z", "a", "z"]},
                "packages": [
                    {
                        "package": "writing.beta",
                        "origin": "builtin",
                        "operations": [
                            {"key": "publish"},
                            {"key": "draft", "visible": False, "available": False},
                        ],
                        "skills": [
                            {
                                "key": "style",
                                "scripts": [
                                    {"key": "lint", "exec": True},
                                    {"key": "fix"},
                                ],
                            },
                            {"key": "format"},
                        ],
                    },
                    {"package": "writing.alpha", "origin": "mcp", "whole_container": True},
                ],
                "mcp_refs": ["conn.b", "conn.a"],
            },
            permission_request={
                "auto_approve": ["ghrah.fs.write_file", "ghrah.fs.read_file"],
                "safe_extra_commands": ["ls", "cat"],
                "safe_extra_sub_commands": [["git", ["status", "diff"]], ["ls", ["-l"]]],
            },
            path_request=[
                {
                    "scope": "package",
                    "package": "writing.beta",
                    "workspace_role": "default",
                    "subpath": "docs",
                    "access": "write",
                    "deny": False,
                },
                {
                    "scope": "agent",
                    "workspace_role": "default",
                    "subpath": ".",
                    "access": "read",
                    "deny": True,
                },
            ],
        )
    )
    assert record.metadata.tags == ["a", "b"]
    assert record.ability_request.surface.static == ["a", "z"]
    assert [item.package for item in record.ability_request.packages] == [
        "writing.alpha",
        "writing.beta",
    ]
    beta = record.ability_request.packages[1]
    assert [item.key for item in beta.operations] == ["draft", "publish"]
    assert [item.key for item in beta.skills] == ["format", "style"]
    assert [item.key for item in beta.skills[1].scripts] == ["lint", "fix"]
    assert record.permission_request.auto_approve == [
        "ghrah.fs.read_file",
        "ghrah.fs.write_file",
    ]
    assert record.permission_request.safe_extra_commands == ["cat", "ls"]
    assert record.permission_request.safe_extra_sub_commands == [
        ("git", ["status", "diff"]),
        ("ls", ["-l"]),
    ]
    assert [(item.scope, item.subpath) for item in record.path_request] == [
        ("agent", "."),
        ("package", "docs"),
    ]


async def test_snapshot_text_is_canonical(store: TemplateStore, tmp_path: Path) -> None:
    record = await store.create(_config(metadata={"tags": ["b", "a"]}))
    with _inspect(tmp_path / DB_FILE) as db:
        (text,) = db.execute(
            "SELECT snapshot FROM agent_template_revisions WHERE template_id = ? AND revision = ?",
            (record.template_id, 1),
        ).fetchone()
    assert text == json.dumps(
        json.loads(text), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert snapshot_digest(text) == "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    history = await store.history(record.template_id)
    assert history[0].size_bytes == len(text.encode("utf-8"))
    assert history[0].digest == snapshot_digest(text)


# ─── 校验 ───


@pytest.mark.parametrize(
    "payload",
    [
        {"metadata": {"namespace": "Writing", "name": "x"}},
        {"metadata": {"namespace": "writing", "name": "weekly-report"}},
        {"context_policy": {"max_iterations": -2}},
        {"context_policy": {"communication_timeout": -2}},
        {"context_policy": {"overrides": {"unknown": 1}}},
        {"context_policy": {"overrides": {"persistence": {"unknown": True}}}},
        {
            "ability_request": {
                "packages": [
                    {
                        "package": "writing.beta",
                        "origin": "builtin",
                        "whole_container": True,
                        "operations": [{"key": "draft"}],
                    }
                ]
            }
        },
        {
            "ability_request": {
                "packages": [
                    {
                        "package": "writing.beta",
                        "origin": "builtin",
                        "operations": [{"key": "a", "visible": False, "available": True}],
                    }
                ]
            }
        },
        {
            "ability_request": {
                "packages": [
                    {
                        "package": "writing.beta",
                        "origin": "builtin",
                        "operations": [{"key": "a"}, {"key": "a"}],
                    }
                ]
            }
        },
        {
            "ability_request": {
                "packages": [
                    {"package": "writing.beta", "origin": "builtin"},
                    {"package": "writing.beta", "origin": "mcp"},
                ]
            }
        },
        {
            "path_request": [
                {
                    "scope": "package",
                    "workspace_role": "default",
                    "subpath": ".",
                    "access": "read",
                    "deny": False,
                }
            ]
        },
        {
            "path_request": [
                {
                    "scope": "agent",
                    "package": "writing.beta",
                    "workspace_role": "default",
                    "subpath": ".",
                    "access": "read",
                    "deny": False,
                }
            ]
        },
        {
            "path_request": [
                {
                    "scope": "agent",
                    "workspace_role": "default",
                    "subpath": "../escape",
                    "access": "read",
                    "deny": False,
                }
            ]
        },
        {
            "path_request": [
                {
                    "scope": "agent",
                    "workspace_role": "default",
                    "subpath": "/abs",
                    "access": "read",
                    "deny": False,
                }
            ]
        },
    ],
)
async def test_invalid_write_is_rejected(store: TemplateStore, payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        await store.create(_config(**payload))


async def test_duplicate_path_request_is_rejected() -> None:
    item = {
        "scope": "agent",
        "workspace_role": "default",
        "subpath": "docs",
        "access": "read",
        "deny": False,
    }
    with pytest.raises(ValidationError):
        AgentTemplateConfig.model_validate(
            {
                "metadata": {"namespace": "writing", "name": "weekly_report"},
                "path_request": [item, {**item, "deny": True}],
            }
        )
