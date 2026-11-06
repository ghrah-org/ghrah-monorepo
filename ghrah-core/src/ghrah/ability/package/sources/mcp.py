# SPDX-License-Identifier: Apache-2.0
"""MCP 工具清单的安装快照；连接与凭据只从受信配置源取得。"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import re
import tempfile
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from ghrah.ability.loading.ability_schema import AbilityIdentity, load_yaml, parse_definition
from ghrah.ability.loading.manifest_fields import ManifestValidationError
from ghrah.ability.package.digest import file_digest, tree_digest
from ghrah.ability.package.installed_store import is_link
from ghrah.ability.package.installer import (
    AbilityInstaller,
    InstallerError,
    InstallResult,
    _check_path_budget,
)
from ghrah.ability.package.lock import McpLock, lock_filename, utc_now_rfc3339, write_lock
from ghrah.mcp.agentconf_source import AgentconfMcpSource
from ghrah.mcp.errors import McpConfigError, McpCredentialMissingError, McpUnsupportedTransportError

_RESERVED = {"search_tools", "describe_tool", "execute_tool", "run_script"}


def tool_keys(names: list[str]) -> dict[str, str]:
    """按原始名排序并规范化工具键；冲突以原始名摘要消解。"""
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(
        names
    ):
        raise InstallerError("invalid_tools", "MCP 工具名为空或重复")
    result: dict[str, str] = {}
    used: set[str] = set()
    for name in sorted(names):
        base = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", name.lower())).strip("_")
        base = base or "mcp_tool"
        if not base[0].isalpha() or base in _RESERVED:
            base = "mcp_" + base
        key = base
        if len(key) > 64 or key in used:
            key = base[:54] + "__" + hashlib.sha256(name.encode()).hexdigest()[:8]
        if key in used:
            raise InstallerError("tool_key_conflict", "MCP 工具键摘要冲突")
        used.add(key)
        result[name] = key
    return result


def _header(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("origin") != "mcp":
        raise InstallerError("invalid_definition", "需要 origin: mcp 声明")
    if set(data) - {
        "format",
        "schema_version",
        "origin",
        "identity",
        "title",
        "description",
        "mcp",
        "members",
    }:
        raise InstallerError("invalid_definition", "MCP 声明包含未知字段")
    server = data.get("mcp")
    if (
        not isinstance(server, dict)
        or set(server) != {"server"}
        or not isinstance(server["server"], str)
        or not server["server"].strip()
    ):
        raise InstallerError("invalid_definition", "MCP 包必须只声明 mcp.server 引用")
    try:
        identity = AbilityIdentity.model_validate(data.get("identity"))
    except ValueError:
        raise InstallerError("invalid_definition", "MCP 包身份非法") from None
    if identity.namespace != "mcp" or not identity.name:
        raise InstallerError("invalid_definition", "MCP 包必须使用 mcp 命名空间及显式名称")
    if data.get("format") != "ghrah.ability-definition" or data.get("schema_version") != "1":
        raise InstallerError("invalid_definition", "MCP 包只支持 v1 定义")
    if any(
        not isinstance(data.get(key), str) or not data[key].strip()
        for key in ("title", "description")
    ):
        raise InstallerError("invalid_definition", "MCP 包缺少标题或描述")
    return {key: value for key, value in data.items() if key != "members"}


@asynccontextmanager
async def _serialized(installer: AbilityInstaller, name: str):
    """用操作系统文件锁串行化同一安装根的 MCP 刷新；退出或崩溃自动释放。"""
    installer._check_root()
    directory = installer.root / ".mcp-locks"
    if is_link(directory):
        raise InstallerError("symlink_rejected", "MCP 锁目录不得是链接")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / hashlib.sha256(name.encode()).hexdigest()
    if is_link(path):
        raise InstallerError("symlink_rejected", "MCP 锁文件不得是链接")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    acquired = False
    try:
        async with asyncio.timeout(30):
            while not acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    await asyncio.sleep(0.05)
                except OSError as error:
                    if os.name != "nt" or error.errno not in {
                        errno.EACCES,
                        errno.EAGAIN,
                        errno.EDEADLK,
                    }:
                        raise
                    await asyncio.sleep(0.05)
        yield
    except TimeoutError:
        raise InstallerError("refresh_busy", "另一个进程正在刷新此 MCP 包") from None
    finally:
        os.close(fd)


async def _observe(header: dict[str, Any], source: Any, connector: Any) -> list[dict[str, Any]]:
    from ghrah.mcp.client import list_tools, open_client

    owned = source is None
    source = AgentconfMcpSource() if owned else source
    try:
        spec = source.connection_spec(
            header["mcp"]["server"], skill_full_name="package/explicit-refresh"
        )
        credential = source.credential_for_spec(spec)
        headers = source.headers_for_spec(spec)
        if headers is not None:
            spec = replace(spec, headers=headers)
        async with asyncio.timeout((spec.connect_timeout_ms + spec.execution_timeout_ms) / 1000):
            async with (connector or open_client)(spec, credential=credential) as client:
                return await list_tools(
                    client, max_pages=spec.max_pages, max_bytes=spec.max_result_bytes
                )
    except McpCredentialMissingError:
        raise InstallerError("credential_missing", "MCP 凭据缺失") from None
    except McpUnsupportedTransportError:
        raise InstallerError("unsupported_transport", "MCP 包只支持 streamable_http") from None
    except McpConfigError:
        raise InstallerError(
            "mcp_config_invalid", "MCP 服务配置不可用；仅支持 streamable_http"
        ) from None
    except InstallerError:
        raise
    except Exception:
        raise InstallerError(
            "mcp_observation_failed", "MCP 工具发现失败；请检查连接与认证"
        ) from None
    finally:
        if owned:
            source.close()


def _snapshot(header: dict[str, Any], tools: list[dict[str, Any]]) -> str:
    if not tools:
        raise InstallerError("no_tools", "MCP 服务器没有工具；保留原快照")
    keys = tool_keys([tool["name"] for tool in tools])
    operations = {}
    server = header["mcp"]["server"]
    for tool in tools:
        name = tool["name"]
        operations[keys[name]] = {
            "title": tool.get("title") or name,
            "description": tool.get("description") or tool.get("title") or name,
            "input_schema": tool.get("input_schema") or {},
            "output_schema": tool.get("output_schema") or {},
            "mcp": {"server": server, "tool": name},
            "tags": sorted({"mcp", server}),
            "effects": ["net:request"],
            "permissions": {"net_access": True},
            "resources": {},
            "execution": {
                "mode": "request",
                "timeout_ms": 300000,
                "cancellation": "unsupported",
                "on_revoke": "drain",
                "retry": "never",
            },
            "expose": True,
        }
    data = {**header, "members": {"operations": dict(sorted(operations.items())), "skills": {}}}
    raw = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=4096)
    try:
        parse_definition(raw, source="MCP snapshot")
    except ManifestValidationError:
        raise InstallerError("invalid_tools", "MCP 工具契约无效") from None
    return raw


async def install_mcp(
    installer: AbilityInstaller, declaration: Path, *, source: Any = None, connector: Any = None
) -> InstallResult:
    """显式发现并安装 MCP 声明；源目录只含声明文件，不复制其它文件。"""
    try:
        header = _header(load_yaml(installer._read_definition_text(declaration)))
    except yaml.YAMLError, ManifestValidationError:
        raise InstallerError("invalid_definition", "MCP 声明 YAML 无效") from None
    entries = installer._collect_source(declaration)
    if {relative for relative, _ in entries} != {"ability.yaml"}:
        raise InstallerError("invalid_content", "MCP 声明目录只能包含 ability.yaml")
    async with _serialized(installer, header["mcp"]["server"]):
        raw = _snapshot(header, await _observe(header, source, connector))
        return _publish(installer, raw)


def _publish(installer: AbilityInstaller, raw: str) -> InstallResult:
    definition = parse_definition(raw, source="MCP snapshot")
    slot = installer.root / "mcp" / definition.identity.name
    for path in (slot.parent, slot):
        if is_link(path):
            raise InstallerError("symlink_rejected", "MCP 安装槽不得是链接")
    version_root = slot / definition.identity.version
    lock_path = slot / lock_filename(definition.identity.version)
    health = McpLock(utc_now_rfc3339(), False, None)
    with tempfile.TemporaryDirectory(prefix="ghrah-mcp-") as temporary:
        root = Path(temporary)
        path = root / "ability.yaml"
        path.write_text(raw, encoding="utf-8", newline="\n")
        entries = [("ability.yaml", path)]
        files = {"ability.yaml": file_digest(path)}
        digest = tree_digest(files)
        _check_path_budget(version_root, lock_path, entries, source=root)
        existing = installer._existing_install(definition, version_root, lock_path, digest)
        if existing is not None:
            item = installer.store.resolve(definition.full_name, definition.identity.version)
            if (
                item is None
                or item.lock is None
                or item.source.kind != "mcp"
                or not installer.verify(definition.full_name, definition.identity.version)[0].ok
            ):
                raise InstallerError("corrupt_install", "MCP 既有快照校验失败")
            write_lock(lock_path, replace(item.lock, mcp=health))
            return existing
        return installer._install_copy(
            definition=definition,
            source=root,
            entries=entries,
            source_files=files,
            package_digest=digest,
            slot=slot,
            version_root=version_root,
            lock_path=lock_path,
            mcp=health,
        )


async def refresh_mcp(
    installer: AbilityInstaller, name: str, *, source: Any = None, connector: Any = None
) -> InstallResult:
    """刷新最新候选；不变只写健康块，变化递增 patch；失败保留原索引。"""
    candidates = installer.store.versions_of(name)
    if not candidates:
        raise InstallerError("not_installed", "MCP 包尚未安装")
    latest = max(candidates, key=lambda v: tuple(map(int, v.version.split("."))))
    if (
        latest.lock is None
        or latest.mcp is None
        or not installer.verify(name, latest.version)[0].ok
    ):
        raise InstallerError("corrupt_install", "MCP 既有快照校验失败")
    header = _header(load_yaml(installer._read_definition_text(latest.root)))
    async with _serialized(installer, header["mcp"]["server"]):
        versions = installer.store.versions_of(name)
        if not versions:
            raise InstallerError("not_installed", "MCP 包尚未安装")
        item = max(versions, key=lambda v: tuple(map(int, v.version.split("."))))
        if item.lock is None or item.mcp is None or not installer.verify(name, item.version)[0].ok:
            raise InstallerError("corrupt_install", "MCP 既有快照校验失败")
        old = load_yaml(installer._read_definition_text(item.root))
        header = _header(old)
        try:
            tools = await _observe(header, source, connector)
            raw = _snapshot(header, tools)
        except InstallerError as error:
            write_lock(
                item.lock_path,
                replace(item.lock, mcp=replace(item.mcp, stale=True, last_error=error.code)),
            )
            raise
        new = load_yaml(raw)
        if json.dumps(new["members"], sort_keys=True) != json.dumps(old["members"], sort_keys=True):
            parts = list(map(int, item.version.split(".")))
            parts[2] += 1
            if parts[2] > 999999999:
                raise InstallerError("version_exhausted", "MCP patch 版本已耗尽")
            header["identity"] = {**header["identity"], "version": ".".join(map(str, parts))}
            raw = _snapshot(header, tools)
        return _publish(installer, raw)
