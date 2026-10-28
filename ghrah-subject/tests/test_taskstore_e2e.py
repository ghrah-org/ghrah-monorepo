# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""TaskStore 端到端测试：真实 Context + 装配链 + bridge_command。

覆盖验收①（无证据 submit Denial 缺口完整）、验收②（checker 命中/未命中）、
探针 1（归因链取回）、探针 2（自批拒）、事件广播。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ghrah.plugin.loader import DiscoveredPlugin
from ghrah.plugin.spec import PluginSpec
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import SubjectConfig, TaskStoreConfig
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta


class _CheckerPluginUnit(SubjectUnit):
    """最小插件 unit（不提供命令，仅使插件可挂载；checker 经工厂注册）。"""

    def __init__(self, plugin_id: str) -> None:
        self._meta = UnitMeta(name=plugin_id, routes=RouteSpec())

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: CommandContext
    ) -> dict[str, Any]:
        return {"success": False, "error": f"no commands: {command}"}


def _make_checker(name: str) -> Any:
    def checker(evidence: Any, task: Any, config: Any) -> dict[str, Any]:
        kinds = [e.get("kind") for e in evidence["items"]]
        return {"passed": "git_commit" in kinds, "detail": None}

    return checker


def _config(db_path: str) -> SubjectConfig:
    return SubjectConfig(
        db_path=str(Path(db_path).parent / "subject.sqlite3"),
        workspace_root=str(Path(db_path).parent / "workspace"),
        manifest_root=str(Path(db_path).parent / "manifests"),
        taskstore_slice=TaskStoreConfig(enabled=True, db_path=db_path),
    )


@pytest.fixture
async def assembled(tmp_path):
    config = _config(str(tmp_path / "ts.sqlite3"))
    emitted: list[tuple[str, dict[str, Any]]] = []
    ctx_cm = Context()
    ctx = await ctx_cm.__aenter__()

    for event_type in ("task_delivered", "task_verified", "task_rejected"):

        def collect(payload: Any, _et: str = event_type) -> None:
            emitted.append((_et, dict(payload)))

        ctx.on(f"event/{event_type}", collect)

    await assemble_subject(ctx, config, profile="coexistence")
    yield ctx, emitted, config
    await ctx_cm.__aexit__(None, None, None)


async def _fake_checker_plugin_mounted(ctx: Any, plugin_id: str = "fake-checker-plugin") -> None:
    """经 mount_enabled_plugins 挂载 fake checker 插件（复用装配链的 PluginSession）。

    assemble_subject 已挂载 PluginSession 并登记 PLUGIN_SESSION_SERVICE；此处
    直接复用该 session 与其 state（不二次挂载，避免 service 冲突）。
    """
    from ghrah.plugin.assembly import PluginAssembly

    from ghrah.subject.config import PluginTrustConfig, SubjectConfig
    from ghrah.subject.runtime.plugin_mount import mount_enabled_plugins
    from ghrah.subject.runtime.plugin_session import PLUGIN_SESSION_SERVICE
    from ghrah.subject.runtime.route_registry import UnitRouteRegistry
    from ghrah.subject.runtime.service_keys import TASKSTORE_CHECKERS

    spec = PluginSpec(
        plugin_id=plugin_id,
        version="0.1.0",
        provides={"checkers": ["commit_in_repo"], "capabilities": [f"{plugin_id}:attr"]},
    )
    discovered = [
        DiscoveredPlugin(
            spec=spec,
            entry_point_name=plugin_id,
            dist_name=None,
            unit_factory=lambda: _CheckerPluginUnit(plugin_id),
            checker_factory=_make_checker,
        )
    ]
    session = ctx.get(PLUGIN_SESSION_SERVICE.name, strict=False)
    assert session is not None, "装配链应已挂载 PluginSession"
    state = session.state
    fibers: dict[str, Any] = {}

    registry = ctx.get(TASKSTORE_CHECKERS.name, strict=False)

    async def sink(pid: str, checkers: dict[str, Any]) -> None:
        if registry is None:
            return
        for name, checker in checkers.items():
            if checker is None:
                registry.unregister(name)
            else:
                registry.register(name, checker)

    report = await mount_enabled_plugins(
        ctx,
        SubjectConfig(plugin_trust_slice=PluginTrustConfig(discoverable=[plugin_id])),
        discovered=discovered,
        assemblies=[("p1", PluginAssembly(enabled=[plugin_id]))],
        route_registry=UnitRouteRegistry(),
        fibers=fibers,
        state=state,
        session=session,
        on_checkers=sink if registry is not None else None,
    )
    assert report.outcomes[0].status == "mounted", report.outcomes[0]


