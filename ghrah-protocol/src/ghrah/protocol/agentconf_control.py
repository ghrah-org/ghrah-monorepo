# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agentconf 连接配置的观察端 REST 契约。

身份用资源 UUID。响应不包含 API key、header/env 的值或外部凭据引用。
嵌套秘密项以十个星号表示「保留原值」；省略字段表示不修改，JSON null 表示清空。
TypeScript 由 ``scripts/export_ability_contracts.py`` 单向生成。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ghrah.protocol.ability_tree import TreeModel

__all__ = [
    "AgentconfCapabilityListView",
    "AgentconfCapabilitySummary",
    "AgentconfDeletedView",
    "AgentconfEndpointCreateRequest",
    "AgentconfEndpointListView",
    "AgentconfEndpointPatchRequest",
    "AgentconfEndpointResolveView",
    "AgentconfEndpointView",
    "AgentconfMcpProviderCreateRequest",
    "AgentconfMcpProviderListView",
    "AgentconfMcpProviderPatchRequest",
    "AgentconfMcpProviderReferencesView",
    "AgentconfMcpProviderView",
    "AgentconfMcpServerCreateRequest",
    "AgentconfMcpServerListView",
    "AgentconfMcpServerPatchRequest",
    "AgentconfMcpServerResolveView",
    "AgentconfMcpServerView",
    "AgentconfProviderCreateRequest",
    "AgentconfProviderListView",
    "AgentconfProviderPatchRequest",
    "AgentconfProviderReferencesView",
    "AgentconfProviderView",
    "AgentconfRevisionRequest",
    "AgentconfStatusView",
    "BindingView",
    "McpProviderReferenceView",
]

_AUTH_MODE = Literal["none", "inline", "provider", "external"]
_BINDING_MODE = Literal["inherit", "override"]
_BINDING_TARGET = Literal["http_header", "stdio_env"]


class AgentconfStatusView(TreeModel):
    """库已打开且 schema 为当前版本。旧库走错误体，不走这个视图。"""

    format: Literal["ghrah.agentconf-control.status.v1"] = "ghrah.agentconf-control.status.v1"
    schema_version: int = Field(ge=0)


class AgentconfRevisionRequest(TreeModel):
    """删除时核对的行代次。"""

    expected_revision: int = Field(ge=1)


class AgentconfDeletedView(TreeModel):
    """删除成功。"""

    format: Literal["ghrah.agentconf-control.deleted.v1"] = "ghrah.agentconf-control.deleted.v1"
    id: str
    name: str


class BindingView(TreeModel):
    """认证放置方式。模板含占位符，不含密钥。"""

    target: _BINDING_TARGET
    name: str
    value_template: str


class AgentconfProviderView(TreeModel):
    """模型 Provider。``base_url`` 去掉 userinfo；连接选项中的字符串已掩码。"""

    format: Literal["ghrah.agentconf-control.provider.v1"] = "ghrah.agentconf-control.provider.v1"
    id: str
    name: str
    provider_type: str
    base_url: str
    api_key_configured: bool
    connection_options: dict[str, Any]
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str


class AgentconfProviderListView(TreeModel):
    """模型 Provider 列表。"""

    format: Literal["ghrah.agentconf-control.providers.v1"] = "ghrah.agentconf-control.providers.v1"
    providers: list[AgentconfProviderView] = Field(default_factory=list)


class AgentconfProviderCreateRequest(TreeModel):
    """新建模型 Provider。"""

    name: str = Field(min_length=1)
    provider_type: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    connection_options: dict[str, Any] = Field(default_factory=dict)


class AgentconfProviderPatchRequest(TreeModel):
    """部分修改。未出现的字段保持原值；``api_key`` 为 null 时清空。"""

    expected_revision: int = Field(ge=1)
    name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    connection_options: dict[str, Any] | None = None


class AgentconfProviderReferencesView(TreeModel):
    """引用该 Provider 的模型端点名称。"""

    format: Literal["ghrah.agentconf-control.provider-references.v1"] = (
        "ghrah.agentconf-control.provider-references.v1"
    )
    id: str
    names: list[str] = Field(default_factory=list)


class AgentconfEndpointView(TreeModel):
    """模型端点。字段本身不含连接密钥。"""

    format: Literal["ghrah.agentconf-control.endpoint.v1"] = "ghrah.agentconf-control.endpoint.v1"
    id: str
    name: str
    provider_id: str
    provider_name: str
    model_name: str
    model_type: str
    api_format: str
    capabilities_key: str | None = None
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str


class AgentconfEndpointListView(TreeModel):
    """模型端点列表。"""

    format: Literal["ghrah.agentconf-control.endpoints.v1"] = "ghrah.agentconf-control.endpoints.v1"
    endpoints: list[AgentconfEndpointView] = Field(default_factory=list)


class AgentconfEndpointCreateRequest(TreeModel):
    """新建模型端点。"""

    name: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_type: str = "llm"
    api_format: str = Field(min_length=1)
    capabilities_key: str | None = None


class AgentconfEndpointPatchRequest(TreeModel):
    """部分修改模型端点。``capabilities_key`` 为 null 时取消引用。"""

    expected_revision: int = Field(ge=1)
    name: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    model_type: str | None = None
    api_format: str | None = None
    capabilities_key: str | None = None


