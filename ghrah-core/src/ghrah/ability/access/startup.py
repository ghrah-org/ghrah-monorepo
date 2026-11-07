# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""启动物化：把一次 spawn 的模板与策略变成可运行工具表。

这是启动核定的生产接线层：解析本次启动涉及的包实例、登记已提交版本、执行
``desired_grants``/``apply_grants``、按唯一命名规则选择工具，再把“获授 ∩ 已选
∩ 可暴露 ∩ 可用”的成员实例化成 Agent 工具。

边界：

- 循环控制成员（``conversation`` / ``end_task``）仍由 Agent 原控制机制提供，
  不进入 Ability 关系库，也不按授权面裁剪；
- 工具 schema 与描述来自**已提交版本的定义面**，不从实现脚本二次读取；
- 内置执行实例由固定类装配，安装包由显式本地 Provider 加载；
- 未配置策略时不授予任何能力；模板只决定“申请哪些成员”，不自行生效。
  已配置迁移或管理员事实的存量 Agent，在未配置策略时按既有事实启动（不扩权）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from importlib import import_module
from os.path import commonpath
from pathlib import Path
from typing import Any

from ghrah.protocol.ability_tree import BindToolRequest, TreeId, TreeKey, UnbindToolRequest
from pydantic import TypeAdapter, ValidationError

from ghrah.ability.access.backends import file_member_paths
from ghrah.ability.access.facts import GrantFacts, stored_facts
from ghrah.ability.access.identity import validate_agent_id
from ghrah.ability.access.policy import GrantPolicy
from ghrah.ability.access.reconcile import (
    GrantInstance,
    GrantReconcileError,
    apply_grants,
    desired_grants,
)
from ghrah.ability.access.store import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.execution.ability_build import instantiate_resolved_abilities
from ghrah.ability.execution.execution_services import ExecutionServices
from ghrah.ability.execution.hooks import Hook, HookResult, HookScope
from ghrah.ability.execution.invocation import (
    InvocationIdentity,
    MemberReference,
    OperationCall,
)
from ghrah.ability.loading.ability_roots import builtin_root
from ghrah.ability.permissions.hitl import HITLRequiredAbility, PermissionDeniedAbility
from ghrah.ability.permissions.scope import resolve_effective_permission
from ghrah.ability.surface.assembly import SurfaceThresholds
from ghrah.ability.surface.naming import META_TOOL_NAMES, assign_tool_names
from ghrah.ability.surface.projection import project_surface
from ghrah.ability.surface.types import SurfaceEntry
from ghrah.ability.versions import AbilityVersion, capture_ability_version
from ghrah.manifest.resolver import ResolvedAbility, ResolvedAgent
from ghrah.types.results import ActionOutcome, ActionResult

__all__ = [
    "CONTROL_HANDLERS",
    "ProjectedAbility",
    "StartupGrantError",
    "StartupGrantMaterializer",
    "StartupMaterialization",
    "StartupToolBinding",
]

logger = logging.getLogger(__name__)

CONTROL_HANDLERS: frozenset[str] = frozenset({"conversation", "end_task"})
"""循环控制成员：不属于能力包授权面，不写入关系库。"""


class StartupGrantError(RuntimeError):
    """启动物化失败；失败即失败，不回退到模板自动授予。"""


class AuthorizedToolTable(list):
    """由授权装配边界产出的工具表。

    builtin 模式下 ``SupervisorActor.spawn_agent`` 只接受这一种表：普通
    ``list`` 意味着调用方在边界之外自行拼装工具，绕过关系库、策略与执行
    接纳。继承 ``list`` 是为了让环境快照注入、``session_info`` 回填等原地
    处理继续可用，同时保留“来自边界”的身份。
    """

    def __init__(self, abilities: Iterable[Any] = (), *, ability_content: Any = None) -> None:
        super().__init__(abilities)
        self.ability_content = ability_content
        self.tool_surface = None
        self._bound_run = ""

    @property
    def bound_run(self) -> str:
        """本次装配绑定的受信 run；空串表示尚未完成注册绑定。"""
        return self._bound_run

    def bind_run(self, run: str) -> None:
        """把注册完成的受信代次固定到表内每个投影工具。"""
        if not run or (self._bound_run and self._bound_run != run):
            raise StartupGrantError("Cannot rebind a tool table from an earlier run")
        for ability in self:
            bind = getattr(ability, "bind_run", None)
            if callable(bind):
                bind(run)
        if self.ability_content is not None:
            self.ability_content.bind_run(run)
        if self.tool_surface is not None:
            self.tool_surface.bind_run(run)
        self._bound_run = run


@dataclass(frozen=True)
class StartupToolBinding:
    """一次启动选定的工具与成员映射。

    Attributes:
        alias: 能力别名。
        member: Operation 键。
        tool_name: 模型可见的工具名。
    """

    alias: str
    member: str
    tool_name: str


