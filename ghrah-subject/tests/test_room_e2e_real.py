# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Room 真实端到端（任务 6）：进程内装配态 Subject + 真实 CoreUnit +
RoomUnit（非 mock），覆盖：

- 真实后端 CRUD/成员/seq/事件（经 bridge_command Observer 命令路径）；
- send ability 真实回路（agent execute_ability → CoreUnit → SerialRoomBridge
  → ctx.serial command/room_get|room_send → RoomUnit append_log）；
- RoomLog 持久化跨重启（同 db_path 重装读回）；
- human 消息投递回路（room_send(human) → RoomUnit deliver → ctx.serial
  command/send_message → CoreUnit → Supervisor.send → agent receive）+
  spawn 即持久化取证（ghrah.db 建表 + agents 行）。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import RecoveryConfig, SubjectConfig
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import bridge_command


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        recovery_slice=RecoveryConfig(
            enabled=True,
            reconcile_on_start=False,
            bootstrap_default_project=False,
        ),
    )


@asynccontextmanager
async def _stack(tmp_path: Path) -> AsyncIterator[Context]:
    async with Context() as ctx:
        await assemble_subject(ctx, _config(tmp_path), profile="full")
        yield ctx


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result["success"], result
    return result["data"]


async def _wait_for(
    predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.05
) -> None:
    """轮询直到 predicate 为真或超时（fire-and-forget 投递的确定性等待）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


async def _make_project(ctx: Context, name: str = "demo") -> str:
    return _data(await bridge_command(ctx, "project_create", {"name": name}))["project"][
        "project_id"
    ]


async def _spawn_agent_with_send(ctx: Context, project_id: str, name: str) -> str:
    """spawn 一个带 ``send`` ability 的 agent（CoreUnit 经 AbilityRegistry 物化）。"""
    result = await bridge_command(
        ctx,
        "spawn_agent",
        {
            "project_id": project_id,
            "config": {"name": name, "system_prompt": "test", "max_iterations": 1},
            "abilities": [{"ability_type": "send", "params": {}}],
        },
    )
    assert result["success"], result.get("error")
    return str(result["data"]["agent_id"])


class TestRoomRealEndToEnd:
    async def test_room_admin_full_flow_real_backend(self, tmp_path: Path) -> None:
        """真实后端：project → 4 room → join → human send → log seq 单调 → 事件。"""
        async with _stack(tmp_path) as ctx:
            events: list[tuple[str, dict[str, Any]]] = []
            ctx.on(
                "event/room_log_appended",
                lambda payload: events.append(("room_log_appended", payload)),
            )

            project_id = await _make_project(ctx)
            room_ids: list[str] = []
            for name in ("architecture", "frontend", "backend", "testing"):
                room = _data(
                    await bridge_command(
                        ctx, "room_create", {"project_id": project_id, "name": name}
                    )
                )["room"]
                room_ids.append(room["room_id"])

            arch = room_ids[0]
            await _spawn_agent_with_send(ctx, project_id, "architect")
            join = await bridge_command(
                ctx,
                "room_join",
                {"room_id": arch, "subject": "architect", "subject_type": "agent"},
            )
            assert join["success"], join.get("error")

            seqs = []
            for i in range(3):
                entry = _data(
                    await bridge_command(
                        ctx,
                        "room_send",
                        {
                            "room_id": arch,
                            "author": "human:yuki",
                            "author_type": "human",
                            "data": {"message": f"hello {i}"},
                        },
                    )
                )["entry"]
                seqs.append(entry["seq"])
            assert seqs == [1, 2, 3]
            assert [e[0] for e in events] == ["room_log_appended"] * 3
            assert events[-1][1]["entry"]["data"]["message"] == "hello 2"

            # room_get_log 读回 + room_list 过滤
            log = _data(await bridge_command(ctx, "room_get_log", {"room_id": arch}))
            assert log["count"] == 3
            listed = _data(await bridge_command(ctx, "room_list", {"project_id": project_id}))
            assert listed["count"] == 4

    async def test_send_ability_real_path_appends_room_log(self, tmp_path: Path) -> None:
        """send 工具经 execute_ability → SerialRoomBridge → RoomUnit 落账（非 mock）。

        收件人 = 仅发送者自身 → 投递排除自身（无 supervisor.send 调用，不依赖
        LLM），但 RoomLog 落账全链路真实执行。
        """
        async with _stack(tmp_path) as ctx:
            events: list[tuple[str, dict[str, Any]]] = []
            ctx.on(
                "event/room_log_appended",
                lambda payload: events.append(("room_log_appended", payload)),
            )

            project_id = await _make_project(ctx)
            room = _data(
                await bridge_command(ctx, "room_create", {"project_id": project_id, "name": "arch"})
            )["room"]
            architect_id = await _spawn_agent_with_send(ctx, project_id, "architect")
            await bridge_command(
                ctx,
                "room_join",
                {
                    "room_id": room["room_id"],
                    "subject": "architect",
                    "subject_type": "agent",
                },
            )

            exec_res = await bridge_command(
                ctx,
                "execute_ability",
                {
                    "request_id": "e2e-send-1",
                    "agent_name": "architect",
                    "ability_name": "send",
                    "tool_args": {
                        "room_ids": [room["room_id"]],
                        "message": "via send ability (real)",
                    },
                },
            )
            assert exec_res["success"], exec_res.get("error")
            payload = exec_res["data"]
            # AbilityResultPayload：success = ability outcome；result = ActionResult.data
            assert payload["success"] is True, payload
            ability_data = payload["result"]
            assert ability_data["status"] == "sent"
            assert ability_data["rooms"] == [room["room_id"]]

            # RoomLog 真实落账（author=agent author_type=agent）
            log = _data(await bridge_command(ctx, "room_get_log", {"room_id": room["room_id"]}))
            assert log["count"] == 1
            entry = log["entries"][0]
            # author 为 project-scoped 稳定 agent_id（成员按稳定 ID 保存）
            assert entry["author"] == architect_id
            assert entry["author_type"] == "agent"
            assert entry["data"]["message"] == "via send ability (real)"
            assert events and events[0][1]["entry"]["id"] == entry["id"]

    async def test_room_log_persistence_across_restart(self, tmp_path: Path) -> None:
        """RoomLog 持久化：同 db_path 重装 assemble_subject 后 get_log 读回。"""
        room_id: str

        async with _stack(tmp_path) as ctx:
            project_id = await _make_project(ctx)
            room = _data(
                await bridge_command(ctx, "room_create", {"project_id": project_id, "name": "r"})
            )["room"]
            room_id = room["room_id"]
            await bridge_command(
                ctx,
                "room_send",
                {
                    "room_id": room_id,
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"message": "persisted"},
                },
            )

        async with _stack(tmp_path) as ctx:
            listed = _data(await bridge_command(ctx, "room_list", {}))
            assert any(r["room_id"] == room_id for r in listed["rooms"])
            log = _data(await bridge_command(ctx, "room_get_log", {"room_id": room_id}))
            assert log["count"] == 1
            assert log["entries"][0]["data"]["message"] == "persisted"
            assert log["entries"][0]["seq"] == 1

    async def test_human_send_delivery_and_spawn_persistence(self, tmp_path: Path) -> None:
        """投递回路冒烟。

        - spawn 即持久化——ghrah.db 建表 + agents 行落库（WAL 只读连接
          直查真相源）；
        - room_send(human) 落账后 fire-and-forget 投递 → agent receive()
          入史（无 LLM 环境下迭代可能失败，但消息入史发生在 LLM 初始化前，
          为确定性投递证据；回复质量不在断言范围）。
        """
        async with _stack(tmp_path) as ctx:
            project_id = await _make_project(ctx)
            room = _data(
                await bridge_command(ctx, "room_create", {"project_id": project_id, "name": "test"})
            )["room"]

            spawn = await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": project_id,
                    "config": {
                        "name": "planner",
                        "system_prompt": "test",
                        "max_iterations": 1,
                    },
                },
            )
            assert spawn["success"], spawn.get("error")

            # Core sqlite 真相源已按 Project Root 隔离。
            project = _data(await bridge_command(ctx, "project_get", {"project_id": project_id}))[
                "project"
            ]
            core_db_path = ProjectPaths.from_locator(
                project["project_root_locator"]
            ).action_chain_db_path
            conn = sqlite3.connect(f"file:{core_db_path}?mode=ro", uri=True)
            try:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                assert {"runs", "agents", "nodes", "chain_meta"} <= tables
                agent_rows = [row[0] for row in conn.execute("SELECT agent_name FROM agents")]
                assert agent_rows == [spawn["data"]["agent_id"]]
            finally:
                conn.close()

            join = await bridge_command(
                ctx,
                "room_join",
                {
                    "room_id": room["room_id"],
                    "subject": "planner",
                    "subject_type": "agent",
                },
            )
            assert join["success"], join.get("error")

            send = await bridge_command(
                ctx,
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"message": "你好", "targets": ["planner"]},
                },
            )
            assert send["success"], send.get("error")

            # 投递为 fire-and-forget，轮询 agent 入史
            # （supervisor 由 CoreUnit 实例自持，经 registry handle 取证）
            registry = ctx.get("core_cluster_registry")
            supervisor = registry.get_handle(project["cluster_ids"][0])._unit.supervisor
            actor = supervisor._registry.get_info("planner").actor_handle

            def delivered() -> bool:
                return len(actor._message_history) > 0

            await _wait_for(delivered, timeout=5.0)
            received = actor._message_history[0]
            assert received.content == "你好"
            assert received.sender == "human:yuki"

            # 贯通断言：投递 metadata（room_id）随 AgentMessage 到达 agent
            # （receive 侧合入 ChatMessage metadata → 链节点 messages_delta）
            assert received.metadata.get("room_id") == room["room_id"]

            # author_type=agent 不投递（send ability 路径自担 Supervisor 投递）
            agent_send = await bridge_command(
                ctx,
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "planner",
                    "author_type": "agent",
                    "data": {"message": "ack"},
                },
            )
            assert agent_send["success"], agent_send.get("error")
            await asyncio.sleep(0.3)
            assert len(actor._message_history) == 1

    async def test_room_filter_appends_conversation_reply(self, tmp_path: Path) -> None:
        """合成事件：白名单 conversation 结果 + room 上下文 → 落 RoomLog。

        真实 LLM 不参与：以真实序列化形状的合成 action_chain_updated 事件
        驱动（事件订阅/Filter/room_send 校验/落账/广播全链路真实执行）。
        """
        async with _stack(tmp_path) as ctx:
            events: list[tuple[str, dict[str, Any]]] = []
            ctx.on(
                "event/room_log_appended",
                lambda payload: events.append(("room_log_appended", payload)),
            )

            project_id = await _make_project(ctx)
            room = _data(
                await bridge_command(ctx, "room_create", {"project_id": project_id, "name": "test"})
            )["room"]
            spawned = await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": project_id,
                    "config": {"name": "planner", "system_prompt": "t"},
                },
            )
            await bridge_command(
                ctx,
                "room_join",
                {
                    "room_id": room["room_id"],
                    "subject": "planner",
                    "subject_type": "agent",
                },
            )

            ctx.emit(
                "core:action_chain_updated",
                {
                    "project_id": project_id,
                    "agent_id": spawned["data"]["agent_id"],
                    "cluster_id": spawned["data"]["cluster_id"],
                    "agent_name": "planner",
                    "node": {
                        "id": "node-e1",
                        "agent_name": "planner",
                        "metadata": {
                            "delivery_context": {
                                "agent_id": spawned["data"]["agent_id"],
                                "room_id": room["room_id"],
                            }
                        },
                        "action_results": [
                            {
                                "ability_name": "conversation",
                                "action_result": {
                                    "outcome": "success",
                                    "data": {"response": "收到，开始规划"},
                                },
                            }
                        ],
                        "messages_delta": [
                            {
                                "role": "user",
                                "metadata": {"room_id": room["room_id"]},
                            }
                        ],
                    },
                },
            )
            await _wait_for(lambda: bool(events), timeout=5.0)

            log = _data(await bridge_command(ctx, "room_get_log", {"room_id": room["room_id"]}))
            assert log["count"] == 1
            entry = log["entries"][0]
            assert entry["author"] == spawned["data"]["agent_id"]
            assert entry["author_type"] == "agent"
            assert entry["data"]["message"] == "收到，开始规划"
            assert entry["data"]["via"] == "message_manager"
            # WS 投影：room_log_appended(agent) 已广播（断言面）
            assert events[0][1]["entry"]["id"] == entry["id"]

            # 负例：无 room 上下文（metadata.room_id 缺失）→ 不落
            ctx.emit(
                "core:action_chain_updated",
                {
                    "agent_name": "planner",
                    "node": {
                        "id": "node-e2",
                        "agent_name": "planner",
                        "action_results": [
                            {
                                "ability_name": "conversation",
                                "action_result": {
                                    "outcome": "success",
                                    "data": {"response": "无归属回复"},
                                },
                            }
                        ],
                        "messages_delta": [
                            {"role": "user", "metadata": {}},
                        ],
                    },
                },
            )
            await asyncio.sleep(0.3)
            log = _data(await bridge_command(ctx, "room_get_log", {"room_id": room["room_id"]}))
            assert log["count"] == 1
