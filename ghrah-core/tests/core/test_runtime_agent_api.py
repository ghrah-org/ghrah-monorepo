"""按进程内资源入口验收真实存储、条件启停与持久消息受理。"""

import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path

import aiosqlite
import pytest
from ghrah.protocol.runtime import RuntimePrincipal
from ghrah.protocol.runtime_agents import RuntimeAgentResource, RuntimeAgentsPage
from ghrah.protocol.runtime_resources import RuntimeDeliveryView

from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.core.runtime import CoreRuntime, RuntimeConfig

PRINCIPAL = RuntimePrincipal(scope_ref="scope")
CONTRACTS = Path(__file__).resolve().parents[3] / "ghrah-protocol/contracts/runtime"


class EchoLLM:
    model = "resource-api-test"

    def configure_tools(self, tools):
        pass

    def apply_model_overrides(self, overrides):
        pass

    async def generate(self, messages, tools=None):
        return LLMResponse(content_blocks=[TextBlock(text="resource reply")])


def config(tmp_path, **kwargs):
    return RuntimeConfig(
        scope_ref="scope", database_path=str(tmp_path / "core.db"), enabled_agent_api=True, **kwargs
    )


def body(external_ref="worker"):
    value = json.loads((CONTRACTS / "fixtures/agent.create.json").read_text())
    value["external_ref"] = external_ref
    return value


async def request(runtime, method, path, body=None, headers=None, principal=PRINCIPAL):
    return await runtime.dispatch(method, path, body=body, headers=headers, principal=principal)


async def create(runtime, external_ref="worker", key="create-worker", **kwargs):
    return await request(
        runtime,
        "POST",
        "/v1/agents",
        body=body(external_ref),
        headers={"Idempotency-Key": key},
        **kwargs,
    )


async def state(runtime, agent_id, target, etag='"rev-1"'):
    return await request(
        runtime,
        "PUT",
        f"/v1/agents/{agent_id}/state",
        body={"state": target},
        headers={"If-Match": etag},
    )


async def test_create_start_message_stop_restore_chain(tmp_path):
    runtime = CoreRuntime(config(tmp_path), llm_factory=lambda _: EchoLLM())
    assert (await create(runtime)).status == 503
    await runtime.init()
    try:
        created = await create(runtime)
        assert created.status == 201, created.body
        agent = RuntimeAgentResource.model_validate(created.body)
        assert agent.state == "stopped" and agent.incarnation_id is None
        assert await runtime._unit.supervisor.list_agents() == []
        assert created.headers["Location"] == f"/v1/agents/{agent.agent_id}"
        assert created.headers["ETag"] == '"rev-1"'
        started = await state(runtime, agent.agent_id, "running")
        assert started.status == 200, started.body
        assert started.body["recovered"] == "initialized"
        assert started.body["agent"]["state"] == "idle"
        incarnation = started.body["agent"]["incarnation_id"]
        assert (await state(runtime, agent.agent_id, "running")).body["recovered"] is None
        accepted = await request(
            runtime,
            "POST",
            f"/v1/agents/{agent.agent_id}/messages",
            body={"text": "work", "kind": "user"},
            headers={"Idempotency-Key": "message-1"},
        )
        assert accepted.status == 202, accepted.body
        assert "content" not in accepted.body
        delivered = await request(runtime, "GET", accepted.headers["Location"] + "?wait=5")
        assert delivered.status == 200, delivered.body
        view = RuntimeDeliveryView.model_validate(delivered.body)
        assert view.state == "completed" and view.attempts == 1
        assert view.created_at <= view.updated_at
        assert "content" not in delivered.body
        stopped = await state(runtime, agent.agent_id, "stopped")
        assert stopped.body["agent"]["state"] == "stopped"
        assert (await state(runtime, agent.agent_id, "stopped")).body["recovered"] is None
        assert (await request(runtime, "GET", created.headers["Location"])).status == 200
    finally:
        await runtime.stop()
    assert (await request(runtime, "GET", "/v1/agents")).status == 503
    restarted = CoreRuntime(config(tmp_path), llm_factory=lambda _: EchoLLM())
    await restarted.init()
    try:
        assert await restarted._unit.supervisor.list_agents() == []
        replay = await create(restarted)
        assert replay.status == 201 and replay.body == created.body
        assert replay.headers["Idempotency-Replayed"] == "true"
        restored = await state(restarted, agent.agent_id, "running")
        assert restored.body["recovered"] == "restored"
        assert restored.body["agent"]["incarnation_id"] != incarnation
        assert (await request(restarted, "GET", accepted.headers["Location"])).body[
            "state"
        ] == "completed"
    finally:
        await restarted.stop()


