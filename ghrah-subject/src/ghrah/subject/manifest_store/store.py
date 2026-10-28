from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from ghrah.scheme.agent_manifest import AgentManifest
from ghrah.scheme.loading.ability_view import AbilityManifest
from ghrah.scheme.loading.manifest_fields import (
    DuplicateManifestError,
    ManifestNotFoundError,
    ManifestValidationError,
)
from ghrah.scheme.loading.manifest_io import (
    parse_ability_manifest,
    parse_agent_manifest,
    validate_manifest,
)

from ghrah.subject._fs import atomic_write_text

logger = logging.getLogger(__name__)

__all__ = ["ManifestStore"]


def _full_name_to_path_parts(full_name: str) -> tuple[str, str]:
    """将 full_name 分解为 (namespace, name)。

    'ghrah.fs.read_file' -> ('ghrah.fs', 'read_file')
    'conversation' -> ('default', 'conversation')
    """
    if "." in full_name:
        namespace, name = full_name.rsplit(".", 1)
    else:
        namespace, name = "default", full_name
    return namespace, name


_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_.]*[a-z0-9]$")
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_full_name(full_name: str) -> None:
    """校验 full_name 格式合法性。"""
    namespace, name = _full_name_to_path_parts(full_name)
    if not _NAMESPACE_PATTERN.match(namespace):
        raise ManifestValidationError(f"Invalid namespace in full_name: {namespace!r}")
    if not _NAME_PATTERN.match(name):
        raise ManifestValidationError(f"Invalid name in full_name: {name!r}")


