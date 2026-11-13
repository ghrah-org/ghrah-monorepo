"""新契约管理的真实存储、条件写和授权恢复回归。"""

import copy
import json
from pathlib import Path

from ghrah.protocol.runtime import RuntimePrincipal

from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.core.runtime import CoreRuntime, RuntimeConfig


class EchoModel:
    model = "management-test"

    def configure_tools(self, tools):
        pass

    def apply_model_overrides(self, overrides):
        pass

    async def generate(self, messages, tools=None):
        return LLMResponse(content_blocks=[TextBlock(text="management reply")])


async def test_management_persists_configuration_and_tool_selection(tmp_path):
    config = RuntimeConfig(
        scope_ref="management", database_path=str(tmp_path / "core.db"), enabled_agent_api=True
    )
    runtime = CoreRuntime(config, llm_factory=lambda _: EchoModel())
    await runtime.init()
    principal = RuntimePrincipal(scope_ref="management")

    async def call(method, path, body=None, etag=None, key=None):
        headers = {}
        if etag is not None:
            headers["If-Match"] = etag
        if key is not None:
            headers["Idempotency-Key"] = key
        return await runtime.dispatch(method, path, principal=principal, headers=headers, body=body)

    fixture = (
        Path(__file__).resolve().parents[3]
        / "ghrah-protocol/contracts/runtime/fixtures/agent.create.json"
    )
    request = json.loads(fixture.read_text())
    try:
        created = await call("POST", "/v1/agents", request, key="create")
        assert created.status == 201, created.body
        path = created.headers["Location"]
        changed = copy.deepcopy(request["config"])
        changed["prompt"]["system_prompt"] = "管理提示词"
        assert (await call("PATCH", path, {"config": changed})).status == 428
        patched = await call("PATCH", path, {"config": changed}, etag=created.headers["ETag"])
        assert patched.status == 200, patched.body
        assert patched.headers["ETag"] == '"rev-2"'
        assert (
            await call("PUT", path + "/state", {"state": "running"}, etag='"rev-1"')
        ).status == 412
        started = await call("PUT", path + "/state", {"state": "running"}, etag='"rev-2"')
        assert started.status == 200, started.body
        assert (await call("PATCH", path, {"config": changed}, etag='"rev-2"')).status == 409
        abilities = await call("GET", path + "/abilities")
        assert abilities.status == 200, abilities.body
        assert abilities.body["bound_tools"]
        tool = abilities.body["bound_tools"][0]
        removed = await call(
            "DELETE", path + "/abilities/tools/" + tool["name"], etag=abilities.headers["ETag"]
        )
        assert removed.status == 200, removed.body
        assert all(item["name"] != tool["name"] for item in removed.body["bound_tools"])
        assert (
            await call("PUT", path + "/state", {"state": "stopped"}, etag='"rev-2"')
        ).status == 200
        assert (
            await call("PUT", path + "/state", {"state": "running"}, etag='"rev-2"')
        ).status == 200
        restored = await call("GET", path + "/abilities")
        assert all(item["name"] != tool["name"] for item in restored.body["bound_tools"])
        assert restored.body["revision"] == removed.body["revision"]
        catalogue = await call("GET", "/v1/abilities")
        assert catalogue.status == 200 and catalogue.body["packages"]
        instance = await call("POST", "/v1/ability-instances", {"package": "ghrah.core"})
        assert instance.status == 200, instance.body
        assert instance.body["instance_id"] == "ghrah-core"
        grants = await call("GET", path + "/abilities")
        grant = {"alias": "core", "instance_id": "ghrah-core", "members": ["session_info"]}
        granted = await call(
            "PUT", path + "/abilities/grants/core", grant, etag=grants.headers["ETag"]
        )
        assert granted.status == 200, granted.body
        bound = await call(
            "PUT",
            path + "/abilities/tools/session_info",
            {"alias": "core", "member": "session_info", "tool_name": "session_info"},
            etag=granted.headers["ETag"],
        )
        assert bound.status == 200, bound.body
        assert (
            await call("DELETE", path + "/abilities/grants/core", etag=granted.headers["ETag"])
        ).status == 412
        audit = await call("GET", path + "/abilities/changes")
        assert audit.status == 200 and any(
            change["source"] == "admin" for change in audit.body["changes"]
        )
        deleted = await call("DELETE", path, etag='"rev-2"')
        assert deleted.status == 204, deleted.body
        assert (await call("GET", path)).status == 404
        assert (await call("GET", "/v1/agents")).body["items"] == []
        assert (await call("POST", "/v1/agents", request, key="create")).status == 409
    finally:
        await runtime.stop()
    replacement = CoreRuntime(config, llm_factory=lambda _: EchoModel())
    await replacement.init()
    try:
        result = await replacement.dispatch("GET", "/v1/agents", principal=principal)
        assert result.body["items"] == []
    finally:
        await replacement.stop()
