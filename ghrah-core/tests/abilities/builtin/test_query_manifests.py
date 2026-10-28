# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""QueryManifestsAbility 测试：只读 manifest 查询面（C2b）。

- 列表 / 单条读取（含能力清单与权限摘要）
- manifest_store 未接线 → FAILURE 指路（零隐式）
- 截断上限与损坏 manifest 的列表层容错
"""

from __future__ import annotations

from typing import Any

from ghrah.scheme.ability.cluster.operations.query_manifests import QueryManifestsAbility
from ghrah.scheme.agent_manifest import AbilityRef, AgentManifest, AgentMetadata, ModelConfig
from ghrah.scheme.execution.base import ActionOutcome
from ghrah.scheme.execution.context import AbilityExecutionContext
from ghrah.scheme.loading.manifest_fields import PermissionFlags


def _agent_manifest(
    name: str = "designer",
    abilities: list[AbilityRef] | None = None,
) -> AgentManifest:
    return AgentManifest(
        manifest="agent",
        version="1",
        metadata=AgentMetadata(namespace="ghrah", name=name, description=f"manifest {name}"),
        model=ModelConfig(agent_config_name="default"),
        system_prompt=f"You are {name}.",
        abilities=abilities or [AbilityRef(type="conversation")],
    )


class _FakeStore:
    """最小 ManifestStore stub：load_agent/list_agents。"""

    def __init__(self, agents: dict[str, AgentManifest]) -> None:
        self._agents = agents

    def load_agent(self, ref: str) -> AgentManifest:
        if ref not in self._agents:
            raise KeyError(f"agent manifest not found: {ref}")
        return self._agents[ref]

    def list_agents(self) -> list[str]:
        return list(self._agents)


def _make_context(
    store: Any = None,
    tool_args: dict[str, Any] | None = None,
) -> AbilityExecutionContext:
    return AbilityExecutionContext(
        current_ability_name="query_manifests",
        tool_args=tool_args or {},
        manifest_store=store,
    )


class TestQueryManifestsAbility:
    def test_name(self) -> None:
        assert QueryManifestsAbility().name == "query_manifests"

    def test_bind_tool_schema(self) -> None:
        schema = QueryManifestsAbility().bind_tool()
        assert schema is not None
        func = schema["function"]
        assert func["name"] == "query_manifests"
        assert "manifest_ref" in func["parameters"]["properties"]

    async def test_store_not_wired_failure(self) -> None:
        """未接线 → 显式 FAILURE 指路（零隐式）。"""
        result = await QueryManifestsAbility().execute(_make_context(store=None))
        assert result.outcome == ActionOutcome.FAILURE
        assert "manifest_store is not wired" in result.data["error"]

    async def test_list_all_manifests(self) -> None:
        store = _FakeStore(
            {
                "ghrah.designer": _agent_manifest("designer"),
                "ghrah.coder": _agent_manifest("coder"),
            }
        )
        result = await QueryManifestsAbility().execute(_make_context(store=store))
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["count"] == 2
        names = {m["full_name"] for m in result.data["manifests"]}
        assert names == {"ghrah.designer", "ghrah.coder"}

    async def test_read_single_manifest_with_abilities(self) -> None:
        store = _FakeStore(
            {
                "ghrah.designer": _agent_manifest(
                    "designer",
                    abilities=[
                        AbilityRef(type="conversation"),
                        AbilityRef(
                            ref="ghrah.fs.write_file",
                            permissions=PermissionFlags(
                                require_hitl=True,
                                allowed_paths=["{{workspace}}/out"],
                            ),
                        ),
                    ],
                )
            }
        )
        result = await QueryManifestsAbility().execute(
            _make_context(store=store, tool_args={"manifest_ref": "ghrah.designer"})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["full_name"] == "ghrah.designer"
        abilities = result.data["abilities"]
        assert len(abilities) == 2
        hitl_ones = [a for a in abilities if a.get("require_hitl")]
        assert len(hitl_ones) == 1
        assert hitl_ones[0]["allowed_paths"] == ["{{workspace}}/out"]

    async def test_read_unknown_manifest_failure(self) -> None:
        store = _FakeStore({"ghrah.designer": _agent_manifest("designer")})
        result = await QueryManifestsAbility().execute(
            _make_context(store=store, tool_args={"manifest_ref": "ghrah.ghost"})
        )
        assert result.outcome == ActionOutcome.FAILURE
        assert "ghrah.ghost" in result.data["error"]

    async def test_list_tolerates_broken_manifest(self) -> None:
        """列表层单个 manifest 损坏不阻断整体（unavailable 标记）。"""

        class _BrokenStore(_FakeStore):
            def load_agent(self, ref: str) -> AgentManifest:
                if ref == "ghrah.broken":
                    raise RuntimeError("corrupt manifest")
                return super().load_agent(ref)

        store = _BrokenStore(
            {
                "ghrah.designer": _agent_manifest("designer"),
                "ghrah.broken": _agent_manifest("broken"),
            }
        )
        result = await QueryManifestsAbility().execute(_make_context(store=store))
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["count"] == 2
        broken = [m for m in result.data["manifests"] if m["full_name"] == "ghrah.broken"]
        assert broken and broken[0].get("unavailable") is True