async def test_create_concurrent_replay_conflict_and_scope(tmp_path):
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    try:
        results = await asyncio.gather(*(create(runtime) for _ in range(6)))
        assert all(result.status == 201 for result in results)
        assert sum("Idempotency-Replayed" in result.headers for result in results) == 5
        assert len({result.body["agent_id"] for result in results}) == 1
        assert (await create(runtime, "different")).status == 409
        assert (await create(runtime, key="another-key")).status == 409
        forged = await create(runtime, principal=RuntimePrincipal(scope_ref="other"))
        assert forged.status == 403
        page = await request(runtime, "GET", "/v1/agents")
        assert len(RuntimeAgentsPage.model_validate(page.body).items) == 1
    finally:
        await runtime.stop()


async def test_conditions_methods_validation_and_disabled_default(tmp_path):
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    try:
        created = await create(runtime)
        url = created.headers["Location"]
        missing = await request(runtime, "PUT", url + "/state", body={"state": "running"})
        assert missing.status == 428 and "code" not in missing.body
        stale = await state(runtime, created.body["agent_id"], "running", '"rev-0"')
        assert stale.status == 412 and stale.body["details"]["current_etag"] == '"rev-1"'
        wrong = await request(runtime, "POST", url)
        assert (
            wrong.status == 405
            and wrong.headers["Allow"] == "GET, PATCH, DELETE"
            and "code" not in wrong.body
        )
        assert (await request(runtime, "GET", "/v1/agents/not-an-id")).status == 404
        assert (await request(runtime, "POST", "/v1/agents", body=body())).status == 422
        assert (
            await request(
                runtime,
                "POST",
                "/v1/agents",
                body={**body(), "scope_ref": "other"},
                headers={"Idempotency-Key": "bad"},
            )
        ).status == 422
        incomplete = body()
        del incomplete["config"]["prompt"]["template_ref"]
        assert (
            await request(
                runtime, "POST", "/v1/agents", body=incomplete, headers={"Idempotency-Key": "bad"}
            )
        ).status == 422
    finally:
        await runtime.stop()
    disabled = CoreRuntime(replace(config(tmp_path), enabled_agent_api=False))
    await disabled.init()
    try:
        assert (await request(disabled, "GET", "/v1/agents")).status == 404
    finally:
        await disabled.stop()


@pytest.mark.parametrize(
    "change", ["packages", "source_mode", "surface", "model", "window", "permissions", "running"]
)
async def test_unsupported_assembly_never_silently_falls_back(tmp_path, change):
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    try:
        value = body()
        if change == "packages":
            value["config"]["ability_request"]["packages"] = []
        elif change == "source_mode":
            value["config"]["ability_request"]["packages"][0]["origin"] = "installed"
        elif change == "surface":
            value["config"]["ability_request"]["surface"]["mode"] = "static"
        elif change == "model":
            value["config"]["model"]["model_overrides"] = {"unsupported_model_option": 1}
        elif change == "window":
            value["config"]["context_policy"]["window"] = {"unsupported_window_option": 1}
        elif change == "permissions":
            value["config"]["permission_request"]["auto_approve"] = ["conversation"]
        else:
            value["state"] = "running"
        result = await request(
            runtime, "POST", "/v1/agents", body=value, headers={"Idempotency-Key": "unsupported"}
        )
        assert result.status == 501, result.body
        assert (await request(runtime, "GET", "/v1/agents")).body["items"] == []
        assert await runtime._unit.supervisor.list_agents() == []
    finally:
        await runtime.stop()


