# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""一次性创建服务：模板快照 → 装配输入 → Core 创建 → AgentRecord。

用假模板源与假创建入口验证编排口径，不起真实 Core：
记录里只有身份/展示/来源，配置一个字都不留；授权不在 Subject 侧写。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from ghrah.protocol.views.core import AgentStartView

from ghrah.subject.agents.creation import (
    CreateAgentRequest,
    CreationError,
    create_agent,
)
from ghrah.subject.agents.render import GrantedPackage
from ghrah.subject.template_store.models import AgentTemplateConfig, AgentTemplateRecord

_NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
_WORKSPACES = {"default": "/ws"}

_SNAPSHOT: dict[str, Any] = {
    "metadata": {
        "namespace": "writing",
        "name": "weekly",
        "title": "周报助手",
        "description": "每周汇总",
        "tags": ["writing"],
    },
    "model": {"endpoint_name": "default-llm"},
    "prompt": {"system_prompt": "先列事实"},
    "context_policy": {"max_iterations": -1, "communication_timeout": 120},
    "ability_request": {
        "surface": {"mode": "auto"},
        "packages": [
            {
                "package": "ghrah.fs",
                "origin": "builtin",
                "operations": [{"key": "read_file"}],
            }
        ],
    },
    "permission_request": {"auto_approve": []},
    "path_request": [
        {
            "scope": "package",
            "package": "ghrah.fs",
            "workspace_role": "default",
            "subpath": "docs",
            "access": "read",
            "deny": False,
        }
    ],
}

_GRANTED = [
    GrantedPackage(
        package="ghrah.fs",
        alias="fs",
        instance_id="i" * 32,
        operations=("read_file",),
    )
]


class _Templates:
    """假模板源：按 ``(template_id, revision)`` 返回构造好的记录。"""

    def __init__(self, snapshot: dict[str, Any] | None) -> None:
        self._record = None if snapshot is None else _record_from(snapshot)
        self.asked: list[tuple[str, int]] = []

    async def get_version(self, template_id: str, revision: int) -> AgentTemplateRecord | None:
        self.asked.append((template_id, revision))
        if self._record is None:
            return None
        return self._record.model_copy(update={"template_id": template_id, "revision": revision})


def _record_from(snapshot: dict[str, Any]) -> AgentTemplateRecord:
    """把七组配置 dict 转成一条模板记录（``archived_at`` 单独传）。"""
    config = AgentTemplateConfig.model_validate(
        {key: value for key, value in snapshot.items() if key != "archived_at"}
    )
    return AgentTemplateRecord(
        template_id="t" * 32,
        revision=1,
        format="ghrah.agent-templates.v1",
        metadata=config.metadata,
        model=config.model,
        prompt=config.prompt,
        context_policy=config.context_policy,
        ability_request=config.ability_request,
        permission_request=config.permission_request,
        path_request=config.path_request,
        created_at="2026-11-17T00:00:00Z",
        updated_at="2026-11-17T00:00:00Z",
        archived_at=snapshot.get("archived_at"),
    )


def _creator(captured: list[Any]):
    async def create(spec):
        captured.append(spec)
        return AgentStartView(
            agent_id="a" * 32, name=spec.name, recovery_mode="created", incarnation_id="inc-1"
        )

    return create


async def test_creates_record_without_configuration() -> None:
    captured: list[Any] = []
    result = await create_agent(
        CreateAgentRequest(template_id="t" * 32, revision=3, name="weekly"),
        templates=_Templates(_SNAPSHOT),
        workspaces=_WORKSPACES,
        available=_GRANTED,
        create=_creator(captured),
        now=_NOW,
    )

    assert result.agent_id == "a" * 32
    assert result.recovery_mode == "created"
    record = result.record
    assert record.agent_id == "a" * 32
    assert record.name == "weekly"
    assert record.presentation.title == "周报助手"
    assert record.presentation.tags == ["writing"]
    assert record.origin is not None
    assert (record.origin.template_id, record.origin.revision) == ("t" * 32, 3)
    dumped = record.model_dump(mode="json")
    for forbidden in ("manifest_ref", "system_prompt", "abilities", "endpoint_name"):
        assert forbidden not in dumped


