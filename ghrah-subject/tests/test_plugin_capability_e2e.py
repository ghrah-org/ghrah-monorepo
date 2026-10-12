# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""插件能力装配端到端验收：整链证据。

与 ``test_ability_bridge.py`` 的分工：彼处为桥接 unit 的单元级测试
（fake registry / fake handle），本文件为**整链**证据——全真实组件
（装配链 / CoreUnit / command_proxy 物化器 / HITLPromiseRegistry /
serial 路由 / chain sqlite），仅 LLM 与插件 unit 为脚本化假件：

    mount_enabled_plugins(P1/P2)
      → AbilityBridgeUnit.reconcile → register_ability dispatch
      → 真实 CoreUnit 物化 command_proxy → Agent 工具面（ScriptedLLM 收到）
      → room_send → agent loop → ToolCallBlock
      → command_proxy.execute → SubjectCommandBridge → ctx.serial
      → 插件 unit handle_command → 回执 → ActionResult → tool_result
      → 最终回复落链（chain_history 可读）

覆盖六条：场景 A 整链、默认 HITL 闸门 promise 往返、
场景 B 双插件自定义工具名、不半装配、崩溃撤销、tool_call 入链审计。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ghrah.chat.content import TextBlock, ToolCallBlock
from ghrah.chat.format import LLMResponse
from ghrah.plugin.assembly import PluginAssembly
from ghrah.plugin.loader import DiscoveredPlugin
from ghrah.plugin.spec import PluginSpec
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import PluginTrustConfig, SubjectConfig
from ghrah.subject.manifest_store.store import ManifestStore
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.runtime.plugin_mount import mount_enabled_plugins
from ghrah.subject.runtime.plugin_session import PLUGIN_SESSION_SERVICE
from ghrah.subject.runtime.route_registry import UnitRouteRegistry
from ghrah.subject.runtime.service_keys import ABILITY_BRIDGE
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit

CODER_MANIFEST_YAML = """\
manifest: agent
version: "1"
metadata:
  namespace: e2e
  name: coder
  description: capability e2e coder
model:
  agent_config_name: default
system_prompt: You are a coder.
max_iterations: 3
abilities:
  - type: conversation
  - type: end_task
"""


# ----------------------------------------------------------------
# 假件（仅两处：LLM 与插件 unit；其余全部真实组件）
# ----------------------------------------------------------------


class ScriptedLLM:
    """脚本化假 LLM：回复队列支持纯文本与 ToolCallBlock 响应。"""

    def __init__(self, replies: list[str | LLMResponse]) -> None:
        self._replies = list(replies)
        self.tools: list[dict[str, Any]] = []
        self.calls: list[list[Any]] = []

    @property
    def model(self) -> str:
        return "scripted-model"

    async def generate(self, messages: list[Any], tools: list[dict[str, Any]] | None = None) -> Any:
        self.calls.append(list(messages))
        if not self._replies:
            raise AssertionError("ScriptedLLM exhausted: no preset reply left")
        reply = self._replies.pop(0)
        if isinstance(reply, LLMResponse):
            return reply
        return LLMResponse(content_blocks=[TextBlock(text=reply)])

    def configure_tools(self, tools: list[dict[str, Any]]) -> None:
        self.tools = list(tools)

    def apply_model_overrides(self, overrides: Any) -> None:
        return None


class _CommandPluginUnit(SubjectUnit):
    """命令插件 fake：记录载荷，``crash_after`` 次调用后崩溃（崩溃撤销用例）。"""

    def __init__(self, plugin_id: str, command: str, *, crash_after: int | None = None) -> None:
        self._meta = UnitMeta(name=plugin_id, routes=RouteSpec(commands=frozenset({command})))
        self._crash_after = crash_after
        self.received: list[dict[str, Any]] = []

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: CommandContext
    ) -> dict[str, Any]:
        self.received.append(dict(payload))
        if self._crash_after is not None and len(self.received) >= self._crash_after:
            raise RuntimeError("plugin exploded on command")
        return {"success": True, "data": {"plugin": self._meta.name}}


class _BridgeService:
    """场景 B 能力包装服务 fake（AbilityBridgeService 契约）。"""

    def __init__(self, caps: dict[str, dict[str, Any]]) -> None:
        self._caps = {k: dict(v) for k, v in caps.items()}

    def capabilities(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._caps.items()}

    async def register(self, capability: str, descriptor: dict[str, Any]) -> None:
        self._caps[capability] = dict(descriptor)

    async def remove(self, capability: str) -> None:
        self._caps.pop(capability, None)


