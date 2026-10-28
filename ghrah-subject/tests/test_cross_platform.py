# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""跨平台（Windows 兼容）修复的回归测试。

覆盖：locator round-trip（Path.as_uri 规范形态 + 旧式盘符兼容）、
原子写（含 Windows 目标被占用的重试）、可写性探针、
blocked 命令归一化（.exe 后缀 / 大小写）、resolve_relative_path 的
isabs 判定。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from ghrah.scheme.paths import is_subpath, resolve_relative_path

from ghrah.subject._fs import atomic_write_text, is_writable
from ghrah.subject.sandbox.executor import SandboxExecutor, SandboxExecutorConfig
from ghrah.subject.workspace.locator import (
    locator_to_path,
    path_to_locator,
)

requires_posix = pytest.mark.skipif(os.name != "posix", reason="POSIX only")
requires_windows = pytest.mark.skipif(os.name != "nt", reason="Windows only")


# ─── locator round-trip ───


class TestLocatorRoundTrip:
    def test_roundtrip_real_path(self, tmp_path: Path) -> None:
        p = str(tmp_path / "ws" / "agent-x")
        loc = path_to_locator(p)
        assert loc.startswith("file://")
        assert locator_to_path(loc) == str(Path(p))

    def test_locator_to_path_non_file_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="file://"):
            locator_to_path("nfs://host/share")

    @requires_posix
    def test_posix_uri_form(self) -> None:
        assert path_to_locator("/tmp/x") == "file:///tmp/x"
        assert locator_to_path("file:///tmp/x") == "/tmp/x"

    @requires_posix
    def test_posix_rejects_remote_host(self) -> None:
        with pytest.raises(ValueError, match="remote host"):
            locator_to_path("file://server/share/x")

    @requires_posix
    def test_posix_localhost_is_local(self) -> None:
        assert locator_to_path("file://localhost/tmp/x") == "/tmp/x"

    @requires_windows
    def test_windows_uri_form(self) -> None:
        assert path_to_locator("C:\\ws\\a") == "file:///C:/ws/a"
        assert locator_to_path("file:///C:/ws/a") == "C:\\ws\\a"

    @requires_windows
    def test_windows_unc_roundtrip(self) -> None:
        loc = path_to_locator("\\\\server\\share\\ws")
        assert loc == "file://server/share/ws"
        assert locator_to_path(loc) == "\\\\server\\share\\ws"

    @requires_windows
    def test_windows_legacy_drive_netloc(self) -> None:
        # 旧式裸拼接形态：盘符落在 netloc
        assert locator_to_path("file://C:/ws/a") == "C:\\ws\\a"

    @requires_windows
    def test_windows_backslash_leniency(self) -> None:
        assert locator_to_path("file://C:\\ws\\a") == "C:\\ws\\a"

    @requires_posix
    def test_legacy_drive_netloc_posix_fallback(self) -> None:
        # POSIX 上盘符 netloc 走宽容路径（/C:/... 原样保留），不抛异常
        assert locator_to_path("file://C:/ws/a") == str(Path("/C:/ws/a"))


# ─── atomic_write_text ───


class TestAtomicWriteText:
    def test_write_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "f.json"
        atomic_write_text(target, '{"a": 1}')
        assert target.read_text(encoding="utf-8") == '{"a": 1}'

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("old", encoding="utf-8")
        atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"

    def test_no_tmp_residue(self, tmp_path: Path) -> None:
        target = tmp_path / "marker.json"
        atomic_write_text(target, "{}")
        assert [p.name for p in tmp_path.iterdir()] == ["marker.json"]

    def test_replace_retries_on_locked_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "f.txt"
        target.write_text("old", encoding="utf-8")
        calls = {"n": 0}
        real_replace = os.replace

        def flaky_replace(src, dst):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(f"locked ({calls['n']})")
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", flaky_replace)
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        atomic_write_text(target, "new")
        assert calls["n"] == 3
        assert target.read_text(encoding="utf-8") == "new"

    def test_failure_cleans_tmp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def broken_replace(_src, _dst):  # noqa: ANN001
            raise PermissionError("permanently locked")

        monkeypatch.setattr(os, "replace", broken_replace)
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        with pytest.raises(PermissionError):
            atomic_write_text(tmp_path / "f.txt", "new")
        assert list(tmp_path.iterdir()) == []


# ─── is_writable ───


class TestIsWritable:
    def test_writable_dir(self, tmp_path: Path) -> None:
        assert is_writable(tmp_path) is True

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert is_writable(tmp_path / "nope") is False

    @requires_posix
    def test_readonly_dir_denied(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:
            pytest.skip("root bypasses permission checks")
        d = tmp_path / "ro"
        d.mkdir()
        os.chmod(d, 0o555)
        try:
            assert is_writable(d) is False
        finally:
            os.chmod(d, 0o755)


# ─── blocked command normalization ───


class TestBlockedCommandNormalization:
    def test_windows_exec_suffix_blocked(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(workspace_root=str(tmp_path))
        allowed, reason = executor.check_command(["shutdown.exe", "-h"])
        assert allowed is False
        assert "shutdown" in reason

    def test_case_insensitive_blocked(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(workspace_root=str(tmp_path))
        allowed, reason = executor.check_command(["TASKKILL", "/F", "/PID", "1"])
        assert allowed is False
        assert "taskkill" in reason

    def test_custom_blocked_case_folded(self, tmp_path: Path) -> None:
        config = SandboxExecutorConfig(blocked_commands={"Dangerous"})
        executor = SandboxExecutor(workspace_root=str(tmp_path), config=config)
        allowed, _ = executor.check_command(["DANGEROUS.EXE"])
        assert allowed is False

    def test_allowed_not_blocked(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(workspace_root=str(tmp_path))
        allowed, _ = executor.check_command(["git", "status"])
        assert allowed is True


# ─── resolve_relative_path（isabs 判定） ───


class TestResolveRelativePath:
    WS = "/ws" if os.name == "posix" else "C:\\ws"

    def test_absolute_passthrough(self) -> None:
        absolute = "/abs/path" if os.name == "posix" else "D:\\other"
        assert resolve_relative_path(self.WS, absolute) == absolute

    def test_relative_joins_workspace(self) -> None:
        joined = resolve_relative_path(self.WS, "rel.txt")
        assert joined == os.path.join(self.WS, "rel.txt")

    def test_dot_is_workspace(self) -> None:
        assert resolve_relative_path(self.WS, ".") == self.WS

    @requires_windows
    def test_windows_drive_and_unc_absolute(self) -> None:
        assert resolve_relative_path("C:\\ws", "D:\\data") == "D:\\data"
        assert resolve_relative_path("C:\\ws", "\\\\srv\\share") == "\\\\srv\\share"


# ─── is_subpath 平台归一 ───


class TestIsSubpathNormalization:
    @requires_windows
    def test_case_insensitive_volume(self) -> None:
        assert is_subpath("C:\\TMP\\data\\f.txt", "c:\\tmp\\data") is True

    @requires_posix
    def test_case_sensitive_on_posix(self) -> None:
        # POSIX 上 normcase 为恒等，保持大小写敏感语义
        assert is_subpath("/tmp/DATA/f.txt", "/tmp/data") is False