async def test_message_idempotency_and_metadata_are_not_internal_commands(tmp_path):
    runtime = CoreRuntime(config(tmp_path), llm_factory=lambda _: EchoLLM())
    await runtime.init()
    try:
        created = await create(runtime)
        agent = created.body["agent_id"]
        url = created.headers["Location"] + "/messages"
        payload = {
            "text": "work",
            "kind": "user",
            "metadata": {
                "execute_ability": {"name": "missing"},
                "room_ids": ["injected"],
                "message_type": "error",
            },
        }
        assert (
            await request(
                runtime, "POST", url, body=payload, headers={"Idempotency-Key": "message"}
            )
        ).status == 409
        assert (await state(runtime, agent, "running")).status == 200
        results = await asyncio.gather(
            *(
                request(runtime, "POST", url, body=payload, headers={"Idempotency-Key": "message"})
                for _ in range(3)
            )
        )
        assert all(result.status == 202 for result in results), results
        assert all(result.body == results[0].body for result in results)
        assert sum("Idempotency-Replayed" in result.headers for result in results) == 2
        delivery = await request(runtime, "GET", results[0].headers["Location"] + "?wait=5")
        assert delivery.body["state"] == "completed", delivery.body
        changed = await request(
            runtime,
            "POST",
            url,
            body={**payload, "text": "different"},
            headers={"Idempotency-Key": "message"},
        )
        assert changed.status == 409
        assert (
            await request(
                runtime,
                "POST",
                url,
                body={"text": "work", "kind": "command"},
                headers={"Idempotency-Key": "command"},
            )
        ).status == 501
        async with aiosqlite.connect(runtime.config.database_path) as db:
            rows = await (
                await db.execute("SELECT metadata FROM mm_messages WHERE external_sender=1")
            ).fetchall()
        assert len(rows) == 1
        metadata = json.loads(rows[0][0])
        assert set(metadata) == {"runtime_message"}
        assert metadata["runtime_message"]["metadata"] == payload["metadata"]
        await state(runtime, agent, "stopped")
        assert (
            await request(
                runtime, "POST", url, body=payload, headers={"Idempotency-Key": "message"}
            )
        ).body == results[0].body
    finally:
        await runtime.stop()


async def test_summary_pagination_and_expiry(tmp_path):
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    try:
        for index in range(3):
            assert (await create(runtime, f"worker-{index}", f"key-{index}")).status == 201
        page = await request(runtime, "GET", "/v1/agents?limit=2")
        assert len(page.body["items"]) == 2 and page.body["has_more"]
        assert "config" not in page.body["items"][0]
        assert "system_prompt" not in page.body["items"][0]["prompt"]
        cursor = page.body["next_cursor"]
        tail = await request(runtime, "GET", "/v1/agents?limit=2&cursor=" + cursor)
        assert len(tail.body["items"]) == 1 and tail.body["next_cursor"] is None
        ids = [item["agent_id"] for item in page.body["items"] + tail.body["items"]]
        assert len(set(ids)) == 3
        for query in ("limit=0", "limit=501", "limit=no", "limit=1&limit=2", "unknown=1"):
            assert (await request(runtime, "GET", "/v1/agents?" + query)).status == 422
        expired = await request(runtime, "GET", "/v1/agents?cursor=invalid")
        assert expired.status == 410 and expired.body["retryable"]
    finally:
        await runtime.stop()
    restarted = CoreRuntime(config(tmp_path))
    await restarted.init()
    try:
        assert (await request(restarted, "GET", "/v1/agents?cursor=" + cursor)).status == 410
    finally:
        await restarted.stop()


async def test_config_quota_rejects_whole_object_and_keeps_long_prompt(tmp_path):
    runtime = CoreRuntime(config(tmp_path, config_quota=300_000))
    await runtime.init()
    try:
        value = body()
        value["config"]["prompt"]["system_prompt"] = "界" * 70_000
        result = await request(
            runtime, "POST", "/v1/agents", body=value, headers={"Idempotency-Key": "long"}
        )
        assert (
            result.status == 201
            and result.body["config"]["prompt"]["system_prompt"] == "界" * 70_000
        )
        large = copy.deepcopy(value)
        large["external_ref"] = "too-large"
        large["config"]["prompt"]["system_prompt"] = "界" * 100_000
        result = await request(
            runtime, "POST", "/v1/agents", body=large, headers={"Idempotency-Key": "large"}
        )
        assert result.status == 413 and "code" not in result.body
        assert len((await request(runtime, "GET", "/v1/agents")).body["items"]) == 1
    finally:
        await runtime.stop()


