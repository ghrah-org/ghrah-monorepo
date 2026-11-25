# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""search_tools 的成员轴过滤：只列 visible=true 且 bind=false 的授予成员。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ghrah.ability.member import Member, MemberAxes
from ghrah.ability.surface.config import SurfaceConfig
from ghrah.ability.surface.runtime import ToolSurfaceRuntime

AGENT = "a" * 32
RUN = "run-one"


class _Store:
    """只提供目录快照的最小关系库替身。"""

    scope_id = "project"

    def __init__(self, catalog: Any) -> None:
        self.catalog = catalog

    def current_run(self, agent: str) -> str:
        return RUN

    async def surface_snapshot(self, actor: Any, agent: str) -> Any:
        return self.catalog


def _member(
    tool: Any, *, visible: bool = True, bind: bool = False, type: str = "operation"
) -> Member:
    """按目录条目造一条实例快照成员记录（引用态，data 为空）。"""
    full_name = tool.full_name
    package = full_name.rpartition(".")[0]
    namespace, _, name = package.partition(".")
    return Member(
        namespace=namespace,
        name=name or namespace,
        member=full_name,
        type=type,  # type: ignore[arg-type]
        ability_type="kernel",
        axes=MemberAxes(visible=visible, bind=bind),
    )


def _runtime(catalog: Any, reader: Any) -> ToolSurfaceRuntime:
    runtime = ToolSurfaceRuntime(
        store=_Store(catalog),
        source=None,
        agent=AGENT,
        table=SimpleNamespace(ability_content=None),
        config=SurfaceConfig(mode="progressive"),
        member_snapshot=reader,
    )
    runtime.run = RUN
    return runtime


def _reader(*members: Any):
    async def read() -> list[Any] | None:
        return None if members == (None,) else list(members)

    return read


def _catalog(make_catalog, *tools: Any) -> Any:
    return make_catalog(list(tools), agent=AGENT, run=RUN)


async def test_lists_visible_unbound_granted_operations(make_tool, make_catalog) -> None:
    read = make_tool("read")
    write = make_tool("write")
    runtime = _runtime(
        _catalog(make_catalog, read, write),
        _reader(_member(read, bind=True), _member(write, bind=False)),
    )
    result = await runtime._visible_unbound()
    assert result["status"] == "ok"
    assert [row["name"] for row in result["data"]] == ["write"]
    assert result["data"][0]["member"] == "example.files.write"
    assert result["data"][0]["available"] is True
    assert result["reason"] == ""


async def test_skips_invisible_bound_skill_and_reserved_members(make_tool, make_catalog) -> None:
    visible = make_tool("visible")
    invisible = make_tool("invisible")
    bound = make_tool("bound")
    skill = make_tool("guide")
    mcp = make_tool("echo")
    reserved = make_tool("execute_tool")
    runtime = _runtime(
        _catalog(make_catalog, visible, invisible, bound, skill, mcp, reserved),
        _reader(
            _member(visible),
            _member(invisible, visible=False),
            _member(bound, bind=True),
            _member(skill, type="skill"),
            _member(mcp, type="mcp"),
            _member(reserved),
        ),
    )
    result = await runtime._visible_unbound()
    assert [row["name"] for row in result["data"]] == ["echo", "visible"]


async def test_snapshot_member_must_be_granted(make_tool, make_catalog) -> None:
    granted = make_tool("granted")
    ungranted = make_tool("revoked")
    runtime = _runtime(
        _catalog(make_catalog, granted),
        _reader(_member(granted), _member(ungranted)),
    )
    result = await runtime._visible_unbound()
    assert [row["name"] for row in result["data"]] == ["granted"]


async def test_empty_and_reason(make_tool, make_catalog) -> None:
    bound = make_tool("bound")
    runtime = _runtime(_catalog(make_catalog, bound), _reader(_member(bound, bind=True)))
    result = await runtime._visible_unbound()
    assert result == {
        "status": "empty",
        "data": [],
        "reason": "No visible unbound members",
    }


async def test_missing_store_and_snapshot_report_reasons() -> None:
    unwired = _runtime(None, None)
    result = await unwired._visible_unbound()
    assert result["status"] == "unavailable"
    assert result["data"] == []
    assert "not wired" in result["reason"]

    missing = _runtime(None, _reader(None))
    result = await missing._visible_unbound()
    assert result["status"] == "not_found"
    assert result["data"] == []
    assert result["reason"]


async def test_unreadable_store_reports_unavailable() -> None:
    def broken():
        async def read() -> list[Any] | None:
            raise RuntimeError("agent instance store not configured")

        return read

    runtime = _runtime(None, broken())
    result = await runtime._visible_unbound()
    assert result["status"] == "unavailable"
    assert "not configured" in result["reason"]
