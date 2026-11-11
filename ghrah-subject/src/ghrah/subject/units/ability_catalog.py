# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""把内置定义和安装存储收成观察端目录、详情和包组。

不返回安装根或包目录的绝对路径。定义解析失败时保留该版本，并写入 problem。
包组按身份把定义面和关系库对齐，不另写一张状态表。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ghrah.ability.access import AccessError
from ghrah.ability.loading.ability_catalog import load_ability_definitions
from ghrah.ability.loading.ability_schema import AbilityDefinition
from ghrah.ability.package.installer import AbilityInstaller, InstallerError
from ghrah.protocol.ability_control import (
    AbilityInstancesView,
    AbilityInstanceVersionView,
    AbilityInstanceView,
    AbilityPackageCatalogView,
    AbilityPackageDetailView,
    AbilityPackageGroupAgentView,
    AbilityPackageGroupProjectView,
    AbilityPackageGroupsView,
    AbilityPackageGroupVersionView,
    AbilityPackageGroupView,
    AbilityPackageVersionDetail,
    AbilityPackageVersionRow,
    AbilityUnboundVersionView,
    AbilityVerifyView,
)

_IDENTITY = re.compile(r"^[a-z][a-z0-9_-]*$")


class CatalogError(Exception):
    """目录读取失败。``code`` 映射到管理面错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def valid_identity_segment(value: str) -> bool:
    """路径上的 namespace / name 必须符合身份字符集。"""
    return bool(_IDENTITY.fullmatch(value))


def _member_count(definition: AbilityDefinition) -> int:
    return len(definition.operations) + len(definition.skills)


def _definition_from_payload(payload: dict[str, Any]) -> AbilityDefinition | None:
    raw = payload.get("definition")
    if not isinstance(raw, dict):
        return None
    try:
        return AbilityDefinition.model_validate(raw)
    except Exception:
        return None


def _problem(*parts: str | None) -> str | None:
    text = "; ".join(part for part in parts if part)
    return text or None


def _builtin_rows() -> dict[tuple[str, str], AbilityPackageVersionRow]:
    rows: dict[tuple[str, str], AbilityPackageVersionRow] = {}
    for definition in load_ability_definitions().values():
        identity = definition.identity
        key = (identity.full_name, identity.version)
        rows[key] = AbilityPackageVersionRow(
            namespace=identity.namespace,
            name=identity.name,
            full_name=identity.full_name,
            title=definition.title,
            origin="builtin",
            version=identity.version,
            member_count=_member_count(definition),
            lock_state="builtin",
        )
    return rows


def _install_root_available(root: Path) -> bool:
    """安装根是否真实可用：不是链接、是目录、且能枚举。

    只判断「能不能读」，不判断里面有没有包：空目录也算可用。
    符号链接与不可读目录按不可用处理，与 ``InstalledStore`` 的枚举口径一致，
    避免 ``installed_available=true`` 却一个已安装包都列不出来。
    """
    if root.is_symlink() or not root.is_dir():
        return False
    try:
        with os.scandir(root) as entries:
            next(entries, None)
    except OSError:
        return False
    return True


def _installed_rows(
    root: str | None,
) -> tuple[bool, dict[tuple[str, str], AbilityPackageVersionRow]]:
    """返回安装侧是否可用，以及按全名和版本索引的行。"""
    if not root:
        return False, {}
    root_path = Path(root)
    if not _install_root_available(root_path):
        return False, {}
    installer = AbilityInstaller(root_path)
    rows: dict[tuple[str, str], AbilityPackageVersionRow] = {}
    grouped: dict[str, list[Any]] = {}
    for record in installer.list():
        grouped.setdefault(record.full_name, []).append(record)
    for full_name, records in grouped.items():
        shown: list[dict[str, Any]]
        try:
            payload = installer.show(full_name)
        except InstallerError as exc:
            payload = None
            show_problem = f"{exc.code}: {exc.message}"
        else:
            show_problem = None
            shown = payload if isinstance(payload, list) else [payload]
        by_version = {}
        if payload is not None:
            for item in shown:
                if isinstance(item, dict) and isinstance(item.get("version"), str):
                    by_version[item["version"]] = item
        for record in records:
            item = by_version.get(record.version, {})
            definition = _definition_from_payload(item) if item else None
            identity_name = definition.identity.name if definition is not None else record.name
            title = definition.title if definition is not None else None
            count = _member_count(definition) if definition is not None else None
            problem = _problem(
                record.problem,
                None if item else show_problem,
                None if definition is not None or not item else "definition_unreadable",
                item.get("problem") if isinstance(item.get("problem"), str) else None,
            )
            rows[(record.full_name, record.version)] = AbilityPackageVersionRow(
                namespace=record.namespace,
                name=identity_name,
                full_name=record.full_name,
                title=title,
                origin="installed",
                version=record.version,
                member_count=count,
                lock_state=record.lock_state,
                problem=problem,
            )
    return True, rows


def build_package_catalog(install_root: str | None) -> AbilityPackageCatalogView:
    """合并内置树与安装存储。同版本时已安装覆盖内置，并标记 shadowed_builtin。"""
    try:
        builtin = _builtin_rows()
    except Exception as exc:
        raise CatalogError("unavailable", f"内置能力目录不可读：{exc}") from exc
    try:
        available, installed = _installed_rows(install_root)
    except Exception as exc:
        raise CatalogError("unavailable", f"已安装能力目录不可读：{exc}") from exc
    merged = dict(builtin)
    for key, row in installed.items():
        if key in builtin:
            merged[key] = row.model_copy(update={"shadowed_builtin": True})
        else:
            merged[key] = row
    packages = sorted(merged.values(), key=lambda item: (item.full_name, item.version))
    return AbilityPackageCatalogView(installed_available=available, packages=packages)


def _verify_view(installer: AbilityInstaller, full_name: str, version: str) -> AbilityVerifyView:
    result = installer.verify(full_name, version)[0]
    return AbilityVerifyView(
        ok=result.ok,
        code=result.code,
        message=result.message,
        package_digest=result.package_digest,
        actual_digest=result.actual_digest,
        added=list(result.added),
        removed=list(result.removed),
        changed=list(result.changed),
    )


def _detail_from_definition(
    definition: AbilityDefinition,
    *,
    origin: str,
    lock_state: str,
    problem: str | None,
    verify: AbilityVerifyView | None,
    shadowed: bool,
) -> AbilityPackageVersionDetail:
    return AbilityPackageVersionDetail(
        version=definition.identity.version,
        origin=origin,  # type: ignore[arg-type]
        title=definition.title,
        description=definition.description,
        operations=sorted(definition.operations),
        skills=sorted(definition.skills),
        lock_state=lock_state,
        problem=problem,
        verify=verify,
        shadowed_builtin=shadowed,
    )


def build_package_detail(
    install_root: str | None, namespace: str, name: str
) -> AbilityPackageDetailView:
    """读取一个两段身份的包。两边都没有时抛 not_found。"""
    if not valid_identity_segment(namespace) or not valid_identity_segment(name):
        raise CatalogError("invalid_request", "能力包身份不合法")
    full_name = f"{namespace}.{name}"
    versions: list[AbilityPackageVersionDetail] = []
    builtin_versions: dict[str, AbilityDefinition] = {}
    try:
        for definition in load_ability_definitions().values():
            identity = definition.identity
            if identity.namespace == namespace and identity.name == name:
                builtin_versions[identity.version] = definition
    except Exception as exc:
        raise CatalogError("unavailable", f"内置能力目录不可读：{exc}") from exc

    installed_versions: dict[str, tuple[AbilityDefinition | None, str | None, str]] = {}
    installer: AbilityInstaller | None = None
    if install_root:
        installer = AbilityInstaller(Path(install_root))
        try:
            payload = installer.show(full_name)
        except InstallerError as exc:
            if exc.code != "not_installed":
                raise CatalogError("unavailable", exc.message) from exc
            payload = None
        if payload is not None:
            shown = payload if isinstance(payload, list) else [payload]
            for item in shown:
                if not isinstance(item, dict) or not isinstance(item.get("version"), str):
                    continue
                definition = _definition_from_payload(item)
                problem = _problem(
                    item.get("problem") if isinstance(item.get("problem"), str) else None,
                    None if definition is not None else "definition_unreadable",
                )
                lock_state = item.get("lock_state")
                installed_versions[item["version"]] = (
                    definition,
                    problem,
                    lock_state if isinstance(lock_state, str) else "missing",
                )

    version_names = sorted(set(builtin_versions) | set(installed_versions))
    if not version_names:
        raise CatalogError("not_found", f"未找到能力包：{full_name}")
    for version in version_names:
        installed = installed_versions.get(version)
        builtin = builtin_versions.get(version)
        if installed is not None:
            definition, problem, lock_state = installed
            verify = None
            if installer is not None and definition is not None:
                try:
                    verify = _verify_view(installer, full_name, version)
                except InstallerError as exc:
                    verify = AbilityVerifyView(ok=False, code=exc.code, message=exc.message)
            if definition is None:
                versions.append(
                    AbilityPackageVersionDetail(
                        version=version,
                        origin="installed",
                        lock_state=lock_state,
                        problem=problem,
                        shadowed_builtin=builtin is not None,
                    )
                )
                continue
            versions.append(
                _detail_from_definition(
                    definition,
                    origin="installed",
                    lock_state=lock_state,
                    problem=problem,
                    verify=verify,
                    shadowed=builtin is not None,
                )
            )
            continue
        assert builtin is not None
        versions.append(
            _detail_from_definition(
                builtin,
                origin="builtin",
                lock_state="builtin",
                problem=None,
                verify=None,
                shadowed=False,
            )
        )
    return AbilityPackageDetailView(
        namespace=namespace, name=name, full_name=full_name, versions=versions
    )


def _identity_of(definition: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    identity = definition.get("identity")
    if not isinstance(identity, dict):
        return None, None, None
    namespace = identity.get("namespace")
    name = identity.get("name")
    if not isinstance(namespace, str):
        return None, None, None
    full_name = namespace if not isinstance(name, str) or not name else f"{namespace}.{name}"
    title = definition.get("title")
    return full_name, title if isinstance(title, str) else None, None


def _dict_member_count(definition: dict[str, Any]) -> int | None:
    operations = definition.get("operations")
    skills = definition.get("skills")
    if not isinstance(operations, dict) or not isinstance(skills, dict):
        return None
    return len(operations) + len(skills)


async def build_instances_view(store: Any, operator: Any, project_id: str) -> AbilityInstancesView:
    """列出项目已提交的实例和版本，不改当前选择，不返回实例根。"""
    try:
        instance_ids = await store.list_instances(operator)
        stored_versions = await store.list_versions(operator)
    except AccessError as exc:
        raise CatalogError(getattr(exc, "code", None) or "unavailable", "能力关系库拒绝") from exc

    parsed: list[tuple[Any, str | None, str | None, int | None, str | None]] = []
    for version in stored_versions:
        try:
            definition = version.definition()
            parsed.append(
                (
                    version,
                    definition.identity.full_name,
                    definition.title,
                    _member_count(definition),
                    None,
                )
            )
        except Exception as exc:
            parsed.append((version, None, None, None, f"definition_unreadable: {exc}"))

    instances: list[AbilityInstanceView] = []
    claimed: set[str] = set()
    for instance_id in instance_ids:
        try:
            row = await store.instance(instance_id)
        except AccessError as exc:
            instances.append(
                AbilityInstanceView(
                    instance_id=instance_id,
                    revision=0,
                    selected_digest="",
                    problem=getattr(exc, "code", None) or "not_found",
                )
            )
            continue
        definition = row.get("definition") if isinstance(row.get("definition"), dict) else {}
        full_name, title, _ = _identity_of(definition)
        selected = str(row.get("digest") or "")
        revision = int(row.get("revision") or 0)
        matched: list[AbilityInstanceVersionView] = []
        for version, version_name, _title, count, problem in parsed:
            if full_name is None or version_name != full_name:
                continue
            claimed.add(version.digest)
            digest = version.definition_digest if problem is None else None
            matched.append(
                AbilityInstanceVersionView(
                    digest=version.digest,
                    definition_digest=digest,
                    member_count=count,
                    selected=version.digest == selected,
                    problem=problem,
                )
            )
        instances.append(
            AbilityInstanceView(
                instance_id=instance_id,
                revision=revision,
                selected_digest=selected,
                full_name=full_name,
                title=title,
                member_count=_dict_member_count(definition),
                versions=matched,
            )
        )

    unbound = [
        AbilityUnboundVersionView(
            digest=version.digest,
            full_name=version_name,
            definition_digest=version.definition_digest if problem is None else None,
            member_count=count,
            problem=problem,
        )
        for version, version_name, _title, count, problem in parsed
        if version.digest not in claimed
    ]
    return AbilityInstancesView(
        project_id=project_id, instances=instances, unbound_versions=unbound
    )


@dataclass
class _GroupDraft:
    """对齐过程中的一行。只在本模块内使用，不进入响应。"""

    namespace: str | None = None
    name: str | None = None
    full_name: str | None = None
    title: str | None = None
    version: str | None = None
    origin: Literal["builtin", "installed"] | None = None
    lock_state: str | None = None
    problem: str | None = None
    shadowed_builtin: bool = False
    member_count: int | None = None
    digest: str | None = None
    instance_id: str | None = None
    instance_revision: int | None = None
    selected: bool = False


@dataclass
class _StoredVersion:
    """关系库里读到的一个版本，定义解析失败时只留下摘要和问题。"""

    digest: str
    full_name: str | None
    namespace: str | None
    name: str | None
    title: str | None
    version: str | None
    member_count: int | None
    problem: str | None


@dataclass
class _InstanceFact:
    """一个实例的身份、修订和当前选中摘要。"""

    instance_id: str
    revision: int
    selected_digest: str
    full_name: str | None
    title: str | None


def _draft_from_catalog(row: AbilityPackageVersionRow) -> _GroupDraft:
    return _GroupDraft(
        namespace=row.namespace,
        name=row.name,
        full_name=row.full_name,
        title=row.title,
        version=row.version,
        origin=row.origin,
        lock_state=row.lock_state,
        problem=row.problem,
        shadowed_builtin=row.shadowed_builtin,
        member_count=row.member_count,
    )


def _parse_stored_version(version: Any) -> _StoredVersion:
    try:
        definition = version.definition()
    except Exception as exc:
        return _StoredVersion(
            digest=version.digest,
            full_name=None,
            namespace=None,
            name=None,
            title=None,
            version=None,
            member_count=None,
            problem=f"definition_unreadable: {exc}",
        )
    return _StoredVersion(
        digest=version.digest,
        full_name=definition.identity.full_name,
        namespace=definition.identity.namespace,
        name=definition.identity.name,
        title=definition.title,
        version=definition.identity.version,
        member_count=_member_count(definition),
        problem=None,
    )


def _missing_draft(stored: _StoredVersion) -> _GroupDraft:
    """定义面没有这一行时，仍保留已提交版本，并标出问题。"""
    return _GroupDraft(
        namespace=stored.namespace,
        name=stored.name,
        full_name=stored.full_name,
        title=stored.title,
        version=stored.version,
        problem=stored.problem or "definition_missing",
        member_count=stored.member_count,
        digest=stored.digest,
    )


def _group_key(draft: _GroupDraft) -> str:
    if draft.full_name:
        return draft.full_name
    return f"digest:{draft.digest or ''}"


def _catalog_error(exc: AccessError) -> CatalogError:
    code = getattr(exc, "code", None) or "unavailable"
    return CatalogError(code, "能力关系库拒绝")


async def _load_relation(
    store: Any, operator: Any, agent_id: str | None
) -> tuple[list[_StoredVersion], list[_InstanceFact], dict[str, str]]:
    """读取版本、实例，以及这个 Agent 的授予。没有 Agent 行时授予为空。"""
    try:
        stored_versions = await store.list_versions(operator)
        instance_ids = await store.list_instances(operator)
    except AccessError as exc:
        raise _catalog_error(exc) from exc

    stored = [_parse_stored_version(version) for version in stored_versions]
    instances: list[_InstanceFact] = []
    for instance_id in instance_ids:
        try:
            row = await store.instance(instance_id)
        except AccessError as exc:
            if getattr(exc, "code", None) == "not_found":
                continue
            raise _catalog_error(exc) from exc
        full_name = None
        title = None
        try:
            definition = AbilityDefinition.model_validate(row["definition"])
            full_name = definition.identity.full_name
            title = definition.title
        except Exception:
            full_name = None
        instances.append(
            _InstanceFact(
                instance_id=str(row.get("id") or instance_id),
                revision=int(row.get("revision") or 0),
                selected_digest=str(row.get("digest") or ""),
                full_name=full_name,
                title=title,
            )
        )

    aliases: dict[str, str] = {}
    if agent_id is None:
        return stored, instances, aliases
    try:
        grants = await store.grants(operator, agent_id)
    except AccessError as exc:
        if getattr(exc, "code", None) == "not_found":
            return stored, instances, aliases
        raise _catalog_error(exc) from exc
    for grant in sorted(grants, key=lambda item: item.alias):
        aliases.setdefault(grant.instance_id, grant.alias)
    return stored, instances, aliases


def _assign_stored(
    groups: dict[str, list[_GroupDraft]],
    stored: list[_StoredVersion],
    selected_digests: set[str],
) -> None:
    """把已提交版本填进同身份同版本的定义行；对不上的另起一行。"""
    open_rows: dict[tuple[str, str], list[_GroupDraft]] = {}
    for drafts in groups.values():
        for draft in drafts:
            if draft.full_name and draft.version and draft.origin is not None:
                open_rows.setdefault((draft.full_name, draft.version), []).append(draft)

    def _order(item: _StoredVersion) -> tuple[int, str]:
        preferred = 0 if item.digest in selected_digests else 1
        return (preferred, item.digest)

    for item in sorted(stored, key=_order):
        if item.full_name and item.version and item.problem is None:
            matches = open_rows.get((item.full_name, item.version), [])
            if matches:
                matches.pop(0).digest = item.digest
                continue
        draft = _missing_draft(item)
        groups.setdefault(_group_key(draft), []).append(draft)


def _attach_instances(groups: dict[str, list[_GroupDraft]], instances: list[_InstanceFact]) -> None:
    """把实例标识和修订写到同身份的每一行，只把当前选中摘要标成 selected。"""
    for fact in instances:
        matched = groups.get(fact.full_name, []) if fact.full_name else []
        if not matched:
            matched = [
                draft
                for drafts in groups.values()
                for draft in drafts
                if draft.digest and draft.digest == fact.selected_digest
            ]
        if not matched:
            draft = _GroupDraft(
                full_name=fact.full_name,
                title=fact.title,
                digest=fact.selected_digest or None,
                problem="definition_missing" if fact.full_name else "definition_unreadable",
                instance_id=fact.instance_id,
                instance_revision=fact.revision,
                selected=bool(fact.selected_digest),
            )
            groups.setdefault(_group_key(draft), []).append(draft)
            continue
        for draft in matched:
            draft.instance_id = fact.instance_id
            draft.instance_revision = fact.revision
            draft.selected = bool(draft.digest) and draft.digest == fact.selected_digest


def _version_view(
    draft: _GroupDraft, scope: str, aliases: dict[str, str]
) -> AbilityPackageGroupVersionView:
    project = None
    agent = None
    if scope != "distribution":
        project = AbilityPackageGroupProjectView(
            committed=draft.digest is not None,
            digest=draft.digest,
            instance_id=draft.instance_id,
            selected=draft.selected,
            instance_revision=draft.instance_revision,
        )
    if scope == "agent":
        alias = aliases.get(draft.instance_id or "")
        granted = bool(draft.selected and draft.instance_id and alias)
        agent = AbilityPackageGroupAgentView(granted=granted, alias=alias if granted else None)
    return AbilityPackageGroupVersionView(
        version=draft.version,
        origin=draft.origin,
        lock_state=draft.lock_state,
        problem=draft.problem,
        shadowed_builtin=draft.shadowed_builtin,
        member_count=draft.member_count,
        project=project,
        agent=agent,
    )


def _group_view(
    drafts: list[_GroupDraft], scope: str, aliases: dict[str, str]
) -> AbilityPackageGroupView:
    named = next((draft for draft in drafts if draft.namespace), drafts[0])
    title = next((draft.title for draft in drafts if draft.title), None)
    versions = sorted(drafts, key=lambda draft: (draft.version or "", draft.digest or ""))
    return AbilityPackageGroupView(
        namespace=named.namespace,
        name=named.name,
        full_name=named.full_name,
        title=title,
        versions=[_version_view(draft, scope, aliases) for draft in versions],
    )


async def build_package_groups(
    install_root: str | None,
    *,
    store: Any | None = None,
    operator: Any | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
) -> AbilityPackageGroupsView:
    """按包身份对齐定义面和关系库，不写库，也不返回安装根或实例根。

    没有项目时只返回发行版。给出项目时补上是否已提交、实例标识和当前选中摘要。
    再给出 Agent 时补上是否已授予。Agent 还没有关系行时名单照常返回，授予为假。
    """
    if agent_id is not None and project_id is None:
        raise CatalogError("invalid_request", "读取 Agent 包组需要项目")
    if project_id is not None and (store is None or operator is None):
        raise CatalogError("invalid_request", "读取项目包组需要关系库")

    if agent_id is not None:
        scope = "agent"
    elif project_id is not None:
        scope = "project"
    else:
        scope = "distribution"

    catalog = build_package_catalog(install_root)
    groups: dict[str, list[_GroupDraft]] = {}
    for row in catalog.packages:
        draft = _draft_from_catalog(row)
        groups.setdefault(_group_key(draft), []).append(draft)

    aliases: dict[str, str] = {}
    if project_id is not None:
        stored, instances, aliases = await _load_relation(store, operator, agent_id)
        selected = {fact.selected_digest for fact in instances if fact.selected_digest}
        _assign_stored(groups, stored, selected)
        _attach_instances(groups, instances)

    ordered = sorted(groups, key=lambda key: (key.startswith("digest:"), key))
    return AbilityPackageGroupsView(
        scope=scope,
        installed_available=catalog.installed_available,
        project_id=project_id,
        agent_id=agent_id,
        groups=[_group_view(groups[key], scope, aliases) for key in ordered],
    )
