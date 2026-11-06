# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""显式取得文件能力定义与实现版本；查询不访问来源。"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path

from ghrah.ability.content import CaptureLimits, canonical, capture_content, digest_bytes
from ghrah.ability.file_members import FILE_OPERATIONS
from ghrah.ability.loading.ability_roots import builtin_root
from ghrah.ability.loading.ability_schema import AbilityDefinition, parse_definition
from ghrah.ability.package.digest import collect_files, tree_digest
from ghrah.ability.permissions import fs_permissions
from ghrah.ability.provider_contracts import (
    CLUSTER_CONTRACT,
    FS_CONTRACT,
    SHELL_CONTRACT,
    contract_for,
)

FILE_MEMBERS = frozenset(FILE_OPERATIONS)

_CONTRACT_SUPPORT_MODULES: dict[str, tuple[str, ...]] = {
    SHELL_CONTRACT: (
        "ghrah.ability.permissions.command_safety",
        "ghrah.ability.permissions.command_scope",
    ),
    CLUSTER_CONTRACT: ("ghrah.ability._cluster_common",),
}
"""契约实现共用的支持模块；这些代码变化必须改变该后端的实现标识。"""


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


def _package_operation_sources(root: Path) -> list[Path]:
    """包内 ``operations/*.py`` 的实现源；没有操作目录时为空。"""
    directory = root / "operations"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.py") if path.name != "__init__.py")


def _contract_identity(contract: str, *, root: Path) -> str:
    """一个提供者契约的实现标识。

    文件后端沿用进程锁定的实现摘要（与 ``capture_file_version`` 同一版本
    号）；其他后端按包内操作模块 + 该契约的支持模块取摘要，未登记契约退到
    包内操作模块摘要，保证不同后端不再共享同一个文件切片版本号。
    """
    if contract == FS_CONTRACT:
        return loaded_file_implementation()
    sources = _package_operation_sources(root)
    for module_name in _CONTRACT_SUPPORT_MODULES.get(contract, ()):
        sources.append(Path(inspect.getfile(importlib.import_module(module_name))))
    if not sources:
        return "content-only:v1"
    return hashlib.sha256(b"\0".join(path.read_bytes() for path in sources)).hexdigest()


def implementation_identity(definition: AbilityDefinition, *, root: Path) -> str:
    """按包来源与身份计算实现标识；同一定义在不同后端下不同号。

    v1 索引不声明 ``provider_requirements``：内置包按身份 ``ghrah.<name>``
    映射到各自后端契约（见 :func:`ghrah.ability.provider_contracts.contract_for`）；
    外部源包没有登记的后端契约，实现标识退到包内 ``operations/*.py`` 的整树摘要
    ——安装后的 Operation 由进程内 Provider 从该包代码加载，代码变化必须换版本号。

    Args:
        definition: 已解析的包定义。
        root: 包根目录，用于读取 ``operations/*.py`` 与脚本实现。

    Returns:
        实现摘要；文件后端与 :func:`loaded_file_implementation` 相同，
        内容类包为 ``content-only:v1``。
    """
    if not definition.operations:
        return "content-only:v1"
    contracts = sorted(
        {
            contract
            for contract in (
                contract_for(
                    definition.origin,
                    definition.identity.namespace,
                    definition.identity.name,
                ),
            )
            if contract is not None
        }
    )
    if not contracts:
        sources = _package_operation_sources(root)
        if not sources:
            return "content-only:v1"
        return hashlib.sha256(b"\0".join(path.read_bytes() for path in sources)).hexdigest()
    if FS_CONTRACT in contracts:
        # 文件后端沿用进程锁定的实现摘要：文件动作的就绪判断依赖与
        # ``capture_file_version`` 同一版本号。
        return loaded_file_implementation()
    parts = [f"{contract}={_contract_identity(contract, root=root)}" for contract in contracts]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class AbilityVersion:
    """一次捕获的定义、实现标识与内容清单。

    ``digest`` 是关系库里的身份。包捕获的身份是 ``package_digest``（版本目录
    整树摘要）；文件切片没有包树，身份仍是定义与实现的密封。从关系库读回时
    只有 ``digest`` 列，``package_digest`` 留空：整树摘要不能从定义文本重算。
    """

    digest: str
    definition_json: str
    implementation_digest: str
    manifest_json: str = "{}"
    blobs: tuple[tuple[str, bytes], ...] = ()
    package_digest: str = ""

    @property
    def content_digest(self) -> str:
        """完整内容清单的摘要，与定义和后端代次分开。"""
        return digest_bytes(self.manifest_json.encode())

    def sealed_digest(self) -> str:
        """定义、实现与内容清单的密封。

        文件切片用它当身份。包版本不再用它当身份：同一份定义文本可以对应
        不同的目录字节。无内容清单时不把空对象 ``{}`` 算进密封，与文件切片
        的历史摘要保持一致。
        """
        suffix = self.manifest_json if self.manifest_json != "{}" else ""
        return digest_bytes((self.definition_json + self.implementation_digest + suffix).encode())

    def expected_digest(self) -> str:
        """提交时必须与 ``digest`` 相等的身份。

        整树版本返回 ``package_digest``。没有整树摘要的文件切片返回密封。
        """
        if self.package_digest:
            return self.package_digest
        return self.sealed_digest()

    @property
    def definition_digest(self) -> str:
        """定义摘要不混入实现代码的摘要，也不再承担版本身份。"""
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
    if definition.skills:
        raise ValueError("文件切片不能丢弃内容；请使用 capture_ability_version 并明确包根")
    operations = {key: item for key, item in definition.operations.items() if key in FILE_MEMBERS}
    if set(operations) != FILE_MEMBERS:
        raise ValueError("文件能力必须包含两个确定成员")
    definition = definition.model_copy(update={"operations": operations, "skills": {}})
    data = definition.model_dump(mode="json")
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = "sha256:" + hashlib.sha256((encoded + implementation).encode()).hexdigest()
    return AbilityVersion(digest, encoded, implementation)


def capture_ability_version(
    raw: str, *, root: Path, limits: CaptureLimits = CaptureLimits()
) -> AbilityVersion:
    """显式捕获完整容器。身份是包根目录的整树摘要。

    ``root`` 是内置包目录或安装槽里的版本目录。摘要覆盖该目录下的普通文件，
    排除缓存目录；版本目录旁的安装记录不在这棵树里。定义文本、实现标识和
    内容清单仍一并捕获，但它们不再决定 ``digest``。

    Args:
        raw: 包定义原文。调用方从 ``root/ability.yaml`` 读出后传入。
        root: 要计算整树摘要的包根。
        limits: 内容捕获的大小限制。

    Returns:
        ``digest`` 与 ``package_digest`` 相同的版本。

    Raises:
        ValueError: 定义或内容清单不合法。
        OSError: 包根不可读。
        PathRuleError: 包根含符号链接、非普通文件或不可移植的文本。
    """
    definition = parse_definition(raw, source=str(root))
    definition, manifest, blobs = capture_content(definition, root, limits)
    encoded = canonical(definition.model_dump(mode="json"))
    implementation = implementation_identity(definition, root=root)
    package_digest = tree_digest(collect_files(root))
    return AbilityVersion(
        package_digest,
        encoded,
        implementation,
        manifest,
        blobs,
        package_digest,
    )
