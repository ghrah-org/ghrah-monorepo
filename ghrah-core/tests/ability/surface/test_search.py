# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""摘要索引的名称权重、中文口径、稳定顺序、大小限制与检索耗时。"""

import random
from dataclasses import asdict, replace
from time import perf_counter

import pytest

from ghrah.ability.access.errors import AccessError
from ghrah.ability.surface import AgentToolSearch, ToolSearchIndex
from ghrah.ability.surface.search import tokenize


def test_name_weighting_and_each_metadata_field(make_tool, make_catalog):
    tools = [
        make_tool("read_report", description="Fetch saved document"),
        make_tool("archive", description="read report " * 6),
        make_tool("remove", effects=["filesystem:delete"]),
        make_tool("lookup", tags=["catalogue"]),
        make_tool("remote", package="network"),
    ]
    index = ToolSearchIndex(make_catalog(tools))
    assert index.search("READ REPORT")[0].tool_name == "read_report"
    assert index.search("delete")[0].tool_name == "remove"
    assert index.search("catalogue")[0].tool_name == "lookup"
    assert index.search("network")[0].tool_name == "remote"
    renamed = replace(tools[0], entry=tools[0].entry.model_copy(update={"tool_name": "renamed"}))
    assert ToolSearchIndex(make_catalog([renamed])).search("report")[0].tool_name == "renamed"


def test_chinese_and_normalization_use_same_tokenizer(make_tool, make_catalog):
    index = ToolSearchIndex(
        make_catalog(
            [
                make_tool("read", description="读取项目文件内容"),
                make_tool("write", description="写入项目日志记录"),
            ]
        )
    )
    assert index.search("读取文件")[0].tool_name == "read"
    assert index.search("日志")[0].tool_name == "write"
    assert tokenize("ＲＥＡＤ_文件") == tokenize("read 文件")
    assert tokenize("文件") == ("文", "件", "文件")


def test_index_only_retains_summary_and_excludes_reserved(make_tool, make_catalog):
    tool = replace(
        make_tool("read", description="First line\nsecondlinesecret"),
        input_schema={"type": "object", "description": "private_schema_keyword"},
    )
    index = ToolSearchIndex(
        make_catalog(
            [
                tool,
                make_tool("execute_tool"),
                make_tool("run_script"),
                make_tool("search_tools"),
                make_tool("describe_tool"),
            ]
        )
    )
    assert len(index.documents) == 1
    assert set(asdict(index.documents[0])) == {
        "tool_name",
        "member",
        "package",
        "description",
        "tags",
        "effects",
    }
    assert index.search("private_schema_keyword") == []
    assert index.search("secondlinesecret") == []
    assert index.search("execute_tool") == []
    assert "private_schema_keyword" not in repr(vars(index))


def test_stable_ties_limits_and_empty_queries(make_tool, make_catalog):
    tools = [make_tool(f"tool_{index:03}", tags=["shared"]) for index in range(50)]
    index = ToolSearchIndex(make_catalog(tools))
    reversed_index = ToolSearchIndex(make_catalog(reversed(tools)))
    first = index.search("shared")
    assert len(first) == 8
    assert [item.tool_name for item in first] == [f"tool_{number:03}" for number in range(8)]
    for _ in range(20):
        assert index.search("shared") == reversed_index.search("shared") == first
    assert len(index.search("shared", limit=200)) == 20
    for query in ("", "   ", "!@#$%^", "nonexistent"):
        assert index.search(query) == []
    for limit in (0, -1, True, "8"):
        with pytest.raises(ValueError):
            index.search("shared", limit=limit)


@pytest.mark.parametrize("allowed_step", [1, 4])
def test_200_random_queries_never_return_ungranted_members(allowed_step, make_tool, make_catalog):
    all_tools = [make_tool(f"member_{index:03}") for index in range(200)]
    allowed = all_tools[::allowed_step]
    index = ToolSearchIndex(make_catalog(allowed))
    allowed_names = {tool.entry.tool_name for tool in allowed}
    randomizer = random.Random(20261106)
    queries = [f"member_{randomizer.randrange(200):03}" for _ in range(200)]
    started = perf_counter()
    results = [index.search(query) for query in queries]
    elapsed = perf_counter() - started
    assert all(item.tool_name in allowed_names for result in results for item in result)
    assert {item.tool_name for item in index.documents} == allowed_names
    assert elapsed / 200 < 0.01, f"平均检索耗时 {elapsed / 200 * 1000:.3f} ms"


class _Store:
    project_id = "project"

    def __init__(self, catalog):
        self.catalog = catalog

    def current_run(self, agent):
        return self.catalog.run_id

    async def surface_snapshot(self, actor, agent):
        assert actor.subject_id == agent and not actor.operator
        return self.catalog


async def test_refresh_grants_selection_version_and_agent_isolation(make_tool, make_catalog):
    first = make_tool("read", tags=["oldtag"])
    store = _Store(make_catalog([first]))
    search = AgentToolSearch("alice", "alice-run", store=store)
    assert (await search.search("oldtag"))[0].tool_name == "read"
    original_index = search._index
    await search.search("read")
    assert search._index is original_index
    # 版本变化不必改变 Agent 修订；也必须重建。
    changed = replace(first, version_digest="version-2", tags=("newtag",))
    store.catalog = make_catalog([changed])
    assert await search.search("oldtag") == []
    assert (await search.search("newtag"))[0].tool_name == "read"
    assert search._index is not original_index
    # 取消选择或撤销后，旧名称与旧摘要不能命中。
    store.catalog = make_catalog([], revision=2)
    assert await search.search("read") == []
    store.catalog = make_catalog([changed], revision=3)
    assert await search.search("newtag")
    bob = AgentToolSearch(
        "bob", "bob-run", store=_Store(make_catalog([], agent="bob", run="bob-run"))
    )
    assert await bob.search("newtag") == []
    assert bob._index is not search._index


@pytest.mark.parametrize("change", ["agent", "project", "run"])
async def test_foreign_or_stale_snapshot_discards_cached_index(change, make_tool, make_catalog):
    store = _Store(make_catalog([make_tool("read")]))
    search = AgentToolSearch("alice", "alice-run", store=store)
    assert await search.search("read")
    store.catalog = replace(store.catalog, **{f"{change}_id": "foreign"})
    with pytest.raises(AccessError, match="access_denied"):
        await search.search("read")
    assert search._index is None
