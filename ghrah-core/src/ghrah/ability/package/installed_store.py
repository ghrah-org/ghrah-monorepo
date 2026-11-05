# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""安装存储的三层枚举：``<root>/<namespace>/<name>/<version>/``。

安装存储**不是**目录池：它只认三层（namespace / name / version），不产出
``AbilityRoot``，也不调用 ``scan_abilities``。读取方（``list`` / ``show`` /
``verify`` / ``select_instance``）只从路径取安装事实，版本目录旁的同名
``<version>.lock.yaml`` 是安装记录。

冻结口径（见 M0 规范冻结 §3.8）：

- ``namespace`` / ``name`` 使用身份字符集；``name`` 从 ``ability.yaml`` 的
  ``identity`` 读取，``name`` 缺省时安装槽退化为 ``<namespace>/<namespace>``，
  不引入第二种路径形状；
- ``version`` 是 SemVer core 三段，直接作路径段；
- 忽略 ``.tmp`` / ``.trash`` 等临时项（所有 ``.`` 开头的条目不参与枚举）；
- 符号链接 / junction 一律拒绝、不跟随；
- 版本目录与旁 lock 按路径配对：lock 缺失可降级（``problem == "lock_missing"``），
  没有对应版本目录的孤儿 lock 不是安装事实，枚举时忽略。
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from ghrah.ability.loading.ability_schema import V1_SEMVER_PATTERN
from ghrah.ability.package.lock import (
    LOCK_CODE_INVALID,
    InstallLock,
    InstallSource,
    LockError,
    McpLock,
    is_lock_filename,
    lock_filename,
    parse_lock_filename,
    read_lock,
)

__all__ = [
    "InstalledPackage",
    "InstalledStore",
    "InstalledVersion",
    "is_link",
    "parse_full_name",
    "version_sort_key",
]

_IDENTITY_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_-]*$")
"""``namespace`` 与 ``name`` 的身份字符集（见 §3.2）。"""

_LOCK_MISSING: str = "lock_missing"


def parse_full_name(full_name: str) -> tuple[str, str] | None:
    """把身份全名解析成 ``(namespace, name)`` 两个路径段。

    全名最多一段点分（身份字符集不含点）：``"fixtures.reporting"`` 拆成两段；
    ``"solo"`` 是 ``name`` 缺省的退化身份，目录段退化为 ``solo/solo``。
    解析失败返回 ``None``，不抛异常（读取方自行决定降级还是报错）。

    Args:
        full_name: 点分身份串。

    Returns:
        ``(namespace, name)``；非法返回 ``None``。
    """
    if not isinstance(full_name, str) or not full_name:
        return None
    parts = full_name.split(".")
    if len(parts) == 1:
        namespace = name = parts[0]
    elif len(parts) == 2:
        namespace, name = parts
    else:
        return None
    if not _IDENTITY_PATTERN.fullmatch(namespace) or not _IDENTITY_PATTERN.fullmatch(name):
        return None
    return namespace, name


