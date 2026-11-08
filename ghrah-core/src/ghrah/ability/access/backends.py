# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""执行接纳的后端专属参数检查：与“成员关系校验”分开的一层。

关系库的 ``_check`` 回答“这个 Agent 此刻是否获授这个成员、引用是否仍然
有效”；本模块回答“这次调用的参数是否落在该后端允许的资源范围内”。两者
分开实现，新增后端只需在这里登记，不必改关系库算法，也不笼统放开任意成员。

每个条目按**提供者槽契约 + 成员键**登记：同名成员只有契约匹配才按该后端
接纳，未知后端一律 ``unavailable``（fail-closed），不按成员名猜测执行入口。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ghrah.ability.access.errors import AccessError
from ghrah.ability.provider_contracts import (
    CLUSTER_CONTRACT,
    CORE_CONTRACT,
    FS_CONTRACT,
    MCP_CONTRACT,
    PYTHON_CONTRACT,
    SHELL_CONTRACT,
    contract_for,
)

__all__ = [
    "BackendAdmission",
    "admission_for",
    "contract_of",
    "file_member_paths",
]

AdmissionCheck = Callable[[str, Mapping[str, Any], tuple[str, ...], str], None]
"""``(成员键, 参数, 授予根, 实例根) -> None``；不合法时抛 ``AccessError``。"""


@dataclass(frozen=True)
class BackendAdmission:
    """一个已登记后端的参数接纳函数。

    Attributes:
        contract: 提供者槽契约（如 ``ghrah.fs.v1``）。
        path_arguments: 该成员声明为路径资源的参数名（供装配期判断是否需要
            授予根及其范围）；shell 的 ``working_dir`` 属路径资源，
            集群/MCP 成员为空。
        check: 接纳函数；参数不合法时抛 :class:`AccessError`。
    """

    contract: str
    path_arguments: tuple[str, ...]
    check: AdmissionCheck


def _resolve_argument(
    value: Any, instance_root: str, roots: tuple[str, ...], *, argument: str
) -> None:
    """路径参数必须为字符串且落在实例根与授予根之内。"""
    if not isinstance(value, str) or not value.strip():
        raise AccessError("invalid_change")
    path = Path(value)
    resolved = (path if path.is_absolute() else Path(instance_root) / path).resolve()
    if not resolved.is_relative_to(Path(instance_root).resolve()):
        raise AccessError("access_denied")
    if not any(resolved.is_relative_to(Path(root).resolve()) for root in roots):
        raise AccessError("access_denied")


def _fs_check(
    member: str,
    arguments: Mapping[str, Any],
    roots: tuple[str, ...],
    instance_root: str,
) -> None:
    """文件成员的路径参数逐个核对；缺参数按参数错误拒绝。"""
    names = _FS_PATH_ARGUMENTS.get(member, ())
    if not names:
        raise AccessError("unavailable")
    for name in names:
        if name not in arguments:
            raise AccessError("invalid_change")
        _resolve_argument(arguments[name], instance_root, roots, argument=name)


def _shell_check(
    member: str,
    arguments: Mapping[str, Any],
    roots: tuple[str, ...],
    instance_root: str,
) -> None:
    """命令成员：命令必须为非空字符串；工作目录（可选）按路径资源核对。"""
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        raise AccessError("invalid_change")
    working_dir = arguments.get("working_dir")
    if working_dir is not None:
        _resolve_argument(working_dir, instance_root, roots, argument="working_dir")


def _cluster_check(
    member: str,
    arguments: Mapping[str, Any],
    roots: tuple[str, ...],
    instance_root: str,
) -> None:
    """集群成员：没有路径资源轴，只核对必填身份参数的类型与形状。"""
    for name in _CLUSTER_REQUIRED.get(member, ()):
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise AccessError("invalid_change")
    for name in _CLUSTER_OPTIONAL.get(member, ()):
        value = arguments.get(name)
        if value is not None and not isinstance(value, str):
            raise AccessError("invalid_change")


