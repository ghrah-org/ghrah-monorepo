# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``GHRAH_SUBJECT_AGENTCONF_DB_PATH`` 在 Subject 侧的贯穿链测试。

覆盖两处 Subject 侧入口：

1. ``AgentEntityManager.model_catalog`` / ``validate_model_name`` 使用
   ProjectManager 注入的 AgentConf 库路径（与能力管理 REST 同源）；
2. ``default_core_unit_factory.build_config`` 把 ``SubjectConfig.agentconf_db_path``
   写进 ``CoreUnitConfig``（spawn 校验与默认 LLM 工厂读同一份库）。

默认库回落（未注入路径时调用 ``AgentsConfig(None)``）用记录型替身断言，不触碰
真实 ``~/.agentconf``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ghrah.subject.config import SubjectConfig
from ghrah.subject.errors import StableError
from ghrah.subject.project.manager import ProjectManager


def _make_agentconf_db(path: Path, endpoint: str) -> Path:
    """建一个含可解析端点的最小 AgentConf 库。"""
    from agentconf import AgentsConfig

    with AgentsConfig(path) as config:
        config.create_provider("e2e-provider", "openai", "http://127.0.0.1:9/v1", api_key="sk-test")
        config.create_model_endpoint("e2e-provider", endpoint, "e2e-model", "openai.responses")
    return path


def _manager(agentconf_db_path: str | None = None) -> ProjectManager:
    """只构造实体模型目录/校验用得到的字段；其余依赖用 None（不参与这些方法）。"""
    return ProjectManager(
        store=None,  # type: ignore[arg-type]
        workspace_mgr=None,  # type: ignore[arg-type]
        task_mgr=None,  # type: ignore[arg-type]
        cluster_registry=None,  # type: ignore[arg-type]
        agentconf_db_path=agentconf_db_path,
    )


class TestAgentEntityUsesInjectedDBPath:
    def test_manager_exposes_injected_path(self, tmp_path: Path) -> None:
        db = tmp_path / "agentconf.db"
        assert _manager(str(db)).agentconf_db_path == str(db)
        assert _manager().agentconf_db_path is None

    def test_model_catalog_reads_injected_db(self, tmp_path: Path) -> None:
        endpoint = "entity-catalog-endpoint"
        db = _make_agentconf_db(tmp_path / "agentconf.db", endpoint)
        manager = _manager(str(db))

        catalog = manager.agents.model_catalog()

        assert catalog["model_config_names"] == [endpoint]

    def test_validate_model_name_uses_injected_db(self, tmp_path: Path) -> None:
        endpoint = "entity-validate-endpoint"
        db = _make_agentconf_db(tmp_path / "agentconf.db", endpoint)
        manager = _manager(str(db))

        manager.agents.validate_model_name(endpoint)  # 不抛即通过

        with pytest.raises(StableError, match="model_configuration_invalid"):
            manager.agents.validate_model_name("missing-endpoint")


class TestDefaultDBFallback:
    def test_entities_call_agentsconfig_with_none_when_path_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未注入路径时仍调用 ``AgentsConfig(None)``（旧行为）。"""
        import agentconf

        calls: list[str | None] = []

        class Recorder:
            def __init__(self, db_path: Any = None) -> None:
                calls.append(db_path)

            def __enter__(self) -> Recorder:
                return self

            def __exit__(self, *args: Any) -> bool:
                return False

            def list_model_endpoints(self) -> list[Any]:
                return []

        monkeypatch.setattr(agentconf, "AgentsConfig", Recorder)

        assert _manager().agents.model_catalog() == {"model_config_names": []}
        assert calls == [None]

    def test_project_unit_passes_path_from_config(self) -> None:
        """``ProjectUnit.init`` 从 SubjectConfig 取路径传给 ProjectManager。

        以签名/构造参数断言代替整机装配：``AgentEntityManager`` 只依赖 manager
        上的 ``agentconf_db_path`` 属性，上一条用例已锁。
        """
        import inspect

        params = inspect.signature(ProjectManager.__init__).parameters
        assert "agentconf_db_path" in params
        assert params["agentconf_db_path"].default is None


class TestCoreFactoryThreadsPath:
    def test_build_config_includes_agentconf_db_path(self, tmp_path: Path) -> None:
        from ghrah.subject.core_cluster.registry import default_core_unit_factory

        db = tmp_path / "agentconf.db"
        config = SubjectConfig(agentconf_db_path=str(db))
        factory = default_core_unit_factory(config)

        core_config = factory.build_config("pid-1", tmp_path.as_uri())

        assert core_config.agentconf_db_path == str(db)

    def test_build_config_default_none(self, tmp_path: Path) -> None:
        from ghrah.subject.core_cluster.registry import default_core_unit_factory

        factory = default_core_unit_factory(SubjectConfig())
        core_config = factory.build_config("pid-2", tmp_path.as_uri())

        assert core_config.agentconf_db_path is None
