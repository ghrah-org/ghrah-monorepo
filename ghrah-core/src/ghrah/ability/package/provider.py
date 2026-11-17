# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""安装包的本地 Provider：校验、选版、加载与函数入口适配。

本组件不依赖 CoreUnit 或 Agent 生命周期；独立服务可复用同一加载器。注册只
提供候选实现，授权仍由关系库裁定。Operation 在 Provider 宿主进程内执行。
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import inspect
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel, ValidationError

from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import ExecutionServices
from ghrah.ability.execution.invocation import OperationCall
from ghrah.ability.loading.ability_schema import AbilityDefinition, parse_definition
from ghrah.ability.package.installed_store import InstalledStore, InstalledVersion, is_link
from ghrah.ability.package.installer import AbilityInstaller
from ghrah.ability.package.schema import strip_schema_metadata
from ghrah.ability.permissions.fs_permissions import FSPermissionChecker
from ghrah.ability.permissions.scope import EffectivePermission
from ghrah.ability.versions import AbilityVersion, capture_ability_version
from ghrah.manifest.ability import AbilityManifest, AbilityMetadata
from ghrah.manifest.errors import ManifestValidationError
from ghrah.manifest.types import ImplementationDef, PermissionFlags
from ghrah.types.results import ActionOutcome, ActionResult


