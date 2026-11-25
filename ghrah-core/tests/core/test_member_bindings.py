# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""成员绑定：快照持久化 + 工具面按绑定集合装配 + 模型侧绑定/解绑。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ghrah.core.agents import AgentInstanceStore
from ghrah.core.agents.bindings import MemberBindingState


async def _store(tmp_path: Path) -> AgentInstanceStore:
    store = AgentInstanceStore(tmp_path / "agents.sqlite3")
    await store.start()
    return store


async def _seed(store: AgentInstanceStore, **overrides: Any) -> None:
    members = [
        {
            "member": "ghrah.fs.read_file",
            "type": "operation",
            "axes": {"visible": True, "available": True},
        },
        {
            "member": "ghrah.fs.write_file",
            "type": "operation",
            "axes": {"visible": True, "available": True},
        },
        {
            "member": "ghrah.fs.edit_file",
            "type": "operation",
            "axes": {"visible": True, "available": True},
        },
    ]
    await store.save(
        agent_id="a" * 32,
        name="binder",
        config={"name": "binder"},
        members=members,
        surface=None,
        mcp_refs=[],
        incarnation_id=None,
        **overrides,
    )


@pytest.mark.asyncio
async def test_binding_state_rewrites_only_the_bind_axis(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        await _seed(store)
        state = MemberBindingState(lambda: store)
        await state.save("a" * 32, {"ghrah.fs.read_file", "ghrah.fs.write_file"})

        record = await store.get("a" * 32)
        assert record is not None
        axes = {item["member"]: item["axes"] for item in record.members}
        assert axes["ghrah.fs.read_file"]["available"] is True
        assert axes["ghrah.fs.write_file"]["available"] is True
        assert axes["ghrah.fs.edit_file"]["available"] is False
        assert all(item["axes"]["visible"] is True for item in record.members)
        assert record.config == {"name": "binder"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_binding_state_rejects_unknown_instance(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="snapshot not found"):
            await MemberBindingState(lambda: store).save("b" * 32, set())
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_bindings_ignore_members_outside_the_snapshot(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        await _seed(store)
        await MemberBindingState(lambda: store).save(
            "a" * 32, {"ghrah.fs.read_file", "ghrah.ghost.missing"}
        )
        record = await store.get("a" * 32)
        assert record is not None
        assert [item["member"] for item in record.members] == [
            "ghrah.fs.read_file",
            "ghrah.fs.write_file",
            "ghrah.fs.edit_file",
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_binding_state_reads_snapshot_member_axes(tmp_path: Path) -> None:
    """读接缝返回定义态成员（含 visible/bind 两轴）；快照缺失返回 None。"""
    store = await _store(tmp_path)
    try:
        members = [
            {
                "namespace": "ghrah",
                "name": "fs",
                "member": "ghrah.fs.read_file",
                "type": "operation",
                "version": None,
                "ability_type": "kernel",
                "axes": {"visible": True, "bind": True},
                "data": {},
            },
            {
                "namespace": "ghrah",
                "name": "fs",
                "member": "ghrah.fs.write_file",
                "type": "operation",
                "version": None,
                "ability_type": "kernel",
                "axes": {"visible": True, "bind": False},
                "data": {},
            },
            {
                "namespace": "ghrah",
                "name": "core",
                "member": "ghrah.core.search_tools",
                "type": "operation",
                "version": None,
                "ability_type": "kernel",
                "axes": {"visible": False, "bind": True},
                "data": {},
            },
        ]
        await store.save(agent_id="a" * 32, name="binder", config={}, members=members)
        state = MemberBindingState(lambda: store)
        read = await state.members("a" * 32)
        assert read is not None
        assert [(item.member, item.axes.visible, item.axes.bind) for item in read] == [
            ("ghrah.fs.read_file", True, True),
            ("ghrah.fs.write_file", True, False),
            ("ghrah.core.search_tools", False, True),
        ]
        assert await state.members("b" * 32) is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_binding_state_fails_closed_on_malformed_snapshot(tmp_path: Path) -> None:
    """快照损坏时不补默认轴，直接报错交给调用方给原因。"""
    from ghrah.ability.member import MemberError

    store = await _store(tmp_path)
    try:
        await store.save(
            agent_id="a" * 32,
            name="binder",
            config={},
            members=[{"member": "ghrah.fs.read_file", "type": "operation"}],
        )
        with pytest.raises(MemberError):
            await MemberBindingState(lambda: store).members("a" * 32)
    finally:
        await store.close()
