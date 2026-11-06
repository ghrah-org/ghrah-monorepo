"""最小用户态命名服务：按会话绑定的 Scope 解析，挂载不授予目标权限。"""

from ..context import ProviderContext
from ..provider import ProviderFault, ProviderResult
from .grants import issue_grant
from .identity import StaticIdentityConfig


class NamespaceProvider:
    """冻结身份配置，允许受信管理者显式替换挂载；旧句柄不跟随路径漂移。"""

    def __init__(self, identities: StaticIdentityConfig, administrator: str) -> None:
        identities.bind(administrator, None)
        self.identities = identities
        self.administrator = administrator
        self.mounts: dict[str, dict[str, dict]] = {
            scope.scope_id: {} for scope in identities.scopes
        }

    @staticmethod
    def _path(path: str) -> str:
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or any(part in (".", "..") for part in path.split("/"))
            or "\x00" in path
            or "//" in path
        ):
            raise ValueError("名字必须为不含相对跳转的绝对路径")
        return path.rstrip("/") or "/"

    def _scope(self, context: ProviderContext) -> str:
        root = context.provider_object_id
        if root == "root" and context.principal == self.administrator:
            root = context.resolution_scope
        try:
            self.identities.scope(root, context.principal)
        except ValueError as exc:
            raise ProviderFault("ACCESS_DENIED", str(exc), effect="NO_EFFECT") from exc
        if root != context.resolution_scope:
            raise ProviderFault("ACCESS_DENIED", "会话不能切换解析 Scope", effect="NO_EFFECT")
        return root

    def open(self, context: ProviderContext, path: str) -> dict:
        """使用 Kernel 交付的根对象解析；不接受业务参数选择身份或空间。"""
        scope = self._scope(context)
        path = self._path(path)
        if path == "/":
            return {
                "result": "opened",
                "provider_object_id": scope,
                "actions": ["inspect"],
                "grant": issue_grant(
                    context.principal,
                    f"scope:{scope}:{context.principal}",
                    ("inspect",),
                    self.identities.grant_lifetime,
                ),
            }
        for mount in sorted(self.mounts[scope], key=len, reverse=True):
            if mount == "/" or path == mount or path.startswith(mount + "/"):
                target = self.mounts[scope][mount]
                suffix = path if mount == "/" else path[len(mount) :]
                return {
                    "result": "redirect",
                    "scheme_id": target["scheme_id"],
                    "domain_generation": target["domain_generation"],
                    "path": suffix or "/",
                }
        raise ProviderFault("NOT_FOUND", "当前 Scope 没有该名字", effect="NO_EFFECT")

    def invoke(self, context: ProviderContext, action: str, arguments: dict) -> ProviderResult:
        """管理根可以改挂载，普通主体只查看自己的解析视图。"""
        if action == "inspect":
            scope = self._scope(context)
            return ProviderResult(
                {
                    "scope": scope,
                    "principal": context.principal,
                    "paths": sorted(self.mounts[scope]),
                },
                "NO_EFFECT",
            )
        if context.principal != self.administrator or context.provider_object_id != "root":
            raise ProviderFault("ACCESS_DENIED", "需要受信命名管理根", effect="NO_EFFECT")
        if action not in ("bind", "unbind"):
            raise ProviderFault("UNKNOWN_ACTION", "未知命名动作", effect="NO_EFFECT")
        scope = next(
            (scope for scope in self.identities.scopes if scope.scope_id == arguments["scope"]),
            None,
        )
        if scope is None:
            raise ValueError("未知解析 Scope")
        path = self._path(arguments["path"])
        if action == "unbind":
            removed = self.mounts[scope.scope_id].pop(path, None) is not None
            return ProviderResult(
                {"unbound": removed}, "EFFECT_OCCURRED" if removed else "NO_EFFECT"
            )
        target = arguments["target"]
        namespace = target["namespace_id"]
        if namespace not in scope.namespaces:
            raise ProviderFault("ACCESS_DENIED", "Scope 未配置目标命名空间", effect="NO_EFFECT")
        if (
            not isinstance(target["scheme_id"], str)
            or not target["scheme_id"]
            or (type(target["domain_generation"]) is not int or target["domain_generation"] < 1)
        ):
            raise ValueError("挂载必须钉住方案实例与代次")
        self.mounts[scope.scope_id][path] = {
            "namespace_id": namespace,
            "scheme_id": target["scheme_id"],
            "domain_generation": target["domain_generation"],
        }
        return ProviderResult({"bound": True}, "EFFECT_OCCURRED")
