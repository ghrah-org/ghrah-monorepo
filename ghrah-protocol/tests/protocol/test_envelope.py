# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Envelope（非泛型信封）与反序列化入口测试。

覆盖以下验收点：
- 裸 Envelope() + dict payload 不崩（Claim1 修复）。
- Envelope 持有 BaseModel 子类 payload 时 model_dump() 不丢字段（Claim3）。
- envelope_from_dict 收窄已知 type 为模型实例；未知 type 回退裸 dict。
- expect_payload 边界收窄 + 校验失败抛 ValidationError。
- type/payload mismatch 不被类型系统阻止（设计意图固化）。
- 工厂函数 payload 存模型实例，序列化正确。
"""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel, ValidationError

from ghrah.protocol.types import (
    AGENT_SCOPED_EVENT_TYPES,
    COMMAND_PAYLOAD_MAP,
    CORE_COMMANDS,
    EVENT_PAYLOAD_MAP,
    PAYLOAD_MAP,
    PLUGIN_COMMANDS,
    TASK_ATTRIBUTION_COMMANDS,
    TASK_COMMANDS,
    AbilityResultPayload,
    AgentCompactContextPayload,
    AgentResetPayload,
    AgentResponsePayload,
    AgentSpawnedPayload,
    CommandResultPayload,
    CommandType,
    ContextUsageUpdatedPayload,
    Envelope,
    ErrorPayload,
    EventType,
    ExecuteAbilityPayload,
    HealthStatusPayload,
    HITLResolvedPayload,
    HITLResponsePayload,
    PluginCrashedPayload,
    PluginLifecyclePayload,
    PluginNegotiatedPayload,
    PluginNegotiatePayload,
    SpawnAgentPayload,
    SystemType,
    TaskClaimEventPayload,
    TaskClaimListPayload,
    TaskDumpPayload,
    TaskSubmitCompletionPayload,
    TaskVerifyPayload,
    create_command_result,
    create_error,
    create_event,
    create_ping,
    create_pong,
    envelope_from_dict,
    expect_payload,
    payload_agent_name,
)

# ─── 验收：Envelope 裸构造 / 序列化 ───


class TestEnvelopeBareConstruct:
    def test_bare_construct_with_dict_payload(self):
        """Claim1 修复：裸 Envelope() + dict payload 不崩。"""
        env = Envelope(type="spawn_agent", payload={"name": "a"})
        assert env.type == "spawn_agent"
        assert env.payload == {"name": "a"}

    def test_bare_construct_without_payload(self):
        """无 payload 时默认空 dict。"""
        env = Envelope(type="ping")
        assert env.payload == {}

    def test_basemodel_payload_model_dump_keeps_fields(self):
        """Claim3：持有 BaseModel 子类 payload 时 model_dump() 不丢字段。"""
        sub_payload = AgentSpawnedPayload.model_validate(
            {"name": "x", "config": {"name": "x", "description": "d"}}
        )
        env = Envelope(type="spawn_agent", payload=sub_payload)
        dumped = env.model_dump()
        assert dumped["payload"]["name"] == "x"
        assert dumped["payload"]["config"]["name"] == "x"
        assert dumped["payload"]["config"]["description"] == "d"

    def test_payload_any_accepts_none(self):
        env = Envelope(type="custom", payload=None)
        assert env.payload is None
        assert env.model_dump()["payload"] is None

    def test_model_dump_with_timestamp_fills_timestamp(self):
        env = Envelope(type="ping")
        before = time.time()
        data = env.model_dump_with_timestamp()
        assert data["timestamp"] is not None
        assert data["timestamp"] >= before - 1


# ─── 验收：wire format 字节级一致性 ───


class TestWireFormatCompat:
    def test_known_type_payload_serializes_to_dict(self):
        """已知 type：经 envelope_from_dict 收窄后，model_dump 的 payload 仍是 dict。"""
        env = envelope_from_dict(
            {
                "type": "spawn_agent",
                "payload": {"project_id": "p1", "config": {"name": "x"}},
            }
        )
        dumped = env.model_dump()
        assert isinstance(dumped["payload"], dict)
        assert dumped["payload"]["config"]["name"] == "x"
        assert dumped["type"] == "spawn_agent"

    def test_unknown_type_payload_stays_raw_dict(self):
        """未知 type：payload 保持裸 dict（前向兼容）。"""
        env = envelope_from_dict(
            {"type": "future_unknown_cmd", "payload": {"foo": 1, "bar": [1, 2]}}
        )
        assert isinstance(env.payload, dict)
        assert env.payload == {"foo": 1, "bar": [1, 2]}

    def test_command_result_payload_stays_dict(self):
        """command_result 未登记 MAP，payload 保持裸 dict（welcome 包兼容）。"""
        env = envelope_from_dict(
            {
                "type": "command_result",
                "payload": {"success": True, "data": {"session_id": "s1"}},
                "request_id": "connect",
            }
        )
        assert isinstance(env.payload, dict)
        assert env.payload["success"] is True

    def test_envelope_round_trip_preserves_fields(self):
        original = {
            "type": "agent_response",
            "payload": {"sender": "a", "recipient": "b", "content": "hi"},
            "request_id": "req-1",
            "client_type": "core",
            "seq_id": 42,
        }
        env = envelope_from_dict(original)
        dumped = env.model_dump()
        assert dumped["type"] == original["type"]
        assert dumped["request_id"] == "req-1"
        assert dumped["client_type"] == "core"
        assert dumped["seq_id"] == 42

    def test_task_update_expected_version_roundtrip(self):
        """task_update 命令经 envelope_from_dict 收窄 + model_dump 后保留 expected_version。"""
        env = envelope_from_dict(
            {
                "type": "task_update",
                "payload": {"task_id": "t1", "status": "in_progress", "expected_version": 3},
                "request_id": "req-2",
            }
        )
        dumped = env.model_dump()
        assert isinstance(dumped["payload"], dict)
        assert dumped["payload"]["task_id"] == "t1"
        assert dumped["payload"]["expected_version"] == 3


# ─── 验收：未知 type 兜底 ───


class TestUnknownTypeFallback:
    def test_missing_type_field(self):
        env = envelope_from_dict({"payload": {"x": 1}})
        assert env.type == ""
        assert env.payload == {"x": 1}

    def test_missing_payload_field(self):
        """已知 type 但缺必填字段：envelope_from_dict 校验失败抛 ValidationError。

        这是期望行为——暴露 wire/schema 不符，而非静默回退为空 dict。
        Server 的 _message_loop 用 try/except 捕获并返回 INVALID_MESSAGE。
        """
        with pytest.raises(ValidationError):
            envelope_from_dict({"type": "spawn_agent"})

    def test_ping_pong_unknown_payload(self):
        """ping/pong 不在 MAP，payload 保持裸 dict。"""
        env = envelope_from_dict({"type": "ping", "payload": {}})
        assert env.payload == {}
        assert env.known_type() is False


# ─── 验收：expect_payload ───


class TestExpectPayload:
    def test_narrow_from_model_instance(self):
        """payload 已是目标类型时直接返回。"""
        env = envelope_from_dict(
            {
                "type": "spawn_agent",
                "payload": {"project_id": "p1", "config": {"name": "x"}},
            }
        )
        assert isinstance(env.payload, SpawnAgentPayload)
        payload = expect_payload(env, SpawnAgentPayload)
        assert payload is env.payload

    def test_narrow_from_dict(self):
        """payload 是 dict（未登记 MAP）时 model_validate 收窄。"""
        env = Envelope(
            type="spawn_agent",
            payload={"project_id": "p1", "config": {"name": "x"}},
        )
        payload = expect_payload(env, SpawnAgentPayload)
        assert isinstance(payload, SpawnAgentPayload)
        assert payload.config.name == "x"

    def test_validation_error_on_mismatch(self):
        """payload 与期望类型不符时抛 ValidationError（而非静默 .get()）。"""
        env = Envelope(type="custom", payload={"wrong_field": 1})
        with pytest.raises(ValidationError):
            expect_payload(env, SpawnAgentPayload)

    def test_execute_ability_narrow(self):
        env = envelope_from_dict(
            {
                "type": "execute_ability",
                "payload": {
                    "request_id": "r1",
                    "agent_name": "a",
                    "ability_name": "write_file",
                    "tool_args": {"path": "/tmp/x"},
                },
            }
        )
        assert isinstance(env.payload, ExecuteAbilityPayload)
        payload = expect_payload(env, ExecuteAbilityPayload)
        assert payload.request_id == "r1"
        assert payload.tool_args == {"path": "/tmp/x"}


# ─── 验收：type/payload mismatch 不被类型系统阻止（设计意图固化）───


class TestTypePayloadMismatch:
    """泛型不阻止 mismatch：防护靠 expect_payload 在 handler 边界，不靠类型系统。"""

    def test_envelope_allows_type_payload_mismatch(self):
        """Envelope(type='agent_spawned', payload=SpawnAgentPayload(...)) 能构造。

        类型系统不阻止 type 与 payload 类型不一致——这是有意为之，
        消费侧通过 expect_payload 在 handler 边界断言正确类型。
        """
        env = Envelope(
            type="agent_spawned",
            payload=SpawnAgentPayload.model_validate({"project_id": "p1", "config": {"name": "x"}}),
        )
        assert env.type == "agent_spawned"
        assert isinstance(env.payload, SpawnAgentPayload)

    def test_mismatch_caught_at_handler_boundary(self):
        """mismatch 在 handler 边界被 expect_payload 抓到（校验失败）。"""
        env = Envelope(type="spawn_agent", payload={"unrelated": 1})
        with pytest.raises(ValidationError):
            expect_payload(env, SpawnAgentPayload)


# ─── 验收：Message 别名 ───


class TestMessageAlias:
    def test_message_is_envelope(self):
        from ghrah.protocol.types import Message

        assert Message is Envelope

    def test_message_alias_stable_non_generic(self):
        """别名稳定，无泛型参数。"""
        from ghrah.protocol.types import Message

        # 非泛型：参数化应抛 TypeError（Pydantic v2 非泛型模型不支持 []）
        with pytest.raises(TypeError):
            Message[SpawnAgentPayload]  # type: ignore[index]


# ─── 验收：工厂函数 payload 存模型实例 ───


class TestFactoryFunctions:
    def test_create_command_result_payload_is_model(self):
        msg = create_command_result(request_id="r1", success=True, data={"k": "v"})
        assert isinstance(msg.payload, CommandResultPayload)
        assert msg.payload.success is True
        assert msg.payload.data == {"k": "v"}
        assert msg.request_id == "r1"
        # 序列化为 dict（wire 格式）
        dumped = msg.model_dump()
        assert dumped["payload"]["success"] is True

    def test_create_command_result_with_error(self):
        msg = create_command_result(
            request_id="r1",
            success=False,
            error="agent_not_found",
            error_detail="Agent a1 was not found",
        )
        assert msg.payload.success is False
        assert msg.payload.error == "agent_not_found"
        assert msg.payload.error_detail == "Agent a1 was not found"

    def test_create_event_payload_is_model(self):
        payload = AgentResponsePayload(sender="a", recipient="b", content="hi")
        msg = create_event(EventType.AGENT_RESPONSE, payload)
        assert msg.payload is payload
        assert msg.type == "agent_response"

    def test_create_error_payload_is_model(self):
        msg = create_error("E001", "msg", request_id="r1")
        assert isinstance(msg.payload, ErrorPayload)
        assert msg.payload.code == "E001"
        assert msg.type == "error"

    def test_create_ping_pong(self):
        ping = create_ping()
        pong = create_pong()
        assert ping.type == SystemType.PING.value
        assert pong.type == SystemType.PONG.value
        assert ping.payload == {}


# ─── 验收：PAYLOAD_MAP 注册表 ───


class TestPayloadMap:
    def test_command_map_covers_core_commands(self):
        """高优先级消费侧命令全部登记。"""
        expected = {
            CommandType.SPAWN_AGENT,
            CommandType.TERMINATE_AGENT,
            CommandType.SEND_MESSAGE,
            CommandType.BROADCAST_MESSAGE,
            CommandType.EXECUTE_ABILITY,
            CommandType.REGISTER_ABILITY,
            CommandType.UNREGISTER_ABILITY,
            CommandType.LIST_AGENTS,
            CommandType.HEALTH_CHECK,
            CommandType.DELEGATE,
            CommandType.SUBSCRIBE,
            CommandType.UNSUBSCRIBE,
            CommandType.GET_AGENT_INFO,
            CommandType.HITL_RESPONSE,
        }
        for ct in expected:
            assert ct in COMMAND_PAYLOAD_MAP, f"{ct} missing from COMMAND_PAYLOAD_MAP"

    def test_event_map_covers_core_events(self):
        expected = {
            EventType.AGENT_SPAWNED,
            EventType.AGENT_TERMINATED,
            EventType.AGENT_RESPONSE,
            EventType.ACTION_CHAIN_UPDATED,
            EventType.AGENT_ERROR,
            EventType.HEALTH_STATUS,
            EventType.ABILITY_RESULT,
            EventType.HITL_REQUEST,
        }
        for et in expected:
            assert et in EVENT_PAYLOAD_MAP, f"{et} missing from EVENT_PAYLOAD_MAP"

    def test_command_result_not_in_map(self):
        """command_result 不登记（welcome 包 schema 不符，保持裸 dict）。"""
        assert SystemType.COMMAND_RESULT.value not in PAYLOAD_MAP

    def test_persist_commands_not_in_map(self):
        """persist_* 不登记（schema 与 wire 不符，留待后续补齐）。"""
        assert CommandType.PERSIST_SAVE_NODE.value not in PAYLOAD_MAP
        assert CommandType.PERSIST_SAVE_MESSAGES.value not in PAYLOAD_MAP

    def test_map_values_are_basemodel_subclasses(self):
        for cls in PAYLOAD_MAP.values():
            assert issubclass(cls, BaseModel)

    def test_plugin_negotiate_registered(self):
        """plugin_negotiate 命令 + payload 双登记，归入 PLUGIN_COMMANDS。"""
        assert COMMAND_PAYLOAD_MAP[CommandType.PLUGIN_NEGOTIATE] is PluginNegotiatePayload
        assert CommandType.PLUGIN_NEGOTIATE.value in PLUGIN_COMMANDS

    def test_plugin_events_registered(self):
        """plugin_* 4 事件登记（enabled/disabled 共用 lifecycle，crashed 独立）。"""
        assert EVENT_PAYLOAD_MAP[EventType.PLUGIN_ENABLED] is PluginLifecyclePayload
        assert EVENT_PAYLOAD_MAP[EventType.PLUGIN_DISABLED] is PluginLifecyclePayload
        assert EVENT_PAYLOAD_MAP[EventType.PLUGIN_NEGOTIATED] is PluginNegotiatedPayload
        assert EVENT_PAYLOAD_MAP[EventType.PLUGIN_CRASHED] is PluginCrashedPayload

    def test_task_attribution_commands_registered(self):
        """task 归因 4 命令登记，归入 TASK_ATTRIBUTION_COMMANDS（不占 TASK_COMMANDS）。"""
        assert (
            COMMAND_PAYLOAD_MAP[CommandType.TASK_SUBMIT_COMPLETION] is TaskSubmitCompletionPayload
        )
        assert COMMAND_PAYLOAD_MAP[CommandType.TASK_VERIFY] is TaskVerifyPayload
        assert COMMAND_PAYLOAD_MAP[CommandType.TASK_LIST_CLAIMS] is TaskClaimListPayload
        assert COMMAND_PAYLOAD_MAP[CommandType.TASK_DUMP] is TaskDumpPayload
        assert CommandType.TASK_SUBMIT_COMPLETION.value in TASK_ATTRIBUTION_COMMANDS
        assert CommandType.TASK_VERIFY.value in TASK_ATTRIBUTION_COMMANDS
        assert CommandType.TASK_LIST_CLAIMS.value in TASK_ATTRIBUTION_COMMANDS
        assert CommandType.TASK_DUMP.value in TASK_ATTRIBUTION_COMMANDS
        # legacy 11 命令照常；归因 4 命令已从 TASK_COMMANDS 移除
        assert len(TASK_COMMANDS) == 11
        assert CommandType.TASK_SUBMIT_COMPLETION.value not in TASK_COMMANDS
        assert CommandType.TASK_VERIFY.value not in TASK_COMMANDS
        assert CommandType.TASK_LIST_CLAIMS.value not in TASK_COMMANDS
        assert CommandType.TASK_DUMP.value not in TASK_COMMANDS

    def test_task_attribution_events_registered(self):
        """task 归因 3 事件登记，共用 TaskClaimEventPayload。"""
        assert EVENT_PAYLOAD_MAP[EventType.TASK_DELIVERED] is TaskClaimEventPayload
        assert EVENT_PAYLOAD_MAP[EventType.TASK_VERIFIED] is TaskClaimEventPayload
        assert EVENT_PAYLOAD_MAP[EventType.TASK_REJECTED] is TaskClaimEventPayload


class TestCompactContextContracts:
    """agent_compact_context 命令与 context_usage_updated 事件契约。"""

    def test_agent_compact_context_registered(self):
        """命令 + payload 双登记，且归入 CORE_COMMANDS（Observer → Subject → Core 转发类）。"""
        assert COMMAND_PAYLOAD_MAP[CommandType.AGENT_COMPACT_CONTEXT] is AgentCompactContextPayload
        assert CommandType.AGENT_COMPACT_CONTEXT.value in CORE_COMMANDS

    def test_context_usage_updated_registered(self):
        """事件 + payload 双登记，且归入 AGENT_SCOPED（发布须带非空归属）。"""
        assert EVENT_PAYLOAD_MAP[EventType.CONTEXT_USAGE_UPDATED] is ContextUsageUpdatedPayload
        assert EventType.CONTEXT_USAGE_UPDATED.value in AGENT_SCOPED_EVENT_TYPES

    def test_agent_compact_context_envelope_round_trip(self):
        """命令载荷经 envelope_from_dict 收窄往返不丢字段。"""
        env = envelope_from_dict(
            {
                "type": "agent_compact_context",
                "payload": {
                    "project_id": "p1",
                    "agent_id": "a1",
                    "agent_name": "writer",
                },
            }
        )
        assert isinstance(env.payload, AgentCompactContextPayload)
        narrowed = expect_payload(env, AgentCompactContextPayload)
        assert narrowed.agent_name == "writer"

    def test_context_usage_updated_envelope_round_trip(self):
        """事件载荷（pre_call 锚点口径 + None 真实值）收窄往返不丢字段。"""
        env = envelope_from_dict(
            {
                "type": "context_usage_updated",
                "payload": {
                    "project_id": "p1",
                    "agent_id": "a1",
                    "agent_name": "writer",
                    "phase": "pre_call",
                    "occupied_tokens": None,
                    "basis": "anchor",
                    "budget_tokens": 32000,
                    "budget_source": "declared",
                    "compact_threshold": 0.8,
                    "compaction": {
                        "occupied": None,
                        "threshold": 0.8,
                        "needs_compaction": False,
                    },
                    "iteration": 3,
                },
            }
        )
        assert isinstance(env.payload, ContextUsageUpdatedPayload)
        narrowed = expect_payload(env, ContextUsageUpdatedPayload)
        assert narrowed.phase == "pre_call"
        assert narrowed.basis == "anchor"
        assert narrowed.occupied_tokens is None
        assert narrowed.budget_source == "declared"
        assert narrowed.real_cache_read_tokens is None
        assert narrowed.real_cache_write_tokens is None
        assert narrowed.compaction["needs_compaction"] is False


class TestAgentResetContracts:
    """agent_reset 命令契约。"""

    def test_agent_reset_registered_in_core_commands(self):
        """命令 + payload 双登记，且归入 CORE_COMMANDS（Observer → Subject → Core 转发类）。"""
        assert COMMAND_PAYLOAD_MAP[CommandType.AGENT_RESET] is AgentResetPayload
        assert CommandType.AGENT_RESET.value in CORE_COMMANDS

    def test_agent_reset_payload_requires_stable_scope(self):
        """缺 project_id / agent_id 快速失败（稳定路由键必填）。"""
        with pytest.raises(ValidationError):
            AgentResetPayload.model_validate({"project_id": "p1"})
        with pytest.raises(ValidationError):
            AgentResetPayload.model_validate({"agent_id": "a1"})
        with pytest.raises(ValidationError):
            AgentResetPayload.model_validate({})

    def test_agent_reset_payload_dumps_exact_fields(self):
        """载荷恰含 project_id/agent_id（不携带可重名 name）。"""
        payload = AgentResetPayload.model_validate({"project_id": "p1", "agent_id": "a1"})
        assert payload.model_dump() == {"project_id": "p1", "agent_id": "a1"}

    def test_agent_reset_envelope_round_trip(self):
        """命令载荷经 envelope_from_dict 收窄往返不丢字段。"""
        env = envelope_from_dict(
            {
                "type": "agent_reset",
                "payload": {"project_id": "p1", "agent_id": "a1"},
            }
        )
        assert isinstance(env.payload, AgentResetPayload)
        narrowed = expect_payload(env, AgentResetPayload)
        assert narrowed.project_id == "p1"
        assert narrowed.agent_id == "a1"


# ─── 验收：known_type / as_*_type 辅助 ───


class TestEnvelopeTypeHelpers:
    def test_known_type_true(self):
        env = Envelope(type="spawn_agent")
        assert env.known_type() is True

    def test_known_type_false(self):
        env = Envelope(type="future_cmd")
        assert env.known_type() is False

    def test_as_command_type(self):
        assert Envelope(type="spawn_agent").as_command_type() == CommandType.SPAWN_AGENT
        assert Envelope(type="future_cmd").as_command_type() is None

    def test_as_event_type(self):
        assert Envelope(type="agent_response").as_event_type() == EventType.AGENT_RESPONSE
        assert Envelope(type="spawn_agent").as_event_type() is None

    def test_as_system_type(self):
        assert Envelope(type="ping").as_system_type() == SystemType.PING
        assert Envelope(type="spawn_agent").as_system_type() is None


# ─── 验收：HITLResponsePayload 定位键 fail-closed ───


class TestHITLResponsePayloadSchema:
    def test_promise_path_fields(self):
        """promise 路径：promise_id/approved/reason（真实链路 WebUI → Core）。"""
        p = HITLResponsePayload(approved=True, promise_id="p1", reason="ok")
        assert p.promise_id == "p1"
        assert p.approved is True
        assert p.reason == "ok"

    def test_triplet_path_fields(self):
        """三元组路径：agent_name/ability_name/tool_call_id/approved/result。"""
        p = HITLResponsePayload(
            approved=False,
            agent_name="a",
            ability_name="write_file",
            tool_call_id="call_1",
            result="denied",
        )
        assert p.agent_name == "a"
        assert p.ability_name == "write_file"
        assert p.tool_call_id == "call_1"
        assert p.approved is False
        assert p.result == "denied"

    def test_promise_and_triplet_both_valid(self):
        """promise 与三元组同时携带：合法（promise 优先解析）。"""
        p = HITLResponsePayload(
            approved=True,
            promise_id="p1",
            agent_name="a",
            ability_name="write_file",
            tool_call_id="call_1",
        )
        assert p.promise_id == "p1"
        assert p.agent_name == "a"

    def test_no_locator_rejected(self):
        """定位键全缺（历史退化载荷）：校验失败（fail-closed 行为变更）。"""
        with pytest.raises(ValidationError):
            HITLResponsePayload(approved=True)

    def test_partial_triplet_rejected(self):
        """三元组不完整（缺 tool_call_id）且无 promise：校验失败。"""
        with pytest.raises(ValidationError):
            HITLResponsePayload(approved=True, agent_name="a", ability_name="write_file")

    def test_envelope_from_dict_narrows_hitl(self):
        env = envelope_from_dict(
            {
                "type": "hitl_response",
                "payload": {"approved": True, "promise_id": "p1"},
            }
        )
        assert isinstance(env.payload, HITLResponsePayload)
        assert env.payload.promise_id == "p1"

    def test_envelope_from_dict_rejects_degenerate_hitl(self):
        """wire 反序列化同样执行 fail-closed 校验。"""
        with pytest.raises(ValidationError):
            envelope_from_dict(
                {
                    "type": "hitl_response",
                    "payload": {"approved": True},
                }
            )


# ─── hitl_resolved 事件载荷 ───


class TestHITLResolvedPayload:
    def test_fields(self):
        p = HITLResolvedPayload(
            agent_name="a",
            ability_name="write_file",
            promise_id="p1",
            tool_call_id="call_1",
            status="timeout",
        )
        assert p.status == "timeout"
        assert p.promise_id == "p1"

    def test_event_registered_in_payload_map(self):
        assert EVENT_PAYLOAD_MAP[EventType.HITL_RESOLVED] is HITLResolvedPayload

    def test_agent_scoped(self):
        assert EventType.HITL_RESOLVED.value in AGENT_SCOPED_EVENT_TYPES


# ─── 验收：payload_agent_name helper ───


class TestPayloadAgentName:
    def test_agent_spawned_without_agent_name_is_unfiltered(self):
        p = AgentSpawnedPayload.model_validate({"name": "agent-x", "config": {"name": "agent-x"}})
        assert payload_agent_name(p) is None

    def test_action_chain_uses_agent_name(self):
        from ghrah.protocol.types import ActionChainUpdatedPayload

        p = ActionChainUpdatedPayload(agent_name="a1", node={})
        assert payload_agent_name(p) == "a1"

    def test_agent_response_without_agent_name_is_unfiltered(self):
        p = AgentResponsePayload(sender="s1", recipient="r1", content="hi")
        assert payload_agent_name(p) is None

    def test_health_status_no_agent(self):
        p = HealthStatusPayload(status={"ok": True})
        assert payload_agent_name(p) is None

    def test_dict_payload(self):
        assert payload_agent_name({"agent_name": "a"}) == "a"
        assert payload_agent_name({"name": "b"}) is None
        assert payload_agent_name({"sender": "c"}) is None
        assert payload_agent_name({"other": 1}) is None

    def test_unknown_payload(self):
        assert payload_agent_name(None) is None
        assert payload_agent_name(42) is None


# ─── 验收：消费侧回归测试 —— type/payload mismatch 静默 vs 抛错 ───


class TestRegressionMismatchExposure:
    """固化：mismatch 在 handler 边界暴露为 ValidationError，不再静默 .get()。"""

    def test_ability_result_with_wrong_shape_raises(self):
        """ability_result 登记了 MAP，错误 shape 会 ValidationError。"""
        env = Envelope(
            type="ability_result",
            payload={"missing_required": "request_id"},
        )
        with pytest.raises(ValidationError):
            expect_payload(env, AbilityResultPayload)

    def test_ability_result_correct_shape(self):
        env = envelope_from_dict(
            {
                "type": "ability_result",
                "payload": {
                    "request_id": "r1",
                    "agent_name": "a",
                    "ability_name": "read_file",
                    "success": True,
                },
            }
        )
        assert isinstance(env.payload, AbilityResultPayload)
        assert env.payload.success is True
