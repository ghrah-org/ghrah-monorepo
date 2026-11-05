# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""安装记录 ``<version>.lock.yaml``：字段、序列化与严格读取校验。

lock 不是包内容，也不是运行时契约：它是安装器在**版本目录旁**写下的安装记录，
字段与校验以 v1 冻结口径为准（见 M0 规范冻结 §3.10）。写者只有安装器；
读者是 ``InstalledStore`` / ``verify`` / ``list`` / ``show`` / ``select_instance``。

本模块只做"锁"这一件事：

- 固定键序：``schema / package_digest / digest_algorithm / installed_at /
  source{kind, ref} / files / mcp``；固定首行注释；UTF-8 / LF；
  ``safe_dump(..., sort_keys=False, allow_unicode=True,
  default_flow_style=False, width=4096)``；
- 写入走同目录临时文件 + ``os.replace``；
- 读取严格校验：重复键拒绝（:func:`load_yaml`）、未知键拒绝、schema == 1、
  摘要/算法/时间/来源/文件清单形状、``mcp`` 条件块，以及锁内
  ``package_digest`` 必须等于由 ``files`` 复算的整树摘要。

**不复用**旧版 per-skill lock（``ability.lock.yaml``）的 schema 与读写函数；
``LOCK_FILE_NAME`` 只描述源包禁止携带的旧锁文件名，本模块的安装记录文件名是
:func:`lock_filename` 生成的 ``<version>.lock.yaml``。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ghrah.ability.loading.ability_schema import load_yaml
from ghrah.ability.loading.manifest_fields import ManifestValidationError
from ghrah.ability.package.digest import (
    DIGEST_ALGORITHM,
    SHA256_DIGEST_PATTERN,
    tree_digest,
)
from ghrah.ability.package.paths import PathRuleError, check_relative_path

__all__ = [
    "DIGEST_ALGORITHM",
    "LOCK_CODE_DIGEST_MISMATCH",
    "LOCK_CODE_INVALID",
    "LOCK_CODE_SCHEMA_UNSUPPORTED",
    "LOCK_CODE_WRITE_FAILED",
    "LOCK_HEADER",
    "LOCK_SCHEMA_VERSION",
    "LOCK_SUFFIX",
    "InstallLock",
    "InstallSource",
    "LockError",
    "McpLock",
    "is_lock_filename",
    "lock_filename",
    "parse_lock_filename",
    "read_lock",
    "render_lock",
    "utc_now_rfc3339",
    "write_lock",
]

LOCK_SCHEMA_VERSION: int = 1
"""安装记录 schema 版本；v1 固定 ``1``，旧版不读、不自动改写。"""

LOCK_SUFFIX: str = ".lock.yaml"
"""安装记录文件后缀：``<version>.lock.yaml``（与版本目录同级）。"""

LOCK_HEADER: str = "# ability.lock.yaml —— 安装期生成，勿手改\n"
"""安装记录固定首行（注释）。"""

LOCK_CODE_INVALID: str = "lock_invalid"
LOCK_CODE_SCHEMA_UNSUPPORTED: str = "lock_schema_unsupported"
LOCK_CODE_DIGEST_MISMATCH: str = "lock_digest_mismatch"
LOCK_CODE_WRITE_FAILED: str = "lock_write_failed"

_SOURCE_KINDS: frozenset[str] = frozenset({"directory", "archive", "mcp"})
_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"schema", "package_digest", "digest_algorithm", "installed_at", "source", "files", "mcp"}
)
_SOURCE_KEYS: frozenset[str] = frozenset({"kind", "ref"})
_MCP_KEYS: frozenset[str] = frozenset({"fetched_at", "stale", "last_error"})
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)
_EXCLUDED_FILES: frozenset[str] = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
_REQUIRED_FILE: str = "ability.yaml"
_UTC_OFFSET = timedelta(0)


