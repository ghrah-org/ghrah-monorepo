# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""观察端对 Agentconf 连接配置的 REST。

每次请求打开短连接，在线程里调用同步 SDK。响应不回传密钥。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any, NoReturn
from urllib.parse import urlsplit, urlunsplit

import anyio
from agentconf import AgentsConfig
from agentconf.catalog import ModelsDevSource
from agentconf.exceptions import (
    AgentsConfigError,
    CapabilityNotFoundError,
    CapabilityTypeMismatchError,
    CatalogImportError,
    EndpointNotFoundError,
    MCPProviderNotFoundError,
    MCPServerNotFoundError,
    MigrationError,
    MigrationRequiredError,
    ProviderNotFoundError,
    ResourceAlreadyExistsError,
    ResourceInUseError,
    RevisionConflictError,
)
from agentconf.models.mcp_provider import ExternalAuth, ProviderAuth
from agentconf.models.mcp_server import (
    GenericMCPConnection,
    StdioConnection,
    StreamableHTTPConnection,
)
from agentconf.models.secure import MASK, contains_mask, reveal
from agentconf.storage.database import SCHEMA_VERSION
from fastapi import APIRouter, Query, Request, Response
from ghrah.protocol.agentconf_control import (
    AgentconfCapabilityConflictView,
    AgentconfCapabilityDetailView,
    AgentconfCapabilityListView,
    AgentconfCapabilitySummary,
    AgentconfCapabilityUpdatePreviewView,
    AgentconfCapabilityUpdateRequest,
    AgentconfCapabilityUpdateResultView,
    AgentconfDeletedView,
    AgentconfEndpointCreateRequest,
    AgentconfEndpointListView,
    AgentconfEndpointPatchRequest,
    AgentconfEndpointResolveView,
    AgentconfEndpointView,
    AgentconfMcpProviderCreateRequest,
    AgentconfMcpProviderListView,
    AgentconfMcpProviderPatchRequest,
    AgentconfMcpProviderReferencesView,
    AgentconfMcpProviderView,
    AgentconfMcpServerCreateRequest,
    AgentconfMcpServerListView,
    AgentconfMcpServerPatchRequest,
    AgentconfMcpServerResolveView,
    AgentconfMcpServerView,
    AgentconfProviderCreateRequest,
    AgentconfProviderListView,
    AgentconfProviderPatchRequest,
    AgentconfProviderReferencesView,
    AgentconfProviderView,
    AgentconfRevisionRequest,
    AgentconfStatusView,
    BindingView,
    McpProviderReferenceView,
)
from pydantic import ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.service_keys import OBSERVER_ENDPOINT
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units.ability_control import AbilityApiError, _authorized

logger = logging.getLogger(__name__)

__all__ = ["AgentconfControlUnit", "build_agentconf_control_router"]

_NOT_FOUND = (
    ProviderNotFoundError,
    EndpointNotFoundError,
    MCPServerNotFoundError,
    MCPProviderNotFoundError,
    CapabilityNotFoundError,
)
_SETTING_FIELDS = frozenset(
    {
        "transport",
        "command",
        "args",
        "env",
        "cwd",
        "url",
        "headers",
        "protocol_version_hint",
        "options",
        "connect_timeout",
    }
)
_AUTH_FIELDS = frozenset({"auth_mode", "provider_id", "binding_mode", "binding", "reference"})


def _public_url(value: str) -> str:
    """去掉 URL 里的 userinfo，保留其余部分供页面编辑。"""
    parts = urlsplit(value)
    if parts.username is None and parts.password is None:
        return value
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname if parts.port is None else f"{hostname}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _url_identity(value: str) -> tuple[str, str, int | None, str, str, str]:
    """比较地址时忽略协议/主机大小写、默认端口和路径末尾的斜杠。"""
    parts = urlsplit(value.strip())
    scheme = parts.scheme.casefold()
    port = parts.port if parts.port is not None else _DEFAULT_PORTS.get(scheme)
    path = "" if parts.path in ("", "/") else parts.path.rstrip("/")
    return (scheme, (parts.hostname or "").casefold(), port, path, parts.query, parts.fragment)


def _url_for_write(submitted: str, stored: str) -> str:
    """回传的是去掉 userinfo 的同一地址时，保留库里的原值。

    提交值自己带了 userinfo 时按新地址写入，避免把用户新填的凭据丢掉。
    """
    parts = urlsplit(submitted)
    if parts.username is None and parts.password is None:
        if _url_identity(_public_url(stored)) == _url_identity(submitted):
            return stored
    return submitted


def _configured(value: Any) -> bool:
    return value not in (None, "")


