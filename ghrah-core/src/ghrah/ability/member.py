# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""成员记录：项目内**唯一**的成员数据实体。

一个 :class:`Member` 就是一「个」能力成员（operation / skill / mcp），既携带
包身份与装载位置，也携带该成员的定义数据。所有场景（写 ``ability.yaml``、
查询接口、Agent 模板、Agent 创建、上下文注入）在内部一律先转成这个实体再做
操作，只在**写文件 / 写表 / 出接口**的边界上换成目标格式。

**本实体是 Core 的内部数据类，不进入 wire 契约**：对外（``ghrah-protocol``、
Subject、前端）一律是既有的 ``members[]`` / ``packages[]`` / 装配规格形状；
Core 在收到这些数据后自行解析成 :class:`Member`。

字段口径（与 wire 完全一致）：

- ``namespace`` / ``name`` / ``member``：包身份与成员全名，三者必须一致；
  ``member = "<package>.<key>"``，``package`` 在 ``identity.name`` 缺省时退化为
  ``namespace``（此时成员全名只有两段）。冗余是有意的——一条记录必须**自定位**，
  消费方不需要另外知道它属于哪个包。
- ``type``：``operation`` / ``skill`` / ``mcp``，直接决定装载模式。
- ``version``：``external`` 包的定义态必填（SemVer 三段）；引用态可留空（装配规格
  不总带版本）；``kernel``（内置件随源码发布）恒为 ``None``。
- ``ability_type``：``kernel`` / ``external``，决定 ``data.path`` 相对哪个根。
- ``axes``：``visible`` / ``bind`` 两轴；按当前语义二者恒等（不可见即不可执行，
  可见即一定可执行），保留两个字段是因为不同场景读不同名字。
- ``data``：类型专属数据。两种形态共用同一个实体：
  **定义态**（``ability.yaml`` / 版本画像）字段齐全，构造期按 ``type`` 校验必填键
  与未知键；**引用态**（模板勾选 / 授权申请）``data`` 为空，只记录身份与轴。

``data.path`` 是**相对 ability 根**的路径（自带 ``<ns>/<name>[/<version>]/`` 前缀），
拿对应根拼上就能找到文件：``external`` 拼安装根，``kernel`` 拼内置树根
（内置树按包名放置，因此解析时去掉 ``namespace`` 段）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "ABILITY_TYPES",
    "MEMBER_TYPES",
    "AbilityType",
    "Member",
    "MemberAxes",
    "MemberError",
    "MemberType",
]

MemberType = Literal["operation", "skill", "mcp"]
"""成员类型；决定装载模式。"""

AbilityType = Literal["kernel", "external"]
"""所属包类型；决定 ``version`` 语义与 ``data.path`` 的解析根。"""

MEMBER_TYPES: frozenset[str] = frozenset({"operation", "skill", "mcp"})
ABILITY_TYPES: frozenset[str] = frozenset({"kernel", "external"})

_IDENTITY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_OPERATION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SKILL_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")

_ENVELOPE_KEYS: frozenset[str] = frozenset(
    {"namespace", "name", "member", "type", "version", "ability_type", "axes", "data"}
)
_AXES_KEYS: frozenset[str] = frozenset({"visible", "bind"})

_REQUIRED_DATA: dict[str, tuple[str, ...]] = {
    "operation": ("title", "description", "path", "input_schema", "output_schema", "execution"),
    "skill": ("title", "description", "path", "media_type"),
    "mcp": ("title", "description", "server", "tool", "input_schema", "output_schema", "execution"),
}
"""按 ``type`` 的 ``data`` 必填键：缺任一项即拒绝，不做隐式补默认。"""