class InstalledAbilityProvider:
    """显式安装根的包加载器；单版本可解析，多版本须显式选定。"""

    def __init__(self, root: str | Path) -> None:
        path = Path(root)
        if not path.is_absolute():
            raise ValueError("ability.install_root 必须是绝对路径")
        self.store = InstalledStore(path)
        self._installer = AbilityInstaller(root=path)
        self._selected: dict[str, str] = {}
        self._loaded: dict[tuple[str, str], ModuleType] = {}
        self._captured: dict[str, tuple[InstalledVersion, AbilityVersion]] = {}

    def select_version(self, package: str, version: str) -> None:
        """由受信装配者显式选版；不写授权、不替换已有执行对象。"""
        self.capture(package, version=version)
        self._selected[package] = version

    def _resolve(self, package: str, version: str | None = None) -> InstalledVersion:
        wanted = version or self._selected.get(package)
        versions = self.store.versions_of(package)
        if wanted is None and len(versions) != 1:
            raise ManifestValidationError(
                f"安装包 {package} 有 {len(versions)} 个候选版本，请显式 select_version"
            )
        item = next((item for item in versions if wanted is None or item.version == wanted), None)
        if item is None:
            raise ManifestValidationError(f"安装包版本不存在：{package}@{wanted}")
        return item

    def capture(self, package: str, *, version: str | None = None) -> AbilityVersion:
        """先验证安装记录，再捕获可提交的领域版本；不 import Operation。"""
        item = self._resolve(package, version)
        results = self._installer.verify(package, item.version)
        if not results or not results[0].ok:
            raise ManifestValidationError(f"安装包校验失败：{package}@{item.version}")
        captured = capture_ability_version(
            (item.root / "ability.yaml").read_text(encoding="utf-8"), root=item.root
        )
        if (
            item.lock is None
            or captured.digest != item.lock.package_digest
            or captured.package_digest != item.lock.package_digest
        ):
            raise ManifestValidationError(f"安装包摘要与安装记录不一致：{package}@{item.version}")
        definition = captured.definition()
        if (
            definition.identity.full_name != package
            or definition.identity.version != item.version
            or (definition.identity.namespace == "mcp") != (definition.origin == "mcp")
            or (definition.origin == "mcp")
            != (item.source is not None and item.source.kind == "mcp")
        ):
            raise ManifestValidationError(f"安装槽与包身份不一致：{package}@{item.version}")
        self._captured[captured.digest] = (item, captured)
        self._selected.setdefault(package, item.version)
        return captured

    def definitions(self) -> dict[str, AbilityDefinition]:
        """只读候选索引；按引用选择时才校验与捕获资源，不加载未选包内容。"""
        result = {}
        for package in self.store.packages():
            if len(package.versions) != 1 and package.full_name not in self._selected:
                continue
            item = self._resolve(package.full_name)
            try:
                definition = parse_definition(
                    (item.root / "ability.yaml").read_text(encoding="utf-8"), source=str(item.root)
                )
            except OSError, ManifestValidationError:
                # 不可读候选由 list/verify 诊断；直接引用时 capture 会明确失败。
                continue
            if (
                definition.identity.full_name == package.full_name
                and definition.identity.version == item.version
                and (definition.identity.namespace == "mcp") == (definition.origin == "mcp")
                and (definition.origin == "mcp")
                == (item.source is not None and item.source.kind == "mcp")
            ):
                result[package.full_name] = definition
        return result

    def manifest(self, full_name: str) -> AbilityManifest:
        """从已校验索引投影成员声明；此处不加载实现。"""
        package, _, member = full_name.rpartition(".")
        definition = self.capture(package).definition()
        operation = definition.operations.get(member)
        if operation is None and member not in definition.skills:
            # skill 键常是 kebab-case，而 ability 名只允许 [a-z0-9_]：反向归一后再查一次
            member = member.replace("_", "-")
        if operation is None:
            skill = definition.skills.get(member)
            if skill is None:
                raise ManifestValidationError(f"安装包没有成员：{full_name}")
            return AbilityManifest(
                manifest="ability",
                version="1",
                metadata=AbilityMetadata(
                    namespace=package,
                    name=_ability_safe_name(member),
                    title=skill.title,
                    description=skill.description,
                    tags=list(skill.tags),
                    permissions=PermissionFlags(),
                ),
                tool=None,
                implementation=ImplementationDef(type="skill", handler=member),
            )
        return AbilityManifest(
            manifest="ability",
            version="1",
            metadata=AbilityMetadata(
                namespace=package,
                name=member,
                title=operation.title,
                description=operation.description,
                tags=list(operation.tags),
                permissions=PermissionFlags.model_validate(operation.permissions),
            ),
            # 兼容声明只用于 ref 与权限解析；完整 JSON Schema 在提交定义中，
            # 由授权工具投影直接读取，保留 $ref/嵌套对象等形状。
            tool=None,
            implementation=ImplementationDef(type="python", handler=member),
        )

    def package_members(self, package: str) -> list[AbilityManifest]:
        """把一个安装包的全部成员投影成可授予清单（operation + skill）。

        这是「一个 ref 即整包」的入口：调用方不需要知道成员是工具还是技能，
        投影出来的 ``implementation.type`` 已经分好类——``python`` 进工具面，
        ``skill`` 进技能面。

        Args:
            package: 包全名（``namespace.name``）

        Returns:
            成员清单；先 operations 后 skills，各自按成员键排序
        """
        definition = self.capture(package).definition()
        members = [
            *sorted(definition.operations),
            *(_ability_safe_name(key) for key in sorted(definition.skills)),
        ]
        return [self.manifest(f"{package}.{member}") for member in members]

    def installed_root(self, package: str, release: str) -> Path | None:
        """定位仍带安装记录的版本目录；不 import 包代码，也不跟随链接。

        Args:
            package: 包身份全名。
            release: 发布版本号。

        Returns:
            版本目录；未安装、缺 lock 或目录本身是链接时为 ``None``。
        """
        item = self.store.resolve(package, release)
        if item is None or item.lock is None or is_link(item.root):
            return None
        definition = item.root / "ability.yaml"
        if is_link(definition) or not definition.is_file():
            return None
        return item.root

    def available(self, version: AbilityVersion) -> bool:
        """检查已捕获版本的安装根和 lock 是否仍存在；读取不改变授权事实。"""
        captured = self._captured.get(version.digest)
        if captured is None:
            return False
        previous, _ = captured
        identity = version.definition().identity
        current = self.store.resolve(identity.full_name, identity.version)
        if current is None or current.lock is None or previous.lock is None:
            return False
        return (
            current.root == previous.root
            and current.lock.package_digest == previous.lock.package_digest
            and (current.root / "ability.yaml").is_file()
            and not is_link(self.store.root)
            and not is_link(current.root.parent.parent)
            and (
                version.definition().origin == "mcp"
                or all(
                    operation.entry is not None
                    and (current.root / operation.entry.path).is_file()
                    and not is_link(current.root / operation.entry.path)
                    for operation in version.definition().operations.values()
                )
            )
        )

    def create_operation(
        self,
        package: str,
        member: str,
        digest: str,
        *,
        scope: EffectivePermission | None = None,
        workspace_root: str | None = None,
    ) -> Ability:
        """只为已捕获、校验且获选的版本加载执行入口；不接收任意模块地址。"""
        item, version = self._captured[digest]
        if version.definition().identity.full_name != package or not self.available(version):
            raise ManifestValidationError(f"安装包不可用：{package}")
        # 加载前再次 verify，拒绝捕获后发生的篡改；已有对象不原地 reload。
        if not self._installer.verify(package, item.version)[0].ok:
            raise ManifestValidationError(f"安装包校验失败：{package}")
        if version.definition().origin == "mcp":
            return McpSnapshotOperation(member)
        key = (digest, member)
        module = self._loaded.get(key)
        if module is None:
            operation = version.definition().operations[member]
            path = item.root / operation.entry.path
            token = hashlib.sha256(f"{item.root}:{digest}".encode()).hexdigest()
            namespace = f"_ghrah_ability_{token}"
            for name, directory in (
                (namespace, item.root),
                (namespace + ".operations", path.parent),
            ):
                if name not in sys.modules:
                    parent = ModuleType(name)
                    parent.__path__ = [str(directory)]
                    sys.modules[name] = parent
            name = f"{namespace}.operations.{member}"
            try:
                module = _load_module(name, path)
                _validate_entry(module, operation.input_schema, operation.output_schema)
            except BaseException:
                sys.modules.pop(name, None)
                raise
            self._loaded[key] = module
        operation = version.definition().operations[member]
        return PythonOperation(
            member,
            module,
            operation.execution.timeout_ms,
            paths=tuple(operation.resources.get("paths", ())),
            scope=scope,
            workspace_root=workspace_root,
            writes=bool(operation.permissions.get("fs_write")),
        )


