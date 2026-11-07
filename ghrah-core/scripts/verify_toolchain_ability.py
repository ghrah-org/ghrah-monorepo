# SPDX-License-Identifier: Apache-2.0
"""用已安装 wheel 的解释器验证工具链 Ability 命令；全部状态落在临时目录。

--python 指定同时安装了 ghrah-toolchain、ghrah-core[mcp]、protocol/plugin wheel 的
解释器。只为测试将 agentconf 的默认库位置指向临时文件；不替换连接、领域方法或 CLI。
原生解释器选择、子进程转调、安装存储、回环 HTTP 与 JSON 信封均走生产实现。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


async def verify(python: Path, output: Path) -> None:
    """执行隔离发行版的成功、拒绝、存储隔离与 MCP 刷新矩阵，保存无密钥报告。"""
    checks: list[dict[str, Any]] = []
    core = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="ghrah-cli-check-") as temporary:
        root = Path(temporary)
        install_home = root / "home"
        install_home.mkdir()
        bootstrap = root / "bootstrap"
        bootstrap.mkdir()
        database = root / "config.db"
        (bootstrap / "sitecustomize.py").write_text(
            "from agentconf import AgentsConfig\n"
            f"AgentsConfig.DEFAULT_DB_PATH = {str(database)!r}\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["GHRAH_HOME"] = str(install_home)
        env["PYTHONPATH"] = str(bootstrap)
        env.pop("VIRTUAL_ENV", None)
        for name in ("demo", "other"):
            target = install_home / name / "current" / "env"
            target.mkdir(parents=True)
            (target / ".venv").symlink_to(python.parent.parent, target_is_directory=True)
        (install_home / "boot.toml").write_text('default = "demo/check"\n', encoding="utf-8")

        async def command(args: list[str], *, input_data: bytes | None = None):
            child = await asyncio.create_subprocess_exec(
                str(python),
                *args,
                env=env,
                cwd=root,
                stdin=asyncio.subprocess.PIPE if input_data else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(child.communicate(input_data), 30)
                return child.returncode, stdout.decode(), stderr.decode()
            finally:
                if child.returncode is None:
                    child.kill()
                    await child.communicate()

        async def cli(label: str, *args: str, expected: int = 0):
            code, stdout, stderr = await command(["-m", "ghrah_toolchain", "ability", *args])
            assert code == expected, (label, code, stderr)
            record: dict[str, Any] = {"check": label, "exit_code": code}
            payload = None
            if "--json" in args:
                payload = json.loads(stdout)
                assert isinstance(payload, dict)
                if "ok" in payload:
                    assert payload["ok"] == (expected == 0)
                    record["ok"] = payload["ok"]
                    record["envelope"] = "core"
                else:
                    assert expected != 0 and payload["scope"] == "ability"
                    assert payload["data"] is None and payload["diagnostics"]
                    record["envelope"] = "toolchain"
                if "error" in payload:
                    record["error_code"] = payload["error"]["code"]
            checks.append(record)
            return payload

        code, stdout, _ = await command(
            ["-c", "import ghrah.ability.package.installer as m;print(m.__file__)"]
        )
        assert code == 0 and str(python.parent.parent) in stdout, stdout
        installed_module = stdout.strip()
        source = root / "reporting"
        shutil.copytree(core / "tests/ability/package/fixtures/reporting", source)
        await cli("build", "build", str(source), "--python", str(python))
        await cli("build-check", "build", str(source), "--check", "--python", str(python))
        installed = await cli("install-default", "install", str(source), "--json")
        data = installed["data"]
        name, version = data["full_name"], data["version"]
        target = f"{name}@{version}"
        assert Path(data["root"]).is_relative_to(install_home / "demo" / "ability")
        repeated = await cli("install-idempotent", "install", str(source), "--json")
        assert repeated["data"]["already_installed"]
        await cli("list-default", "list", "--json")
        await cli("show", "show", target, "--json")
        await cli("verify", "verify", target, "--json")
        other = await cli("distro-isolation", "list", "--distro", "other", "--json")
        assert not other["data"]["versions"]
        other = await cli(
            "explicit-python",
            "install",
            str(source),
            "--distro",
            "other",
            "--python",
            str(python),
            "--json",
        )
        assert Path(other["data"]["root"]).is_relative_to(install_home / "other" / "ability")
        await cli("unsupported-source-refresh", "refresh", name, "--json", expected=1)
        await cli("refresh-version-rejected", "refresh", target, "--json", expected=2)
        await cli("uninstall-version-required", "uninstall", name, "--json", expected=1)
        await cli(
            "uninstall-conflicting-options", "uninstall", target, "--all", "--json", expected=2
        )
        await cli(
            "invalid-python-no-fallback",
            "list",
            "--python",
            str(root / "absent"),
            "--json",
            expected=1,
        )
        package_root = Path(data["root"])
        member = package_root / "operations/render.py"
        original = member.read_bytes()
        member.write_bytes(original + b"\n# modified\n")
        await cli("tamper-detected", "verify", target, "--json", expected=1)
        member.write_bytes(original)
        lock = package_root.parent / f"{version}.lock.yaml"
        original_lock = lock.read_bytes()
        lock.unlink()
        await cli("missing-lock-rejected", "verify", target, "--json", expected=1)
        lock.write_bytes(original_lock)
        await cli("restored-verify", "verify", target, "--json")
        await cli("uninstall-version", "uninstall", target, "--json")
        await cli(
            "uninstall-all", "uninstall", name, "--distro", "other", "--all", "--yes", "--json"
        )
        empty = await cli("empty-after-uninstall", "list", "--json")
        assert not empty["data"]["versions"]

        token = secrets.token_hex(16)
        with (root / "mcp.log").open("wb") as log:
            server = await asyncio.create_subprocess_exec(
                str(python),
                str(core / "scripts/mock_mcp_server.py"),
                "--port",
                "0",
                "--token",
                token,
                "--state-dir",
                str(root / "mcp"),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
            )
        try:
            line = await asyncio.wait_for(server.stdout.readline(), 15)
            address = json.loads(line)["listening"]
            configure = (
                "import json,sys\nfrom agentconf import AgentsConfig\n"
                "data=json.load(sys.stdin)\n"
                "with AgentsConfig() as config:\n"
                " config.create_mcp_server('local-test','streamable_http',"
                "{'url':data['url'],'headers':{'Authorization':'Bearer '+data['token']}},"
                "auth={'mode':'inline'})\n"
            )
            code, _, stderr = await command(
                ["-c", configure], input_data=json.dumps({"url": address, "token": token}).encode()
            )
            assert code == 0, stderr
            declaration = root / "mcp-declaration"
            declaration.mkdir()
            (declaration / "ability.yaml").write_text(
                "format: ghrah.ability-definition\nschema_version: '1'\norigin: mcp\n"
                "identity: {namespace: mcp, name: local, version: '0.0.1'}\n"
                "title: Local\ndescription: Local snapshot\nmcp: {server: local-test}\n",
                encoding="utf-8",
            )
            snapshot = await cli("mcp-install", "install", str(declaration), "--json")
            refreshed = await cli("mcp-refresh", "refresh", "mcp.local", "--json")
            assert refreshed["data"]["package_digest"] == snapshot["data"]["package_digest"]
            server.terminate()
            await asyncio.wait_for(server.wait(), 5)
            await cli("mcp-unreachable", "refresh", "mcp.local", "--json", expected=1)
            stale = await cli("mcp-stale-preserved", "show", "mcp.local", "--json")
            assert stale["data"]["versions"][0]["mcp"]["stale"]
            await cli("mcp-stale-verify", "verify", "mcp.local", "--json")
        finally:
            if server.returncode is None:
                server.kill()
            await server.wait()
        report = {
            "checks": checks,
            "count": len(checks),
            "runtime_module": installed_module,
            "config_isolation": "temporary sitecustomize changes only agentconf default DB path",
            "python": str(python),
            "platform": sys.platform,
        }
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        assert token not in encoded
        output.write_text(encoded + "\n", encoding="utf-8")
        print(f"{len(checks)} checks passed; report: {output}")


def main() -> None:
    """运行隔离发行版验收，不安装软件或触碰现有发行版。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(verify(args.python.absolute(), args.output.absolute()))


if __name__ == "__main__":
    main()