class _BridgeProviderUnit(SubjectUnit):
    """包装插件 fake：init 时 provide ABILITY_BRIDGE 服务。"""

    def __init__(self, name: str, service: Any) -> None:
        self._meta = UnitMeta(name=name)
        self._service = service

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def init(self, ctx: Any) -> None:
        ctx.provide(ABILITY_BRIDGE.name, self._service)


# ----------------------------------------------------------------
# 栈与通用 harness
# ----------------------------------------------------------------


def _config(tmp_path: Path, *, auto_approve: list[str], trust: list[str]) -> SubjectConfig:
    config = SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
    )
    # HITL 等待上限压秒级（默认 300s 会拖垮拒绝/超时类断言）
    config.core.command_timeout = 10.0
    config.hitl_policy.auto_approve_abilities = list(auto_approve)
    config._plugin_trust = PluginTrustConfig(discoverable=list(trust))  # noqa: SLF001
    return config


def _write_coder_manifest(config: SubjectConfig) -> None:
    store = ManifestStore(config.manifest_root)
    store.ensure_dirs()
    store.put_agent("e2e.coder", CODER_MANIFEST_YAML, overwrite=True)


@asynccontextmanager
async def _stack(tmp_path: Path, llm: ScriptedLLM, config: SubjectConfig) -> AsyncIterator[Context]:
    _write_coder_manifest(config)
    async with Context() as ctx:
        registry_unit = CoreClusterRegistryUnit(config, llm_factory=lambda _cfg: llm)
        await assemble_subject(ctx, config, profile="full", core_cluster_unit=registry_unit)
        yield ctx


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result["success"], result
    return result["data"]


async def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout}s")


async def _wait_for_predicate(apredicate: Callable[[], Any], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await apredicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout}s")


def _tool_names(llm: ScriptedLLM) -> set[str]:
    return {
        t["function"]["name"]
        for t in llm.tools
        if isinstance(t, dict) and isinstance(t.get("function"), dict)
    }


def _plugin(plugin_id: str, *, provides: dict[str, Any], unit: SubjectUnit) -> DiscoveredPlugin:
    spec = PluginSpec(plugin_id=plugin_id, version="0.1.0", provides=provides)
    return DiscoveredPlugin(
        spec=spec,
        entry_point_name=plugin_id,
        dist_name=None,
        unit_factory=lambda: unit,
    )


async def _mount_plugins(
    ctx: Context, config: SubjectConfig, project_id: str, plugins: list[DiscoveredPlugin]
) -> list[str]:
    """经真实挂载链（信任闸 → mount_enabled_plugins）装配 fake 插件。"""
    session = ctx.get(PLUGIN_SESSION_SERVICE.name)
    report = await mount_enabled_plugins(
        ctx,
        config,
        discovered=plugins,
        assemblies=[(project_id, PluginAssembly(enabled=[p.spec.plugin_id for p in plugins]))],
        route_registry=UnitRouteRegistry(),
        fibers={},
        state=session.state,
        session=session,
    )
    return [outcome.status for outcome in report.outcomes]


async def _spawn_worker(ctx: Context, project_id: str) -> dict[str, Any]:
    return _data(
        await bridge_command(
            ctx,
            "spawn_agent",
            {
                "project_id": project_id,
                "config": {"name": "worker"},
                "manifest_ref": "e2e.coder",
            },
        )
    )


async def _setup_room(ctx: Context, project_id: str, agent_name: str) -> dict[str, Any]:
    room = _data(
        await bridge_command(ctx, "room_create", {"project_id": project_id, "name": "dev"})
    )["room"]
    await bridge_command(
        ctx,
        "room_join",
        {"room_id": room["room_id"], "subject": agent_name, "subject_type": "agent"},
    )
    return room


async def _send(ctx: Context, room: dict[str, Any], agent_name: str, message: str) -> None:
    _data(
        await bridge_command(
            ctx,
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": message, "targets": [agent_name]},
            },
        )
    )


async def _agent_logged(ctx: Context, room: dict[str, Any], message: str) -> bool:
    logged = _data(await bridge_command(ctx, "room_get_log", {"room_id": room["room_id"]}))
    return any(
        e["author_type"] == "agent" and e["data"].get("message") == message
        for e in logged["entries"]
    )


