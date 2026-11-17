# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``project_remove_agent``：runtime 已离线时仍可删除 Project 定义。

C3 端口化把 terminate 的失败从稳定码（``agent_identity_mismatch`` /
``agent_not_found``）改成 ``ApiError`` 文本（``NOT_FOUND: agent '<id>' is not
online``）。旧判定只认稳定码，导致「spawn 失败或已停止」的 Agent 删除被拒
（定义保留、运行侧本来就没有实例）。本文件锁定两条语义：

1. 「运行侧已无该 Agent」的两种形态都视为已终止，继续删定义；
2. 真正的终止失败仍拒绝删除，避免留下「定义已删、实例仍在跑」的游离 Agent。
"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import Problem, ProblemCode

from ghrah.subject.project.manager import ProjectManager, _is_terminate_absent
from ghrah.subject.project.models import AgentRecord, ProjectRecord, make_project_record

AGENT_ID = "a" * 32


class _FakeHandle:
    """假句柄：只实现删除路径用到的 ``delete_assembly``（停止 + 删快照）。"""

    def __init__(
        self,
        delete_result: dict[str, Any] | None = None,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self._delete_result = delete_result or {"success": True, "data": {}}
        self._raise_exc = raise_exc
        self.delete_calls: list[str] = []

    @property
    def is_connected(self) -> bool:
        return True

    async def delete_assembly(self, agent_id: str) -> dict[str, Any]:
        self.delete_calls.append(agent_id)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._delete_result


class _FakeTransport:
    """只有已登记的在线 cluster 才会被 remove 路径使用。"""

    def __init__(self, handle: _FakeHandle | None) -> None:
        self._handle = handle

    def has_cluster(self, cluster_id: str) -> bool:
        return self._handle is not None

    def get_handle(self, cluster_id: str) -> _FakeHandle:
        assert self._handle is not None
        return self._handle


class _FakeStore:
    """内存 ProjectStore：只实现 remove 路径用到的 get/update。"""

    def __init__(self, record: ProjectRecord) -> None:
        self.record = record
        self.update_calls = 0

    async def get(self, project_id: str, include_archived: bool = False) -> ProjectRecord | None:
        return self.record if project_id == self.record.project_id else None

    async def update(
        self,
        project_id: str,
        expected_version: int,
        mutator: Any,
    ) -> ProjectRecord:
        assert expected_version == self.record.version
        self.update_calls += 1
        updated = mutator(self.record)
        self.record = updated.model_copy(update={"version": updated.version + 1})
        return self.record


def _record() -> ProjectRecord:
    agent = AgentRecord(
        name="requirement",
        agent_id=AGENT_ID,
        desired_state="running",
        runtime_status="failed",
        last_error="model_configuration_invalid",
    )
    return make_project_record(
        name="demo",
        project_root_locator="file:///tmp/demo",
    ).model_copy(update={"agents": [agent]})


def _remove_payload(record: ProjectRecord) -> dict[str, Any]:
    return {
        "project_id": record.project_id,
        "agent_id": AGENT_ID,
        "agent_name": "requirement",
        "expected_version": record.version,
    }


async def test_remove_agent_accepts_absent_instance() -> None:
    """快照/进程都不存在（``delete_assembly`` 幂等成功）→ 删定义成功。"""
    record = _record()
    store = _FakeStore(record)
    handle = _FakeHandle({"success": True, "data": {"deleted": False, "stopped": False}})
    manager = ProjectManager(
        store=store,  # type: ignore[arg-type]
        workspace_mgr=None,  # type: ignore[arg-type]
        task_mgr=None,  # type: ignore[arg-type]
        cluster_registry=_FakeTransport(handle),  # type: ignore[arg-type]
    )

    result = await manager.handle_command("project_remove_agent", _remove_payload(record))

    assert result["success"] is True, result
    assert store.update_calls == 1
    assert store.record.agents == []
    assert handle.delete_calls == [AGENT_ID]


async def test_remove_agent_accepts_raised_port_api_error() -> None:
    """端口实现直接抛 ``ApiError('NOT_FOUND: ... is not online')`` 同样视为已终止。"""
    record = _record()
    store = _FakeStore(record)
    handle = _FakeHandle(
        raise_exc=ApiError(
            Problem.make(
                ProblemCode.NOT_FOUND,
                capability="agents.delete",
                detail=f"agent '{AGENT_ID}' is not online",
            )
        )
    )
    manager = ProjectManager(
        store=store,  # type: ignore[arg-type]
        workspace_mgr=None,  # type: ignore[arg-type]
        task_mgr=None,  # type: ignore[arg-type]
        cluster_registry=_FakeTransport(handle),  # type: ignore[arg-type]
    )

    result = await manager.handle_command("project_remove_agent", _remove_payload(record))

    assert result["success"] is True, result
    assert store.update_calls == 1
    assert store.record.agents == []


async def test_remove_agent_keeps_definition_on_real_terminate_failure() -> None:
    """真正的终止失败仍拒绝删除，定义保留。"""
    record = _record()
    store = _FakeStore(record)
    handle = _FakeHandle({"success": False, "error": "supervisor unavailable"})
    manager = ProjectManager(
        store=store,  # type: ignore[arg-type]
        workspace_mgr=None,  # type: ignore[arg-type]
        task_mgr=None,  # type: ignore[arg-type]
        cluster_registry=_FakeTransport(handle),  # type: ignore[arg-type]
    )

    result = await manager.handle_command("project_remove_agent", _remove_payload(record))

    assert result["success"] is False, result
    assert result["error"] == "agent_terminate_failed"
    assert store.update_calls == 0
    assert [agent.agent_id for agent in store.record.agents] == [AGENT_ID]


def test_is_terminate_absent_recognizes_both_shapes() -> None:
    """稳定码（旧命令面）与 ApiError 文本（端口面）都算已终止。"""
    assert _is_terminate_absent("agent_not_found") is True
    assert _is_terminate_absent("agent_identity_mismatch") is True
    assert _is_terminate_absent(f"NOT_FOUND: agent '{AGENT_ID}' is not online") is True
    assert _is_terminate_absent("not_found: agent x") is True
    assert _is_terminate_absent("supervisor unavailable") is False
    assert _is_terminate_absent("") is False
