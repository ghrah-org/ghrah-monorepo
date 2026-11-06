# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""表面契约形状：条目、覆盖配置、manifest 字段与 Core 配置默认值。"""

from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from ghrah.ability.surface import (
    DEFAULT_DEFINITION_TOKEN_RATIO,
    DEFAULT_HARD_CAP,
    DEFAULT_LOADED_LIMIT,
    DEFAULT_MAX_STATIC,
    DEFAULT_SEARCH_LIMIT,
    SEARCH_LIMIT_ABS_MAX,
    STATIC_ABS_LIMIT,
    MemberRef,
    SurfaceConfig,
    SurfaceEntry,
)
from ghrah.core.unit import CoreUnitConfig
from ghrah.manifest.agent import AgentManifest


def _agent_manifest(**overrides: object) -> AgentManifest:
    """构造一个最小 AgentManifest，用于验证可选 ``surface`` 段。"""
    data: dict[str, object] = {
        "manifest": "agent",
        "version": "2",
        "metadata": {"namespace": "my_project", "name": "dev_agent"},
        "model": {"endpoint_name": "gpt-4o-dev"},
        "system_prompt": "You are a dev agent.",
        "abilities": [{"ref": "ghrah.fs.read_file"}],
        "surface": None,
    }
    data.update(overrides)
    return AgentManifest.model_validate(data)


def _surface_entry(**overrides: object) -> SurfaceEntry:
    data: dict[str, object] = {
        "tool_name": "read_file",
        "member": {"instance_id": "inst-1", "member": "read_file"},
        "package": "fs",
    }
    data.update(overrides)
    return SurfaceEntry.model_validate(data)


class TestMemberRef:
    def test_valid(self) -> None:
        ref = MemberRef(instance_id="inst-1", member="read_file")
        assert ref.instance_id == "inst-1"
        assert ref.member == "read_file"

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            MemberRef(instance_id="inst-1", member="read_file", path="fs/read_file.py")

    def test_frozen(self) -> None:
        ref = MemberRef(instance_id="inst-1", member="read_file")
        with pytest.raises(ValidationError):
            ref.member = "write_file"


class TestSurfaceEntry:
    def test_defaults(self) -> None:
        entry = _surface_entry()
        assert entry.static is False
        assert entry.available is True

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            _surface_entry(schema={"type": "object"})

    def test_frozen(self) -> None:
        entry = _surface_entry()
        with pytest.raises(ValidationError):
            entry.tool_name = "write_file"

    def test_tool_name_length_bound(self) -> None:
        assert _surface_entry(tool_name="a" * 64).tool_name == "a" * 64
        with pytest.raises(ValidationError):
            _surface_entry(tool_name="a" * 65)

    def test_json_schema_has_contract_fields(self) -> None:
        schema = SurfaceEntry.model_json_schema()
        assert set(schema["properties"]) == {
            "tool_name",
            "member",
            "package",
            "static",
            "available",
        }
        assert schema["properties"]["tool_name"]["maxLength"] == 64


class TestSurfaceConfig:
    def test_defaults(self) -> None:
        config = SurfaceConfig()
        assert config.mode == "auto"
        assert config.static == []
        assert config.max_static is None

    def test_full(self) -> None:
        config = SurfaceConfig(mode="static", static=["ghrah.fs.read_file"], max_static=12)
        assert config.mode == "static"
        assert config.static == ["ghrah.fs.read_file"]
        assert config.max_static == 12

    @pytest.mark.parametrize("mode", ["invalid", "AUTO", ""])
    def test_invalid_mode(self, mode: str) -> None:
        with pytest.raises(ValidationError):
            SurfaceConfig(mode=mode)  # type: ignore[arg-type]

    def test_max_static_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SurfaceConfig(max_static=0)

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            SurfaceConfig(hard_cap=30)


class TestAgentManifestSurface:
    def test_surface_is_optional(self) -> None:
        manifest = _agent_manifest()
        assert manifest.surface is None

    def test_surface_is_parsed(self) -> None:
        manifest = _agent_manifest(
            surface={"mode": "progressive", "static": ["ghrah.fs.read_file"], "max_static": 12}
        )
        assert isinstance(manifest.surface, SurfaceConfig)
        assert manifest.surface.mode == "progressive"
        assert manifest.surface.static == ["ghrah.fs.read_file"]

    def test_surface_rejects_unknown_key(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            _agent_manifest(surface={"mode": "auto", "unknown": True})


class TestCoreUnitConfigSurface:
    def test_surface_defaults(self) -> None:
        config = CoreUnitConfig()
        assert config.surface_definition_token_ratio == DEFAULT_DEFINITION_TOKEN_RATIO
        assert config.surface_max_static == DEFAULT_MAX_STATIC
        assert config.surface_hard_cap == DEFAULT_HARD_CAP
        assert config.surface_search_limit == DEFAULT_SEARCH_LIMIT
        assert config.surface_loaded_limit == DEFAULT_LOADED_LIMIT

    def test_frozen_defaults(self) -> None:
        """默认值就是冻结值，不随实现漂移。"""
        assert DEFAULT_DEFINITION_TOKEN_RATIO == 0.02
        assert DEFAULT_MAX_STATIC == 20
        assert DEFAULT_HARD_CAP == 30
        assert DEFAULT_SEARCH_LIMIT == 8
        assert DEFAULT_LOADED_LIMIT == 8

    def test_absolute_limits(self) -> None:
        assert STATIC_ABS_LIMIT == 64
        assert SEARCH_LIMIT_ABS_MAX == 20
        assert DEFAULT_SEARCH_LIMIT <= SEARCH_LIMIT_ABS_MAX


def test_surface_import_does_not_create_cycle() -> None:
    """两种导入顺序都必须成功：manifest 不得反向依赖 ability 包。"""
    for code in (
        "import ghrah.manifest.agent; import ghrah.ability.surface",
        "import ghrah.ability.surface; import ghrah.manifest.agent",
    ):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr
