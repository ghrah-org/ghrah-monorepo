# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目 Shell 记忆 REST 接口：真实 HTTP 请求与 SQLite 持久化。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from test_units_ability_control import _request, _serve

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.models import make_project_record
from ghrah.subject.project.store import ProjectStore
from ghrah.subject.runtime.service_keys import (
    CORE_CATALOG_SERVICE,
    CORE_PORT_SERVICE,
    OBSERVER_ENDPOINT,
    OBSERVER_EVENT_BUS,
    PROJECT_STORE,
)
from ghrah.subject.units.ability_control import AbilityControlUnit


@pytest.fixture
async def api(tmp_path):
    store = ProjectStore(tmp_path / "subject.db")
    await store.start()
    first = make_project_record(name="A")
    second = make_project_record(name="B")
    await store.upsert(first)
    await store.upsert(second)
    app = FastAPI()
    config = SubjectConfig(observer_http_api_enabled=True)
    services = {
        OBSERVER_ENDPOINT.name: SimpleNamespace(app=app),
        PROJECT_STORE.name: store,
        CORE_PORT_SERVICE.name: object(),
        CORE_CATALOG_SERVICE.name: object(),
        OBSERVER_EVENT_BUS.name: object(),
    }
    # 通过生产 Unit 挂载，避免只测了独立 router，却没有实际注册接口。
    await AbilityControlUnit(config).init(SimpleNamespace(get=services.__getitem__))
    server, task, port = await _serve(app)
    try:
        yield store, config, f"http://127.0.0.1:{port}/api/v1/projects", first, second
    finally:
        server.should_exit = True
        await task
        await store.stop()


async def test_query_add_deduplicate_and_delete(api):
    store, _, root, first, second = api
    url = f"{root}/{first.project_id}/shell-allow"
    status, body = await _request(url)
    assert status == 200
    assert body["entries"] == []
    assert len(body["builtins"]) == 3
    assert all(rule["readonly"] for rule in body["builtins"])

    entry = {"kind": "class", "key": "pnpm:script:lint", "label": "Run lint"}
    status, saved = await _request(url, method="POST", body=entry)
    assert status == 200
    assert saved["key"] == entry["key"]
    assert saved["created_at"]
    record = await store.get(first.project_id)
    version = record.version
    status, duplicate = await _request(url, method="POST", body=entry)
    assert status == 200
    assert duplicate == saved
    record = await store.get(first.project_id)
    assert record.version == version
    assert len(record.shell_allow) == 1

    status, other = await _request(f"{root}/{second.project_id}/shell-allow")
    assert status == 200
    assert other["entries"] == []
    status, _ = await _request(
        f"{root}/{second.project_id}/shell-allow/class/{entry['key']}", method="DELETE"
    )
    assert status == 404
    status, _ = await _request(f"{url}/class/{entry['key']}", method="DELETE")
    assert status == 204
    status, body = await _request(url)
    assert status == 200
    assert body["entries"] == []
    assert len(body["builtins"]) == 3


async def test_exact_paths_roundtrip_and_delete_only_selected_entry(api):
    store, _, root, first, _ = api
    url = f"{root}/{first.project_id}/shell-allow"
    commands = ['python report.py --output "C:/reports/a b.txt"', "pnpm run lint"]
    for command in commands:
        status, _ = await _request(
            url,
            method="POST",
            body={
                "kind": "exact",
                "key": command,
                "label": command,
                "created_at": "2000-01-01T00:00:00Z",
            },
        )
        assert status == 200
    status, _ = await _request(f"{url}/exact/{quote(commands[0], safe='')}", method="DELETE")
    assert status == 204
    record = await store.get(first.project_id)
    assert [entry.key for entry in record.shell_allow] == [commands[1]]
    assert record.shell_allow[0].created_at.year != 2000


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("exact", "rm -rf /tmp/x"),
        ("exact", "python report.py > /etc/passwd"),
        ("exact", "uv run --directory . --with foo rm -rf /tmp/x"),
        ("class", "uv:run:rm"),
        ("class", "uv:run:python"),
        ("class", "pnpm:script:exec"),
        ("class", "arbitrary"),
        ("class", "uv:run:pytest"),
        ("exact", "pnpm test"),
    ],
)
async def test_invalid_or_builtin_memory_is_not_saved(api, kind, key):
    store, _, root, first, _ = api
    status, _ = await _request(
        f"{root}/{first.project_id}/shell-allow",
        method="POST",
        body={
            "kind": kind,
            "key": key,
            "label": key,
        },
    )
    assert status == 400
    assert (await store.get(first.project_id)).shell_allow == []


async def test_builtin_cannot_be_deleted(api):
    _, _, root, first, _ = api
    status, _ = await _request(
        f"{root}/{first.project_id}/shell-allow/class/uv:run:pytest", method="DELETE"
    )
    assert status == 404


async def test_missing_project_and_invalid_body(api):
    _, _, root, first, _ = api
    for method in ["GET", "POST", "DELETE"]:
        url = f"{root}/missing/shell-allow" + ("/exact/test" if method == "DELETE" else "")
        status, _ = await _request(url, method=method, body={} if method == "POST" else None)
        assert status == 404
    status, _ = await _request(
        f"{root}/{first.project_id}/shell-allow", method="POST", body={"kind": "class"}
    )
    assert status == 400


async def test_disabled_http_api(api):
    _, config, root, first, _ = api
    config.observer_http_api_enabled = False
    status, _ = await _request(f"{root}/{first.project_id}/shell-allow")
    assert status == 503


async def test_script_name_with_colon(api):
    _, _, root, first, _ = api
    url = f"{root}/{first.project_id}/shell-allow"
    status, saved = await _request(
        url,
        method="POST",
        body={
            "kind": "class",
            "key": "pnpm:script:lint:fix",
            "label": "lint:fix",
        },
    )
    assert status == 200
    assert saved["key"] == "pnpm:script:lint:fix"


async def test_concurrent_additions_do_not_overwrite_each_other(api):
    store, _, root, first, _ = api
    url = f"{root}/{first.project_id}/shell-allow"
    results = await asyncio.gather(
        *[
            _request(url, method="POST", body={"kind": "exact", "key": command, "label": command})
            for command in ["python a.py", "python b.py"]
        ]
    )
    assert all(status in {200, 409} for status, _ in results)
    saved_keys = {body["key"] for status, body in results if status == 200}
    assert saved_keys
    record = await store.get(first.project_id)
    assert {entry.key for entry in record.shell_allow} == saved_keys
