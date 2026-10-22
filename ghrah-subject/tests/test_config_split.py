"""SubjectConfig 配置拆分测试。

覆盖：
- flat 字段向后兼容（旧构造调用零改动）
- slice 派生关系（未显式传入时由 flat 字段派生）
- slice 显式覆盖
- slice property 非 Optional（mypy 友好）
- transport kind / enabled_third_party_units 默认值
- from_env() 扩展（新 env var + 回退行为）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from ghrah.subject.config import (
    CoreConnectionConfig,
    CoreTransportConfig,
    HITLPolicyConfig,
    ManifestConfig,
    PersistenceConfig,
    PluginTrustConfig,
    SandboxUnitConfig,
    SubjectConfig,
    TransportKindConfig,
)


class TestFlatFieldCompat:
    """旧 flat 字段保留，旧构造调用零改动。"""

    def test_default_flat_fields_present(self) -> None:
        config = SubjectConfig()
        assert config.workspace_root is not None and config.workspace_root != ""
        assert config.db_path is not None and config.db_path != ""
        assert config.manifest_root is not None and config.manifest_root != ""
        assert isinstance(config.hitl_policy, HITLPolicyConfig)
        assert isinstance(config.core, CoreTransportConfig)
        assert config.log_level == "INFO"

    def test_core_connection_config_is_alias_of_core_transport_config(self) -> None:
        # 别名兼容：CoreConnectionConfig 即 CoreTransportConfig
        assert CoreConnectionConfig is CoreTransportConfig

    def test_core_connection_config_constructible(self) -> None:
        # 旧外部代码 CoreConnectionConfig(url=..., command_timeout=...) 仍可用
        cc = CoreConnectionConfig(url="ws://example:1234/ws", command_timeout=42)
        assert cc.url == "ws://example:1234/ws"
        assert cc.command_timeout == 42

    def test_legacy_construction_unchanged(self, tmp_path: Path) -> None:
        # 模拟 scripts/start_all.py:185 的构造调用（零改动）
        config = SubjectConfig(
            workspace_root=str(tmp_path / "ws"),
            db_path=str(tmp_path / "subject.db"),
            hitl_policy=HITLPolicyConfig(
                auto_approve_abilities=["conversation", "read_file"],
                require_approval_by_default=True,
            ),
            core=CoreConnectionConfig(
                url="ws://core:4111/ws",
                command_timeout=300,
            ),
        )
        assert config.workspace_root == str(tmp_path / "ws")
        assert config.db_path == str(tmp_path / "subject.db")
        assert config.core.url == "ws://core:4111/ws"
        assert config.core.command_timeout == 300
        assert config.hitl_policy.auto_approve_abilities == ["conversation", "read_file"]


class TestSliceDerivation:
    """未显式传入 slice 时，由 flat 字段派生。"""

    def test_persistence_slice_derived(self, tmp_path: Path) -> None:
        config = SubjectConfig(db_path=str(tmp_path / "test.db"))
        assert config.persistence.db_path == str(tmp_path / "test.db")

    def test_sandbox_slice_derived_from_workspace_root(self, tmp_path: Path) -> None:
        config = SubjectConfig(workspace_root=str(tmp_path / "ws"))
        assert config.sandbox.workspace_root == str(tmp_path / "ws")

    def test_sandbox_default_timeout_derives_from_core(self) -> None:
        # 默认派生：sandbox.default_timeout == core.command_timeout
        config = SubjectConfig()
        assert config.sandbox.default_timeout == config.core.command_timeout

    def test_sandbox_default_timeout_derives_from_custom_core(self) -> None:
        config = SubjectConfig(core=CoreTransportConfig(command_timeout=99.0))
        assert config.sandbox.default_timeout == 99.0

    def test_manifest_slice_derived(self, tmp_path: Path) -> None:
        config = SubjectConfig(manifest_root=str(tmp_path / "manifests"))
        assert config.manifest.manifest_root == str(tmp_path / "manifests")

    def test_hitl_slice_derived_from_hitl_policy(self) -> None:
        policy = HITLPolicyConfig(auto_approve_abilities=["x"])
        config = SubjectConfig(hitl_policy=policy)
        # hitl slice 从 hitl_policy flat 字段派生（同一对象）
        assert config.hitl is policy


class TestSliceExplicitOverride:
    """slice 可显式传入，覆盖 flat 派生。"""

    def test_persistence_slice_explicit(self, tmp_path: Path) -> None:
        config = SubjectConfig(
            db_path=str(tmp_path / "flat.db"),
            persistence_slice=PersistenceConfig(db_path=str(tmp_path / "slice.db")),
        )
        assert config.persistence.db_path == str(tmp_path / "slice.db")

    def test_sandbox_slice_explicit(self, tmp_path: Path) -> None:
        config = SubjectConfig(
            workspace_root=str(tmp_path / "flat-ws"),
            sandbox_slice=SandboxUnitConfig(
                workspace_root=str(tmp_path / "slice-ws"),
                default_timeout=42.0,
            ),
        )
        assert config.sandbox.workspace_root == str(tmp_path / "slice-ws")
        assert config.sandbox.default_timeout == 42.0

    def test_manifest_slice_explicit(self, tmp_path: Path) -> None:
        config = SubjectConfig(
            manifest_root=str(tmp_path / "flat-man"),
            manifest_slice=ManifestConfig(manifest_root=str(tmp_path / "slice-man")),
        )
        assert config.manifest.manifest_root == str(tmp_path / "slice-man")

    def test_hitl_slice_explicit(self) -> None:
        slice_policy = HITLPolicyConfig(auto_approve_abilities=["slice-ability"])
        flat_policy = HITLPolicyConfig(auto_approve_abilities=["flat-ability"])
        config = SubjectConfig(
            hitl_policy=flat_policy,
            hitl_slice=slice_policy,
        )
        # 显式传入的 slice 优先
        assert config.hitl is slice_policy
        # flat 字段不变
        assert config.hitl_policy is flat_policy


class TestSliceNonOptional:
    """slice property 非 Optional（mypy strict 友好）。"""

    def test_persistence_property_non_optional(self) -> None:
        config = SubjectConfig()
        assert type(config.persistence) is PersistenceConfig

    def test_sandbox_property_non_optional(self) -> None:
        config = SubjectConfig()
        assert type(config.sandbox) is SandboxUnitConfig

    def test_manifest_property_non_optional(self) -> None:
        config = SubjectConfig()
        assert type(config.manifest) is ManifestConfig

    def test_hitl_property_non_optional(self) -> None:
        config = SubjectConfig()
        assert type(config.hitl) is HITLPolicyConfig


class TestTransportKind:
    """transport kind 配置。"""

    def test_default_transport(self) -> None:
        config = SubjectConfig()
        assert config.transport.core == "websocket"
        assert config.transport.observer == "websocket"

    def test_transport_explicit(self) -> None:
        config = SubjectConfig(
            transport=TransportKindConfig(core="grpc", observer="http"),
        )
        assert config.transport.core == "grpc"
        assert config.transport.observer == "http"


class TestEnabledThirdPartyUnits:
    """enabled_third_party_units allowlist。"""

    def test_default_empty_allowlist(self) -> None:
        config = SubjectConfig()
        assert config.enabled_third_party_units == []

    def test_explicit_allowlist(self) -> None:
        config = SubjectConfig(enabled_third_party_units=["foo", "bar"])
        assert config.enabled_third_party_units == ["foo", "bar"]


class TestFromEnv:
    """from_env() 环境变量加载 + 回退行为。"""

    def test_from_env_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # 清除所有相关 env，验证默认值
        env_keys = [
            "GHRAH_SUBJECT_WORKSPACE_ROOT",
            "GHRAH_SUBJECT_DB_PATH",
            "GHRAH_SUBJECT_MANIFEST_ROOT",
            "GHRAH_SUBJECT_LOG_LEVEL",
            "GHRAH_SUBJECT_CORE_URL",
            "GHRAH_SUBJECT_CORE_COMMAND_TIMEOUT",
            "GHRAH_SUBJECT_SANDBOX_DEFAULT_TIMEOUT",
            "GHRAH_SUBJECT_ABILITY_HITL_TIMEOUT",
            "GHRAH_SUBJECT_TRANSPORT_CORE_KIND",
            "GHRAH_SUBJECT_TRANSPORT_OBSERVER_KIND",
            "GHRAH_SUBJECT_ENABLED_UNITS",
            "GHRAH_SUBJECT_PROJECT_DEFAULT_ROOT_LOCATOR_TEMPLATE",
        ]
        for key in env_keys:
            monkeypatch.delenv(key, raising=False)

        caplog.set_level(logging.WARNING, logger="ghrah.subject.config")

        config = SubjectConfig.from_env()
        assert config.log_level == "INFO"
        assert config.core.url == "ws://localhost:4111/ws"
        assert config.transport.core == "websocket"
        assert config.transport.observer == "websocket"
        assert config.enabled_third_party_units == []
        assert config.project.default_root_locator_template == "~/.ghrah/projects/{project_id}"
        assert "GHRAH_SUBJECT_SANDBOX_DEFAULT_TIMEOUT" not in caplog.text

    def test_from_env_flat_fields(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_WORKSPACE_ROOT", str(tmp_path / "env-ws"))
        monkeypatch.setenv("GHRAH_SUBJECT_DB_PATH", str(tmp_path / "env.db"))
        monkeypatch.setenv("GHRAH_SUBJECT_MANIFEST_ROOT", str(tmp_path / "env-man"))
        monkeypatch.setenv("GHRAH_SUBJECT_LOG_LEVEL", "DEBUG")

        config = SubjectConfig.from_env()
        assert config.workspace_root == str(tmp_path / "env-ws")
        assert config.db_path == str(tmp_path / "env.db")
        assert config.manifest_root == str(tmp_path / "env-man")
        assert config.log_level == "DEBUG"

    def test_from_env_project_root_template(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "GHRAH_SUBJECT_PROJECT_DEFAULT_ROOT_LOCATOR_TEMPLATE",
            "/srv/ghrah/projects/{project_id}",
        )
        config = SubjectConfig.from_env()
        assert config.project.default_root_locator_template == "/srv/ghrah/projects/{project_id}"

    def test_from_env_sandbox_timeout_fallback_to_core(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 不设 SANDBOX_DEFAULT_TIMEOUT，应回退到 core.command_timeout
        monkeypatch.setenv("GHRAH_SUBJECT_CORE_COMMAND_TIMEOUT", "123.0")
        monkeypatch.delenv("GHRAH_SUBJECT_SANDBOX_DEFAULT_TIMEOUT", raising=False)

        config = SubjectConfig.from_env()
        assert config.sandbox.default_timeout == 123.0

    def test_from_env_sandbox_timeout_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_CORE_COMMAND_TIMEOUT", "123.0")
        monkeypatch.setenv("GHRAH_SUBJECT_SANDBOX_DEFAULT_TIMEOUT", "456.0")

        config = SubjectConfig.from_env()
        assert config.sandbox.default_timeout == 456.0

    def test_from_env_transport_kind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_TRANSPORT_CORE_KIND", "grpc")
        monkeypatch.setenv("GHRAH_SUBJECT_TRANSPORT_OBSERVER_KIND", "http")

        config = SubjectConfig.from_env()
        assert config.transport.core == "grpc"
        assert config.transport.observer == "http"

    def test_from_env_enabled_units_csv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_ENABLED_UNITS", "foo, bar ,baz")

        config = SubjectConfig.from_env()
        assert config.enabled_third_party_units == ["foo", "bar", "baz"]

    def test_from_env_enabled_units_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GHRAH_SUBJECT_ENABLED_UNITS", raising=False)

        config = SubjectConfig.from_env()
        assert config.enabled_third_party_units == []

    def test_from_env_hitl_alias_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 回归保护：GHRAH_SUBJECT_HITL_* 行为不变
        monkeypatch.setenv("GHRAH_SUBJECT_HITL_AUTO_APPROVE_ABILITIES", "a,b")
        monkeypatch.setenv("GHRAH_SUBJECT_HITL_REQUIRE_APPROVAL", "false")
        monkeypatch.setenv("GHRAH_SUBJECT_HITL_ALLOWED_PATHS", "/p1;/p2")
        monkeypatch.setenv("GHRAH_SUBJECT_HITL_WORKSPACE_ROOT", "/hitl-ws")

        config = SubjectConfig.from_env()
        assert config.hitl_policy.auto_approve_abilities == ["a", "b"]
        assert config.hitl_policy.require_approval_by_default is False
        assert config.hitl_policy.allowed_paths == ["/p1", "/p2"]
        assert config.hitl_policy.workspace_root == "/hitl-ws"
        # hitl slice 与 flat 字段一致（from_env 未显式传 hitl_slice，由 flat 派生）
        assert config.hitl is config.hitl_policy

    def test_from_env_sandbox_workspace_derived(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_WORKSPACE_ROOT", str(tmp_path / "env-ws2"))

        config = SubjectConfig.from_env()
        assert config.sandbox.workspace_root == str(tmp_path / "env-ws2")

    def test_from_env_safe_extra_commands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """safe 白名单扩展 env：GHRAH_SUBJECT_SAFE_EXTRA_COMMANDS / _SUB_COMMANDS。"""
        monkeypatch.setenv("GHRAH_SUBJECT_SAFE_EXTRA_COMMANDS", "pnpm, make; just")
        monkeypatch.setenv("GHRAH_SUBJECT_SAFE_EXTRA_SUB_COMMANDS", "pnpm:test,type-check; uv:run")

        config = SubjectConfig.from_env()
        assert config.hitl_policy.safe_extra_commands == ["pnpm", "make", "just"]
        assert config.hitl_policy.safe_extra_sub_commands == {
            "pnpm": ["test", "type-check"],
            "uv": ["run"],
        }

    def test_from_env_safe_extra_commands_default_empty(self) -> None:
        config = SubjectConfig.from_env()
        assert config.hitl_policy.safe_extra_commands == []
        assert config.hitl_policy.safe_extra_sub_commands == {}

    def test_from_env_hitl_circuit_defaults(self) -> None:
        """连续超时熔断 env：默认禁用（零隐式），显式设置生效。"""
        config = SubjectConfig.from_env()
        assert config.hitl_policy.hitl_consecutive_timeout_limit == 0
        assert config.hitl_policy.hitl_degraded_timeout == 30.0

    def test_from_env_hitl_circuit_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_HITL_CONSECUTIVE_TIMEOUT_LIMIT", "3")
        monkeypatch.setenv("GHRAH_SUBJECT_HITL_DEGRADED_TIMEOUT", "15.5")

        config = SubjectConfig.from_env()
        assert config.hitl_policy.hitl_consecutive_timeout_limit == 3
        assert config.hitl_policy.hitl_degraded_timeout == 15.5

    def test_from_env_environment_injection_default_off(self) -> None:
        """环境注入 env：默认关（零隐式）。"""
        config = SubjectConfig.from_env()
        assert config.hitl_policy.environment_injection is False

    def test_from_env_environment_injection_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_ENVIRONMENT_INJECTION", "true")
        config = SubjectConfig.from_env()
        assert config.hitl_policy.environment_injection is True

    def test_from_env_environment_injection_truthy_variants(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """truthy 变体（1/yes）识别；非 truthy 值保持关闭。"""
        monkeypatch.setenv("GHRAH_SUBJECT_ENVIRONMENT_INJECTION", "1")
        assert SubjectConfig.from_env().hitl_policy.environment_injection is True
        monkeypatch.setenv("GHRAH_SUBJECT_ENVIRONMENT_INJECTION", "Yes")
        assert SubjectConfig.from_env().hitl_policy.environment_injection is True
        monkeypatch.setenv("GHRAH_SUBJECT_ENVIRONMENT_INJECTION", "false")
        assert SubjectConfig.from_env().hitl_policy.environment_injection is False

    def test_plugin_trust_default_empty(self) -> None:
        """plugin_trust slice：默认空清单（零隐式，发现 ≠ 信任）。"""
        config = SubjectConfig()
        assert config.plugin_trust.discoverable == []
        assert config.plugin_trust.trust_set == frozenset()

    def test_from_env_plugin_trust(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_PLUGIN_TRUST", "task-commit-attribution, demo-plugin")
        config = SubjectConfig.from_env()
        assert config.plugin_trust.discoverable == [
            "task-commit-attribution",
            "demo-plugin",
        ]

    def test_from_env_plugin_trust_default_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GHRAH_SUBJECT_PLUGIN_TRUST", raising=False)
        assert SubjectConfig.from_env().plugin_trust.discoverable == []

    def test_plugin_trust_slice_explicit(self) -> None:
        config = SubjectConfig(
            plugin_trust_slice=PluginTrustConfig(discoverable=["a", "b"]),
        )
        assert config.plugin_trust.trust_set == frozenset({"a", "b"})

    def test_taskstore_defaults(self) -> None:
        """taskstore slice：默认启用 + 默认路径（零隐式，enabled=False 不挂载 unit）。"""
        config = SubjectConfig()
        assert config.taskstore.enabled is True
        assert config.taskstore.db_path.endswith("taskstore.sqlite3")

    def test_from_env_taskstore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_TASKSTORE_ENABLED", "false")
        monkeypatch.setenv("GHRAH_SUBJECT_TASKSTORE_DB", "/tmp/ts/x.sqlite3")
        config = SubjectConfig.from_env()
        assert config.taskstore.enabled is False
        assert config.taskstore.db_path == "/tmp/ts/x.sqlite3"

    def test_from_env_taskstore_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GHRAH_SUBJECT_TASKSTORE_ENABLED", raising=False)
        monkeypatch.delenv("GHRAH_SUBJECT_TASKSTORE_DB", raising=False)
        config = SubjectConfig.from_env()
        assert config.taskstore.enabled is True

    def test_skill_defaults(self) -> None:
        """skill slice：默认根目录是 skill-ability（测试期由 conftest 隔离为临时目录）。"""
        config = SubjectConfig()
        assert config.skill.root.endswith("skill-ability")

    def test_from_env_skill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHRAH_SUBJECT_SKILL_ROOT", "/srv/ghrah/skill-ability")
        assert SubjectConfig.from_env().skill.root == "/srv/ghrah/skill-ability"

    def test_from_env_skill_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """delenv 后断言生产默认值（from_env 显式传字面量，不受 conftest 覆写影响）。"""
        monkeypatch.delenv("GHRAH_SUBJECT_SKILL_ROOT", raising=False)
        assert SubjectConfig.from_env().skill.root == os.path.expanduser("~/.ghrah/skill-ability")