async def test_failed_start_is_visible_and_can_be_retried_without_recreating_definition(
    tmp_path, monkeypatch
):
    runtime = CoreRuntime(config(tmp_path), llm_factory=lambda _: EchoLLM())
    await runtime.init()
    calls = 0
    original_prepare = runtime._unit.supervisor._prepare_agent_persistence

    async def fail_first_recovery(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated storage recovery failure")
        return await original_prepare(*args)

    monkeypatch.setattr(runtime._unit.supervisor, "_prepare_agent_persistence", fail_first_recovery)
    try:
        created = await create(runtime)
        agent_id = created.body["agent_id"]
        failed = await state(runtime, agent_id, "running")
        assert failed.status == 500 and failed.body["code"] == "INTERNAL"
        observed = await request(runtime, "GET", created.headers["Location"])
        assert observed.body["state"] == "error"
        assert observed.body["last_error"]["code"] == "INTERNAL"
        assert "simulated storage recovery failure" not in json.dumps(observed.body)
        retried = await state(runtime, agent_id, "running")
        assert retried.status == 200 and retried.body["agent"]["state"] == "idle"
        assert retried.body["agent"]["last_error"] is None
        assert len((await request(runtime, "GET", "/v1/agents")).body["items"]) == 1
    finally:
        await runtime.stop()


async def test_resource_init_failure_releases_database_owner(tmp_path, monkeypatch):
    from ghrah.core.runtime_agent_store import RuntimeAgentStore

    original_open = RuntimeAgentStore.open

    async def failing_open(store):
        await original_open(store)
        raise RuntimeError("resource preparation failed")

    monkeypatch.setattr(RuntimeAgentStore, "open", failing_open)
    runtime = CoreRuntime(config(tmp_path))
    with pytest.raises(RuntimeError, match="resource preparation"):
        await runtime.init()
    assert runtime.status().phase == "failed"
    assert runtime._agent_api.store.db is None
    monkeypatch.setattr(RuntimeAgentStore, "open", original_open)
    replacement = CoreRuntime(config(tmp_path))
    await replacement.init()
    try:
        assert replacement.status().ready
    finally:
        await replacement.stop()


async def test_resource_start_interrupted_by_runtime_stop_does_not_leak_agent(
    tmp_path, monkeypatch
):
    runtime = CoreRuntime(config(tmp_path, stop_timeout=0.02), llm_factory=lambda _: EchoLLM())
    await runtime.init()
    created = await create(runtime)
    supervisor = runtime._unit.supervisor
    original_spawn = supervisor.spawn_agent
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_spawn(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(supervisor, "spawn_agent", slow_spawn)
    starting = asyncio.create_task(state(runtime, created.body["agent_id"], "running"))
    await entered.wait()
    stopping = asyncio.create_task(runtime.stop())
    try:
        # 等待请求被停止宽限取消，再允许受屏蔽的装配结束以核对它被回收。
        await asyncio.sleep(0.05)
        assert (await request(runtime, "GET", "/v1/status")).body["phase"] == "stopping"
    finally:
        release.set()
    assert (await starting).status == 503
    await asyncio.wait_for(stopping, timeout=5)
    assert await supervisor.list_agents() == []
    replacement = CoreRuntime(config(tmp_path), llm_factory=lambda _: EchoLLM())
    await replacement.init()
    await replacement.stop()


async def test_failed_predecessor_reports_dependency_block_instead_of_waiting_forever(tmp_path):
    class FailingLLM(EchoLLM):
        async def generate(self, messages, tools=None):
            raise RuntimeError("simulated generation failure")

    runtime = CoreRuntime(config(tmp_path), llm_factory=lambda _: FailingLLM())
    await runtime.init()
    try:
        created = await create(runtime)
        agent_id = created.body["agent_id"]
        await state(runtime, agent_id, "running")
        url = created.headers["Location"] + "/messages"
        first = await request(
            runtime,
            "POST",
            url,
            body={"text": "first", "kind": "user"},
            headers={"Idempotency-Key": "first"},
        )
        failed = await request(runtime, "GET", first.headers["Location"] + "?wait=5")
        assert failed.body["state"] == "failed", failed.body
        second = await request(
            runtime,
            "POST",
            url,
            body={"text": "second", "kind": "user"},
            headers={"Idempotency-Key": "second"},
        )
        blocked = await request(runtime, "GET", second.headers["Location"] + "?wait=5")
        assert blocked.body["state"] == "blocked"
        assert blocked.body["blocked_reason"] == "dependency"
        observed = await request(runtime, "GET", created.headers["Location"])
        assert observed.body["current_delivery"] == {
            "delivery_id": second.body["delivery_id"],
            "state": "blocked",
        }
    finally:
        await runtime.stop()
