# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""CoreUnit 实际发射事件 dict 的 wire 契约锁定。

对齐源是 ``_core_event_to_dict`` 的实际输出（Observer 消费的就是它），
而非 protocol 包中已声明但可能未被使用(payload 类型)的形状——两者
字段漂移在此处快速失败。TS 侧 ``protocol-align.spec.ts`` 以同源快照
对齐 Zod schema。
"""

from __future__ import annotations

from ghrah.core.events import (
    ActionChainUpdatedEvent,
    BranchActivatedEvent,
    BranchCreatedEvent,
    SessionActivatedEvent,
    SessionCreatedEvent,
)
from ghrah.core.unit import _core_event_to_dict

_SESSION_INFO = {
    "session_id": "session-001",
    "agent_name": "agent-1",
    "name": "Session 1",
    "root_node_id": "node-root",
    "active_branch_id": "branch-main",
    "lifecycle": "open",
    "system_prompt": "",
    "origin_agent_name": None,
    "origin_session_id": None,
    "origin_node_id": None,
    "created_at": "2026-09-18T00:00:00+00:00",
    "metadata": {},
    "message_count": 0,
    "iteration_count": 0,
}

_BRANCH_INFO = {
    "branch_id": "branch-main",
    "session_id": "session-001",
    "name": "main",
    "head_node_id": "node-001",
    "lifecycle": "open",
    "parent_branch_id": None,
    "fork_point_node_id": None,
    "created_at": "2026-09-18T00:00:00+00:00",
    "metadata": {},
}


class TestCoreEventDictWireContract:
    """锁定 session/branch/chain 事件的实际发射 dict 键集。"""

    def test_session_created_event_dict_keys(self) -> None:
        event = SessionCreatedEvent(agent_name="agent-1", session=_SESSION_INFO)
        event_type, payload = _core_event_to_dict(event)
        assert event_type == "session_created"
        assert payload["agent_name"] == "agent-1"
        assert payload["event_type"] == "session_created"
        assert payload["session"] == _SESSION_INFO

    def test_session_activated_event_dict_keys(self) -> None:
        event = SessionActivatedEvent(agent_name="agent-1", session=_SESSION_INFO)
        event_type, payload = _core_event_to_dict(event)
        assert event_type == "session_activated"
        assert payload["session"] == _SESSION_INFO

    def test_branch_created_event_dict_carries_branch(self) -> None:
        event = BranchCreatedEvent(agent_name="agent-1", branch=_BRANCH_INFO)
        event_type, payload = _core_event_to_dict(event)
        assert event_type == "branch_created"
        assert payload["branch"] == _BRANCH_INFO

    def test_branch_activated_event_dict_carries_branch(self) -> None:
        event = BranchActivatedEvent(agent_name="agent-1", branch=_BRANCH_INFO)
        event_type, payload = _core_event_to_dict(event)
        assert event_type == "branch_activated"
        assert payload["branch"] == _BRANCH_INFO

    def test_action_chain_updated_event_dict_carries_branch_snapshot(self) -> None:
        node = {
            "id": "node-001",
            "session_id": "session-001",
            "created_on_branch_id": "branch-main",
        }
        event = ActionChainUpdatedEvent(agent_name="agent-1", node=node, branch=_BRANCH_INFO)
        event_type, payload = _core_event_to_dict(event)
        assert event_type == "action_chain_updated"
        assert payload["node"] == node
        assert payload["branch"] == _BRANCH_INFO
        assert payload["branch"]["head_node_id"] == "node-001"

    def test_event_type_map_covers_session_and_branch_domain(self) -> None:
        """_core_event_to_dict 必须认识全部 session/branch 事件类型。"""
        from ghrah.core.events import (
            BranchArchivedEvent,
            BranchDeletedEvent,
            SessionArchivedEvent,
            SessionDeletedEvent,
        )

        cases = [
            (
                SessionArchivedEvent(agent_name="a", session_id="s"),
                "session_archived",
            ),
            (
                SessionDeletedEvent(agent_name="a", session_id="s"),
                "session_deleted",
            ),
            (
                BranchArchivedEvent(agent_name="a", session_id="s", branch_id="b"),
                "branch_archived",
            ),
            (
                BranchDeletedEvent(agent_name="a", session_id="s", branch_id="b"),
                "branch_deleted",
            ),
        ]
        for event, expected in cases:
            event_type, _ = _core_event_to_dict(event)
            assert event_type == expected


def test_turn_and_hitl_events_are_accepted_by_wire_models() -> None:
    from ghrah.protocol.enums import EventType
    from ghrah.protocol.envelope import EVENT_PAYLOAD_MAP

    from ghrah.core.events import (
        HITLRequestEvent,
        HITLResolvedEvent,
        TurnClosedEvent,
        TurnOpenedEvent,
        TurnWaitingHitlEvent,
    )

    turn = {"agent_name": "display", "turn_id": "turn", "session_id": "session"}
    hitl = {
        "agent_name": "display",
        "promise_id": "promise",
        "ability_name": "tool",
        "turn_id": "turn",
    }
    cases = [
        (TurnOpenedEvent(**turn, opener_delivery_id="delivery"), {"status", "opener_delivery_id"}),
        (TurnWaitingHitlEvent(**turn, promise_id="promise"), {"promise_id"}),
        (
            TurnClosedEvent(**turn, status="closed", closing_kind="completed", end_node_id="end"),
            {"status", "closing_kind", "end_node_id"},
        ),
    ]
    for event, keys in cases:
        name, payload = _core_event_to_dict(event)
        assert set(payload) == set(turn) | keys | {"event_type"}
        assert EVENT_PAYLOAD_MAP[EventType(name)].model_validate(payload).turn_id == "turn"

    request = HITLRequestEvent(**hitl, delivery_id="delivery", deadline_at="2026-11-17T00:00:00Z")
    name, payload = _core_event_to_dict(request)
    model = EVENT_PAYLOAD_MAP[EventType(name)].model_validate(payload)
    assert (model.turn_id, model.delivery_id, model.deadline_at) == (
        request.turn_id,
        request.delivery_id,
        request.deadline_at,
    )
    name, payload = _core_event_to_dict(HITLResolvedEvent(**hitl, status="approved"))
    assert EVENT_PAYLOAD_MAP[EventType(name)].model_validate(payload).turn_id == "turn"
