# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""安装器的领域层：install / uninstall / list / show / verify / refresh。

边界（见 M0 规范冻结 §3.8 / §3.10）：

- 处理 v1 源包目录和显式 MCP 声明；MCP 快照由受信客户端发现生成。
  归档须先解包（``unsupported_source``）。
- 安装槽是 ``<root>/<namespace>/<name>/<version>/``，安装记录是版本目录旁的
  ``<version>.lock.yaml``；``<root>`` 由调用方显式传入，领域层不拼默认路径。
- 安装只**校验**已生成的 ``members`` 与内容摘要，**不 import 包代码、不重建索引**。
- 安装顺序：完整复制到同盘临时目录 → 复算摘要自检 → 写 lock 临时文件并
  ``os.replace`` → 原子 rename 版本目录。失败清理临时物，不留下"无 lock 的版本目录"。
- ``list`` / ``show`` / ``verify`` 读安装存储，不调用 ``scan_abilities``；
  ``refresh`` 只刷新 MCP 快照。
- 审计、授权与运行时选择不在本层。

错误码（:class:`InstallerError.code`，供 CLI 透传）：

``unsupported_source`` / ``mcp_config_invalid`` / ``reserved_namespace`` /
``reserved_member_name`` / ``lock_in_source`` / ``legacy_layout`` /
``invalid_definition`` / ``invalid_content`` / ``index_mismatch`` /
``portable_tree_violation`` / ``symlink_rejected`` / ``root_invalid`` /
``path_too_long`` / ``version_conflict`` / ``corrupt_install`` /
``digest_mismatch`` / ``install_failed`` / ``version_required`` /
``not_installed`` / ``invalid_full_name`` / ``invalid_version`` /
``invalid_arguments`` / ``uninstall_failed`` / ``unsupported_refresh`` /
``unsupported_until_p5``。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from ghrah.ability.content import CaptureLimits, capture_content
from ghrah.ability.loading.ability_schema import (
    DEFINITION_FILE_NAME,
    V1_SEMVER_PATTERN,
    AbilityDefinition,
    load_yaml,
    parse_definition,
)
from ghrah.ability.loading.manifest_fields import ManifestValidationError
from ghrah.ability.package.digest import (
    DIGEST_ALGORITHM,
    collect_files,
    file_digest,
    tree_digest,
)
from ghrah.ability.package.installed_store import (
    InstalledStore,
    InstalledVersion,
    is_link,
    parse_full_name,
)
from ghrah.ability.package.lock import (
    LOCK_SUFFIX,
    InstallLock,
    InstallSource,
    LockError,
    McpLock,
    lock_filename,
    read_lock,
    utc_now_rfc3339,
    write_lock,
)
from ghrah.ability.package.paths import (
    PathRuleError,
    check_relative_path,
    ensure_portable_tree,
)

__all__ = [
    "MAX_ABSOLUTE_UTF16",
    "AbilityInstaller",
    "InstallOrigin",
    "InstallResult",
    "InstalledRecord",
    "InstallerError",
    "RemovedVersion",
    "UninstallResult",
    "VerifyResult",
]

MAX_ABSOLUTE_UTF16: int = 240
"""安装后绝对路径上限（UTF-16 码元）；给 Windows 默认 ``MAX_PATH=260`` 留余量。"""


class InstallOrigin(StrEnum):
    """安装通道：决定保留命名空间的放行边界。

    ``USER``（缺省）：第三方显式安装；``ghrah`` / ``mcp`` 一律拒绝。
    ``DISTRO``：发行版预装；允许 ``ghrah``（系统自带），仍拒绝 ``mcp``
    （``mcp`` 是 ``tools/list`` 快照专用槽，与来源无关）。
    """

    USER = "user"
    DISTRO = "distro"


_MCP_NAMESPACE: str = "mcp"
_GHRAH_NAMESPACE: str = "ghrah"

_RESERVED_TOOL_NAMES: frozenset[str] = frozenset(
    {"search_tools", "describe_tool", "execute_tool", "run_script"}
)
"""工具保留名：生成期拒绝一次，安装期再拒绝一次（双重检查）。"""

_OLD_LAYOUT_TOP_LEVEL: frozenset[str] = frozenset(
    {"SKILL.md", "skill", "references", "assets", "scripts"}
)
"""旧版包布局的包根条目；v1 安装期明确拒绝（不提供迁移）。"""

_ABILITY_FILE_NAME: str = DEFINITION_FILE_NAME
_SOURCE_LOCK_FILE_NAME: str = "ability.lock.yaml"
"""源包禁止携带的旧版安装记录文件名（后缀 ``.lock.yaml`` 一律拒绝）。"""


