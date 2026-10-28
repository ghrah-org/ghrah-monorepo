# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""AgentConfig 测试。"""

from ghrah.core.config import AgentConfig


class TestAgentConfig:
    """AgentConfig 数据类测试。"""

    def test_defaults(self):
        """默认值。"""
        config = AgentConfig(name="test")
        assert config.name == "test"
        assert config.endpoint_name is None
        assert config.description == ""
        assert config.system_prompt == ""
        assert config.max_iterations == 10
        assert config.communication_timeout == 300.0
        assert config.resources == {}
        assert config.window is None
        assert config.context is None

    def test_custom_values(self):
        """自定义值。"""
        config = AgentConfig(
            name="reviewer",
            description="代码审查助手",
            system_prompt="你是一个代码审查专家",
            max_iterations=5,
            communication_timeout=600.0,
        )
        assert config.name == "reviewer"
        assert config.description == "代码审查助手"
        assert config.system_prompt == "你是一个代码审查专家"
        assert config.max_iterations == 5
        assert config.communication_timeout == 600.0

    def test_communication_timeout_infinite(self):
        """communication_timeout=-1 表示无限等待。"""
        config = AgentConfig(
            name="long_task",
            communication_timeout=-1,
        )
        assert config.communication_timeout == -1

    def test_endpoint_name_default_none(self):
        """endpoint_name 默认为 None。"""
        config = AgentConfig(name="planner")
        assert config.endpoint_name is None

    def test_endpoint_name_has_no_name_fallback(self):
        """endpoint_name 为 None 时不按运行时 name 查找。"""
        config = AgentConfig(name="planner")
        assert config.endpoint_name is None

    def test_endpoint_name_explicit(self):
        """endpoint_name 在指定 endpoint_name 时使用显式值。"""
        config = AgentConfig(
            name="solve_worker_3",
            endpoint_name="solve_worker",
        )
        assert config.name == "solve_worker_3"
        assert config.endpoint_name == "solve_worker"

    def test_endpoint_name_empty_string_has_no_fallback(self):
        """空端点不按运行时名称回退，交由默认工厂明确诊断。"""
        config = AgentConfig(name="planner", endpoint_name="")
        assert config.endpoint_name == ""

    def test_worker_pool_scenario(self):
        """Worker 池场景：多个 worker 共享同一份 agentconf 配置。"""
        config = AgentConfig(
            name="solve_worker_0",
            endpoint_name="solve_worker",
            description="解题 Worker #0",
        )
        assert config.name == "solve_worker_0"
        assert config.endpoint_name == "solve_worker"

    def test_role_alias_scenario(self):
        """角色别名场景：不同运行时名称，共享同一份 LLM 配置。"""
        config = AgentConfig(
            name="code_reviewer_v2",
            endpoint_name="reviewer",
            description="代码审查专家 v2",
        )
        assert config.name == "code_reviewer_v2"
        assert config.endpoint_name == "reviewer"
