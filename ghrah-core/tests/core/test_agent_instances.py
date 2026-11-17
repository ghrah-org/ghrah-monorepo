# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 实例快照与装配输入解析。

两块覆盖：``agents.sqlite3`` 的读写与版本 fail-fast；``AgentAssemblySpec`` →
``ResolvedAgent`` 的配置映射与「未申请 ≠ 收紧」的成员权限语义。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from ghrah.protocol.views.core import AgentAssemblySpec

from ghrah.ability.source import BuiltinAbilitySource
from ghrah.core.agents import (
    AgentInstanceStore,
    AgentInstanceStoreError,
    AssemblyError,
    resolve_assembly,
)
from ghrah.manifest.types import PermissionFlags

FS_MEMBER = "ghrah.fs.read_file"


def _config(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": {"endpoint_name": "default-llm", "model_overrides": {"temperature": 0.3}},
        "prompt": {"system_prompt": "整理进展"},
        "limits": {"max_iterations": -1, "communication_timeout": 120},
        "context": {
            "window": {"max_tokens": 4096},
            "persistence": {"type": "json_file", "compress": True, "root_dir": "/tmp/ctx"},
        },
        "surface": {"mode": "static", "static": [FS_MEMBER], "max_static": 8},
        "mcp_refs": ["search"],
    }
    payload.update(overrides)
    return payload


def _spec(members: list[dict[str, Any]] | None = None) -> AgentAssemblySpec:
    return AgentAssemblySpec.model_validate(
        {"name": "weekly", "config": _config(), "members": members or [{"member": FS_MEMBER}]}
    )


def _source() -> BuiltinAbilitySource:
    return BuiltinAbilitySource()


async def _store(tmp_path: Path) -> AgentInstanceStore:
    store = AgentInstanceStore(tmp_path / "db" / "agents.sqlite3")
    await store.start()
    return store


async def test_snapshot_roundtrip(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        saved = await store.save(
            agent_id="a" * 32,
            name="weekly",
            config={"max_iterations": -1},
            members=[{"member": FS_MEMBER, "permissions": {"fs_write": True}}],
            surface={"mode": "auto"},
            mcp_refs=["search"],
            incarnation_id="inc-1",
        )
        assert saved.created_at == saved.updated_at

        loaded = await store.get("a" * 32)
        assert loaded is not None
        assert loaded.name == "weekly"
        assert loaded.config["max_iterations"] == -1
        assert loaded.members[0]["member"] == FS_MEMBER
        assert loaded.surface == {"mode": "auto"}
        assert loaded.mcp_refs == ["search"]
        assert loaded.incarnation_id == "inc-1"
    finally:
        await store.close()


async def test_snapshot_update_keeps_created_at(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        first = await store.save(agent_id="a" * 32, name="weekly", config={"v": 1})
        second = await store.save(agent_id="a" * 32, name="weekly", config={"v": 2})
        assert second.created_at == first.created_at
        assert (await store.get("a" * 32)).config == {"v": 2}
    finally:
        await store.close()


async def test_snapshot_delete_is_idempotent(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        await store.save(agent_id="a" * 32, name="weekly", config={})
        assert await store.delete("a" * 32) is True
        assert await store.delete("a" * 32) is False
        assert await store.get("a" * 32) is None
    finally:
        await store.close()


async def test_snapshot_rejects_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "db" / "agents.sqlite3"
    store = await _store(tmp_path)
    await store.close()
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE agent_schema_versions SET version=99")
        await db.commit()
    reopened = AgentInstanceStore(path)
    with pytest.raises(AgentInstanceStoreError):
        await reopened.start()


def test_resolve_assembly_maps_config() -> None:
    resolved = resolve_assembly(_spec(), _source(), workspace_root="/tmp/ws")

    assert resolved.config.name == "weekly"
    assert resolved.config.endpoint_name == "default-llm"
    assert resolved.config.system_prompt == "整理进展"
    assert resolved.config.max_iterations == -1
    assert resolved.config.communication_timeout == 120
    assert resolved.config.model_overrides is not None
    assert resolved.config.workspace_root == "/tmp/ws"
    assert resolved.config.context is not None
    assert resolved.config.context.persistence_type == "json_file"
    assert resolved.config.context.persistence_root_dir == "/tmp/ctx"
    assert resolved.surface is not None
    assert resolved.surface.mode == "static"
    assert [item.ability_name for item in resolved.abilities] == [FS_MEMBER]


def test_member_without_request_keeps_package_declaration() -> None:
    resolved = resolve_assembly(_spec(), _source())

    ability = resolved.abilities[0]
    assert ability.requested_permissions is None
    assert ability.permissions == ability.declared_permissions


def test_member_request_marks_only_provided_dimensions() -> None:
    """未申请的维度必须缺席，而不是被填成 False。"""
    spec = _spec([{"member": FS_MEMBER, "permissions": {"fs_write": True}}])
    ability = resolve_assembly(spec, _source()).abilities[0]

    assert ability.requested_permissions is not None
    assert ability.requested_permissions.model_fields_set == {"fs_write"}
    assert ability.permissions.fs_write is True
    assert ability.requested_permissions.allowed_paths == []


def test_unknown_member_is_rejected() -> None:
    spec = _spec([{"member": "ghrah.fs.not_a_member"}])
    with pytest.raises(AssemblyError, match="member not resolvable"):
        resolve_assembly(spec, _source())


def test_recovery_spec_must_not_be_resolved() -> None:
    spec = AgentAssemblySpec.model_validate({"name": "weekly", "agent_id": "a" * 32})
    with pytest.raises(AssemblyError, match="recovery mode"):
        resolve_assembly(spec, _source())


class _ControlSource:
    """伪来源：只声明一个控制成员，验证规格越界会被拒绝。"""

    def load_ability(self, member: str) -> Any:
        del member
        implementation = type("Impl", (), {"handler": "conversation"})()
        metadata = type("Meta", (), {"permissions": PermissionFlags()})()
        return type(
            "Manifest",
            (),
            {
                "full_name": "ghrah.core.conversation",
                "tool": None,
                "implementation": implementation,
                "metadata": metadata,
                "hooks": type("Hooks", (), {})(),
            },
        )()


def test_control_member_is_rejected() -> None:
    spec = _spec([{"member": "ghrah.core.conversation"}])
    with pytest.raises(AssemblyError, match="control member"):
        resolve_assembly(spec, _ControlSource())  # type: ignore[arg-type]
