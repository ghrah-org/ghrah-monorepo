# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""通过真实 Subject、CoreUnit 与 MessageManager 验证 Room 命令和日志。"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ability_testutil import EchoLLM, put_agent_template
from ghrah.ability.access import GrantPolicy
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import RecoveryConfig, SubjectConfig
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        ability_grant_policy=GrantPolicy(mode="merge"),
        recovery_slice=RecoveryConfig(
            enabled=True,
            reconcile_on_start=False,
            bootstrap_default_project=False,
        ),
    )


@asynccontextmanager
async def _stack(tmp_path: Path) -> AsyncIterator[Context]:
    config = _config(tmp_path)
    async with Context() as ctx:
        await assemble_subject(
            ctx,
            config,
            profile="full",
            core_cluster_unit=CoreClusterRegistryUnit(config, llm_factory=lambda _: EchoLLM()),
        )
        put_agent_template(ctx.get("manifest_store"))
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


async def _spawn_agent(ctx: Context, project_id: str, name: str) -> str:
    """通过生产模板和授权库启动 Agent。"""
    result = await bridge_command(
        ctx,
        "spawn_agent",
        {
            "project_id": project_id,
            "config": {"name": name},
            "manifest_ref": "tests.agent",
        },
    )
    assert result["success"] and not result["data"]["runtime_pending"], result
    return str(result["data"]["agent_id"])


class TestRoomRealEndToEnd:
    async def test_room_admin_full_flow_real_backend(self, tmp_path: Path) -> None:
        """真实后端：project → 4 room → join → Agent 日志 → log seq 单调 → 事件。"""
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
            architect_id = await _spawn_agent(ctx, project_id, "architect")
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
                            "author": architect_id,
                            "author_type": "agent",
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

    async def test_agent_room_send_appends_room_log(self, tmp_path: Path) -> None:
        """Agent 经 Room 命令发布日志，使用稳定身份归属。"""
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
            architect_id = await _spawn_agent(ctx, project_id, "architect")
            await bridge_command(
                ctx,
                "room_join",
                {
                    "room_id": room["room_id"],
                    "subject": "architect",
                    "subject_type": "agent",
                },
            )

            _data(
                await bridge_command(
                    ctx,
                    "room_send",
                    {
                        "room_id": room["room_id"],
                        "author": architect_id,
                        "author_type": "agent",
                        "data": {"message": "agent published"},
                    },
                )
            )

            # RoomLog 真实落账（author=agent author_type=agent）
            log = _data(await bridge_command(ctx, "room_get_log", {"room_id": room["room_id"]}))
            assert log["count"] == 1
            entry = log["entries"][0]
            # author 为 project-scoped 稳定 agent_id（成员按稳定 ID 保存）
            assert entry["author"] == architect_id
            assert entry["author_type"] == "agent"
            assert entry["data"]["message"] == "agent published"
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
        """启动后直查持久身份，并验证 Room 用户消息送达 Agent。"""
        async with _stack(tmp_path) as ctx:
            project_id = await _make_project(ctx)
            room = _data(
                await bridge_command(ctx, "room_create", {"project_id": project_id, "name": "test"})
            )["room"]

            agent_id = await _spawn_agent(ctx, project_id, "planner")

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
                assert agent_rows == [agent_id]
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
                    "data": {"message": "你好", "targets": [agent_id]},
                },
            )
            assert send["success"], send.get("error")

            # 投递为 fire-and-forget，轮询 agent 入史
            # （supervisor 由 CoreUnit 实例自持，经 registry handle 取证）
            registry = ctx.get("core_cluster_registry")
            supervisor = registry.get_handle(project["cluster_ids"][0])._unit.supervisor
            actor = supervisor._registry.get_info("planner").actor_handle

            manager = registry.get_message_manager(project_id)
            await manager.dispatch_pending()
            received = actor._message_history[0]
            assert received.content == "你好"
            assert received.sender == "human:yuki"

            # 贯通断言：投递 metadata（room_id）随 AgentMessage 到达 agent
            # （receive 侧合入 ChatMessage metadata → 链节点 messages_delta）
            assert received.metadata.get("room_id") == room["room_id"]

            # Agent 的发布日志入口不产生投递。
            history_before = list(actor._message_history)
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
            await manager.dispatch_pending()
            assert actor._message_history == history_before

    async def test_human_room_send_image_blocks_reach_agent(self, tmp_path: Path) -> None:
        """房间发图端到端：room_send 带块 → MM 落库/投递 → Agent 收到 image block。"""
        async with _stack(tmp_path) as ctx:
            project_id = await _make_project(ctx)
            room = _data(
                await bridge_command(
                    ctx, "room_create", {"project_id": project_id, "name": "image"}
                )
            )["room"]
            agent_id = await _spawn_agent(ctx, project_id, "planner")
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

            blocks = [{"type": "image", "base64": "aGVsbG8=", "mime_type": "image/png"}]
            send = await bridge_command(
                ctx,
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"content_blocks": blocks, "targets": [agent_id]},
                },
            )
            assert send["success"], send.get("error")
            # 纯块消息：日志条目不带兜底 JSON 正文
            entry = send["data"]["entry"]
            assert entry["data"]["content_blocks"] == blocks
            assert "message" not in entry["data"]

            project = _data(await bridge_command(ctx, "project_get", {"project_id": project_id}))[
                "project"
            ]
            registry = ctx.get("core_cluster_registry")
            supervisor = registry.get_handle(project["cluster_ids"][0])._unit.supervisor
            actor = supervisor._registry.get_info("planner").actor_handle
            manager = registry.get_message_manager(project_id)
            await manager.dispatch_pending()

            received = actor._message_history[0]
            assert received.content == ""
            assert received.content_blocks == blocks
            assert received.metadata.get("room_id") == room["room_id"]

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
                    "config": {"name": "planner"},
                    "manifest_ref": "tests.agent",
                },
            )
            assert spawned["success"] and not spawned["data"]["runtime_pending"], spawned
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
