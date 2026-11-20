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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "NAME_PATTERN",
    "NAMESPACE_PATTERN",
    "AgentTemplateAbilityRequest",
    "AgentTemplateConfig",
    "AgentTemplateContextOverrides",
    "AgentTemplateContextPolicy",
    "AgentTemplateMember",
    "AgentTemplateMetadata",
    "AgentTemplateModel",
    "AgentTemplatePathRequest",
    "AgentTemplatePersistenceOverrides",
    "AgentTemplatePermissionRequest",
    "AgentTemplatePrompt",
    "AgentTemplateRecord",
    "AgentTemplateScript",
    "AgentTemplateSurface",
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


class AgentTemplateScript(_TemplateModel):
    """skill 下的脚本申请；``exec`` 才是可执行性。"""

    key: str = Field(min_length=1)
    visible: bool = True
    exec: bool = False


class AgentTemplateAxes(_TemplateModel):
    """成员两条轴；按现行语义二者恒等（不可见即不可执行，可见即一定可执行）。"""

    visible: bool = True
    bind: bool = True

    @model_validator(mode="after")
    def _check_axes(self) -> AgentTemplateAxes:
        if self.bind != self.visible:
            raise ValueError("bind 必须与 visible 一致（不可见即不可执行，可见即一定可执行）")
        return self


class AgentTemplateMember(_TemplateModel):
    """模板里的一条成员申请（引用态）。

    与 Core 的统一成员实体同形：包身份 + 成员全名 + 类型 + 轴；**不复制定义数据**
    （``data`` 恒为空）。``scripts`` 是模板侧独有的脚本申请，按成员挂在记录上。

    落库的就是这个形状；接口层再按包聚合成 ``packages[]`` 视图。
    """

    namespace: str = Field(min_length=1)
    name: str = Field(min_length=1)
    member: str = Field(min_length=1)
    type: Literal["operation", "skill", "mcp"] = "operation"
    version: str | None = None
    ability_type: Literal["kernel", "external"] = "external"
    axes: AgentTemplateAxes = Field(default_factory=AgentTemplateAxes)
    scripts: list[AgentTemplateScript] = Field(default_factory=list)

    @property
    def package(self) -> str:
        """包全名；``name`` 等于 ``namespace`` 时退化为单段（与 Core 同口径）。"""
        return self.namespace if self.name == self.namespace else f"{self.namespace}.{self.name}"

    @property
    def key(self) -> str:
        """成员键（全名最后一段）。"""
        return self.member.rsplit(".", 1)[-1]

    @model_validator(mode="after")
    def _check_shape(self) -> AgentTemplateMember:
        parts = self.member.split(".")
        if ".".join(parts[:-1]) != self.package:
            raise ValueError(f"member 必须是 {self.package}.<key>，当前是 {self.member!r}")
        if self.ability_type == "kernel":
            if self.version is not None:
                raise ValueError("kernel 成员没有版本层，version 必须为 null")
        elif self.version is not None and not self.version.strip():
            raise ValueError("external 成员的 version 给定时不得为空串")
        if self.type != "skill" and self.scripts:
            raise ValueError("只有 skill 成员可以申请脚本")
        keys = [item.key for item in self.scripts]
        if len(keys) != len(set(keys)):
            raise ValueError("scripts 的 key 必须唯一")
        return self


