# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 模板的持久化侧模型、默认值与写入规范化。

权威边界：``agent_templates`` 表是本主题的唯一权威；``ManifestStore`` 的
YAML 与协议层 ``AgentTemplateSpec`` 都不是，同一模板事实不得双写。

模型承担三件事：

1. **默认值**：创建只强制 ``metadata.namespace`` + ``metadata.name``，其余
   字段按 T0 v3.1 与模板 JSON 对象文档补默认值，因此写入后读出的对象永远
   「全字段显式」。
2. **形状校验**：未知字段一律拒绝；``whole_container`` 与成员互斥、
   ``visible=false`` 且 ``available=true``、key 唯一、``path_request`` 的
   scope/package 与相对路径约束都在构造期失败。
3. **写入规范化**：``tags``/``auto_approve``/``safe_extra_commands``/
   ``surface.static`` 去重排序，``packages`` 按 ``package``、``operations``/
   ``skills`` 按 ``key`` 排序，``scripts`` 与 ``safe_extra_sub_commands``
   保持申请顺序，``path_request`` 按五元组排序。

这里刻意**不是**装配输入口径（``AgentAssemblySpec``）：模板允许草稿状态
（空端点、空提示词），装配输入必须全字段可启动。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "NAME_PATTERN",
    "NAMESPACE_PATTERN",
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
    "AgentTemplateSkill",
    "AgentTemplateSnapshotSummary",
    "AgentTemplateSurface",
    "snapshot_digest",
]

#: 命名空间正则沿用旧 Manifest ``AgentMetadata``，保证迁移逐字段可搬。
NAMESPACE_PATTERN = r"^[a-z][a-z0-9_.]*[a-z0-9]$"
#: 模板名正则（旧 Manifest 同形；不含点号）。
NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

#: 修订快照里不出现的服务端字段（由行与列承载）。
_SNAPSHOT_EXCLUDED = ("template_id", "revision", "created_at", "updated_at", "archived_at")


def _sorted_unique(field_name: str, values: list[str]) -> list[str]:
    """校验非空字符串列表并去重排序（规范化：写入前调用）。"""
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} 的元素必须是非空字符串")
    return sorted(set(values))


def _ordered_unique(field_name: str, values: list[str]) -> list[str]:
    """校验非空字符串列表，保持申请顺序（不排序、不去重）。"""
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} 的元素必须是非空字符串")
    return list(values)


def snapshot_digest(snapshot: str) -> str:
    """快照 digest：``sha256:<hex>``，对快照文本的 UTF-8 字节求值。

    快照文本本身即以 ``sort_keys=True`` + 紧凑分隔符写出，因此 digest 与
    「对规范化对象排序序列化后求哈希」等价，无需二次解析。
    """
    import hashlib

    return "sha256:" + hashlib.sha256(snapshot.encode("utf-8")).hexdigest()