class McpSnapshotOperation(Ability):
    """快照成员的进程内适配；只把已固定调用交给受信 MCP 端口。"""

    def __init__(self, member: str) -> None:
        self._member = member

    @property
    def name(self) -> str:
        """返回包内成员键。"""
        return self._member

    def get_hooks(self) -> list[Any]:
        """权限与审批由启动物化器的统一包装层装配。"""
        return []

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        """逐成员执行，不允许参数选择服务器、凭据或工具。"""
        from ghrah.ability.mcp_wiring import execute_mcp_member

        return await execute_mcp_member(call, services)


def _ability_safe_name(member: str) -> str:
    """把成员键归一成 ability 合法名（只保留 ``[a-z0-9_]``）。

    operation 键本就是 snake_case；skill 键常带连字符，直接当 ability 名会被
    ``AbilityMetadata`` 的名字规则拒绝，因此在投影处做一次归一。

    Args:
        member: 定义里的成员键

    Returns:
        合法 ability 名
    """
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in member.lower())


def validate_source_tree(root: Path) -> None:
    """安装期 ABI 预检：上传包里的每个 Operation 都必须能被建 agent 期接受。

    安装是唯一能拦住「装得进去、建 agent 才报错」的时机，因此这里对
    ``ability.yaml`` 声明的 operations 逐个做入口 ABI 校验：
    ``async def execute(call, services)``，且 ``Input`` / ``Output`` 的 pydantic
    schema 与声明逐字段一致。下面复用运行时同一个加载器（``_load_module``）与
    同一套校验（``_validate_entry``），保证「安装期通过」与「建 agent 期通过」
    是同一个判定。

    **信任边界（重要）**：预检会 ``exec`` 上传包里的 operation 源码，也就是
    「上传安装 = 在 Subject 进程内执行一次包代码」，且不在沙箱内。因此它必须
    只在操作者已通过鉴权之后执行（Subject 上传端点在调用 ``package_import``
    之前先过 ``_authorized``；远程请求必须配置 Bearer token，回环默认放行）。
    不要把本函数挪到任何鉴权之前，也不要假设上传包是可信数据。

    **skill 成员不在此预检**：skill 没有可执行的 ``ref`` 入口，其内容
    （``skills/<key>/…``）由安装器既有的内容校验覆盖；skill 成员的授权/授予
    能力由模板与装配线承接，本函数既不校验也不拒绝。

    Args:
        root: 解包后的源包根目录

    Raises:
        ManifestValidationError: 任一 operation 的入口文件缺失或 ABI 不一致
    """
    definition = parse_definition(
        (root / "ability.yaml").read_text(encoding="utf-8"), source=str(root)
    )
    token = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    namespace = f"_ghrah_ability_precheck_{token}"
    try:
        for member, operation in definition.operations.items():
            path = root / operation.entry.path
            if not path.is_file():
                raise ManifestValidationError(
                    f"成员 {member} 的入口文件不存在：{operation.entry.path}"
                )
            for name, directory in (
                (namespace, root),
                (namespace + ".operations", path.parent),
            ):
                if name not in sys.modules:
                    parent = ModuleType(name)
                    parent.__path__ = [str(directory)]
                    sys.modules[name] = parent
            module_name = f"{namespace}.operations.{member}"
            module = _load_module(module_name, path)
            _validate_entry(module, operation.input_schema, operation.output_schema)
    finally:
        _clear_precheck_modules(namespace)


