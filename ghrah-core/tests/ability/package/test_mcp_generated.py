# SPDX-License-Identifier: Apache-2.0
"""页面驱动的 MCP 定义生成与安装：read_only、前缀、版本叠加、幂等。"""

from __future__ import annotations

from pathlib import Path

from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.package.installer import AbilityInstaller
from ghrah.ability.package.sources.mcp import build_mcp_definition, tool_keys


def header(version: str = "0.0.1") -> dict:
    return {
        "format": "ghrah.ability-definition",
        "schema_version": "2",
        "origin": "mcp",
        "identity": {"namespace": "mcp", "name": "generated", "version": version},
        "title": "生成的服务",
        "description": "页面选择生成",
        "mcp": {"server": "local-server"},
    }


def tools(*, read_only: bool = True) -> list[dict]:
    return [
        {
            "name": "Echo",
            "title": "回显",
            "description": "回显文本",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "output_schema": {"type": "object"},
            "read_only_hint": read_only,
        },
        {
            "name": "Write",
            "title": "写入",
            "description": "写入数据",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "read_only_hint": False,
        },
    ]


def test_tool_keys_honors_prefix_and_reserved_names() -> None:
    keys = tool_keys(["Echo", "search_tools"], prefix="ali_")
    assert keys["Echo"] == "ali_echo"
    assert keys["search_tools"] == "ali_search_tools"


def test_build_definition_keeps_selected_members_and_read_only() -> None:
    raw = build_mcp_definition(header(), tools(), prefix="ali_")
    definition = parse_definition(raw, source="test")
    assert set(definition.operations) == {"ali_echo", "ali_write"}
    echo = definition.operations["ali_echo"]
    assert echo.mcp is not None
    assert echo.mcp.read_only is True
    assert echo.mcp.tool == "Echo"
    write = definition.operations["ali_write"]
    assert write.mcp is not None
    assert write.mcp.read_only is False
    members = definition.to_members()
    assert {member.member for member in members} == {
        "mcp.generated.ali_echo",
        "mcp.generated.ali_write",
    }
    echo_member = next(member for member in members if member.key == "ali_echo")
    assert echo_member.data["read_only"] is True


async def test_install_selected_definition_is_idempotent_and_stacks_version(tmp_path: Path) -> None:
    installer = AbilityInstaller(tmp_path / "store")
    first = await installer.install_mcp_definition(header(), tools(), prefix="ali_")
    assert first.version == "0.0.1"
    assert first.already_installed is False
    raw = (first.root / "ability.yaml").read_text(encoding="utf-8")
    definition = parse_definition(raw, source="test")
    assert set(definition.operations) == {"ali_echo", "ali_write"}

    same = await installer.install_mcp_definition(header(), tools(), prefix="ali_")
    assert same.version == "0.0.1"
    assert same.already_installed is True
    assert same.package_digest == first.package_digest

    changed = await installer.install_mcp_definition(
        header(), tools(read_only=False), prefix="ali_"
    )
    assert changed.version == "0.0.2"
    assert changed.already_installed is False

    requested_old = await installer.install_mcp_definition(
        header("0.0.1"), tools(), prefix="ali_", requested_version="0.0.1"
    )
    assert requested_old.version == "0.0.3"
