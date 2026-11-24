# SPDX-License-Identifier: Apache-2.0
"""成员驱动的 MCP 调用：连接由成员 server/tool 决定，不依赖部署绑定。"""

from __future__ import annotations

from typing import Any

from agentconf import AgentsConfig

from ghrah.ability.execution.execution_services import build_execution_services
from ghrah.ability.execution.invocation import OperationCall
from ghrah.ability.mcp_member import McpMemberDeployment, McpRuntimeConfig
from ghrah.ability.package.provider import McpSnapshotOperation
from ghrah.mcp.agentconf_source import AgentconfMcpSource
from ghrah.mcp.spec import McpToolSpec
from ghrah.types.results import ActionOutcome

AGENT = "a" * 32


class _Store:
    project_id = "project"

    def __init__(self) -> None:
        self.backends: dict[Any, Any] = {}

    def register_member_backend(self, owner: Any, available: Any) -> None:
        self.backends[owner] = available

    def unregister_member_backend(self, owner: Any) -> None:
        self.backends.pop(owner, None)


async def test_member_service_calls_frozen_server_and_tool(tmp_path, mock_mcp_endpoint) -> None:
    endpoint = mock_mcp_endpoint
    config = AgentsConfig(tmp_path / "agentconf.db")
    config.create_mcp_server(
        "local-server",
        "streamable_http",
        {
            "url": endpoint.url,
            "headers": {"Authorization": f"Bearer {endpoint.token}"},
        },
        auth={"mode": "inline"},
    )
    source = AgentconfMcpSource(config)
    store = _Store()
    deployment = McpMemberDeployment(
        McpRuntimeConfig(enabled=True, source=source),
        store,
    )
    service = deployment.for_agent(AGENT)
    service.bind_run("run-1")
    try:
        outcome = await service.call_member(
            server="local-server",
            tool=McpToolSpec(
                name="echo",
                description="回显",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            ),
            arguments={"text": "member driven"},
        )
        assert outcome.is_error is False
        assert "member driven" in outcome.render_for_model()
        snapshot = McpSnapshotOperation(
            "echo",
            server="local-server",
            tool="echo",
            read_only=True,
            description="回显",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )
        bridged = await snapshot.execute(
            OperationCall(arguments={"text": "through snapshot"}),
            build_execution_services(mcp_runtime=service),
        )
        assert bridged.outcome is ActionOutcome.SUCCESS
        assert "through snapshot" in bridged.data["content"]
        assert endpoint.stats()["calls"] == ["echo", "echo"]
        assert endpoint.token not in outcome.render_for_model()
    finally:
        await deployment.close()


async def test_member_service_rejects_unbound_and_disallowed_server(
    tmp_path, mock_mcp_endpoint
) -> None:
    config = AgentsConfig(tmp_path / "agentconf.db")
    config.create_mcp_server(
        "local-server",
        "streamable_http",
        {"url": mock_mcp_endpoint.url},
        auth={"mode": "none"},
    )
    source = AgentconfMcpSource(config)
    deployment = McpMemberDeployment(
        McpRuntimeConfig(enabled=True, allowed_servers=("other",), source=source),
        _Store(),
    )
    service = deployment.for_agent(AGENT)
    service.bind_run("run-1")
    try:
        try:
            await service.call_member(
                server="local-server",
                tool=McpToolSpec(name="echo", description="回显", input_schema={"type": "object"}),
                arguments={},
            )
        except Exception as error:
            assert "not allowed" in str(error)
        else:  # pragma: no cover - 明确失败
            raise AssertionError("disallowed server must fail")
        assert mock_mcp_endpoint.stats()["calls"] == []
    finally:
        await deployment.close()
