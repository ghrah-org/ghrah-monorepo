# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""持久化读取投影与轻量查询测试。

覆盖：
- SQLite 的 ``snapshot_payloads`` 三档投影（all / boundaries / none）；
- ``has_checkpoint`` / ``load_chain_meta`` / ``list_node_ids`` 轻量查询；
- 恢复时只加载边界载荷仍能重建出逐条一致的消息；
- 显式 ``persist()`` 只写尚未落库的节点。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from ghrah.chat.factory import ChatMessageFactory
from ghrah.context import ContextManager, ContextNode
from ghrah.context.persistence import ContextChanges
from ghrah.context.persistence.sqlite_backend import SqliteBackend


def _branch_with_head(cm: ContextManager, head_node_id: str) -> Any:
    """复制当前 active branch 并把 head 指向新节点。"""
    branch = cm.get_active_session_branch()
    return dataclasses.replace(branch, head_node_id=head_node_id)


async def _seed_legacy_periodic_snapshot(
    backend: SqliteBackend, cm: ContextManager
) -> tuple[str, str]:
    """在已提交一轮的链上手工补一个「老式周期性快照」节点。

    返回 (周期快照节点 id, 其 messages_delta 的消息文本)。
    """
    head = cm.active_head
    delta_message = ChatMessageFactory().create_message(role="user", text="round-2")
    snapshot_messages = [
        *cm.message_store.current_messages,
        delta_message,
    ]
    legacy = ContextNode(
        parent_id=head.id,
        agent_name=cm.agent_name,
        session_id=cm.active_session_id,
        created_on_branch_id=cm.get_active_session().active_branch_id,
        iteration=head.iteration + 1,
        ability_names=["legacy-periodic"],
        messages_delta=[delta_message],
        messages_snapshot=snapshot_messages,
        is_snapshot=True,
    )
    branch = _branch_with_head(cm, legacy.id)
    await backend.apply_changes(
        ContextChanges(
            agent_name=cm.agent_name,
            branches=(branch,),
            nodes=(legacy,),
        )
    )
    return legacy.id, "round-2"


@pytest.mark.asyncio
async def test_snapshot_payload_projections(tmp_path: Path) -> None:
    """all / boundaries / none 三档投影只影响快照载荷。"""
    backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-a")
    await backend.connect()
    factory = ChatMessageFactory()
    cm = ContextManager(
        agent_name="agent",
        persistence=backend,
        auto_persist=True,
        message_factory=factory,
    )
    cm.begin_iteration()
    cm.add_messages([factory.create_message(role="user", text="round-1")])
    cm.commit_iteration(ability_names=["step-1"])
    await cm.wait_for_persist()
    legacy_id, _ = await _seed_legacy_periodic_snapshot(backend, cm)
    root_id = [node.id for node in cm.get_history() if node.parent_id is None][0]

    full = await backend.load_checkpoint("agent")
    assert full is not None
    full_by_id = {node.id: node for node in full.nodes}
    assert full_by_id[legacy_id].messages_snapshot is not None
    assert full_by_id[root_id].messages_snapshot is not None

    boundaries = await backend.load_checkpoint("agent", snapshot_payloads="boundaries")
    assert boundaries is not None
    boundary_by_id = {node.id: node for node in boundaries.nodes}
    # 老式周期快照不是语义边界：载荷被剥离，但 delta 保留
    assert boundary_by_id[legacy_id].messages_snapshot is None
    assert boundary_by_id[legacy_id].messages_delta
    # root 是语义边界：载荷保留
    assert boundary_by_id[root_id].messages_snapshot is not None

    none = await backend.load_checkpoint("agent", snapshot_payloads="none")
    assert none is not None
    assert all(node.messages_snapshot is None for node in none.nodes)
    await backend.close()


@pytest.mark.asyncio
async def test_lightweight_queries(tmp_path: Path) -> None:
    """has_checkpoint / load_chain_meta / list_node_ids 不加载节点载荷。"""
    backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-a")
    await backend.connect()
    cm = ContextManager(
        agent_name="agent",
        persistence=backend,
        auto_persist=True,
        message_factory=ChatMessageFactory(),
    )
    cm.begin_iteration()
    cm.commit_iteration(ability_names=["step-1"])
    await cm.wait_for_persist()
    assert await backend.has_checkpoint("agent") is True
    assert await backend.has_checkpoint("missing") is False

    meta = await backend.load_chain_meta("agent")
    assert meta is not None
    assert meta.agent_name == "agent"
    assert {session.session_id for session in meta.sessions} == {cm.active_session_id}
    assert {branch.branch_id for branch in meta.branches} == {
        branch.branch_id for branch in cm.list_branches(cm.active_session_id)
    }

    node_ids = await backend.list_node_ids("agent")
    assert {node.id for node in cm.get_history()} == node_ids
    await backend.close()


@pytest.mark.asyncio
async def test_restore_from_boundary_projection_matches_full(tmp_path: Path) -> None:
    """只加载边界载荷的恢复结果与 all 投影逐条一致。"""
    backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-a")
    await backend.connect()
    factory = ChatMessageFactory()
    cm = ContextManager(
        agent_name="agent",
        persistence=backend,
        auto_persist=True,
        message_factory=factory,
    )
    cm.begin_iteration()
    cm.add_messages([factory.create_message(role="user", text="round-1")])
    cm.commit_iteration(ability_names=["step-1"])
    await cm.wait_for_persist()
    await _seed_legacy_periodic_snapshot(backend, cm)
    await backend.close()

    restored_backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-b")
    await restored_backend.connect()
    restored = ContextManager(
        agent_name="agent",
        persistence=restored_backend,
        message_factory=ChatMessageFactory(),
    )
    await restored.restore("agent")
    texts = [message.text for message in restored.message_store.current_messages]
    assert texts == ["round-1", "round-2"]
    await restored_backend.close()


@pytest.mark.asyncio
async def test_persist_only_writes_missing_nodes(tmp_path: Path) -> None:
    """显式 persist 只提交尚未落库的节点。"""
    backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-a")
    await backend.connect()
    cm = ContextManager(
        agent_name="agent",
        persistence=backend,
        auto_persist=False,
        message_factory=ChatMessageFactory(),
    )
    written: list[tuple[str, ...]] = []
    original_apply = backend.apply_changes

    async def _recording_apply(changes: ContextChanges) -> None:
        written.append(tuple(node.id for node in changes.nodes))
        await original_apply(changes)

    backend.apply_changes = _recording_apply  # type: ignore[method-assign]

    cm.begin_iteration()
    cm.commit_iteration(ability_names=["step-1"])
    await cm.persist()
    first = written[-1]
    assert len(first) == 2  # root + step-1

    await cm.persist()
    assert written[-1] == ()  # 已全部落库，不重复序列化
    await backend.close()
