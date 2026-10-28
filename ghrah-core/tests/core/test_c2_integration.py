# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""集成测试：manifest 冻结 + HITL 门 + 集群可见性全链。

覆盖冒烟场景：
1. manifest_ref spawn 父 agent（能力面由 manifest 冻结）
2. spawn_agent ability 经 materialize 通用分支携带 PRE_EXECUTE HITL hook
   （require_hitl: true 真实生效，非纸面门禁）
3. 执行被 HITL 结构性拦截 → 审批放行 → 子 agent 以 manifest 能力面注册
4. query_agents 可见双方；query_manifests 可读 manifest 定义
"""

from __future__ import annotations

import asyncio
from typing import Any

from ghrah.protocol.types import EventType

from ghrah.core.unit import CoreUnitConfig, create_core_unit
from ghrah.manifest.parser import parse_agent_manifest
from ghrah.manifest.protocols import ManifestStoreProtocol
from ghrah.manifest.store import BuiltinManifestStore

_PARENT_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: smoke
  name: orchestrator
  description: orchestrator
model:
  endpoint_name: default
system_prompt: You orchestrate.
max_iterations: 5
abilities:
  - type: conversation
  - type: end_task
  - ref: ghrah.cluster.spawn_agent
  - ref: ghrah.cluster.query_agents
"""

_CHILD_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: smoke
  name: worker
  description: worker
model:
  endpoint_name: default
system_prompt: You work.
max_iterations: 5
abilities:
  - type: conversation
  - type: end_task
"""


class _CompositeStore(ManifestStoreProtocol):
    """builtin abilities + 自定义 agent manifests。"""

    def __init__(self) -> None:
        self._builtin = BuiltinManifestStore()
        self._agents = {
            "smoke.orchestrator": parse_agent_manifest(_PARENT_YAML),
            "smoke.worker": parse_agent_manifest(_CHILD_YAML),
        }

    def load_ability(self, full_name: str) -> Any:
        return self._builtin.load_ability(full_name)

    def load_agent(self, full_name: str) -> Any:
        if full_name in self._agents:
            return self._agents[full_name]
        from ghrah.manifest.errors import ManifestNotFoundError

        raise ManifestNotFoundError(full_name)

    def list_abilities(self, namespace: str | None = None) -> list[str]:
        return self._builtin.list_abilities(namespace)

    def list_agents(self, namespace: str | None = None) -> list[str]:
        return list(self._agents)


class _FakeCtx:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def provide(self, name: str, value: Any) -> None:
        pass

    def emit(self, name: str, payload: dict) -> None:
        self.events.append((name, payload))


async def test_manifest_spawn_hitl_gate_full_chain() -> None:
    """manifest_ref spawn → HITL 结构性拦截 → 审批 → 集群可见。"""
    unit = create_core_unit(
        CoreUnitConfig(
            project_id="default",
            cluster_id="smoke-cluster",
            manifest_store=_CompositeStore(),
        )
    )
    ctx = _FakeCtx()
    await unit.init(ctx)

    # 1. manifest_ref spawn 父 agent（spawn_agent ability 面）
    spawn = await unit.handle_command(
        "spawn_agent",
        {
            "project_id": "default",
            "config": {"name": "orch"},
            "manifest_ref": "smoke.orchestrator",
        },
        None,
    )
    assert spawn["success"], spawn.get("error")

    # 2. spawn_agent ability 携带 PRE_EXECUTE HITL hook（通用 HITL 接线生效）
    handle = await unit.supervisor.get_agent_handle("orch")
    hitl_hooks = [
        h
        for h in handle._abilities["spawn_agent"].get_hooks()
        if h.hook_point.name == "PRE_EXECUTE"
    ]
    assert len(hitl_hooks) == 1

    # 3. 执行被 HITL 拦截 → 审批放行 → worker 以 manifest 能力面注册
    task = asyncio.create_task(
        unit.handle_command(
            "execute_ability",
            {
                "request_id": "req-1",
                "agent_name": "orch",
                "ability_name": "spawn_agent",
                "tool_args": {
                    "call_id": "call-1",
                    "manifest_ref": "smoke.worker",
                    "name": "worker-1",
                },
            },
            None,
        )
    )
    executor = handle._ability_executor
    store = executor.hitl_store
    for _ in range(200):
        if store.list_pending():
            break
        await asyncio.sleep(0.01)
    assert store.list_pending() == [("orch", "spawn_agent", "call-1")]
    assert any(e[0] == f"core:{EventType.HITL_REQUEST.value}" for e in ctx.events)

    resp = await unit.handle_command(
        "hitl_response",
        {
            "agent_name": "orch",
            "ability_name": "spawn_agent",
            "tool_call_id": "call-1",
            "approved": True,
        },
        None,
    )
    assert resp["data"]["resolved"]

    result = await asyncio.wait_for(task, timeout=5.0)
    assert result["data"]["success"], result
    assert result["data"]["result"]["agent_name"] == "worker-1"

    # 4. 子 agent 能力面由 manifest 冻结（conversation/end_task，无 spawn）
    worker = await unit.supervisor.get_agent_handle("worker-1")
    assert set(worker._abilities) == {"conversation", "end_task"}

    # 5. query_agents 可见双方
    listing = await unit.handle_command("list_agents", {"project_id": "default"}, None)
    names = sorted(a["name"] for a in listing["data"]["agents"])
    assert names == ["orch", "worker-1"]

    await unit.stop()
