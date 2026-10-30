# SPDX-License-Identifier: Apache-2.0
"""资源树到领域接口的适配，保留真实请求者与运行引用。"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ghrah.protocol.ability_tree import (
    AbilityTreeView,
    BindToolRequest,
    EmptyTreeRequest,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    RuntimeObservation,
    TreeChangeResult,
    UnbindToolRequest,
)
from ghrah.scheme import (
    AccessRequest,
    ActionSpec,
    Admission,
    Authorization,
    CodecArguments,
    ControlPrincipal,
    ErrorCode,
    JsonSchemaCodec,
    Principal,
    ProviderCall,
    PydanticCodec,
    Requester,
    ResourceRef,
    SchemeError,
    SchemeModel,
    SchemeRuntime,
    SchemeSpec,
    Session,
    Target,
)
from pydantic import TypeAdapter, ValidationError

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity, MemberFence
from ghrah.ability.contracts import operation_result_schema
from ghrah.ability.file_backend import FileBackend
from ghrah.ability.versions import loaded_file_implementation

_REQUESTS = {
    "grant": GrantAbilityRequest,
    "revoke": RevokeAbilityRequest,
    "bind_tool": BindToolRequest,
    "unbind_tool": UnbindToolRequest,
}
_AGENT_SPEC = SchemeSpec(
    kind="agent",
    contract="ghrah.agent.ability-tree",
    version="1",
    actions=tuple(
        ActionSpec(
            name,
            mutating=True,
            codec=PydanticCodec(model, TreeChangeResult),
        )
        for name, model in _REQUESTS.items()
    ),
)
_VIEW_SPEC = SchemeSpec(
    kind="view",
    contract="ghrah.view.ability-tree",
    version="1",
    actions=(ActionSpec("read", codec=PydanticCodec(EmptyTreeRequest, AbilityTreeView)),),
)


def _identity(principal: Requester) -> AccessIdentity:
    if isinstance(principal, Principal):
        return AccessIdentity(principal.project_id, principal.agent_id, principal.run_id)
    return AccessIdentity(
        principal.project_id,
        principal.subject_id,
        principal.session_id,
        operator=True,
    )


def _instance_alias(instance: str) -> str:
    return "instance-" + hashlib.sha256(instance.encode()).hexdigest()[:24]


def _fence(target: Target, instance: str) -> MemberFence:
    parts = target.resource
    if len(parts) != 2 or parts[0] != "member-v1":
        raise AccessError("access_denied")
    try:
        fence = TypeAdapter(MemberFence).validate_json(parts[1], strict=True)
    except ValidationError as error:
        raise AccessError("access_denied") from error
    if fence.instance_id != instance:
        raise AccessError("access_denied")
    return fence


def _scheme_error(error: AccessError) -> SchemeError:
    return SchemeError(
        {
            "not_found": ErrorCode.NOT_FOUND,
            "access_denied": ErrorCode.ACCESS_DENIED,
            "conflict": ErrorCode.REQUEST_CONFLICT,
            "invalid_change": ErrorCode.INVALID_ARGUMENT,
            "unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
        }[error.code]
    )


@dataclass(frozen=True)
class _Export:
    provider: str
    revision: int
    spec: SchemeSpec


class _TreeProvider:
    def __init__(
        self,
        store: AbilityAccessStore,
        *,
        view: bool = False,
        project: Callable[[AbilityTreeView], AbilityTreeView] = lambda value: value,
    ) -> None:
        self.store = store
        self.view_only = view
        self.project = project

    async def execute(self, call: ProviderCall) -> SchemeModel:
        assert isinstance(call.arguments, CodecArguments)
        actor = _identity(call.principal)
        agent = call.target.resource[0]
        if self.view_only:
            try:
                snapshot = await self.store.view(actor, agent)
            except AccessError as error:
                raise _scheme_error(error) from error
            return CodecArguments(value=self.project(snapshot).model_dump(mode="json"))
        try:
            model = _REQUESTS[call.action].model_validate(call.arguments.value)
            method = getattr(self.store, call.action)  # 动作已在固定 _REQUESTS 中声明。
            result = TreeChangeResult(view=self.project(await method(actor, agent, model)))
        except AccessError as error:
            result = TreeChangeResult(error=error.code)
        return CodecArguments(value=result.model_dump(mode="json"))


class _FileProvider:
    def __init__(self, store: AbilityAccessStore, instance: str, backend: FileBackend) -> None:
        self.store = store
        self.instance = instance
        self.backend = backend

    async def execute(self, call: ProviderCall) -> SchemeModel:
        assert isinstance(call.arguments, CodecArguments)
        fence = _fence(call.target, self.instance)
        try:
            await self.store.authorize(_identity(call.principal), fence)
        except AccessError as error:
            raise _scheme_error(error) from error
        result = await self.backend.execute(
            _identity(call.principal),
            call.action,
            call.arguments.value,
            call.invocation_id,
        )
        return CodecArguments(
            value={
                "outcome": result.outcome.value,
                "data": result.data,
                "next_action_hint": result.next_action_hint,
            }
        )


class AbilitySchemeHost:
    """显式安装导出与访问会话，不修改生产 Agent 装配或启动外部服务。"""

    def __init__(self, store: AbilityAccessStore) -> None:
        self.store = store
        self.runtime = SchemeRuntime(self)
        self._exports: dict[str, _Export] = {}
        self._install_lock = asyncio.Lock()
        self.host_id = "host-" + uuid4().hex
        self._routes: dict[tuple[Any, str], _Export] = {}
        self._control_scopes: set[Any] = set()
        self.runtime.manager.publish(
            "ability-tree-agent", _AGENT_SPEC, _TreeProvider(store, project=self.project_view)
        )
        self.runtime.manager.publish(
            "ability-tree-view",
            _VIEW_SPEC,
            _TreeProvider(store, view=True, project=self.project_view),
        )

    def _available(self, instance: str, revision: int) -> bool:
        export = self._exports.get(instance)
        states = self.runtime.manager.diagnostics()["providers"]
        return (
            export is not None
            and export.revision == revision
            and isinstance(states, dict)
            and states.get(export.provider) == "ready"
        )

    def project_view(self, view: AbilityTreeView) -> AbilityTreeView:
        """同源关系加上本 host 此刻的观测，领域读取保持 unknown。"""
        abilities = [
            node.model_copy(
                update={
                    "availability": "ready"
                    if self._available(node.instance_id, node.instance_revision)
                    else "unavailable"
                }
            )
            for node in view.abilities
        ]
        return view.model_copy(
            update={
                "abilities": abilities,
                "runtime": RuntimeObservation(
                    host_id=self.host_id,
                    observed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                ),
            }
        )

    async def restore(self, actor: AccessIdentity) -> None:
        """显式从当前选定实例重建导出；路由和会话按消费重新派生。"""
        for instance in await self.store.list_instances(actor):
            await self.install(instance)

    async def install(self, instance: str) -> None:
        """幂等安装确定代次；旧导出收束后才替换，不重新扫描来源。"""
        async with self._install_lock:
            info = await self.store.instance(instance)
            if info["implementation_digest"] != loaded_file_implementation():
                raise ValueError("当前进程不含该实例锁定的后端代码")
            actions = tuple(
                ActionSpec(
                    key,
                    cancellable=False,
                    description=value["description"],
                    codec=JsonSchemaCodec(
                        value["input_schema"], operation_result_schema(value["output_schema"])
                    ),
                )
                for key, value in sorted(info["definition"]["operations"].items())
                if value["expose"]
            )
            if not actions:
                raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE)
            # 契约版本只随动作契约变化；实例版本和根目录由独立 revision 固定。
            draft = SchemeSpec(
                kind="ability", contract="ghrah.ability.files", version="schema", actions=actions
            )
            spec = SchemeSpec(
                kind=draft.kind, contract=draft.contract, version=draft.digest, actions=actions
            )
            provider = "files:" + instance
            previous = self._exports.get(instance)
            if (
                previous is not None
                and previous.revision == info["revision"]
                and previous.spec.digest == spec.digest
                and self._available(instance, info["revision"])
            ):
                return
            if previous is not None:
                report = await self.runtime.manager.withdraw(previous.provider)
                if not report.complete:
                    raise SchemeError(ErrorCode.BUSY)
            self.runtime.manager.publish(
                provider, spec, _FileProvider(self.store, instance, FileBackend(Path(info["root"])))
            )
            self._exports[instance] = _Export(provider, info["revision"], spec)

    def session(self, principal: Requester) -> AbilityTreeSession:
        """消费受信身份；操作员是否管理本项目仍由领域服务检查。"""
        manager = self.runtime.manager
        if principal.scope in self._control_scopes:
            return AbilityTreeSession(self, principal, manager.create_session(principal))
        manager.bind(
            principal.scope,
            "agent",
            "control",
            "ability-tree-agent",
            contract=_AGENT_SPEC.contract,
            contract_version=_AGENT_SPEC.version,
        )
        manager.bind(
            principal.scope,
            "view",
            "control",
            "ability-tree-view",
            contract=_VIEW_SPEC.contract,
            contract_version=_VIEW_SPEC.version,
        )
        self._control_scopes.add(principal.scope)
        return AbilityTreeSession(self, principal, manager.create_session(principal))

    async def authorize(self, request: AccessRequest) -> Authorization:
        """直接消费当前领域关系，不能由 runtime bind 授予资格。"""
        try:
            actor = _identity(request.principal)
            if request.target.kind == "ability":
                instance = next(
                    key
                    for key, export in self._exports.items()
                    if export.provider == request.target.provider_id
                )
                fence = _fence(request.target, instance)
                if not self._available(instance, fence.instance_revision):
                    return Authorization(False)
                if request.actions != frozenset({fence.member}):
                    return Authorization(False)
                basis = await self.store.authorize(actor, fence)
            else:
                if len(request.target.resource) != 1:
                    return Authorization(False)
                agent = request.target.resource[0]
                snapshot = await self.store.view(actor, agent)
                if request.target.kind == "agent":
                    if not request.actions <= _REQUESTS.keys():
                        return Authorization(False)
                    if request.actions & {"grant", "revoke"}:
                        self.store.require_operator(actor)
                elif request.target.kind != "view" or request.actions != frozenset({"read"}):
                    return Authorization(False)
                basis = f"agent:{agent}:{snapshot.run_id}:{snapshot.revision}"
            return Authorization(True, basis)
        except (AccessError, ValueError, StopIteration):
            return Authorization(False)

    async def admit(self, request: AccessRequest) -> Admission:
        """只读成员读取当前关系；变更在领域写事务中再次核对。"""
        authorization = await self.authorize(request)
        if not authorization.allowed:
            return Admission(False)
        if request.target.kind == "ability":
            instance = next(
                key
                for key, export in self._exports.items()
                if export.provider == request.target.provider_id
            )
            try:
                basis, record = await self.store.admit(
                    _identity(request.principal),
                    _fence(request.target, instance),
                    request.arguments or {},
                )
                return Admission(True, basis, record)
            except AccessError:
                return Admission(False)
        return Admission(True, authorization.basis)

    async def close(self) -> Any:
        """收束自己的运行时，不关闭传入领域数据库。"""
        return await self.runtime.manager.close()


class AbilityTreeSession:
    """显式公共资源解析门面；全局实例入口与局部 alias 不混用。"""

    def __init__(self, host: AbilitySchemeHost, principal: Requester, session: Session) -> None:
        self.host = host
        self.principal = principal
        self.session = session

    async def acquire(
        self,
        path: str,
        *,
        actions: frozenset[str],
        global_instance: bool = False,
    ) -> ResourceRef:
        """公开消费入口统一抛 SchemeError，领域 AccessError 在边界转换。"""
        try:
            return await self._acquire(path, actions=actions, global_instance=global_instance)
        except AccessError as error:
            raise _scheme_error(error) from error

    async def _acquire(
        self,
        path: str,
        *,
        actions: frozenset[str],
        global_instance: bool = False,
    ) -> ResourceRef:
        """将公开节点定位为保留原请求者的运行引用。"""
        parts = path.split("/")
        if parts[:2] != ["", "scheme"] or any(
            not re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in parts[2:]
        ):
            raise AccessError("not_found")
        pieces = parts[2:]
        actor = _identity(self.principal)
        if len(pieces) == 2 and pieces[0] in {"agent", "view"}:
            internal = f"/scheme/{pieces[0]}/control/{pieces[1]}"
            return await self.session.acquire(internal, actions=actions)
        tool = None
        if len(pieces) == 4 and pieces[0] == "agent" and pieces[2] == "bound_tools":
            if isinstance(self.principal, ControlPrincipal) or pieces[1] != actor.subject_id:
                raise AccessError("access_denied")
            view = await self.host.store.view(actor, pieces[1])
            selected = next((item for item in view.bound_tools if item.name == pieces[3]), None)
            if selected is None:
                raise AccessError("not_found")
            tool = selected.name
            pieces = ["ability", selected.alias, "operations", selected.member]
        if (
            len(pieces) == 6
            and pieces[0] == "agent"
            and pieces[2] == "ability"
            and pieces[4] == "operations"
        ):
            if isinstance(self.principal, ControlPrincipal) or pieces[1] != actor.subject_id:
                raise AccessError("access_denied")
            pieces = ["ability", pieces[3], "operations", pieces[5]]
        if len(pieces) != 4 or pieces[0] != "ability" or pieces[2] != "operations":
            raise AccessError("not_found")
        fence = await self.host.store.resolve(
            actor,
            pieces[1],
            pieces[3],
            tool=tool,
            global_instance=global_instance,
        )
        return await self._member_reference(fence, actions)

    async def _member_reference(self, fence: MemberFence, actions: frozenset[str]) -> ResourceRef:
        try:
            export = self.host._exports[fence.instance_id]
        except KeyError as error:
            raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE) from error
        if not self.host._available(fence.instance_id, fence.instance_revision):
            raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE)
        provider, spec = export.provider, export.spec
        alias = _instance_alias(fence.instance_id)
        route_key = self.principal.scope, fence.instance_id
        if self.host._routes.get(route_key) != export:
            self.host.runtime.manager.bind(
                self.principal.scope,
                "ability",
                alias,
                provider,
                contract=spec.contract,
                contract_version=spec.version,
                contract_digest=spec.digest,
            )
            self.host._routes[route_key] = export
        resource = TypeAdapter(MemberFence).dump_json(fence).decode()
        return await self.session.acquire(
            f"/scheme/ability/{alias}/member-v1/{resource}",
            actions=actions,
        )

    async def project_tools(self) -> ToolProjection:
        """固定一次模型工具面及其成员引用，旧工具名不会静默改指。"""
        if not isinstance(self.principal, Principal):
            raise SchemeError(ErrorCode.ACCESS_DENIED)
        view = await self.read(self.principal.agent_id)
        references: dict[str, tuple[ResourceRef, str]] = {}
        tools = []
        try:
            for tool in view.bound_tools:
                node = next(item for item in view.abilities if item.alias == tool.alias)
                if node.availability != "ready":
                    continue
                member = next(item for item in node.operations if item.key == tool.member)
                fence = MemberFence(
                    node.instance_id,
                    node.instance_revision,
                    member.key,
                    view.agent_id,
                    view.run_id,
                    node.alias,
                    tool.grant_revision,
                    tool.name,
                    tool.revision,
                )
                reference = await self._member_reference(fence, frozenset({member.key}))
                references[tool.name] = reference, member.key
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": member.description,
                            "parameters": member.input_schema,
                        },
                    }
                )
            return ToolProjection(self, tools, references)
        except BaseException:
            for reference, _ in references.values():
                await self.session.release(reference)
            raise

    async def read(self, agent: str) -> AbilityTreeView:
        """通过 Scheme 读取同源 View，而非直达领域替代公开消费。"""
        result = await self.call(f"/scheme/view/{agent}", "read", {})
        return AbilityTreeView.model_validate(result)

    async def call(
        self,
        path: str,
        action: str,
        arguments: dict[str, Any],
        *,
        request_key: str | None = None,
    ) -> dict[str, Any]:
        """合并获取、提交和观察；观察超时不隐式重试领域动作。"""
        reference = await self.acquire(path, actions=frozenset({action}))
        try:
            invocation = await self.session.submit(
                reference,
                action=action,
                arguments=arguments,
                request_key=request_key,
            )
            try:
                snapshot = await self.session.observe(invocation, wait=1)
                while snapshot.pending:
                    snapshot = await self.session.observe(
                        invocation,
                        wait=1,
                        after_version=snapshot.version,
                    )
                if snapshot.error is not None:
                    raise SchemeError(snapshot.error)
                if snapshot.result is None:
                    raise SchemeError(ErrorCode.ACCESS_DENIED)
                return snapshot.result
            finally:
                await self.session.release(invocation)
        finally:
            await self.session.release(reference)


class ToolProjection:
    """一次模型工具输入及反查表；消费结束后显式释放运行引用。"""

    def __init__(
        self,
        client: AbilityTreeSession,
        tools: list[dict[str, Any]],
        references: dict[str, tuple[ResourceRef, str]],
    ) -> None:
        self.tools = tools
        self._client = client
        self._references = references

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """用固定引用执行模型回调；不拆工具名，不重解析成新目标。"""
        if name not in self._references:
            raise SchemeError(ErrorCode.NOT_FOUND)
        resource, member = self._references[name]
        session = self._client.session
        call = await session.submit(resource, action=member, arguments=arguments)
        try:
            snapshot = await session.observe(call, wait=1)
            while snapshot.pending:
                snapshot = await session.observe(call, wait=1, after_version=snapshot.version)
            if snapshot.error is not None:
                raise SchemeError(snapshot.error)
            if snapshot.result is None:
                raise SchemeError(ErrorCode.ACCESS_DENIED)
            return snapshot.result
        finally:
            await session.release(call)

    async def close(self) -> None:
        """释放本次工具面持有的引用，不更改持久选择。"""
        for reference, _ in self._references.values():
            await self._client.session.release(reference)
        self._references.clear()
