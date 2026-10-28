from __future__ import annotations

import logging

import yaml
from ghrah.scheme.loading.ability_catalog import load_all_builtin_manifests
from ghrah.scheme.loading.ability_view import AbilityManifest

from ghrah.subject.manifest_store.store import ManifestStore

logger = logging.getLogger(__name__)

__all__ = ["ensure_builtins"]


def _manifest_to_yaml(manifest: AbilityManifest) -> str:
    """将 AbilityManifest 序列化为 YAML 字符串。"""
    data = manifest.model_dump(mode="python")
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def ensure_builtins(store: ManifestStore) -> list[str]:
    """确保内置能力 Manifest 存在于 ManifestStore 中。

    对每个 builtin manifest，检查是否已存在。
    不存在则调用 store.put_ability() 写入。
    已存在则跳过（幂等）。

    Returns:
        写入的 full_name 列表。
    """
    builtins = load_all_builtin_manifests()
    written: list[str] = []

    for full_name, manifest in builtins.items():
        try:
            store.get_ability(full_name)
        except Exception:
            content = _manifest_to_yaml(manifest)
            store.put_ability(full_name, content)
            written.append(full_name)
            logger.info("Seeded builtin manifest: %s", full_name)

    return written