class _TemplateModel(BaseModel):
    """模板侧基类：拒绝未知字段、不可变，非 strict（JSON 数值/布尔可归一）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentTemplateMetadata(_TemplateModel):
    """模板元数据（7 字段，旧 Manifest 逐字段保留）。"""

    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    name: str = Field(pattern=NAME_PATTERN)
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    icon: str = ""
    deprecated: bool = False

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: list[str]) -> list[str]:
        return _sorted_unique("tags", value)


class AgentTemplateModel(_TemplateModel):
    """模型引用（端点名，不是凭据；草稿可空）。"""

    endpoint_name: str | None = None
    model_overrides: dict[str, Any] | None = None

    @field_validator("endpoint_name")
    @classmethod
    def _non_empty_endpoint(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("endpoint_name 提供时不得为空串")
        return value


class AgentTemplatePrompt(_TemplateModel):
    """系统提示词（草稿允许空串；套用/启动前由检查拒绝）。"""

    system_prompt: str = ""


class AgentTemplatePersistenceOverrides(_TemplateModel):
    """上下文持久化覆盖（三键，均可空）。"""

    type: str | None = None
    compress: bool | None = None
    auto_persist: bool | None = None


class AgentTemplateContextOverrides(_TemplateModel):
    """``context_policy.overrides``：只允许 ``persistence`` 一组。"""

    persistence: AgentTemplatePersistenceOverrides | None = None


class AgentTemplateContextPolicy(_TemplateModel):
    """上下文策略；``-1`` 表示关闭限制。"""

    max_iterations: int = Field(default=-1, ge=-1)
    communication_timeout: float = Field(default=300, ge=-1)
    window: dict[str, Any] | None = None
    overrides: AgentTemplateContextOverrides | None = None


class AgentTemplateSurface(_TemplateModel):
    """能力可见面：``static`` 去重排序。"""

    mode: Literal["auto", "static", "progressive"] = "auto"
    static: list[str] = Field(default_factory=list)
    max_static: int | None = Field(default=None, ge=1)

    @field_validator("static")
    @classmethod
    def _normalize_static(cls, value: list[str]) -> list[str]:
        return _sorted_unique("surface.static", value)


class AgentTemplateOperation(_TemplateModel):
    """operation 轴申请；``visible=false && available=true`` 非法。"""

    key: str = Field(min_length=1)
    visible: bool = True
    available: bool = True

    @model_validator(mode="after")
    def _check_axes(self) -> AgentTemplateOperation:
        if not self.visible and self.available:
            raise ValueError("visible=false 的 operation 不能同时 available=true")
        return self


class AgentTemplateScript(_TemplateModel):
    """skill 下的脚本申请；``exec`` 才是可执行性。"""

    key: str = Field(min_length=1)
    visible: bool = True
    exec: bool = False


class AgentTemplateSkill(_TemplateModel):
    """skill 申请；skill 没有 ``available``，脚本保持申请顺序。"""

    key: str = Field(min_length=1)
    visible: bool = True
    scripts: list[AgentTemplateScript] = Field(default_factory=list)

    @field_validator("scripts")
    @classmethod
    def _check_scripts(cls, value: list[AgentTemplateScript]) -> list[AgentTemplateScript]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("skills[].scripts 的 key 必须唯一")
        return value


class AgentTemplatePackage(_TemplateModel):
    """包级申请：整包与成员二选一，成员按 key 排序。"""

    package: str = Field(min_length=1)
    origin: Literal["builtin", "installed", "mcp"]
    whole_container: bool = False
    operations: list[AgentTemplateOperation] = Field(default_factory=list)
    skills: list[AgentTemplateSkill] = Field(default_factory=list)

    @field_validator("operations")
    @classmethod
    def _check_operations(cls, value: list[AgentTemplateOperation]) -> list[AgentTemplateOperation]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("operations 的 key 必须唯一")
        return sorted(value, key=lambda item: item.key)

    @field_validator("skills")
    @classmethod
    def _check_skills(cls, value: list[AgentTemplateSkill]) -> list[AgentTemplateSkill]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("skills 的 key 必须唯一")
        return sorted(value, key=lambda item: item.key)

    @model_validator(mode="after")
    def _check_container(self) -> AgentTemplatePackage:
        if self.whole_container and (self.operations or self.skills):
            raise ValueError("whole_container=true 时不能同时申请 operations/skills")
        return self


class AgentTemplateAbilityRequest(_TemplateModel):
    """能力申请：模板层只做形状与一致性校验，不判断成员是否存在。"""

    surface: AgentTemplateSurface = Field(default_factory=AgentTemplateSurface)
    packages: list[AgentTemplatePackage] = Field(default_factory=list)
    mcp_refs: list[str] = Field(default_factory=list)

    @field_validator("packages")
    @classmethod
    def _check_packages(cls, value: list[AgentTemplatePackage]) -> list[AgentTemplatePackage]:
        names = [item.package for item in value]
        if len(names) != len(set(names)):
            raise ValueError("packages 的 package 必须唯一")
        return sorted(value, key=lambda item: item.package)

    @field_validator("mcp_refs")
    @classmethod
    def _check_mcp_refs(cls, value: list[str]) -> list[str]:
        return _ordered_unique("mcp_refs", value)


class AgentTemplatePermissionRequest(_TemplateModel):
    """审批策略覆盖（L4 口径，只能收紧部署策略）。"""

    auto_approve: list[str] = Field(default_factory=list)
    require_approval_by_default: bool = True
    safe_extra_commands: list[str] = Field(default_factory=list)
    safe_extra_sub_commands: list[tuple[str, list[str]]] | None = None

    @field_validator("auto_approve", "safe_extra_commands")
    @classmethod
    def _normalize_commands(cls, value: list[str]) -> list[str]:
        return _sorted_unique("permission_request 列表", value)

    @field_validator("safe_extra_sub_commands")
    @classmethod
    def _check_sub_commands(
        cls, value: list[tuple[str, list[str]]] | None
    ) -> list[tuple[str, list[str]]] | None:
        if value is None:
            return None
        for pair in value:
            if len(pair) != 2:
                raise ValueError("safe_extra_sub_commands 的元素必须是 [命令, [子命令...]]")
            _ordered_unique("safe_extra_sub_commands 命令", [pair[0]])
            _ordered_unique("safe_extra_sub_commands 子命令", pair[1])
        return value


class AgentTemplatePathRequest(_TemplateModel):
    """路径申请（模板层保留申请形状；解析成绝对根是创建期的事）。"""

    scope: Literal["agent", "package"]
    package: str | None = None
    workspace_role: str = Field(min_length=1)
    subpath: str = Field(min_length=1)
    access: Literal["read", "write"]
    deny: bool

    @field_validator("subpath")
    @classmethod
    def _check_subpath(cls, value: str) -> str:
        text = value.replace("\\", "/")
        if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
            raise ValueError("subpath 必须是 workspace 内相对路径")
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if any(part == ".." for part in parts):
            raise ValueError("subpath 不得包含 ..")
        return value

    @model_validator(mode="after")
    def _check_scope(self) -> AgentTemplatePathRequest:
        if self.scope == "package":
            if not self.package:
                raise ValueError("scope=package 时 package 必填")
        elif self.package is not None:
            raise ValueError("scope=agent 时 package 必须为 null")
        return self


class AgentTemplateConfig(_TemplateModel):
    """模板配置对象（metadata + 五组 + path_request）。

    这是写入规范化与修订快照的共同形状：不含 ``template_id``/``revision``/
    时间戳/``archived_at``，那些由行与列承载。
    """

    metadata: AgentTemplateMetadata
    model: AgentTemplateModel = Field(default_factory=AgentTemplateModel)
    prompt: AgentTemplatePrompt = Field(default_factory=AgentTemplatePrompt)
    context_policy: AgentTemplateContextPolicy = Field(default_factory=AgentTemplateContextPolicy)
    ability_request: AgentTemplateAbilityRequest = Field(
        default_factory=AgentTemplateAbilityRequest
    )
    permission_request: AgentTemplatePermissionRequest = Field(
        default_factory=AgentTemplatePermissionRequest
    )
    path_request: list[AgentTemplatePathRequest] = Field(default_factory=list)

    @field_validator("path_request")
    @classmethod
    def _normalize_path_request(
        cls, value: list[AgentTemplatePathRequest]
    ) -> list[AgentTemplatePathRequest]:
        seen: set[tuple[str, str | None, str, str]] = set()
        for item in value:
            key = (item.scope, item.package, item.workspace_role, item.subpath)
            if key in seen:
                raise ValueError("path_request 存在重复条目")
            seen.add(key)
        return sorted(
            value,
            key=lambda item: (
                item.scope,
                item.package or "",
                item.workspace_role,
                item.subpath,
                item.access,
            ),
        )

    def document(self) -> dict[str, Any]:
        """规范化后的配置对象（全字段显式，JSON 可用）。"""
        return self.model_dump(mode="json")

    def snapshot_json(self) -> str:
        """修订快照文本：``sort_keys`` + 紧凑分隔符（digest 的规范形式）。"""
        return json.dumps(
            self.document(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


class AgentTemplateRecord(_TemplateModel):
    """一行模板 + 其配置（读模型；时间戳为 RFC 3339 UTC 文本）。"""

    template_id: str
    revision: int = Field(ge=1)
    metadata: AgentTemplateMetadata
    model: AgentTemplateModel = Field(default_factory=AgentTemplateModel)
    prompt: AgentTemplatePrompt = Field(default_factory=AgentTemplatePrompt)
    context_policy: AgentTemplateContextPolicy = Field(default_factory=AgentTemplateContextPolicy)
    ability_request: AgentTemplateAbilityRequest = Field(
        default_factory=AgentTemplateAbilityRequest
    )
    permission_request: AgentTemplatePermissionRequest = Field(
        default_factory=AgentTemplatePermissionRequest
    )
    path_request: list[AgentTemplatePathRequest] = Field(default_factory=list)
    created_at: str
    updated_at: str
    archived_at: str | None = None

    @property
    def archived(self) -> bool:
        """是否已归档（v0 只归档不删除）。"""
        return self.archived_at is not None

    def config(self) -> AgentTemplateConfig:
        """取本记录的配置对象（快照口径）。"""
        return AgentTemplateConfig(
            metadata=self.metadata,
            model=self.model,
            prompt=self.prompt,
            context_policy=self.context_policy,
            ability_request=self.ability_request,
            permission_request=self.permission_request,
            path_request=self.path_request,
        )

    def snapshot_json(self) -> str:
        """本条记录的修订快照文本。"""
        return self.config().snapshot_json()


@dataclass(frozen=True)
class AgentTemplateSnapshotSummary:
    """修订历史摘要（列表不返回快照正文）。"""

    revision: int
    created_at: str
    digest: str
    size_bytes: int