class InstallerError(ValueError):
    """安装域失败；``code`` 面向 CLI 信封，``message`` 面向人。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class InstallResult:
    """一次安装（或幂等命中）的结果。"""

    full_name: str
    version: str
    root: Path
    package_digest: str
    already_installed: bool = False


@dataclass(frozen=True)
class RemovedVersion:
    """卸载掉的一个版本：``root`` 是删除前的版本目录。"""

    version: str
    root: Path


@dataclass(frozen=True)
class UninstallResult:
    """一次卸载的结果。"""

    full_name: str
    removed: tuple[RemovedVersion, ...]


@dataclass(frozen=True)
class InstalledRecord:
    """``list`` 的一行：一个已安装版本（不解析 ``ability.yaml``）。"""

    full_name: str
    namespace: str
    name: str
    version: str
    root: Path
    installed_at: str | None = None
    source: InstallSource | None = None
    lock_state: str = "missing"
    mcp: McpLock | None = None
    problem: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON 可序列化的单行形状（``root`` 转字符串）。"""
        return {
            "full_name": self.full_name,
            "namespace": self.namespace,
            "name": self.name,
            "version": self.version,
            "root": str(self.root),
            "installed_at": self.installed_at,
            "source": _source_dict(self.source),
            "lock_state": self.lock_state,
            "mcp": _mcp_dict(self.mcp),
            "problem": self.problem,
        }


