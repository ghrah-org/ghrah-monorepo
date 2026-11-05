# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``sha256-tree-v1`` 整树摘要与文件枚举的验收测试。

摘要公式是安装记录自洽与运行版本身份的唯一来源；这里逐条钉死：
文件字节摘要、路径 NFC 归一与升序、缓存/垃圾文件排除、链接拒绝、
以及 ``package_digest`` 的拼接公式与顺序无关性。
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from pathlib import Path

import pytest

from ghrah.ability.package.digest import (
    DIGEST_ALGORITHM,
    SHA256_DIGEST_PATTERN,
    collect_files,
    file_digest,
    tree_digest,
)
from ghrah.ability.package.paths import PathRuleError

WINDOWS = os.name == "nt"


def _write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(data)
    return path


def test_digest_algorithm_is_frozen_value() -> None:
    assert DIGEST_ALGORITHM == "sha256-tree-v1"


def test_file_digest_known_bytes(tmp_path: Path) -> None:
    path = _write(tmp_path / "hello.txt", b"hello")
    expected = "sha256:" + hashlib.sha256(b"hello").hexdigest()
    assert file_digest(path) == expected
    assert SHA256_DIGEST_PATTERN.fullmatch(file_digest(path))


def test_file_digest_str_path_accepted(tmp_path: Path) -> None:
    path = _write(tmp_path / "x.bin", b"\x00\x01")
    assert file_digest(str(path)) == "sha256:" + hashlib.sha256(b"\x00\x01").hexdigest()


def test_collect_files_sorted_and_complete(tmp_path: Path) -> None:
    _write(tmp_path / "ability.yaml", "schema_version: '1'\n")
    _write(tmp_path / "operations" / "render.py", "x = 1\n")
    _write(tmp_path / "README.md", "# readme\n")
    files = collect_files(tmp_path)
    assert sorted(files) == ["README.md", "ability.yaml", "operations/render.py"]
    assert list(files) == sorted(files)


def test_collect_files_uses_nfc_keys(tmp_path: Path) -> None:
    decomposed = unicodedata.normalize("NFD", "café.md")
    _write(tmp_path / decomposed, "body\n")
    files = collect_files(tmp_path)
    assert list(files) == [unicodedata.normalize("NFC", decomposed)]


def test_collect_files_excludes_cache_and_junk(tmp_path: Path) -> None:
    _write(tmp_path / "ability.yaml", "schema_version: '1'\n")
    _write(tmp_path / ".git" / "config", "x\n")
    _write(tmp_path / "__pycache__" / "mod.pyc", b"\x00")
    _write(tmp_path / ".mypy_cache" / "state.json", "{}")
    _write(tmp_path / ".pytest_cache" / "v" / "cache", "x\n")
    _write(tmp_path / ".ruff_cache" / "c", "x\n")
    _write(tmp_path / ".DS_Store", b"\x00")
    _write(tmp_path / "Thumbs.db", b"\x00")
    _write(tmp_path / "desktop.ini", b"\x00")
    assert list(collect_files(tmp_path)) == ["ability.yaml"]


def test_collect_files_rejects_text_bom_and_crlf(tmp_path: Path) -> None:
    _write(tmp_path / "bom.md", b"\xef\xbb\xbfbody\n")
    with pytest.raises(PathRuleError, match="BOM"):
        collect_files(tmp_path)
    _write(tmp_path / "bom.md", "ok\n")
    _write(tmp_path / "crlf.py", b"x = 1\r\n")
    with pytest.raises(PathRuleError, match="CR"):
        collect_files(tmp_path)


@pytest.mark.skipif(WINDOWS, reason="Windows 上创建符号链接需要额外权限")
def test_collect_files_rejects_symlink(tmp_path: Path) -> None:
    _write(tmp_path / "real.txt", "data\n")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    with pytest.raises(PathRuleError, match="链接"):
        collect_files(tmp_path)


def test_collect_files_rejects_case_collision(tmp_path: Path) -> None:
    _write(tmp_path / "A.md", "a\n")
    _write(tmp_path / "a.md", "b\n")
    if len({p.name for p in tmp_path.iterdir()}) != 2:
        pytest.skip("文件系统大小写不敏感，无法构造只差大小写的两个文件")
    with pytest.raises(PathRuleError, match="冲突"):
        collect_files(tmp_path)


def test_tree_digest_matches_frozen_formula() -> None:
    files = {
        "operations/render.py": "sha256:" + "1" * 64,
        "ability.yaml": "sha256:" + "2" * 64,
    }
    payload = b"".join(f"file\0{path}\0{files[path]}\n".encode() for path in sorted(files))
    expected = "sha256:" + hashlib.sha256(payload).hexdigest()
    assert tree_digest(files) == expected


def test_tree_digest_order_independent() -> None:
    first = {"b.txt": "sha256:" + "b" * 64, "a.txt": "sha256:" + "a" * 64}
    second = {"a.txt": "sha256:" + "a" * 64, "b.txt": "sha256:" + "b" * 64}
    assert tree_digest(first) == tree_digest(second)


def test_tree_digest_changes_with_content_and_path() -> None:
    base = {"ability.yaml": "sha256:" + "a" * 64}
    assert tree_digest(base) != tree_digest({"ability.yaml": "sha256:" + "b" * 64})
    assert tree_digest(base) != tree_digest({"other.yaml": "sha256:" + "a" * 64})
    assert tree_digest(base) != tree_digest({"ability.yaml": file_digest_str("hello")})


def file_digest_str(data: str) -> str:
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def test_collect_files_is_stable(tmp_path: Path) -> None:
    _write(tmp_path / "ability.yaml", "a: 1\n")
    _write(tmp_path / "dir" / "b.bin", b"\x01\x02\x03")
    assert collect_files(tmp_path) == collect_files(tmp_path)


def test_collect_files_result_feeds_tree_digest(tmp_path: Path) -> None:
    _write(tmp_path / "ability.yaml", "a: 1\n")
    first = tree_digest(collect_files(tmp_path))
    _write(tmp_path / "ability.yaml", "a: 2\n")
    second = tree_digest(collect_files(tmp_path))
    assert first != second
    assert SHA256_DIGEST_PATTERN.fullmatch(first)