async def test_reads_the_requested_revision_not_current() -> None:
    templates = _Templates(_SNAPSHOT)
    await create_agent(
        CreateAgentRequest(template_id="t" * 32, revision=7, name="weekly"),
        templates=templates,
        workspaces=_WORKSPACES,
        available=_GRANTED,
        create=_creator([]),
        now=_NOW,
    )
    assert templates.asked == [("t" * 32, 7)]


async def test_missing_revision_is_not_found() -> None:
    with pytest.raises(CreationError) as error:
        await create_agent(
            CreateAgentRequest(template_id="t" * 32, revision=1, name="weekly"),
            templates=_Templates(None),
            workspaces=_WORKSPACES,
            available=_GRANTED,
            create=_creator([]),
        )
    assert error.value.code == "template_not_found"


async def test_archived_template_is_rejected() -> None:
    with pytest.raises(CreationError) as error:
        await create_agent(
            CreateAgentRequest(template_id="t" * 32, revision=1, name="weekly"),
            templates=_Templates({**_SNAPSHOT, "archived_at": "2026-11-17T00:00:00Z"}),
            workspaces=_WORKSPACES,
            available=_GRANTED,
            create=_creator([]),
        )
    assert error.value.code == "template_archived"


async def test_render_errors_become_creation_codes() -> None:
    with pytest.raises(CreationError) as error:
        await create_agent(
            CreateAgentRequest(template_id="t" * 32, revision=1, name="weekly"),
            templates=_Templates({**_SNAPSHOT, "model": {"endpoint_name": None}}),
            workspaces=_WORKSPACES,
            available=_GRANTED,
            create=_creator([]),
        )
    assert error.value.code == "assembly_not_ready"


async def test_core_failure_is_wrapped() -> None:
    async def boom(spec):  # noqa: ANN001, ARG001
        raise RuntimeError("agent_exists")

    with pytest.raises(CreationError) as error:
        await create_agent(
            CreateAgentRequest(template_id="t" * 32, revision=1, name="weekly"),
            templates=_Templates(_SNAPSHOT),
            workspaces=_WORKSPACES,
            available=_GRANTED,
            create=boom,
        )
    assert error.value.code == "agent_create_failed"
    assert "agent_exists" in error.value.detail


async def test_no_granted_members_fails_before_calling_core() -> None:
    """空成员集合在 Subject 侧就拒绝，不把必然失败的规格送进 Core。"""
    captured: list[Any] = []
    with pytest.raises(CreationError) as error:
        await create_agent(
            CreateAgentRequest(template_id="t" * 32, revision=1, name="weekly"),
            templates=_Templates(_SNAPSHOT),
            workspaces=_WORKSPACES,
            available=[],
            create=_creator(captured),
            now=_NOW,
        )
    assert error.value.code == "no_granted_members"
    assert captured == []


async def test_desired_state_is_recorded_as_is() -> None:
    result = await create_agent(
        CreateAgentRequest(
            template_id="t" * 32, revision=1, name="weekly", desired_state="stopped"
        ),
        templates=_Templates(_SNAPSHOT),
        workspaces=_WORKSPACES,
        available=_GRANTED,
        create=_creator([]),
        now=_NOW,
    )
    assert result.record.desired_state == "stopped"


async def test_hung_core_call_becomes_a_timeout_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Core 的 spawn 卡住时必须折成超时错误，不能无限挂住 HTTP 请求。"""
    from ghrah.subject.agents import creation

    monkeypatch.setattr(creation, "CREATE_TIMEOUT_SECONDS", 0.05)

    async def never(spec):  # noqa: ANN001, ARG001
        await asyncio.sleep(10)
        return None

    with pytest.raises(CreationError) as error:
        await create_agent(
            CreateAgentRequest(template_id="t" * 32, revision=1, name="weekly"),
            templates=_Templates(_SNAPSHOT),
            workspaces=_WORKSPACES,
            available=_GRANTED,
            create=never,
        )
    assert error.value.code == "agent_create_timeout"