@dataclass(frozen=True)
class StartupMaterialization:
    """启动物化的结果。

    Attributes:
        abilities: 可交给 Agent 的工具实例（含循环控制成员）。
        tools: 本次选定的工具与成员映射。
        revision: 核定后的 Agent 关系修订。
        granted: 实际写入过获授变更的 alias。
        revoked: 实际撤销的 alias。
    """

    abilities: AuthorizedToolTable
    tools: tuple[StartupToolBinding, ...]
    revision: int
    granted: tuple[str, ...]
    revoked: tuple[str, ...]

    def bind_run(self, run: str) -> None:
        """把本次运行的受信 run 绑定到所有投影工具上（Agent 注册完成后调用）。"""
        self.abilities.bind_run(run)


class _ProjectedHook(Hook):
    """把执行对象的 Hook 固定到同一实例/成员，避免不同工具的权限相互污染。"""

    def __init__(self, inner: Hook, member: MemberReference) -> None:
        self.inner, self.member = inner, member
        self.scope = HookScope(
            points=inner.scope.points if inner.scope is not None else frozenset({inner.hook_point}),
            target_abilities=frozenset({member.member}),
        )

    async def should_trigger(self, call: OperationCall, services: ExecutionServices) -> bool:
        return call.member == self.member and (
            self.inner.scope is not None or await self.inner.should_trigger(call, services)
        )

    async def execute(
        self, call: OperationCall, services: ExecutionServices, result: ActionResult | None = None
    ) -> HookResult:
        if not await self.should_trigger(call, services):
            return HookResult.continue_()
        return await self.inner.execute(call, services, result)


class ProjectedAbility:
    """把执行实例包装成固定工具名与定义面 schema 的模型工具。

    工具名由表面命名算法分配，可能与成员键不同；执行仍委托给本地装配出的
    实例，调用入口只按这张映射表查成员，不从模型传入的名字反推模块。

    这里同时是**受信身份的装配边界**：调用对象里的成员引用与身份在执行前
    被替换为启动时捕获的事实（实例、别名、成员键、版本摘要、项目与 run），
    调用方（ActorAgent／执行器）无权改写。run 由 :meth:`bind_run` 在 Agent
    注册完成后补齐——绑定之前是空串，接纳层按“旧对象不得借用新 run”拒绝
    不匹配的调用。
    """

    def __init__(
        self,
        inner: Any,
        *,
        tool_name: str,
        tool_schema: dict[str, Any],
        member: str = "",
        member_reference: MemberReference | None = None,
        identity: InvocationIdentity | None = None,
        admission: Any = None,
        current_run: Any = None,
        input_model: Any = None,
    ) -> None:
        self._inner = inner
        self._tool_name = tool_name
        self._tool_schema = tool_schema
        self._member = member
        self._member_reference = member_reference or MemberReference(member=member)
        self._identity = identity or InvocationIdentity()
        self._admission = admission
        self._run_resolver = current_run
        self._input_model = input_model

    @property
    def name(self) -> str:
        """模型可见的工具名。"""
        return self._tool_name

    @property
    def member(self) -> str:
        """该工具固定的成员键；执行器按它解析路径参数，不从工具名反推。"""
        return self._member

    @property
    def member_reference(self) -> MemberReference:
        """启动时固定的成员引用（实例、别名、成员键、版本摘要）。"""
        return self._member_reference

    @property
    def bound_run(self) -> str:
        """装配边界绑定的受信 run；空串表示尚未绑定。"""
        return self._identity.run

    def bind_run(self, run: str) -> None:
        """在 Agent 注册完成后绑定本次运行的受信 run。

        绑定前调用对象里的 run 为空；绑定后接纳层要求当前 run 与它一致，
        旧运行留下的工具对象不能借新 run 继续执行。
        """
        if not run or (self._identity.run and self._identity.run != run):
            raise StartupGrantError("Cannot rebind a tool from an earlier run")
        self._identity = replace(self._identity, run=run)

    @property
    def invocation_identity(self) -> InvocationIdentity:
        """装配者固定的受信身份，供 Agent 级 Hook 读取。"""
        return self._identity

    @property
    def bound_agent(self) -> str:
        """返回装配时固定的稳定主体。"""
        return self._identity.agent

    def prepare_call(self, call: OperationCall) -> OperationCall:
        """在 Hook 之前固定成员和身份；调用参数无权覆盖。"""
        return replace(call, member=self._member_reference, identity=self._identity)

    def check_call(self, call: OperationCall, services: ExecutionServices) -> ActionResult | None:
        """未绑定运行代次或权限硬拒绝时不进入审批。"""
        if self._admission is not None and not self._identity.run:
            return ActionResult(
                outcome=ActionOutcome.FAILURE, data={"error": "Trusted run is not bound"}
            )
        if self._run_resolver is not None:
            try:
                current = self._run_resolver()
            except AccessError:
                current = None
            if not current or current != self._identity.run:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={"error": "Tool belongs to an earlier run of this Agent"},
                )
        check = getattr(self._inner, "check_call", None)
        if self._input_model is not None:
            try:
                self._input_model.model_validate(call.arguments)
            except ValidationError:
                return ActionResult(
                    ActionOutcome.FAILURE,
                    {"error": "Invalid arguments; check describe_tool's input_schema"},
                )
        return check(call, services) if callable(check) else None

    async def execute(self, call: OperationCall, services: ExecutionServices) -> Any:
        """先做执行接纳，再委托给本地执行实例。

        成员引用与受信身份在执行前被替换为装配时捕获的事实；接纳在 HITL
        钩子之后运行：审批只决定“范围外的调用要不要问人”，关系与授予范围的
        复核不能被审批跳过——撤销、重新选择与越界在此最后兜底，失败时没有
        副作用。
        """
        call = self.prepare_call(call)
        denied = self.check_call(call, services)
        if denied is not None:
            return denied
        if self._admission is not None:
            error = await self._admission(call)
            if error is not None:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={"error": error},
                )
        return await self._inner.execute(call, services)

    def bind_tool(self) -> dict[str, Any]:
        """返回来自定义面的固定 tool schema。"""
        return self._tool_schema

    def get_hooks(self) -> list[Any]:
        """沿用执行实例的 Hook，并绑定到实际物化的实例和成员。

        同一 Agent 的其他文件工具不能拿自己的权限检查器审查本成员。
        """
        return [_ProjectedHook(hook, self._member_reference) for hook in self._inner.get_hooks()]

    def get_default_state(self) -> dict[str, Any]:
        """沿用执行实例的默认状态。"""
        return dict(self._inner.get_default_state())

    def __getattr__(self, item: str) -> Any:
        """其余属性（如环境快照读取的 checker）继续指向执行实例。"""
        return getattr(self._inner, item)