def _core_check(
    member: str,
    arguments: Mapping[str, Any],
    roots: tuple[str, ...],
    instance_root: str,
) -> None:
    """核心成员：只核对显式声明过的参数形状，不做资源轴判断。

    内建核心成员在当前进程内执行，没有外部资源根；内容读取
    （``load_skill``）的授权属于内容接线，不在这里冒充已授权。
    """
    checker = _CORE_ARGUMENTS.get(member)
    if checker is None:
        raise AccessError("unavailable")
    checker(arguments)


def _optional_str(arguments: Mapping[str, Any], name: str) -> None:
    value = arguments.get(name)
    if value is not None and not isinstance(value, str):
        raise AccessError("invalid_change")


def _required_str(arguments: Mapping[str, Any], name: str) -> None:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AccessError("invalid_change")


def _core_list_abilities(arguments: Mapping[str, Any]) -> None:
    _optional_str(arguments, "full_name")


def _core_load_skill(arguments: Mapping[str, Any]) -> None:
    _required_str(arguments, "full_name")
    _optional_str(arguments, "path")


def _core_recall_context(arguments: Mapping[str, Any]) -> None:
    mode = arguments.get("mode")
    if mode is not None and mode not in {"list", "messages"}:
        raise AccessError("invalid_change")
    for name in ("node_id", "keyword"):
        _optional_str(arguments, name)
    if arguments.get("node_id_range") is not None and not isinstance(
        arguments["node_id_range"], list
    ):
        raise AccessError("invalid_change")


def _core_no_arguments(arguments: Mapping[str, Any]) -> None:
    """无参数成员：接受空映射；多余参数由实现侧契约拒绝。"""


def _mcp_check(
    member: str,
    arguments: Mapping[str, Any],
    roots: tuple[str, ...],
    instance_root: str,
) -> None:
    """MCP 成员：server / tool 必须为非空字符串，arguments 必须是对象。"""
    for name in ("server", "tool"):
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise AccessError("invalid_change")
    payload = arguments.get("arguments")
    if payload is not None and not isinstance(payload, Mapping):
        raise AccessError("invalid_change")


_FS_PATH_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "read_file": ("file_path",),
    "write_file": ("file_path",),
    "edit_file": ("file_path",),
    "delete_file": ("file_path",),
    "list_directory": ("dir_path",),
    "search_files": ("path",),
    "move_file": ("source_path", "destination_path"),
}

_CLUSTER_REQUIRED: dict[str, tuple[str, ...]] = {
    "send_message": ("target", "content"),
    "broadcast_message": ("content",),
    "spawn_agent": ("manifest_ref",),
    "terminate_agent": ("agent_name",),
    "send": ("message",),
}

_CLUSTER_OPTIONAL: dict[str, tuple[str, ...]] = {
    "query_agents": ("filter",),
    "query_manifests": ("manifest_ref",),
}

_CORE_ARGUMENTS: dict[str, Any] = {
    "search_tools": lambda args: _required_str(args, "query"),
    "describe_tool": lambda args: _required_str(args, "name"),
    "execute_tool": lambda args: _required_str(args, "name"),
    "list_abilities": _core_list_abilities,
    "load_skill": _core_load_skill,
    "recall_context": _core_recall_context,
    "session_info": _core_no_arguments,
}

_REGISTRY: dict[tuple[str, str], BackendAdmission] = {}


def _register(contract: str, member: str, paths: tuple[str, ...], check: AdmissionCheck) -> None:
    _REGISTRY[(contract, member)] = BackendAdmission(contract, paths, check)


for _member, _args in _FS_PATH_ARGUMENTS.items():
    _register(FS_CONTRACT, _member, _args, _fs_check)
_register(SHELL_CONTRACT, "execute_command", ("working_dir",), _shell_check)
for _member in (*_CLUSTER_REQUIRED, *_CLUSTER_OPTIONAL):
    _register(CLUSTER_CONTRACT, _member, (), _cluster_check)
