# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""从指定模板版本准备 Core 的 Agent 装配输入。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ghrah.protocol.views.core import AgentAssemblySpec

from ghrah.subject.agents.render import GrantedPackage, RenderError, render_assembly
from ghrah.subject.template_store.models import AgentTemplateMetadata, AgentTemplateRecord

__all__ = [
    "CreationError",
    "PreparedAgentAssembly",
    "TemplateSource",
    "prepare_agent_assembly",
]


class TemplateSource(Protocol):
    """按 ``(template_id, revision)`` 读取模板版本。"""

    async def get_version(self, template_id: str, revision: int) -> AgentTemplateRecord | None:
        """取指定版本；不存在返回 ``None``。"""
        ...


@dataclass(frozen=True)
class PreparedAgentAssembly:
    """Core 装配规格及同一模板版本的展示元数据。"""

    spec: AgentAssemblySpec
    metadata: AgentTemplateMetadata


class CreationError(RuntimeError):
    """模板读取或装配输入准备失败，携带可映射的领域错误码。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


async def prepare_agent_assembly(
    *,
    template_id: str,
    revision: int,
    agent_name: str,
    templates: TemplateSource,
    workspaces: Mapping[str, str],
    available: list[GrantedPackage] | None = None,
    initial_bindings: tuple[str, ...] | None = None,
) -> PreparedAgentAssembly:
    """读取模板快照并渲染装配规格，供调用方提交给 Core。"""
    record = await templates.get_version(template_id, revision)
    if record is None:
        raise CreationError("template_not_found", template_id)
    if record.archived:
        raise CreationError("template_archived", template_id)
    config = record.config()

    try:
        rendered = render_assembly(
            config,
            agent_name=agent_name,
            workspaces=workspaces,
            granted=available,
            initial_bindings=initial_bindings,
        )
    except RenderError as error:
        raise CreationError(error.code, error.detail) from error

    if not rendered.spec.members:
        raise CreationError(
            "no_granted_members",
            ",".join(rendered.report.missing_packages + rendered.report.not_granted),
        )

    return PreparedAgentAssembly(spec=rendered.spec, metadata=config.metadata)
