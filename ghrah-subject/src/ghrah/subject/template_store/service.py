# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""AgentTemplateService：模板读写的业务层。

职责边界：**JSON ↔ 模型校验、组级替换语义、错误映射的前置事实**。持久化
（DDL/CAS/快照事务）在 ``TemplateStore``；HTTP 形状在
``units/agent_template_control``。

更新语义按模板 JSON 对象文档：``metadata`` 的展示字段按子字段 patch，其余
六组整组替换（提供哪个组就用新对象替换整个组，缺省子字段按默认值补），不做
深层合并——避免「删掉一个 operation 却删不掉」的歧义。
"""

from __future__ import annotations

from typing import Any

from ghrah.subject.template_store.models import (
    AgentTemplateConfig,
    AgentTemplateRecord,
)
from ghrah.subject.template_store.store import (
    TemplateNotFoundError,
    TemplateStore,
)

__all__ = ["AgentTemplateService"]

#: 整组替换的六组（metadata 单独按子字段 patch）。
_REPLACE_GROUPS = (
    "model",
    "prompt",
    "context_policy",
    "available_members",
    "permission_request",
    "path_request",
)

#: ``metadata`` 里可按子字段 patch 的展示字段（namespace/name 不可改）。
_METADATA_PATCH_FIELDS = ("title", "description", "tags", "icon", "deprecated")


class AgentTemplateService:
    """模板业务层：构造规范化配置并调用 store。"""

    def __init__(self, store: TemplateStore) -> None:
        self._store = store

    @property
    def store(self) -> TemplateStore:
        """底层 store（诊断与测试用）。"""
        return self._store

    # ─── 写入 ───

    async def create(self, payload: dict[str, Any]) -> AgentTemplateRecord:
        """创建模板：稀疏草稿按默认值补全后落库。

        ``payload`` 是创建请求的 JSON 对象（``metadata`` 必填，其余组可省略
        或为 None）。校验失败抛 pydantic ``ValidationError``。
        """
        source: dict[str, Any] = {"metadata": payload.get("metadata")}
        for group in _REPLACE_GROUPS:
            default: Any = [] if group in {"path_request", "available_members"} else {}
            source[group] = payload.get(group) or default
        config = AgentTemplateConfig.model_validate(source)
        return await self._store.create(config)

    async def update(
        self, template_id: str, expected_revision: int, payload: dict[str, Any]
    ) -> AgentTemplateRecord:
        """按 CAS 更新模板：``metadata`` 按子字段 patch，其余组整组替换。

        只把 ``payload`` 里**出现**的键当作变更（REST 层用
        ``exclude_unset=True`` 保证省略与显式 null 的区分）。

        Raises:
            TemplateNotFoundError: 模板不存在。
            TemplateConflictError: revision 不一致或模板已归档。
        """
        current = await self._store.get(template_id)
        if current is None:
            raise TemplateNotFoundError(template_id)
        source = current.config().document()
        metadata = dict(source["metadata"])
        for field in _METADATA_PATCH_FIELDS:
            if field in payload and payload[field] is not None:
                metadata[field] = payload[field]
        source["metadata"] = metadata
        for group in _REPLACE_GROUPS:
            if group in payload and payload[group] is not None:
                source[group] = payload[group]
        config = AgentTemplateConfig.model_validate(source)
        return await self._store.update(template_id, expected_revision, config)

    async def archive(self, template_id: str, expected_revision: int) -> AgentTemplateRecord:
        """归档模板（只写 ``archived_at``；重复归档幂等）。"""
        return await self._store.archive(template_id, expected_revision)

    # ─── 读取 ───

    async def get(self, template_id: str) -> AgentTemplateRecord:
        """取模板详情（含归档；不存在抛 ``TemplateNotFoundError``）。"""
        record = await self._store.get(template_id)
        if record is None:
            raise TemplateNotFoundError(template_id)
        return record

    async def list(
        self,
        *,
        namespace: str | None = None,
        name: str | None = None,
        tag: str | None = None,
        deprecated: bool | None = None,
        include_archived: bool = False,
        include_history: bool = False,
    ) -> list[AgentTemplateRecord]:
        """列模板（默认只返回每个模板的最新版本，且排除归档）。"""
        return await self._store.list(
            namespace=namespace,
            name=name,
            tag=tag,
            deprecated=deprecated,
            include_archived=include_archived,
            include_history=include_history,
        )

    async def versions(self, template_id: str) -> list[AgentTemplateRecord]:
        """该模板的全部版本（模板不存在时先抛 ``TemplateNotFoundError``）。"""
        await self.get(template_id)
        return await self._store.versions(template_id)

    async def get_version(self, template_id: str, revision: int) -> AgentTemplateRecord:
        """指定 ``(template_id, revision)`` 的模板记录。"""
        record = await self._store.get_version(template_id, revision)
        if record is None:
            raise TemplateNotFoundError(f"{template_id}#{revision}")
        return record
