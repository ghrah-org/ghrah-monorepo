# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""把装配规格里的成员引用补齐成定义态成员实体。

创建 Agent 时 Core 收到的是**引用态**成员（全名 + 类型 + 版本 + 轴），定义数据
（``data``：path / schema / media_type…）不在 wire 里。本模块按成员的来源把定义
读出来，合成定义态 :class:`Member`，供实例快照与上下文注入使用：

- ``external``：按 ``<namespace>/<ability_name>/<version>`` 精确读安装记录
  （``read_installed_version``），不猜"最新版本"；
- ``kernel``：读内置源码树（``load_ability_definitions``），内核件没有版本层。

成员匹配按**成员键**（全名最后一段）：能力定义侧对退化包仍可能输出两段名
（``research.research``），而模板/规格用三段规范名（``research.research.research``）。
"""

from __future__ import annotations

from typing import Any

from ghrah.ability.loading.ability_catalog import load_ability_definitions
from ghrah.ability.loading.ability_schema import definition_from_payload
from ghrah.ability.member import Member, MemberAxes, MemberError
from ghrah.core.agents.assembly import AssemblyError, parse_spec_member
from ghrah.core.read.ability_catalog import CatalogError, read_installed_version

__all__ = ["resolve_member_entities"]


def _definition_members(definition: Any, *, ability_type: str) -> dict[str, Member]:
    """定义 → 成员键索引（同键不同类时，类型也必须匹配）。"""
    result: dict[str, Member] = {}
    for member in definition.to_members(ability_type=ability_type):
        result[member.key] = member
    return result


def _builtin_definitions() -> dict[str, Any]:
    """内置树 → 包全名索引（``ghrah.fs`` → 定义）。"""
    index: dict[str, Any] = {}
    for definition in load_ability_definitions().values():
        namespace = definition.identity.namespace
        name = definition.identity.name or namespace
        package = namespace if name == namespace else f"{namespace}.{name}"
        index[package] = definition
    return index


def _external_definition(install_root: str | None, reference: Member) -> Any:
    """按版本读安装记录并解析成定义；版本必须显式存在。"""
    if reference.version is None:
        raise AssemblyError(f"external 成员缺少版本，无法定位能力定义：{reference.member}")
    # 单层身份包（安装目录 <ns>/<name>/，但 yaml identity 省略 name）在安装记录里
    # 的包名是 namespace 本身；两种候选都试，避免把可用包判成不存在。
    candidates = [f"{reference.namespace}.{reference.name}"]
    if reference.name == reference.namespace:
        candidates.append(reference.namespace)
    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for package in candidates:
        try:
            payload = read_installed_version(install_root, package, reference.version)
        except CatalogError as error:
            last_error = error
            continue
        break
    if payload is None:
        raise AssemblyError(
            f"member not resolvable: {reference.member}: {last_error or 'not installed'}"
        )
    raw = payload.get("definition")
    if not isinstance(raw, dict):
        raise AssemblyError(
            f"member not resolvable: {reference.member}: installed definition missing"
        )
    try:
        return definition_from_payload(raw, source=f"installed {candidates[0]}@{reference.version}")
    except Exception as error:  # noqa: BLE001 — 统一折成装配错误
        raise AssemblyError(f"member not resolvable: {reference.member}: {error}") from error


def _enrich(reference: Member, definition_member: Member) -> Member:
    """引用态 + 定义数据 → 定义态成员（轴以引用为准）。"""
    try:
        return Member(
            namespace=reference.namespace,
            name=reference.name,
            member=reference.member,
            type=definition_member.type,
            version=definition_member.version,
            ability_type=definition_member.ability_type,
            axes=MemberAxes(visible=True, bind=reference.axes.bind),
            data=dict(definition_member.data),
        )
    except MemberError as error:
        raise AssemblyError(f"invalid member entry {reference.member!r}: {error}") from error


def resolve_member_entities(
    items: list[dict[str, Any]], *, install_root: str | None
) -> list[Member]:
    """把规格成员补齐成定义态成员实体。

    Args:
        items: 装配规格或实例快照里的成员数组（引用态）。
        install_root: 外部能力安装根；``None`` 表示没有安装存储。

    Returns:
        与输入同序的定义态成员。

    Raises:
        AssemblyError: 成员无法解析、定义缺失，或跨字段约束不成立。
    """
    builtin: dict[str, Any] | None = None
    resolved: list[Member] = []
    for item in items:
        reference = parse_spec_member(item)
        if bool(item.get("whole_container")):
            # 整包在渲染期已展开；真出现时按包展开成全部成员。
            if builtin is None:
                builtin = _builtin_definitions()
            package = f"{reference.namespace}.{reference.name}"
            definition = builtin.get(package)
            if definition is None:
                raise AssemblyError(f"package not resolvable: {package}")
            for member in definition.to_members(ability_type="kernel"):
                resolved.append(
                    _enrich(
                        parse_spec_member({"member": member.member, "type": member.type}), member
                    )
                )
            continue
        if reference.ability_type == "kernel":
            if builtin is None:
                builtin = _builtin_definitions()
            package = f"{reference.namespace}.{reference.name}"
            definition = builtin.get(package)
            if definition is None:
                raise AssemblyError(
                    f"member not resolvable: {reference.member}: unknown kernel package"
                )
            definition_member = _definition_members(definition, ability_type="kernel").get(
                reference.key
            )
        else:
            definition = _external_definition(install_root, reference)
            definition_member = _definition_members(definition, ability_type="external").get(
                reference.key
            )
        if definition_member is None:
            raise AssemblyError(
                f"member not resolvable: {reference.member}: not declared by the ability definition"
            )
        if definition_member.type != reference.type:
            raise AssemblyError(
                f"member type mismatch for {reference.member}: "
                f"spec={reference.type}, definition={definition_member.type}"
            )
        resolved.append(_enrich(reference, definition_member))
    return resolved
