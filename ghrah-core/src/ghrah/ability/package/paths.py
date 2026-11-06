# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""包内相对路径的可移植性规则（纯函数实现）。

规则在**生成期**执行：失败即拒绝，不做隐式修正。这里只做字符串与结构判定，
不触碰磁盘；磁盘侧的链接/junction 检查见 :func:`ensure_portable_tree`。
"""

from __future__ import annotations

import os
import stat
import unicodedata
from pathlib import Path

__all__ = [
    "MAX_RELATIVE_UTF16",
    "MAX_SEGMENT_BYTES",
    "PathRuleError",
    "check_relative_path",
    "derive_resource_key",
    "ensure_portable_tree",
    "is_reserved_windows_name",
    "nfc",
]

MAX_SEGMENT_BYTES = 255
"""单段长度上限（UTF-8 字节）。"""

MAX_RELATIVE_UTF16 = 180
"""包内相对路径上限（UTF-16 码元）。"""

_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        "CLOCK$",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_FORBIDDEN_CHARS = frozenset('<>:"|?*') | frozenset(chr(i) for i in range(0x20))


class PathRuleError(ValueError):
    """路径违反可移植性规则；消息面向包作者，可直接展示。"""


def nfc(text: str) -> str:
    """NFC 归一（只用于摘要与冲突检查，不重命名磁盘文件）。"""
    return unicodedata.normalize("NFC", text)


def is_reserved_windows_name(segment: str) -> bool:
    """Windows 保留设备名判定：大小写不敏感、带扩展名也算（``CON.txt`` 也拒绝）。"""
    stem = segment.split(".", 1)[0].rstrip(" .")
    return stem.upper() in _RESERVED_NAMES


def check_relative_path(path: str, *, what: str) -> str:
    """校验包内相对路径并返回 NFC 归一结果。

    逐条执行路径规则：分隔符、绝对/越界、禁用字符、结尾点/空格、
    保留名、Unicode 冲突基线（NFC）、长度。

    Args:
        path: 声明里的相对路径（``/`` 分隔）。
        what: 报错时的定位标签（如 ``members.skills.x.body.path``）。

    Returns:
        NFC 归一后的路径。

    Raises:
        PathRuleError: 任一规则不满足。
    """
    if not path:
        raise PathRuleError(f"{what}: 路径为空")
    if "\\" in path:
        raise PathRuleError(f"{what}: 路径必须用 `/` 分隔，出现反斜杠: {path!r}")
    normalized = nfc(path)
    if normalized.startswith("/"):
        raise PathRuleError(f"{what}: 拒绝绝对路径: {path!r}")
    if len(path) >= 2 and path[1] == ":":
        raise PathRuleError(f"{what}: 拒绝盘符路径: {path!r}")
    if path.startswith("//"):
        raise PathRuleError(f"{what}: 拒绝 UNC/设备前缀路径: {path!r}")
    segments = path.split("/")
    for segment in segments:
        if not segment:
            raise PathRuleError(f"{what}: 路径含空段: {path!r}")
        if segment == "." or segment == "..":
            raise PathRuleError(f"{what}: 路径含 `.`/`..` 段: {path!r}")
        if segment[-1] in ". ":
            raise PathRuleError(f"{what}: 路径段以点或空格结尾: {segment!r}")
        bad = _FORBIDDEN_CHARS.intersection(segment)
        if bad:
            raise PathRuleError(f"{what}: 路径段含禁用字符 {sorted(bad)}: {segment!r}")
        if is_reserved_windows_name(segment):
            raise PathRuleError(f"{what}: Windows 保留名: {segment!r}")
        size = len(segment.encode("utf-8"))
        if size > MAX_SEGMENT_BYTES:
            raise PathRuleError(f"{what}: 路径段超过 {MAX_SEGMENT_BYTES} UTF-8 字节: {segment!r}")
    utf16_units = len(path.encode("utf-16-le")) // 2
    if utf16_units > MAX_RELATIVE_UTF16:
        raise PathRuleError(f"{what}: 相对路径超过 {MAX_RELATIVE_UTF16} UTF-16 码元: {path!r}")
    return normalized


def derive_resource_key(path: str) -> str:
    """资源键派生：去掉文件后缀、目录分隔换 ``-``。

    ``skills/<name>/references/api/auth.md`` → ``api-auth``。
    """
    stem = path.rsplit(".", 1)[0] if "." in path.rsplit("/", 1)[-1] else path
    return stem.replace("/", "-")


_WALK_EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
"""摘要/枚举排除的缓存目录（casefold 匹配）。"""

_WALK_EXCLUDED_FILES = {".ds_store", "thumbs.db", "desktop.ini"}
"""排除的已知垃圾文件。"""

_TEXT_SUFFIXES = frozenset({".py", ".md", ".yaml", ".yml", ".json"})
"""需要检查 BOM / CRLF 的文本源后缀。"""


def _check_text_source(rel: str, data: bytes) -> None:
    """拒绝文本源的 BOM 与 CRLF；空字节也视为非文本。"""
    if data.startswith(b"\xef\xbb\xbf"):
        raise PathRuleError(
            f"{rel}: 文本源含 BOM；须 UTF-8 无 BOM（建议 .gitattributes: * text=auto eol=lf）"
        )
    if b"\r" in data:
        raise PathRuleError(
            f"{rel}: 文本源含 CR/CRLF；须 LF 换行（建议 .gitattributes: * text=auto eol=lf）"
        )


def ensure_portable_tree(root: Path) -> list[tuple[str, Path]]:
    """枚举包文件并执行磁盘侧可移植检查（链接/junction、普通文件、编码）。

    Args:
        root: 源包根目录。

    Returns:
        ``(nfc 相对路径, 绝对路径)`` 列表，按 NFC 码点升序。

    Raises:
        PathRuleError: 发现符号链接/junction、非普通文件、路径冲突或文本源编码问题。
    """
    entries: list[tuple[str, Path]] = []
    seen: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept: list[str] = []
        for name in dirnames:
            _reject_link(Path(dirpath) / name)
            if name.casefold() not in _WALK_EXCLUDED_DIRS:
                kept.append(name)
        dirnames[:] = kept
        for filename in filenames:
            if filename.casefold() in _WALK_EXCLUDED_FILES:
                continue
            full = Path(dirpath) / filename
            _reject_link(full)
            rel = full.relative_to(root).as_posix()
            check_relative_path(rel, what=f"包内文件 {rel!r}")
            info = full.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise PathRuleError(f"非普通文件: {full}")
            if full.suffix.lower() in _TEXT_SUFFIXES:
                _check_text_source(rel, full.read_bytes())
            normalized = nfc(rel)
            folded = normalized.casefold()
            if folded in seen:
                raise PathRuleError(
                    f"路径冲突（NFC + casefold 后相同）: {seen[folded]!r} 与 {normalized!r}"
                )
            seen[folded] = normalized
            entries.append((normalized, full))
    return sorted(entries)


def _reject_link(path: Path) -> bool:
    """链接/junction 一律拒绝；返回是否为链接（供 dirnames 过滤）。"""
    if path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
        raise PathRuleError(f"拒绝符号链接/junction: {path}")
    return False