def version_sort_key(version: str) -> tuple[int, int, int]:
    """SemVer core 三段的数值排序键（调用方须先确认格式合法）。"""
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def is_link(path: str | Path) -> bool:
    """符号链接、Windows junction 或不可判定项一律按链接处理。

    不调用 ``resolve()``，也不跟随链接；不存在返回 ``False``（不存在不是链接）。
    """
    target = Path(path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if stat.S_ISLNK(info.st_mode):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        try:
            return bool(isjunction(target))
        except OSError:
            return True
    return False


def _child_directories(directory: Path) -> list[str]:
    """列出直接子目录名（跳过 ``.`` 开头、符号链接/junction），按名称升序。"""
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return []
    result: list[str] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        path = Path(entry.path)
        if entry.is_symlink() or is_link(path):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                result.append(entry.name)
        except OSError:
            continue
    return sorted(result)


@dataclass(frozen=True)
class InstalledVersion:
    """一个已安装版本：版本目录 + 旁安装记录。

    ``lock`` 为 ``None`` 表示安装记录缺失或损坏，原因在 ``problem``；
    读取方据此降级（``list`` / ``show``）或拒绝（``verify`` / 选择）。
    """

    namespace: str
    name: str
    version: str
    root: Path
    lock_path: Path
    lock: InstallLock | None = None
    problem: str | None = None

    @property
    def full_name(self) -> str:
        """身份全名；``name`` 段等于 ``namespace`` 时视为 ``name`` 缺省的退化槽。"""
        if self.name == self.namespace:
            return self.namespace
        return f"{self.namespace}.{self.name}"

    @property
    def lock_state(self) -> str:
        """安装记录状态：``ok`` / ``missing`` / ``invalid``。"""
        if self.lock is not None:
            return "ok"
        if self.problem is not None and self.problem.startswith(_LOCK_MISSING):
            return "missing"
        return "invalid"

    @property
    def installed_at(self) -> str | None:
        """安装时刻（读 lock）；缺 lock 为 ``None``。"""
        return self.lock.installed_at if self.lock is not None else None

    @property
    def source(self) -> InstallSource | None:
        """安装来源（读 lock）；缺 lock 为 ``None``。"""
        return self.lock.source if self.lock is not None else None

    @property
    def mcp(self) -> McpLock | None:
        """MCP 健康块（读 lock）；非 MCP 或缺 lock 为 ``None``。"""
        return self.lock.mcp if self.lock is not None else None


@dataclass(frozen=True)
class InstalledPackage:
    """一个已安装包（同一身份的全版本集合），版本按 SemVer 数值升序。"""

    namespace: str
    name: str
    versions: tuple[InstalledVersion, ...]

    @property
    def full_name(self) -> str:
        """身份全名；``name`` 段等于 ``namespace`` 时视为退化槽。"""
        if self.name == self.namespace:
            return self.namespace
        return f"{self.namespace}.{self.name}"


def _read_version_lock(lock_path: Path) -> tuple[InstallLock | None, str | None]:
    """读取版本旁的安装记录；返回 ``(lock, problem)``，不抛异常。"""
    if is_link(lock_path):
        return None, f"{LOCK_CODE_INVALID}: 安装记录是符号链接/junction"
    try:
        lock_path.lstat()
    except FileNotFoundError:
        return None, _LOCK_MISSING
    except OSError as exc:
        return None, f"{LOCK_CODE_INVALID}: 安装记录不可读: {exc}"
    if not lock_path.is_file():
        return None, f"{LOCK_CODE_INVALID}: 安装记录不是普通文件"
    try:
        return read_lock(lock_path), None
    except LockError as exc:
        return None, f"{exc.code}: {exc.message}"


class InstalledStore:
    """安装存储的只读枚举器；不写盘、不跟随链接、不产出 ``AbilityRoot``。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        """安装根（调用方显式传入的发行版级存储根）。"""
        return self._root

    def packages(self) -> tuple[InstalledPackage, ...]:
        """枚举全部已安装包（按 full_name 升序）；根不存在时返回空元组。"""
        root = self._root
        if is_link(root) or not root.is_dir():
            return ()
        packages: list[InstalledPackage] = []
        for namespace in _child_directories(root):
            if not _IDENTITY_PATTERN.fullmatch(namespace):
                continue
            namespace_root = root / namespace
            for name in _child_directories(namespace_root):
                if not _IDENTITY_PATTERN.fullmatch(name):
                    continue
                versions = self._versions(namespace_root / name, namespace, name)
                if versions:
                    packages.append(
                        InstalledPackage(namespace=namespace, name=name, versions=versions)
                    )
        return tuple(sorted(packages, key=lambda package: package.full_name))

    def versions_of(self, full_name: str) -> tuple[InstalledVersion, ...]:
        """某个身份的全版本（SemVer 数值升序）；非法身份或不存在的包返回空元组。"""
        split = parse_full_name(full_name)
        if split is None:
            return ()
        namespace, name = split
        package_dir = self._root / namespace / name
        if (
            is_link(self._root)
            or is_link(package_dir.parent)
            or is_link(package_dir)
            or not package_dir.is_dir()
        ):
            return ()
        return self._versions(package_dir, namespace, name)

    def resolve(self, full_name: str, version: str) -> InstalledVersion | None:
        """按 ``full_name`` + ``version`` 定位一个已安装版本；不存在返回 ``None``。"""
        for item in self.versions_of(full_name):
            if item.version == version:
                return item
        return None

    def package_dir(self, full_name: str) -> Path | None:
        """包槽（``<root>/<namespace>/<name>``）；非法身份返回 ``None``。"""
        split = parse_full_name(full_name)
        if split is None:
            return None
        return self._root / split[0] / split[1]

    def orphan_locks(self, full_name: str) -> tuple[Path, ...]:
        """包槽里没有对应版本目录的孤儿 lock（不改变枚举结果，供显式清理）。"""
        package_dir = self.package_dir(full_name)
        if package_dir is None or is_link(package_dir) or not package_dir.is_dir():
            return ()
        known = {item.version for item in self.versions_of(full_name)}
        found: list[Path] = []
        try:
            entries = list(os.scandir(package_dir))
        except OSError:
            return ()
        for entry in entries:
            if not is_lock_filename(entry.name):
                continue
            version = parse_lock_filename(entry.name)
            if version is None or version in known:
                continue
            found.append(Path(entry.path))
        return tuple(sorted(found))

    def _versions(
        self, package_dir: Path, namespace: str, name: str
    ) -> tuple[InstalledVersion, ...]:
        versions: list[InstalledVersion] = []
        for version in _child_directories(package_dir):
            if not V1_SEMVER_PATTERN.fullmatch(version):
                continue
            version_root = package_dir / version
            lock_path = package_dir / lock_filename(version)
            lock, problem = _read_version_lock(lock_path)
            versions.append(
                InstalledVersion(
                    namespace=namespace,
                    name=name,
                    version=version,
                    root=version_root,
                    lock_path=lock_path,
                    lock=lock,
                    problem=problem,
                )
            )
        return tuple(sorted(versions, key=lambda item: version_sort_key(item.version)))