@dataclass(frozen=True)
class VerifyResult:
    """``verify`` 对单个版本的诊断结果。"""

    full_name: str
    version: str
    root: Path
    ok: bool
    package_digest: str | None = None
    actual_digest: str | None = None
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON 可序列化的单版本形状（路径与差异列表转纯量）。"""
        return {
            "full_name": self.full_name,
            "version": self.version,
            "root": str(self.root),
            "ok": self.ok,
            "package_digest": self.package_digest,
            "actual_digest": self.actual_digest,
            "added": list(self.added),
            "removed": list(self.removed),
            "changed": list(self.changed),
            "code": self.code,
            "message": self.message,
        }


def _source_dict(source: InstallSource | None) -> dict[str, Any] | None:
    if source is None:
        return None
    return {"kind": source.kind, "ref": source.ref}


def _mcp_dict(mcp: McpLock | None) -> dict[str, Any] | None:
    if mcp is None:
        return None
    return {"fetched_at": mcp.fetched_at, "stale": mcp.stale, "last_error": mcp.last_error}


def _utf16_units(text: str) -> int:
    """``text`` 的 UTF-16 码元数（Windows 路径预算口径）。"""
    return len(text.encode("utf-16-le")) // 2


def _declared_path(path: str, *, what: str) -> str:
    """校验索引里声明的包内路径，返回 NFC 归一结果；失败按索引问题处理。"""
    try:
        return check_relative_path(path, what=what)
    except PathRuleError as exc:
        raise InstallerError("index_mismatch", str(exc)) from exc


def _declared_content_paths(definition: AbilityDefinition) -> list[str]:
    """索引声明的 skill 正文与资源路径（相对包根，按声明顺序）。"""
    paths: list[str] = []
    for skill in definition.skills.values():
        paths.append(skill.body.path)
        for role in ("assets", "references", "scripts"):
            paths.extend(resource.path for resource in getattr(skill, role).values())
    return paths


def _content_limits(definition: AbilityDefinition, root: Path) -> CaptureLimits:
    """按索引声明的实际文件大小推导内容捕获额度。

    M0 没有定义包大小预算；§3.10 要求的是 ``files`` 与索引登记的 digest / size
    一致，而不是内容捕获预算。因此额度只由"这些声明文件实际有多大"决定，
    不能用一个硬编码上限把合法的大资源包误拒。
    """
    declared_paths = _declared_content_paths(definition)
    sizes: list[int] = []
    for declared in declared_paths:
        try:
            relative = check_relative_path(declared, what="内容路径")
        except PathRuleError:
            # 非可移植路径由 capture_content 拒绝；这里不跟着越界 stat。
            continue
        try:
            sizes.append((root / relative).stat().st_size)
        except OSError:
            # 文件缺失/不可读交给 capture_content 按"内容与索引不符"处理；
            # 这里只保证额度是正整数。
            continue
    return CaptureLimits(
        file_bytes=max(max(sizes, default=0), 1),
        total_bytes=max(sum(sizes), 1),
        entries=max(len(declared_paths), 1),
    )


class AbilityInstaller:
    """安装域入口：一个显式安装根上的安装 / 卸载 / 查询 / 校验。"""

    def __init__(
        self, root: str | Path, *, origin: InstallOrigin = InstallOrigin.USER
    ) -> None:
        self._root = Path(root)
        self._store = InstalledStore(self._root)
        self._origin = InstallOrigin(origin)

    @property
    def root(self) -> Path:
        """安装根（调用方显式传入的发行版级存储根）。"""
        return self._root

    @property
    def store(self) -> InstalledStore:
        """只读安装存储枚举器。"""
        return self._store

    @property
    def origin(self) -> InstallOrigin:
        """本次安装器绑定的信任来源通道。"""
        return self._origin

    def _reject_reserved_namespace(self, namespace: str) -> None:
        """按安装通道拒绝保留命名空间；判定依据是通道，不是包自述。"""
        if namespace == _MCP_NAMESPACE:
            raise InstallerError(
                "reserved_namespace",
                f"namespace {namespace!r} 保留给 MCP 快照，任何安装通道不得使用",
            )
        if namespace == _GHRAH_NAMESPACE and self._origin is not InstallOrigin.DISTRO:
            raise InstallerError(
                "reserved_namespace",
                f"namespace {namespace!r} 保留给内置包或发行版预装；"
                "用户通道不得安装（请经发行版预装通道）",
            )

    # ------------------------------------------------------------------ install

    def install(self, source: str | Path) -> InstallResult:
        """把 v1 源包目录安装进 ``<root>/<namespace>/<name>/<version>/``。

        Args:
            source: 源包或 MCP 声明目录（含 ``ability.yaml``）；归档须先解包。

        Returns:
            安装结果；目标版本已存在且摘要相同时 ``already_installed=True``。

        Raises:
            InstallerError: 来源/定义/内容/索引/路径/目标状态任一不合法。
        """
        source_path = Path(source)
        self._check_source(source_path)
        raw = self._read_definition_text(source_path)
        if self._declares_mcp_origin(raw):
            return asyncio.run(self.install_mcp(source_path))
        definition = self._parse_definition_text(raw, source_path)
        namespace = definition.identity.namespace
        self._reject_reserved_namespace(namespace)
        for key in definition.operations:
            if key in _RESERVED_TOOL_NAMES:
                raise InstallerError(
                    "reserved_member_name",
                    f"operations/{key}.py: 成员键是工具保留名，外部包不得占用",
                )

        entries = self._collect_source(source_path)
        source_files = {relative: file_digest(full) for relative, full in entries}
        for relative in source_files:
            if relative.casefold().endswith(LOCK_SUFFIX):
                raise InstallerError(
                    "lock_in_source",
                    f"源包含安装记录文件（不是包内容，旧版 lock 也不读不迁移）: {relative}",
                )
        self._check_member_files(definition, source_files)
        self._check_content(definition, source_path)
        package_digest = tree_digest(source_files)

        name = definition.identity.name or namespace
        version = definition.identity.version
        slot = self._root / namespace / name
        version_root = slot / version
        lock_path = slot / lock_filename(version)
        self._check_root()
        _check_path_budget(version_root, lock_path, entries, source=source_path)

        existing = self._existing_install(definition, version_root, lock_path, package_digest)
        if existing is not None:
            return existing
        return self._install_copy(
            definition=definition,
            source=source_path,
            entries=entries,
            source_files=source_files,
            package_digest=package_digest,
            slot=slot,
            version_root=version_root,
            lock_path=lock_path,
        )

    @staticmethod
    def _check_source(source: Path) -> None:
        if is_link(source):
            raise InstallerError("symlink_rejected", f"源包根是符号链接/junction: {source}")
        if not source.exists():
            raise InstallerError("unsupported_source", f"安装来源不存在: {source}")
        if not source.is_dir():
            raise InstallerError(
                "unsupported_source",
                f"P2 只支持目录来源；归档请先解包成 v1 源包目录: {source}",
            )

    @staticmethod
    def _read_definition_text(source: Path) -> str:
        """读 ``ability.yaml`` 原文（链接/缺失/BOM/编码失败按 invalid_definition 拒绝）。"""
        definition_path = source / _ABILITY_FILE_NAME
        if is_link(definition_path):
            raise InstallerError(
                "symlink_rejected", f"{_ABILITY_FILE_NAME} 是符号链接/junction: {definition_path}"
            )
        if not definition_path.is_file():
            raise InstallerError("invalid_definition", f"源包缺少 {_ABILITY_FILE_NAME}: {source}")
        try:
            raw = definition_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise InstallerError(
                "invalid_definition", f"{_ABILITY_FILE_NAME} 不可读或不是 UTF-8: {exc}"
            ) from exc
        if raw.startswith("\ufeff"):
            raise InstallerError(
                "invalid_definition", f"{_ABILITY_FILE_NAME} 含 BOM（须 UTF-8 无 BOM）"
            )
        return raw

    @staticmethod
    def _declares_mcp_origin(raw: str) -> bool:
        """只 peek 头段的 ``origin``，不要求整份定义可解析（MCP 快照另走 P5）。"""
        try:
            header = load_yaml(raw)
        except ManifestValidationError, yaml.YAMLError:
            return False
        return isinstance(header, Mapping) and header.get("origin") == "mcp"

    @staticmethod
    def _parse_definition_text(raw: str, source: Path) -> AbilityDefinition:
        """解析 v1 定义；旧版/非法定义按 invalid_definition 拒绝。"""
        try:
            return parse_definition(raw, source=str(source / _ABILITY_FILE_NAME))
        except (ManifestValidationError, yaml.YAMLError) as exc:
            raise InstallerError(
                "invalid_definition",
                f"不是合法的 v1 能力包定义（旧版定义不读、不迁移）: {exc}",
            ) from exc

    @classmethod
    def _load_definition(cls, source: Path) -> AbilityDefinition:
        """读并解析 ``ability.yaml``（v1）；供 show / verify 等只读路径复用。"""
        return cls._parse_definition_text(cls._read_definition_text(source), source)

    @staticmethod
    def _collect_source(source: Path) -> list[tuple[str, Path]]:
        try:
            return ensure_portable_tree(source)
        except PathRuleError as exc:
            raise InstallerError("portable_tree_violation", str(exc)) from exc

    @staticmethod
    def _check_member_files(definition: AbilityDefinition, source_files: Mapping[str, str]) -> None:
        """按定义推导期望文件集合，与源包实际文件严格比对（缺/多都拒）。"""
        expected: dict[str, str] = {_ABILITY_FILE_NAME: "定义文件"}
        for key, operation in definition.operations.items():
            if operation.entry is None:
                raise InstallerError(
                    "invalid_definition", f"operations.{key}: v1 索引必须声明 entry"
                )
            entry_path = _declared_path(operation.entry.path, what=f"operations.{key}.entry")
            if entry_path != f"operations/{key}.py":
                raise InstallerError(
                    "index_mismatch",
                    f"operations.{key}: entry 必须是 operations/{key}.py，实际 {entry_path!r}",
                )
            expected[entry_path] = f"operations.{key}.entry"
        for skill_key, skill in definition.skills.items():
            body_path = _declared_path(skill.body.path, what=f"skills.{skill_key}.body.path")
            expected[body_path] = f"skills.{skill_key}.body"
            for role in ("assets", "references", "scripts"):
                for resource_key, resource in getattr(skill, role).items():
                    resource_path = _declared_path(
                        resource.path, what=f"skills.{skill_key}.{role}.{resource_key}"
                    )
                    expected[resource_path] = f"skills.{skill_key}.{role}.{resource_key}"

        missing = sorted(set(expected) - set(source_files))
        if missing:
            raise InstallerError("index_mismatch", f"索引声明的文件在源包中缺失: {missing}")

        for relative in sorted(source_files):
            if relative in expected:
                continue
            top_level = relative.split("/", 1)[0]
            if top_level in _OLD_LAYOUT_TOP_LEVEL:
                raise InstallerError(
                    "legacy_layout",
                    "旧版包布局条目拒绝（v1 只允许根 ability.yaml、"
                    f"operations/、skills/）: {relative}",
                )
            if relative in {"operations", "skills"}:
                raise InstallerError("index_mismatch", f"{relative} 必须是目录，不能是文件")
            if relative.startswith("operations/"):
                remainder = relative[len("operations/") :]
                if "/" in remainder:
                    raise InstallerError(
                        "index_mismatch", f"operations/ 只扫顶层（子目录拒绝）: {relative}"
                    )
                if not remainder.endswith(".py"):
                    raise InstallerError(
                        "index_mismatch", f"operations/ 下的非 .py 条目拒绝: {relative}"
                    )
                if not remainder.startswith("_"):
                    raise InstallerError(
                        "index_mismatch", f"operations/ 下存在未声明成员: {relative}"
                    )
                continue
            if relative == "skills/SKILL.md":
                raise InstallerError(
                    "legacy_layout",
                    f"旧版单数 skill 布局拒绝（v1 使用 skills/<name>/SKILL.md）: {relative}",
                )
            if relative.startswith("skills/"):
                raise InstallerError("index_mismatch", f"skills/ 下存在未登记文件: {relative}")
            # 包根的其他非成员文件（README.md、LICENSE 等）允许，参与 package_digest。

    @staticmethod
    def _check_content(definition: AbilityDefinition, source: Path) -> None:
        """校验 skill 正文/资源（digest、size、frontmatter、链接），不 import 包代码。"""
        try:
            _, manifest_text, _ = capture_content(
                definition, source, _content_limits(definition, source)
            )
        except (OSError, ValueError, yaml.YAMLError, ManifestValidationError) as exc:
            raise InstallerError("invalid_content", f"skill 正文/资源与索引不符: {exc}") from exc
        manifest = json.loads(manifest_text)
        for skill_key, skill in definition.skills.items():
            for role in ("assets", "references", "scripts"):
                for resource_key, resource in getattr(skill, role).items():
                    captured = manifest.get(f"skill/{skill_key}/{role}/{resource_key}")
                    if not isinstance(captured, dict):
                        raise InstallerError(
                            "invalid_content",
                            f"未捕获索引资源: skills/{skill_key}/{role}/{resource_key}",
                        )
                    size = captured.get("size")
                    if resource.size is not None and size != resource.size:
                        raise InstallerError(
                            "invalid_content",
                            f"skills/{skill_key}/{role}/{resource_key}: size 与索引不符"
                            f"（索引 {resource.size}，实际 {size}）",
                        )

    def _check_root(self) -> None:
        """安装根必须是真实目录（或尚不存在）；链接与非目录拒绝。"""
        root = self._root
        if is_link(root):
            raise InstallerError("symlink_rejected", f"安装根是符号链接/junction: {root}")
        if root.exists() and not root.is_dir():
            raise InstallerError("root_invalid", f"安装根已存在但不是目录: {root}")

    def _existing_install(
        self,
        definition: AbilityDefinition,
        version_root: Path,
        lock_path: Path,
        package_digest: str,
    ) -> InstallResult | None:
        """目标版本已存在时的裁决：同摘要幂等、不同摘要拒绝、缺 lock 视为损坏。"""
        if is_link(version_root):
            raise InstallerError(
                "symlink_rejected", f"目标版本目录是符号链接/junction: {version_root}"
            )
        if not version_root.exists():
            return None
        version = definition.identity.version
        if not version_root.is_dir():
            raise InstallerError("corrupt_install", f"目标版本路径已存在但不是目录: {version_root}")
        if is_link(lock_path):
            raise InstallerError(
                "corrupt_install", f"目标版本的安装记录是符号链接/junction: {lock_path}"
            )
        if not lock_path.is_file():
            raise InstallerError(
                "corrupt_install",
                f"目标版本目录已存在但缺少安装记录（视为未完成安装，不覆盖）: {version_root}",
            )
        try:
            existing = read_lock(lock_path)
        except LockError as exc:
            raise InstallerError(
                "corrupt_install",
                f"目标版本目录已存在但安装记录损坏: {exc.code}: {exc.message}",
            ) from exc
        if existing.package_digest == package_digest:
            return InstallResult(
                full_name=definition.full_name,
                version=version,
                root=version_root,
                package_digest=package_digest,
                already_installed=True,
            )
        raise InstallerError(
            "version_conflict",
            f"版本 {version} 已安装且内容不同（既有 {existing.package_digest}，"
            f"本次 {package_digest}）；不覆盖已安装版本",
        )

    def _install_copy(
        self,
        *,
        definition: AbilityDefinition,
        source: Path,
        entries: list[tuple[str, Path]],
        source_files: Mapping[str, str],
        package_digest: str,
        slot: Path,
        version_root: Path,
        lock_path: Path,
        mcp: McpLock | None = None,
    ) -> InstallResult:
        """同盘临时目录完整拷贝 → 摘要自检 → 写 lock → 原子 rename。"""
        lock_written = False
        try:
            slot.mkdir(parents=True, exist_ok=True)
            tmp_dir = slot / f".tmp-{uuid.uuid4().hex}"
            tmp_dir.mkdir()
        except OSError as exc:
            raise InstallerError("install_failed", f"无法创建安装目录: {exc}") from exc
        try:
            for relative, full in entries:
                destination = tmp_dir / full.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(full, destination)
            try:
                copied = collect_files(tmp_dir)
            except PathRuleError as exc:
                raise InstallerError(
                    "digest_mismatch", f"临时目录自检失败（可移植性）: {exc}"
                ) from exc
            if dict(copied) != dict(source_files):
                raise InstallerError("digest_mismatch", "复制后自检失败：临时目录内容与源包不一致")
            copied_digest = tree_digest(copied)
            if copied_digest != package_digest:
                raise InstallerError(
                    "digest_mismatch",
                    f"复制后自检失败：摘要不一致（{copied_digest} != {package_digest}）",
                )
            lock = InstallLock(
                package_digest=package_digest,
                digest_algorithm=DIGEST_ALGORITHM,
                installed_at=utc_now_rfc3339(),
                source=InstallSource(kind="directory", ref=str(source))
                if mcp is None
                else InstallSource(kind="mcp"),
                files=dict(source_files),
                mcp=mcp,
            )
            try:
                write_lock(lock_path, lock)
            except LockError as exc:
                raise InstallerError("install_failed", f"安装记录写入失败: {exc}") from exc
            lock_written = True
            try:
                os.replace(tmp_dir, version_root)
            except OSError as exc:
                if self._concurrent_digest(version_root, lock_path) == package_digest:
                    return InstallResult(
                        full_name=definition.full_name,
                        version=definition.identity.version,
                        root=version_root,
                        package_digest=package_digest,
                        already_installed=True,
                    )
                raise InstallerError(
                    "version_conflict", f"目标版本在安装期间被创建，放弃覆盖: {version_root}"
                ) from exc
            return InstallResult(
                full_name=definition.full_name,
                version=definition.identity.version,
                root=version_root,
                package_digest=package_digest,
            )
        except OSError as exc:
            raise InstallerError("install_failed", f"安装写入失败: {exc}") from exc
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            if lock_written and not version_root.exists():
                # 只在"没有版本目录"时清理自己写的 lock，避免并发下删掉别人的记录。
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _concurrent_digest(version_root: Path, lock_path: Path) -> str | None:
        """rename 失败时读目标版本旁 lock 的摘要；目标不可用返回 ``None``。"""
        if not version_root.is_dir() or is_link(version_root):
            return None
        try:
            return read_lock(lock_path).package_digest
        except LockError:
            return None

    # ---------------------------------------------------------------- uninstall

    def uninstall(
        self,
        full_name: str,
        version: str | None = None,
        *,
        all: bool = False,
        confirm: bool = False,
        invalidate: Callable[[InstalledVersion], None] | None = None,
    ) -> UninstallResult:
        """卸载指定版本；省略版本且未 ``all=True`` 时按业务错误拒绝。

        Args:
            full_name: 身份全名（如 ``fixtures.reporting``）；退化身份为单段。
            version: 要卸载的版本；与 ``all=True`` 互斥。
            all: 卸载该身份的全部版本。
            confirm: 保留参数。P2 的 CLI ``--yes`` 只是接受，不做交互确认；
                领域层不消费该值。
            invalidate: 可选回调，删除前对每个版本调用（供运行时把相关授予标为
                不可用）；回调抛异常时中止删除。

        Returns:
            卸载结果；``removed`` 按版本升序。

        Raises:
            InstallerError: 参数非法、未安装或删除失败。
        """
        if parse_full_name(full_name) is None:
            raise InstallerError("invalid_full_name", f"非法身份全名: {full_name!r}")
        if version is not None and all:
            raise InstallerError("invalid_arguments", "不能同时指定 version 与 all")
        if version is None and not all:
            raise InstallerError(
                "version_required", "卸载需要指定版本；卸载该身份全部版本请显式传 all=True"
            )
        self._check_root()
        selected = self._select_for_uninstall(full_name, version, all)
        if invalidate is not None:
            for item in selected:
                invalidate(item)
        removed: list[RemovedVersion] = []
        for item in selected:
            self._delete_version(item)
            removed.append(RemovedVersion(version=item.version, root=item.root))
        if all:
            self._remove_orphan_locks(full_name)
        return UninstallResult(full_name=full_name, removed=tuple(removed))

    def _select_for_uninstall(
        self, full_name: str, version: str | None, all: bool
    ) -> tuple[InstalledVersion, ...]:
        if version is not None:
            if not V1_SEMVER_PATTERN.fullmatch(version):
                raise InstallerError("invalid_version", f"版本必须是 SemVer core 三段: {version!r}")
            item = self._store.resolve(full_name, version)
            if item is None:
                raise InstallerError("not_installed", f"未安装: {full_name}@{version}")
            return (item,)
        versions = self._store.versions_of(full_name)
        if versions:
            return versions
        if self._store.orphan_locks(full_name):
            self._remove_orphan_locks(full_name)
            return ()
        raise InstallerError("not_installed", f"该身份没有已安装版本: {full_name}")

    def _delete_version(self, item: InstalledVersion) -> None:
        """先把版本目录 rename 到 ``.trash`` 再删除，随后删旁 lock 并清理空父目录。"""
        package_dir = item.root.parent
        trash = package_dir / ".trash"
        staged = trash / uuid.uuid4().hex
        try:
            trash.mkdir(exist_ok=True)
            os.replace(item.root, staged)
        except OSError as exc:
            raise InstallerError(
                "uninstall_failed", f"无法移走版本目录 {item.root}: {exc}"
            ) from exc
        try:
            shutil.rmtree(staged)
        except OSError as exc:
            raise InstallerError("uninstall_failed", f"删除版本目录失败 {staged}: {exc}") from exc
        try:
            item.lock_path.unlink(missing_ok=True)
        except OSError as exc:
            raise InstallerError(
                "uninstall_failed", f"删除安装记录失败 {item.lock_path}: {exc}"
            ) from exc
        try:
            trash.rmdir()
        except OSError:
            pass
        self._prune_empty_dirs(package_dir)

    def _remove_orphan_locks(self, full_name: str) -> None:
        """删除没有对应版本目录的孤儿 lock（不是安装事实，显式清理时删）。"""
        for orphan in self._store.orphan_locks(full_name):
            try:
                orphan.unlink(missing_ok=True)
            except OSError as exc:
                raise InstallerError(
                    "uninstall_failed", f"删除孤儿安装记录失败 {orphan}: {exc}"
                ) from exc
        package_dir = self._store.package_dir(full_name)
        if package_dir is not None:
            self._prune_empty_dirs(package_dir)

    def _prune_empty_dirs(self, start: Path) -> None:
        """自下而上删除空目录，但保留安装根本身。"""
        root = self._root
        current = start
        while current != root and root in current.parents:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    # ------------------------------------------------------------- list / show

    def list(self) -> list[InstalledRecord]:
        """列出全部已安装版本（不存在的安装根返回空列表，不抛异常）。"""
        records: list[InstalledRecord] = []
        for package in self._store.packages():
            for item in package.versions:
                records.append(_record(item))
        return records

    def show(
        self, full_name: str, version: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """查看某个身份的安装事实与 ``ability.yaml`` 定义。

        省略 ``version`` 返回该身份全部版本的列表；指定时返回单个版本。
        缺 lock 时降级展示（``lock_state`` 标 ``missing`` / ``invalid``）。
        """
        selected = self._select_existing(full_name, version)
        payloads = [self._show_payload(item) for item in selected]
        return payloads[0] if version is not None else payloads

    def _select_existing(self, full_name: str, version: str | None) -> tuple[InstalledVersion, ...]:
        if parse_full_name(full_name) is None:
            raise InstallerError("invalid_full_name", f"非法身份全名: {full_name!r}")
        if version is not None:
            if not V1_SEMVER_PATTERN.fullmatch(version):
                raise InstallerError("invalid_version", f"版本必须是 SemVer core 三段: {version!r}")
            item = self._store.resolve(full_name, version)
            if item is None:
                raise InstallerError("not_installed", f"未安装: {full_name}@{version}")
            return (item,)
        versions = self._store.versions_of(full_name)
        if not versions:
            raise InstallerError("not_installed", f"未安装: {full_name}")
        return versions

    def _show_payload(self, item: InstalledVersion) -> dict[str, Any]:
        definition: dict[str, Any] | None = None
        definition_error: str | None = None
        try:
            definition = self._load_definition(item.root).model_dump(mode="json")
        except InstallerError as exc:
            definition_error = f"{exc.code}: {exc.message}"
        problem = item.problem
        if definition_error is not None:
            problem = f"{problem}; {definition_error}" if problem else definition_error
        lock = item.lock
        return {
            "version": item.version,
            "root": str(item.root),
            "installed_at": item.installed_at,
            "source": None if lock is None else _source_dict(lock.source),
            "mcp": None if lock is None else _mcp_dict(lock.mcp),
            "lock_state": item.lock_state,
            "problem": problem,
            "definition": definition,
        }

    # ------------------------------------------------------------------- verify

    def verify(self, full_name: str, version: str | None = None) -> list[VerifyResult]:
        """用安装记录的 ``files`` 逐文件 diff，并复算 ``package_digest``。

        省略 ``version`` 校验该身份全部版本；缺 lock 视为拒绝（``lock_missing``），
        不假装健康。
        """
        selected = self._select_existing(full_name, version)
        return [self._verify_one(item) for item in selected]

    def _verify_one(self, item: InstalledVersion) -> VerifyResult:
        lock = item.lock
        if lock is None:
            code = _lock_problem_code(item.problem)
            return VerifyResult(
                full_name=item.full_name,
                version=item.version,
                root=item.root,
                ok=False,
                code=code,
                message=item.problem,
            )
        try:
            current = collect_files(item.root)
        except PathRuleError as exc:
            return VerifyResult(
                full_name=item.full_name,
                version=item.version,
                root=item.root,
                ok=False,
                package_digest=lock.package_digest,
                code="tree_invalid",
                message=str(exc),
            )
        except OSError as exc:
            return VerifyResult(
                full_name=item.full_name,
                version=item.version,
                root=item.root,
                ok=False,
                package_digest=lock.package_digest,
                code="tree_unreadable",
                message=str(exc),
            )
        added = tuple(sorted(set(current) - set(lock.files)))
        removed = tuple(sorted(set(lock.files) - set(current)))
        changed = tuple(
            sorted(
                path for path in set(current) & set(lock.files) if current[path] != lock.files[path]
            )
        )
        actual_digest = tree_digest(current)
        common: dict[str, Any] = {
            "full_name": item.full_name,
            "version": item.version,
            "root": item.root,
            "package_digest": lock.package_digest,
            "actual_digest": actual_digest,
        }
        if added or removed or changed or actual_digest != lock.package_digest:
            return VerifyResult(
                ok=False,
                added=added,
                removed=removed,
                changed=changed,
                code="digest_mismatch",
                message=(
                    f"added={len(added)} removed={len(removed)} changed={len(changed)}；"
                    f"复算摘要 {actual_digest} != 安装记录 {lock.package_digest}"
                ),
                **common,
            )
        index_problem = self._index_content_problem(item.root)
        if index_problem is not None:
            return VerifyResult(
                ok=False,
                added=added,
                removed=removed,
                changed=changed,
                code="index_mismatch",
                message=index_problem,
                **common,
            )
        return VerifyResult(ok=True, **common)

    def _index_content_problem(self, version_root: Path) -> str | None:
        """校验 ``files`` 与 ``ability.yaml`` 登记的资源摘要/size 一致。"""
        try:
            definition = self._load_definition(version_root)
        except InstallerError as exc:
            return f"{exc.code}: {exc.message}"
        try:
            _, manifest_text, _ = capture_content(
                definition, version_root, _content_limits(definition, version_root)
            )
        except (OSError, ValueError, yaml.YAMLError, ManifestValidationError) as exc:
            return f"skill 正文/资源与索引不符: {exc}"
        manifest = json.loads(manifest_text)
        for skill_key, skill in definition.skills.items():
            for role in ("assets", "references", "scripts"):
                for resource_key, resource in getattr(skill, role).items():
                    captured = manifest.get(f"skill/{skill_key}/{role}/{resource_key}")
                    if not isinstance(captured, dict):
                        return f"索引资源未捕获: skills/{skill_key}/{role}/{resource_key}"
                    if resource.digest is not None and captured.get("digest") != resource.digest:
                        return f"skills/{skill_key}/{role}/{resource_key}: digest 与索引不符"
                    if resource.size is not None and captured.get("size") != resource.size:
                        return f"skills/{skill_key}/{role}/{resource_key}: size 与索引不符"
        return None

    # ------------------------------------------------------------------ refresh

    def refresh(
        self,
        target: str | Path | None = None,
        *,
        full_name: str | None = None,
        version: str | None = None,
    ) -> InstallResult:
        """刷新 MCP 最新候选；源包拒绝刷新，变化不会自动切换实例版本。"""
        candidate = full_name if full_name is not None else target
        if version is not None:
            raise InstallerError("invalid_arguments", "MCP refresh 不接受版本")
        if isinstance(candidate, str) and candidate.startswith("mcp."):
            return asyncio.run(self.refresh_mcp(candidate))
        raise InstallerError("unsupported_refresh", "源包不支持 refresh；请重新 install")

    async def install_mcp(
        self, source_path: Path, *, source: Any = None, connector: Any = None
    ) -> InstallResult:
        """从显式声明观测 MCP 并安装；异步宿主使用本入口。"""
        from ghrah.ability.package.sources.mcp import install_mcp

        self._check_source(source_path)
        return await install_mcp(self, source_path, source=source, connector=connector)

    async def refresh_mcp(
        self, full_name: str, *, source: Any = None, connector: Any = None
    ) -> InstallResult:
        """刷新已安装 MCP 包；不会自动替换任何实例的选择。"""
        from ghrah.ability.package.sources.mcp import refresh_mcp

        if parse_full_name(full_name) is None or not full_name.startswith("mcp."):
            raise InstallerError("invalid_full_name", "需要 mcp.<name> 身份")
        return await refresh_mcp(self, full_name, source=source, connector=connector)


_LOCK_PROBLEM_CODES: frozenset[str] = frozenset(
    {"lock_invalid", "lock_schema_unsupported", "lock_digest_mismatch"}
)
"""安装记录损坏时可供上层判别的具体错误码（{code}: {message} 的前半段）。"""


def _lock_problem_code(problem: str | None) -> str:
    """从 ``InstalledVersion.problem`` 还原安装记录错误码（缺省 ``lock_invalid``）。"""
    if problem is None:
        return "lock_invalid"
    if problem.startswith("lock_missing"):
        return "lock_missing"
    code = problem.split(":", 1)[0]
    return code if code in _LOCK_PROBLEM_CODES else "lock_invalid"


def _record(item: InstalledVersion) -> InstalledRecord:
    return InstalledRecord(
        full_name=item.full_name,
        namespace=item.namespace,
        name=item.name,
        version=item.version,
        root=item.root,
        installed_at=item.installed_at,
        source=item.source,
        lock_state=item.lock_state,
        mcp=item.mcp,
        problem=item.problem,
    )


def _check_path_budget(
    version_root: Path,
    lock_path: Path,
    entries: list[tuple[str, Path]],
    *,
    source: Path,
) -> None:
    """安装后绝对路径 ≤ :data:`MAX_ABSOLUTE_UTF16` 个 UTF-16 码元。"""
    candidates: list[tuple[str, Path]] = [
        ("版本目录", version_root),
        ("安装记录", lock_path),
    ]
    for relative, full in entries:
        candidates.append((relative, version_root / relative))
        actual_relative = full.relative_to(source).as_posix()
        if actual_relative != relative:
            candidates.append((actual_relative, version_root / actual_relative))
    for label, path in candidates:
        units = _utf16_units(str(path))
        if units > MAX_ABSOLUTE_UTF16:
            raise InstallerError(
                "path_too_long",
                f"安装后绝对路径超过 {MAX_ABSOLUTE_UTF16} 个 UTF-16 码元"
                f"（{units}）: {path}（{label}）；请缩短安装根",
            )
