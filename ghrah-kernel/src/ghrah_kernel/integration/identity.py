"""冻结的静态主体、组、资源归属与服务入口许可；不管理 Ability 成员。"""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256

from ..context import CallerBinding
from .grants import GrantLifetime, issue_grant


def _nonempty(*values: str) -> None:
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("身份与资源引用必须为非空字符串")


@dataclass(frozen=True, slots=True)
class Subject:
    """稳定主体：签发域与 ID 唯一标识，种类不产生权限。"""

    issuer: str
    subject_id: str
    kind: str

    def __post_init__(self) -> None:
        _nonempty(self.issuer, self.subject_id)
        if self.kind not in ("user", "agent", "service"):
            raise ValueError("主体种类必须为 user、agent 或 service")

    @property
    def ref(self) -> str:
        """导出不混淆签发域和短 ID 的稳定引用。"""
        return json.dumps([self.issuer, self.subject_id], separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class Group:
    """冻结的直接成员集合；本轮不支持在线移组或嵌套组。"""

    group_id: str
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResourceNamespace:
    """资源的管理归属，owner 不隐含资源调用权。"""

    namespace_id: str
    owner: str


@dataclass(frozen=True, slots=True)
class ResolutionScope:
    """可授予指定主体的解析根；可解析不等于可调用。"""

    scope_id: str
    namespaces: tuple[str, ...]
    subjects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServiceAccessRule:
    """某主体或组对指定命名空间内服务资源的显式动作许可。"""

    rule_id: str
    namespace_id: str
    resource: str
    actions: tuple[str, ...]
    subject: str | None = None
    group: str | None = None


@dataclass(frozen=True, slots=True)
class StaticIdentityConfig:
    """唯一静态配置作者；不可在线修改，不提供账号或动态授权接口。"""

    subjects: tuple[Subject, ...]
    groups: tuple[Group, ...]
    namespaces: tuple[ResourceNamespace, ...]
    scopes: tuple[ResolutionScope, ...]
    grant_lifetime: GrantLifetime
    access: tuple[ServiceAccessRule, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.grant_lifetime, GrantLifetime):
            raise ValueError("必须显式提供许可期限策略")
        collections = (
            (self.subjects, Subject, "ref"),
            (self.groups, Group, "group_id"),
            (self.namespaces, ResourceNamespace, "namespace_id"),
            (self.scopes, ResolutionScope, "scope_id"),
            (self.access, ServiceAccessRule, "rule_id"),
        )
        for values, cls, key in collections:
            if not isinstance(values, tuple) or not all(isinstance(x, cls) for x in values):
                raise ValueError("身份配置必须使用不可变记录元组")
            ids = [getattr(x, key) for x in values]
            _nonempty(*ids)
            if len(ids) != len(set(ids)):
                raise ValueError("身份配置中的引用不能重复")
        subjects = {x.ref for x in self.subjects}
        groups = {x.group_id for x in self.groups}
        namespaces = {x.namespace_id for x in self.namespaces}
        for group in self.groups:
            self._references(group.members, subjects)
        for namespace in self.namespaces:
            self._references((namespace.owner,), subjects)
        for scope in self.scopes:
            self._references(scope.subjects, subjects)
            self._references(scope.namespaces, namespaces)
            if scope.scope_id == "root":
                raise ValueError("root 保留给受信管理根")
        for rule in self.access:
            _nonempty(rule.resource)
            self._references((rule.namespace_id,), namespaces)
            self._references(rule.actions, set(rule.actions))
            if not rule.actions or (rule.subject is None) == (rule.group is None):
                raise ValueError("服务许可必须有动作，且只指向主体或组之一")
            self._references(
                (rule.subject,) if rule.subject is not None else (rule.group,),
                subjects if rule.subject is not None else groups,
            )

    @staticmethod
    def _references(values: tuple[str, ...], known: set[str]) -> None:
        if not isinstance(values, tuple):
            raise ValueError("配置引用集合必须为元组")
        _nonempty(*values)
        if len(set(values)) != len(values) or not set(values) <= known:
            raise ValueError("配置含重复或未知引用")

    def bind(
        self, principal: str, scope: str | None, *, diagnostics: bool = False
    ) -> CallerBinding:
        """受信装配核定连接身份与根；不接收客户端自报组或空间。"""
        if principal not in {x.ref for x in self.subjects}:
            raise ValueError("未知主体")
        if scope is not None:
            self.scope(scope, principal)
        return CallerBinding(principal, scope, diagnostics)

    def scope(self, scope_id: str, principal: str) -> ResolutionScope:
        """核验指定主体是否获准使用解析根。"""
        scope = next((x for x in self.scopes if x.scope_id == scope_id), None)
        if scope is None or principal not in scope.subjects:
            raise ValueError("主体未获准使用该解析 Scope")
        return scope

    def grant(self, principal: str, namespace: str, resource: str) -> dict:
        """给受信服务适配器生成入口许可，不因组成员或 owner 身份自动放行。"""
        memberships = {x.group_id for x in self.groups if principal in x.members}
        rules = [
            rule
            for rule in self.access
            if rule.namespace_id == namespace
            and rule.resource == resource
            and (rule.subject == principal or rule.group in memberships)
        ]
        if not rules:
            raise ValueError("主体没有该服务资源的显式许可")
        actions = sorted({action for rule in rules for action in rule.actions})
        basis = json.dumps([principal, namespace, resource, sorted(x.rule_id for x in rules)])
        return issue_grant(
            principal, sha256(basis.encode()).hexdigest(), tuple(actions), self.grant_lifetime
        )

    def to_dict(self) -> dict:
        """导出固定配置供受管服务读取。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> StaticIdentityConfig:
        """从受信配置重建并检查引用；未知字段直接拒绝。"""
        if set(data) != {"subjects", "groups", "namespaces", "scopes", "grant_lifetime", "access"}:
            raise ValueError("静态身份配置字段不完整或含未知字段")
        return cls(
            tuple(Subject(**x) for x in data["subjects"]),
            tuple(Group(**{**x, "members": tuple(x["members"])}) for x in data["groups"]),
            tuple(ResourceNamespace(**x) for x in data["namespaces"]),
            tuple(
                ResolutionScope(
                    **{**x, "namespaces": tuple(x["namespaces"]), "subjects": tuple(x["subjects"])}
                )
                for x in data["scopes"]
            ),
            GrantLifetime(**data["grant_lifetime"]),
            tuple(
                ServiceAccessRule(**{**x, "actions": tuple(x["actions"])}) for x in data["access"]
            ),
        )
