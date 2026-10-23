# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""RoomFilterUnit 单测（显式 Filter 契约）。

- 白名单 + room 上下文 + success → room_send 落账；
- 无 room 上下文不落；send 跳过防双记；
- 非白名单/失败结果不落；同节点事件重放去重。
"""

from __future__ import annotations

from typing import Any

from ghrah.subject.config import RoomFilterConfig, SubjectConfig
from ghrah.subject.units.room_filter import RoomFilterUnit


class _FakeRoomManager:
    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sends.append(payload)
        return {"success": True, "data": {"entry": {"id": "e1"}}, "error": None}


class _FakeCtx:
    def __init__(self, manager: _FakeRoomManager) -> None:
        self._manager = manager

    def get(self, name: str, strict: bool = True) -> Any:
        return self._manager

    def on(self, event: str, handler: Any) -> None:
        pass


def _config(abilities: tuple[str, ...] = ("conversation",)) -> SubjectConfig:
    return SubjectConfig(
        workspace_root="/tmp/ws",
        db_path="/tmp/subject.db",
        room_filter_slice=RoomFilterConfig(enabled=True, abilities=abilities),
    )


def _node(
    node_id: str = "n1",
    agent: str = "planner",
    action_results: list[dict[str, Any]] | None = None,
    room_id: str | None = "room-1",
) -> dict[str, Any]:
    delta = []
    if room_id is not None:
        delta.append({"role": "user", "metadata": {"room_id": room_id}})
    return {
        "id": node_id,
        "agent_name": agent,
        "action_results": _conv_result() if action_results is None else action_results,
        "messages_delta": delta,
    }


def _conv_result(response: str = "收到") -> list[dict[str, Any]]:
    return [
        {
            "ability_name": "conversation",
            "action_result": {"outcome": "success", "data": {"response": response}},
        }
    ]


async def _unit(
    abilities: tuple[str, ...] = ("conversation",),
) -> tuple[RoomFilterUnit, _FakeRoomManager]:
    manager = _FakeRoomManager()
    unit = RoomFilterUnit(_config(abilities))
    await unit.init(_FakeCtx(manager))
    return unit, manager


async def test_whitelist_hit_with_room_context_appends() -> None:
    unit, manager = await _unit()
    await unit._on_chain_updated({"agent_name": "planner", "node": _node()})
    assert len(manager.sends) == 1
    send = manager.sends[0]
    assert send["room_id"] == "room-1"
    assert send["author"] == "planner"
    assert send["author_type"] == "agent"
    assert send["data"]["message"] == "收到"
    assert send["data"]["via"] == "chain_filter"


async def test_default_whitelist_covers_conversation_and_end_task() -> None:
    """默认白名单 = conversation + end_task：任务终止答复同样落 Room。"""
    manager = _FakeRoomManager()
    unit = RoomFilterUnit(SubjectConfig(workspace_root="/tmp/ws", db_path="/tmp/subject.db"))
    await unit.init(_FakeCtx(manager))
    assert RoomFilterConfig().abilities == ("conversation", "end_task")

    node = _node(
        action_results=[
            {
                "ability_name": "end_task",
                "action_result": {"outcome": "success", "data": {"response": "全部完成"}},
            }
        ]
    )
    await unit._on_chain_updated({"agent_name": "planner", "node": node})

    assert len(manager.sends) == 1
    assert manager.sends[0]["data"]["ability"] == "end_task"
    assert manager.sends[0]["data"]["message"] == "全部完成"


async def test_no_room_context_not_appended() -> None:
    """无 room 上下文（metadata.room_id 缺失）→ 不落。"""
    unit, manager = await _unit()
    await unit._on_chain_updated({"agent_name": "planner", "node": _node(room_id=None)})
    assert manager.sends == []


async def test_delivery_context_from_later_tool_iteration_appends() -> None:
    """多轮 tool call 的最终节点从 durable delivery_context 恢复 Room 归属。"""
    unit, manager = await _unit()
    node = _node(room_id=None)
    node["metadata"] = {
        "delivery_context": {
            "room_id": "room-tool-loop",
            "project_id": "project-1",
            "agent_id": "stable-1",
        }
    }

    await unit._on_chain_updated({"agent_name": "shuoxi", "node": node})

    assert len(manager.sends) == 1
    assert manager.sends[0]["room_id"] == "room-tool-loop"
    assert manager.sends[0]["author"] == "stable-1"
    assert manager.sends[0]["data"]["message"] == "收到"


async def test_send_ability_skipped() -> None:
    """send 自带 RoomLog 落账 → 跳过防双记。"""
    unit, manager = await _unit()
    node = _node(
        action_results=[
            {
                "ability_name": "send",
                "action_result": {"outcome": "success", "data": {"status": "sent"}},
            }
        ]
    )
    await unit._on_chain_updated({"agent_name": "planner", "node": node})
    assert manager.sends == []


async def test_non_whitelist_and_failure_skipped() -> None:
    unit, manager = await _unit()
    node = _node(
        action_results=[
            # 非白名单
            {
                "ability_name": "execute_command",
                "action_result": {"outcome": "success", "data": {"output": "x"}},
            },
            # 白名单但失败
            {
                "ability_name": "conversation",
                "action_result": {"outcome": "failure", "data": {"error": "e"}},
            },
        ]
    )
    await unit._on_chain_updated({"agent_name": "planner", "node": node})
    assert manager.sends == []


async def test_duplicate_node_event_deduped() -> None:
    unit, manager = await _unit()
    payload = {"agent_name": "planner", "node": _node()}
    await unit._on_chain_updated(payload)
    await unit._on_chain_updated(payload)
    assert len(manager.sends) == 1


async def test_custom_whitelist_config() -> None:
    unit, manager = await _unit(abilities=("echo",))
    node = _node(
        action_results=[
            {
                "ability_name": "echo",
                "action_result": {"outcome": "success", "data": {"content": "hi"}},
            }
        ]
    )
    await unit._on_chain_updated({"agent_name": "planner", "node": node})
    assert len(manager.sends) == 1
    assert manager.sends[0]["data"]["message"] == "hi"


async def test_malformed_payload_ignored() -> None:
    """非 dict payload / 缺 node / 缺 id / 空 action_results 均安全忽略。"""
    unit, manager = await _unit()
    await unit._on_chain_updated(None)
    await unit._on_chain_updated({})
    await unit._on_chain_updated({"agent_name": "a", "node": {"no_id": 1}})
    await unit._on_chain_updated({"agent_name": "a", "node": _node(action_results=[])})
    assert manager.sends == []