@dataclass(frozen=True)
class RegisteredPackage:
    """一次启动捕获并登记后的包实例事实。

    Attributes:
        alias: 包在 Agent 内的能力别名（容器名）。
        package: 包身份全名。
        instance: 启动核定与关系库写入使用的实例事实。
        version: 已提交的包版本，含定义面与实现摘要。
    """

    alias: str
    package: str
    instance: GrantInstance
    version: AbilityVersion


async def register_package_instance(
    *,
    store: AbilityAccessStore,
    actor: AccessIdentity,
    catalog: Any,
    package: str,
    members: set[str],
    workspace_root: str | None,
    source: Any = None,
    filesystem_roots: tuple[str, ...] = (),
) -> RegisteredPackage:
    """捕获包版本并登记实例；启动核定与迁移 bootstrap 共用这一条登记路径。

    模板与迁移只提供身份（包名与成员键）；版本、内容与定义以捕获结果为准，
    之后核定只读这些已提交事实。同一版本重复提交是幂等的。

    Args:
        store: 授权关系库。
        actor: 操作员身份（启动核定用 deployment，迁移用 migration）。
        catalog: ``load_ability_definitions()`` 的目录面。
        package: 包身份全名（``ghrah.fs``）。
        members: 本次需要的成员键集合，用于校验文件类成员需要工作区。
        workspace_root: 工作区根；文件类成员必须显式给出。

    Returns:
        已登记实例与版本。

    Raises:
        StartupGrantError: 包未找到、内置树不可读、文件类成员缺工作区。
    """
    entry = next(
        ((key, item) for key, item in catalog.items() if item.identity.full_name == package),
        None,
    )
    if entry is None:
        raise StartupGrantError(f"未找到能力包：{package}")
    container, container_definition = entry
    if source is not None:
        version = source.capture_package(package)
    else:
        root = builtin_root().path
        if root is None:
            raise StartupGrantError("内置能力树不是可读目录，无法捕获版本")
        package_root = root / container
        version = capture_ability_version(
            (package_root / "ability.yaml").read_text(encoding="utf-8"), root=package_root
        )
    definition = version.definition().model_dump(mode="json")
    file_members = sorted(member for member in members if file_member_paths(definition, member))
    roots = tuple(sorted({str(Path(p).resolve()) for p in filesystem_roots}))
    if any(not Path(p).is_absolute() for p in filesystem_roots):
        raise StartupGrantError("文件沙盒根必须是绝对路径")
    has_file_members = any(file_member_paths(definition, key) for key in definition["operations"])
    if not roots or not has_file_members:
        roots = (str(Path(workspace_root).resolve()),) if workspace_root else ()
    try:
        instance_root = Path(commonpath(roots)) if roots else None
    except ValueError as error:
        raise StartupGrantError("文件沙盒根必须位于同一文件系统盘符") from error
    if not workspace_root and file_members:
        raise StartupGrantError(
            f"包 {package} 的文件类成员需要显式 workspace_root 才能授予：{file_members}"
        )
    await store.commit_version(actor, version)
    instance_id = _instance_id(package)
    expected_revision = 0
    try:
        expected_revision = int((await store.instance(instance_id))["revision"])
    except AccessError:
        expected_revision = 0
    await store.select_instance(
        actor,
        instance_id,
        version.digest,
        instance_root,
        expected_revision=expected_revision,
    )
    alias = TypeAdapter(TreeKey).validate_python(container_definition.container_name)
    instance = GrantInstance(
        alias=alias,
        package=package,
        instance_id=instance_id,
        version_digest=version.digest,
        definition=definition,
        allowed_roots=roots,
    )
    return RegisteredPackage(alias=alias, package=package, instance=instance, version=version)


