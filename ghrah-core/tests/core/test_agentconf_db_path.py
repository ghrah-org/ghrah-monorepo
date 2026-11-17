# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``CoreUnitConfig.agentconf_db_path`` 贯穿链测试（Core 侧）。

覆盖三处此前仍读 ``~/.agentconf`` 的入口：

1. ``CoreUnit._validate_model_config``（spawn 校验）；
2. ``default_llm_factory(db_path)``（默认 LLM 工厂，兼容旧 ``_default_llm_factory``）；
3. ``CoreUnit`` 装配 Supervisor 时把路径绑定进默认工厂，且不覆盖显式注入的
   ``llm_factory``（注入即全责）。

默认库（``~/.agentconf/config.db``）通过 ``HOME`` 重定向到 ``tmp_path``，避免
读写真实用户目录。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest
from test_core_unit import FakeCtx

from ghrah.agents.builder import _default_llm_factory, default_llm_factory
from ghrah.core.unit import CoreUnitConfig, create_core_unit
from ghrah.types.config_types import AgentConfig


def _make_agentconf_db(path: Path, endpoint: str) -> Path:
    """在指定路径建一个可解析的最小 AgentConf 库（不需要真实 LLM）。"""
    from agentconf import AgentsConfig

    with AgentsConfig(path) as config:
        config.create_provider("e2e-provider", "openai", "http://127.0.0.1:9/v1", api_key="sk-test")
        config.create_model_endpoint("e2e-provider", endpoint, "e2e-model", "openai.responses")
    return path


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


class TestValidateModelConfigUsesBoundDB:
    def test_bound_path_resolves_endpoint(self, tmp_path: Path) -> None:
        """绑定路径后，spawn 校验在自定义库里解析端点。"""
        endpoint = _unique("bound")
        db = _make_agentconf_db(tmp_path / "agentconf.db", endpoint)
        unit = create_core_unit(CoreUnitConfig(project_id="default", agentconf_db_path=str(db)))

        unit._validate_model_config(endpoint)  # 不抛即通过

    def test_default_path_does_not_see_custom_endpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未绑定路径时仍读默认库：自定义库里的端点解析失败（旧行为）。"""
        endpoint = _unique("custom-only")
        _make_agentconf_db(tmp_path / "agentconf.db", endpoint)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        unit = create_core_unit(CoreUnitConfig(project_id="default"))

        with pytest.raises(ValueError, match=f"model_configuration_invalid: {endpoint}"):
            unit._validate_model_config(endpoint)

    def test_injected_llm_factory_keeps_validation_skipped(self) -> None:
        """显式注入 llm_factory 时仍跳过默认校验（注入即全责）。"""
        unit = create_core_unit(
            CoreUnitConfig(
                project_id="default",
                agentconf_db_path="/nonexistent/agentconf.db",
                llm_factory=lambda _config: object(),
            )
        )

        unit._validate_model_config("whatever")  # 不抛即通过


class TestDefaultLLMFactory:
    def test_bound_factory_resolves_from_bound_db(self, tmp_path: Path) -> None:
        endpoint = _unique("factory")
        db = _make_agentconf_db(tmp_path / "agentconf.db", endpoint)

        llm = default_llm_factory(str(db))(AgentConfig(name="runtime", endpoint_name=endpoint))

        assert llm is not None

    def test_unbound_factory_rejects_bound_only_endpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _unique("bound-only")
        _make_agentconf_db(tmp_path / "agentconf.db", endpoint)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        from agentconf.exceptions import EndpointNotFoundError

        with pytest.raises(EndpointNotFoundError):
            _default_llm_factory(AgentConfig(name="runtime", endpoint_name=endpoint))

    @pytest.mark.parametrize("name", [None, "", " "])
    def test_missing_endpoint_name_still_rejected(self, name: str | None) -> None:
        with pytest.raises(ValueError, match="endpoint_name is required"):
            default_llm_factory()(AgentConfig(name="runtime", endpoint_name=name))


class TestSupervisorBindsDefaultFactory:
    async def test_bound_path_binds_default_factory(
        self,
        ability_core_config: Callable[..., CoreUnitConfig],
        tmp_path: Path,
    ) -> None:
        """有 agentconf_db_path 且未注入 llm_factory 时，Supervisor 持有绑定工厂。"""
        endpoint = _unique("supervisor")
        db = _make_agentconf_db(tmp_path / "agentconf.db", endpoint)
        unit = create_core_unit(ability_core_config(agentconf_db_path=str(db)))
        await unit.init(FakeCtx())
        try:
            factory = unit.supervisor._llm_factory  # noqa: SLF001 - 断言装配结果
            assert callable(factory)
            assert factory(AgentConfig(name="runtime", endpoint_name=endpoint)) is not None
        finally:
            await unit.stop()

    async def test_no_path_keeps_builder_default(
        self, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        """未配置路径时不绑定默认工厂，保持 AgentBuilder 内建默认。"""
        unit = create_core_unit(ability_core_config())
        await unit.init(FakeCtx())
        try:
            assert unit.supervisor._llm_factory is None  # noqa: SLF001
        finally:
            await unit.stop()

    async def test_injected_factory_is_not_overridden(
        self,
        ability_core_config: Callable[..., CoreUnitConfig],
        tmp_path: Path,
    ) -> None:
        """显式注入 llm_factory 时路径不改变注入结果（注入即全责）。"""
        injected = lambda _config: object()  # noqa: E731
        db = tmp_path / "agentconf.db"
        unit = create_core_unit(
            ability_core_config(llm_factory=injected, agentconf_db_path=str(db))
        )
        await unit.init(FakeCtx())
        try:
            assert unit.supervisor._llm_factory is injected  # noqa: SLF001
        finally:
            await unit.stop()
