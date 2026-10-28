from __future__ import annotations

import dataclasses
import logging
from typing import Any

from ghrah.protocol.types import (  # type: ignore[import-untyped]
    MANIFEST_COMMANDS as _MANIFEST_COMMANDS,
)
from ghrah.scheme.agent_resolver import ManifestResolver
from ghrah.scheme.loading.manifest_fields import (
    DuplicateManifestError,
    ManifestError,
    ManifestNotFoundError,
    ManifestValidationError,
)

from ghrah.subject.manifest_store.store import ManifestStore

logger = logging.getLogger(__name__)

__all__ = ["_MANIFEST_COMMANDS", "handle_manifest_command"]


def _config_to_payload_dict(config: Any) -> dict[str, Any]:
    """将 AgentConfig dataclass 转为 AgentConfigPayload 兼容的 dict。

    确保与 gateway/observer-core 的 AgentConfigPayload 结构一致。
    """
    window_dict = dataclasses.asdict(config.window) if config.window else None
    context_dict = dataclasses.asdict(config.context) if config.context else None
    model_overrides_dict = (
        dataclasses.asdict(config.model_overrides) if config.model_overrides else None
    )
    return {
        "name": config.name,
        "agent_config_name": config.agent_config_name,
        "description": config.description,
        "system_prompt": config.system_prompt,
        "max_iterations": config.max_iterations,
        "communication_timeout": config.communication_timeout,
        "window": window_dict,
        "context": context_dict,
        "model_overrides": model_overrides_dict,
    }


def handle_manifest_command(
    command: str,
    payload: dict[str, Any],
    store: ManifestStore,
) -> dict[str, Any]:
    """分发 manifest 命令，返回响应 dict。

    Args:
        command: 命令类型，如 'manifest_list_abilities'
        payload: 命令载荷
        store: ManifestStore 实例

    Returns:
        响应 dict，包含 success 字段
    """
    try:
        if command == "manifest_list_abilities":
            namespace = payload.get("namespace")
            names = store.list_abilities(namespace=namespace)
            abilities = []
            for name in names:
                try:
                    m = store.get_ability(name)
                    abilities.append(
                        {
                            "full_name": m.full_name,
                            "namespace": m.metadata.namespace,
                            "name": m.metadata.name,
                            "title": m.metadata.title,
                            "description": m.description,
                            "tags": m.metadata.tags,
                            "permissions": m.permissions.model_dump(mode="python"),
                            "implementation_type": m.implementation.type,
                            "has_hitl": m.permissions.require_hitl,
                        }
                    )
                except Exception:
                    abilities.append({"full_name": name})
            return {"success": True, "data": {"abilities": abilities}}

        elif command == "manifest_get_ability":
            full_name = payload.get("full_name", "")
            manifest = store.get_ability(full_name)
            source = store.get_ability_source(full_name)
            return {
                "success": True,
                "data": {
                    "manifest": manifest.model_dump(mode="python"),
                    "source": source,
                },
            }

        elif command == "manifest_put_ability":
            full_name = payload.get("full_name", "")
            content = payload.get("content", "")
            overwrite = payload.get("overwrite", False)
            store.put_ability(full_name, content, overwrite=overwrite)
            manifest = store.get_ability(full_name)
            source = store.get_ability_source(full_name)
            return {
                "success": True,
                "data": {
                    "full_name": full_name,
                    "manifest": manifest.model_dump(mode="python"),
                    "source": source,
                },
            }

        elif command == "manifest_delete_ability":
            full_name = payload.get("full_name", "")
            store.delete_ability(full_name)
            return {"success": True, "data": {"full_name": full_name}}

        elif command == "manifest_list_agents":
            namespace = payload.get("namespace")
            names = store.list_agents(namespace=namespace)
            agents = []
            for name in names:
                try:
                    m = store.get_agent(name)
                    agents.append(
                        {
                            "full_name": m.full_name,
                            "namespace": m.metadata.namespace,
                            "name": m.metadata.name,
                            "title": m.metadata.title,
                            "description": m.description,
                            "tags": m.metadata.tags,
                            "agent_config_name": m.model.agent_config_name,
                            "system_prompt": m.system_prompt,
                            "ability_refs": [ar.ref or ar.type or "" for ar in m.abilities],
                            "max_iterations": m.max_iterations,
                            "communication_timeout": m.communication_timeout,
                        }
                    )
                except Exception:
                    agents.append({"full_name": name})
            return {"success": True, "data": {"agents": agents}}

        elif command == "manifest_get_agent":
            full_name = payload.get("full_name", "")
            manifest = store.get_agent(full_name)
            source = store.get_agent_source(full_name)
            return {
                "success": True,
                "data": {
                    "manifest": manifest.model_dump(mode="python"),
                    "source": source,
                },
            }

        elif command == "manifest_put_agent":
            full_name = payload.get("full_name", "")
            content = payload.get("content", "")
            overwrite = payload.get("overwrite", False)
            store.put_agent(full_name, content, overwrite=overwrite)
            manifest = store.get_agent(full_name)
            source = store.get_agent_source(full_name)
            return {
                "success": True,
                "data": {
                    "full_name": full_name,
                    "manifest": manifest.model_dump(mode="python"),
                    "source": source,
                },
            }

        elif command == "manifest_delete_agent":
            full_name = payload.get("full_name", "")
            store.delete_agent(full_name)
            return {"success": True, "data": {"full_name": full_name}}

        elif command == "manifest_resolve_agent":
            agent_full_name = payload.get("agent_full_name", "")
            runtime_name = payload.get("runtime_name")
            manifest = store.get_agent(agent_full_name)
            resolver = ManifestResolver(store)
            resolved = resolver.resolve(manifest, runtime_name=runtime_name)
            return {
                "success": True,
                "data": {
                    "config": _config_to_payload_dict(resolved.config),
                    "abilities": [
                        {
                            "ability_name": a.ability_name,
                            "tool_schema": (
                                a.tool_schema.model_dump(mode="python")
                                if a.tool_schema is not None
                                else None
                            ),
                            "permissions": a.permissions.model_dump(mode="python"),
                            "implementation": a.implementation.model_dump(mode="python"),
                            "hooks": a.hooks.model_dump(mode="python"),
                        }
                        for a in resolved.abilities
                    ],
                },
            }

        elif command == "manifest_validate":
            content = payload.get("content", "")
            is_valid, errors = store.validate_manifest(content)
            return {"success": True, "data": {"valid": is_valid, "errors": errors}}

        else:
            return {"success": False, "error": f"Unknown manifest command: {command}"}

    except ManifestNotFoundError as e:
        logger.warning("Manifest not found: %s", e)
        return {"success": False, "error": str(e)}
    except DuplicateManifestError as e:
        logger.warning("Duplicate manifest: %s", e)
        return {"success": False, "error": str(e)}
    except ManifestValidationError as e:
        logger.warning("Manifest validation error: %s", e)
        return {"success": False, "error": str(e)}
    except ManifestError as e:
        logger.error("Manifest error: %s", e)
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("Unexpected error handling manifest command %s", command)
        return {"success": False, "error": f"Internal error: {e}"}
