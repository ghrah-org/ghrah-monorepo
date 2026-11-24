# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""验证从指定模板版本准备 Core 装配输入。"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.subject.agents.creation import (
    CreationError,
    prepare_agent_assembly,
)
from ghrah.subject.agents.render import GrantedPackage
from ghrah.subject.template_store.models import AgentTemplateConfig, AgentTemplateRecord

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
    "available_members": [
        {
            "namespace": "ghrah",
            "name": "fs",
            "member": "ghrah.fs.read_file",
            "type": "operation",
            "ability_type": "kernel",
        }
    ],
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
        available_members=config.available_members,
        permission_request=config.permission_request,
        path_request=config.path_request,
        created_at="2026-11-17T00:00:00Z",
        updated_at="2026-11-17T00:00:00Z",
        archived_at=snapshot.get("archived_at"),
    )


async def test_prepares_spec_and_metadata_from_requested_revision() -> None:
    templates = _Templates(_SNAPSHOT)
    result = await prepare_agent_assembly(
        template_id="t" * 32,
        revision=7,
        agent_name="weekly",
        templates=templates,
        workspaces=_WORKSPACES,
        available=_GRANTED,
    )

    assert templates.asked == [("t" * 32, 7)]
    assert result.spec.name == "weekly"
    assert result.spec.config is not None
    assert result.spec.config.model.endpoint_name == "default-llm"
    assert [member.member for member in result.spec.members] == ["ghrah.fs.read_file"]
    assert result.metadata.title == "周报助手"
    assert result.metadata.tags == ["writing"]


@pytest.mark.parametrize(
    ("snapshot", "available", "code"),
    [
        (None, _GRANTED, "template_not_found"),
        ({**_SNAPSHOT, "archived_at": "2026-11-17T00:00:00Z"}, _GRANTED, "template_archived"),
        ({**_SNAPSHOT, "model": {"endpoint_name": None}}, _GRANTED, "assembly_not_ready"),
        (_SNAPSHOT, [], "no_granted_members"),
    ],
)
async def test_preparation_errors_keep_stable_codes(
    snapshot: dict[str, Any] | None, available: list[GrantedPackage], code: str
) -> None:
    with pytest.raises(CreationError) as error:
        await prepare_agent_assembly(
            template_id="t" * 32,
            revision=1,
            agent_name="weekly",
            templates=_Templates(snapshot),
            workspaces=_WORKSPACES,
            available=available,
        )
    assert error.value.code == code