async def _new_project(ctx: Context) -> str:
    project = _data(await bridge_command(ctx, "project_create", {"name": "cap-e2e"}))["project"]
    return str(project["project_id"])


# ----------------------------------------------------------------
# 用例 1：场景 A 整链（运行期注册 → 已初始化 LLM 工具面刷新）
# ----------------------------------------------------------------


async def test_scenario_a_full_chain_runtime_registration(tmp_path: Path) -> None:
    """场景 A 整链：spawn → 首建 LLM → mount → 运行期注册刷新（D0）→
    tool_call → serial 命中 P1 → 回执 → 终轮回复落 RoomLog。"""
    llm = ScriptedLLM(
        replies=[
            "任务已接收",
            LLMResponse(
                content_blocks=[
                    ToolCallBlock(id="call-run", name="run", arguments={"payload": {"x": 1}})
                ]
            ),
            "命令已完成",
        ]
    )
    config = _config(tmp_path, auto_approve=["run"], trust=["p1"])
    async with _stack(tmp_path, llm, config) as ctx:
        project_id = await _new_project(ctx)
        await _spawn_worker(ctx, project_id)
        room = await _setup_room(ctx, project_id, "worker")

        # 1. 首轮对话：LLM 首建（此时工具面无插件工具）
        await _send(ctx, room, "worker", "准备")
        await _wait_for(lambda: len(llm.calls) >= 1)
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "任务已接收"))
        assert "run" not in _tool_names(llm)

        # 2. 挂载 P1（命令 + 能力声明）→ 挂载尾部钩子重算 → 运行期注册
        p1 = _CommandPluginUnit("p1", "run")
        statuses = await _mount_plugins(
            ctx,
            config,
            project_id,
            [
                _plugin(
                    "p1",
                    provides={"commands": ["run"], "capabilities": ["p1:run"]},
                    unit=p1,
                )
            ],
        )
        assert statuses == ["mounted"]
        # D0 证据：LLM 已初始化，运行期注册立即刷新工具面
        assert "run" in _tool_names(llm)

        # 3. 第二轮：tool_call → command_proxy → serial command/run → P1
        await _send(ctx, room, "worker", "执行 run")
        await _wait_for(lambda: len(llm.calls) >= 3)
        assert p1.received == [{"payload": {"x": 1}}]

        # 4. 终轮回复经 chain_filter 落 RoomLog
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "命令已完成"))


# ----------------------------------------------------------------
# 用例 2：默认 HITL 闸门 promise 往返（先挂载后 spawn，事件装配路径）
# ----------------------------------------------------------------


async def test_hitl_gate_promise_round_trip(tmp_path: Path) -> None:
    """默认闸门：mount 先于 spawn → ``agent_spawned`` 事件装配 → tool_call
    触发 ``core:hitl_request`` → ``hitl_response`` 批准后命令执行入账。"""
    llm = ScriptedLLM(
        replies=[
            "准备就绪",
            LLMResponse(
                content_blocks=[
                    ToolCallBlock(id="call-hitl", name="run", arguments={"payload": {"k": "v"}})
                ]
            ),
            "审批后完成",
        ]
    )
    config = _config(tmp_path, auto_approve=[], trust=["p1"])
    async with _stack(tmp_path, llm, config) as ctx:
        project_id = await _new_project(ctx)

        # 先挂载（此刻无 cluster，重算为 no-op）→ spawn 经事件路径装配
        p1 = _CommandPluginUnit("p1", "run")
        statuses = await _mount_plugins(
            ctx,
            config,
            project_id,
            [
                _plugin(
                    "p1",
                    provides={"commands": ["run"], "capabilities": ["p1:run"]},
                    unit=p1,
                )
            ],
        )
        assert statuses == ["mounted"]
        await _spawn_worker(ctx, project_id)
        room = await _setup_room(ctx, project_id, "worker")

        # 首轮文本：LLM 首建；装配经 agent_spawned 事件完成（工具面含 run）
        await _send(ctx, room, "worker", "准备")
        await _wait_for(lambda: len(llm.calls) >= 1)
        await _wait_for(lambda: "run" in _tool_names(llm))
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "准备就绪"))

        hitl_events: list[dict[str, Any]] = []
        ctx.on("core:hitl_request", lambda payload: hitl_events.append(dict(payload)))

        # 第二轮：tool_call → PRE_EXECUTE HITL hook → promise 挂起
        await _send(ctx, room, "worker", "执行 run")
        await _wait_for(lambda: len(llm.calls) >= 2)
        await _wait_for(lambda: bool(hitl_events))
        event = hitl_events[0]
        assert event["ability_name"] == "run"
        assert event["promise_id"]

        # 批准 → executor future resolve → 命令执行 → 终轮回复
        approved = await bridge_command(
            ctx,
            "hitl_response",
            {"promise_id": event["promise_id"], "approved": True},
        )
        assert approved["success"], approved
        await _wait_for(lambda: len(llm.calls) >= 3)
        assert p1.received == [{"payload": {"k": "v"}}]
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "审批后完成"))


