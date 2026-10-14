# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""功能级全链路测试：装配态 Subject + 注入 ScriptedLLM 的完整回路。

CoreUnitConfig.llm_factory 注入落地后，此前被"真实 LLM 不参与"截断的
断言面终于可测。本文件覆盖一条完整业务链：

    ManifestStore 写入 agent manifest
    → project_create / room_create
    → spawn_agent(manifest_ref)（CoreUnit 内解析 + 能力物化）
    → room_send(human) 投递 → agent loop 经 ScriptedLLM 推理
    → 预设回复落 ActionChain（chain_history 读侧可见）
    → 回复经 room chain_filter 落 RoomLog（author_type=agent）
    → 持久化跨重启（同 db 重装后链与日志仍可读）

无 unittest.mock：LLM 为脚本化假实现，其余全部真实组件。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import RecoveryConfig, SubjectConfig
from ghrah.subject.manifest_store.store import ManifestStore
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit

CODER_MANIFEST_YAML = """\
manifest: agent
version: "1"
metadata:
  namespace: e2e
  name: coder
  description: scripted coder
model:
  agent_config_name: default
system_prompt: You are a coder.
max_iterations: 3
abilities:
  - type: conversation
  - type: end_task
"""

PRESET_REPLY = "预设回复：任务已接收"


class ScriptedLLM:
    """脚本化假 LLM（与 ghrah-core tests 同形状；tests/ 不跨包发布，本地自持）。"""

    def __init__(self, replies: list[str], model: str = "scripted-model") -> None:
        self._replies = list(replies)
        self._model = model
        self.tools: list[dict[str, Any]] = []
        self.calls: list[list[Any]] = []

    @property
    def model(self) -> str:
        return self._model

    async def generate(self, messages: list[Any], tools: list[dict[str, Any]] | None = None) -> Any:
        from ghrah.chat.content import TextBlock
        from ghrah.chat.format import LLMResponse

        self.calls.append(list(messages))
        if not self._replies:
            raise AssertionError("ScriptedLLM exhausted: no preset reply left")
        return LLMResponse(content_blocks=[TextBlock(text=self._replies.pop(0))])

    def configure_tools(self, tools: list[dict[str, Any]]) -> None:
        self.tools = list(tools)

    def apply_model_overrides(self, overrides: Any) -> None:
        return None


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


def _write_coder_manifest(config: SubjectConfig) -> None:
    store = ManifestStore(config.manifest_root)
    store.ensure_dirs()
    store.put_agent("e2e.coder", CODER_MANIFEST_YAML, overwrite=True)


@asynccontextmanager
async def _stack(tmp_path: Path, llm: ScriptedLLM) -> AsyncIterator[Context]:
    """装配全量 Subject，CoreClusterRegistryUnit 注入 llm_factory。"""
    config = _config(tmp_path)
    _write_coder_manifest(config)

    async with Context() as ctx:
        registry_unit = CoreClusterRegistryUnit(config, llm_factory=lambda _cfg: llm)
        await assemble_subject(
            ctx,
            config,
            profile="full",
            core_cluster_unit=registry_unit,
        )
        yield ctx


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result["success"], result
    return result["data"]


async def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await _sleep()
    raise AssertionError(f"condition not met within {timeout}s")


async def _wait_for_predicate(apredicate: Callable[[], Any], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await apredicate():
            return
        await _sleep()
    raise AssertionError(f"condition not met within {timeout}s")


async def _sleep(interval: float = 0.05) -> None:
    import asyncio

    await asyncio.sleep(interval)


async def test_full_chain_manifest_spawn_scripted_reply_persisted(tmp_path: Path) -> None:
    """manifest spawn → ScriptedLLM 回复 → 落链 → 落 RoomLog → 跨重启可读。"""
    llm = ScriptedLLM(replies=[PRESET_REPLY])
    async with _stack(tmp_path, llm) as ctx:
        project = _data(await bridge_command(ctx, "project_create", {"name": "full-chain"}))[
            "project"
        ]
        project_id = project["project_id"]

        room = _data(
            await bridge_command(ctx, "room_create", {"project_id": project_id, "name": "dev"})
        )["room"]

        # manifest_ref spawn：CoreUnit 内解析 e2e.coder → conversation/end_task
        spawn = _data(
            await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": project_id,
                    "config": {"name": "coder"},
                    "manifest_ref": "e2e.coder",
                },
            )
        )
        assert spawn["name"] == "coder"

        await bridge_command(
            ctx,
            "room_join",
            {"room_id": room["room_id"], "subject": "coder", "subject_type": "agent"},
        )

        # human 消息 → fire-and-forget 投递 → agent loop → ScriptedLLM 推理
        send = _data(
            await bridge_command(
                ctx,
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"message": "开始任务", "targets": ["coder"]},
                },
            )
        )
        assert send["entry"]["seq"] == 1

        # 等 agent loop 跑完（LLM 被实际调用）
        await _wait_for(lambda: len(llm.calls) >= 1)

        # 预设回复经 chain_filter 落 RoomLog（human + agent 两条；chain_filter
        # 经 core:action_chain_updated 事件异步驱动，轮询至 agent 条目出现）
        async def _reply_logged() -> bool:
            logged = _data(await bridge_command(ctx, "room_get_log", {"room_id": room["room_id"]}))
            return any(e["author_type"] == "agent" for e in logged["entries"])

        await _wait_for_predicate(_reply_logged)

        log = _data(await bridge_command(ctx, "room_get_log", {"room_id": room["room_id"]}))
        assert log["count"] == 2
        agent_entries = [e for e in log["entries"] if e["author_type"] == "agent"]
        assert len(agent_entries) == 1
        assert agent_entries[0]["data"]["message"] == PRESET_REPLY
        assert agent_entries[0]["data"].get("via") == "message_manager"

    # 跨重启：同 db 重装后 RoomLog 仍可读（human + agent 回复双条）
    async with _stack(tmp_path, ScriptedLLM(replies=[])) as ctx2:
        listed = _data(await bridge_command(ctx2, "room_list", {}))
        assert any(r["room_id"] == room["room_id"] for r in listed["rooms"])
        log = _data(await bridge_command(ctx2, "room_get_log", {"room_id": room["room_id"]}))
        assert log["count"] == 2
        messages = [e["data"].get("message") for e in log["entries"]]
        assert "开始任务" in messages
        assert PRESET_REPLY in messages


async def test_spawn_with_direct_abilities_uses_injected_llm(tmp_path: Path) -> None:
    """直传 abilities 路径同样消费注入 LLM（receive 回复断言）。"""
    llm = ScriptedLLM(replies=["收到，马上处理"])
    async with _stack(tmp_path, llm) as ctx:
        project = _data(await bridge_command(ctx, "project_create", {"name": "direct"}))["project"]
        spawn = _data(
            await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": project["project_id"],
                    "config": {"name": "worker", "system_prompt": "t", "max_iterations": 1},
                    "abilities": [{"ability_type": "conversation", "params": {}}],
                },
            )
        )
        assert spawn["name"] == "worker"

        # 经 supervisor 驱动一次 receive → ScriptedLLM 被调用
        registry = ctx.get("core_cluster_registry")
        supervisor = registry.get_handle(project["cluster_ids"][0])._unit.supervisor
        actor = supervisor._registry.get_info("worker").actor_handle

        from ghrah.core.message import AgentMessage, MessageType

        await actor.receive(
            AgentMessage(sender="user", recipient="worker", content="ping", type=MessageType.CHAT)
        )
        assert len(llm.calls) == 1
