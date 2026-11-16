# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力包归档：格式识别、安全解包与目录树校验。

上传的归档先落到本机临时文件，本模块负责把它变成「一棵可信的源包目录」，
安装语义仍留在 :class:`~ghrah.ability.package.installer.AbilityInstaller`。

三条边界：

- **格式**：只接受 ``zip``（标准库解）；其它格式明确报「不支持」而不是静默失败。
- **安全**：路径穿越、符号链接、绝对路径、条目数与解压体积上限，
  压缩比异常按 zip bomb 拒绝。
- **可移植性**：解包出来的相对路径复用安装器的路径规则，
  文件名非法（Windows 保留名、控制字符等）直接拒绝。
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ghrah.ability.package.installed_store import is_link
from ghrah.ability.package.paths import PathRuleError, check_relative_path

__all__ = [
    "ARCHIVE_FORMATS",
    "ArchiveFormat",
    "ARCHIVE_SUFFIXES",
    "MAX_TREE_DEPTH",
    "ArchiveError",
    "ArchiveLimits",
    "DEFAULT_LIMITS",
    "detect_format",
    "extract_package",
]

type ArchiveFormat = Literal["zip"]

#: 支持的归档格式；值同时用于 ``AbilityPackageImportView.archive_format``。
ARCHIVE_FORMATS: tuple[ArchiveFormat, ...] = ("zip",)

#: 扩展名 → 格式。只用于魔数不可识别时的兜底。
ARCHIVE_SUFFIXES: dict[str, ArchiveFormat] = {".zip": "zip"}

#: 解包后允许的最大目录深度。
MAX_TREE_DEPTH = 16



