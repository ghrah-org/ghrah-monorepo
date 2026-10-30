# SPDX-License-Identifier: Apache-2.0
"""新增驱动作为显式来源进入版本 2 Agent 解析与真实后端。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ghrah.ability import BuiltinAbilitySource, Operation
from ghrah.manifest.agent import AgentManifest
from ghrah.manifest.errors import ManifestNotFoundError, ManifestValidationError
from ghrah.manifest.protocols import ManifestStoreProtocol
from ghrah.manifest.resolver import ResolvedAbility
from ghrah.scheme_ability.execution.context import AbilityExecutionContext
from ghrah.scheme_ability.permissions.fs_permissions import FSPermissionChecker
from ghrah.types.results import ActionOutcome


def manifest() -> AgentManifest:
    """生产 schema 同时覆盖内置 type 和完整 ref 两条声明入口。"""
    return AgentManifest.model_validate(
        {
            "manifest": "agent",
            "version": "2",
            "metadata": {"namespace": "test_ns", "name": "source_agent"},
            "model": {"endpoint_name": "local/ability", "extra_params": {"stop": ["END"]}},
            "system_prompt": "Use explicitly selected abilities.",
            "abilities": [{"type": "read_file"}, {"ref": "ghrah.core.conversation"}],
        }
    )


def test_current_resolver_uses_new_source_without_legacy_lookup(monkeypatch) -> None:
    from ghrah.abilities.registry import AbilityRegistry
    from ghrah.manifest import resolver as legacy_resolver

    def reject_legacy_lookup(*args, **kwargs):
        raise AssertionError("selected source must not fall back to the legacy registry")

    monkeypatch.setattr(AbilityRegistry, "has", reject_legacy_lookup)
    monkeypatch.setattr(legacy_resolver, "load_all_builtin_manifests", reject_legacy_lookup)
    resolved = BuiltinAbilitySource().resolver().resolve(manifest())
    assert resolved.config.endpoint_name == "local/ability"
    assert [entry.ability_name for entry in resolved.abilities] == [
        "ghrah.fs.read_file",
        "ghrah.core.conversation",
    ]


def test_unknown_and_special_assembly_members_are_not_implicitly_resolved() -> None:
    source = BuiltinAbilitySource()
    with pytest.raises(ManifestValidationError, match="Unknown builtin"):
        source.load_builtin("unknown")
    with pytest.raises(ManifestNotFoundError, match="explicit assembly"):
        source.load_builtin("send")
    with pytest.raises(ManifestNotFoundError):
        source.create_operation("arbitrary.module.execute")
    with pytest.raises(ValueError, match="registry and catalog"):
        ResolvedAbility.from_builtin("read_file", registry=Mock(), catalog=source)


def test_agent_delegation_and_ability_views_are_read_only() -> None:
    store = Mock(spec=ManifestStoreProtocol)
    store.load_agent.return_value = manifest()
    store.list_agents.return_value = ["test_ns.source_agent"]
    source = BuiltinAbilitySource(agent_store=store)
    assert source.load_agent("test_ns.source_agent") is store.load_agent.return_value
    assert source.list_agents("test_ns") == ["test_ns.source_agent"]
    store.list_agents.assert_called_once_with("test_ns")
    first = source.load_ability("ghrah.fs.read_file")
    first.metadata.permissions.allowed_paths.append("/unexpected")
    assert (
        "/unexpected"
        not in source.load_ability("ghrah.fs.read_file").metadata.permissions.allowed_paths
    )
    with pytest.raises(ManifestNotFoundError, match="not wired"):
        BuiltinAbilitySource().load_agent("test_ns.source_agent")


async def test_selected_new_operation_has_real_file_effect_and_rejects_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    source = BuiltinAbilitySource()
    operation = source.create_operation(
        "ghrah.fs.write_file",
        permission_checker=FSPermissionChecker(allowed_paths=[str(root)], require_approval=False),
    )
    assert isinstance(operation, Operation)
    assert type(operation).__module__.startswith("ghrah.scheme_ability.")
    path = root / "note.txt"
    result = await operation.execute(
        AbilityExecutionContext(tool_args={"file_path": str(path), "content": "new driver"})
    )
    assert result.outcome is ActionOutcome.SUCCESS
    assert path.read_text() == "new driver"
    outside = tmp_path / "outside.txt"
    refused = await operation.execute(
        AbilityExecutionContext(tool_args={"file_path": str(outside), "content": "escape"})
    )
    assert refused.outcome is ActionOutcome.FAILURE
    assert not outside.exists()


async def test_new_spawn_consumes_current_manifest_store_and_endpoint_config() -> None:
    store = Mock(spec=ManifestStoreProtocol)
    store.load_agent.return_value = manifest()
    source = BuiltinAbilitySource(agent_store=store)
    supervisor = SimpleNamespace(
        get_cluster_context=lambda: {"cluster_id": "test-cluster"},
        spawn_agent=AsyncMock(return_value="spawned"),
    )
    spawn = source.create_operation("ghrah.cluster.spawn_agent")
    result = await spawn.execute(
        AbilityExecutionContext(
            manifest_store=source,
            supervisor=supervisor,
            tool_args={"manifest_ref": "test_ns.source_agent", "name": "child"},
        )
    )
    assert result.outcome is ActionOutcome.SUCCESS
    config = supervisor.spawn_agent.await_args.args[0]
    assert config.name == "child"
    assert config.endpoint_name == "local/ability"
    assert config.model_overrides.extra_params == {"stop": ["END"]}
    assert all(
        type(operation).__module__.startswith("ghrah.scheme_ability.")
        for operation in supervisor.spawn_agent.await_args.kwargs["abilities"]
    )