class StartupGrantMaterializer:
    """按模板与策略物化一次 spawn 的授权与工具表。"""

    def __init__(
        self,
        *,
        store: AbilityAccessStore,
        source: Any,
        actor: AccessIdentity,
        policy: GrantPolicy | None,
        workspace_root: str | None,
        filesystem_roots: tuple[str, ...] = (),
        auto_approve_abilities: tuple[str, ...] = (),
        require_approval_by_default: bool = True,
        command_runner: Any = None,
        safe_extra_commands: tuple[str, ...] = (),
        safe_extra_sub_commands: tuple[tuple[str, tuple[str, ...]], ...] = (),
        mcp_deployment: Any = None,
        surface_thresholds: SurfaceThresholds | None = None,
        surface_search_limit: int = 8,
        surface_loaded_limit: int = 8,
    ) -> None:
        if policy is not None and not isinstance(policy, GrantPolicy):
            raise TypeError("policy 必须是 GrantPolicy 或 None")
        self._store = store
        self._source = source
        self._store.set_version_availability(source.version_available)
        self._actor = actor
        self._policy = policy
        self._workspace_root = workspace_root
        self._filesystem_roots = filesystem_roots
        self._auto_approve_abilities = auto_approve_abilities
        self._require_approval_by_default = require_approval_by_default
        self._command_runner = command_runner
        self._safe_extra_commands = safe_extra_commands
        self._safe_extra_sub_commands = safe_extra_sub_commands
        self._mcp_deployment = mcp_deployment
        self._content_services: dict[str, list[Any]] = {}
        self._surface_services: dict[str, list[Any]] = {}
        self._surface_thresholds = surface_thresholds
        self._surface_search_limit = surface_search_limit
        self._surface_loaded_limit = surface_loaded_limit

    def registration(self, agent: str) -> Any:
        """返回从授权核定到运行绑定的库级独占范围。"""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def reserved() -> Any:
            async with self._store.startup_registration(self._actor, agent) as registration:
                try:
                    yield registration
                except BaseException:
                    await self.close_agent_resources(agent)
                    raise

        return reserved()

    async def close_agent_resources(self, agent: str) -> None:
        """先停止本次主体资源接纳，再释放其会话；不关闭共享配置源或关系库。"""
        for surface in self._surface_services.pop(agent, []):
            await surface.close()
        providers = self._content_services.pop(agent, [])
        services = [p.mcp_service for p in providers if p.mcp_service is not None]
        for service in services:
            service.stop_admission()
        for service in services:
            await service.close()

    def resolve_agent(self, manifest: Any, *, runtime_name: str | None = None) -> ResolvedAgent:
        """使用同一包来源解析子 Agent，保留显式安装根与已选版本。"""
        return self._source.resolver().resolve(manifest, runtime_name=runtime_name)

    async def release_registration(self, agent: str) -> None:
        """运行已停止后解除主体占位；持久授权保留供显式恢复。"""
        await self.close_agent_resources(agent)
        await self._store.release_registration(self._actor, agent)

    def content_provider(self, agent: str) -> Any:
        """为一次 Agent 装配创建独立内容端口；注册完成前不具备读取身份。"""
        from ghrah.ability.local_content import LocalGrantedContentProvider

        validate_agent_id(agent)
        provider = LocalGrantedContentProvider(
            self._store,
            agent,
            mcp_service=(
                self._mcp_deployment.for_agent(agent) if self._mcp_deployment is not None else None
            ),
        )
        if self._mcp_deployment is not None:
            self._content_services.setdefault(agent, []).append(provider)
        return provider

    async def materialize(self, *, agent: str, resolved: ResolvedAgent) -> StartupMaterialization:
        """执行一次启动物化。

        Args:
            agent: 正在启动的稳定 Agent ID。
            resolved: 已解析的 Agent（模板成员与控制成员）。

        Returns:
            工具实例、选定映射与核定摘要。

        Raises:
            StartupGrantError: 包实例无法登记、策略与模板无法对齐，或没有
                任何可用工具时失败，不回退到模板自动授予。
        """
        validate_agent_id(agent)
        catalog = self._source.definitions()
        control, containers = self._split(catalog, resolved.abilities)
        if self._policy is not None:
            # 内容包可以没有 Operation，因而不会出现在模板的工具 ref 中。
            # 只准备部署策略显式引用的包；登记版本本身不产生授予。
            for rule in self._policy.for_agent(agent):
                if rule.package not in {
                    definition.identity.full_name for definition in catalog.values()
                }:
                    raise StartupGrantError(f"策略引用了未解析的包：{rule.package}")
                containers.setdefault(rule.package, {})
        else:
            # 无启动策略时保留管理员已授予的内置内容包；不从磁盘发现新授予。
            await self._store.ensure_agent(self._actor, agent)
            records = await self._store.grants(self._actor, agent)
            content_instances = {record.instance_id for record in records if record.content}
            for definition in catalog.values():
                package = definition.identity.full_name
                if _instance_id(package) in content_instances:
                    containers.setdefault(package, {})
        if not containers:
            raise StartupGrantError(
                f"spawn '{agent}' 的模板没有任何能力包成员——builtin 模式不能只靠"
                "循环控制成员启动；请在 manifest 中声明能力包 ref"
            )
        registered: dict[str, tuple[GrantInstance, AbilityVersion, dict[str, ResolvedAbility]]] = {}
        for package, members in sorted(containers.items()):
            instance, version = await self._register(catalog, package, set(members))
            if instance.alias in registered:
                raise StartupGrantError(f"能力包局部别名冲突：{instance.alias}")
            registered[instance.alias] = (instance, version, members)
        instances = [item[0] for item in registered.values()]
        template = {item[0].alias: set(item[2]) for item in registered.values()}
        if self._policy is None:
            # 未配置策略的存量模式：只认已写入关系库的既有事实（一次性迁移或
            # 管理员写入），启动不自动扩权也不撤销；没有事实即不授予、启动失败。
            desired = await self._existing_desired(agent, instances)
            if not desired:
                raise StartupGrantError(
                    f"spawn '{agent}' 未配置启动授权策略且没有存量授权事实——"
                    "不授予任何能力；请配置启动策略，或对已部署 Agent 执行一次性迁移"
                )
        else:
            desired = self._desired(instances, template, agent)
            if not desired:
                raise StartupGrantError(
                    f"spawn '{agent}' 的启动授权为空——策略与模板没有授予任何成员；"
                    "请检查授权策略或 manifest 模板"
                )
        # 系统元工具只在普通授权已成立之后加入；不能让空策略靠元工具启动。
        core = next((item for item in registered.values() if item[0].package == "ghrah.core"), None)
        if core is None:
            instance, version = await self._register(catalog, "ghrah.core", set())
            if instance.alias in registered:
                raise StartupGrantError("内置 core 系统包与已有能力包局部别名冲突")
            registered[instance.alias] = (instance, version, {})
            instances.append(instance)
        else:
            instance = core[0]
        facts = desired.get(instance.alias, GrantFacts())
        revoked = set()
        if self._policy is not None:
            for rule in self._policy.for_agent(agent):
                if rule.package == "ghrah.core" and rule.effect == "revoke":
                    revoked.update(META_TOOL_NAMES if rule.member is None else {rule.operation})
        for member in sorted(META_TOOL_NAMES - revoked):
            if member not in facts.members:
                facts = facts.with_member(member)
        if not facts.empty:
            desired[instance.alias] = facts
        try:
            result = await apply_grants(
                self._store,
                self._actor,
                agent,
                desired,
                instances=instances,
                policy_digest=self._policy.digest if self._policy is not None else None,
            )
        except AccessError as error:
            raise StartupGrantError(f"启动授权写入失败：{error.code}") from error
        bindings, revision = await self._bind(agent, desired, result.revision)
        sources = {alias: item[2] for alias, item in registered.items()}
        run = ""
        abilities = self._instantiate(
            agent,
            control,
            instances,
            desired,
            bindings,
            sources,
            run=run,
            agent_name=resolved.config.name,
        )
        abilities.ability_content = self.content_provider(agent)
        from ghrah.ability.surface.runtime import RunScriptTool, ToolSurfaceRuntime

        surface = ToolSurfaceRuntime(
            store=self._store,
            source=self._source,
            agent=agent,
            table=abilities,
            config=resolved.surface,
            thresholds=self._surface_thresholds,
            search_limit=self._surface_search_limit,
            loaded_limit=self._surface_loaded_limit,
        )
        abilities.tool_surface = surface
        abilities.append(RunScriptTool(surface))
        self._surface_services.setdefault(agent, []).append(surface)
        return StartupMaterialization(
            abilities=abilities,
            tools=bindings,
            revision=revision,
            granted=result.granted,
            revoked=result.revoked,
        )

    async def bind_run(
        self, agent: str, materialization: StartupMaterialization | AuthorizedToolTable
    ) -> None:
        """把当前受信 run 绑定到本次物化的投影工具上。

        启动核定发生在 Agent 注册之前，那时读不到运行实例；注册完成后由装配
        方调用本方法补齐 run。读不到当前运行代次时明确失败，不借用名称或
        旧运行的身份；注册方负责回收本次新建的执行实例。

        Args:
            agent: 已注册的稳定 Agent ID。
            materialization: 本次启动物化结果或授权工具表。
        """
        try:
            run = self._store.current_run(agent)
        except AccessError as error:
            raise StartupGrantError(f"Agent {agent} has no trusted run to bind") from error
        materialization.bind_run(run)
        logger.info("builtin spawn: agent %s 绑定受信 run=%s", agent, run)

    def tool_table_projector(self, agent: str, materialization: StartupMaterialization) -> Any:
        """返回“按关系库当前授予重算模型可见工具名”的投影器。

        工具表是关系库的派生投影，必须能从权威重建：调用方每轮取一次可见
        工具名，撤销在下一轮就从模型视野消失，无需重启 Agent。只保留本次
        物化实际实例化过且仍可用的工具——没有实例的成员即使后来被授予也不能
        凭空出现。投影固定使用本次装配绑定的 run，旧装配不能借用新 run。

        Args:
            agent: 稳定 Agent ID。
            materialization: 本次启动物化结果（提供已实例化工具集合）。

        Returns:
            无参异步投影器，返回当前可见的工具名元组；无法确认时返回空元组。
        """
        store = self._store
        ready = {
            (ability.name, reference.instance_id, reference.alias, reference.member)
            for ability in materialization.abilities
            if (reference := getattr(ability, "member_reference", None)) is not None
        }

        def backend_available(entry: SurfaceEntry) -> bool:
            return (
                entry.tool_name,
                entry.member.instance_id,
                entry.package,
                entry.member.member,
            ) in ready

        async def project() -> tuple[str, ...]:
            run = materialization.abilities.bound_run
            if not run:
                return ()
            try:
                entries = await project_surface(
                    agent,
                    run,
                    store=store,
                    backend_available=backend_available,
                )
            except AccessError:
                return ()
            return tuple(entry.tool_name for entry in entries)

        return project

    def _split(
        self, catalog: Any, resolved: list[ResolvedAbility]
    ) -> tuple[list[ResolvedAbility], dict[str, dict[str, ResolvedAbility]]]:
        """把模板成员分成循环控制与能力包成员（保留模板声明供权限合并）。"""
        control: list[ResolvedAbility] = []
        containers: dict[str, dict[str, ResolvedAbility]] = {}
        for item in resolved:
            handler = item.implementation.handler or ""
            if handler in CONTROL_HANDLERS and item.ability_name.startswith("ghrah.core."):
                control.append(item)
                continue
            package = self._package_of(catalog, item)
            containers.setdefault(package, {})[handler] = item
        return control, containers

    @staticmethod
    def _package_of(catalog: Any, item: ResolvedAbility) -> str:
        """由成员全名定位所属包身份（``ghrah.fs.read_file`` → ``ghrah.fs``）。"""
        for definition in catalog.values():
            package = definition.identity.full_name
            if item.ability_name.rpartition(".")[0] == package:
                return package
        raise StartupGrantError(f"成员 {item.ability_name} 不属于任何已解析能力包")

    async def _register(
        self, catalog: Any, package: str, members: set[str]
    ) -> tuple[GrantInstance, AbilityVersion]:
        """捕获并登记包实例，返回启动核定所需的实例事实。

        模板只提供身份；版本、内容与成员定义以捕获结果为准，之后核定只读
        这些已提交事实。同一版本重复提交是幂等的。登记路径与迁移 bootstrap
        共用 :func:`register_package_instance`，不存在第二条写版本/实例的路。
        """
        registered = await register_package_instance(
            store=self._store,
            actor=self._actor,
            catalog=catalog,
            package=package,
            members=members,
            workspace_root=self._workspace_root,
            source=self._source,
            filesystem_roots=self._filesystem_roots,
        )
        return registered.instance, registered.version

    def _desired(
        self,
        instances: list[GrantInstance],
        template: dict[str, set[str]],
        agent: str,
    ) -> dict[str, GrantFacts]:
        """执行模板 + 策略折算；失败即失败，不回退模板自动授予。"""
        try:
            return desired_grants(
                instances=instances,
                template=template,
                policy=self._policy,
                agent=agent,
            )
        except GrantReconcileError as error:
            raise StartupGrantError(str(error)) from error

    async def _existing_desired(
        self, agent: str, instances: list[GrantInstance]
    ) -> dict[str, GrantFacts]:
        """未配置策略时的存量事实：只取关系库已有事实，不读模板、不自动扩权。

        迁移 bootstrap 与管理员写入写进关系库的获授，是这些 Agent 继续启动的
        依据；本次启动只把它作为目标事实原样保留（撤销后不会复活：事实已不在
        库里）。不在本次模板包集合里的 alias 不进入目标，由核定按陈旧关系撤销。
        """
        await self._store.ensure_agent(self._actor, agent)
        records = {record.alias: record for record in await self._store.grants(self._actor, agent)}
        catalog = {instance.alias for instance in instances}
        desired: dict[str, GrantFacts] = {}
        for alias, record in records.items():
            if alias not in catalog:
                continue
            facts = stored_facts(list(record.members), list(record.content), record.axes)
            if not facts.empty:
                desired[alias] = facts
        return dict(sorted(desired.items()))

    async def _bind(
        self,
        agent: str,
        desired: dict[str, GrantFacts],
        revision: int,
    ) -> tuple[tuple[StartupToolBinding, ...], int]:
        """按唯一命名规则选定工具并清理陈旧选择。"""
        pairs = [(alias, key) for alias, facts in desired.items() for key in sorted(facts.members)]
        if any(key in META_TOOL_NAMES and alias != "core" for alias, key in pairs):
            raise StartupGrantError("只有内置 core 包可以装配元工具保留名")
        bindings = tuple(
            StartupToolBinding(item.package, item.member, item.tool_name)
            for item in assign_tool_names(pairs, allow_reserved=META_TOOL_NAMES)
        )
        wanted = {binding.tool_name for binding in bindings}
        for stale in sorted(set(await self._store.tool_names(self._actor, agent)) - wanted):
            revision = await self._store.write_unbind_tool(
                self._actor,
                agent,
                UnbindToolRequest(tool_name=stale, expected_revision=revision),
            )
        for binding in bindings:
            revision = await self._store.write_bind_tool(
                self._actor,
                agent,
                BindToolRequest(
                    alias=binding.alias,
                    member=binding.member,
                    tool_name=binding.tool_name,
                    expected_revision=revision,
                ),
            )
        return bindings, revision

    def _instantiate(
        self,
        agent: str,
        control: list[ResolvedAbility],
        instances: list[GrantInstance],
        desired: dict[str, GrantFacts],
        bindings: tuple[StartupToolBinding, ...],
        sources: dict[str, dict[str, ResolvedAbility]],
        *,
        run: str = "",
        agent_name: str = "",
    ) -> AuthorizedToolTable:
        """把选定成员实例化成工具，并把固定 schema、权限范围与接纳绑到包装层。"""
        definitions = {instance.alias: instance for instance in instances}
        abilities: list[Any] = instantiate_resolved_abilities(
            control,
            workspace_root=self._workspace_root,
            auto_approve_abilities=self._auto_approve_abilities,
            require_approval_by_default=self._require_approval_by_default,
            command_runner=self._command_runner,
            safe_extra_commands=self._safe_extra_commands,
            safe_extra_sub_commands=self._safe_extra_sub_commands,
        )
        for binding in bindings:
            instance = definitions[binding.alias]
            address = f"operations/{binding.member}"
            visible, available = desired[binding.alias].axes[address]
            if not (visible and available):
                continue
            operation = instance.definition["operations"][binding.member]
            template = sources.get(binding.alias, {}).get(binding.member)
            resolved = self._resolved_operation(instance.package, binding.member)
            scope = resolve_effective_permission(
                resolved.permissions,
                template.requested_permissions if template is not None else None,
                workspace_root=self._workspace_root,
            )
            if file_member_paths(instance.definition, binding.member):
                scope = replace(scope, sandbox_roots=instance.allowed_roots)
            if resolved.implementation.type == "python":
                provider = self._source.packages
                if provider is None:
                    raise StartupGrantError("安装包 Provider 未接线")
                inner = provider.create_operation(
                    instance.package,
                    binding.member,
                    instance.version_digest,
                    scope=scope,
                    workspace_root=self._workspace_root,
                )
                required = [
                    name
                    for name in ("fs_write", "shell_access", "net_access")
                    if getattr(resolved.permissions, name)
                ]
                if any(not getattr(scope, name) for name in required):
                    inner = PermissionDeniedAbility(inner, "Permission denied by effective scope")
                if scope.require_hitl and resolved.ability_name not in self._auto_approve_abilities:
                    inner = HITLRequiredAbility(
                        inner,
                        ability_name=binding.member,
                        reason="Operation requires HITL approval",
                    )
                built = [inner]
            else:
                built = instantiate_resolved_abilities(
                    [resolved],
                    workspace_root=self._workspace_root,
                    auto_approve_abilities=self._auto_approve_abilities,
                    require_approval_by_default=self._require_approval_by_default,
                    command_runner=self._command_runner,
                    safe_extra_commands=self._safe_extra_commands,
                    safe_extra_sub_commands=self._safe_extra_sub_commands,
                    permission_scopes={resolved.ability_name: scope},
                )
            if not built:
                raise StartupGrantError(
                    f"成员 {binding.alias}/{binding.member} 无法实例化——builtin 来源不支持该后端"
                )
            abilities.append(
                ProjectedAbility(
                    built[0],
                    tool_name=binding.tool_name,
                    member=binding.member,
                    member_reference=MemberReference(
                        instance_id=instance.instance_id,
                        alias=binding.alias,
                        member=binding.member,
                        version_digest=instance.version_digest,
                    ),
                    identity=InvocationIdentity(
                        project_id=self._store.project_id,
                        agent=agent,
                        run=run,
                        agent_name=agent_name,
                    ),
                    tool_schema={
                        "type": "function",
                        "function": {
                            "name": binding.tool_name,
                            "description": operation["description"],
                            "parameters": operation["input_schema"],
                        },
                    },
                    admission=self._admission(agent, binding, instance.version_digest),
                    current_run=lambda agent=agent: self._store.current_run(agent),
                    input_model=(
                        getattr(
                            import_module(
                                f"ghrah.ability.builtin.{instance.package.rsplit('.', 1)[-1]}"
                                f".operations.{binding.member}"
                            ),
                            "Input",
                            None,
                        )
                        if resolved.implementation.type == "builtin"
                        else None
                    ),
                )
            )
        return AuthorizedToolTable(abilities)

    def _admission(self, agent: str, binding: StartupToolBinding, version_digest: str) -> Any:
        """按启动时固定的成员、版本与受信 run 在当前关系中复核。

        执行实现绑定在启动时的 ``_inner``，因此接纳不能只看同名工具当前指向
        哪个成员：期望的别名、成员与版本必须一并比对，否则管理端把工具改绑
        到别的成员后，旧实现仍会按新绑定被放行。

        受信 run 同理：调用对象带着装配时绑定的 run，接纳用它作为关系库身份，
        并要求它与当前 run 一致。运行代次变化后，旧工具对象不能借用新 run
        继续执行。
        """
        store = self._store

        async def admit(call: OperationCall) -> str | None:
            if call.member.alias != binding.alias or call.member.member != binding.member:
                return _denial(binding, "access_denied")
            if self._mcp_deployment is not None:
                await self._mcp_deployment.refresh_agent(agent, call.identity.run)
            try:
                current_run = store.current_run(agent)
            except AccessError:
                return _denial(binding, "access_denied")
            bound_run = call.identity.run
            if not bound_run or bound_run != current_run:
                return _denial(binding, "stale_invocation")
            actor = AccessIdentity(store.project_id, agent, current_run)
            try:
                basis = await store.admit_tool(
                    actor,
                    agent,
                    binding.tool_name,
                    dict(call.arguments),
                    expected_alias=binding.alias,
                    expected_member=binding.member,
                )
            except AccessError as error:
                return _denial(binding, error.code)
            if version_digest and not basis.startswith(f"{version_digest}:"):
                return _denial(binding, "access_denied")
            return None

        return admit

    def _resolved_operation(self, package: str, member: str) -> ResolvedAbility:
        """按成员全名取回内置视图，构造与模板同形的已解析能力。"""
        view = self._source.load_ability(f"{package}.{member}")
        return ResolvedAbility(
            ability_name=view.full_name,
            tool_schema=view.tool,
            permissions=view.metadata.permissions,
            implementation=view.implementation,
            hooks=view.hooks,
        )


def _denial(binding: StartupToolBinding, code: str) -> str:
    """把接纳拒绝翻译成模型可读的错误；不泄露内部修订号。"""
    target = f"{binding.alias}/{binding.member}"
    if code == "access_denied":
        return (
            f"Access denied for tool '{binding.tool_name}' ({target}): the member is not "
            "currently granted, or the arguments are outside the granted scope"
        )
    if code == "invalid_change":
        return (
            f"Invalid arguments for tool '{binding.tool_name}' ({target}): a required "
            "resource argument is missing or malformed"
        )
    if code == "stale_invocation":
        return (
            f"Tool '{binding.tool_name}' ({target}) belongs to an earlier run of this Agent; "
            "start a new run or re-request the tool before calling it again"
        )
    if code == "unavailable":
        return (
            f"Tool '{binding.tool_name}' ({target}) is not available: the backend is not "
            "supported by the admission layer"
        )
    return f"Tool '{binding.tool_name}' ({target}) could not be admitted: {code}"


def _instance_id(package: str) -> str:
    """包身份 → 实例标识：点分身份不能直接做关系库实例键。"""
    return TypeAdapter(TreeId).validate_python(package.replace(".", "-"))
