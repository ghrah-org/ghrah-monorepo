"""真实 Core 执行后的公共读面、序号持久性和有界分页验收。"""

import json
from urllib.parse import urlencode

import aiosqlite
import pytest
from ghrah.protocol.runtime import RuntimePrincipal
from ghrah.protocol.runtime_resources import RuntimeNodeView, RuntimeTimelinePage
from test_runtime_agent_api import EchoLLM, body, config, create, request, state

from ghrah.core.runtime import CoreRuntime


async def execute(runtime, agent_id, key="message"):
    accepted = await request(
        runtime,
        "POST",
        f"/v1/agents/{agent_id}/messages",
        body={"text": "work", "kind": "user"},
        headers={"Idempotency-Key": key},
    )
    assert accepted.status == 202, accepted.body
    delivered = await request(runtime, "GET", accepted.headers["Location"] + "?wait=5")
    assert delivered.body["state"] == "completed", delivered.body
    return delivered.body["last_node_id"]


async def test_read_snapshot_new_commits_restart_and_conditional_node(tmp_path):
    runtime = CoreRuntime(config(tmp_path), llm_factory=lambda _: EchoLLM())
    await runtime.init()
    try:
        created = await create(runtime)
        agent = created.body["agent_id"]
        url = f"/v1/agents/{agent}/timeline"
        empty = await request(runtime, "GET", url)
        assert empty.body["items"] == [] and empty.body["watermark_seq"] == 0
        await state(runtime, agent, "running")
        node_id = await execute(runtime, agent)
        first = await request(runtime, "GET", url + "?limit=1")
        page = RuntimeTimelinePage.model_validate(first.body)
        assert page.has_more
        watermark = page.watermark_seq
        await execute(runtime, agent, "later")
        items = list(page.items)
        cursor = page.next_cursor
        while cursor:
            response = await request(
                runtime, "GET", url + "?" + urlencode({"cursor": cursor, "limit": 1})
            )
            next_page = RuntimeTimelinePage.model_validate(response.body)
            assert next_page.watermark_seq == watermark
            items.extend(next_page.items)
            cursor = next_page.next_cursor
        assert len({item.node_id for item in items}) == len(items)
        assert max(item.seq for item in items) == watermark
        assert [(item.branch_id, item.seq) for item in items] == sorted(
            (item.branch_id, item.seq) for item in items
        )
        rebuilt = await request(
            runtime, "GET", url + "?" + urlencode({"cursor": page.snapshot_cursor})
        )
        assert [item["node_id"] for item in rebuilt.body["items"]] == [
            item.node_id for item in items
        ]
        refreshed = await request(runtime, "GET", url)
        assert refreshed.body["watermark_seq"] > watermark
        assert all("messages_delta" not in item for item in refreshed.body["items"])
        detail = await request(runtime, "GET", f"/v1/nodes/{node_id}")
        node = RuntimeNodeView.model_validate(detail.body)
        assert node.seq in {item.seq for item in items}
        assert "delivery_context" not in json.dumps(detail.body)
        assert node.size == next(item.payload_ref.size for item in items if item.node_id == node_id)
        branch = await request(
            runtime,
            "GET",
            url + "?" + urlencode({"branch_id": node.branch_id, "session_id": node.session_id}),
        )
        assert all(item["branch_id"] == node.branch_id for item in branch.body["items"])
        assert (
            await request(
                runtime,
                "GET",
                f"/v1/nodes/{node_id}",
                headers={"If-None-Match": detail.headers["ETag"]},
            )
        ).status == 304
        assert (
            await request(runtime, "GET", f"/v1/nodes/{node_id}?schema_version=2")
        ).status == 406
        assert (
            await request(
                runtime,
                "GET",
                url
                + "?"
                + urlencode({"cursor": page.snapshot_cursor, "branch_id": node.branch_id}),
            )
        ).status == 410
    finally:
        await runtime.stop()
    restarted = CoreRuntime(config(tmp_path), llm_factory=lambda _: EchoLLM())
    await restarted.init()
    try:
        assert (
            await request(restarted, "GET", url + "?" + urlencode({"cursor": page.snapshot_cursor}))
        ).status == 410
        persisted = await request(restarted, "GET", f"/v1/nodes/{node_id}")
        assert persisted.body == detail.body and persisted.headers["ETag"] == detail.headers["ETag"]
        await state(restarted, agent, "running")
        later = await execute(restarted, agent, "after-restart")
        assert (await request(restarted, "GET", f"/v1/nodes/{later}")).body["seq"] > refreshed.body[
            "watermark_seq"
        ]
    finally:
        await restarted.stop()


@pytest.mark.parametrize(
    "suffix,status",
    [
        ("?limit=0", 422),
        ("?limit=501", 422),
        ("?limit=1&limit=2", 422),
        ("?include_payloads=true", 422),
        ("?cursor=invalid", 410),
    ],
)
async def test_read_query_validation_and_scope(tmp_path, suffix, status):
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    try:
        agent = (await create(runtime)).body["agent_id"]
        url = f"/v1/agents/{agent}/timeline"
        assert (await request(runtime, "GET", url + suffix)).status == status
        denied = RuntimePrincipal(scope_ref="another")
        assert (await request(runtime, "GET", url, principal=denied)).status == 403
        missing = "/v1/nodes/nod_00000000000000000000000000"
        assert (await request(runtime, "GET", missing)).status == 404
        assert (await request(runtime, "GET", missing, principal=denied)).status == 403
        wrong_method = await request(runtime, "POST", url)
        assert wrong_method.status == 405 and wrong_method.headers["Allow"] == "GET"
    finally:
        await runtime.stop()