# ----------------------------------------------------------------
# 用例 3：场景 B 双插件（包装服务优先 + 自定义工具名）
# ----------------------------------------------------------------


async def test_scenario_b_wrapper_custom_tool_name(tmp_path: Path) -> None:
    """场景 B：P1 只给命令，P2 经 ABILITY_BRIDGE 补全包装——工具名为 P2
    descriptor 声明（fetch_report），调用仍命中 P1 的 run 命令。"""
    llm = ScriptedLLM(
        replies=[
            "任务已接收",
            LLMResponse(
                content_blocks=[
                    ToolCallBlock(
                        id="call-wrap", name="fetch_report", arguments={"payload": {"q": 1}}
                    )
                ]
            ),
            "包装调用完成",
        ]
    )
    config = _config(tmp_path, auto_approve=["fetch_report"], trust=["p1", "p2"])
    async with _stack(tmp_path, llm, config) as ctx:
        project_id = await _new_project(ctx)
        await _spawn_worker(ctx, project_id)
        room = await _setup_room(ctx, project_id, "worker")
        await _send(ctx, room, "worker", "准备")
        await _wait_for(lambda: len(llm.calls) >= 1)
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "任务已接收"))

        p1 = _CommandPluginUnit("p1", "run")
        service = _BridgeService(
            {
                "p2:wrap": {
                    "tool_name": "fetch_report",
                    "description": "wrapped run",
                    "parameters": {"type": "object", "properties": {}},
                    "command": "run",
                    "require_hitl": False,
                    "timeout": None,
                }
            }
        )
        p2 = _BridgeProviderUnit("p2", service)
        statuses = await _mount_plugins(
            ctx,
            config,
            project_id,
            [
                _plugin("p1", provides={"commands": ["run"]}, unit=p1),
                _plugin("p2", provides={"capabilities": ["p2:wrap"]}, unit=p2),
            ],
        )
        assert statuses == ["mounted", "mounted"]
        # 服务映射优先：自定义工具名装配，不落场景 A 默认（无 run 工具）
        assert "fetch_report" in _tool_names(llm)
        assert "run" not in _tool_names(llm)

        await _send(ctx, room, "worker", "执行 fetch_report")
        await _wait_for(lambda: len(llm.calls) >= 3)
        assert p1.received == [{"payload": {"q": 1}}]
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "包装调用完成"))


# ----------------------------------------------------------------
# 用例 4：不半装配（闭环校验 e2e）
# ----------------------------------------------------------------


async def test_wrapper_without_command_plugin_not_assembled(tmp_path: Path) -> None:
    """P2 包装在场、P1 命令缺席 → 目标命令不在场 → 不装配（不半装配）。"""
    llm = ScriptedLLM(replies=["任务已接收"])
    config = _config(tmp_path, auto_approve=[], trust=["p2"])
    async with _stack(tmp_path, llm, config) as ctx:
        project_id = await _new_project(ctx)
        await _spawn_worker(ctx, project_id)
        room = await _setup_room(ctx, project_id, "worker")
        await _send(ctx, room, "worker", "准备")
        await _wait_for(lambda: len(llm.calls) >= 1)
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "任务已接收"))

        service = _BridgeService({"p2:wrap": {"tool_name": "fetch_report", "command": "run"}})
        p2 = _BridgeProviderUnit("p2", service)
        statuses = await _mount_plugins(
            ctx,
            config,
            project_id,
            [_plugin("p2", provides={"capabilities": ["p2:wrap"]}, unit=p2)],
        )
        assert statuses == ["mounted"]
        assert "fetch_report" not in _tool_names(llm)


