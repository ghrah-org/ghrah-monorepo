# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""模板路径的端到端：建模板 → 建 Agent → 计数 +1 → 改模板不影响既有实例。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ghrah.protocol.views.core import AgentStartView

from ghrah.subject.project.manager import ProjectManager
from ghrah.subject.project.models import make_project_record
from ghrah.subject.template_store import AgentTemplateConfig, TemplateStore

AGENT_ID = "a" * 32


class _ProjectStore:
    """内存 ProjectStore：只实现建 Agent 路径用到的 get / update。"""

    def __init__(self, record: Any) -> None:
        self.record = record

    async def get(self, project_id: str, include_archived: bool = False) -> Any:
        return self.record if project_id == self.record.project_id else None

    async def update(self, project_id: str, expected_version: int, mutator: Any) -> Any:
        assert expected_version == self.record.version, "版本冲突未被处理"
        updated = mutator(self.record)
        self.record = updated.model_copy(update={"version": updated.version + 1})
        return self.record


class _Handle:
    """假 CoreUnit 句柄：记录每次创建拿到的装配输入。"""

    def __init__(self) -> None:
        self.specs: list[Any] = []

    def __getattr__(self, item: str) -> Any:
        raise AssertionError(f"建 Agent 不应调用 {item}")

    async def create_assembly(self, spec: Any) -> AgentStartView:
        self.specs.append(spec)
        return AgentStartView(
            agent_id=AGENT_ID,
            name=spec.name,
            recovery_mode="created",
            incarnation_id="inc-1",
        )


class _Transport:
    """假 cluster registry：总是返回同一个句柄。"""

    def __init__(self, handle: _Handle) -> None:
        self._handle = handle

    async def ensure_cluster(self, *_args: Any, **_kwargs: Any) -> _Handle:
        return self._handle


def _config(prompt: str, *, name: str = "weekly") -> AgentTemplateConfig:
    return AgentTemplateConfig.model_validate(
        {
            "metadata": {"namespace": "writing", "name": name, "title": "周报助手"},
            "model": {"endpoint_name": "default-llm"},
            "prompt": {"system_prompt": prompt},
            "context_policy": {"max_iterations": 6},
            "available_members": [
                {
                    "namespace": "ghrah",
                    "name": "core",
                    "member": "ghrah.core.load_skill",
                    "type": "operation",
                    "ability_type": "kernel",
                }
            ],
            "permission_request": {},
            "path_request": [],
        }
    )


@pytest.fixture
async def wired(tmp_path: Path) -> tuple[ProjectManager, TemplateStore, _ProjectStore, _Handle]:
    """装配一个内存项目 + 真模板库的 ProjectManager。"""
    templates = TemplateStore(tmp_path / "subject.db")
    await templates.start()
    project = make_project_record(name="demo", project_root_locator="file:///tmp/demo")
    store = _ProjectStore(project)
    handle = _Handle()
    manager = ProjectManager(
        store=store,  # type: ignore[arg-type]
        workspace_mgr=None,  # type: ignore[arg-type]
        task_mgr=None,  # type: ignore[arg-type]
        cluster_registry=_Transport(handle),  # type: ignore[arg-type]
        template_store=lambda: templates,
    )
    try:
        yield manager, templates, store, handle
    finally:
        await templates.stop()


def _spec(template_id: str, revision: int, *, name: str = "weekly") -> Any:
    from ghrah.protocol.payloads.project import AgentCreateSpecSchema

    return AgentCreateSpecSchema(
        name=name, template_id=template_id, revision=revision, desired_state="running"
    )


async def test_instantiation_pins_version_and_counts(
    wired: tuple[ProjectManager, TemplateStore, _ProjectStore, _Handle],
) -> None:
    manager, templates, store, handle = wired
    template = await templates.create(_config("先列事实"))
    project_id = store.record.project_id

    result = await manager.create_agent(project_id, _spec(template.template_id, 1))

    assert result["success"] is True, result
    record = store.record.agents[0]
    assert record.name == "weekly"
    # 记录只钉模板身份与版本，不复制配置
    assert record.origin is not None
    assert (record.origin.template_id, record.origin.revision) == (template.template_id, 1)
    # 装配输入来自模板
    assert handle.specs[0].config.prompt.system_prompt == "先列事实"
    # 该版本计数 +1
    version = await templates.get_version(template.template_id, 1)
    assert version is not None and version.reference_count == 1


async def test_template_update_does_not_touch_existing_agent(
    wired: tuple[ProjectManager, TemplateStore, _ProjectStore, _Handle],
) -> None:
    manager, templates, store, handle = wired
    template = await templates.create(_config("先列事实"))
    project_id = store.record.project_id
    await manager.create_agent(project_id, _spec(template.template_id, 1))

    # 改模板 → 新版本
    await templates.update(template.template_id, 1, _config("改成结构化周报"))
    # 列表默认只返回最新版本
    latest_list = await templates.list()
    assert [(item.template_id, item.revision) for item in latest_list] == [
        (template.template_id, 2)
    ]

    # 既有 Agent 记录不变，仍指向 revision 1
    assert store.record.agents[0].origin.revision == 1
    # 老版本仍是 revision 1 的内容，计数保持 1
    old = await templates.get_version(template.template_id, 1)
    assert old is not None and old.prompt.system_prompt == "先列事实"
    assert old.reference_count == 1

    # 用新版本再建一个 Agent：拿到新内容，且只给新行 +1
    result = await manager.create_agent(
        project_id,
        _spec(template.template_id, 2, name="weekly_v2"),
        expected_version=store.record.version,
    )
    assert result["success"] is True, result
    assert handle.specs[-1].config.prompt.system_prompt == "改成结构化周报"
    new = await templates.get_version(template.template_id, 2)
    assert new is not None and new.reference_count == 1
    old = await templates.get_version(template.template_id, 1)
    assert old is not None and old.reference_count == 1


async def test_missing_template_is_refused_without_touching_project(
    wired: tuple[ProjectManager, TemplateStore, _ProjectStore, _Handle],
) -> None:
    manager, _, store, handle = wired
    project_id = store.record.project_id
    before = store.record.version

    with pytest.raises(Exception) as error:  # noqa: PT011 - StableError 由上层映射
        await manager.create_agent(project_id, _spec("f" * 32, 1))

    assert "template_not_found" in str(error.value)
    assert store.record.version == before
    assert store.record.agents == []
    assert handle.specs == []