_ALLOWED_DATA: dict[str, frozenset[str]] = {
    "operation": frozenset(
        {
            "title",
            "description",
            "path",
            "input_schema",
            "output_schema",
            "tags",
            "effects",
            "permissions",
            "resources",
            "execution",
            "expose",
        }
    ),
    "skill": frozenset(
        {
            "title",
            "description",
            "path",
            "media_type",
            "tags",
            "assets",
            "references",
            "scripts",
        }
    ),
    "mcp": frozenset(
        {
            "title",
            "description",
            "server",
            "tool",
            "input_schema",
            "output_schema",
            "tags",
            "effects",
            "permissions",
            "resources",
            "execution",
            "expose",
        }
    ),
}
"""按 ``type`` 的 ``data`` 允许键：未知键一律拒绝（与契约 ``extra="forbid"`` 同口径）。"""


class MemberError(ValueError):
    """成员记录不合法；``message`` 面向作者，可直接展示。"""


@dataclass(frozen=True, slots=True)
class MemberAxes:
    """成员的两条轴：模型可见、绑定为工具。

    两轴描述**授予之后**的状态：

    - ``visible``：能否被发现（目录/搜索可见）；
    - ``bind``：是否绑定进模型上下文（工具 schema 常驻）。

    约束只有一条：``bind=true`` 要求 ``visible=true``（绑定必然可见）。
    ``visible=true, bind=false`` 是合法状态——可发现但未绑定，模型可通过
    ``bind_member`` 显式装上，或直接经 ``execute_tool`` 调用。
    """

    visible: bool = True
    bind: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.visible, bool) or not isinstance(self.bind, bool):
            raise MemberError("axes.visible / axes.bind 必须是 bool")
        if self.bind and not self.visible:
            raise MemberError("axes.bind=true 要求 visible=true（绑定必然可见）")

    @classmethod
    def from_wire(cls, raw: Any) -> MemberAxes:
        """从 wire 形状解析。

        wire 沿用历史字段名：``visible`` 与新语义一致；绑定轴在过渡期由
        ``available`` 承载（见 ``Member.reference``），也接受 ``bind`` 直写。
        """
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise MemberError("axes 必须是映射")
        unknown = set(raw) - _AXES_KEYS - {"available"}
        if unknown:
            raise MemberError(f"axes 未知字段 {sorted(unknown)}")
        bind = raw.get("bind", raw.get("available", True))
        return cls(visible=bool(raw.get("visible", True)), bind=bool(bind))

    def to_wire(self) -> dict[str, bool]:
        """输出 wire 形状。"""
        return {"visible": self.visible, "bind": self.bind}

    def with_bind(self, bind: bool) -> MemberAxes:
        """换绑定轴，返回新轴（绑定要求可见，``visible=false`` 时强制不绑）。"""
        return MemberAxes(visible=self.visible, bind=bool(bind) and self.visible)


