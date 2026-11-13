"""显式运行真实模型验收：Subject 经 REST 风格契约在进程内调用 Core。"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from ghrah.core.runtime import CoreRuntime, RuntimeConfig
from ghrah.core.unit import CoreUnitConfig
from ghrah.subject.manifest_store.store import ManifestStore
from ghrah.subject.runtime_connection import (
    RuntimeClient,
    RuntimeConnection,
    RuntimeConnectionConfig,
)


async def _command(client: RuntimeClient, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.request(
        "POST", "/v1/commands", body={"command": command, "payload": payload}
    )
    if response.status != 200 or not response.body or not response.body.get("success"):
        # 不输出提供商错误正文；其中可能包含端点连接信息。
        raise RuntimeError(f"{command} returned an unsuccessful receipt")
    return response.body["data"]


async def verify(endpoint: str, *, timeout: float) -> dict[str, Any]:
    """使用显式端点和临时数据库验证进程内业务闭环，不注入测试 LLM。"""
    from agentconf import AgentsConfig

    with AgentsConfig(read_only=True) as config:
        resolved = config.resolve_model_endpoint(endpoint)
        model = resolved.model.model_name
        api_format = resolved.connection.api_format
    scope = "runtime-live-model"
    with tempfile.TemporaryDirectory(prefix="ghrah-runtime-live-") as temporary:
        templates = ManifestStore(Path(temporary) / "templates")
        templates.put_agent(
            "runtime.probe",
            yaml.safe_dump(
                {
                    "manifest": "agent",
                    "version": "2",
                    "metadata": {"namespace": "runtime", "name": "probe"},
                    "model": {"endpoint_name": endpoint, "max_tokens": 512},
                    "system_prompt": "Return exactly CORE_RUNTIME_OK. Do not use tools.",
                    "max_iterations": 2,
                    "communication_timeout": timeout,
                    "abilities": [{"type": "conversation"}, {"ref": "ghrah.core.session_info"}],
                }
            ),
        )
        agent_id = uuid4().hex
        runtime = CoreRuntime(
            RuntimeConfig(
                scope_ref=scope,
                database_path=str(Path(temporary) / "core.db"),
                enabled_commands=("spawn_agent", "list_agents", "send_message", "terminate_agent"),
            ),
            unit_config=CoreUnitConfig(manifest_store=templates),
        )
        connection = RuntimeConnection(
            RuntimeConnectionConfig(enabled=True, scope_ref=scope), runtime
        )
        try:
            await connection.start()
            client = connection.client
            if client is None or not (await client.status()).ready:
                raise RuntimeError("Runtime did not become ready")
            async with asyncio.timeout(timeout):
                assert (await _command(client, "list_agents", {"project_id": scope}))[
                    "agents"
                ] == []
                spawned = await _command(
                    client,
                    "spawn_agent",
                    {
                        "project_id": scope,
                        "config": {
                            "name": "live-worker",
                            "agent_id": agent_id,
                            "endpoint_name": endpoint,
                            "system_prompt": "Return exactly CORE_RUNTIME_OK. Do not use tools.",
                            "max_iterations": 2,
                            "communication_timeout": timeout,
                            "model_overrides": {"max_tokens": 512},
                        },
                        "manifest_ref": "runtime.probe",
                    },
                )
                assert spawned["recovery_mode"] == "initialized"
                assert (
                    len((await _command(client, "list_agents", {"project_id": scope}))["agents"])
                    == 1
                )
                started = time.monotonic()
                reply = await _command(
                    client,
                    "send_message",
                    {
                        "project_id": scope,
                        "agent_id": agent_id,
                        "target": "live-worker",
                        "content": "Reply with CORE_RUNTIME_OK.",
                        "timeout": timeout,
                    },
                )
                elapsed = round(time.monotonic() - started, 3)
                if "CORE_RUNTIME_OK" not in str(reply.get("content", "")):
                    raise RuntimeError("Real model response did not contain the probe marker")
                terminated = await _command(
                    client,
                    "terminate_agent",
                    {"project_id": scope, "agent_id": agent_id, "name": "live-worker"},
                )
                assert terminated["terminated"]
                assert (await _command(client, "list_agents", {"project_id": scope}))[
                    "agents"
                ] == []
                assert (await client.status()).ready
        finally:
            await connection.stop()
        assert (await client.status()).phase == "stopped"
        return {
            "python": platform.python_version(),
            "platform": platform.system() + "/" + platform.machine(),
            "endpoint": endpoint,
            "model": model,
            "api_format": api_format,
            "model_factory": "default",
            "transport": "in_process",
            "request_contract": "method/path/headers/body",
            "reply_marker": "CORE_RUNTIME_OK",
            "reply_seconds": elapsed,
            "agent_terminated": True,
            "runtime_stopped_cleanly": True,
        }


async def verify_resources(endpoint: str, *, timeout: float) -> dict[str, Any]:
    """真实模型经资源 API 执行，并通过公共 Node 详情核对实际回复。"""
    from agentconf import AgentsConfig

    with AgentsConfig(read_only=True) as config:
        resolved = config.resolve_model_endpoint(endpoint)
        model = resolved.model.model_name
        api_format = resolved.connection.api_format
    with tempfile.TemporaryDirectory(prefix="ghrah-runtime-resource-live-") as temporary:
        database = str(Path(temporary) / "core.db")
        connection = RuntimeConnection(
            RuntimeConnectionConfig(
                enabled=True,
                scope_ref="runtime-resource-live",
                database_path=database,
                enabled_agent_api=True,
            )
        )
        try:
            await connection.start()
            client = connection.client
            if client is None or not (await client.status()).ready:
                raise RuntimeError("Runtime did not become ready")
            async with asyncio.timeout(timeout):
                fixture = (
                    Path(__file__).resolve().parents[1]
                    / "ghrah-protocol/contracts/runtime/fixtures/agent.create.json"
                )
                body = json.loads(fixture.read_text())
                body["config"]["model"]["endpoint_name"] = endpoint
                body["config"]["prompt"]["system_prompt"] = (
                    "Return exactly CORE_RUNTIME_OK. Do not use tools."
                )
                body["config"]["context_policy"]["max_iterations"] = 2
                created = await client.create_agent(body, idempotency_key="live-create")
                if created.status != 201:
                    raise RuntimeError(f"Resource creation failed ({created.status})")
                agent, etag = created.body["agent_id"], created.headers["ETag"]
                assert created.body["state"] == "stopped"
                started = await client.set_agent_state(agent, "running", etag=etag)
                if started.status != 200:
                    raise RuntimeError(f"Resource start failed ({started.status})")
                began = time.monotonic()
                accepted = await client.submit_message(
                    agent, "Reply with CORE_RUNTIME_OK only.", idempotency_key="live-message"
                )
                if accepted.status != 202:
                    raise RuntimeError(f"Message submission failed ({accepted.status})")
                while True:
                    result = await client.get_delivery(
                        accepted.body["delivery_id"], wait=min(30, timeout)
                    )
                    if result.status != 200:
                        raise RuntimeError(f"Delivery query failed ({result.status})")
                    if result.body["state"] in {"completed", "failed", "interrupted", "blocked"}:
                        break
                if result.body["state"] != "completed":
                    raise RuntimeError("Real-model delivery did not complete")
                elapsed = round(time.monotonic() - began, 3)
                node_id = result.body["last_node_id"]
                if node_id is None:
                    raise RuntimeError("Completed delivery did not reference a node")
                node = await client.get_node(node_id)
                if node.status != 200:
                    raise RuntimeError(f"Node query failed ({node.status})")
                replies = [
                    block.get("text", "")
                    for message in node.body["payload"]["messages_delta"] or []
                    if message.get("role") == "ai"
                    for block in message.get("content_blocks", [])
                    if block.get("type") == "text"
                ]
                if not any("CORE_RUNTIME_OK" in text for text in replies):
                    raise RuntimeError("Real model response did not contain the probe marker")
                timeline = await client.get_timeline(agent)
                if timeline.status != 200 or not any(
                    item["node_id"] == node_id for item in timeline.body["items"]
                ):
                    raise RuntimeError("Completed node was not visible in the timeline")
                stopped = await client.set_agent_state(agent, "stopped", etag=etag)
                assert stopped.status == 200 and stopped.body["agent"]["state"] == "stopped"
        finally:
            await connection.stop()
        assert (await client.status()).phase == "stopped"
        return {
            "python": platform.python_version(),
            "platform": platform.system() + "/" + platform.machine(),
            "endpoint": endpoint,
            "model": model,
            "api_format": api_format,
            "model_factory": "default",
            "transport": "in_process",
            "api": "resources",
            "request_contract": "method/path/headers/body",
            "delivery_state": "completed",
            "reply_marker": "CORE_RUNTIME_OK",
            "reply_seconds": elapsed,
            "reply_verification": "resource_node_api",
            "agent_stopped": True,
            "runtime_stopped_cleanly": True,
        }


def main() -> None:
    """必须显式指定已有模型端点；本命令会实际发送一次模型请求。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--api", choices=("commands", "resources"), default="commands")
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    if not 0 < args.timeout <= 240:
        parser.error("timeout must be greater than 0 and at most 240 seconds")
    verifier = verify_resources if args.api == "resources" else verify
    result = asyncio.run(verifier(args.endpoint, timeout=args.timeout))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