def _mask_tree(value: Any) -> Any:
    """字符串叶子换成掩码；数字和布尔值保留，便于编辑 timeout 这类选项。"""
    if isinstance(value, str):
        return MASK
    if isinstance(value, dict):
        return {str(key): _mask_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_tree(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return MASK


def _reject_new_mask(value: Any) -> Any:
    if contains_mask(value):
        raise ValueError("掩码只能保留已有敏感字段")
    return value


def _merge_secret(incoming: Any, stored: Any) -> Any:
    """掩码保留旧值，对象按键合并。调用方再把明文结果交给 SDK。"""
    if incoming == MASK:
        if stored is None:
            raise ValueError("掩码只能保留已有敏感字段")
        return stored
    if isinstance(incoming, dict):
        stored_dict = stored if isinstance(stored, dict) else {}
        return {
            str(key): (
                _merge_secret(item, stored_dict[key])
                if key in stored_dict
                else _reject_new_mask(item)
            )
            for key, item in incoming.items()
        }
    if isinstance(incoming, list):
        stored_list = stored if isinstance(stored, list) else []
        if len(incoming) == len(stored_list):
            return [_merge_secret(item, stored_list[index]) for index, item in enumerate(incoming)]
        return [_reject_new_mask(item) for item in incoming]
    return _reject_new_mask(incoming)


def _binding_view(binding: Any) -> BindingView | None:
    if binding is None:
        return None
    return BindingView(
        target=binding.target, name=binding.name, value_template=binding.value_template
    )


def _binding_payload(binding: BindingView | None) -> dict[str, str] | None:
    if binding is None:
        return None
    return {
        "target": binding.target,
        "name": binding.name,
        "value_template": binding.value_template,
    }


def _stamp(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _hint(value: Any) -> str | None:
    if value is None:
        return None
    distribution = getattr(value, "distribution", None)
    import_name = getattr(value, "import_name", None)
    if isinstance(distribution, str) and isinstance(import_name, str):
        return f"{distribution}:{import_name}"
    return str(value)


def _open(config: SubjectConfig) -> AgentsConfig:
    path = config.agentconf_db_path
    return AgentsConfig(path) if path else AgentsConfig()


def _raise_mapped(exc: Exception) -> None:
    if isinstance(exc, MigrationRequiredError):
        raise AbilityApiError(
            "migration_required",
            "配置库需要先迁移，请使用 agentconf migrate prepare/apply",
            status=503,
        ) from exc
    if isinstance(exc, MigrationError):
        raise AbilityApiError("unavailable", str(exc), status=503) from exc
    if isinstance(exc, RevisionConflictError):
        raise AbilityApiError("conflict", str(exc), status=409) from exc
    if isinstance(exc, ResourceInUseError):
        raise AbilityApiError("resource_in_use", str(exc), status=409) from exc
    if isinstance(exc, ResourceAlreadyExistsError):
        raise AbilityApiError("already_exists", str(exc), status=409) from exc
    if isinstance(exc, _NOT_FOUND):
        raise AbilityApiError("not_found", str(exc), status=404) from exc
    if isinstance(exc, (ValidationError, ValueError, CapabilityTypeMismatchError)):
        raise AbilityApiError("invalid_request", str(exc), status=400) from exc
    if isinstance(exc, CatalogImportError):
        raise AbilityApiError(
            "catalog_unavailable",
            "能力目录暂时无法从上游更新",
            status=503,
        ) from exc
    if isinstance(exc, sqlite3.Error):
        raise AbilityApiError("unavailable", "配置库暂时不可用", status=503) from exc
    if isinstance(exc, AgentsConfigError):
        raise AbilityApiError("invalid_change", str(exc), status=400) from exc
    raise exc


def _call(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except AbilityApiError:
        raise
    except Exception as exc:
        _raise_mapped(exc)
        raise


def _provider_view(provider: Any) -> AgentconfProviderView:
    key = reveal(provider.api_key) if provider.api_key is not None else None
    return AgentconfProviderView(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        base_url=_public_url(reveal(provider.base_url)),
        api_key_configured=_configured(key),
        connection_options=_mask_tree(reveal(provider.connection_options)),
        revision=provider.revision,
        created_at=_stamp(provider.created_at),
        updated_at=_stamp(provider.updated_at),
    )


def _endpoint_view(endpoint: Any, provider_name: str) -> AgentconfEndpointView:
    return AgentconfEndpointView(
        id=endpoint.id,
        name=endpoint.name,
        provider_id=endpoint.provider_id,
        provider_name=provider_name,
        model_name=endpoint.model_name,
        model_type=endpoint.model_type,
        api_format=endpoint.api_format,
        capabilities_key=endpoint.capabilities_key,
        revision=endpoint.revision,
        created_at=_stamp(endpoint.created_at),
        updated_at=_stamp(endpoint.updated_at),
    )


def _mcp_provider_view(provider: Any) -> AgentconfMcpProviderView:
    key = reveal(provider.api_key) if provider.api_key is not None else None
    return AgentconfMcpProviderView(
        id=provider.id,
        name=provider.name,
        vendor=provider.vendor,
        api_key_configured=_configured(key),
        default_binding=_binding_view(provider.default_binding),
        revision=provider.revision,
        created_at=_stamp(provider.created_at),
        updated_at=_stamp(provider.updated_at),
    )


def _server_view(server: Any) -> AgentconfMcpServerView:
    settings = server.settings
    command = cwd = url = hint = None
    args: list[str] = []
    env: dict[str, Any] = {}
    headers: dict[str, Any] = {}
    options: dict[str, Any] = {}
    timeout = None
    if isinstance(settings, StdioConnection):
        command = reveal(settings.command)
        args = list(reveal(settings.args))
        env = _mask_tree(reveal(settings.env))
        cwd = reveal(settings.cwd) if settings.cwd is not None else None
        timeout = settings.connect_timeout
    elif isinstance(settings, StreamableHTTPConnection):
        url = _public_url(reveal(settings.url))
        headers = _mask_tree(reveal(settings.headers))
        timeout = settings.connect_timeout
        hint = settings.protocol_version_hint
    elif isinstance(settings, GenericMCPConnection):
        options = _mask_tree(reveal(settings.options))
    auth = server.auth
    provider_id = binding_mode = None
    binding = None
    reference_configured = False
    if isinstance(auth, ProviderAuth):
        provider_id = auth.provider_id
        binding_mode = auth.binding_mode
        binding = _binding_view(auth.binding)
    elif isinstance(auth, ExternalAuth):
        reference_configured = _configured(reveal(auth.reference))
        binding = _binding_view(auth.binding)
    return AgentconfMcpServerView(
        id=server.id,
        name=server.name,
        transport=server.transport,
        revision=server.revision,
        command=command,
        args=args,
        env=env,
        cwd=cwd,
        url=url,
        headers=headers,
        protocol_version_hint=hint,
        options=options,
        connect_timeout=timeout,
        auth_mode=auth.mode,
        provider_id=provider_id,
        binding_mode=binding_mode,
        binding=binding,
        reference_configured=reference_configured,
        created_at=_stamp(server.created_at),
        updated_at=_stamp(server.updated_at),
    )


def _conflict(exc: RevisionConflictError, view: Any) -> NoReturn:
    raise AbilityApiError(
        "conflict",
        str(exc),
        detail={"view": view.model_dump(mode="json")},
        status=409,
    ) from exc


def _status(config: SubjectConfig) -> AgentconfStatusView:
    with _open(config) as cfg:
        del cfg
    return AgentconfStatusView(schema_version=SCHEMA_VERSION)


def _list_providers(config: SubjectConfig) -> AgentconfProviderListView:
    with _open(config) as cfg:
        providers = [_provider_view(item) for item in cfg.list_providers()]
        return AgentconfProviderListView(providers=providers)


def _create_provider(
    config: SubjectConfig, body: AgentconfProviderCreateRequest
) -> AgentconfProviderView:
    with _open(config) as cfg:
        created = cfg.create_provider(
            body.name,
            body.provider_type,
            body.base_url,
            body.api_key,
            connection_options=body.connection_options,
        )
        return _provider_view(created)


def _provider_changes(current: Any, body: AgentconfProviderPatchRequest) -> dict[str, Any]:
    fields = body.model_fields_set - {"expected_revision"}
    changes: dict[str, Any] = {}
    if "name" in fields:
        changes["name"] = body.name
    if "provider_type" in fields:
        changes["provider_type"] = body.provider_type
    if "base_url" in fields:
        if body.base_url is None:
            raise ValueError("服务地址不能清空")
        changes["base_url"] = _url_for_write(body.base_url, reveal(current.base_url))
    if "api_key" in fields:
        changes["api_key"] = _reject_new_mask(body.api_key)
    if "connection_options" in fields:
        stored = reveal(current.connection_options)
        incoming = body.connection_options
        changes["connection_options"] = {} if incoming is None else _merge_secret(incoming, stored)
    return changes


def _update_provider(
    config: SubjectConfig, resource_id: str, body: AgentconfProviderPatchRequest
) -> AgentconfProviderView:
    with _open(config) as cfg:
        current = cfg.get_provider_by_id(resource_id)
        changes = _provider_changes(current, body)
        if not changes:
            if current.revision != body.expected_revision:
                _conflict(RevisionConflictError("资源已变化，请重新读取"), _provider_view(current))
            return _provider_view(current)
        try:
            updated = cfg.update_provider(
                current.name,
                expected_revision=body.expected_revision,
                expected_id=current.id,
                **changes,
            )
        except RevisionConflictError as exc:
            _conflict(exc, _provider_view(cfg.get_provider_by_id(resource_id)))
        return _provider_view(updated)


def _delete_provider(
    config: SubjectConfig, resource_id: str, body: AgentconfRevisionRequest
) -> AgentconfDeletedView:
    with _open(config) as cfg:
        current = cfg.get_provider_by_id(resource_id)
        try:
            cfg.delete_provider(
                current.name, expected_revision=body.expected_revision, expected_id=current.id
            )
        except ResourceInUseError as exc:
            raise AbilityApiError(
                "resource_in_use",
                str(exc),
                detail={"references": cfg.provider_references(current.name)},
                status=409,
            ) from exc
        return AgentconfDeletedView(id=current.id, name=current.name)


def _provider_references(
    config: SubjectConfig, resource_id: str
) -> AgentconfProviderReferencesView:
    with _open(config) as cfg:
        current = cfg.get_provider_by_id(resource_id)
        return AgentconfProviderReferencesView(
            id=current.id, names=cfg.provider_references(current.name)
        )


def _provider_name_map(cfg: AgentsConfig) -> dict[str, str]:
    return {item.id: item.name for item in cfg.list_providers()}


def _list_endpoints(
    config: SubjectConfig,
    *,
    model_type: str | None,
    api_format: str | None,
    provider_name: str | None,
) -> AgentconfEndpointListView:
    with _open(config) as cfg:
        names = _provider_name_map(cfg)
        rows = cfg.list_model_endpoints(
            provider_name=provider_name,
            model_type=model_type,
            api_format=api_format,
        )
        return AgentconfEndpointListView(
            endpoints=[_endpoint_view(item, names.get(item.provider_id, "")) for item in rows]
        )


def _create_endpoint(
    config: SubjectConfig, body: AgentconfEndpointCreateRequest
) -> AgentconfEndpointView:
    with _open(config) as cfg:
        created = cfg.create_model_endpoint(
            body.provider_name,
            body.name,
            body.model_name,
            body.api_format,
            model_type=body.model_type,
            capabilities_key=body.capabilities_key,
        )
        provider = cfg.get_provider_by_id(created.provider_id)
        return _endpoint_view(created, provider.name)


def _endpoint_changes(body: AgentconfEndpointPatchRequest) -> dict[str, Any]:
    fields = body.model_fields_set - {"expected_revision"}
    return {name: getattr(body, name) for name in fields}


def _update_endpoint(
    config: SubjectConfig, resource_id: str, body: AgentconfEndpointPatchRequest
) -> AgentconfEndpointView:
    with _open(config) as cfg:
        current = cfg.get_model_endpoint_by_id(resource_id)
        changes = _endpoint_changes(body)
        if not changes:
            if current.revision != body.expected_revision:
                names = _provider_name_map(cfg)
                _conflict(
                    RevisionConflictError("资源已变化，请重新读取"),
                    _endpoint_view(current, names.get(current.provider_id, "")),
                )
            names = _provider_name_map(cfg)
            return _endpoint_view(current, names.get(current.provider_id, ""))
        try:
            updated = cfg.update_model_endpoint(
                current.name,
                expected_revision=body.expected_revision,
                expected_id=current.id,
                **changes,
            )
        except RevisionConflictError as exc:
            names = _provider_name_map(cfg)
            latest = cfg.get_model_endpoint_by_id(resource_id)
            _conflict(exc, _endpoint_view(latest, names.get(latest.provider_id, "")))
        provider = cfg.get_provider_by_id(updated.provider_id)
        return _endpoint_view(updated, provider.name)


def _delete_endpoint(
    config: SubjectConfig, resource_id: str, body: AgentconfRevisionRequest
) -> AgentconfDeletedView:
    with _open(config) as cfg:
        current = cfg.get_model_endpoint_by_id(resource_id)
        cfg.delete_model_endpoint(
            current.name, expected_revision=body.expected_revision, expected_id=current.id
        )
        return AgentconfDeletedView(id=current.id, name=current.name)


def _resolve_endpoint(config: SubjectConfig, resource_id: str) -> AgentconfEndpointResolveView:
    with _open(config) as cfg:
        current = cfg.get_model_endpoint_by_id(resource_id)
        resolved = cfg.resolve_model_endpoint(current.name)
        connection = resolved.connection
        key = reveal(connection.api_key) if connection.api_key is not None else None
        capabilities = resolved.capabilities
        dumped = (
            capabilities.model_dump(mode="json")
            if hasattr(capabilities, "model_dump")
            else dict(capabilities)
        )
        metadata = resolved.metadata
        return AgentconfEndpointResolveView(
            endpoint_id=resolved.endpoint_id,
            name=resolved.name,
            revision=resolved.revision,
            provider_name=connection.provider_name,
            provider_type=connection.provider_type,
            api_format=connection.api_format,
            base_url=_public_url(reveal(connection.base_url)),
            api_key_configured=_configured(key),
            model_name=resolved.model.model_name,
            model_type=resolved.model.model_type,
            sdk_hint=_hint(connection.sdk_hint),
            metadata_model_key=None if metadata is None else metadata.model_key,
            capabilities=dumped,
            capability_sources=dict(resolved.capability_sources),
        )


def _list_mcp_providers(config: SubjectConfig) -> AgentconfMcpProviderListView:
    with _open(config) as cfg:
        return AgentconfMcpProviderListView(
            providers=[_mcp_provider_view(item) for item in cfg.list_mcp_providers()]
        )


def _create_mcp_provider(
    config: SubjectConfig, body: AgentconfMcpProviderCreateRequest
) -> AgentconfMcpProviderView:
    with _open(config) as cfg:
        created = cfg.create_mcp_provider(
            body.name,
            vendor=body.vendor,
            api_key=body.api_key,
            default_binding=_binding_payload(body.default_binding),
        )
        return _mcp_provider_view(created)


def _mcp_provider_changes(
    body: AgentconfMcpProviderPatchRequest,
) -> dict[str, Any]:
    fields = body.model_fields_set - {"expected_revision"}
    changes: dict[str, Any] = {}
    if "name" in fields:
        changes["name"] = body.name
    if "vendor" in fields:
        changes["vendor"] = body.vendor
    if "api_key" in fields:
        changes["api_key"] = _reject_new_mask(body.api_key)
    if "default_binding" in fields:
        changes["default_binding"] = _binding_payload(body.default_binding)
    return changes


def _update_mcp_provider(
    config: SubjectConfig, resource_id: str, body: AgentconfMcpProviderPatchRequest
) -> AgentconfMcpProviderView:
    with _open(config) as cfg:
        current = cfg.get_mcp_provider_by_id(resource_id)
        changes = _mcp_provider_changes(body)
        if not changes:
            if current.revision != body.expected_revision:
                _conflict(
                    RevisionConflictError("资源已变化，请重新读取"),
                    _mcp_provider_view(current),
                )
            return _mcp_provider_view(current)
        try:
            updated = cfg.update_mcp_provider(
                current.name,
                expected_revision=body.expected_revision,
                expected_id=current.id,
                **changes,
            )
        except RevisionConflictError as exc:
            _conflict(exc, _mcp_provider_view(cfg.get_mcp_provider_by_id(resource_id)))
        return _mcp_provider_view(updated)


def _delete_mcp_provider(
    config: SubjectConfig, resource_id: str, body: AgentconfRevisionRequest
) -> AgentconfDeletedView:
    with _open(config) as cfg:
        current = cfg.get_mcp_provider_by_id(resource_id)
        try:
            cfg.delete_mcp_provider(
                current.name, expected_revision=body.expected_revision, expected_id=current.id
            )
        except ResourceInUseError as exc:
            raise AbilityApiError(
                "resource_in_use",
                str(exc),
                detail={"references": cfg.mcp_provider_references(current.name)},
                status=409,
            ) from exc
        return AgentconfDeletedView(id=current.id, name=current.name)


def _mcp_provider_references(
    config: SubjectConfig, resource_id: str
) -> AgentconfMcpProviderReferencesView:
    with _open(config) as cfg:
        current = cfg.get_mcp_provider_by_id(resource_id)
        rows = cfg.mcp_provider_references(current.name)
        return AgentconfMcpProviderReferencesView(
            id=current.id,
            servers=[
                McpProviderReferenceView(
                    id=item["id"], name=item["name"], transport=item["transport"]
                )
                for item in rows
            ],
        )


def _auth_dict(
    mode: str,
    *,
    provider_id: str | None,
    binding_mode: str | None,
    binding: BindingView | None,
    reference: str | None,
) -> dict[str, Any]:
    if mode == "none":
        return {"mode": "none"}
    if mode == "inline":
        return {"mode": "inline"}
    if mode == "provider":
        payload: dict[str, Any] = {
            "mode": "provider",
            "provider_id": provider_id,
            "binding_mode": binding_mode,
        }
        if binding_mode == "override":
            payload["binding"] = _binding_payload(binding)
        return payload
    return {
        "mode": "external",
        "reference": reference,
        "binding": _binding_payload(binding),
    }


def _create_settings(body: AgentconfMcpServerCreateRequest) -> dict[str, Any]:
    if body.transport == "stdio":
        settings: dict[str, Any] = {
            "command": body.command or "",
            "args": list(body.args),
            "env": body.env,
            "cwd": body.cwd,
        }
        if body.connect_timeout is not None:
            settings["connect_timeout"] = body.connect_timeout
        return settings
    if body.transport == "streamable_http":
        settings = {"url": body.url or "", "headers": body.headers}
        if body.connect_timeout is not None:
            settings["connect_timeout"] = body.connect_timeout
        if body.protocol_version_hint is not None:
            settings["protocol_version_hint"] = body.protocol_version_hint
        return settings
    return dict(body.options)


def _stored_settings(server: Any) -> dict[str, Any]:
    settings = server.settings
    if isinstance(settings, StdioConnection):
        return {
            "command": reveal(settings.command),
            "args": list(reveal(settings.args)),
            "env": reveal(settings.env),
            "cwd": reveal(settings.cwd) if settings.cwd is not None else None,
            "connect_timeout": settings.connect_timeout,
        }
    if isinstance(settings, StreamableHTTPConnection):
        return {
            "url": reveal(settings.url),
            "headers": reveal(settings.headers),
            "connect_timeout": settings.connect_timeout,
            "protocol_version_hint": settings.protocol_version_hint,
        }
    if isinstance(settings, GenericMCPConnection):
        return {"options": reveal(settings.options)}
    return {}


def _pick(body: Any, fields: set[str], name: str, raw: dict[str, Any], default: Any) -> Any:
    if name in fields:
        return getattr(body, name)
    return raw.get(name, default)


def _patched_settings(server: Any, body: AgentconfMcpServerPatchRequest) -> dict[str, Any] | None:
    fields = set(body.model_fields_set)
    if not (fields & _SETTING_FIELDS):
        return None
    transport = body.transport if "transport" in fields and body.transport else server.transport
    raw = {} if transport != server.transport else _stored_settings(server)
    if transport == "stdio":
        env = raw.get("env") or {}
        if "env" in fields:
            env = {} if body.env is None else _merge_secret(body.env, env)
        settings: dict[str, Any] = {
            "command": _pick(body, fields, "command", raw, "") or "",
            "args": list(_pick(body, fields, "args", raw, []) or []),
            "env": env,
            "cwd": _pick(body, fields, "cwd", raw, None),
        }
        timeout = _pick(body, fields, "connect_timeout", raw, None)
        if timeout is not None:
            settings["connect_timeout"] = timeout
        return settings
    if transport == "streamable_http":
        headers = raw.get("headers") or {}
        if "headers" in fields:
            headers = {} if body.headers is None else _merge_secret(body.headers, headers)
        stored_url = raw.get("url")
        if "url" in fields and body.url is not None and isinstance(stored_url, str):
            url = _url_for_write(body.url, stored_url)
        elif "url" in fields:
            url = body.url or ""
        else:
            url = stored_url or ""
        settings = {"url": url, "headers": headers}
        timeout = _pick(body, fields, "connect_timeout", raw, None)
        if timeout is not None:
            settings["connect_timeout"] = timeout
        hint = _pick(body, fields, "protocol_version_hint", raw, None)
        if hint is not None:
            settings["protocol_version_hint"] = hint
        return settings
    options = raw.get("options") or {}
    if "options" in fields:
        options = {} if body.options is None else _merge_secret(body.options, options)
    return options


def _patched_auth(server: Any, body: AgentconfMcpServerPatchRequest) -> dict[str, Any] | None:
    fields = set(body.model_fields_set)
    if not (fields & _AUTH_FIELDS):
        return None
    auth = server.auth
    mode = body.auth_mode if "auth_mode" in fields and body.auth_mode else auth.mode
    provider_id = (
        body.provider_id if "provider_id" in fields else getattr(auth, "provider_id", None)
    )
    binding_mode = (
        body.binding_mode if "binding_mode" in fields else getattr(auth, "binding_mode", None)
    )
    binding = body.binding if "binding" in fields else _binding_view(getattr(auth, "binding", None))
    if "reference" in fields:
        reference = body.reference
    elif isinstance(auth, ExternalAuth):
        reference = reveal(auth.reference)
    else:
        reference = None
    return _auth_dict(
        mode,
        provider_id=provider_id,
        binding_mode=binding_mode,
        binding=binding,
        reference=reference,
    )


def _list_servers(config: SubjectConfig, *, transport: str | None) -> AgentconfMcpServerListView:
    with _open(config) as cfg:
        return AgentconfMcpServerListView(
            servers=[_server_view(item) for item in cfg.list_mcp_servers(transport=transport)]
        )


def _create_server(
    config: SubjectConfig, body: AgentconfMcpServerCreateRequest
) -> AgentconfMcpServerView:
    with _open(config) as cfg:
        created = cfg.create_mcp_server(
            body.name,
            body.transport,
            _create_settings(body),
            auth=_auth_dict(
                body.auth_mode,
                provider_id=body.provider_id,
                binding_mode=body.binding_mode,
                binding=body.binding,
                reference=body.reference,
            ),
        )
        return _server_view(created)


def _update_server(
    config: SubjectConfig, resource_id: str, body: AgentconfMcpServerPatchRequest
) -> AgentconfMcpServerView:
    with _open(config) as cfg:
        current = cfg.get_mcp_server_by_id(resource_id)
        changes: dict[str, Any] = {}
        if "name" in body.model_fields_set:
            changes["name"] = body.name
        if "transport" in body.model_fields_set and body.transport:
            changes["transport"] = body.transport
        settings = _patched_settings(current, body)
        if settings is not None:
            changes["settings"] = settings
        auth = _patched_auth(current, body)
        if auth is not None:
            changes["auth"] = auth
        if not changes:
            if current.revision != body.expected_revision:
                _conflict(RevisionConflictError("资源已变化，请重新读取"), _server_view(current))
            return _server_view(current)
        try:
            updated = cfg.update_mcp_server(
                current.name,
                expected_revision=body.expected_revision,
                expected_id=current.id,
                **changes,
            )
        except RevisionConflictError as exc:
            _conflict(exc, _server_view(cfg.get_mcp_server_by_id(resource_id)))
        return _server_view(updated)


def _delete_server(
    config: SubjectConfig, resource_id: str, body: AgentconfRevisionRequest
) -> AgentconfDeletedView:
    with _open(config) as cfg:
        current = cfg.get_mcp_server_by_id(resource_id)
        cfg.delete_mcp_server(
            current.name, expected_revision=body.expected_revision, expected_id=current.id
        )
        return AgentconfDeletedView(id=current.id, name=current.name)


def _resolve_server(config: SubjectConfig, resource_id: str) -> AgentconfMcpServerResolveView:
    with _open(config) as cfg:
        current = cfg.get_mcp_server_by_id(resource_id)
        resolved = cfg.resolve_mcp_server(current.name)
        connection = resolved.connection
        url = command = None
        if isinstance(connection, StreamableHTTPConnection):
            url = _public_url(reveal(connection.url))
        elif isinstance(connection, StdioConnection):
            command = reveal(connection.command)
        auth = resolved.auth
        mode = "none" if auth is None else auth.mode
        reference = None if auth is None else auth.reference
        return AgentconfMcpServerResolveView(
            server_id=resolved.server_id,
            name=resolved.name,
            revision=resolved.revision,
            transport=resolved.transport,
            url=url,
            command=command,
            auth_mode=mode,
            key_configured=False if auth is None else bool(auth.key_configured),
            provider_id=None if auth is None else auth.provider_id,
            provider_name=None if auth is None else auth.provider_name,
            binding_mode=None if auth is None else auth.binding_mode,
            binding=None if auth is None else _binding_view(auth.binding),
            reference_configured=_configured(reveal(reference) if reference is not None else None),
        )


def _capability_detail(config: SubjectConfig, model_key: str) -> AgentconfCapabilityDetailView:
    """返回档案里明确声明的能力，不把未声明字段补成默认值。"""
    with _open(config) as cfg:
        profile = cfg.catalog_get(model_key)
    return AgentconfCapabilityDetailView(
        model_key=profile.model_key,
        display_name=profile.display_name,
        model_type=profile.model_type,
        origin=str(profile.origin),
        capabilities=profile.declaration(),
    )


def _capabilities(
    config: SubjectConfig, *, model_type: str | None, query: str | None
) -> AgentconfCapabilityListView:
    with _open(config) as cfg:
        rows = cfg.catalog_list(model_type=model_type)
        needle = (query or "").casefold()
        matched = [
            AgentconfCapabilitySummary(
                model_key=item.model_key,
                display_name=item.display_name,
                model_type=item.model_type,
                origin=str(item.origin),
            )
            for item in rows
            if not needle
            or needle in item.model_key.casefold()
            or needle in item.display_name.casefold()
        ]
        return AgentconfCapabilityListView(capabilities=matched)


def _capability_update_preview(config: SubjectConfig) -> AgentconfCapabilityUpdatePreviewView:
    """下载上游目录并只返回计数。快照留在服务端这一次调用里。"""
    snapshot = ModelsDevSource().fetch()
    with _open(config) as cfg:
        plan = cfg.catalog_update_preview(snapshot)
    return AgentconfCapabilityUpdatePreviewView(
        source=snapshot.source,
        source_sha256=snapshot.source_sha256,
        added_count=len(plan.added),
        updatable_count=len(plan.updatable),
        protected_count=len(plan.protected),
        conflict_count=len(plan.conflicts),
        missing_count=len(plan.missing),
        conflicts=[
            AgentconfCapabilityConflictView(key=key, reason=reason)
            for key, reason in sorted(plan.conflicts.items())
        ],
    )


def _capability_update_apply(
    config: SubjectConfig, body: AgentconfCapabilityUpdateRequest
) -> AgentconfCapabilityUpdateResultView:
    """再次下载。指纹与预览一致才写入新增项和可更新项。"""
    snapshot = ModelsDevSource().fetch()
    if snapshot.source_sha256 != body.source_sha256:
        raise AbilityApiError(
            "catalog_source_changed",
            "上游目录已变化，请重新预览",
            status=409,
        )
    with _open(config) as cfg:
        plan = cfg.catalog_update_preview(snapshot)
        result = cfg.catalog_update_apply(plan)
    return AgentconfCapabilityUpdateResultView(
        added_count=len(result["added"]),
        updated_count=len(result["updated"]),
        skipped_count=len(result["skipped"]),
        protected_count=len(result["protected"]),
        conflict_count=len(result["conflicts"]),
    )


def _blank(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def build_agentconf_control_router(*, config: SubjectConfig) -> APIRouter:
    """构造 Agentconf 连接配置路由。认证与能力管理面相同。"""
    enabled = config.observer_http_api_enabled

    async def _guard(request: Request) -> None:
        if not enabled:
            raise AbilityApiError(
                "capability_unavailable",
                "观测端能力管理面未启用（observer_http_api_enabled=false）",
            )
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")

    async def _run(fn: Callable[[], Any]) -> Any:
        return await anyio.to_thread.run_sync(_call, fn)

    router = APIRouter(prefix="/api/v1/agentconf")

    @router.get("/status", response_model=AgentconfStatusView)
    async def status(request: Request) -> AgentconfStatusView:
        await _guard(request)
        return await _run(lambda: _status(config))

    @router.get("/providers", response_model=AgentconfProviderListView)
    async def list_providers(request: Request) -> AgentconfProviderListView:
        await _guard(request)
        return await _run(lambda: _list_providers(config))

    @router.post("/providers", response_model=AgentconfProviderView, status_code=201)
    async def create_provider(
        body: AgentconfProviderCreateRequest, request: Request, response: Response
    ) -> AgentconfProviderView:
        await _guard(request)
        view = await _run(lambda: _create_provider(config, body))
        response.headers["Location"] = f"/api/v1/agentconf/providers/{view.id}"
        return view

    @router.get("/providers/{resource_id}", response_model=AgentconfProviderView)
    async def get_provider(resource_id: str, request: Request) -> AgentconfProviderView:
        await _guard(request)
        return await _run(lambda: _one_provider(config, resource_id))

    @router.patch("/providers/{resource_id}", response_model=AgentconfProviderView)
    async def patch_provider(
        resource_id: str, body: AgentconfProviderPatchRequest, request: Request
    ) -> AgentconfProviderView:
        await _guard(request)
        return await _run(lambda: _update_provider(config, resource_id, body))

    @router.delete("/providers/{resource_id}", response_model=AgentconfDeletedView)
    async def delete_provider(
        resource_id: str, body: AgentconfRevisionRequest, request: Request
    ) -> AgentconfDeletedView:
        await _guard(request)
        return await _run(lambda: _delete_provider(config, resource_id, body))

    @router.get(
        "/providers/{resource_id}/references",
        response_model=AgentconfProviderReferencesView,
    )
    async def provider_references(
        resource_id: str, request: Request
    ) -> AgentconfProviderReferencesView:
        await _guard(request)
        return await _run(lambda: _provider_references(config, resource_id))

    @router.get("/endpoints", response_model=AgentconfEndpointListView)
    async def list_endpoints(
        request: Request,
        model_type: str | None = None,
        api_format: str | None = None,
        provider_name: str | None = None,
    ) -> AgentconfEndpointListView:
        await _guard(request)
        return await _run(
            lambda: _list_endpoints(
                config,
                model_type=_blank(model_type),
                api_format=_blank(api_format),
                provider_name=_blank(provider_name),
            )
        )

    @router.post("/endpoints", response_model=AgentconfEndpointView, status_code=201)
    async def create_endpoint(
        body: AgentconfEndpointCreateRequest, request: Request, response: Response
    ) -> AgentconfEndpointView:
        await _guard(request)
        view = await _run(lambda: _create_endpoint(config, body))
        response.headers["Location"] = f"/api/v1/agentconf/endpoints/{view.id}"
        return view

    @router.get("/endpoints/{resource_id}", response_model=AgentconfEndpointView)
    async def get_endpoint(resource_id: str, request: Request) -> AgentconfEndpointView:
        await _guard(request)
        return await _run(lambda: _one_endpoint(config, resource_id))

    @router.patch("/endpoints/{resource_id}", response_model=AgentconfEndpointView)
    async def patch_endpoint(
        resource_id: str, body: AgentconfEndpointPatchRequest, request: Request
    ) -> AgentconfEndpointView:
        await _guard(request)
        return await _run(lambda: _update_endpoint(config, resource_id, body))

    @router.delete("/endpoints/{resource_id}", response_model=AgentconfDeletedView)
    async def delete_endpoint(
        resource_id: str, body: AgentconfRevisionRequest, request: Request
    ) -> AgentconfDeletedView:
        await _guard(request)
        return await _run(lambda: _delete_endpoint(config, resource_id, body))

    @router.post(
        "/endpoints/{resource_id}/resolve",
        response_model=AgentconfEndpointResolveView,
    )
    async def resolve_endpoint(resource_id: str, request: Request) -> AgentconfEndpointResolveView:
        await _guard(request)
        return await _run(lambda: _resolve_endpoint(config, resource_id))

    @router.get("/mcp-providers", response_model=AgentconfMcpProviderListView)
    async def list_mcp_providers(request: Request) -> AgentconfMcpProviderListView:
        await _guard(request)
        return await _run(lambda: _list_mcp_providers(config))

    @router.post("/mcp-providers", response_model=AgentconfMcpProviderView, status_code=201)
    async def create_mcp_provider(
        body: AgentconfMcpProviderCreateRequest, request: Request, response: Response
    ) -> AgentconfMcpProviderView:
        await _guard(request)
        view = await _run(lambda: _create_mcp_provider(config, body))
        response.headers["Location"] = f"/api/v1/agentconf/mcp-providers/{view.id}"
        return view

    @router.get("/mcp-providers/{resource_id}", response_model=AgentconfMcpProviderView)
    async def get_mcp_provider(resource_id: str, request: Request) -> AgentconfMcpProviderView:
        await _guard(request)
        return await _run(lambda: _one_mcp_provider(config, resource_id))

    @router.patch("/mcp-providers/{resource_id}", response_model=AgentconfMcpProviderView)
    async def patch_mcp_provider(
        resource_id: str, body: AgentconfMcpProviderPatchRequest, request: Request
    ) -> AgentconfMcpProviderView:
        await _guard(request)
        return await _run(lambda: _update_mcp_provider(config, resource_id, body))

    @router.delete("/mcp-providers/{resource_id}", response_model=AgentconfDeletedView)
    async def delete_mcp_provider(
        resource_id: str, body: AgentconfRevisionRequest, request: Request
    ) -> AgentconfDeletedView:
        await _guard(request)
        return await _run(lambda: _delete_mcp_provider(config, resource_id, body))

    @router.get(
        "/mcp-providers/{resource_id}/references",
        response_model=AgentconfMcpProviderReferencesView,
    )
    async def mcp_provider_references(
        resource_id: str, request: Request
    ) -> AgentconfMcpProviderReferencesView:
        await _guard(request)
        return await _run(lambda: _mcp_provider_references(config, resource_id))

    @router.get("/mcp-servers", response_model=AgentconfMcpServerListView)
    async def list_mcp_servers(
        request: Request, transport: str | None = None
    ) -> AgentconfMcpServerListView:
        await _guard(request)
        return await _run(lambda: _list_servers(config, transport=_blank(transport)))

    @router.post("/mcp-servers", response_model=AgentconfMcpServerView, status_code=201)
    async def create_mcp_server(
        body: AgentconfMcpServerCreateRequest, request: Request, response: Response
    ) -> AgentconfMcpServerView:
        await _guard(request)
        view = await _run(lambda: _create_server(config, body))
        response.headers["Location"] = f"/api/v1/agentconf/mcp-servers/{view.id}"
        return view

    @router.get("/mcp-servers/{resource_id}", response_model=AgentconfMcpServerView)
    async def get_mcp_server(resource_id: str, request: Request) -> AgentconfMcpServerView:
        await _guard(request)
        return await _run(lambda: _one_server(config, resource_id))

    @router.patch("/mcp-servers/{resource_id}", response_model=AgentconfMcpServerView)
    async def patch_mcp_server(
        resource_id: str, body: AgentconfMcpServerPatchRequest, request: Request
    ) -> AgentconfMcpServerView:
        await _guard(request)
        return await _run(lambda: _update_server(config, resource_id, body))

    @router.delete("/mcp-servers/{resource_id}", response_model=AgentconfDeletedView)
    async def delete_mcp_server(
        resource_id: str, body: AgentconfRevisionRequest, request: Request
    ) -> AgentconfDeletedView:
        await _guard(request)
        return await _run(lambda: _delete_server(config, resource_id, body))

    @router.post(
        "/mcp-servers/{resource_id}/resolve",
        response_model=AgentconfMcpServerResolveView,
    )
    async def resolve_mcp_server(
        resource_id: str, request: Request
    ) -> AgentconfMcpServerResolveView:
        await _guard(request)
        return await _run(lambda: _resolve_server(config, resource_id))

    @router.get("/capabilities/profile", response_model=AgentconfCapabilityDetailView)
    async def capability_profile(
        request: Request, model_key: str = Query(min_length=1)
    ) -> AgentconfCapabilityDetailView:
        await _guard(request)
        return await _run(lambda: _capability_detail(config, model_key))

    @router.get("/capabilities", response_model=AgentconfCapabilityListView)
    async def capabilities(
        request: Request,
        model_type: str | None = None,
        q: str | None = Query(default=None),
    ) -> AgentconfCapabilityListView:
        await _guard(request)
        return await _run(
            lambda: _capabilities(config, model_type=_blank(model_type), query=_blank(q))
        )

    @router.post(
        "/capabilities/updates/preview",
        response_model=AgentconfCapabilityUpdatePreviewView,
    )
    async def preview_capability_update(
        request: Request,
    ) -> AgentconfCapabilityUpdatePreviewView:
        await _guard(request)
        return await _run(lambda: _capability_update_preview(config))

    @router.post(
        "/capabilities/updates",
        response_model=AgentconfCapabilityUpdateResultView,
    )
    async def apply_capability_update(
        body: AgentconfCapabilityUpdateRequest, request: Request
    ) -> AgentconfCapabilityUpdateResultView:
        await _guard(request)
        return await _run(lambda: _capability_update_apply(config, body))

    return router


def _one_provider(config: SubjectConfig, resource_id: str) -> AgentconfProviderView:
    with _open(config) as cfg:
        return _provider_view(cfg.get_provider_by_id(resource_id))


def _one_endpoint(config: SubjectConfig, resource_id: str) -> AgentconfEndpointView:
    with _open(config) as cfg:
        current = cfg.get_model_endpoint_by_id(resource_id)
        names = _provider_name_map(cfg)
        return _endpoint_view(current, names.get(current.provider_id, ""))


def _one_mcp_provider(config: SubjectConfig, resource_id: str) -> AgentconfMcpProviderView:
    with _open(config) as cfg:
        return _mcp_provider_view(cfg.get_mcp_provider_by_id(resource_id))


def _one_server(config: SubjectConfig, resource_id: str) -> AgentconfMcpServerView:
    with _open(config) as cfg:
        return _server_view(cfg.get_mcp_server_by_id(resource_id))


class AgentconfControlUnit(SubjectUnit):
    """Agentconf 配置面。挂在 Agent 管理面之后，共用同一个 FastAPI 应用。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._router: APIRouter | None = None
        self._meta = UnitMeta(
            name="agentconf_control",
            requires=frozenset({OBSERVER_ENDPOINT}),
            routes=RouteSpec(),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def router(self) -> APIRouter:
        if self._router is None:
            raise RuntimeError("AgentconfControlUnit has not been initialized.")
        return self._router

    async def init(self, ctx: Any) -> None:
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        router = build_agentconf_control_router(config=self._config)
        endpoint.app.include_router(router)
        self._router = router
        logger.info(
            "agentconf control REST mounted (enabled=%s)",
            self._config.observer_http_api_enabled,
        )

    async def stop(self) -> None:
        return None