async def test_end_to_end_attribution(assembled) -> None:
    """验收①② + 探针 1/2 端到端：造 task → checker 插件 → submit/verify → 归因链取回。"""
    ctx, emitted, _config_obj = assembled

    # 1) 内核 facade 造 task（含 verification checks=["commit_in_repo"]）
    kernel = ctx.get("taskstore_service")
    from ghrah.taskstore.models import Verification

    task = await kernel.create_task(
        project_id="p1",
        title="demo",
        verification=Verification(
            evidence_min=1, evidence_kinds=["git_commit"], checks=["commit_in_repo"]
        ),
    )

    # 2) fake checker 插件挂载（真实装配链路径 → checker 注册进内核）
    await _fake_checker_plugin_mounted(ctx)

    # 3) 无证据 submit → Denial 缺口完整（验收① 端到端）
    result = await bridge_command(
        ctx,
        "task_submit_completion",
        {"task_id": task.task_id, "claimant_id": "agent-1"},
    )
    assert result["success"] is False
    gaps = result["data"]["gaps"]
    assert gaps["evidence_have"] == 0
    assert gaps["evidence_need"] == 1
    assert gaps["missing_evidence_kinds"] == ["git_commit"]
    assert gaps["missing_checks"] == []  # checker 已挂载：checks 不缺
    assert "gaps" in result["data"]

    # 4) 补证据 submit → delivered + task_delivered 事件
    result = await bridge_command(
        ctx,
        "task_submit_completion",
        {
            "task_id": task.task_id,
            "claimant_id": "agent-1",
            "evidence": [{"kind": "git_commit", "ref": "abc", "digest": "d1"}],
        },
    )
    assert result["success"] is True, result
    claim = result["data"]["claim"]
    assert claim["state"] == "submitted"
    assert claim["checks"] == [{"checker": "commit_in_repo", "passed": True, "detail": None}]
    delivered = [e for e in emitted if e[0] == "task_delivered"]
    assert delivered, "expected task_delivered event"
    assert delivered[-1][1]["claim"]["claim_id"] == claim["claim_id"]
    assert delivered[-1][1]["previous_status"] == "pending"

    # 5) task_verify（他人）→ completed + task_verified 事件
    result = await bridge_command(
        ctx,
        "task_verify",
        {
            "task_id": task.task_id,
            "claim_id": claim["claim_id"],
            "verdict": "verified",
            "verifier_id": "agent-2",
        },
    )
    assert result["success"] is True
    assert result["data"]["task_status"] == "completed"
    verified = [e for e in emitted if e[0] == "task_verified"]
    assert verified
    assert verified[-1][1]["claim"]["state"] == "verified"

    # 6) task_list_claims / task_dump 取回归因链（探针 1：claimant/evidence/verdict）
    result = await bridge_command(
        ctx,
        "task_list_claims",
        {"task_id": task.task_id},
    )
    claims = result["data"]["claims"]
    assert len(claims) == 1
    assert claims[0]["claimant_id"] == "agent-1"
    assert claims[0]["verdict_by"] == "agent-2"
    assert claims[0]["state"] == "verified"
    assert len(claims[0]["evidence"]) == 1
    assert claims[0]["evidence"][0]["kind"] == "git_commit"

    result = await bridge_command(ctx, "task_dump", {"project_id": "p1"})
    dump = result["data"]
    assert dump["tasks"][0]["status"] == "completed"
    assert dump["claims"][0]["verdict_by"] == "agent-2"
    assert len(dump["evidence"]) == 1


async def test_end_to_end_checker_miss_and_self_approval(assembled) -> None:
    """验收②（未命中）+ 探针 2：checker 未命中 → gaps 含候选；claimant 自批拒。"""
    ctx, _bus, _config_obj = assembled
    kernel = ctx.get("taskstore_service")
    from ghrah.taskstore.models import Verification

    # checker 未注册的 task：submit 缺口含候选（此处无插件注册表 → 候选空）
    task = await kernel.create_task(
        project_id="p1",
        title="demo2",
        verification=Verification(evidence_min=1, checks=["ghost_checker"]),
    )
    result = await bridge_command(
        ctx,
        "task_submit_completion",
        {
            "task_id": task.task_id,
            "claimant_id": "agent-1",
            "evidence": [{"kind": "git_commit", "ref": "x"}],
        },
    )
    assert result["success"] is False
    gaps = result["data"]["gaps"]
    assert gaps["missing_checks"] == [{"checker": "ghost_checker", "candidates": []}]

    # 探针 2：approver 非空时 claimant 自批拒
    t2 = await kernel.create_task(
        project_id="p1",
        title="t2",
        verification=Verification(evidence_min=0, approver="human"),
    )
    submit = await bridge_command(
        ctx,
        "task_submit_completion",
        {"task_id": t2.task_id, "claimant_id": "agent-1"},
    )
    assert submit["success"] is True
    result = await bridge_command(
        ctx,
        "task_verify",
        {
            "task_id": t2.task_id,
            "claim_id": submit["data"]["claim"]["claim_id"],
            "verdict": "verified",
            "verifier_id": "agent-1",  # 自批
        },
    )
    assert result["success"] is False
    assert "approver" in result["error"] or "claim" in result["error"]
