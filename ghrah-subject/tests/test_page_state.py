# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""工作台动态页面存储的读写与版本语义。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ghrah.subject.page.store import PageStateStore


async def _store(tmp_path: Path) -> PageStateStore:
    store = PageStateStore(tmp_path / "pages.sqlite3", "project-1")
    await store.start()
    return store


async def test_set_replaces_page_and_bumps_revision(tmp_path: Path) -> None:
    """整页替换会写入标题/HTML/数据，并把 revision 从 1 递增。"""
    store = await _store(tmp_path)
    try:
        first = await store.set_page(
            page_id="main", title="需求核对", html="<p>v1</p>", data={"q": 1}, updated_by="a1"
        )
        second = await store.set_page(
            page_id="main", title="需求核对", html="<p>v2</p>", data={"q": 2}, updated_by="a1"
        )
        assert first["revision"] == 1
        assert second["revision"] == 2
        current = await store.get("main")
        assert current is not None
        assert current["html"] == "<p>v2</p>"
        assert current["data"] == {"q": 2}
    finally:
        await store.stop()


async def test_patch_merges_data_and_requires_page(tmp_path: Path) -> None:
    """patch 只合并 data；页面不存在时报 page_not_found。"""
    store = await _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="page_not_found"):
            await store.patch_data(page_id="main", data={"q": 1}, updated_by="a1")
        await store.set_page(
            page_id="main", title="页面", html="<p/>", data={"q": 1, "n": "x"}, updated_by="a1"
        )
        result = await store.patch_data(page_id="main", data={"q": 2}, updated_by="a2")
        assert result["revision"] == 2
        current = await store.get("main")
        assert current is not None
        assert current["data"] == {"q": 2, "n": "x"}
        assert current["updated_by"] == "a2"
    finally:
        await store.stop()


async def test_get_returns_none_before_first_render(tmp_path: Path) -> None:
    """未渲染过的项目没有页面。"""
    store = await _store(tmp_path)
    try:
        assert await store.get("main") is None
    finally:
        await store.stop()