class ManifestStore:
    """Subject 端的 Manifest 文件存储，实现 ManifestStoreProtocol。

    目录结构:
        {root}/abilities/{namespace}/{name}.yaml
        {root}/agents/{namespace}/{name}.yaml
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._abilities_dir = self._root / "abilities"
        self._agents_dir = self._root / "agents"

    def ensure_dirs(self) -> None:
        """确保存储目录结构存在。"""
        self._abilities_dir.mkdir(parents=True, exist_ok=True)
        self._agents_dir.mkdir(parents=True, exist_ok=True)

    # ─── Ability 操作 ───

    def list_abilities(self, namespace: str | None = None) -> list[str]:
        """列出所有能力 full_name，可选按 namespace 过滤。"""
        return self._list_entries(self._abilities_dir, namespace)

    def get_ability(self, full_name: str) -> AbilityManifest:
        """加载完整 AbilityManifest（含校验）。"""
        path = self._ability_path(full_name)
        if not path.exists():
            raise ManifestNotFoundError(f"Ability manifest not found: {full_name}")
        content = path.read_text(encoding="utf-8")
        return parse_ability_manifest(content)

    def get_ability_source(self, full_name: str) -> str:
        """返回 Ability Manifest 的原始 YAML 源文本。"""
        path = self._ability_path(full_name)
        if not path.exists():
            raise ManifestNotFoundError(f"Ability manifest not found: {full_name}")
        return path.read_text(encoding="utf-8")

    def load_ability(self, full_name: str) -> AbilityManifest:
        """ManifestStoreProtocol.load_ability 委托给 get_ability。"""
        return self.get_ability(full_name)

    def put_ability(self, full_name: str, content: str, overwrite: bool = False) -> None:
        """创建或更新 Ability Manifest（先校验后写盘）。"""
        self._put_entry(full_name, content, "abilities", overwrite)

    def delete_ability(self, full_name: str) -> None:
        """删除 Ability Manifest 文件。"""
        self._delete_entry(full_name, "abilities")

    def validate_ability(self, content: str) -> tuple[bool, list[str]]:
        """仅校验 Ability Manifest 内容（不写盘）。"""
        return validate_manifest(content)

    # ─── Agent 操作 ───

    def list_agents(self, namespace: str | None = None) -> list[str]:
        """列出所有 agent full_name，可选按 namespace 过滤。"""
        return self._list_entries(self._agents_dir, namespace)

    def get_agent(self, full_name: str) -> AgentManifest:
        """加载完整 AgentManifest（含校验）。"""
        path = self._agent_path(full_name)
        if not path.exists():
            raise ManifestNotFoundError(f"Agent manifest not found: {full_name}")
        content = path.read_text(encoding="utf-8")
        return parse_agent_manifest(content)

    def get_agent_source(self, full_name: str) -> str:
        """返回 Agent Manifest 的原始 YAML 源文本。"""
        path = self._agent_path(full_name)
        if not path.exists():
            raise ManifestNotFoundError(f"Agent manifest not found: {full_name}")
        return path.read_text(encoding="utf-8")

    def load_agent(self, full_name: str) -> AgentManifest:
        """ManifestStoreProtocol.load_agent 委托给 get_agent。"""
        return self.get_agent(full_name)

    def put_agent(self, full_name: str, content: str, overwrite: bool = False) -> None:
        """创建或更新 Agent Manifest（先校验后写盘）。"""
        self._put_entry(full_name, content, "agents", overwrite)

    def delete_agent(self, full_name: str) -> None:
        """删除 Agent Manifest 文件。"""
        self._delete_entry(full_name, "agents")

    def validate_agent(self, content: str) -> tuple[bool, list[str]]:
        """仅校验 Agent Manifest 内容（不写盘）。"""
        return validate_manifest(content)

    # ─── 通用校验 ───

    def validate_manifest(self, content: str) -> tuple[bool, list[str]]:
        """校验 Manifest 内容（自动识别 ability/agent 类型）。"""
        return validate_manifest(content)

    # ─── 内部工具方法 ───

    def _ability_path(self, full_name: str) -> Path:
        """full_name = 'ghrah.fs.read_file' -> abilities/ghrah.fs/read_file.yaml"""
        return self._full_name_to_path(full_name, "abilities")

    def _agent_path(self, full_name: str) -> Path:
        """full_name = 'my_project.dev_agent' -> agents/my_project/dev_agent.yaml"""
        return self._full_name_to_path(full_name, "agents")

    def _full_name_to_path(self, full_name: str, kind: str) -> Path:
        """将 full_name 映射到文件系统路径。

        Args:
            full_name: 如 'ghrah.fs.read_file' 或 'conversation'
            kind: 'abilities' 或 'agents'
        """
        namespace, name = _full_name_to_path_parts(full_name)
        base = self._abilities_dir if kind == "abilities" else self._agents_dir
        return base / namespace / f"{name}.yaml"

    def _list_entries(self, base_dir: Path, namespace: str | None = None) -> list[str]:
        """列出目录中的所有 manifest full_name。"""
        if not base_dir.exists():
            return []

        entries: list[str] = []
        for ns_dir in sorted(base_dir.iterdir()):
            if not ns_dir.is_dir():
                continue
            ns_name = ns_dir.name
            if namespace is not None and ns_name != namespace:
                continue
            for yaml_file in sorted(ns_dir.glob("*.yaml")):
                entries.append(f"{ns_name}.{yaml_file.stem}")

        return entries

    def _put_entry(
        self,
        full_name: str,
        content: str,
        dir_kind: str,
        overwrite: bool,
    ) -> None:
        """创建或更新 Manifest 条目。

        Args:
            full_name: Manifest 的 full_name
            content: YAML 内容
            dir_kind: 'abilities' 或 'agents'（目录名）
            overwrite: 是否覆盖已存在的条目
        """
        _validate_full_name(full_name)

        is_valid, errors = validate_manifest(content)
        if not is_valid:
            raise ManifestValidationError(f"Invalid manifest: {'; '.join(errors)}")

        data = yaml.safe_load(content)
        manifest_type = data.get("manifest", "")
        expected_type = "ability" if dir_kind == "abilities" else "agent"
        if manifest_type != expected_type:
            raise ManifestValidationError(
                f"Expected manifest type '{expected_type}', got {manifest_type!r}"
            )

        path = self._full_name_to_path(full_name, dir_kind)
        if path.exists() and not overwrite:
            raise DuplicateManifestError(
                f"{expected_type.capitalize()} manifest already exists: {full_name}"
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
        logger.info("Wrote %s manifest: %s", expected_type, full_name)

    def _delete_entry(self, full_name: str, dir_kind: str) -> None:
        """删除 Manifest 条目文件。

        Args:
            full_name: Manifest 的 full_name
            dir_kind: 'abilities' 或 'agents'（目录名）
        """
        path = self._full_name_to_path(full_name, dir_kind)
        if not path.exists():
            kind_name = "Ability" if dir_kind == "abilities" else "Agent"
            raise ManifestNotFoundError(f"{kind_name} manifest not found: {full_name}")
        path.unlink()
        kind_name = "ability" if dir_kind == "abilities" else "agent"
        logger.info("Deleted %s manifest: %s", kind_name, full_name)
