# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``model.configure`` 已切换到 Core 的实例快照（不再由 Subject 拒绝）。

配置权威在 Core：Subject 只把"改模型"这件事转给 ``agents.update_model``，自己不
再持有模型与提示词。
"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.subject.project.manager import ProjectManager
from ghrah.subject.project.models import AgentRecord, make_project_record

AGENT_ID = "a" * 32


class _ProjectStore:
    """内存 ProjectStore：只实现实体操作路径用到的 get / update。"""

    def __init__(self, record: Any) -> None:
        self.record = record

    async def get(self, project_id: str, include_archived: bool = False) -> Any:
        return self.record if project_id == self.record.project_id else None

    async def update(self, project_id: str, expected_version: int, mutator: Any) -> Any:
        assert expected_version == self.record.version
        updated = mutator(self.record)
        self.record = updated.model_copy(update={"version": updated.version + 1})
        return self.record


class _Handle:
    """假 Core 句柄：记录 update_model 调用并回答运行态。"""

    def __init__(self) -> None:
        self.project_id = ""
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    @property
    def is_connected(self) -> bool:
        return True

    async def get_entity_runtime(self, agent_id: str) -> dict[str, Any]:
        return {"availability": "online", "incarnation_id": "inc-1", "agent_id": agent_id}

    async def update_model(
        self,
        agent_id: str,
        endpoint_name: str,
        model_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((agent_id, endpoint_name, model_overrides))
        return {"success": True, "data": {"agent_id": agent_id, "name": "weekly"}}


class _Transport:
    def __init__(self, handle: _Handle) -> None:
        self._handle = handle

    def has_cluster(self, cluster_id: str) -> bool:
        return True

    def get_handle(self, cluster_id: str) -> _Handle:
        return self._handle

    async def ensure_cluster(self, *_args: Any, **_kwargs: Any) -> _Handle:
        return self._handle


@pytest.fixture
def wired() -> tuple[ProjectManager, _ProjectStore, _Handle]:
    project = make_project_record(name="demo", project_root_locator="file:///tmp/demo").model_copy(
        update={
            "agents": [
                AgentRecord(
                    name="weekly",
                    agent_id=AGENT_ID,
                    desired_state="running",
                    runtime_status="running",
                )
            ]
        }
    )
    store = _ProjectStore(project)
    handle = _Handle()
    handle.project_id = project.project_id
    manager = ProjectManager(
        store=store,  # type: ignore[arg-type]
        workspace_mgr=None,  # type: ignore[arg-type]
        task_mgr=None,  # type: ignore[arg-type]
        cluster_registry=_Transport(handle),  # type: ignore[arg-type]
    )
    return manager, store, handle


async def test_model_configure_is_forwarded_to_core(
    wired: tuple[ProjectManager, _ProjectStore, _Handle],
) -> None:
    manager, store, handle = wired
    payload = {
        "project_id": store.record.project_id,
        "agent_id": AGENT_ID,
        "operation": {
            "type": "model.configure",
            "endpoint_name": "glm-x",
            "model_overrides": {"temperature": 0.2},
            "expected_revision": store.record.version,
        },
    }

    result = await manager.handle_command("agent_entity", payload)

    assert result["success"] is True, result
    # 交给 Core 落库并应用，Subject 不保存模型事实
    assert handle.calls == [(AGENT_ID, "glm-x", {"temperature": 0.2})]
    assert store.record.agents[0].runtime_status == "running"


async def test_model_configure_reports_core_failure(
    wired: tuple[ProjectManager, _ProjectStore, _Handle],
) -> None:
    manager, store, handle = wired

    async def _fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"success": False, "error": "snapshot not found"}

    handle.update_model = _fail  # type: ignore[method-assign]
    payload = {
        "project_id": store.record.project_id,
        "agent_id": AGENT_ID,
        "operation": {
            "type": "model.configure",
            "endpoint_name": "glm-x",
            "expected_revision": store.record.version,
        },
    }

    result = await manager.handle_command("agent_entity", payload)

    assert result["success"] is False
    assert "agent_config_update_failed" in str(result.get("error"))
