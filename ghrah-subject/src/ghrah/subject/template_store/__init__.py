# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 模板持久化与业务层（``agent_templates`` 主题）。

权威边界：``agent_templates`` 表是本主题的唯一权威；旧 ``ManifestStore``
的 YAML 与协议层 ``AgentTemplateSpec``（Core 端口 DTO）都不是，同一模板
事实不得双写。
"""

from __future__ import annotations

from ghrah.subject.template_store.models import (
    AgentTemplateAbilityRequest,
    AgentTemplateConfig,
    AgentTemplateContextOverrides,
    AgentTemplateContextPolicy,
    AgentTemplateMetadata,
    AgentTemplateModel,
    AgentTemplateOperation,
    AgentTemplatePackage,
    AgentTemplatePathRequest,
    AgentTemplatePermissionRequest,
    AgentTemplatePersistenceOverrides,
    AgentTemplatePrompt,
    AgentTemplateRecord,
    AgentTemplateScript,
    AgentTemplateSkill,
    AgentTemplateSurface,
)
from ghrah.subject.template_store.service import AgentTemplateService
from ghrah.subject.template_store.store import (
    TemplateConflictError,
    TemplateIdentityConflictError,
    TemplateNotFoundError,
    TemplateStore,
    TemplateStoreError,
)

__all__ = [
    "AgentTemplateAbilityRequest",
    "AgentTemplateConfig",
    "AgentTemplateContextOverrides",
    "AgentTemplateContextPolicy",
    "AgentTemplateMetadata",
    "AgentTemplateModel",
    "AgentTemplateOperation",
    "AgentTemplatePackage",
    "AgentTemplatePathRequest",
    "AgentTemplatePersistenceOverrides",
    "AgentTemplatePermissionRequest",
    "AgentTemplatePrompt",
    "AgentTemplateRecord",
    "AgentTemplateScript",
    "AgentTemplateService",
    "AgentTemplateSkill",
    "AgentTemplateSurface",
    "TemplateConflictError",
    "TemplateIdentityConflictError",
    "TemplateNotFoundError",
    "TemplateStore",
    "TemplateStoreError",
]
