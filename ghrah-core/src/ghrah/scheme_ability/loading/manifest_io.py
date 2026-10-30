# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""声明的来源：文本 → 模型（解析）、按名取（内置 store）、取用接口（协议）。

由原 ``manifest/{protocols,parser,store}.py`` 合并——三者都在回答
「这份声明从哪来、怎么拿到」。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

import yaml
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ghrah.scheme_ability.agent_manifest import AgentManifest
from ghrah.scheme_ability.loading.ability_view import AbilityManifest
from ghrah.scheme_ability.loading.manifest_fields import (
    ManifestNotFoundError,
    ManifestValidationError,
    ManifestVersionError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


__all__ = [
    "BuiltinManifestStore",
    "ManifestStoreProtocol",
    "parse_ability_manifest",
    "parse_agent_manifest",
    "validate_manifest",
]


class ManifestStoreProtocol(Protocol):
    """Manifest 存储能力接口（Core 侧不关心具体实现）。"""

    def load_ability(self, full_name: str) -> AbilityManifest:
        """根据 full_name 加载 AbilityManifest。"""
        ...

    def load_agent(self, full_name: str) -> AgentManifest:
        """根据 full_name 加载 AgentManifest。"""
        ...

    def list_abilities(self, namespace: str | None = None) -> list[str]:
        """列出所有能力 full_name，可选按 namespace 过滤。"""
        ...

    def list_agents(self, namespace: str | None = None) -> list[str]:
        """列出所有 agent full_name，可选按 namespace 过滤。"""
        ...


def parse_ability_manifest(raw: str, format: str = "yaml") -> AbilityManifest:
    data = _load_raw(raw, format)
    try:
        return AbilityManifest.model_validate(data)
    except (PydanticValidationError, ManifestVersionError) as exc:
        raise ManifestValidationError(str(exc)) from exc


def parse_agent_manifest(raw: str, format: str = "yaml") -> AgentManifest:
    data = _load_raw(raw, format)
    try:
        return AgentManifest.model_validate(data)
    except (PydanticValidationError, ManifestVersionError) as exc:
        raise ManifestValidationError(str(exc)) from exc


def validate_manifest(raw: str, format: str = "yaml") -> tuple[bool, list[str]]:
    try:
        data = _load_raw(raw, format)
    except Exception as exc:
        return False, [f"Parse error: {exc}"]

    manifest_type = data.get("manifest", "")
    if manifest_type == "ability":
        return _validate_model(data, AbilityManifest)
    elif manifest_type == "agent":
        return _validate_model(data, AgentManifest)
    else:
        return False, [f"Unknown or missing manifest type: {manifest_type!r}"]


def _load_raw(raw: str, format: str) -> dict[str, Any]:
    if format == "yaml":
        result = yaml.safe_load(raw)
        if not isinstance(result, dict):
            raise ValueError("YAML content is not a mapping")
        return result
    elif format == "json":
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("JSON content is not a mapping")
        return data
    else:
        raise ValueError(f"Unsupported format: {format!r}")


def _validate_model(data: dict[str, Any], model_cls: type[BaseModel]) -> tuple[bool, list[str]]:
    try:
        model_cls.model_validate(data)
        return True, []
    except ManifestVersionError as exc:
        return False, [str(exc)]
    except PydanticValidationError as exc:
        errors = [f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return False, errors


class BuiltinManifestStore:
    """基于 builtins 能力包的 ManifestStore 实现。

    仅支持 load_ability，不支持持久化写入（测试与基本集成用）。
    完整 ManifestStore 实现在 ghrah-subject 中提供文件系统 CRUD。

    **不缓存**：每次访问都重新扫盘投影（热插拔），外部新增能力包在下一次
    查询即生效。
    """

    @property
    def _builtins(self) -> Mapping[str, AbilityManifest]:
        # 惰性 import：builtins 门面依赖能力目录，而本模块经 manifest/__init__
        # 参与包初始化，模块级 import 会形成 builtins ↔ store 的初始化环。
        from ghrah.scheme_ability.loading.ability_catalog import load_all_builtin_manifests

        return load_all_builtin_manifests()

    def load_ability(self, full_name: str) -> AbilityManifest:
        try:
            return self._builtins[full_name]
        except KeyError:
            raise ManifestNotFoundError(f"Ability manifest not found: {full_name}")

    def load_agent(self, full_name: str) -> AgentManifest:
        raise ManifestNotFoundError(f"Agent manifest not found: {full_name}")

    def list_abilities(self, namespace: str | None = None) -> list[str]:
        names = list(self._builtins.keys())
        if namespace:
            names = [n for n in names if n.startswith(f"{namespace}.")]
        return names

    def list_agents(self, namespace: str | None = None) -> list[str]:
        return []
