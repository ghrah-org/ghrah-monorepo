# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 产物库单测：内容寻址、幂等、sidecar、列表倒序与上限。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ghrah.subject.project.artifacts import (
    ArtifactRecord,
    ArtifactStore,
    is_valid_artifact_id,
    project_artifact_store,
)


def _svg(body: str = "<svg/>") -> bytes:
    return body.encode("utf-8")


async def test_publish_writes_bytes_and_sidecar(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    data = _svg("<svg width='10'/>")
    record = await store.publish(
        data,
        filename="req.svg",
        mime_type="image/svg+xml",
        title="需求图",
        source={"workspace_id": "w1", "path": "outputs/req.svg"},
    )
    expected_id = hashlib.sha256(data).hexdigest()
    assert record.artifact_id == expected_id
    assert record.size == len(data)
    assert record.title == "需求图"
    assert record.published_at
    assert record.source == {"workspace_id": "w1", "path": "outputs/req.svg"}

    assert (tmp_path / "artifacts" / expected_id).read_bytes() == data
    sidecar = json.loads(
        (tmp_path / "artifacts" / f"{expected_id}.json").read_text(encoding="utf-8")
    )
    assert sidecar == {
        "filename": "req.svg",
        "mime_type": "image/svg+xml",
        "size": len(data),
        "title": "需求图",
        "published_at": record.published_at,
        "source": {"workspace_id": "w1", "path": "outputs/req.svg"},
    }
    # 原子写不留临时文件
    assert not [p for p in (tmp_path / "artifacts").iterdir() if ".tmp-" in p.name]


async def test_publish_is_idempotent_first_metadata_wins(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    data = _svg()
    first = await store.publish(
        data, filename="a.svg", mime_type="image/svg+xml", title="first", source={}
    )
    second = await store.publish(
        data, filename="b.svg", mime_type="text/plain", title="second", source={"x": 1}
    )
    assert second.artifact_id == first.artifact_id
    assert second.filename == "a.svg"
    assert second.title == "first"
    assert second.mime_type == "image/svg+xml"
    assert second.published_at == first.published_at


async def test_load_and_missing(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    record = await store.publish(
        _svg(), filename="a.svg", mime_type="image/svg+xml", title="", source={}
    )
    assert await store.load(record.artifact_id) == record
    assert await store.load("c" * 64) is None
    assert not is_valid_artifact_id("../etc/passwd")
    with pytest.raises(ValueError, match="invalid artifact_id"):
        store.path_for("../etc/passwd")


async def test_list_is_ordered_desc_and_limited(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    for index in range(3):
        await store.publish(
            _svg(f"<svg id='{index}'/>"),
            filename=f"f{index}.svg",
            mime_type="image/svg+xml",
            title=f"t{index}",
            source={},
        )
    records = await store.list()
    assert len(records) == 3
    published = [record.published_at for record in records]
    assert published == sorted(published, reverse=True)
    limited = await store.list(2)
    assert len(limited) == 2
    assert limited == records[:2]
    assert await store.list(0)


async def test_list_skips_junk_and_tolerates_missing_sidecar(tmp_path: Path) -> None:
    directory = tmp_path / "artifacts"
    directory.mkdir()
    orphan = hashlib.sha256(b"orphan").hexdigest()
    (directory / orphan).write_bytes(b"orphan")
    (directory / "not-a-sha.txt").write_text("junk", encoding="utf-8")
    (directory / f".{orphan}.tmp-dead").write_bytes(b"tmp")
    store = ArtifactStore(directory)
    [record] = await store.list()
    assert record.artifact_id == orphan
    assert record.mime_type == "application/octet-stream"
    assert record.size == len(b"orphan")
    assert record.source == {}
    assert isinstance(record, ArtifactRecord)
    assert (await store.load(orphan)) is not None


def test_project_store_factory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "db").mkdir(parents=True)
    (root / ".ghrah-project.json").write_text('{"project_id": "p1"}', encoding="utf-8")
    store = project_artifact_store(str(root))
    assert store.directory == root / "artifacts"
