# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ActionSession / ActionBranch 领域不变量测试。"""

from __future__ import annotations

import dataclasses

import pytest

from ghrah.context.action_session import ActionSession
from ghrah.context.branch import ActionBranch
from ghrah.context.node import ContextNode
from ghrah.context.persistence.serialization import (
    deserialize_action_session,
    deserialize_branch,
    serialize_action_session,
    serialize_branch,
)
from ghrah.context.topology import validate_session_topology


def test_root_session_and_branch_have_distinct_stable_ids() -> None:
    """Session 标识独立 Root，Branch 标识独立 Head 引用。"""
    session_id = ActionSession.new_id()
    branch_id = ActionBranch.new_id()
    branch = ActionBranch.create_root(
        session_id=session_id,
        branch_id=branch_id,
        head_node_id="root-node",
    )
    session = ActionSession.create(
        session_id=session_id,
        agent_name="planner",
        name="Session 1",
        root_node_id="root-node",
        active_branch_id=branch.branch_id,
    )

    assert session.session_id != branch.branch_id
    assert session.root_node_id == branch.head_node_id
    assert branch.is_root
    assert not session.is_derived


def test_fork_branch_records_same_session_provenance() -> None:
    """fork Branch 必须同时记录父 Branch 与 fork 节点。"""
    branch = ActionBranch.create_fork(
        session_id="session-1",
        name="retry-1",
        head_node_id="fork-node",
        parent_branch_id="branch-main",
        fork_point_node_id="node-12",
    )

    assert branch.session_id == "session-1"
    assert branch.parent_branch_id == "branch-main"
    assert branch.fork_point_node_id == "node-12"
    assert not branch.is_root


@pytest.mark.parametrize(
    ("parent_branch_id", "fork_point_node_id"),
    [("branch-main", None), (None, "node-12")],
)
def test_branch_rejects_partial_fork_provenance(
    parent_branch_id: str | None,
    fork_point_node_id: str | None,
) -> None:
    """Branch 的两项 fork 来源必须同时存在或同时为空。"""
    with pytest.raises(ValueError, match="must either both be set"):
        ActionBranch(
            branch_id="branch-1",
            session_id="session-1",
            name="retry",
            head_node_id="node-13",
            parent_branch_id=parent_branch_id,
            fork_point_node_id=fork_point_node_id,
        )


def test_session_rejects_partial_cross_session_origin() -> None:
    """派生 Session 必须完整记录来源 Agent、Session 与节点。"""
    with pytest.raises(ValueError, match="must either"):
        ActionSession(
            session_id="session-2",
            agent_name="planner",
            name="Session 2",
            root_node_id="root-2",
            active_branch_id="branch-2",
            origin_agent_name="planner",
            origin_session_id="session-1",
        )


def test_metadata_is_copied_at_domain_boundary() -> None:
    """调用方后续修改 metadata 不得污染领域对象。"""
    metadata = {"archived": False}
    session = ActionSession.create(
        agent_name="planner",
        name="Session 1",
        root_node_id="root",
        active_branch_id="main",
        metadata=metadata,
    )
    branch = ActionBranch.create_root(
        session_id=session.session_id,
        head_node_id="root",
        metadata=metadata,
    )

    metadata["archived"] = True

    assert session.metadata == {"archived": False}
    assert branch.metadata == {"archived": False}


def test_valid_session_topology_accepts_shared_ancestors() -> None:
    """同 Session 的 fork Branch 可以共享 Root 和祖先节点。"""
    session_id = "session-1"
    main_id = "branch-main"
    retry_id = "branch-retry"
    root = ContextNode.create_root(
        agent_name="planner",
        session_id=session_id,
        created_on_branch_id=main_id,
    )
    main_head = ContextNode(
        id="main-head",
        parent_id=root.id,
        agent_name="planner",
        session_id=session_id,
        created_on_branch_id=main_id,
    )
    retry_head = ContextNode(
        id="retry-head",
        parent_id=root.id,
        agent_name="planner",
        session_id=session_id,
        created_on_branch_id=retry_id,
    )
    main = ActionBranch.create_root(
        session_id=session_id,
        branch_id=main_id,
        head_node_id=main_head.id,
    )
    retry = ActionBranch.create_fork(
        session_id=session_id,
        branch_id=retry_id,
        name="retry-1",
        head_node_id=retry_head.id,
        parent_branch_id=main_id,
        fork_point_node_id=root.id,
    )
    session = ActionSession.create(
        session_id=session_id,
        agent_name="planner",
        name="Session 1",
        root_node_id=root.id,
        active_branch_id=retry_id,
    )

    validate_session_topology(session, [main, retry], [root, main_head, retry_head])