class AgentTemplateAbilityRequest(_TemplateModel):
    """能力申请：模板层只做形状与一致性校验，不判断成员是否存在。

    **落库形状是成员列表**（``members``）。接口层仍以包级视图 ``packages[]``
    收发；``model_validator(mode="before")`` 在读入时把包级视图展开成成员记录，
    :meth:`to_packages_payload` 在输出时再聚合回去。旧行（存的是 ``packages``）
    因此不需要数据迁移。
    """

    surface: AgentTemplateSurface = Field(default_factory=AgentTemplateSurface)
    members: list[AgentTemplateMember] = Field(default_factory=list)
    mcp_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_packages_view(cls, value: Any) -> Any:
        """兼容两种输入：已落库的 ``members``，或接口层的 ``packages[]``。"""
        if not isinstance(value, dict) or "packages" not in value:
            return value
        data = dict(value)
        packages = data.pop("packages") or []
        members = list(data.get("members") or [])
        seen: set[str] = set()
        for package in packages:
            name = str((package or {}).get("package") or "") if isinstance(package, dict) else ""
            if name and name in seen:
                raise ValueError("packages 的 package 必须唯一")
            seen.add(name)
            members.extend(_members_from_package(package))
        data["members"] = members
        return data

    @field_validator("members")
    @classmethod
    def _check_members(cls, value: list[AgentTemplateMember]) -> list[AgentTemplateMember]:
        names = [item.member for item in value]
        if len(names) != len(set(names)):
            raise ValueError("members 的 member 必须唯一")
        return sorted(value, key=lambda item: item.member)

    @field_validator("mcp_refs")
    @classmethod
    def _check_mcp_refs(cls, value: list[str]) -> list[str]:
        return _ordered_unique("mcp_refs", value)

    def to_packages_payload(self) -> dict[str, Any]:
        """把成员记录聚合回接口层的包级视图（``packages[]``）。

        Returns:
            ``{"surface": {...}, "packages": [...], "mcp_refs": [...]}``。
        """
        grouped: dict[str, list[AgentTemplateMember]] = {}
        for item in self.members:
            grouped.setdefault(item.package, []).append(item)
        packages: list[dict[str, Any]] = []
        for package, items in sorted(grouped.items()):
            first = items[0]
            if first.ability_type == "kernel":
                origin = "builtin"
            elif any(item.type == "mcp" for item in items):
                origin = "mcp"
            else:
                origin = "installed"
            packages.append(
                {
                    "package": package,
                    "origin": origin,
                    "version": first.version,
                    "whole_container": False,
                    "operations": [
                        {"key": item.key, "visible": item.axes.visible, "available": item.axes.bind}
                        for item in items
                        if item.type != "skill"
                    ],
                    "skills": [
                        {
                            "key": item.key,
                            "visible": item.axes.visible,
                            "scripts": [script.model_dump(mode="json") for script in item.scripts],
                        }
                        for item in items
                        if item.type == "skill"
                    ],
                }
            )
        return {
            "surface": self.surface.model_dump(mode="json"),
            "packages": packages,
            "mcp_refs": list(self.mcp_refs),
        }


def _members_from_package(package: Any) -> list[dict[str, Any]]:
    """把一个包级申请展开成成员记录（落库前必展开，不留整包）。

    展开时先构造统一成员实体：跨字段约束（版本层、轴、全名）只由实体判一次，
    落库形状只是它的 wire 投影。
    """
    if not isinstance(package, dict):
        return []
    full_name = str(package.get("package") or "")
    if not full_name:
        return []
    if package.get("whole_container"):
        raise ValueError("整包申请必须先展开成成员再落库（whole_container 不被持久化）")
    namespace, _, name = full_name.partition(".")
    name = name or namespace
    origin = package.get("origin")
    ability_type = "kernel" if origin == "builtin" else "external"
    version = None if ability_type == "kernel" else package.get("version")
    members: list[dict[str, Any]] = []
    for operation in package.get("operations") or []:
        members.append(
            {
                "namespace": namespace,
                "name": name,
                "member": f"{full_name}.{operation['key']}",
                "type": "operation",
                "version": version,
                "ability_type": ability_type,
                "axes": {
                    "visible": bool(operation.get("visible", True)),
                    "bind": bool(operation.get("available", True)),
                },
            }
        )
    for skill in package.get("skills") or []:
        members.append(
            {
                "namespace": namespace,
                "name": name,
                "member": f"{full_name}.{skill['key']}",
                "type": "skill",
                "version": version,
                "ability_type": ability_type,
                "axes": {
                    "visible": bool(skill.get("visible", True)),
                    "bind": bool(skill.get("visible", True)),
                },
                "scripts": skill.get("scripts") or [],
            }
        )
    return members


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


class AgentTemplateRecord(_TemplateModel):
    """一行模板版本 + 其配置（读模型；时间戳为 RFC 3339 UTC 文本）。"""

    template_id: str
    revision: int = Field(ge=1)
    is_latest: int = Field(default=1, ge=0, le=1)
    format: str
    reference_count: int = Field(default=0, ge=0)
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
        """是否已归档（``archived_at`` 非空即软删除）。"""
        return self.archived_at is not None

    @property
    def latest(self) -> bool:
        """是否该模板的最新版本。"""
        return self.is_latest == 1

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