# ----------------------------------------------------------------
# 用例 5：崩溃撤销（crash handler 通知重算 → unregister → 工具面刷新）
# ----------------------------------------------------------------


async def test_plugin_crash_revokes_assembled_tool(tmp_path: Path) -> None:
    """P1 崩溃 → crash handler 卸载并通知重算 → 存量 agent 工具面移除 run。"""
    llm = ScriptedLLM(replies=["任务已接收"])
    config = _config(tmp_path, auto_approve=["run"], trust=["p1"])
    async with _stack(tmp_path, llm, config) as ctx:
        project_id = await _new_project(ctx)
        await _spawn_worker(ctx, project_id)
        room = await _setup_room(ctx, project_id, "worker")
        await _send(ctx, room, "worker", "准备")
        await _wait_for(lambda: len(llm.calls) >= 1)
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "任务已接收"))

        p1 = _CommandPluginUnit("p1", "run", crash_after=1)
        statuses = await _mount_plugins(
            ctx,
            config,
            project_id,
            [
                _plugin(
                    "p1",
                    provides={"commands": ["run"], "capabilities": ["p1:run"]},
                    unit=p1,
                )
            ],
        )
        assert statuses == ["mounted"]
        assert "run" in _tool_names(llm)

        # 命令触发崩溃：回执失败 + 插件卸载 + 桥接重算撤销
        result = await bridge_command(ctx, "run", {"payload": {}})
        assert result["success"] is False
        assert "plugin crashed: p1" in result["error"]
        await _wait_for(lambda: "run" not in _tool_names(llm))


# ----------------------------------------------------------------
# 用例 6：tool_call 入链审计（chain_history 读侧可见）
# ----------------------------------------------------------------


async def test_tool_call_audited_in_chain(tmp_path: Path) -> None:
    """场景 A 闭环后，tool_call 的 ActionChain 节点在 chain_history 可读。"""
    llm = ScriptedLLM(
        replies=[
            "任务已接收",
            LLMResponse(
                content_blocks=[
                    ToolCallBlock(id="call-audit", name="run", arguments={"payload": {"a": 2}})
                ]
            ),
            "审计样本完成",
        ]
    )
    config = _config(tmp_path, auto_approve=["run"], trust=["p1"])
    async with _stack(tmp_path, llm, config) as ctx:
        project_id = await _new_project(ctx)
        spawn = await _spawn_worker(ctx, project_id)
        room = await _setup_room(ctx, project_id, "worker")
        await _send(ctx, room, "worker", "准备")
        await _wait_for(lambda: len(llm.calls) >= 1)
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "任务已接收"))

        p1 = _CommandPluginUnit("p1", "run")
        statuses = await _mount_plugins(
            ctx,
            config,
            project_id,
            [
                _plugin(
                    "p1",
                    provides={"commands": ["run"], "capabilities": ["p1:run"]},
                    unit=p1,
                )
            ],
        )
        assert statuses == ["mounted"]
        await _send(ctx, room, "worker", "执行 run")
        await _wait_for(lambda: len(llm.calls) >= 3)
        await _wait_for_predicate(lambda: _agent_logged(ctx, room, "审计样本完成"))
        assert p1.received == [{"payload": {"a": 2}}]

        # 读侧：session/branch 定位 → chain_history 含 run 工具调用节点
        session = _data(
            await bridge_command(
                ctx,
                "session_list",
                {
                    "project_id": project_id,
                    "agent_id": spawn["agent_id"],
                    "agent_name": "worker",
                },
            )
        )["sessions"][0]
        branch = _data(
            await bridge_command(
                ctx,
                "branch_list",
                {
                    "project_id": project_id,
                    "agent_id": spawn["agent_id"],
                    "agent_name": "worker",
                    "session_id": session["session_id"],
                },
            )
        )["branches"][0]
        history = _data(
            await bridge_command(
                ctx,
                "get_chain_history",
                {
                    "project_id": project_id,
                    "agent_id": spawn["agent_id"],
                    "agent_name": "worker",
                    "session_id": session["session_id"],
                    "branch_id": branch["branch_id"],
                },
            )
        )
        nodes = history["nodes"]
        assert len(nodes) >= 2
        assert any("run" in json.dumps(node) for node in nodes)
