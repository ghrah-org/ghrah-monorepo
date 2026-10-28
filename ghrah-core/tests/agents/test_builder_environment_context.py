# SPDX-FileCopyrightText: 2026 chenxya@ghrah.org
#
# SPDX-License-Identifier: Apache-2.0

"""环境信息注入测试（008 T-2 注入形态）：environment_context_injection 开关
+ [Environment] 段渲染。

- 开关默认关（prompt 无变化，零隐式）
- 开启且 environment_info 提供 → 段前置拼接 system_prompt
- 开启但 environment_info 缺失 → 跳过注入 + 不崩（warning 路径）
- 段内容含 deployment / workspace_root / allowed_roots / hitl 参数
"""

from __future__ import annotations

from ghrah.agents.builder import AgentBuilder
from ghrah.scheme.ability.core.operations.conversation import ConversationAbility
from ghrah.scheme.ability.core.operations.end_task import EndTaskAbility
from ghrah.types.config_types import AgentConfig


def _build_agent(**config_kwargs: object) -> list[str]:
    agent = AgentBuilder.from_config(
        AgentConfig(name="a", system_prompt="base prompt", **config_kwargs),  # type: ignore[arg-type]
        abilities=[ConversationAbility(), EndTaskAbility()],
    )
    messages = agent._context_manager.message_store.current_messages
    return [m.text or "" for m in messages if m.role == "system"]


class TestEnvironmentInjection:
    def test_default_off_no_injection(self) -> None:
        """开关默认关：system_prompt 原样（零隐式）。"""
        system_texts = _build_agent()
        assert system_texts == ["base prompt"]

    def test_injection_on_prepends_section(self) -> None:
        """开关开启 + 快照齐备 → [Environment] 段前置拼接。"""
        system_texts = _build_agent(
            environment_context_injection=True,
            environment_info={
                "deployment": "subject-mounted",
                "workspace_root": "/ws",
                "allowed_roots": ["/ws", "/data"],
                "hitl_timeout": 300,
            },
        )
        assert len(system_texts) == 1
        text = system_texts[0]
        assert text.startswith("[Environment]")
        assert "deployment: subject-mounted" in text
        assert "workspace_root: /ws" in text
        assert "allowed_roots:" in text
        assert "- /ws" in text
        assert "- /data" in text
        assert "hitl_timeout: 300s" in text
        # 原 prompt 保留在段之后
        assert text.endswith("base prompt")

    def test_injection_missing_snapshot_skipped(self) -> None:
        """开关开启但快照缺失 → 跳过注入（不崩、prompt 原样）。"""
        system_texts = _build_agent(environment_context_injection=True)
        assert system_texts == ["base prompt"]

    def test_injection_standalone_deployment(self) -> None:
        """deployment 缺省 → 渲染 standalone。"""
        system_texts = _build_agent(
            environment_context_injection=True,
            environment_info={"hitl_timeout": 60},
        )
        text = system_texts[0]
        assert "deployment: standalone" in text
        assert "hitl_timeout: 60s" in text
        # 无 allowed_roots 时不渲染该行
        assert "allowed_roots:" not in text

    def test_injection_degraded_timeout_only_with_limit(self) -> None:
        """熔断 limit=0（禁用）→ 不渲染熔断两行；非零才渲染。"""
        off = _build_agent(
            environment_context_injection=True,
            environment_info={"hitl_timeout": 300, "hitl_consecutive_timeout_limit": 0},
        )
        assert "hitl_consecutive_timeout_limit" not in off[0]

        on = _build_agent(
            environment_context_injection=True,
            environment_info={
                "hitl_timeout": 300,
                "hitl_consecutive_timeout_limit": 3,
                "hitl_degraded_timeout": 30,
            },
        )
        text = on[0]
        assert "hitl_consecutive_timeout_limit: 3" in text
        assert "hitl_degraded_timeout: 30s" in text

    def test_empty_prompt_section_only(self) -> None:
        """原 prompt 为空 → 段即完整 system prompt。"""
        agent = AgentBuilder.from_config(
            AgentConfig(
                name="a",
                system_prompt="",
                environment_context_injection=True,
                environment_info={"deployment": "standalone", "hitl_timeout": 300},
            ),
            abilities=[ConversationAbility(), EndTaskAbility()],
        )
        messages = agent._context_manager.message_store.current_messages
        system_text = next(m.text for m in messages if m.role == "system")
        assert system_text.startswith("[Environment]")
        assert "deployment: standalone" in system_text
