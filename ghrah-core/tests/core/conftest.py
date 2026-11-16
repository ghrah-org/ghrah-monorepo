# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""真实 CoreUnit 授权装配的测试夹具。"""

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from ghrah.ability.access import AbilityAccessStore, GrantPolicy
from ghrah.core.unit import CoreUnitConfig
from ghrah.manifest.agent import AbilityRef, AgentManifest
from ghrah.manifest.parser import parse_agent_manifest


class _TestAgentStore:
    def __init__(self, members: tuple[str, ...]) -> None:
        self._members = members

    def load_agent(self, full_name: str) -> AgentManifest:
        if full_name != "tests.agent":
            raise KeyError(full_name)
        manifest = parse_agent_manifest(
            """\
manifest: agent
version: "2"
metadata:
  namespace: tests
  name: agent
model:
  endpoint_name: default
system_prompt: You are a test agent.
abilities:
  - type: conversation
  - ref: ghrah.core.session_info
"""
        )
        manifest.abilities.extend(AbilityRef(ref=member) for member in self._members)
        return manifest

    def list_agents(self, namespace: str | None = None) -> list[str]:
        return ["tests.agent"] if namespace in (None, "tests") else []


@pytest.fixture
async def ability_core_config(tmp_path: Path) -> AsyncIterator[Callable[..., CoreUnitConfig]]:
    """每个用例独立授权库，可在重启用例中为多个 Unit 复用。"""
    store = await AbilityAccessStore.open(
        tmp_path / "ability.db",
        "default",
        operators=frozenset({"deployment"}),
        runs=lambda agent: f"{agent}-run",
    )

    def config(*, members: tuple[str, ...] = (), **kwargs: Any) -> CoreUnitConfig:
        return CoreUnitConfig(
            **{
                "project_id": "default",
                "manifest_store": _TestAgentStore(members),
                "ability_store": store,
                "ability_grant_policy": GrantPolicy(mode="merge"),
                "workspace_root": str(tmp_path),
                **kwargs,
            }
        )

    try:
        yield config
    finally:
        await store.close()


@pytest.fixture
def runtime_unit_config() -> CoreUnitConfig:
    """新 Runtime 显式注入模板来源，授权连接仍由 Runtime 管理。"""
    return CoreUnitConfig(manifest_store=_TestAgentStore(()))
