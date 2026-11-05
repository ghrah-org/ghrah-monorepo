# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""安装记录 ``<version>.lock.yaml`` 的序列化与严格读取校验。

覆盖 v1 冻结口径：固定首行、固定键序、``files`` 升序、``mcp`` 条件块、
写读往返、原子写，以及"任一读取校验失败即视为损坏"的逐条红线。
"""

from __future__ import annotations

import stat
import unicodedata
from pathlib import Path
from typing import Any

import pytest
import yaml

from ghrah.ability.package.digest import DIGEST_ALGORITHM, tree_digest
from ghrah.ability.package.lock import (
    LOCK_CODE_DIGEST_MISMATCH,
    LOCK_CODE_INVALID,
    LOCK_CODE_SCHEMA_UNSUPPORTED,
    LOCK_HEADER,
    LOCK_SCHEMA_VERSION,
    LOCK_SUFFIX,
    InstallLock,
    InstallSource,
    LockError,
    McpLock,
    is_lock_filename,
    lock_filename,
    parse_lock_filename,
    read_lock,
    render_lock,
    utc_now_rfc3339,
    write_lock,
)

SHA = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def _files() -> dict[str, str]:
    return {"ability.yaml": SHA}


def _lock(**overrides: Any) -> InstallLock:
    files = overrides.pop("files", _files())
    values: dict[str, Any] = {
        "package_digest": tree_digest(files),
        "digest_algorithm": DIGEST_ALGORITHM,
        "installed_at": "2026-11-03T12:34:56Z",
        "source": InstallSource(kind="directory", ref="/home/user/pkgs/reporting"),
        "files": files,
    }
    values.update(overrides)
    return InstallLock(**values)


def _valid_text(**overrides: Any) -> str:
    return render_lock(_lock(**overrides))


def _document(**overrides: Any) -> dict[str, Any]:
    return yaml.safe_load(_valid_text(**overrides))


def _dump(document: Any) -> str:
    return LOCK_HEADER + yaml.safe_dump(
        document, sort_keys=False, allow_unicode=True, default_flow_style=False
    )


def _write_raw(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _mcp_document() -> dict[str, Any]:
    files = _files()
    lock = InstallLock(
        package_digest=tree_digest(files),
        digest_algorithm=DIGEST_ALGORITHM,
        installed_at="2026-11-03T12:34:56Z",
        source=InstallSource(kind="mcp"),
        files=files,
        mcp=McpLock(fetched_at="2026-11-03T12:34:56Z", stale=False, last_error=None),
    )
    return yaml.safe_load(render_lock(lock))


# --------------------------------------------------------------------- render


def test_lock_constants() -> None:
    assert LOCK_SUFFIX == ".lock.yaml"
    assert LOCK_HEADER == "# ability.lock.yaml —— 安装期生成，勿手改\n"
    assert lock_filename("0.1.0") == "0.1.0.lock.yaml"
    assert parse_lock_filename("0.1.0.lock.yaml") == "0.1.0"
    assert parse_lock_filename("ability.lock.yaml") == "ability"
    assert parse_lock_filename("notes.txt") is None
    assert is_lock_filename("x.LOCK.YAML")


def test_utc_now_rfc3339_shape() -> None:
    value = utc_now_rfc3339()
    assert value.endswith("Z")
    assert len(value) == len("2026-11-03T12:34:56Z")


def test_render_fixed_header_and_key_order() -> None:
    text = _valid_text()
    assert text.startswith(LOCK_HEADER)
    assert "\r" not in text
    top_level = [
        line.split(":", 1)[0]
        for line in text.splitlines()
        if line and not line.startswith((" ", "#"))
    ]
    assert top_level == [
        "schema",
        "package_digest",
        "digest_algorithm",
        "installed_at",
        "source",
        "files",
    ]
    assert "mcp" not in top_level


def test_render_mcp_is_last_block_and_has_three_keys() -> None:
    document = _mcp_document()
    assert list(document) == [
        "schema",
        "package_digest",
        "digest_algorithm",
        "installed_at",
        "source",
        "files",
        "mcp",
    ]
    assert list(document["mcp"]) == ["fetched_at", "stale", "last_error"]
    assert list(document["source"]) == ["kind"]


def test_render_files_sorted() -> None:
    files = {"operations/render.py": SHA, "ability.yaml": SHA_B}
    document = yaml.safe_load(_valid_text(files=files))
    assert list(document["files"]) == ["ability.yaml", "operations/render.py"]


def test_render_non_mcp_never_emits_mcp_key() -> None:
    assert "mcp:" not in _valid_text()


# ------------------------------------------------------------------ roundtrip


def test_write_read_roundtrip(tmp_path: Path) -> None:
    lock = _lock()
    path = tmp_path / lock_filename("0.1.0")
    write_lock(path, lock)
    assert not list(tmp_path.glob("*.tmp"))
    assert list(tmp_path.iterdir()) == [path]
    assert read_lock(path) == lock


def test_roundtrip_with_files_and_mcp(tmp_path: Path) -> None:
    files = {"ability.yaml": SHA, "README.md": SHA_B}
    lock = InstallLock(
        package_digest=tree_digest(files),
        digest_algorithm=DIGEST_ALGORITHM,
        installed_at="2026-11-03T12:34:56Z",
        source=InstallSource(kind="mcp"),
        files=files,
        mcp=McpLock(fetched_at="2026-11-03T12:34:56Z", stale=True, last_error="offline"),
    )
    path = tmp_path / lock_filename("0.0.1")
    write_lock(path, lock)
    assert read_lock(path) == lock


def test_write_lock_is_atomic_and_overwrites(tmp_path: Path) -> None:
    path = tmp_path / lock_filename("0.1.0")
    write_lock(path, _lock())
    original = path.read_text(encoding="utf-8")
    files = {"ability.yaml": SHA_B}
    write_lock(path, _lock(files=files))
    assert path.read_text(encoding="utf-8") != original
    assert read_lock(path).files == {"ability.yaml": SHA_B}


def test_read_accepts_document_without_header(tmp_path: Path) -> None:
    """读取校验只认字段；首行注释是写入口径（不拿来拒绝合法内容）。"""
    document = _document()
    body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    path = _write_raw(tmp_path / lock_filename("0.1.0"), body)
    assert read_lock(path).package_digest == document["package_digest"]


def test_render_rejects_inconsistent_in_memory_lock() -> None:
    lock = _lock()
    broken = InstallLock(
        package_digest="sha256:" + "0" * 64,
        digest_algorithm=lock.digest_algorithm,
        installed_at=lock.installed_at,
        source=lock.source,
        files=lock.files,
    )
    with pytest.raises(LockError) as excinfo:
        render_lock(broken)
    assert excinfo.value.code == LOCK_CODE_DIGEST_MISMATCH


# ------------------------------------------------------------- read validation


def _mutate(mutator: Any) -> str:
    document = _document()
    mutator(document)
    return _dump(document)


@pytest.mark.parametrize(
    ("case_id", "text", "code"),
    [
        pytest.param(
            "schema-missing",
            _mutate(lambda d: d.pop("schema")),
            LOCK_CODE_SCHEMA_UNSUPPORTED,
            id="schema-missing",
        ),
        pytest.param(
            "schema-2",
            _mutate(lambda d: d.__setitem__("schema", 2)),
            LOCK_CODE_SCHEMA_UNSUPPORTED,
            id="schema-2",
        ),
        pytest.param(
            "schema-string",
            _mutate(lambda d: d.__setitem__("schema", "1")),
            LOCK_CODE_SCHEMA_UNSUPPORTED,
            id="schema-string",
        ),
        pytest.param(
            "schema-bool",
            _mutate(lambda d: d.__setitem__("schema", True)),
            LOCK_CODE_SCHEMA_UNSUPPORTED,
            id="schema-bool",
        ),
        pytest.param(
            "unknown-top-key",
            _mutate(lambda d: d.__setitem__("extra", 1)),
            LOCK_CODE_INVALID,
            id="unknown-top-key",
        ),
        pytest.param(
            "package-digest-bad",
            _mutate(lambda d: d.__setitem__("package_digest", "sha256:ABC")),
            LOCK_CODE_INVALID,
            id="package-digest-bad",
        ),
        pytest.param(
            "algorithm-bad",
            _mutate(lambda d: d.__setitem__("digest_algorithm", "sha256-snapshot-v1")),
            LOCK_CODE_INVALID,
            id="algorithm-bad",
        ),
        pytest.param(
            "installed-at-naive",
            _mutate(lambda d: d.__setitem__("installed_at", "2026-11-03T12:34:56")),
            LOCK_CODE_INVALID,
            id="installed-at-naive",
        ),
        pytest.param(
            "installed-at-date",
            _mutate(lambda d: d.__setitem__("installed_at", "2026-11-03")),
            LOCK_CODE_INVALID,
            id="installed-at-date",
        ),
        pytest.param(
            "installed-at-offset",
            _mutate(lambda d: d.__setitem__("installed_at", "2026-11-03T20:34:56+08:00")),
            LOCK_CODE_INVALID,
            id="installed-at-offset",
        ),
        pytest.param(
            "source-missing",
            _mutate(lambda d: d.pop("source")),
            LOCK_CODE_INVALID,
            id="source-missing",
        ),
        pytest.param(
            "source-kind-unknown",
            _mutate(lambda d: d["source"].__setitem__("kind", "git")),
            LOCK_CODE_INVALID,
            id="source-kind-unknown",
        ),
        pytest.param(
            "source-ref-missing",
            _mutate(lambda d: d["source"].pop("ref")),
            LOCK_CODE_INVALID,
            id="source-ref-missing",
        ),
        pytest.param(
            "source-ref-empty",
            _mutate(lambda d: d["source"].__setitem__("ref", "")),
            LOCK_CODE_INVALID,
            id="source-ref-empty",
        ),
        pytest.param(
            "source-extra-key",
            _mutate(lambda d: d["source"].__setitem__("url", "https://example.com")),
            LOCK_CODE_INVALID,
            id="source-extra-key",
        ),
        pytest.param(
            "files-missing",
            _mutate(lambda d: d.pop("files")),
            LOCK_CODE_INVALID,
            id="files-missing",
        ),
        pytest.param(
            "files-empty",
            _mutate(lambda d: d.__setitem__("files", {})),
            LOCK_CODE_INVALID,
            id="files-empty",
        ),
        pytest.param(
            "files-without-ability-yaml",
            _mutate(lambda d: d.__setitem__("files", {"README.md": SHA})),
            LOCK_CODE_INVALID,
            id="files-without-ability-yaml",
        ),
        pytest.param(
            "files-value-bad",
            _mutate(lambda d: d["files"].__setitem__("ability.yaml", "deadbeef")),
            LOCK_CODE_INVALID,
            id="files-value-bad",
        ),
        pytest.param(
            "files-path-absolute",
            _mutate(lambda d: d["files"].__setitem__("/abs.py", SHA)),
            LOCK_CODE_INVALID,
            id="files-path-absolute",
        ),
        pytest.param(
            "files-path-backslash",
            _mutate(lambda d: d["files"].__setitem__("operations\\x.py", SHA)),
            LOCK_CODE_INVALID,
            id="files-path-backslash",
        ),
        pytest.param(
            "files-path-dotdot",
            _mutate(lambda d: d["files"].__setitem__("../x.py", SHA)),
            LOCK_CODE_INVALID,
            id="files-path-dotdot",
        ),
        pytest.param(
            "files-path-nfd",
            _mutate(lambda d: d["files"].__setitem__(unicodedata.normalize("NFD", "café.md"), SHA)),
            LOCK_CODE_INVALID,
            id="files-path-nfd",
        ),
        pytest.param(
            "files-lock-inside",
            _mutate(lambda d: d["files"].__setitem__("0.1.0.lock.yaml", SHA)),
            LOCK_CODE_INVALID,
            id="files-lock-inside",
        ),
        pytest.param(
            "files-cache-dir-inside",
            _mutate(lambda d: d["files"].__setitem__("__pycache__/x.pyc", SHA)),
            LOCK_CODE_INVALID,
            id="files-cache-dir-inside",
        ),
        pytest.param(
            "files-junk-inside",
            _mutate(lambda d: d["files"].__setitem__(".DS_Store", SHA)),
            LOCK_CODE_INVALID,
            id="files-junk-inside",
        ),
        pytest.param(
            "files-case-collision",
            _mutate(lambda d: d["files"].__setitem__("Ability.yaml", SHA)),
            LOCK_CODE_INVALID,
            id="files-case-collision",
        ),
        pytest.param(
            "digest-mismatch",
            _mutate(lambda d: d.__setitem__("package_digest", "sha256:" + "0" * 64)),
            LOCK_CODE_DIGEST_MISMATCH,
            id="digest-mismatch",
        ),
        pytest.param(
            "mcp-on-source",
            _mutate(
                lambda d: d.__setitem__(
                    "mcp",
                    {"fetched_at": "2026-11-03T12:34:56Z", "stale": False, "last_error": None},
                )
            ),
            LOCK_CODE_INVALID,
            id="mcp-on-source",
        ),
    ],
)
def test_read_rejects_invalid_document(tmp_path: Path, case_id: str, text: str, code: str) -> None:
    path = _write_raw(tmp_path / lock_filename("0.1.0"), text)
    with pytest.raises(LockError) as excinfo:
        read_lock(path)
    assert excinfo.value.code == code, case_id


@pytest.mark.parametrize(
    ("case_id", "text", "code"),
    [
        pytest.param("bom", "\ufeff" + _valid_text(), LOCK_CODE_INVALID, id="bom"),
        pytest.param("broken-yaml", "schema: [", LOCK_CODE_INVALID, id="broken-yaml"),
        pytest.param("top-level-list", "- a\n- b\n", LOCK_CODE_INVALID, id="top-level-list"),
        pytest.param("empty", "", LOCK_CODE_INVALID, id="empty"),
        pytest.param(
            "duplicate-key",
            LOCK_HEADER + "schema: 1\nschema: 1\n",
            LOCK_CODE_INVALID,
            id="duplicate-key",
        ),
        pytest.param(
            "multi-document",
            _valid_text() + "---\nschema: 1\n",
            LOCK_CODE_INVALID,
            id="multi-document",
        ),
    ],
)
def test_read_rejects_malformed_text(tmp_path: Path, case_id: str, text: str, code: str) -> None:
    path = _write_raw(tmp_path / lock_filename("0.1.0"), text)
    with pytest.raises(LockError) as excinfo:
        read_lock(path)
    assert excinfo.value.code == code, case_id


def test_read_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(LockError) as excinfo:
        read_lock(tmp_path / "missing.lock.yaml")
    assert excinfo.value.code == LOCK_CODE_INVALID


def test_read_rejects_symlink_lock(tmp_path: Path) -> None:
    """read_lock 本身跟随路径读取；整树枚举层才拒绝链接（见 store 测试）。"""
    target = _write_raw(tmp_path / "target.yaml", _valid_text())
    link = tmp_path / lock_filename("0.1.0")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("当前平台无法创建符号链接")
    assert stat.S_ISLNK(link.lstat().st_mode)
    assert read_lock(link) == read_lock(target)


# ------------------------------------------------------------- mcp validation


@pytest.mark.parametrize(
    ("case_id", "mutator", "code"),
    [
        pytest.param(
            "mcp-missing-on-mcp-source",
            lambda d: d.pop("mcp"),
            LOCK_CODE_INVALID,
            id="mcp-missing-on-mcp-source",
        ),
        pytest.param(
            "mcp-unknown-key",
            lambda d: d["mcp"].__setitem__("server", "github"),
            LOCK_CODE_INVALID,
            id="mcp-unknown-key",
        ),
        pytest.param(
            "mcp-stale-not-bool",
            lambda d: d["mcp"].__setitem__("stale", "false"),
            LOCK_CODE_INVALID,
            id="mcp-stale-not-bool",
        ),
        pytest.param(
            "mcp-last-error-not-str",
            lambda d: d["mcp"].__setitem__("last_error", 1),
            LOCK_CODE_INVALID,
            id="mcp-last-error-not-str",
        ),
        pytest.param(
            "mcp-fetched-at-bad",
            lambda d: d["mcp"].__setitem__("fetched_at", "yesterday"),
            LOCK_CODE_INVALID,
            id="mcp-fetched-at-bad",
        ),
        pytest.param(
            "source-ref-on-mcp-ok-then-bad",
            lambda d: d["source"].__setitem__("ref", ""),
            LOCK_CODE_INVALID,
            id="source-ref-on-mcp-ok-then-bad",
        ),
    ],
)
def test_read_rejects_invalid_mcp_block(
    tmp_path: Path, case_id: str, mutator: Any, code: str
) -> None:
    document = _mcp_document()
    mutator(document)
    path = _write_raw(tmp_path / lock_filename("0.0.1"), _dump(document))
    with pytest.raises(LockError) as excinfo:
        read_lock(path)
    assert excinfo.value.code == code, case_id


def test_mcp_source_may_omit_ref(tmp_path: Path) -> None:
    files = _files()
    lock = InstallLock(
        package_digest=tree_digest(files),
        digest_algorithm=DIGEST_ALGORITHM,
        installed_at="2026-11-03T12:34:56Z",
        source=InstallSource(kind="mcp"),
        files=files,
        mcp=McpLock(fetched_at="2026-11-03T12:34:56Z", stale=False, last_error=None),
    )
    path = tmp_path / lock_filename("0.0.1")
    write_lock(path, lock)
    assert read_lock(path).source == InstallSource(kind="mcp")


def test_schema_version_constant() -> None:
    assert LOCK_SCHEMA_VERSION == 1
    assert _lock().schema == 1