class AgentconfEndpointResolveView(TreeModel):
    """本地解析预览。连接密钥只以是否已配置表示。"""

    format: Literal["ghrah.agentconf-control.endpoint-resolve.v1"] = (
        "ghrah.agentconf-control.endpoint-resolve.v1"
    )
    endpoint_id: str
    name: str
    revision: int = Field(ge=1)
    provider_name: str
    provider_type: str
    api_format: str
    base_url: str
    api_key_configured: bool
    model_name: str
    model_type: str
    sdk_hint: str | None = None
    metadata_model_key: str | None = None
    capabilities: dict[str, Any]
    capability_sources: dict[str, str]


class AgentconfMcpProviderView(TreeModel):
    """MCP Provider。密钥不回传。"""

    format: Literal["ghrah.agentconf-control.mcp-provider.v1"] = (
        "ghrah.agentconf-control.mcp-provider.v1"
    )
    id: str
    name: str
    vendor: str | None = None
    api_key_configured: bool
    default_binding: BindingView | None = None
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str


class AgentconfMcpProviderListView(TreeModel):
    """MCP Provider 列表。"""

    format: Literal["ghrah.agentconf-control.mcp-providers.v1"] = (
        "ghrah.agentconf-control.mcp-providers.v1"
    )
    providers: list[AgentconfMcpProviderView] = Field(default_factory=list)


class AgentconfMcpProviderCreateRequest(TreeModel):
    """新建 MCP Provider。名称可以含斜杠，路径使用 id。"""

    name: str = Field(min_length=1)
    vendor: str | None = None
    api_key: str | None = None
    default_binding: BindingView | None = None


class AgentconfMcpProviderPatchRequest(TreeModel):
    """部分修改 MCP Provider。"""

    expected_revision: int = Field(ge=1)
    name: str | None = None
    vendor: str | None = None
    api_key: str | None = None
    default_binding: BindingView | None = None


class McpProviderReferenceView(TreeModel):
    """引用某个 MCP Provider 的服务。"""

    id: str
    name: str
    transport: str


class AgentconfMcpProviderReferencesView(TreeModel):
    """MCP Provider 的引用名单。"""

    format: Literal["ghrah.agentconf-control.mcp-provider-references.v1"] = (
        "ghrah.agentconf-control.mcp-provider-references.v1"
    )
    id: str
    servers: list[McpProviderReferenceView] = Field(default_factory=list)


class AgentconfMcpServerView(TreeModel):
    """MCP 服务。command、args、cwd、url 明文；header/env/options 中的字符串已掩码。"""

    format: Literal["ghrah.agentconf-control.mcp-server.v1"] = (
        "ghrah.agentconf-control.mcp-server.v1"
    )
    id: str
    name: str
    transport: str
    revision: int = Field(ge=1)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, Any] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, Any] = Field(default_factory=dict)
    protocol_version_hint: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    connect_timeout: float | None = None
    auth_mode: _AUTH_MODE
    provider_id: str | None = None
    binding_mode: _BINDING_MODE | None = None
    binding: BindingView | None = None
    reference_configured: bool = False
    created_at: str
    updated_at: str


class AgentconfMcpServerListView(TreeModel):
    """MCP 服务列表。"""

    format: Literal["ghrah.agentconf-control.mcp-servers.v1"] = (
        "ghrah.agentconf-control.mcp-servers.v1"
    )
    servers: list[AgentconfMcpServerView] = Field(default_factory=list)


class AgentconfMcpServerCreateRequest(TreeModel):
    """新建 MCP 服务。自定义 transport 使用 ``options``。"""

    name: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, Any] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, Any] = Field(default_factory=dict)
    protocol_version_hint: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    connect_timeout: float | None = None
    auth_mode: _AUTH_MODE = "none"
    provider_id: str | None = None
    binding_mode: _BINDING_MODE | None = None
    binding: BindingView | None = None
    reference: str | None = None


class AgentconfMcpServerPatchRequest(TreeModel):
    """部分修改 MCP 服务。设置与认证在服务端按最终 transport 合并。"""

    expected_revision: int = Field(ge=1)
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, Any] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, Any] | None = None
    protocol_version_hint: str | None = None
    options: dict[str, Any] | None = None
    connect_timeout: float | None = None
    auth_mode: _AUTH_MODE | None = None
    provider_id: str | None = None
    binding_mode: _BINDING_MODE | None = None
    binding: BindingView | None = None
    reference: str | None = None


class AgentconfMcpServerResolveView(TreeModel):
    """MCP 本地解析预览。不含密钥。"""

    format: Literal["ghrah.agentconf-control.mcp-server-resolve.v1"] = (
        "ghrah.agentconf-control.mcp-server-resolve.v1"
    )
    server_id: str
    name: str
    revision: int = Field(ge=1)
    transport: str
    url: str | None = None
    command: str | None = None
    auth_mode: _AUTH_MODE
    key_configured: bool = False
    provider_id: str | None = None
    provider_name: str | None = None
    binding_mode: _BINDING_MODE | None = None
    binding: BindingView | None = None
    reference_configured: bool = False


class AgentconfCapabilitySummary(TreeModel):
    """能力目录的只读摘要，供端点选择 ``capabilities_key``。"""

    model_key: str
    display_name: str
    model_type: str
    origin: str


class AgentconfCapabilityListView(TreeModel):
    """能力目录检索结果。"""

    format: Literal["ghrah.agentconf-control.capabilities.v1"] = (
        "ghrah.agentconf-control.capabilities.v1"
    )
    capabilities: list[AgentconfCapabilitySummary] = Field(default_factory=list)