def _clear_precheck_modules(namespace: str) -> None:
    """清理本次预检在 ``sys.modules`` 留下的父包与已加载成员。

    成功与失败路径都要清：预检模块只为一次校验存在，长驻 Subject 进程反复
    导入时不能只增不减（``_load_module`` 会把成员与包内 helper 写进
    ``sys.modules``）。清理按命名空间前缀进行，幂等。
    """
    prefix = f"{namespace}."
    leaked = [name for name in sys.modules if name == namespace or name.startswith(prefix)]
    for name in leaked:
        sys.modules.pop(name, None)


def _load_module(name: str, path: Path) -> ModuleType:
    """在私有命名空间加载源码，包内 _helper 导入不污染全局模块名或 sys.path。"""
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    module = ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = name.rpartition(".")[0]

    def import_local(
        imported: str,
        globals: dict | None = None,
        locals: dict | None = None,
        fromlist: tuple = (),
        level: int = 0,
    ) -> Any:
        if level == 0 and imported.startswith("_") and "." not in imported:
            helper = path.parent / f"{imported}.py"
            if helper.is_file() and not is_link(helper):
                return _load_module(f"{module.__package__}.{imported}", helper)
        return builtins.__import__(imported, globals, locals, fromlist, level)

    module.__dict__["__builtins__"] = {**vars(builtins), "__import__": import_local}
    sys.modules[name] = module
    try:
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _validate_entry(module: ModuleType, input_schema: dict, output_schema: dict) -> None:
    """校验函数 ABI 与声明一致性；索引继续作为工具面唯一来源。"""
    execute = getattr(module, "execute", None)
    if not inspect.iscoroutinefunction(execute):
        raise ManifestValidationError("外部 Operation 必须定义 async def execute(call, services)")
    parameters = list(inspect.signature(execute).parameters.values())
    if (
        len(parameters) != 2
        or [p.name for p in parameters] != ["call", "services"]
        or any(p.kind not in {p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD} for p in parameters)
    ):
        raise ManifestValidationError("外部 Operation 入口必须为 execute(call, services)")
    for name, schema in (("Input", input_schema), ("Output", output_schema)):
        model = getattr(module, name, None)
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise ManifestValidationError(f"外部 Operation 的 {name} 必须是 BaseModel")
        if strip_schema_metadata(model.model_json_schema()) != schema:
            raise ManifestValidationError(f"外部 Operation 的 {name} 与已安装索引不一致")