def test_session_topology_rejects_multiple_roots() -> None:
    """一个 Session 内出现第二个 Root 时必须失败。"""
    session_id = "session-1"
    branch_id = "branch-main"
    first_root = ContextNode.create_root(
        agent_name="planner",
        session_id=session_id,
        created_on_branch_id=branch_id,
    )
    second_root = ContextNode.create_root(
        agent_name="planner",
        session_id=session_id,
        created_on_branch_id=branch_id,
    )
    branch = ActionBranch.create_root(
        session_id=session_id,
        branch_id=branch_id,
        head_node_id=first_root.id,
    )
    session = ActionSession.create(
        session_id=session_id,
        agent_name="planner",
        name="Session 1",
        root_node_id=first_root.id,
        active_branch_id=branch_id,
    )

    with pytest.raises(ValueError, match="exactly one declared root"):
        validate_session_topology(session, [branch], [first_root, second_root])


def test_session_topology_rejects_cross_session_node() -> None:
    """Branch Head 不得指向其他 Session 的节点。"""
    branch = ActionBranch.create_root(
        session_id="session-1",
        branch_id="branch-main",
        head_node_id="root",
    )
    root = ContextNode(
        id="root",
        parent_id=None,
        agent_name="planner",
        session_id="session-2",
        created_on_branch_id=branch.branch_id,
    )
    session = ActionSession.create(
        session_id="session-1",
        agent_name="planner",
        name="Session 1",
        root_node_id=root.id,
        active_branch_id=branch.branch_id,
    )

    with pytest.raises(ValueError, match="belongs to another session"):
        validate_session_topology(session, [branch], [root])


def test_action_session_serialization_round_trip() -> None:
    """ActionSession 的 Root、active Branch 与来源信息必须完整往返。"""
    session = ActionSession.create(
        session_id="session-2",
        agent_name="planner",
        name="Session 2",
        root_node_id="root-2",
        active_branch_id="branch-2",
        system_prompt="system",
        origin_agent_name="planner",
        origin_session_id="session-1",
        origin_node_id="node-9",
        metadata={"label": "retry cleanly"},
    )

    restored = deserialize_action_session(serialize_action_session(session))

    assert restored == session


def test_action_branch_serialization_round_trip() -> None:
    """ActionBranch 的 Head 与 fork 来源必须完整往返。"""
    branch = ActionBranch.create_fork(
        branch_id="branch-2",
        session_id="session-1",
        name="retry-1",
        head_node_id="node-10",
        parent_branch_id="branch-1",
        fork_point_node_id="node-8",
        metadata={"reason": "failed test"},
    )

    restored = deserialize_branch(serialize_branch(branch))

    assert restored == branch


def test_session_rejects_invalid_lifecycle() -> None:
    """lifecycle 只允许 open|archived|deleted。"""
    with pytest.raises(ValueError, match="invalid lifecycle"):
        ActionSession(
            session_id="session-1",
            agent_name="planner",
            name="Session 1",
            root_node_id="root-1",
            active_branch_id="branch-1",
            lifecycle="bogus",
        )


def test_branch_rejects_invalid_lifecycle() -> None:
    """Branch lifecycle 与 Session 同构，只允许 open|archived|deleted。"""
    with pytest.raises(ValueError, match="invalid lifecycle"):
        ActionBranch(
            branch_id="branch-1",
            session_id="session-1",
            name="main",
            head_node_id="node-1",
            lifecycle="bogus",
        )


def test_lifecycle_transitions_follow_state_machine() -> None:
    """open→archived/deleted 合法；archived/deleted 为终态。"""
    session = ActionSession.create(
        agent_name="planner",
        name="Session 1",
        root_node_id="root",
        active_branch_id="main",
    )
    assert session.lifecycle == "open"

    archived = dataclasses.replace(session, lifecycle="archived")
    assert archived.lifecycle == "archived"
    deleted = dataclasses.replace(session, lifecycle="deleted")
    assert deleted.lifecycle == "deleted"
    # 终态语义由 ContextManager 守卫（activate/create_branch 拒绝），域层
    # 只锁定枚举合法值；从终态再次 dataclasses.replace 到非法值仍被拒绝。
    with pytest.raises(ValueError, match="invalid lifecycle"):
        dataclasses.replace(archived, lifecycle="bogus")
