# SPDX-License-Identifier: Apache-2.0
"""后端专属接纳：按提供者槽契约登记，未登记的后端 fail-closed。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghrah.ability.access.backends import (
    admission_for,
    contract_of,
    file_member_paths,
)
from ghrah.ability.access.errors import AccessError
from ghrah.ability.loading.ability_catalog import load_ability_definitions


def _definition(container: str) -> dict:
    """取一个内置容器的已提交定义形状（JSON 可序列化）。"""
    return load_ability_definitions()[container].model_dump(mode="json")


def _admit(container: str, member: str, arguments: dict, roots: tuple[str, ...], root: str):
    admission = admission_for(_definition(container), member)
    assert admission is not None
    admission.check(member, arguments, roots, root)


def test_fs_member_paths_come_from_contract(tmp_path: Path) -> None:
    """文件成员的路径参数由契约登记，不由成员名猜测。"""
    definition = _definition("fs")
    assert file_member_paths(definition, "read_file") == ("file_path",)
    assert file_member_paths(definition, "move_file") == ("source_path", "destination_path")
    assert file_member_paths(definition, "execute_command") == ()


def test_unknown_contract_is_not_admitted(tmp_path: Path) -> None:
    """契约不匹配或未登记时返回 None，调用方按不可用拒绝。

    v1 不写 ``provider_requirements``：成员的提供者契约由包来源与身份决定。
    把内置包的身份改成另一个内置后端（契约不匹配），或改成未登记来源
    （契约解析失败），都必须 fail-closed。
    """
    definition = _definition("fs")
    definition["identity"]["name"] = "shell"
    assert contract_of(definition, "read_file") == "ghrah.shell.v1"
    assert admission_for(definition, "read_file") is None
    assert admission_for(definition, "unknown_member") is None

    unregistered = _definition("fs")
    unregistered["identity"]["name"] = "unregistered"
    assert contract_of(unregistered, "read_file") is None
    assert admission_for(unregistered, "read_file") is None


def test_fs_member_accepts_paths_inside_granted_roots(tmp_path: Path) -> None:
    """授予根内的相对与绝对路径都被接纳。"""
    root = str(tmp_path)
    _admit("fs", "read_file", {"file_path": "notes/a.txt"}, (root,), root)
    _admit("fs", "read_file", {"file_path": str(tmp_path / "b.txt")}, (root,), root)


def test_fs_member_rejects_escape_and_missing_argument(tmp_path: Path) -> None:
    """越界路径与缺参数按明确错误拒绝。"""
    root = str(tmp_path)
    with pytest.raises(AccessError) as escaped:
        _admit("fs", "write_file", {"file_path": "../outside.txt"}, (root,), root)
    assert escaped.value.code == "access_denied"
    with pytest.raises(AccessError) as missing:
        _admit("fs", "write_file", {}, (root,), root)
    assert missing.value.code == "invalid_change"


def test_fs_move_checks_both_endpoints(tmp_path: Path) -> None:
    """移动成员逐个核对源与目标路径。"""
    root = str(tmp_path)
    with pytest.raises(AccessError) as escaped:
        _admit(
            "fs",
            "move_file",
            {"source_path": "a.txt", "destination_path": "../b.txt"},
            (root,),
            root,
        )
    assert escaped.value.code == "access_denied"


def test_fs_narrow_grant_root_rejects_workspace_paths(tmp_path: Path) -> None:
    """授予根可以比实例根更窄：沙盒内但不在授予内仍拒绝。"""
    root = str(tmp_path)
    narrow = str(tmp_path / "src")
    with pytest.raises(AccessError) as denied:
        _admit("fs", "read_file", {"file_path": "docs/a.md"}, (narrow,), root)
    assert denied.value.code == "access_denied"


def test_shell_member_checks_command_and_working_dir(tmp_path: Path) -> None:
    """命令成员要求非空命令；工作目录按路径资源核对。"""
    root = str(tmp_path)
    _admit("shell", "execute_command", {"command": "git status"}, (root,), root)
    with pytest.raises(AccessError) as empty:
        _admit("shell", "execute_command", {"command": " "}, (root,), root)
    assert empty.value.code == "invalid_change"
    with pytest.raises(AccessError) as escaped:
        _admit(
            "shell",
            "execute_command",
            {"command": "ls", "working_dir": "../elsewhere"},
            (root,),
            root,
        )
    assert escaped.value.code == "access_denied"


def test_cluster_member_checks_identity_fields(tmp_path: Path) -> None:
    """集群成员没有路径资源，只核对必填身份参数。"""
    root = str(tmp_path)
    _admit("cluster", "send_message", {"target": "bob", "content": "hi"}, (root,), root)
    with pytest.raises(AccessError) as missing:
        _admit("cluster", "send_message", {"target": "bob"}, (root,), root)
    assert missing.value.code == "invalid_change"
    with pytest.raises(AccessError) as malformed:
        _admit("cluster", "terminate_agent", {"agent_name": 7}, (root,), root)
    assert malformed.value.code == "invalid_change"


def test_mcp_member_checks_server_and_tool(tmp_path: Path) -> None:
    """MCP 成员要求 server / tool 字符串与对象形状的 arguments。"""
    root = str(tmp_path)
    _admit(
        "mcp",
        "mcp_call",
        {"server": "docs", "tool": "search", "arguments": {"q": "x"}},
        (root,),
        root,
    )
    with pytest.raises(AccessError):
        _admit("mcp", "mcp_call", {"server": "", "tool": "search"}, (root,), root)
    with pytest.raises(AccessError):
        _admit(
            "mcp",
            "mcp_call",
            {"server": "docs", "tool": "search", "arguments": ["q"]},
            (root,),
            root,
        )


def test_registry_entries_are_json_definitions_only() -> None:
    """登记表只读定义形状，不 import 实现模块即可判定（导入隔离）。"""
    definition = _definition("fs")
    assert json.loads(json.dumps(definition))["operations"]["read_file"]["expose"] is True


def test_core_member_shape_admission(tmp_path: Path) -> None:
    """核心成员按参数形状接纳；内容读取成员仍只做形状检查。"""
    root = str(tmp_path)
    _admit("core", "list_abilities", {}, (root,), root)
    _admit("core", "list_abilities", {"full_name": "ghrah.fs.read_file"}, (root,), root)
    _admit("core", "load_skill", {"full_name": "guide"}, (root,), root)
    _admit("core", "recall_context", {"mode": "list"}, (root,), root)
    _admit("core", "session_info", {}, (root,), root)
    with pytest.raises(AccessError) as missing:
        _admit("core", "load_skill", {}, (root,), root)
    assert missing.value.code == "invalid_change"
    with pytest.raises(AccessError) as bad_mode:
        _admit("core", "recall_context", {"mode": "all"}, (root,), root)
    assert bad_mode.value.code == "invalid_change"


def test_core_control_members_are_not_admitted() -> None:
    """循环控制成员不进授权面：接纳层不为其登记入口。"""
    definition = _definition("core")
    assert admission_for(definition, "list_abilities") is not None
    assert admission_for(definition, "conversation") is None
    assert admission_for(definition, "end_task") is None
