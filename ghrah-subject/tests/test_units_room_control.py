# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""经 full profile 验证 REST 和旧 Room 历史入口使用同一离线批读。"""

from pathlib import Path

import httpx2
from ghrah.chat.message import ChatMessage
from ghrah.communication import MessageManager
from ghrah.context import ContextManager
from ghrah.context.persistence import ContextChanges
from ghrah.protocol.types import RoomSubjectType
from ouroboros import Context

from ghrah.subject.config import RecoveryConfig, SubjectConfig, TaskStoreConfig
from ghrah.subject.project.models import make_project_record
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.room.models import make_room_log_record
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    OBSERVER_ENDPOINT,
    PROJECT_STORE,
    ROOM_MANAGER,
)
from ghrah.subject.units import mount_builtin_units


def _config(tmp_path: Path, *, enabled: bool = True) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        observer_http_api_enabled=enabled,
        observer_http_api_token="test-token",
        recovery_slice=RecoveryConfig(enabled=False, reconcile_on_start=False),
        taskstore_slice=TaskStoreConfig(db_path=str(tmp_path / "taskstore.sqlite3")),
    )


async def test_transcript_paginates_and_preserves_missing_references(tmp_path: Path):
    config = _config(tmp_path)
    project = make_project_record(name="sample", project_root_locator=str(tmp_path / "project"))
    foreign = make_project_record(name="foreign", project_root_locator=str(tmp_path / "foreign"))
    paths = ProjectPaths.from_locator(project.project_root_locator)
    paths.initialize(project.project_id)

    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        projects = ctx.get(PROJECT_STORE.name)
        await projects.upsert(project)
        await projects.upsert(foreign)
        rooms = ctx.get(ROOM_MANAGER.name)
        created = await rooms.handle_command(
            "room_create",
            {"project_id": project.project_id, "name": "test"},
        )
        room_id = created["data"]["room"]["room_id"]
        url = f"/api/v1/projects/{project.project_id}/rooms/{room_id}/transcript"
        app = ctx.get(OBSERVER_ENDPOINT.name).app
        transport = httpx2.ASGITransport(app=app, client=("192.0.2.10", 50000))
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get(url)).status_code == 403
            client.headers["Authorization"] = "Bearer test-token"
            assert (await client.get(url)).json()["entries"] == []
            assert not paths.action_chain_db_path.exists()

            # 以正常持久受理入口写入消息，关闭写侧后再读 Room 日志。
            async with MessageManager(
                paths.action_chain_db_path,
                project_id=project.project_id,
                agent_ids=("worker",),
            ) as messages:
                sent = await messages.send_external(
                    sender="reader",
                    recipient_ids=[],
                    content="Core 正文",
                    idempotency_key="request",
                    metadata={"room_ids": [room_id]},
                )
            entry = await rooms.append_log(
                room_id,
                author="reader",
                author_type=RoomSubjectType.HUMAN,
                data={"mm_message_id": sent.message_id},
            )
            assert entry["success"], entry
            await rooms.append_log(
                room_id,
                author="worker",
                author_type=RoomSubjectType.AGENT,
                data={"message": "历史正文", "via": "chain_filter", "ability": "end_task"},
            )
            await rooms.append_log(
                room_id,
                author="reader",
                author_type=RoomSubjectType.HUMAN,
                data={"message": "最后一条", "ability": 42},
            )

            current = (await client.get(url, params={"limit": 1})).json()
            assert [e["seq"] for e in current["entries"]] == [3]
            assert current["entries"][0]["ability"] is None
            assert current["next_before_seq"] == current["latest_seq"] == 3
            previous = (await client.get(url, params={"before_seq": 3})).json()
            assert [e["content"] for e in previous["entries"]] == ["Core 正文", "历史正文"]
            assert [e["human_state"] for e in previous["entries"]] == [None, None]
            assert [e["ability"] for e in previous["entries"]] == [None, "end_task"]
            incremental = (await client.get(url, params={"since_seq": 0, "limit": 1})).json()
            assert incremental["next_since_seq"] == 1
            assert incremental["has_more"]
            legacy = await rooms.handle_command("room_get_log", {"room_id": room_id})
            assert (
                legacy["data"]["entries"][0]["data"]["message"] == previous["entries"][0]["content"]
            )
            assert (
                await client.get(url.replace(project.project_id, foreign.project_id))
            ).status_code == 404
            assert (
                await client.get(url, params={"before_seq": 2, "since_seq": 0})
            ).status_code == 400
            registry = ctx.get(CORE_CLUSTER_REGISTRY.name)
            assert not registry.has_cluster(project.project_id)

            # 模拟旧日志指向已经缺失的 Core 消息。
            await rooms.store.append_log(
                room_id,
                lambda seq: make_room_log_record(
                    room_id=room_id,
                    seq=seq,
                    author="reader",
                    author_type=RoomSubjectType.HUMAN,
                    data={
                        "mm_message_id": "missing",
                        "message": "未验证的旧正文",
                        "ability": "end_task",
                    },
                ),
            )
            missing = (await client.get(url, params={"since_seq": 3})).json()
            assert missing["next_since_seq"] == 4
            assert missing["entries"][0]["message_id"] == "missing"
            assert missing["entries"][0]["content"] == ""
            assert missing["entries"][0]["human_state"] is None
            assert missing["entries"][0]["ability"] is None
            legacy = await rooms.handle_command("room_get_log", {"room_id": room_id})
            assert legacy["data"]["count"] == 3

            await projects.archive(project.project_id, expected_version=project.version)
            archived = await client.get(url)
            assert archived.status_code == 409
            assert archived.json()["error"]["code"] == "project_archived"