class LockError(ValueError):
    """安装记录损坏或读写失败；``code`` 供上层透传，``message`` 面向人。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class InstallSource:
    """安装来源：``kind`` 属于 ``directory`` / ``archive`` / ``mcp``。

    ``ref`` 是来源路径（MCP 可省）。它不参与摘要，也不进 ``ability.yaml``。
    """

    kind: str
    ref: str | None = None


@dataclass(frozen=True)
class McpLock:
    """MCP 快照的缓存健康块；只有 ``origin: mcp`` 的包才有。"""

    fetched_at: str
    stale: bool
    last_error: str | None = None


@dataclass(frozen=True, kw_only=True)
class InstallLock:
    """``<version>.lock.yaml`` 的内存形状（v1）。

    不含 ``identity`` / ``version``：身份以安装路径与 ``ability.yaml`` 为准，
    lock 不制造第二份身份。也不含 ``permissions`` / ``protected`` /
    ``index_digest`` 与整树文本。
    """

    package_digest: str
    digest_algorithm: str
    installed_at: str
    source: InstallSource
    files: dict[str, str]
    mcp: McpLock | None = None
    schema: int = LOCK_SCHEMA_VERSION


def lock_filename(version: str) -> str:
    """版本目录旁边的安装记录文件名（``<version>.lock.yaml``）。"""
    return f"{version}{LOCK_SUFFIX}"


def is_lock_filename(name: str) -> bool:
    """文件名是否为安装记录（只看后缀，大小写不敏感）。"""
    return name.casefold().endswith(LOCK_SUFFIX)


def parse_lock_filename(name: str) -> str | None:
    """从安装记录文件名取出 ``<version>``；不是安装记录则返回 ``None``。

    只做字符串切分，不校验 SemVer（调用方按目录枚举结果配对）。
    """
    if not is_lock_filename(name):
        return None
    version = name[: -len(LOCK_SUFFIX)]
    return version or None


def utc_now_rfc3339() -> str:
    """当前 UTC 时刻的 RFC 3339 文本（秒精度，``Z`` 结尾）。"""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_utc_timestamp(value: Any) -> bool:
    """RFC 3339 UTC 判定：可解析且显式带时区、偏移为零。"""
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    return parsed.utcoffset() == _UTC_OFFSET


def _source_document(source: InstallSource) -> dict[str, Any]:
    document: dict[str, Any] = {"kind": source.kind}
    if source.ref is not None:
        document["ref"] = source.ref
    return document


def _mcp_document(mcp: McpLock) -> dict[str, Any]:
    return {
        "fetched_at": mcp.fetched_at,
        "stale": mcp.stale,
        "last_error": mcp.last_error,
    }


def _lock_document(lock: InstallLock) -> dict[str, Any]:
    """按冻结键序构造落盘文档。"""
    document: dict[str, Any] = {
        "schema": lock.schema,
        "package_digest": lock.package_digest,
        "digest_algorithm": lock.digest_algorithm,
        "installed_at": lock.installed_at,
        "source": _source_document(lock.source),
        "files": {path: lock.files[path] for path in sorted(lock.files)},
    }
    if lock.mcp is not None:
        document["mcp"] = _mcp_document(lock.mcp)
    return document


def render_lock(lock: InstallLock) -> str:
    """渲染落盘文本：固定首行 + 固定键序 YAML。

    Raises:
        LockError: 内存中的 lock 本身不满足 v1 校验（拒绝写出损坏记录）。
    """
    document = _lock_document(lock)
    # 写前自检：内存对象与落盘后的读回必须同构，否则宁可失败也不写坏记录。
    _lock_from_document(document)
    body = yaml.safe_dump(
        document,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=4096,
    )
    return LOCK_HEADER + body


def write_lock(path: str | Path, lock: InstallLock) -> None:
    """同目录临时文件 + ``os.replace`` 原子写入安装记录。

    Args:
        path: 目标 lock 路径（``<版本目录旁>/<version>.lock.yaml``）。
        lock: 待写入的安装记录。

    Raises:
        LockError: lock 不合法或写入失败。
    """
    target = Path(path)
    text = render_lock(lock)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(tmp, target)
    except OSError as exc:
        raise LockError(LOCK_CODE_WRITE_FAILED, f"安装记录写入失败: {target}: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)


def read_lock(path: str | Path) -> InstallLock:
    """严格读取并校验安装记录。

    Args:
        path: lock 文件路径。

    Returns:
        校验通过的 :class:`InstallLock`。

    Raises:
        LockError: 文件不可读、含 BOM、YAML 非法/多文档/重复键、未知键、
            schema 不支持、字段形状非法，或 ``package_digest`` 与 ``files``
            不自洽（``lock_digest_mismatch``）。
    """
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise LockError(LOCK_CODE_INVALID, f"安装记录读取失败: {target}: {exc}") from exc
    if raw.startswith("\ufeff"):
        raise LockError(LOCK_CODE_INVALID, f"安装记录含 BOM（须 UTF-8 无 BOM）: {target}")
    try:
        data = load_yaml(raw)
    except (yaml.YAMLError, ManifestValidationError) as exc:
        raise LockError(LOCK_CODE_INVALID, f"安装记录 YAML 解析失败: {target}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise LockError(LOCK_CODE_INVALID, f"安装记录顶层不是映射: {target}")
    return _lock_from_document(data)


_MISSING = object()


def _lock_from_document(data: Mapping[str, Any]) -> InstallLock:
    """把映射校验成 :class:`InstallLock`；所有字段规则集中在这里。"""
    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise LockError(LOCK_CODE_INVALID, f"安装记录含未知键: {sorted(unknown)}")

    schema = data.get("schema")
    if type(schema) is not int or schema != LOCK_SCHEMA_VERSION:
        raise LockError(
            LOCK_CODE_SCHEMA_UNSUPPORTED,
            f"安装记录 schema={schema!r}，当前只支持 {LOCK_SCHEMA_VERSION}"
            "（旧 lock 需重装，不自动改写）",
        )

    package_digest = data.get("package_digest")
    if not isinstance(package_digest, str) or not SHA256_DIGEST_PATTERN.fullmatch(package_digest):
        raise LockError(
            LOCK_CODE_INVALID, "安装记录 package_digest 必须是 sha256:<64 位小写十六进制>"
        )

    algorithm = data.get("digest_algorithm")
    if algorithm != DIGEST_ALGORITHM:
        raise LockError(
            LOCK_CODE_INVALID,
            f"安装记录 digest_algorithm 必须是 {DIGEST_ALGORITHM!r}，实际 {algorithm!r}",
        )

    installed_at = data.get("installed_at")
    if not _is_utc_timestamp(installed_at):
        raise LockError(LOCK_CODE_INVALID, "安装记录 installed_at 必须是 RFC 3339 UTC 时间")

    source = _read_source(data.get("source"))
    files = _read_files(data.get("files"))
    mcp = _read_mcp(data["mcp"] if "mcp" in data else _MISSING, source=source)

    lock = InstallLock(
        package_digest=package_digest,
        digest_algorithm=algorithm,
        installed_at=installed_at,
        source=source,
        files=files,
        mcp=mcp,
    )
    computed = tree_digest(lock.files)
    if computed != lock.package_digest:
        raise LockError(
            LOCK_CODE_DIGEST_MISMATCH,
            f"安装记录 package_digest 与 files 复算结果不一致: {lock.package_digest} != {computed}",
        )
    return lock


def _read_source(raw: Any) -> InstallSource:
    if not isinstance(raw, Mapping):
        raise LockError(LOCK_CODE_INVALID, "安装记录 source 必须是映射")
    unknown = set(raw) - _SOURCE_KEYS
    if unknown:
        raise LockError(LOCK_CODE_INVALID, f"安装记录 source 含未知键: {sorted(unknown)}")
    kind = raw.get("kind")
    if kind not in _SOURCE_KINDS:
        raise LockError(
            LOCK_CODE_INVALID,
            f"安装记录 source.kind 必须是 {sorted(_SOURCE_KINDS)} 之一，实际 {kind!r}",
        )
    ref = raw.get("ref", _MISSING)
    if kind == "mcp":
        if ref is not _MISSING and (not isinstance(ref, str) or not ref):
            raise LockError(LOCK_CODE_INVALID, "安装记录 source.ref 必须是非空字符串或省略（MCP）")
        return InstallSource(kind=kind, ref=None if ref is _MISSING else ref)
    if not isinstance(ref, str) or not ref:
        raise LockError(LOCK_CODE_INVALID, f"安装记录 source.kind={kind!r} 时 source.ref 必填非空")
    return InstallSource(kind=kind, ref=ref)


def _read_files(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping) or not raw:
        raise LockError(LOCK_CODE_INVALID, "安装记录 files 必须是非空映射")
    seen: dict[str, str] = {}
    files: dict[str, str] = {}
    for path, digest in raw.items():
        if not isinstance(path, str):
            raise LockError(LOCK_CODE_INVALID, f"安装记录 files 的键必须是字符串: {path!r}")
        try:
            normalized = check_relative_path(path, what="安装记录 files 路径")
        except PathRuleError as exc:
            raise LockError(LOCK_CODE_INVALID, str(exc)) from exc
        if normalized != path:
            raise LockError(LOCK_CODE_INVALID, f"安装记录 files 路径不是 NFC 归一形式: {path!r}")
        folded = normalized.casefold()
        if folded in seen:
            raise LockError(
                LOCK_CODE_INVALID,
                f"安装记录 files 路径只差大小写: {seen[folded]!r} 与 {path!r}",
            )
        seen[folded] = path
        segments = path.split("/")
        if any(segment.casefold() in _EXCLUDED_DIRS for segment in segments[:-1]):
            raise LockError(LOCK_CODE_INVALID, f"安装记录 files 不得包含缓存/.git 目录: {path!r}")
        if segments[-1].casefold() in _EXCLUDED_FILES:
            raise LockError(LOCK_CODE_INVALID, f"安装记录 files 不得包含已知垃圾文件: {path!r}")
        if path.casefold().endswith(LOCK_SUFFIX):
            raise LockError(LOCK_CODE_INVALID, f"安装记录 files 不得包含安装记录自身: {path!r}")
        if not isinstance(digest, str) or not SHA256_DIGEST_PATTERN.fullmatch(digest):
            raise LockError(
                LOCK_CODE_INVALID, f"安装记录 files[{path!r}] 必须是 sha256:<64 位小写十六进制>"
            )
        files[path] = digest
    if _REQUIRED_FILE not in files:
        raise LockError(LOCK_CODE_INVALID, f"安装记录 files 必须包含 {_REQUIRED_FILE}")
    return {path: files[path] for path in sorted(files)}


def _read_mcp(raw: Any, *, source: InstallSource) -> McpLock | None:
    if source.kind != "mcp":
        if raw is not _MISSING:
            raise LockError(LOCK_CODE_INVALID, "非 MCP 包的安装记录不得出现 mcp 段")
        return None
    if raw is _MISSING:
        raise LockError(LOCK_CODE_INVALID, "MCP 包的安装记录必须包含 mcp 段")
    if not isinstance(raw, Mapping):
        raise LockError(LOCK_CODE_INVALID, "安装记录 mcp 段必须是映射")
    unknown = set(raw) - _MCP_KEYS
    if unknown:
        raise LockError(LOCK_CODE_INVALID, f"安装记录 mcp 段含未知键: {sorted(unknown)}")
    fetched_at = raw.get("fetched_at")
    if not _is_utc_timestamp(fetched_at):
        raise LockError(LOCK_CODE_INVALID, "安装记录 mcp.fetched_at 必须是 RFC 3339 UTC 时间")
    stale = raw.get("stale")
    if type(stale) is not bool:
        raise LockError(LOCK_CODE_INVALID, "安装记录 mcp.stale 必须是 bool")
    last_error = raw.get("last_error")
    if last_error is not None and not isinstance(last_error, str):
        raise LockError(LOCK_CODE_INVALID, "安装记录 mcp.last_error 必须是字符串或 null")
    return McpLock(fetched_at=fetched_at, stale=stale, last_error=last_error)
