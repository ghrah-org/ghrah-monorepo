# SPDX-License-Identifier: Apache-2.0
"""受信部署配置到获授 MCP 成员的本地接线；不负责工具发现或安装。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ghrah.ability.access.errors import AccessError
from ghrah.ability.access.identity import validate_agent_id
from ghrah.ability.access.store import AccessIdentity, MemberFence
from ghrah.ability.execution.invocation import MemberReference, OperationCall
from ghrah.mcp.errors import McpConfigError, McpNotActivatedError
from ghrah.mcp.identity import validate_server_name
from ghrah.mcp.result import ToolOutcome
from ghrah.mcp.runtime import McpRuntime
from ghrah.mcp.spec import McpToolSpec

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.types.results import ActionResult


def _validate_reference(reference: MemberReference, *, skill: bool) -> None:
    if not all(
        (reference.instance_id, reference.alias, reference.member, reference.version_digest)
    ):
        raise McpConfigError("MCP 关联必须固定实例、别名、成员与版本摘要")
    if reference.member.startswith("skill/") != skill:
        raise McpConfigError("MCP 关联的成员种类不匹配")


@dataclass(frozen=True)
class McpMemberBinding:
    """一个已提交成员到服务器原始工具名及本地 schema 的受信映射。"""

    reference: MemberReference
    tool: McpToolSpec

    def __post_init__(self) -> None:
        _validate_reference(self.reference, skill=False)


@dataclass(frozen=True)
class McpSkillBinding:
    """适用范围、确定 Skill、agentconf 引用、server 别名和逐成员白名单。"""

    project_id: str
    cluster_id: str
    agent_id: str
    skill: MemberReference
    server_ref: str
    server: str
    members: tuple[McpMemberBinding, ...]

    def __post_init__(self) -> None:
        validate_agent_id(self.agent_id)
        _validate_reference(self.skill, skill=True)
        validate_server_name(self.server)
        if not all((self.project_id, self.cluster_id, self.server_ref, self.members)):
            raise McpConfigError("MCP 关联缺少显式作用域、服务器引用或工具白名单")
        if len({item.tool.name for item in self.members}) != len(self.members):
            raise McpConfigError("MCP 白名单存在重复工具")
        if len({item.reference for item in self.members}) != len(self.members):
            raise McpConfigError("MCP 白名单存在重复成员")


@dataclass(frozen=True)
class McpDeploymentConfig:
    """进程内受信配置；source/connector 只由部署代码注入，不接收模型输入。

    外部 source 由其创建者关闭；没有注入时每个 CoreUnit 自建并最后关闭 source。
    """

    bindings: tuple[McpSkillBinding, ...]
    source: Any = field(default=None, repr=False, compare=False)
    connector: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        keys = [(b.project_id, b.cluster_id, b.agent_id, b.server) for b in self.bindings]
        if len(set(keys)) != len(keys):
            raise McpConfigError("同一 MCP scope 内 server 别名冲突")
        members = [
            (b.project_id, b.cluster_id, b.agent_id, m.reference)
            for b in self.bindings
            for m in b.members
        ]
        if len(set(members)) != len(members):
            raise McpConfigError("同一 MCP scope 内成员映射冲突")


class McpDeployment:
    """CoreUnit 所有的装配者；关系库借用，Agent/run 的运行对象独立持有。"""

    def __init__(self, config: McpDeploymentConfig, store: Any, cluster_id: str) -> None:
        from ghrah.mcp.agentconf_source import AgentconfMcpSource

        self.config = config
        self._bindings = deepcopy(config.bindings)
        self.store = store
        self.cluster_id = cluster_id
        self.source = config.source if config.source is not None else AgentconfMcpSource()
        self._owned_source = config.source is None
        self._agents: set[AgentMcpService] = set()
        self._closed = False

    def for_agent(self, agent_id: str) -> AgentMcpService:
        """为一次装配创建空激活集合，不联网，也不读取凭据。"""
        if self._closed:
            raise McpConfigError("MCP deployment is closed")
        bindings = tuple(
            b
            for b in self._bindings
            if (b.project_id, b.cluster_id, b.agent_id)
            == (self.store.project_id, self.cluster_id, agent_id)
        )
        service = AgentMcpService(self, agent_id, bindings)
        self._agents.add(service)
        return service

    async def close(self) -> None:
        """先关闭全部 Agent 接纳，清理会话后才关闭自己创建的配置源。"""
        self._closed = True
        agents = tuple(self._agents)
        for service in agents:
            service.stop_admission()
        try:
            for service in agents:
                await service.close()
        finally:
            if self._owned_source:
                self.source.close()


@dataclass(frozen=True)
class _Activation:
    binding: McpSkillBinding
    skill: MemberFence
    members: tuple[MemberFence, ...]


class AgentMcpService:
    """绑定项目、集群、稳定主体与 run 的执行端口；调用不接收 scope 参数。"""

    def __init__(
        self, owner: McpDeployment, agent_id: str, bindings: tuple[McpSkillBinding, ...]
    ) -> None:
        self._owner = owner
        self._bindings = bindings
        self._identity = AccessIdentity(owner.store.project_id, agent_id, "")
        self._active: dict[str, _Activation] = {}
        self._closed = False
        self._epoch = 0
        self._runtime = McpRuntime(
            connector=owner.config.connector,
            credentials_for_spec=self._credential,
            headers_for_spec=lambda spec: owner.source.headers_for_spec(spec),
        )

    def _credential(self, spec: Any) -> str | None:
        activation = self._active.get(spec.server)
        if activation is None or self._closed:
            raise McpNotActivatedError("MCP activation ended")
        binding = activation.binding
        current = self._owner.source.connection_spec(
            binding.server_ref,
            skill_full_name=spec.skill_full_name,
            server=binding.server,
            tools=tuple(m.tool for m in binding.members),
        )
        if current != spec:
            raise McpConfigError("MCP connection changed; reload its Skill")
        return self._owner.source.credential_for_spec(spec)

    @property
    def scope(self) -> str:
        """无分隔歧义的受信 scope；显示名称不参与。"""
        return json.dumps(
            [
                self._identity.project_id,
                self._owner.cluster_id,
                self._identity.subject_id,
                self._identity.run_id,
            ],
            separators=(",", ":"),
        )

    def bind_run(self, run: str) -> None:
        """只绑定一次当前运行代次，禁止旧对象借用新 run。"""
        if (
            self._closed
            or not run
            or (self._identity.run_id and self._identity.run_id != run)
            or self._owner.store.current_run(self._identity.subject_id) != run
        ):
            raise McpConfigError("Cannot bind MCP service to another run")
        self._identity = AccessIdentity(self._identity.project_id, self._identity.subject_id, run)

    def stop_admission(self) -> None:
        """同步关闭新调用接纳，随后由生命周期 owner 异步清理。"""
        self._closed = True
        self._epoch += 1

    async def _current(self) -> None:
        if self._closed or not self._identity.run_id:
            raise McpNotActivatedError("MCP service is not active for this run")
        try:
            current = self._owner.store.current_run(self._identity.subject_id)
        except AccessError:
            current = None
        if current != self._identity.run_id:
            await self.close()
            raise McpNotActivatedError("MCP service belongs to an ended run")

    @staticmethod
    def _matches(reference: MemberReference, fence: MemberFence) -> bool:
        return (
            reference.instance_id,
            reference.alias,
            reference.member,
            reference.version_digest,
        ) == (
            fence.instance_id,
            fence.alias,
            fence.member,
            fence.version_digest,
        )

    async def _members(self, binding: McpSkillBinding) -> tuple[MemberFence, ...]:
        store = self._owner.store
        view = await store.view(self._identity, self._identity.subject_id)
        fences = []
        for member in binding.members:
            reference = member.reference
            selected = next(
                (
                    t
                    for t in view.bound_tools
                    if (t.alias, t.member) == (reference.alias, reference.member)
                ),
                None,
            )
            if selected is None:
                raise AccessError("access_denied")
            fence = await store.resolve(
                self._identity, reference.alias, reference.member, tool=selected.name
            )
            if not self._matches(reference, fence):
                raise AccessError("access_denied")
            node = next(n for n in view.abilities if n.alias == reference.alias)
            operation = next(o for o in node.operations if o.key == reference.member)
            if operation.input_schema != member.tool.input_schema:
                raise McpConfigError("MCP 本地 schema 与已提交成员不一致")
            fences.append(fence)
        return tuple(fences)

    async def skill_loaded(self, fence: MemberFence) -> None:
        """仅完整正文成功读取后激活；只读配置，不取凭据或联网。"""
        await self._current()
        await self.refresh()
        epoch = self._epoch
        for binding in self._bindings:
            if not self._matches(binding.skill, fence):
                continue
            members = await self._members(binding)
            await self._owner.store.authorize(self._identity, fence)
            spec = self._owner.source.connection_spec(
                binding.server_ref,
                skill_full_name=f"{fence.alias}/{fence.member}",
                server=binding.server,
                tools=tuple(m.tool for m in binding.members),
            )
            await self._owner.store.authorize_many(self._identity, (fence, *members))
            await self._current()
            if epoch != self._epoch:
                raise McpNotActivatedError("MCP activation was unloaded while loading")
            existing = self._runtime.spec_for(agent_scope=self.scope, server=binding.server)
            if existing is not None and existing != spec:
                await self._runtime.deactivate_server(agent_scope=self.scope, server=binding.server)
                await self._owner.store.authorize_many(self._identity, (fence, *members))
                await self._current()
            await self._runtime.activate(spec, agent_scope=self.scope)
            self._active[binding.server] = _Activation(binding, fence, members)

    async def refresh(self) -> None:
        """撤销、改版或取消选择后先删除接纳记录，再关闭失效连接。"""
        await self._current()
        removed = []
        for server, activation in tuple(self._active.items()):
            try:
                await self._owner.store.authorize_many(
                    self._identity, (activation.skill, *activation.members)
                )
            except AccessError:
                self._active.pop(server, None)
                removed.append(activation)
        for activation in removed:
            await self._runtime.deactivate_server(
                agent_scope=self.scope,
                server=activation.binding.server,
            )

    async def unload_skill(self, alias: str, member: str) -> None:
        """卸载只清理本服务对应 Skill，不能撤销已发生的外部效果。"""
        self._epoch += 1
        removed = [
            a for a in self._active.values() if (a.skill.alias, a.skill.member) == (alias, member)
        ]
        for activation in removed:
            self._active.pop(activation.binding.server, None)
        for activation in removed:
            await self._runtime.deactivate(
                agent_scope=self.scope,
                skill_full_name=f"{alias}/{member}",
            )

    async def invoke(self, call: OperationCall) -> ToolOutcome:
        """按固定成员调用；参数只作为远端 payload，不能切换身份或服务器。"""
        await self.refresh()
        if (call.identity.project_id, call.identity.agent, call.identity.run) != (
            self._identity.project_id,
            self._identity.subject_id,
            self._identity.run_id,
        ):
            raise AccessError("access_denied")
        for activation in tuple(self._active.values()):
            for member, fence in zip(activation.binding.members, activation.members, strict=True):
                if call.member != member.reference:
                    continue
                await self._owner.store.authorize_many(self._identity, (activation.skill, fence))
                await self._current()
                binding = activation.binding
                current = self._owner.source.connection_spec(
                    binding.server_ref,
                    skill_full_name=f"{activation.skill.alias}/{activation.skill.member}",
                    server=binding.server,
                    tools=tuple(m.tool for m in binding.members),
                )
                previous = self._runtime.spec_for(agent_scope=self.scope, server=binding.server)
                if previous is not None and current != previous:
                    old_id, new_id = previous.connection_identity, current.connection_identity
                    if old_id is None or new_id is None or old_id[:3] != new_id[:3]:
                        raise McpConfigError("MCP 服务或凭据关联已更换；请显式重新加载")
                    await self._runtime.deactivate_server(
                        agent_scope=self.scope, server=binding.server
                    )
                    await self._owner.store.authorize_many(
                        self._identity, (activation.skill, fence)
                    )
                    await self._current()
                    await self._runtime.activate(current, agent_scope=self.scope)
                return await self._runtime.call(
                    agent_scope=self.scope,
                    server=activation.binding.server,
                    tool=member.tool.name,
                    arguments=call.arguments,
                )
        raise McpNotActivatedError("MCP member is not activated; load its granted Skill first")

    async def close(self) -> None:
        """关闭本次 Agent/run 的所有激活与连接；配置源和关系库不归本对象所有。"""
        self.stop_admission()
        self._active.clear()
        await self._runtime.close()
        self._owner._agents.discard(self)


async def execute_mcp_member(call: OperationCall, services: ExecutionServices) -> ActionResult:
    """逐成员 Operation 的公共执行适配；失败不向模型回灌配置源或传输异常原文。"""
    from ghrah.ability.execution.execution_services import MCP_RUNTIME
    from ghrah.mcp.errors import McpCredentialMissingError, McpError, McpRequestError
    from ghrah.types.results import ActionOutcome, ActionResult

    service = services.get(MCP_RUNTIME)
    if not isinstance(service, AgentMcpService):
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "MCP service not wired"})
    try:
        result = await service.invoke(call)
    except McpCredentialMissingError:
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "MCP credential missing"})
    except McpRequestError as error:
        data = {"error": "MCP request failed", "uncertain": error.uncertain}
        if error.uncertain:
            data["note"] = "Verify external state before retrying; this write may have taken effect"
        return ActionResult(outcome=ActionOutcome.FAILURE, data=data)
    except AccessError, McpNotActivatedError:
        return ActionResult(
            outcome=ActionOutcome.FAILURE, data={"error": "MCP member not activated"}
        )
    except McpError:
        return ActionResult(
            outcome=ActionOutcome.FAILURE, data={"error": "MCP configuration failed"}
        )
    except Exception:
        return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "MCP transport failed"})
    return ActionResult(
        outcome=ActionOutcome.FAILURE if result.is_error else ActionOutcome.SUCCESS,
        data={"content": result.render_for_model()},
    )
