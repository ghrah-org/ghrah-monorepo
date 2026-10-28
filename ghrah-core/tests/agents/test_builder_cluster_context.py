# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""集群身份注入测试（C2c）：cluster_context_injection 开关 + [Cluster Context] 段渲染。

- 开关默认关（prompt 无变化，零隐式）
- 开启且 supervisor 提供 get_cluster_context → 段前置拼接 system_prompt
- supervisor=None / 缺方法（structural typing 缺省）→ 跳过 + 不崩
- 段内容含 cluster_id / 成员名 / 描述 / tags
"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.agents.builder import AgentBuilder
from ghrah.scheme.ability.core.operations.conversation import ConversationAbility
from ghrah.scheme.ability.core.operations.end_task import EndTaskAbility
from ghrah.types.config_types import AgentConfig


class _FakeSupervisor:
    """duck-typed supervisor：仅提供 get_cluster_context。"""

    def __init__(self, cluster_id: str = "cluster-x") -> None:
        self._ctx = {
            "cluster_id": cluster_id,
            "members": [
                {
                    "name": "planner",
                    "description": "任务规划",
                    "tags": ["planning"],
                },
                {"name": "coder", "description": "代码编写", "tags": []},
            ],
        }

    def get_cluster_context(self) -> dict[str, Any]:
        return self._ctx


class TestClusterContextInjection:
    def test_default_off_no_injection(self) -> None:
        """开关默认关：system_prompt 原样（零隐式）。"""
        supervisor = _FakeSupervisor()
        agent = AgentBuilder.from_config(
            AgentConfig(name="a", system_prompt="base prompt"),
            abilities=[ConversationAbility(), EndTaskAbility()],
            supervisor=supervisor,
        )
        messages = agent._context_manager.message_store.current_messages
        system_text = next(m.text for m in messages if m.role == "system")
        assert system_text == "base prompt"

    def test_injection_on_prepends_section(self) -> None:
        """开关开启 → [Cluster Context] 段前置拼接。"""
        supervisor = _FakeSupervisor(cluster_id="cluster-x")
        agent = AgentBuilder.from_config(
            AgentConfig(
                name="a",
                system_prompt="base prompt",
                cluster_context_injection=True,
            ),
            abilities=[ConversationAbility(), EndTaskAbility()],
            supervisor=supervisor,
        )
        messages = agent._context_manager.message_store.current_messages
        system_text = next(m.text for m in messages if m.role == "system")
        assert system_text.startswith("[Cluster Context]")
        assert "cluster_id: cluster-x" in system_text
        assert "name: planner" in system_text
        assert "description: 任务规划" in system_text
        assert "tags: [planning]" in system_text
        assert "name: coder" in system_text
        # 原 prompt 保留在段之后
        assert system_text.endswith("base prompt")

    def test_injection_without_supervisor_skips(self, caplog: pytest.LogCaptureFixture) -> None:
        """supervisor=None → 跳过注入 + warning（不阻断）。"""
        agent = AgentBuilder.from_config(
            AgentConfig(
                name="a",
                system_prompt="base prompt",
                cluster_context_injection=True,
            ),
            abilities=[ConversationAbility(), EndTaskAbility()],
            supervisor=None,
        )
        messages = agent._context_manager.message_store.current_messages
        system_text = next(m.text for m in messages if m.role == "system")
        assert system_text == "base prompt"
        assert any("skipping injection" in r.message for r in caplog.records)

    def test_injection_supervisor_lacks_method_skips(self) -> None:
        """supervisor 无 get_cluster_context（structural typing 缺省）→ 跳过。"""
        bare = object()  # 无 get_cluster_context 属性
        agent = AgentBuilder.from_config(
            AgentConfig(
                name="a",
                system_prompt="base prompt",
                cluster_context_injection=True,
            ),
            abilities=[ConversationAbility(), EndTaskAbility()],
            supervisor=bare,
        )
        messages = agent._context_manager.message_store.current_messages
        system_text = next(m.text for m in messages if m.role == "system")
        assert system_text == "base prompt"

    def test_injection_get_context_raises_skips(self) -> None:
        """get_cluster_context 抛异常 → 跳过 + 不阻断构建。"""

        class _Broken:
            def get_cluster_context(self) -> dict[str, Any]:
                raise RuntimeError("boom")

        agent = AgentBuilder.from_config(
            AgentConfig(
                name="a",
                system_prompt="base prompt",
                cluster_context_injection=True,
            ),
            abilities=[ConversationAbility(), EndTaskAbility()],
            supervisor=_Broken(),
        )
        messages = agent._context_manager.message_store.current_messages
        system_text = next(m.text for m in messages if m.role == "system")
        assert system_text == "base prompt"

    def test_empty_system_prompt_injection_only(self) -> None:
        """空 system_prompt + 开启 → 注入段独占（不产生空行残留）。"""
        supervisor = _FakeSupervisor(cluster_id="c1")
        agent = AgentBuilder.from_config(
            AgentConfig(name="a", cluster_context_injection=True),
            abilities=[ConversationAbility(), EndTaskAbility()],
            supervisor=supervisor,
        )
        messages = agent._context_manager.message_store.current_messages
        system_text = next(m.text for m in messages if m.role == "system")
        assert system_text.startswith("[Cluster Context]")
        assert not system_text.endswith("\n")