async def test_transcript_honors_disabled_observer_api(tmp_path: Path):
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path, enabled=False), profile="full")
        app = ctx.get(OBSERVER_ENDPOINT.name).app
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            result = await client.get("/api/v1/projects/p/rooms/r/transcript")
            assert result.status_code == 503
            assert result.json()["error"]["code"] == "capability_unavailable"


async def test_transcript_links_both_opener_turns_and_positions_injection(tmp_path: Path):
    project = make_project_record(name="sample", project_root_locator=str(tmp_path / "project"))
    paths = ProjectPaths.from_locator(project.project_root_locator)
    paths.initialize(project.project_id)
    recipients = ("worker-a", "worker-b")
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="full")
        await ctx.get(PROJECT_STORE.name).upsert(project)
        rooms = ctx.get(ROOM_MANAGER.name)
        created = await rooms.handle_command(
            "room_create", {"project_id": project.project_id, "name": "test"}
        )
        room_id = created["data"]["room"]["room_id"]
        messages = MessageManager(
            paths.action_chain_db_path,
            project_id=project.project_id,
            agent_ids=recipients,
            session_routing=True,
        )
        await messages.open(prepare_context=True, start_dispatcher=False)
        try:
            async with messages.context_backend(None) as backend:
                chains = {agent: ContextManager(agent, persistence=backend) for agent in recipients}
                for chain in chains.values():
                    await chain.persist()
                opener = await messages.send_external(
                    sender="reader",
                    recipient_ids=recipients,
                    content="opened",
                    idempotency_key="opener",
                    metadata={"room_id": room_id},
                )
                claims = {}
                for agent in recipients:
                    claim = await messages._delivery_store.claim_delivery(
                        agent, session_aware=True, session_routing=True, require_turns=True
                    )
                    assert claim is not None
                    claims[agent] = claim.execution
                injected = await messages.send_external(
                    sender="reader",
                    recipient_ids=(recipients[0],),
                    content="injected",
                    idempotency_key="injection",
                    delivery_policy="inject",
                    await_reply=False,
                    metadata={"room_id": room_id},
                )
                delivery = injected.deliveries[0]
                execution = claims[recipients[0]]
                chain = chains[recipients[0]]
                assert execution.turn_id is not None
                await chain.attach_claimed_turn(
                    turn_id=execution.turn_id,
                    session_id=chain.active_session_id,
                    opener_delivery_id=execution.delivery_id,
                )
                chain.begin_iteration()
                chain.add_messages(
                    [ChatMessage.user("opened", metadata={"delivery_id": execution.delivery_id})]
                    + list(await messages.load_turn_inputs(execution.turn_id))
                )
                node = chain.commit_iteration()
                await messages._delivery_store.commit_iteration(
                    execution,
                    ContextChanges(
                        agent_name=recipients[0],
                        nodes=(node,),
                        sessions=(chain.get_active_session(),),
                        branches=(chain.get_active_session_branch(),),
                    ),
                    drafts=(),
                    consumed_deliveries=frozenset({delivery.delivery_id}),
                    inline_updates={},
                )
            for message in (opener, injected):
                result = await rooms.append_log(
                    room_id,
                    author="reader",
                    author_type=RoomSubjectType.HUMAN,
                    data={"mm_message_id": message.message_id},
                )
                assert result["success"], result

            app = ctx.get(OBSERVER_ENDPOINT.name).app
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    f"/api/v1/projects/{project.project_id}/rooms/{room_id}/transcript"
                )
            assert response.status_code == 200, response.text
            data = response.json()
            first, followup = data["entries"]
            assert len(first["deliveries"]) == len(data["turns"]) == 2
            assert {card["turn_id"] for card in data["turns"]} == {
                execution.turn_id for execution in claims.values()
            }
            assert {card["opener_entry_id"] for card in data["turns"]} == {first["entry_id"]}
            assert followup["insert_before_turn_id"] == execution.turn_id
            assert followup["injection_step"] == 1
            assert followup["human_state"] == "injected"
        finally:
            await messages.close()
