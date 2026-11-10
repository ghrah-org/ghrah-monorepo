# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""只读代内 catalog，机械映射受信配置与监督策略，不扫描包或源码清单。"""

import hashlib
import json
import os
import re
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from ghrah_kernel.context import CallerBinding
from ghrah_kernel.integration.assembly import host_configuration
from ghrah_kernel.launch import LaunchRecord
from ghrah_kernel.supervisor import ServiceBinding, ServiceSpec
from ghrah_kernel.supervisor import Supervisor as KernelSupervisor


class CatalogError(ValueError):
    """代内目录损坏、声明非法或受信环境缺失。"""


def _validate(catalog: dict) -> None:
    expected = {
        "schema",
        "digest",
        "workspace",
        "distro",
        "host",
        "kernel",
        "services",
        "identity",
        "callers",
    }
    if not isinstance(catalog, dict) or set(catalog) != expected:
        raise CatalogError("catalog 顶层字段不符合版本 1")
    if type(catalog["schema"]) is not int or catalog["schema"] != 1:
        raise CatalogError("catalog schema 必须为 1")
    if not isinstance(catalog["callers"], dict) or set(catalog["callers"]) != {
        "control",
        "observer",
        "peer",
    }:
        raise CatalogError("catalog 必须声明 control/observer/peer 三个连接")
    if not isinstance(catalog["host"], dict) or not catalog["host"].get("command"):
        raise CatalogError("catalog 缺少宿主入口")
    if not isinstance(catalog["kernel"], dict) or (
        catalog["kernel"].get("module"),
        catalog["kernel"].get("entry"),
    ) != ("ghrah_kernel", "_kernel"):
        raise CatalogError("当前宿主只支持 ghrah_kernel 的 _kernel 入口")
    services = catalog["services"]
    if not isinstance(services, list) or not 1 <= len(services) <= 8:
        raise CatalogError("catalog 服务数必须在 1..8")
    try:
        ids = [service["id"] for service in services]
        if not all(isinstance(key, str) and key for key in ids) or len(set(ids)) != len(ids):
            raise CatalogError("catalog 服务 id 必须非空且唯一")
    except (KeyError, TypeError) as exc:
        raise CatalogError("catalog 服务形状非法") from exc


def load_catalog(generation_root: Path) -> dict:
    """校验 catalog 文件字节摘要，只读取 catalog.json 与 GENERATION.toml。"""
    try:
        raw = (generation_root / "catalog.json").read_bytes()
        generation = tomllib.loads((generation_root / "GENERATION.toml").read_text("utf-8"))
        expected = generation["generation"]["catalog_sha256"]
        if hashlib.sha256(raw).hexdigest() != expected:
            raise CatalogError("catalog_sha256 与 catalog 文件摘要不符")
        catalog = json.loads(raw)
        _validate(catalog)
        return catalog
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CatalogError(f"代内 catalog 装载失败：{exc}") from exc


def _expand(value: object, env: Mapping[str, str]) -> object:
    if isinstance(value, dict):
        return {key: _expand(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, env) for item in value]
    if not isinstance(value, str):
        return value

    def substitute(match: re.Match) -> str:
        name = match[1]
        if name not in {"DATA_ROOT", "DISTRO_ROOT", "GENERATION_ID"}:
            raise CatalogError(f"未识别的占位符：{match[0]}")
        resolved = env.get(f"GHRAH_{name}")
        if not resolved:
            raise CatalogError(f"缺少工具链环境变量 GHRAH_{name}")
        return resolved

    return re.sub(r"\$\{([^}]*)\}", substitute, value)


def _map(catalog: dict, env: Mapping[str, str]) -> tuple[list, dict, list]:
    _validate(catalog)
    specs, schemes, launches = [], {}, []
    for service in catalog["services"]:
        kind, key = service["kind"], service["id"]
        declared = [
            {"name": item["name"], "contract": item["contract"], "actions": list(item["actions"])}
            for item in service["schemes"]
        ]
        if kind in schemes and schemes[kind] != declared:
            raise CatalogError(f"同 kind 的 schemes 必须完全相同：{kind}")
        schemes[kind] = declared
        raw = service["configuration"]
        if not isinstance(raw, dict) or raw.keys() & {
            "provider",
            "identities",
            "administrator",
            "principal",
        }:
            raise CatalogError("configuration 必须为对象，且不得声明宿主保留字段")
        configuration = _expand(raw, env)
        configuration.update(
            provider=service["provider"],
            identities=catalog["identity"],
            administrator=catalog["callers"]["control"]["principal"],
            principal=service["binding"]["principal"],
        )
        encoded = json.dumps(
            configuration, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        launches.append(
            LaunchRecord(
                key=key,
                kind=kind,
                module=service["module"],
                binding=CallerBinding(**service["binding"]),
                executable=sys.executable,
                configuration=encoded,
            )
        )
        policy = {
            name: service[name]
            for name in (
                "restart",
                "max_restarts",
                "window_seconds",
                "drain_ms",
                "close_timeout_ms",
            )
            if name in service
        }
        specs.append(
            ServiceSpec(
                service_key=key,
                kind=kind,
                entry_key=key,
                bindings=tuple(ServiceBinding(**item) for item in service["bindings"]),
                close_action=tuple(service["close_action"]) if "close_action" in service else None,
                **policy,
            )
        )
    # 构造期校验不发 RPC；错误策略、接线与容量必须在任何 spawn 之前拒绝。
    KernelSupervisor(None, specs, schemes)
    return specs, schemes, launches


def map_supervisor(
    catalog: dict,
    env: Mapping[str, str] | None = None,
) -> tuple[list[ServiceSpec], dict[str, list[dict]], list[LaunchRecord]]:
    """映射监督策略与已注入配置的启动记录；同一 kind 只允许一份方案声明。"""
    try:
        return _map(catalog, os.environ if env is None else env)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CatalogError(f"catalog 映射失败：{exc}") from exc


def map_host_configuration(catalog: dict, env: Mapping[str, str]) -> dict:
    """派生唯一身份快照与三个固定调用者，并经内核既有装配校验预检。"""
    _, _, launches = map_supervisor(catalog, env)
    data = {
        "identities": catalog["identity"],
        "launches": [asdict(record) for record in launches],
        "callers": catalog["callers"],
    }
    try:
        host_configuration(data)
    except (ValueError, KeyError, TypeError) as exc:
        raise CatalogError(f"受信宿主配置非法：{exc}") from exc
    return data