@dataclass(frozen=True, slots=True)
class Member:
    """一个成员的完整记录（身份 + 类型 + 轴 + 类型专属数据）。

    Args:
        namespace: 包命名空间（``^[a-z][a-z0-9_-]*$``）。
        name: 包名；等价命名空间时退化槽使用同名。
        member: 成员全名 ``<package>.<key>``。
        type: 成员类型。
        version: 包版本；``kernel`` 恒为 ``None``。
        ability_type: 所属包类型。
        axes: 可见/绑定轴。
        data: 类型专属数据（构造期按 ``type`` 校验必填键）。

    Raises:
        MemberError: 任一跨字段约束不成立。
    """

    namespace: str
    name: str
    member: str
    type: MemberType
    version: str | None = None
    ability_type: AbilityType = "external"
    axes: MemberAxes = field(default_factory=MemberAxes)
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in MEMBER_TYPES:
            raise MemberError(f"member.type 非法: {self.type!r}")
        if self.ability_type not in ABILITY_TYPES:
            raise MemberError(f"member.ability_type 非法: {self.ability_type!r}")
        if not _IDENTITY_PATTERN.fullmatch(self.namespace):
            raise MemberError(f"member.namespace 非法: {self.namespace!r}")
        if not _IDENTITY_PATTERN.fullmatch(self.name):
            raise MemberError(f"member.name 非法: {self.name!r}")
        key = self.key
        pattern = _SKILL_KEY_PATTERN if self.type == "skill" else _OPERATION_KEY_PATTERN
        if not pattern.fullmatch(key):
            raise MemberError(f"成员键非法: {key!r}（member={self.member!r}）")
        package = self.package_full_name
        parts = self.member.split(".")
        if ".".join(parts[:-1]) != package or len(parts) != len(package.split(".")) + 1:
            raise MemberError(
                f"member 必须是 <package>.<key>（package={package!r}），当前是 {self.member!r}"
            )
        if self.ability_type == "kernel":
            if self.version is not None:
                raise MemberError("ability_type=kernel 时 version 必须为 null（内置件没有版本层）")
        elif self.version is not None:
            if not isinstance(self.version, str) or not _SEMVER_PATTERN.fullmatch(self.version):
                raise MemberError(
                    f"ability_type=external 时 version 必须是 SemVer core 三段: {self.version!r}"
                )
        elif self.data:
            raise MemberError("定义态 external 成员必须带 version（引用态记录可留空）")
        if not isinstance(self.axes, MemberAxes):
            raise MemberError("member.axes 必须是 MemberAxes")
        if not isinstance(self.data, Mapping):
            raise MemberError("member.data 必须是映射")
        if not self.data:
            # 引用态（模板勾选 / 授权申请）：只有身份与轴，没有类型专属数据。
            return
        missing = [name for name in _REQUIRED_DATA[self.type] if name not in self.data]
        if missing:
            raise MemberError(f"{self.type} 成员缺少 data 必填键 {missing}")
        unknown = set(self.data) - _ALLOWED_DATA[self.type]
        if unknown:
            raise MemberError(f"{self.type} 成员的 data 含未知键 {sorted(unknown)}")

    # ---------------------------------------------------------------- identity

    @property
    def is_definition(self) -> bool:
        """是否携带完整类型数据（定义态）。

        ``data`` 为空表示**引用态**：只记录「哪个包的哪个成员、什么类型、什么轴」，
        用于模板勾选与授权申请；定义态（ability.yaml / 版本画像）必须字段齐全。
        """
        return bool(self.data)

    @property
    def key(self) -> str:
        """成员键（``member`` 的最后一段）。"""
        return self.member.rsplit(".", 1)[-1]

    @property
    def package_full_name(self) -> str:
        """包全名；``name`` 等于 ``namespace`` 时退化为单段（与安装槽同口径）。"""
        return self.namespace if self.name == self.namespace else f"{self.namespace}.{self.name}"

    def logical_prefix(self) -> str:
        """``data.path`` 的包前缀（相对 ability 根）。

        Returns:
            ``kernel`` 为 ``<ns>/<name>/``；``external`` 为 ``<ns>/<name>/<version>/``。
        """
        if self.ability_type == "kernel":
            return f"{self.namespace}/{self.name}/"
        return f"{self.namespace}/{self.name}/{self.version}/"

    def package_relative_path(self) -> str:
        """把 ``data.path`` 剥成**包内**相对路径。

        Returns:
            包根相对路径，例如 ``operations/render.py``。

        Raises:
            MemberError: ``path`` 不在本成员的包前缀下。
        """
        path = self.data.get("path")
        if not isinstance(path, str) or not path:
            raise MemberError(f"{self.member}: data.path 必须是非空字符串")
        prefix = self.logical_prefix()
        if not path.startswith(prefix):
            raise MemberError(
                f"{self.member}: data.path 必须是以 {prefix!r} 开头的 ability 根相对路径，"
                f"当前是 {path!r}"
            )
        return path[len(prefix) :]

    def with_data_path(self, package_relative: str) -> Member:
        """换算出带包前缀的 ``data.path``，返回新记录。

        Args:
            package_relative: 包内相对路径。

        Returns:
            仅 ``data.path`` 变化的新记录。
        """
        data = dict(self.data)
        data["path"] = f"{self.logical_prefix()}{package_relative}"
        return Member(
            namespace=self.namespace,
            name=self.name,
            member=self.member,
            type=self.type,
            version=self.version,
            ability_type=self.ability_type,
            axes=self.axes,
            data=data,
        )

    def resolve_path(self, *, builtin_root: Path, install_root: Path) -> Path:
        """把 ``data.path`` 解析成磁盘上的绝对路径。

        内置树按包名放置（``<builtin_root>/<name>/…``），因此解析时去掉
        ``namespace`` 段；外部包按 ``<install_root>/<ns>/<name>/<version>/…``。

        Args:
            builtin_root: 内置能力树根。
            install_root: 外部能力安装根。

        Returns:
            成员文件（operation 实现 / skill 正文）的绝对路径。
        """
        relative = self.package_relative_path()
        if self.ability_type == "kernel":
            return builtin_root / self.name / relative
        return install_root / self.namespace / self.name / str(self.version) / relative

    # ------------------------------------------------------------------- wire

    def to_wire(self) -> dict[str, Any]:
        """输出 wire 形状（``ability.yaml`` 列表项 / JSON 记录）。"""
        return {
            "namespace": self.namespace,
            "name": self.name,
            "member": self.member,
            "type": self.type,
            "version": self.version,
            "ability_type": self.ability_type,
            "axes": self.axes.to_wire(),
            "data": dict(self.data),
        }

    @classmethod
    def reference(
        cls,
        *,
        member: str,
        type: MemberType,
        version: str | None = None,
        ability_type: AbilityType = "external",
        visible: bool = True,
        bind: bool = True,
    ) -> Member:
        """从只带身份的入参构造引用态记录（Agent 装配规格 / 授权申请）。

        调用方只给成员全名与类型，包身份由全名切分：``package`` 是最后一段之前
        的部分，``identity.name`` 缺省时包身份退化为 ``namespace``。

        Args:
            member: 成员全名 ``<package>.<key>``。
            type: 成员类型。
            version: 包版本；``kernel`` 必须为 ``None``。
            ability_type: 所属包类型。
            visible: 是否进入可见面。
            bind: 是否绑定为工具；必须与 ``visible`` 一致。

        Returns:
            引用态成员记录（``data`` 为空）。

        Raises:
            MemberError: 全名切不出包身份，或跨字段约束不成立。
        """
        package, _, key = member.rpartition(".")
        namespace, _, name = package.partition(".")
        if not namespace or not key:
            raise MemberError(f"member 必须是 <package>.<key>，当前是 {member!r}")
        return cls(
            namespace=namespace,
            name=name or namespace,
            member=member,
            type=type,
            version=version,
            ability_type=ability_type,
            axes=MemberAxes(visible=visible, bind=bind),
        )

    @classmethod
    def from_wire(cls, raw: Any, *, source: str = "member") -> Member:
        """从 wire 形状解析并校验。

        Args:
            raw: 一条成员记录（映射）。
            source: 报错前缀（通常是包定位串）。

        Returns:
            已校验的成员记录。

        Raises:
            MemberError: 未知字段、缺字段或跨字段约束不成立。
        """
        if not isinstance(raw, Mapping):
            raise MemberError(f"{source}: 成员记录必须是映射")
        unknown = set(raw) - _ENVELOPE_KEYS
        if unknown:
            raise MemberError(f"{source}: 成员未知字段 {sorted(unknown)}")
        missing = [
            name
            for name in ("namespace", "name", "member", "type", "ability_type")
            if name not in raw
        ]
        if missing:
            raise MemberError(f"{source}: 成员缺少必填字段 {missing}")
        data = raw.get("data") or {}
        if not isinstance(data, Mapping):
            raise MemberError(f"{source}: 成员 data 必须是映射")
        return cls(
            namespace=raw["namespace"],
            name=raw["name"],
            member=raw["member"],
            type=raw["type"],
            version=raw.get("version"),
            ability_type=raw["ability_type"],
            axes=MemberAxes.from_wire(raw.get("axes")),
            data=dict(data),
        )