_register(MCP_CONTRACT, "mcp_call", (), _mcp_check)
for _member in _CORE_ARGUMENTS:
    _register(CORE_CONTRACT, _member, (), _core_check)


def contract_of(definition: Mapping[str, Any], member: str) -> str | None:
    """由已提交版本定义解析成员的提供者契约；未知来源返回 ``None``。

    v1 索引不写 ``provider_requirements``：契约由包来源与身份决定（内置包
    ``ghrah.<name>`` 映射到各自后端；MCP 快照走 MCP 契约；外部 Python 源包
    走已登记的函数入口契约，宿主未接线 Provider 时仍不可执行）。
    """
    operations = definition.get("operations", {})
    operation = operations.get(member) if isinstance(operations, Mapping) else None
    if not isinstance(operation, Mapping):
        return None
    if definition.get("origin") == "mcp":
        route = operation.get("mcp")
        if (
            definition.get("identity", {}).get("namespace") == "mcp"
            and isinstance(route, Mapping)
            and route.get("server") == definition.get("mcp", {}).get("server")
            and isinstance(route.get("tool"), str)
            and not operation.get("entry")
        ):
            return MCP_CONTRACT
        return None
    entry = operation.get("entry")
    if not isinstance(entry, Mapping) or entry.get("path") != f"operations/{member}.py":
        return None
    identity = definition.get("identity", {})
    if not isinstance(identity, Mapping):
        return None
    namespace = identity.get("namespace")
    name = identity.get("name")
    if not isinstance(namespace, str) or not (name is None or isinstance(name, str)):
        return None
    return contract_for(definition.get("origin") or "source", namespace, name)


def admission_for(definition: Mapping[str, Any], member: str) -> BackendAdmission | None:
    """按定义解析成员的后端接纳；未登记的后端返回 ``None``（fail-closed）。"""
    contract = contract_of(definition, member)
    if contract is None:
        return None
    if contract == MCP_CONTRACT and definition.get("origin") == "mcp":
        # 远端 payload 的语义由服务器校验；身份与显式后端可用性由关系接纳核对。
        return BackendAdmission(contract, (), lambda member, arguments, roots, root: None)
    if contract == PYTHON_CONTRACT:
        operation = definition["operations"][member]
        schema = operation["input_schema"]
        resources = operation.get("resources", {})
        if not isinstance(resources, Mapping):
            return None
        declared = resources.get("paths", [])
        if not isinstance(declared, list) or any(
            not isinstance(name, str) or name not in schema.get("properties", {})
            for name in declared
        ):
            return None
        paths = tuple(declared)

        def check(
            key: str, arguments: Mapping[str, Any], roots: tuple[str, ...], instance_root: str
        ) -> None:
            for name in paths:
                prop = schema.get("properties", {}).get(name, {})
                if name not in arguments and "default" not in prop:
                    if name in schema.get("required", ()):
                        raise AccessError("invalid_change")
                    continue
                value = arguments.get(name, prop.get("default"))
                if value is None and name not in schema.get("required", ()):
                    continue
                _resolve_argument(value, instance_root, roots, argument=name)

        return BackendAdmission(contract, paths, check)
    return _REGISTRY.get((contract, member))


def file_member_paths(definition: Mapping[str, Any], member: str) -> tuple[str, ...]:
    """成员是否声明了路径资源；是则返回参数名，否则空元组。

    文件成员（``file_path`` 等）与 shell 成员（``working_dir``）都算路径资源；
    装配期据此决定授予根：有路径资源的包必须按部署声明的文件根登记，不能
    退化成只取工作区根。
    """
    admission = admission_for(definition, member)
    if admission is None or admission.contract not in {
        FS_CONTRACT,
        PYTHON_CONTRACT,
        SHELL_CONTRACT,
    }:
        return ()
    return admission.path_arguments
