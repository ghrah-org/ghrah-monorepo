# SPDX-License-Identifier: Apache-2.0
"""启动物化：把一次 spawn 的模板与策略变成可运行工具表。

这是启动核定的生产接线层：解析本次启动涉及的包实例、登记已提交版本、执行
``desired_grants``/``apply_grants``、按唯一命名规则选择工具，再把“获授 ∩ 已选
∩ 可暴露 ∩ 可用”的成员实例化成 Agent 工具。

边界：

- 循环控制成员（``conversation`` / ``end_task``）仍由 Agent 原控制机制提供，
  不进入 Ability 关系库，也不按授权面裁剪；
- 工具 schema 与描述来自**已提交版本的定义面**，不从实现脚本二次读取；
- 执行实例仍由本地 builtin 装配构造，执行上下文接线属于后续工作包；
- 未配置策略时不授予任何能力；模板只决定“申请哪些成员”，不自行生效。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ghrah.protocol.ability_tree import BindToolRequest, TreeId, TreeKey, UnbindToolRequest
from pydantic import TypeAdapter

from ghrah.ability.access.backends import file_member_paths
from ghrah.ability.access.facts import GrantFacts
from ghrah.ability.access.policy import GrantPolicy
from ghrah.ability.access.reconcile import (
    GrantInstance,
    GrantReconcileError,
    apply_grants,
    desired_grants,
)
from ghrah.ability.access.store import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.execution.ability_build import instantiate_resolved_abilities
from ghrah.ability.loading.ability_catalog import load_ability_definitions
from ghrah.ability.loading.ability_roots import builtin_root
from ghrah.ability.permissions.scope import resolve_effective_permission
from ghrah.ability.surface.naming import assign_tool_names
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

    abilities: list[Any]
    tools: tuple[StartupToolBinding, ...]
    revision: int
    granted: tuple[str, ...]
    revoked: tuple[str, ...]


class ProjectedAbility:
    """把执行实例包装成固定工具名与定义面 schema 的模型工具。

    工具名由表面命名算法分配，可能与成员键不同；执行仍委托给本地装配出的
    实例，调用入口只按这张映射表查成员，不从模型传入的名字反推模块。
    """

    def __init__(
        self,
        inner: Any,
        *,
        tool_name: str,
        tool_schema: dict[str, Any],
        member: str = "",
        admission: Any = None,
    ) -> None:
        self._inner = inner
        self._tool_name = tool_name
        self._tool_schema = tool_schema
        self._member = member
        self._admission = admission

    @property
    def name(self) -> str:
        """模型可见的工具名。"""
        return self._tool_name

    @property
    def member(self) -> str:
        """该工具固定的成员键；执行器按它解析路径参数，不从工具名反推。"""
        return self._member

    async def execute(self, context: Any) -> Any:
        """先做执行接纳，再委托给本地执行实例。

        接纳在 HITL 钩子之后运行：审批只决定“范围外的调用要不要问人”，
        关系与授予范围的复核不能被审批跳过——撤销、重新选择与越界在此
        最后兜底，失败时没有副作用。
        """
        if self._admission is not None:
            error = await self._admission(context.tool_args or {})
            if error is not None:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={"error": error},
                )
        return await self._inner.execute(context)

    def bind_tool(self) -> dict[str, Any]:
        """返回来自定义面的固定 tool schema。"""
        return self._tool_schema

    def get_hooks(self) -> list[Any]:
        """沿用执行实例的 Hook，并把包装工具名映射回成员身份。

        内核固定 Hook（路径审批、命令审批、显式 HITL）按成员键裁决；表面
        工具名在跨包撞名时会被限定，二者不能混用。
        """
        return [
            _MemberHook(hook, member=self._member, tool_name=self._tool_name)
            if self._member
            else hook
            for hook in self._inner.get_hooks()
        ]

    def get_default_state(self) -> dict[str, Any]:
        """沿用执行实例的默认状态。"""
        return dict(self._inner.get_default_state())

    def __getattr__(self, item: str) -> Any:
        """其余属性（如环境快照读取的 checker）继续指向执行实例。"""
        return getattr(self._inner, item)


class _MemberHook:
    """把包装工具名映射回成员身份后调用内核固定 Hook。"""

    def __init__(self, inner: Any, *, member: str, tool_name: str) -> None:
        self._inner = inner
        self._member = member
        self._tool_name = tool_name
        self.hook_point = inner.hook_point

    def _named(self, context: Any) -> Any:
        if context.current_ability_name != self._tool_name:
            return context
        return _MemberNamedContext(context, self._member)

    async def should_trigger(self, context: Any) -> bool:
        """仅在本次工具调用时询问原 Hook，并以成员身份裁决。"""
        return bool(await self._inner.should_trigger(self._named(context)))

    async def execute(self, context: Any, result: Any) -> Any:
        """用成员身份执行原 Hook，返回结果原样透传。"""
        return await self._inner.execute(self._named(context), result)


class _MemberNamedContext:
    """只覆盖能力名的上下文视图；其余属性继续指向真实执行上下文。"""

    def __init__(self, inner: Any, member: str) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_member", member)

    @property
    def current_ability_name(self) -> str:
        """内核 Hook 看到的成员身份。"""
        return self._member

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


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
        auto_approve_abilities: tuple[str, ...] = (),
        require_approval_by_default: bool = True,
        command_runner: Any = None,
        safe_extra_commands: tuple[str, ...] = (),
        safe_extra_sub_commands: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> None:
        if policy is not None and not isinstance(policy, GrantPolicy):
            raise TypeError("policy 必须是 GrantPolicy 或 None")
        self._store = store
        self._source = source
        self._actor = actor
        self._policy = policy
        self._workspace_root = workspace_root
        self._auto_approve_abilities = auto_approve_abilities
        self._require_approval_by_default = require_approval_by_default
        self._command_runner = command_runner
        self._safe_extra_commands = safe_extra_commands
        self._safe_extra_sub_commands = safe_extra_sub_commands

    async def materialize(self, *, agent: str, resolved: ResolvedAgent) -> StartupMaterialization:
        """执行一次启动物化。

        Args:
            agent: 正在启动的 Agent 名称。
            resolved: 已解析的 Agent（模板成员与控制成员）。

        Returns:
            工具实例、选定映射与核定摘要。

        Raises:
            StartupGrantError: 包实例无法登记、策略与模板无法对齐，或没有
                任何可用工具时失败，不回退到模板自动授予。
        """
        catalog = load_ability_definitions()
        control, containers = self._split(catalog, resolved.abilities)
        if not containers:
            raise StartupGrantError(
                f"spawn '{agent}' 的模板没有任何能力包成员——builtin 模式不能只靠"
                "循环控制成员启动；请在 manifest 中声明能力包 ref"
            )
        registered: dict[str, tuple[GrantInstance, AbilityVersion, dict[str, ResolvedAbility]]] = {}
        for package, members in sorted(containers.items()):
            instance, version = await self._register(catalog, package, set(members))
            registered[instance.alias] = (instance, version, members)
        instances = [item[0] for item in registered.values()]
        template = {item[0].alias: set(item[2]) for item in registered.values()}
        desired = self._desired(instances, template, agent)
        if not desired:
            raise StartupGrantError(
                f"spawn '{agent}' 的启动授权为空——策略与模板没有授予任何成员；"
                "请检查授权策略或 manifest 模板"
            )
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
        abilities = self._instantiate(agent, control, instances, desired, bindings, sources)
        return StartupMaterialization(
            abilities=abilities,
            tools=bindings,
            revision=revision,
            granted=result.granted,
            revoked=result.revoked,
        )

    def _split(
        self, catalog: Any, resolved: list[ResolvedAbility]
    ) -> tuple[list[ResolvedAbility], dict[str, dict[str, ResolvedAbility]]]:
        """把模板成员分成循环控制与能力包成员（保留模板声明供权限合并）。"""
        control: list[ResolvedAbility] = []
        containers: dict[str, dict[str, ResolvedAbility]] = {}
        for item in resolved:
            handler = item.implementation.handler or ""
            if handler in CONTROL_HANDLERS:
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
            if item.ability_name.startswith(package + "."):
                return package
        raise StartupGrantError(
            f"成员 {item.ability_name} 不属于任何内置能力包——builtin 模式不接受外部来源"
        )

    async def _register(
        self, catalog: Any, package: str, members: set[str]
    ) -> tuple[GrantInstance, AbilityVersion]:
        """捕获并登记包实例，返回启动核定所需的实例事实。

        模板只提供身份；版本、内容与成员定义以捕获结果为准，之后核定只读
        这些已提交事实。同一版本重复提交是幂等的。
        """
        entry = next(
            ((key, item) for key, item in catalog.items() if item.identity.full_name == package),
            None,
        )
        if entry is None:
            raise StartupGrantError(f"未找到内置能力包：{package}")
        container, container_definition = entry
        root = builtin_root().path
        if root is None:
            raise StartupGrantError("内置能力树不是可读目录，无法捕获版本")
        package_root = root / container
        version = capture_ability_version(
            (package_root / "ability.yaml").read_text(encoding="utf-8"), root=package_root
        )
        definition = version.definition().model_dump(mode="json")
        file_members = sorted(member for member in members if file_member_paths(definition, member))
        if not self._workspace_root and file_members:
            raise StartupGrantError(
                f"包 {package} 的文件类成员需要显式 workspace_root 才能授予：{file_members}"
            )
        await self._store.commit_version(self._actor, version)
        instance_id = _instance_id(package)
        expected_revision = 0
        try:
            expected_revision = int((await self._store.instance(instance_id))["revision"])
        except AccessError:
            expected_revision = 0
        await self._store.select_instance(
            self._actor,
            instance_id,
            version.digest,
            Path(self._workspace_root) if self._workspace_root else None,
            expected_revision=expected_revision,
        )
        instance = GrantInstance(
            alias=TypeAdapter(TreeKey).validate_python(container_definition.container_name),
            package=package,
            instance_id=instance_id,
            version_digest=version.digest,
            definition=definition,
            allowed_roots=(
                (str(Path(self._workspace_root).resolve()),) if self._workspace_root else ()
            ),
        )
        return instance, version

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

    async def _bind(
        self,
        agent: str,
        desired: dict[str, GrantFacts],
        revision: int,
    ) -> tuple[tuple[StartupToolBinding, ...], int]:
        """按唯一命名规则选定工具并清理陈旧选择。"""
        pairs = [(alias, key) for alias, facts in desired.items() for key in sorted(facts.members)]
        bindings = tuple(
            StartupToolBinding(item.package, item.member, item.tool_name)
            for item in assign_tool_names(pairs)
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
    ) -> list[Any]:
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
                    tool_schema={
                        "type": "function",
                        "function": {
                            "name": binding.tool_name,
                            "description": operation["description"],
                            "parameters": operation["input_schema"],
                        },
                    },
                    admission=self._admission(agent, binding),
                )
            )
        return abilities

    def _admission(self, agent: str, binding: StartupToolBinding) -> Any:
        """按工具名在当前关系中重新解析并接纳；撤销立即影响下一次调用。"""
        store = self._store

        async def admit(arguments: dict[str, Any]) -> str | None:
            try:
                run = store.current_run(agent)
            except AccessError:
                return _denial(binding, "access_denied")
            actor = AccessIdentity(store.project_id, agent, run)
            try:
                await store.admit_tool(actor, agent, binding.tool_name, dict(arguments))
            except AccessError as error:
                return _denial(binding, error.code)
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
    if code == "unavailable":
        return (
            f"Tool '{binding.tool_name}' ({target}) is not available: the backend is not "
            "supported by the admission layer"
        )
    return f"Tool '{binding.tool_name}' ({target}) could not be admitted: {code}"


def _instance_id(package: str) -> str:
    """包身份 → 实例标识：点分身份不能直接做关系库实例键。"""
    return TypeAdapter(TreeId).validate_python(package.replace(".", "-"))
