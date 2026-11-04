# SPDX-License-Identifier: Apache-2.0
"""显式取得文件能力定义与实现版本；查询不访问来源。"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path

from ghrah.ability.content import CaptureLimits, canonical, capture_content, digest_bytes
from ghrah.ability.file_members import FILE_OPERATIONS
from ghrah.ability.loading.ability_roots import builtin_root
from ghrah.ability.loading.ability_schema import AbilityDefinition, parse_definition
from ghrah.ability.permissions import fs_permissions

FILE_MEMBERS = frozenset(FILE_OPERATIONS)


def _implementation_digest() -> str:
    sources = [inspect.getfile(item.operation) for item in FILE_OPERATIONS.values()]
    sources.append(inspect.getfile(fs_permissions))
    sources.extend(
        str(Path(__file__).with_name(name))
        for name in ("file_backend.py", "contracts.py", "file_members.py")
    )
    return hashlib.sha256(b"\0".join(Path(path).read_bytes() for path in sources)).hexdigest()


_RUNNING_IMPLEMENTATION = _implementation_digest()


def loaded_file_implementation() -> str:
    """返回当前进程锁定的文件后端版本，不扫描来源。"""
    return _RUNNING_IMPLEMENTATION


@dataclass(frozen=True)
class AbilityVersion:
    """密封的定义文本与当前已加载后端版本，不持可变来源对象。"""

    digest: str
    definition_json: str
    implementation_digest: str
    manifest_json: str = "{}"
    blobs: tuple[tuple[str, bytes], ...] = ()

    @property
    def content_digest(self) -> str:
        """完整内容清单的摘要，与定义和后端代次分开。"""
        return digest_bytes(self.manifest_json.encode())

    def expected_digest(self) -> str:
        """无内容的旧版本保持原摘要；新内容格式显式加入清单。"""
        suffix = self.manifest_json if self.manifest_json != "{}" else ""
        return digest_bytes((self.definition_json + self.implementation_digest + suffix).encode())

    @property
    def definition_digest(self) -> str:
        """定义摘要不混入实现代码的摘要。"""
        return "sha256:" + hashlib.sha256(self.definition_json.encode()).hexdigest()

    def definition(self) -> AbilityDefinition:
        """返回隔离的定义副本。"""
        return AbilityDefinition.model_validate_json(self.definition_json)


def capture_file_version(raw: str | None = None) -> AbilityVersion:
    """显式读取并密封来源；代码改变需重启加载，拒绝原地 reload。"""
    implementation = _implementation_digest()
    if implementation != _RUNNING_IMPLEMENTATION:
        raise ValueError("后端代码已变化，需要重新加载服务进程")
    if raw is None:
        raw = builtin_root().locator.joinpath("fs", "ability.yaml").read_text(encoding="utf-8")
    definition = parse_definition(raw, source="filesystem")
    if definition.skills or definition.assets:
        raise ValueError("文件切片不能丢弃内容；请使用 capture_ability_version 并明确包根")
    operations = {key: item for key, item in definition.operations.items() if key in FILE_MEMBERS}
    if set(operations) != FILE_MEMBERS:
        raise ValueError("文件能力必须包含两个确定成员")
    definition = definition.model_copy(
        update={"operations": operations, "skills": {}, "assets": {}}
    )
    data = definition.model_dump(mode="json")
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = "sha256:" + hashlib.sha256((encoded + implementation).encode()).hexdigest()
    return AbilityVersion(digest, encoded, implementation)


def capture_ability_version(
    raw: str, *, root: Path, limits: CaptureLimits = CaptureLimits()
) -> AbilityVersion:
    """显式捕获完整容器；未知 Operation 后端保留声明并报告不可执行。"""
    definition = parse_definition(raw, source=str(root))
    definition, manifest, blobs = capture_content(definition, root, limits)
    encoded = canonical(definition.model_dump(mode="json"))
    implementation = loaded_file_implementation() if definition.operations else "content-only:v1"
    version = AbilityVersion("", encoded, implementation, manifest, blobs)
    return AbilityVersion(version.expected_digest(), encoded, implementation, manifest, blobs)


def import_skill_package(
    root: Path,
    *,
    key: str,
    identity: dict[str, str],
    assets: dict[str, dict[str, str]],
    references: dict[str, dict[str, str]],
    limits: CaptureLimits = CaptureLimits(),
) -> AbilityVersion:
    """显式导入标准 SKILL.md 包，元数据以正文为作者，不要求另写重复 YAML。"""
    raw = canonical(
        {
            "format": "ghrah.ability-definition",
            "schema_version": "0.2",
            "identity": identity,
            "title": identity["namespace"],
            "description": "Skill content",
            "skills": {
                key: {
                    "body": {"path": "SKILL.md", "media_type": "text/markdown"},
                    "assets": assets,
                    "references": references,
                }
            },
        }
    )
    return capture_ability_version(raw, root=root, limits=limits)