async def test_node_sequences_vacuum_no_read_writes_and_migrate(tmp_path):
    runtime = CoreRuntime(config(tmp_path), llm_factory=lambda _: EchoLLM())
    await runtime.init()
    try:
        agent = (await create(runtime)).body["agent_id"]
        await state(runtime, agent, "running")
        node_id = await execute(runtime, agent)
        url = f"/v1/agents/{agent}/timeline"
        before = await request(runtime, "GET", url)
        total = runtime._agent_api.store.connection().total_changes
        await request(runtime, "GET", url)
        await request(runtime, "GET", f"/v1/nodes/{node_id}")
        assert runtime._agent_api.store.connection().total_changes == total
    finally:
        await runtime.stop()
    async with aiosqlite.connect(config(tmp_path).database_path) as db:
        await db.execute("VACUUM")
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    try:
        after = await request(runtime, "GET", url)
        assert after.body["items"] == before.body["items"]
    finally:
        await runtime.stop()
    # 模拟上一版本的资源数据库：历史节点尚未分配新序号。
    async with aiosqlite.connect(config(tmp_path).database_path) as db:
        await db.execute("DROP TRIGGER runtime_allocate_node_seq")
        await db.execute("DROP TABLE runtime_node_counters")
        await db.execute("UPDATE nodes SET runtime_node_seq=NULL")
        await db.commit()
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    try:
        migrated = await request(runtime, "GET", url)
        seqs = [item["seq"] for item in migrated.body["items"]]
        assert sorted(seqs) == list(range(1, len(seqs) + 1))
        assert (await request(runtime, "GET", f"/v1/nodes/{node_id}")).status == 200
    finally:
        await runtime.stop()


async def test_model_window_options_reach_actual_execution(tmp_path):
    configs = []

    def factory(value):
        configs.append(value)
        return EchoLLM()

    runtime = CoreRuntime(config(tmp_path), llm_factory=factory)
    await runtime.init()
    try:
        value = body()
        value["config"]["model"]["model_overrides"] = {
            "temperature": 0,
            "max_tokens": 128,
            "extra_params": {"seed": 42},
        }
        value["config"]["context_policy"]["window"] = {
            "max_tokens": 8192,
            "strategies": ["sliding_window", "truncation"],
            "sliding_window_size": 4,
        }
        created = await request(
            runtime, "POST", "/v1/agents", body=value, headers={"Idempotency-Key": "configured"}
        )
        assert created.status == 201, created.body
        agent = created.body["agent_id"]
        assert (await state(runtime, agent, "running")).status == 200
        await execute(runtime, agent)
        assert configs and configs[-1].model_overrides.temperature == 0
        assert configs[-1].model_overrides.extra_params == {"seed": 42}
        assert configs[-1].window.max_tokens == 8192
        assert configs[-1].window.strategies == ["sliding_window", "truncation"]
        assert configs[-1].window.sliding_window_size == 4
    finally:
        await runtime.stop()


@pytest.mark.parametrize(
    "group,field,value",
    [
        ("model", "model_overrides", {"temperature": True}),
        ("model", "model_overrides", {"max_tokens": 0}),
        ("model", "model_overrides", {"extra_params": []}),
        ("context_policy", "window", {"max_tokens": "8192"}),
        ("context_policy", "window", {"strategies": ["unknown"]}),
        ("context_policy", "window", {"compact_keep_recent": 1}),
        ("context_policy", "window", {"compact_threshold": 0}),
    ],
)
async def test_invalid_options_rejected_before_definition_or_spawn(tmp_path, group, field, value):
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    try:
        data = body()
        data["config"][group][field] = value
        result = await request(
            runtime, "POST", "/v1/agents", body=data, headers={"Idempotency-Key": "invalid"}
        )
        assert result.status == 422, result.body
        assert (await request(runtime, "GET", "/v1/agents")).body["items"] == []
    finally:
        await runtime.stop()


async def test_sequences_are_per_agent_and_deleted_anchor_expires(tmp_path):
    runtime = CoreRuntime(config(tmp_path), llm_factory=lambda _: EchoLLM())
    await runtime.init()
    try:
        agents = [(await create(runtime, name, key=name)).body["agent_id"] for name in ("a", "b")]
        for agent in agents:
            await state(runtime, agent, "running")
            await execute(runtime, agent)
        urls = [f"/v1/agents/{agent}/timeline" for agent in agents]
        pages = [(await request(runtime, "GET", url)).body for url in urls]
        assert all(min(item["seq"] for item in page["items"]) == 1 for page in pages)
        assert {item["node_id"] for item in pages[0]["items"]}.isdisjoint(
            item["node_id"] for item in pages[1]["items"]
        )
        first = await request(runtime, "GET", urls[0] + "?limit=1")
        cursor = first.body["next_cursor"]
        assert (
            await request(runtime, "GET", urls[1] + "?" + urlencode({"cursor": cursor}))
        ).status == 410
        await state(runtime, agents[0], "stopped")
        # 模拟权威保留清理：读面必须拒绝已删除的翻页锚点。
        db = runtime._agent_api.store.connection()
        await db.execute(
            "DELETE FROM nodes WHERE agent_name=? AND runtime_node_seq=?",
            (agents[0], first.body["items"][0]["seq"]),
        )
        await db.commit()
        expired = await request(runtime, "GET", urls[0] + "?" + urlencode({"cursor": cursor}))
        assert expired.status == 410 and expired.body["code"] == "CURSOR_EXPIRED"
    finally:
        await runtime.stop()
