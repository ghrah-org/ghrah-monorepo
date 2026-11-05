# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``python -m ghrah.ability.tools.cli`` 的契约级测试。

全部用例都通过子进程调用模块入口，验证的是工具链（``ghrah ability``）实际拿到的
行为：argv 形状、``--json`` 单对象、退出码 0/1/2、``--root`` 语义与安装域错误码。

测试不依赖解释器解析与工具链仓：直接以当前 ``sys.executable`` 子进程运行，并在
``PYTHONPATH`` 里显式加入本 worktree 的 ``ghrah-core/src``，避免误用主 checkout
的可编辑安装。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_GIT_ROOT = Path(__file__).resolve().parents[3]
_CORE_SRC = _GIT_ROOT / "ghrah-core" / "src"
_FIXTURES = Path(__file__).resolve().parent / "package" / "fixtures"

_FULL_NAME = "fixtures.reporting"
_VERSION = "0.1.0"


def _env() -> dict[str, str]:
    """子进程环境：把本 worktree 的 ``ghrah-core/src`` 放到 PYTHONPATH 最前面。"""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    source = str(_CORE_SRC)
    env["PYTHONPATH"] = f"{source}{os.pathsep}{existing}" if existing else source
    return env


def _run(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """以子进程运行 ``python -m ghrah.ability.tools.cli <argv...>``。"""
    return subprocess.run(
        [sys.executable, "-m", "ghrah.ability.tools.cli", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=_env(),
        cwd=str(cwd) if cwd is not None else None,
    )


def _json_only(result: subprocess.CompletedProcess[str]) -> dict:
    """断言 stdout **恰好一个** JSON 对象并返回解析结果（模板字面量混入即失败）。"""
    stdout = result.stdout
    assert stdout.strip(), f"stdout 为空；stderr={result.stderr}"
    payload = json.loads(stdout)
    assert isinstance(payload, dict), f"stdout 不是 JSON 对象：{stdout!r}"
    stripped = stdout.strip()
    assert stripped.startswith("{") and stripped.endswith("}"), (
        f"stdout 混入非 JSON 文本：{stdout!r}"
    )
    return payload


def _copy_package(tmp_path: Path, *, fixture: str = "reporting", name: str | None = None) -> Path:
    """把 fixture 包复制到临时目录（复制件可自由改动，不动仓库原件）。"""
    root = tmp_path / "packages" / (name or fixture)
    shutil.copytree(_FIXTURES / fixture, root)
    return root


def _built_package(tmp_path: Path, *, fixture: str = "reporting") -> Path:
    """复制 fixture 并跑一次 ``build``，得到索引已生成的源包。"""
    root = _copy_package(tmp_path, fixture=fixture)
    built = _run("build", str(root))
    assert built.returncode == 0, built.stderr
    return root


def _install(
    package: Path,
    root: Path,
    *,
    json_mode: bool = True,
) -> subprocess.CompletedProcess[str]:
    """以工具链转调的形状调用 install（``install <dir> --json --root R``）。"""
    argv = ["install", str(package)]
    if json_mode:
        argv.append("--json")
    argv.extend(["--root", str(root)])
    return _run(*argv)


def _version_root(ability_root: Path, version: str = _VERSION) -> Path:
    return ability_root / "fixtures" / "reporting" / version


def _lock_path(ability_root: Path, version: str = _VERSION) -> Path:
    return _version_root(ability_root, version).parent / f"{version}.lock.yaml"


@pytest.fixture
def ability_root(tmp_path: Path) -> Path:
    """发行版级安装存储根（指向临时目录，绝不碰真实用户目录或默认根）。"""
    return tmp_path / "distro" / "ability"


@pytest.fixture
def package_root(tmp_path: Path) -> Path:
    return _built_package(tmp_path)


# ── build：委托 P0 入口 ──────────────────────────────────────


class TestBuild:
    def test_build_generates_index(self, tmp_path: Path) -> None:
        """``build <dir>`` 成功且输出索引摘要（与 tools.build 同形）。"""
        root = _copy_package(tmp_path)
        result = _run("build", str(root))
        assert result.returncode == 0, result.stderr
        assert "index_digest=sha256:" in result.stdout

    def test_build_check_clean_exit_0(self, package_root: Path) -> None:
        """索引与声明一致时 ``--check`` 退出 0。"""
        result = _run("build", str(package_root), "--check")
        assert result.returncode == 0, result.stderr
        assert "ok (unchanged)" in result.stdout

    def test_build_check_mismatch_exit_1(self, package_root: Path) -> None:
        """声明改了但未重建索引：``--check`` 退出 1 并把 diff 写 stderr。"""
        body = package_root / "skills" / "reporting-guide" / "SKILL.md"
        body.write_text(
            body.read_text(encoding="utf-8").replace(
                "description: 写报告的步骤与模板说明。",
                "description: 已过期的说明。",
            ),
            encoding="utf-8",
            newline="\n",
        )
        result = _run("build", str(package_root), "--check")
        assert result.returncode == 1
        assert "mismatch:" in result.stderr

    def test_build_builtin_flag_accepted(self, tmp_path: Path) -> None:
        """``--builtin`` 被接受并转交生成器（内置包命名空间放行）。"""
        root = _copy_package(tmp_path, fixture="builder")
        result = _run("build", str(root), "--builtin")
        assert result.returncode == 0, result.stderr

    def test_build_missing_dir_exit_2(self, tmp_path: Path) -> None:
        """源包目录不存在：沿用生成器的参数错误退出码 2。"""
        result = _run("build", str(tmp_path / "not-a-package"))
        assert result.returncode == 2
        assert "不是目录" in result.stderr


# ── 参数错误与信封（不需要安装域） ──────────────────────────


class TestUsageAndEnvelope:
    def test_no_verb_exit_2(self) -> None:
        result = _run()
        assert result.returncode == 2
        assert result.stdout == ""

    def test_unknown_verb_exit_2(self) -> None:
        result = _run("frobnicate")
        assert result.returncode == 2
        assert "invalid choice" in result.stderr

    def test_missing_positional_exit_2(self, tmp_path: Path) -> None:
        result = _run("show", "--root", str(tmp_path))
        assert result.returncode == 2
        assert result.stdout == ""

    def test_usage_error_json_single_object(self, tmp_path: Path) -> None:
        """``--all`` 与 ``@version`` 互斥：与工具链一致按参数错误退出 2，JSON 也只有一个对象。"""
        result = _run(
            "uninstall",
            f"{_FULL_NAME}@{_VERSION}",
            "--all",
            "--yes",
            "--json",
            "--root",
            str(tmp_path / "ability"),
        )
        assert result.returncode == 2
        payload = _json_only(result)
        assert payload["schema"] == 1
        assert payload["verb"] == "uninstall"
        assert payload["ok"] is False
        assert payload["error"]["code"] == "usage_error"
        assert "--all" in payload["error"]["message"]

    def test_refresh_rejects_version_exit_2(self, tmp_path: Path) -> None:
        result = _run(
            "refresh", f"{_FULL_NAME}@{_VERSION}", "--json", "--root", str(tmp_path / "ability")
        )
        assert result.returncode == 2
        payload = _json_only(result)
        assert payload["error"]["code"] == "usage_error"

    def test_store_requires_explicit_root(self) -> None:
        """缺 ``--root`` 按领域错误退出 1，且绝不回退隐式默认根。"""
        result = _run("list", "--json")
        assert result.returncode == 1
        payload = _json_only(result)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "root_required"

    def test_root_must_be_absolute(self) -> None:
        result = _run("list", "--json", "--root", "relative/ability")
        assert result.returncode == 1
        payload = _json_only(result)
        assert payload["error"]["code"] == "root_not_absolute"

    def test_module_discoverable_by_find_spec(self) -> None:
        """工具链探测条件：``importlib.util.find_spec`` 能在子进程里找到入口。"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.util;"
                "assert importlib.util.find_spec('ghrah.ability.tools.cli') is not None",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=_env(),
            timeout=120,
        )
        assert result.returncode == 0, result.stderr


# ── install ─────────────────────────────────────────────────


class TestInstall:
    def test_install_json_single_object(self, package_root: Path, ability_root: Path) -> None:
        """安装成功：``--json`` 恰好一个成功信封，根目录不存在时按需创建。"""
        assert not ability_root.exists()
        result = _install(package_root, ability_root)
        assert result.returncode == 0, result.stderr
        payload = _json_only(result)
        assert payload["schema"] == 1
        assert payload["verb"] == "install"
        assert payload["ok"] is True
        data = payload["data"]
        assert data["full_name"] == _FULL_NAME
        assert data["version"] == _VERSION
        assert data["already_installed"] is False
        assert data["package_digest"].startswith("sha256:")
        # 安装结果的 root 是版本目录（不是发行版存储根）
        assert data["root"] == str(_version_root(ability_root))
        assert (_version_root(ability_root) / "ability.yaml").is_file()
        assert _lock_path(ability_root).is_file()

    def test_install_human_mode_creates_root(self, package_root: Path, ability_root: Path) -> None:
        """人读模式：stdout 是中文提示，root 与父目录按需创建。"""
        result = _install(package_root, ability_root, json_mode=False)
        assert result.returncode == 0, result.stderr
        assert "已安装" in result.stdout
        assert ability_root.is_dir()

    def test_install_idempotent(self, package_root: Path, ability_root: Path) -> None:
        """同一内容重复安装：幂等 no-op，摘要不变。"""
        first = _install(package_root, ability_root)
        assert first.returncode == 0, first.stderr
        second = _install(package_root, ability_root)
        assert second.returncode == 0, second.stderr
        data = _json_only(second)["data"]
        assert data["already_installed"] is True
        assert data["package_digest"] == _json_only(first)["data"]["package_digest"]

    def test_install_same_version_different_content_rejected(
        self, tmp_path: Path, package_root: Path, ability_root: Path
    ) -> None:
        """同版本不同内容：拒绝覆盖，退出 1 且错误码 version_conflict。"""
        assert _install(package_root, ability_root).returncode == 0
        other = _copy_package(tmp_path, name="reporting-changed")
        body = other / "skills" / "reporting-guide" / "SKILL.md"
        body.write_text(
            body.read_text(encoding="utf-8") + "\n改写后的正文。\n",
            encoding="utf-8",
            newline="\n",
        )
        rebuilt = _run("build", str(other))
        assert rebuilt.returncode == 0, rebuilt.stderr
        result = _install(other, ability_root)
        assert result.returncode == 1
        payload = _json_only(result)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "version_conflict"

    def test_install_archive_rejected(self, tmp_path: Path, ability_root: Path) -> None:
        """P2 安装器只支持目录；归档文件是领域失败（退出 1，不是 2）。"""
        archive = tmp_path / "reporting.tar.gz"
        archive.write_bytes(b"not-a-real-archive")
        result = _install(archive, ability_root)
        assert result.returncode == 1
        payload = _json_only(result)
        assert payload["error"]["code"] == "unsupported_source"


# ── list ────────────────────────────────────────────────────


class TestList:
    def test_list_missing_root_is_empty(self, ability_root: Path) -> None:
        """不存在的 root 返回空列表、退出 0，且不创建目录。"""
        result = _run("list", "--json", "--root", str(ability_root))
        assert result.returncode == 0, result.stderr
        payload = _json_only(result)
        assert payload["ok"] is True
        assert payload["data"]["root"] == str(ability_root)
        assert payload["data"]["versions"] == []
        assert not ability_root.exists()

    def test_list_human_empty(self, ability_root: Path) -> None:
        result = _run("list", "--root", str(ability_root))
        assert result.returncode == 0, result.stderr
        assert "没有已安装" in result.stdout

    def test_list_after_install(self, package_root: Path, ability_root: Path) -> None:
        assert _install(package_root, ability_root).returncode == 0
        result = _run("list", "--json", "--root", str(ability_root))
        assert result.returncode == 0, result.stderr
        data = _json_only(result)["data"]
        assert data["root"] == str(ability_root)
        assert len(data["versions"]) == 1
        entry = data["versions"][0]
        assert entry["full_name"] == _FULL_NAME
        assert entry["version"] == _VERSION
        assert entry["lock_state"] == "ok"
        assert entry["root"] == str(_version_root(ability_root))
        assert entry["installed_at"]
        assert entry["source"]["kind"] == "directory"


# ── show ────────────────────────────────────────────────────


class TestShow:
    def test_show_with_version(self, package_root: Path, ability_root: Path) -> None:
        assert _install(package_root, ability_root).returncode == 0
        result = _run("show", f"{_FULL_NAME}@{_VERSION}", "--json", "--root", str(ability_root))
        assert result.returncode == 0, result.stderr
        payload = _json_only(result)
        assert payload["verb"] == "show"
        assert payload["data"]["full_name"] == _FULL_NAME
        versions = payload["data"]["versions"]
        assert len(versions) == 1
        assert versions[0]["version"] == _VERSION
        assert versions[0]["root"] == str(_version_root(ability_root))
        assert versions[0]["lock_state"] == "ok"
        assert versions[0]["definition"]["identity"]["namespace"] == "fixtures"

    def test_show_without_version_lists_all(self, package_root: Path, ability_root: Path) -> None:
        """省略版本：列出该身份全部已安装版本（这里只有一个）。"""
        assert _install(package_root, ability_root).returncode == 0
        result = _run("show", _FULL_NAME, "--json", "--root", str(ability_root))
        assert result.returncode == 0, result.stderr
        versions = _json_only(result)["data"]["versions"]
        assert [entry["version"] for entry in versions] == [_VERSION]

    def test_show_missing_package_exit_1(self, ability_root: Path) -> None:
        """包不存在：业务错误（退出 1），JSON 仍只有一个对象。"""
        result = _run("show", "fixtures.nope", "--json", "--root", str(ability_root))
        assert result.returncode == 1
        payload = _json_only(result)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "not_installed"


# ── verify ──────────────────────────────────────────────────


class TestVerify:
    def test_verify_ok(self, package_root: Path, ability_root: Path) -> None:
        assert _install(package_root, ability_root).returncode == 0
        result = _run("verify", _FULL_NAME, "--json", "--root", str(ability_root))
        assert result.returncode == 0, result.stderr
        payload = _json_only(result)
        assert payload["ok"] is True
        results = payload["data"]["results"]
        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["version"] == _VERSION

    def test_verify_with_version(self, package_root: Path, ability_root: Path) -> None:
        assert _install(package_root, ability_root).returncode == 0
        result = _run("verify", f"{_FULL_NAME}@{_VERSION}", "--json", "--root", str(ability_root))
        assert result.returncode == 0, result.stderr
        assert _json_only(result)["data"]["results"][0]["ok"] is True

    def test_verify_tampered_exit_1(self, package_root: Path, ability_root: Path) -> None:
        """安装后改动包内文件：校验失败退出 1，失败细节在 error.details。"""
        assert _install(package_root, ability_root).returncode == 0
        body = _version_root(ability_root) / "skills" / "reporting-guide" / "SKILL.md"
        body.write_text(body.read_text(encoding="utf-8") + "\n被篡改。\n", encoding="utf-8")
        result = _run("verify", _FULL_NAME, "--json", "--root", str(ability_root))
        assert result.returncode == 1
        payload = _json_only(result)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "verify_failed"
        failed = payload["error"]["details"]["results"][0]
        assert failed["ok"] is False
        assert failed["changed"] or failed["added"] or failed["message"]

    def test_verify_lock_missing_exit_1(self, package_root: Path, ability_root: Path) -> None:
        """缺 lock 的版本目录：verify 拒绝（不假装成功）。"""
        assert _install(package_root, ability_root).returncode == 0
        _lock_path(ability_root).unlink()
        result = _run("verify", _FULL_NAME, "--json", "--root", str(ability_root))
        assert result.returncode == 1
        payload = _json_only(result)
        assert payload["error"]["details"]["results"][0]["ok"] is False


# ── uninstall ───────────────────────────────────────────────


class TestUninstall:
    def test_uninstall_without_version_rejected(
        self, package_root: Path, ability_root: Path
    ) -> None:
        """省略版本且未给 --all：不隐式全删，退出 1 且错误码 version_required。"""
        assert _install(package_root, ability_root).returncode == 0
        result = _run("uninstall", _FULL_NAME, "--json", "--yes", "--root", str(ability_root))
        assert result.returncode == 1
        payload = _json_only(result)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "version_required"
        assert _version_root(ability_root).is_dir()

    def test_uninstall_with_version(self, package_root: Path, ability_root: Path) -> None:
        """带 ``@version`` + ``--yes``：卸载该版本并删除旁 lock。"""
        assert _install(package_root, ability_root).returncode == 0
        result = _run(
            "uninstall",
            f"{_FULL_NAME}@{_VERSION}",
            "--yes",
            "--json",
            "--root",
            str(ability_root),
        )
        assert result.returncode == 0, result.stderr
        payload = _json_only(result)
        data = payload["data"]
        assert data["full_name"] == _FULL_NAME
        assert [entry["version"] for entry in data["removed"]] == [_VERSION]
        assert not _version_root(ability_root).exists()
        assert not _lock_path(ability_root).exists()

    def test_uninstall_all_removes_every_version(
        self, tmp_path: Path, package_root: Path, ability_root: Path
    ) -> None:
        """``--all --yes``：同一身份的多版本全部删除。"""
        assert _install(package_root, ability_root).returncode == 0
        newer = _copy_package(tmp_path, name="reporting-newer")
        definition = newer / "ability.yaml"
        definition.write_text(
            definition.read_text(encoding="utf-8").replace(
                f"version: {_VERSION}", "version: 0.1.1", 1
            ),
            encoding="utf-8",
            newline="\n",
        )
        rebuilt = _run("build", str(newer))
        assert rebuilt.returncode == 0, rebuilt.stderr
        assert _install(newer, ability_root).returncode == 0
        result = _run(
            "uninstall", _FULL_NAME, "--all", "--yes", "--json", "--root", str(ability_root)
        )
        assert result.returncode == 0, result.stderr
        removed = _json_only(result)["data"]["removed"]
        assert sorted(entry["version"] for entry in removed) == [_VERSION, "0.1.1"]
        after = _run("list", "--json", "--root", str(ability_root))
        assert _json_only(after)["data"]["versions"] == []

    def test_uninstall_human_mode(self, package_root: Path, ability_root: Path) -> None:
        assert _install(package_root, ability_root).returncode == 0
        result = _run("uninstall", f"{_FULL_NAME}@{_VERSION}", "--yes", "--root", str(ability_root))
        assert result.returncode == 0, result.stderr
        assert "已卸载" in result.stdout


# ── refresh ─────────────────────────────────────────────────


class TestRefresh:
    def test_refresh_source_package_unsupported(
        self, package_root: Path, ability_root: Path
    ) -> None:
        """P2 还没有 MCP：refresh 明确报 unsupported_refresh，不假装成功。"""
        assert _install(package_root, ability_root).returncode == 0
        result = _run("refresh", _FULL_NAME, "--json", "--root", str(ability_root))
        assert result.returncode == 1
        payload = _json_only(result)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "unsupported_refresh"

    def test_refresh_human_failure_writes_stderr(self, ability_root: Path) -> None:
        result = _run("refresh", _FULL_NAME, "--root", str(ability_root))
        assert result.returncode == 1
        assert result.stdout == ""
        assert "错误" in result.stderr