class ArchiveError(ValueError):
    """归档不可用；``code`` 映射到 Core 的错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ArchiveLimits:
    """解包预算。

    Attributes:
        max_entries: 条目数上限
        max_total_bytes: 解压后总体积上限
        max_file_bytes: 单文件体积上限
        max_ratio: 解压总体积 / 归档体积 的上限（防压缩炸弹）
    """

    max_entries: int = 20_000
    max_total_bytes: int = 1 << 30
    max_file_bytes: int = 512 << 20
    max_ratio: int = 200


#: 默认预算：一个能力包正常在几 MB 量级，上面的值留足了余量又不至于坏盘。
DEFAULT_LIMITS = ArchiveLimits()

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
)


def detect_format(archive: Path, *, name: str | None = None) -> ArchiveFormat:
    """按魔数识别归档格式，识别不了再看扩展名。

    Args:
        archive: 归档文件路径
        name: 原始文件名（可省）；只用于扩展名兜底

    Returns:
        ``"zip"`` / ``"7z"`` / ``"rar"``

    Raises:
        ArchiveError: 既不是已知魔数，也不是已知扩展名
    """
    try:
        with archive.open("rb") as handle:
            head = handle.read(8)
    except OSError as exc:
        raise ArchiveError("invalid_request", f"归档读不出来: {exc}") from exc
    for magic, fmt in _MAGIC:
        if head.startswith(magic):
            return fmt
    suffix = Path(name or archive.name).suffix.lower()
    fmt = ARCHIVE_SUFFIXES.get(suffix)
    if fmt is None:
        raise ArchiveError(
            "unsupported_format",
            f"不支持的归档格式: {suffix or '（无扩展名）'}；支持 zip / 7z / rar",
        )
    return fmt


def extract_package(
    archive: Path,
    dest: Path,
    *,
    format: ArchiveFormat,
    limits: ArchiveLimits = DEFAULT_LIMITS,
) -> Path:
    """把归档解包到 ``dest``，返回源包根目录。

    Args:
        archive: 归档文件
        dest: 解包目标目录（调用方保证是空目录）
        format: :func:`detect_format` 的结果
        limits: 解包预算

    Returns:
        含 ``ability.yaml`` 的源包根

    Raises:
        ArchiveError: 格式不支持、内容越界或缺少 ``ability.yaml``
    """
    dest.mkdir(parents=True, exist_ok=True)
    if format != "zip":
        raise ArchiveError("unsupported_format", f"不支持的归档格式: {format}")
    _extract_zip(archive, dest, limits)
    root = _locate_root(dest)
    _check_tree(root, limits)
    return root


# ── zip：自己逐个条目写，路径与体积都在掌控里 ──


def _extract_zip(archive: Path, dest: Path, limits: ArchiveLimits) -> None:
    try:
        handle = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError("invalid_package", f"压缩包损坏或不是 zip: {exc}") from exc
    with handle:
        infos = [info for info in handle.infolist() if not info.is_dir()]
        if not infos:
            raise ArchiveError("invalid_package", "压缩包是空的")
        if len(infos) > limits.max_entries:
            raise ArchiveError(
                "invalid_package",
                f"压缩包条目过多（{len(infos)} > {limits.max_entries}）",
            )
        declared = sum(info.file_size for info in infos)
        if declared > limits.max_total_bytes:
            raise ArchiveError(
                "invalid_package",
                f"解压后体积超限（{declared} > {limits.max_total_bytes} 字节）",
            )
        archive_size = max(archive.stat().st_size, 1)
        if declared / archive_size > limits.max_ratio:
            raise ArchiveError("invalid_package", "压缩比异常，疑似压缩炸弹")
        written = 0
        for info in infos:
            if info.file_size > limits.max_file_bytes:
                raise ArchiveError(
                    "invalid_package",
                    f"单个文件过大: {info.filename}（{info.file_size} 字节）",
                )
            if _is_symlink_entry(info):
                raise ArchiveError("invalid_package", f"压缩包含符号链接: {info.filename}")
            target = _target_path(dest, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with handle.open(info) as source, target.open("wb") as sink:
                    copied = 0
                    while chunk := source.read(1 << 20):
                        copied += len(chunk)
                        written += len(chunk)
                        if copied > limits.max_file_bytes or written > limits.max_total_bytes:
                            raise ArchiveError("invalid_package", "解压体积超出预算")
                        sink.write(chunk)
            except ArchiveError:
                raise
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                raise ArchiveError("invalid_package", f"解包失败: {info.filename}: {exc}") from exc


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """zip 用外部属性高 16 位存 Unix mode；``0o120000`` 是符号链接。"""
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _target_path(dest: Path, raw: str) -> Path:
    """把归档里的条目名变成目标路径；越界一律拒绝。"""
    relative = _safe_relative(raw)
    return dest / relative


def _safe_relative(raw: str) -> str:
    """规范化条目名并复用安装器的相对路径规则。"""
    name = raw.replace("\\", "/").strip()
    while name.startswith("./"):
        name = name[2:]
    if not name:
        raise ArchiveError("invalid_package", "压缩包含空路径条目")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise ArchiveError("invalid_package", f"压缩包含绝对路径: {raw}")
    if any(part == ".." for part in name.split("/")):
        raise ArchiveError("invalid_package", f"压缩包含路径穿越: {raw}")
    try:
        return check_relative_path(name, what="归档条目")
    except PathRuleError as exc:
        raise ArchiveError("invalid_package", f"归档条目名不合法: {raw}: {exc}") from exc


# ── 解包后：定位源包根并校验目录树 ──


def _locate_root(dest: Path) -> Path:
    """归档可以「直接是包」，也可以「套一层顶层目录」。"""
    if (dest / "ability.yaml").is_file():
        return dest
    children = [child for child in dest.iterdir() if not child.name.startswith("__MACOSX")]
    directories = [child for child in children if child.is_dir() and not is_link(child)]
    if len(directories) == 1 and (directories[0] / "ability.yaml").is_file():
        return directories[0]
    raise ArchiveError(
        "invalid_package",
        "压缩包里没有 ability.yaml；能力包要么是包目录本身，要么只有一层顶层目录",
    )


def _check_tree(root: Path, limits: ArchiveLimits) -> None:
    """拒绝链接、过深目录与预算外的文件数。"""
    seen = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_TREE_DEPTH:
            raise ArchiveError("invalid_package", f"目录层级过深（>{MAX_TREE_DEPTH}）")
        for entry in current.iterdir():
            if is_link(entry):
                raise ArchiveError("invalid_package", f"源包含符号链接: {entry.name}")
            if entry.is_dir():
                stack.append((entry, depth + 1))
                continue
            seen += 1
            if seen > limits.max_entries:
                raise ArchiveError("invalid_package", "源包文件数超出预算")
