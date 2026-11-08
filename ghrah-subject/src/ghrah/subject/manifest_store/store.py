# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import re
from pathlib import Path

from ghrah.manifest.agent import AgentManifest
from ghrah.manifest.errors import (
    DuplicateManifestError,
    ManifestNotFoundError,
    ManifestValidationError,
)
from ghrah.manifest.parser import parse_agent_manifest, validate_manifest

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
    """Subject 的 Agent 模板文件库。"""

    def __init__(self, root: str | Path) -> None:
        self._agents_dir = Path(root) / "agents"

    def ensure_dirs(self) -> None:
        """准备 Agent 模板目录。"""
        self._agents_dir.mkdir(parents=True, exist_ok=True)

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
        self._put_entry(full_name, content, overwrite)

    def delete_agent(self, full_name: str) -> None:
        """删除 Agent Manifest 文件。"""
        self._delete_entry(full_name)

    def validate_agent(self, content: str) -> tuple[bool, list[str]]:
        """仅校验 Agent Manifest 内容（不写盘）。"""
        return validate_manifest(content)

    # ─── 通用校验 ───

    def validate_manifest(self, content: str) -> tuple[bool, list[str]]:
        """校验 Agent 模板内容。"""
        return validate_manifest(content)

    # ─── 内部工具方法 ───

    def _agent_path(self, full_name: str) -> Path:
        namespace, name = _full_name_to_path_parts(full_name)
        return self._agents_dir / namespace / f"{name}.yaml"

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

    def _put_entry(self, full_name: str, content: str, overwrite: bool) -> None:
        _validate_full_name(full_name)
        valid, errors = validate_manifest(content)
        if not valid:
            raise ManifestValidationError(f"Invalid manifest: {'; '.join(errors)}")

        path = self._agent_path(full_name)
        if path.exists() and not overwrite:
            raise DuplicateManifestError(f"Agent manifest already exists: {full_name}")

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
        logger.info("Wrote Agent manifest: %s", full_name)

    def _delete_entry(self, full_name: str) -> None:
        path = self._agent_path(full_name)
        if not path.exists():
            raise ManifestNotFoundError(f"Agent manifest not found: {full_name}")
        path.unlink()
        logger.info("Deleted Agent manifest: %s", full_name)