class PythonOperation(Ability):
    """把模块级函数适配为本地执行对象；服务表仅是当前宿主的依赖注入。"""

    def __init__(
        self,
        member: str,
        module: ModuleType,
        timeout_ms: int,
        *,
        paths: tuple[str, ...] = (),
        scope: EffectivePermission | None = None,
        workspace_root: str | None = None,
        writes: bool = False,
    ) -> None:
        self._member = member
        self._module = module
        self._timeout_ms = timeout_ms
        self._paths = paths
        self._workspace = Path(workspace_root) if workspace_root else None
        self._writes = writes
        self._checker = (
            None
            if scope is None
            else FSPermissionChecker(
                allowed_paths=list(scope.allowed_paths)
                if scope.allowed_paths is not None
                else None,
                denied_paths=list(scope.denied_paths),
                workspace_root=workspace_root,
                require_approval=scope.require_hitl,
                deny_all_paths=scope.allowed_paths == (),
                sandbox_roots=scope.sandbox_roots,
                read_only=scope.fs_read_only or not scope.fs_write,
            )
        )

    @property
    def name(self) -> str:
        """返回包内成员键。"""
        return self._member

    def get_hooks(self) -> list[Any]:
        """作者入口不提供 Hook，权限与审批由运行时装配。"""
        return []

    def check_call(self, call: OperationCall, services: ExecutionServices) -> ActionResult | None:
        """在审批前检查输入，不执行作者函数。"""
        try:
            arguments = self._module.Input.model_validate(call.arguments).model_dump(mode="json")
        except ValidationError as error:
            return _failure(f"Invalid Operation input: {error}")
        if self._checker is not None:
            for name in self._paths:
                value = arguments.get(name)
                if value is None:
                    continue
                if not isinstance(value, str) or not value.strip():
                    return _failure(f"Invalid path argument: {name}")
                path = Path(value)
                if not path.is_absolute() and self._workspace is not None:
                    path = self._workspace / path
                allowed, reason = self._checker.check_access(
                    str(path), "write" if self._writes else "read"
                )
                if not allowed:
                    return _failure(reason or "Path access denied")
        return None

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        """校验输入、应用声明超时并检查结果信封与成功数据。"""
        denied = self.check_call(call, services)
        if denied is not None:
            return denied
        try:
            async with asyncio.timeout(self._timeout_ms / 1000):
                arguments = self._module.Input.model_validate(call.arguments).model_dump(
                    mode="json"
                )
                for name in self._paths:
                    value = arguments.get(name)
                    if value is not None and self._workspace is not None:
                        path = Path(value)
                        arguments[name] = str(
                            (path if path.is_absolute() else self._workspace / path).resolve()
                        )
                result = await self._module.execute(replace(call, arguments=arguments), services)
            if not isinstance(result, ActionResult):
                return _failure("Operation must return ActionResult")
            if not isinstance(result.outcome, ActionOutcome) or not isinstance(result.data, dict):
                return _failure("Operation returned an invalid ActionResult")
            output = getattr(self._module, "Output", None)
            if result.outcome == ActionOutcome.SUCCESS and output is not None:
                output.model_validate(result.data)
            return result
        except TimeoutError:
            return _failure(
                "Operation timed out; effects may have occurred; do not retry automatically"
            )
        except Exception as error:
            return _failure(f"Operation failed: {error}")


def _failure(message: str) -> ActionResult:
    return ActionResult(ActionOutcome.FAILURE, {"error": message})
