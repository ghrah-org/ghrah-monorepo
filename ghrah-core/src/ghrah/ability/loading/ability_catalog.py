# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Ability 装载器：内置树扫描 → 定义解析 → 运行时 view 投影。

权威关系（单向，可重建）：

    ability/<容器>/ability.yaml     定义面（契约事实）
    ability/<容器>/operations/*.py  实现面（实现 + PERMISSIONS/SUMMARY/TAGS）
              │
              ▼
    AbilityManifest                 运行时 view：由上面两者投影而来，不被写回

两面的成员必须双向一一对应：声明了却没有 ``operations/<键>.py``、或有脚本却
没在定义面声明，都在装载时直接失败（:func:`load_ability_definitions`），
不静默忽略、也不拖到第一次调用才暴露。

扫描根由 ``ability_roots`` 的目录池给出；本模块只用**内置树**那一项
（``ability_roots.builtin_pool()``）——外部包没有实现脚本，落不进实现面，
它们的定义与 skill 成员由 ``ability_scan`` 的目录面读。

``ghrah.ability.manifest_builtins`` 只是本模块的旧口径门面。
定义与视图按查询重建，没有 ``cache_clear()`` 入口；Python 实现模块仍按
``import_module`` 的模块缓存规则复用，不因定义重扫而自动重载。
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from importlib.resources.abc import Traversable
from types import ModuleType
from typing import Any

from pydantic import ValidationError

from ghrah.ability.execution.base import Ability
from ghrah.ability.loading import ability_roots as roots
from ghrah.ability.loading.ability_roots import AbilityRoot
from ghrah.ability.loading.ability_scan import AbilityScanResult, scan_ability_definitions
from ghrah.ability.loading.ability_schema import (
    DEFINITION_FILE_NAME,
    AbilityDefinition,
    OperationDefinition,
    load_yaml,
)
from ghrah.ability.loading.ability_view import AbilityManifest, AbilityMetadata
from ghrah.ability.loading.manifest_fields import (
    ImplementationDef,
    ManifestValidationError,
    PermissionFlags,
    ToolParameter,
    ToolSchema,
)

__all__ = [
    "ABILITY_DEFINITION_FILE",
    "REGISTRY_ONLY_OPERATIONS",
    "ability_class",
    "builtin_full_names",
    "builtin_handlers",
    "builtin_pool",
    "build_tool_schema",
    "create_ability",
    "has_handler",
    "load_all_builtin_manifests",
    "load_ability_definitions",
    "load_builtin_manifest",
    "load_definition",
    "load_yaml",
    "operation_containers",
]

ABILITY_DEFINITION_FILE = DEFINITION_FILE_NAME
"""容器定义文件名。"""

LEGACY_MANIFEST_VERSION = "1"
"""投影出的旧 manifest 的**格式版本**（非能力定义版本）。"""

REGISTRY_ONLY_OPERATIONS: frozenset[str] = frozenset({"send"})
"""只在 registry 存在、不走声明路径的成员。

``send`` 由 Core 的 room 装配期注入，历史上没有 manifest 声明；投影时跳过它
以保持既有语义（``list_abilities`` 的 registry-only 差异面依赖这一点）。
它仍是 ``cluster/ability.yaml`` 里的正式成员，只是不产出运行时 view。
"""

OPERATIONS_DIR_NAME = "operations"
"""成员实现脚本所在目录名（相对容器目录）。

约定：``operations/<成员键>.py`` 与 ``ability.yaml`` 的 ``operations`` 键一一对应，
脚本名就是成员键。唯一例外是包标记 ``__init__.py``，它不承载成员。
"""

_IGNORED_OPERATION_MODULES: frozenset[str] = frozenset({"__init__"})
"""``operations/`` 下不承载成员的模块名（包标记）。"""


def builtin_pool() -> tuple[AbilityRoot, ...]:
    """实现面的目录池：只有内置树。

    独立成函数（而不是直接用 ``ability_roots.builtin_pool``）是为了让测试能把
    内置根换到临时目录，等价于「换一套内置能力树」，不必改动真实包树。

    Returns:
        单元素池：内置树。
    """
    return roots.builtin_pool()


def _implemented_member_keys(container_locator: Traversable) -> frozenset[str]:
    """读出容器 ``operations/`` 下的成员键（不含包标记 ``__init__``）。

    脚本名即成员键，因此这里只做文件名到键的收敛，不 import、不解析脚本内容
    ——存在性校验要在投影之前完成。

    Args:
        container_locator: 容器目录句柄（内置树里的 ``ability/<容器>/``）。

    Returns:
        成员键集合；``operations/`` 不存在时为空集。
    """
    operations_dir = container_locator.joinpath(OPERATIONS_DIR_NAME)
    if not operations_dir.is_dir():
        return frozenset()
    keys: set[str] = set()
    for item in operations_dir.iterdir():
        if not item.is_file() or not item.name.endswith(".py"):
            continue
        key = item.name.removesuffix(".py")
        if key in _IGNORED_OPERATION_MODULES:
            continue
        keys.add(key)
    return frozenset(keys)


def _verify_member_existence(result: AbilityScanResult) -> None:
    """校验定义面成员与 ``operations/*.py`` 双向一一对应。

    两个方向都是定义面与实现面失去对应，必须在装配期直接失败：

    - **声明了但没有实现脚本**：此前只在投影 import 时才报，错误信息落在
      「无法装载实现脚本」上，看不出是缺文件还是脚本本身坏了；
    - **有实现脚本但没有声明**：此前完全静默——脚本不会被任何投影引用，
      看起来"没影响"，实则是一份不会被加载、也不会被发现的能力实现。

    Args:
        result: 内置树的一轮扫描结果（每个条目带容器目录句柄）。

    Raises:
        ManifestValidationError: 任一容器的两个方向存在差集；一次报全部容器，
            便于在同一轮装配里把缺口看全。
    """
    problems: list[str] = []
    for entry in result.entries:
        declared = set(entry.definition.operations)
        implemented = set(_implemented_member_keys(entry.locator))
        missing = sorted(declared - implemented)
        undeclared = sorted(implemented - declared)
        if not missing and not undeclared:
            continue
        lines = [f"ability 容器 {entry.container!r} 的成员声明与实现脚本不一致："]
        if missing:
            lines.append(f"  声明了但没有实现脚本：{', '.join(missing)}")
        if undeclared:
            lines.append(f"  有实现脚本但没有声明：{', '.join(undeclared)}")
        lines.append(
            f"  约定：{OPERATIONS_DIR_NAME}/<成员键>.py 与 ability.yaml 的 "
            "operations 键必须一一对应（包标记 __init__.py 除外）。"
        )
        problems.append("\n".join(lines))
    if problems:
        raise ManifestValidationError("\n".join(problems))


def load_ability_definitions() -> Mapping[str, AbilityDefinition]:
    """扫描内置树并解析全部容器定义（实现面的严格读数）。

    用在需要「现在有哪些能力、各自声明了什么」且**必须可信**的场景：投影 view、
    handler 解析、``list_abilities``、装配前体检。外部目录里的包不在这里——
    它们由 ``ability_scan.scan_ability_definitions()`` 的目录面负责。

    与目录面的差别只有两条：范围只到内置树，且遇错**直接抛**（内置树是源码，
    坏了就是发布事故，不能降级成一条问题继续跑）。

    解析成功后还会做一次成员存在性校验：``operations/<成员键>.py`` 与定义面的
    ``operations`` 键必须双向一一对应，缺脚本或脚本未声明都在这里失败，
    不拖到投影 import 才暴露、也不静默忽略。

    **每次调用都重新扫描内置定义**（无结果缓存），不读取用户目录池。
    定义更新不等于重载已导入的 Python 实现或替换在用实例；YAML loader
    由 ``ability_schema`` 统一选择。

    Returns:
        ``{容器名: AbilityDefinition}``；容器名即 ``ability/<容器>/`` 的目录名。

    Raises:
        ManifestValidationError: 定义缺失/非法、schema_version 不受支持、
            容器名与目录名不一致，或成员声明与实现脚本不一一对应。
    """
    result = scan_ability_definitions(builtin_pool())
    if result.issues:
        issue = result.issues[0]
        raise ManifestValidationError(str(issue))
    _verify_member_existence(result)
    return result.definitions


def load_definition(container: str) -> AbilityDefinition:
    """按容器名取**单个**容器的定义。

    用在已知道容器名、只想读它一家的场景（例如按容器展示成员清单）。

    Args:
        container: 容器名（``ability/<容器>/`` 的目录名，如 ``fs``）。

    Returns:
        该容器的 :class:`AbilityDefinition`。

    Raises:
        ManifestValidationError: 没有这个容器。
    """
    try:
        return load_ability_definitions()[container]
    except KeyError:
        raise ManifestValidationError(f"未知的 ability 容器：{container!r}") from None


def _operation_module(container: str, operation_key: str) -> ModuleType:
    """按路径约定 import 实现脚本（惰性：只在投影时发生）。

    约定路径：``ghrah.ability.builtin.<容器>.operations.<成员键>``。

    Args:
        container: 容器名（目录名）。
        operation_key: 成员键。

    Returns:
        已导入的实现模块。

    Raises:
        ManifestValidationError: 模块不存在或导入失败——这是「实现脚本必须落在
            可 import 的包树里」这一约束的具体报错点，报错会指名缺失的模块。
    """
    dotted = f"ghrah.ability.builtin.{container}.operations.{operation_key}"
    try:
        return import_module(dotted)
    except ImportError as exc:
        raise ManifestValidationError(
            f"{container}.{operation_key}: 无法装载实现脚本 {dotted}: {exc}"
        ) from exc


def _operation_class(module: ModuleType) -> type[Ability]:
    """取该脚本中定义的 Ability 子类（约定：一个脚本一个实现）。

    只认 ``obj.__module__ == module.__name__`` 的类，避免把 import 进来的基类
    或他人实现误当成本脚本的实现。

    Args:
        module: 已导入的实现脚本模块。

    Returns:
        该脚本里唯一的 :class:`Ability` 子类。

    Raises:
        ManifestValidationError: 子类数量不为 1（0 个或 2 个都拒绝，不猜）。
    """
    found = [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type)
        and issubclass(obj, Ability)
        and obj is not Ability
        and obj.__module__ == module.__name__
    ]
    if len(found) != 1:
        raise ManifestValidationError(
            f"{module.__name__}: 期望恰好一个 Ability 实现类，实际 {len(found)} 个"
        )
    return found[0]


def _json_type(raw: Any) -> str:
    """把 JSON Schema 的 ``type`` 收成单一类型名。

    用在工具参数转换：只支持单类型，以及「T 或 null」形式的可空类型。

    Args:
        raw: ``type`` 字段原值（字符串或列表）。

    Returns:
        单一类型名（如 ``string``）。

    Raises:
        ManifestValidationError: 真联合类型（除 null 外多于一个）——工具面无法
            表达，拒绝而不是悄悄取第一个。
    """
    if isinstance(raw, list):
        primitives = [item for item in raw if item != "null"]
        if len(primitives) != 1:
            raise ManifestValidationError(f"不支持的联合类型：{raw!r}")
        return str(primitives[0])
    if raw is None:
        raise ManifestValidationError("属性缺少 type")
    return str(raw)


def _normalize_property(prop: Mapping[str, Any]) -> Mapping[str, Any]:
    """把 pydantic 生成的 ``anyOf: [T, null]`` 归一成 ``T`` 的属性 schema。

    pydantic 对 ``T | None`` 生成 ``anyOf: [{type: T}, {type: "null"}]``，与
    ``type: ["T", "null"]`` 是同一事实的两种表示。工具面只表达"可空参数"，
    因此取非 null 分支的约束（``enum`` / ``minimum`` / ``maximum`` / ``items``），
    外层字段（``description`` / ``default``）优先。

    Args:
        prop: ``input_schema.properties.<名>`` 的原始 schema。

    Returns:
        合并后的属性 schema；不是可空联合时原样返回。

    Raises:
        ManifestValidationError: ``anyOf`` 里除 null 外不是恰好一个分支。
    """
    raw_any = prop.get("anyOf")
    if prop.get("type") is not None or not isinstance(raw_any, list):
        return prop
    branches = [
        item
        for item in raw_any
        if isinstance(item, Mapping) and item.get("type") not in {None, "null"}
    ]
    nulls = [item for item in raw_any if isinstance(item, Mapping) and item.get("type") == "null"]
    if len(branches) != 1 or not nulls:
        raise ManifestValidationError(f"不支持的联合类型：{raw_any!r}")
    merged = dict(branches[0])
    merged.update({key: value for key, value in prop.items() if key != "anyOf"})
    return merged


def _to_tool_parameter(prop: Mapping[str, Any], *, required: bool) -> ToolParameter:
    """把 JSON Schema 的一条属性转成工具参数模型（``items`` 递归转换）。

    用在 :func:`_build_tool` 组装入参表。

    Args:
        prop: 单条属性 schema（来自 ``input_schema.properties.<名>``）。
        required: 该属性是否列在 ``input_schema.required`` 里。

    Returns:
        对应的 :class:`ToolParameter`；``items`` 是对象时递归转换。
    """
    prop = _normalize_property(prop)
    items_raw = prop.get("items")
    return ToolParameter(
        type=_json_type(prop.get("type")),
        description=str(prop.get("description") or ""),
        required=required,
        default=prop.get("default"),
        enum=[str(x) for x in prop["enum"]] if prop.get("enum") else None,
        minimum=prop.get("minimum"),
        maximum=prop.get("maximum"),
        items=_to_tool_parameter(items_raw, required=False)
        if isinstance(items_raw, dict)
        else None,
        sensitive=False,
    )


def _build_tool(
    operation_key: str, operation: OperationDefinition, *, enabled: bool
) -> ToolSchema | None:
    """由成员的 ``input_schema`` 组装模型工具面的 schema。

    工具名取成员键（不是全名）：模型看到的就是 ``read_file`` 这类短名。

    Args:
        operation_key: 成员键，用作工具名。
        operation: 该成员的定义（取 ``description`` 与 ``input_schema``）。
        enabled: 是否进工具面（取自定义面的 ``expose``）。

    Returns:
        :class:`ToolSchema`；``enabled`` 为 False 时返回 None。
    """
    if not enabled:
        return None
    schema = operation.input_schema
    properties = schema.get("properties") or {}
    required_names = set(schema.get("required") or [])
    parameters = {
        name: _to_tool_parameter(prop, required=name in required_names)
        for name, prop in properties.items()
        if isinstance(prop, dict)
    }
    return ToolSchema(name=operation_key, description=operation.description, parameters=parameters)


def build_tool_schema(operation_key: str, operation: OperationDefinition) -> ToolSchema | None:
    """按定义面组装模型工具 schema（公开入口）。

    用在「只要工具面、不要运行时 view」的场景：外部列举工具、注入模型上下文、
    与 ``list_abilities`` 的展示对齐。与 :func:`_project` 共用同一个转换，
    因此工具面只有一个形状，不会出现两套参数契约。

    Args:
        operation_key: 成员键，用作工具名。
        operation: 该成员的定义（取 ``description`` / ``input_schema`` / ``expose``）。

    Returns:
        :class:`ToolSchema`；``operation.expose`` 为 False 时返回 None。
    """
    return _build_tool(operation_key, operation, enabled=operation.expose)


def _project(
    definition: AbilityDefinition, operation_key: str, operation: OperationDefinition
) -> AbilityManifest:
    """定义面 + 实现面 → 运行时 view（本模块的投影核心）。

    成员事实（``title`` / ``description`` / ``tags`` / ``permissions``）一律取自
    生成索引 ``members.operations.<键>``，不再从实现模块读第二份声明；实现脚本
    只负责"确实存在且恰好一个 Ability 实现类"这一条约定。这正是 P1 的
    "索引是成员事实的唯一来源"。

    挂载面不携带 hook 引用：``AbilityManifest.hooks`` 仅供读取旧 manifest，
    内置树投影不填充（安全语义由索引里的 ``permissions`` 申请值、内核固定实现）。

    Args:
        definition: 该成员所属容器的定义。
        operation_key: 成员键。
        operation: 该成员的定义。

    Returns:
        该成员的运行时 view（旧 manifest 形态），喂给展示与工具面注入。

    Raises:
        ManifestValidationError: 实现脚本装载失败、实现类不唯一，或索引里的
            ``permissions`` 不是合法的 ``PermissionFlags``。
    """
    module = _operation_module(definition.container_name, operation_key)
    # 约定校验：一个脚本恰好定义一个有且只有一个 Ability 实现类。
    _operation_class(module)

    try:
        permissions = PermissionFlags.model_validate(operation.permissions)
    except ValidationError as exc:
        raise ManifestValidationError(
            f"{definition.container_name}.{operation_key}: permissions 非法：{exc}"
        ) from exc

    return AbilityManifest(
        manifest="ability",
        version=LEGACY_MANIFEST_VERSION,
        metadata=AbilityMetadata(
            namespace=definition.legacy_namespace,
            name=operation_key,
            title=operation.title,
            description=operation.description,
            tags=[str(tag) for tag in operation.tags],
            permissions=permissions,
        ),
        tool=_build_tool(operation_key, operation, enabled=operation.expose),
        implementation=ImplementationDef(type="builtin", handler=operation_key),
    )


def load_all_builtin_manifests() -> Mapping[str, AbilityManifest]:
    """全部运行时 view（定义面 + 实现面投影的结果）。

    用在「要展示 / 要注入模型工具面」的场景：``list_abilities``、
    manifest store、``catalog.scan_catalog``。工具 schema 就在 view 的 ``tool``。

    **每次调用都重新读取内置定义并投影**（无结果缓存）。已导入的 Python
    模块仍复用模块缓存，不自动更新已有工具绑定或执行实例。

    Returns:
        ``{全名: AbilityManifest}``，键为旧口径全名（``ghrah.fs.read_file``）；
        不含 registry-only 成员（``send``）。

    Raises:
        ManifestValidationError: 定义非法，或实现脚本装载失败。
    """
    manifests: dict[str, AbilityManifest] = {}
    for container, definition in load_ability_definitions().items():
        for operation_key, operation in definition.operations.items():
            if operation_key in REGISTRY_ONLY_OPERATIONS:
                continue
            full_name = definition.full_name_of(operation_key)
            manifests[full_name] = _project(definition, operation_key, operation)
    return manifests


def builtin_full_names() -> tuple[str, ...]:
    """声明路径上的成员全名。

    用在需要「声明面名单」的场景（原 ``_BUILTIN_YAML_NAMES`` 的替代，
    现算而非 import 期快照）。

    Returns:
        全名元组，顺序由容器与成员声明顺序决定；不含 registry-only 成员。
    """
    names: list[str] = []
    for definition in load_ability_definitions().values():
        for operation_key in definition.operations:
            if operation_key in REGISTRY_ONLY_OPERATIONS:
                continue
            names.append(definition.full_name_of(operation_key))
    return tuple(names)


# ---------------------------------------------------------------------------
# 扫盘式解析：handler → 实现类 / 实例
#
# 取代原 ``AbilityRegistry``（类型名 → 类的进程内注册表）。唯一权威是定义面：
# handler 落在哪个容器的 ``operations`` 表里，实现脚本就在
# ``ability/<容器>/operations/<handler>.py``，类就是该脚本里唯一的 Ability 子类。
# 因此不需要任何 import 期注册，热插拔自然生效。
# ---------------------------------------------------------------------------


def operation_containers() -> Mapping[str, str]:
    """handler → 容器名（扫盘派生）。

    用在「按 handler 名反查它属于哪个容器」——实例化与全名拼接都要先走这一步。
    含 ``REGISTRY_ONLY_OPERATIONS``：它们是**有实现、不进工具面**的成员，
    仍必须可解析（例如 ``send`` 是控制面原语）。

    Returns:
        ``{handler: 容器名}``；每次调用重新扫盘，不缓存。
    """
    index: dict[str, str] = {}
    for container, definition in load_ability_definitions().items():
        for operation_key in definition.operations:
            index[operation_key] = container
    return index


def builtin_handlers() -> tuple[str, ...]:
    """全部可解析的 handler 名。

    用在需要「能力类型全集」的场景：``available_ability_types``、
    registry-only 差异面（``list_abilities``）。

    Returns:
        升序元组（确定性，便于比对与展示）；含 registry-only 成员。
    """
    return tuple(sorted(operation_containers()))


def has_handler(handler: str) -> bool:
    """该 handler 是否在定义面声明过（扫盘判定）。

    用在「先校验再实例化」的场景，替代原 ``AbilityRegistry.has``。

    Args:
        handler: 能力 handler 名（如 ``read_file``）。

    Returns:
        是否已声明；每次调用重新扫盘。
    """
    return handler in operation_containers()


def ability_class(handler: str) -> type[Ability]:
    """按 handler 取实现类（不实例化）。

    用在只想要类型、不需要实例的场景（类型检查、注册面比对、测试断言）。

    Args:
        handler: 能力 handler 名。

    Returns:
        定义面声明的容器下、实现脚本里唯一的 :class:`Ability` 子类。

    Raises:
        ManifestValidationError: handler 未在定义面声明，实现脚本装载失败，
            或脚本里的 Ability 子类不唯一（约定「一个脚本一个实现」）。
    """
    container = operation_containers().get(handler)
    if container is None:
        raise ManifestValidationError(
            f"未知的 ability handler: {handler!r}。已知: {list(builtin_handlers())}"
        )
    return _operation_class(_operation_module(container, handler))


def create_ability(handler: str, **params: Any) -> Ability:
    """按 handler 实例化实现（原 ``AbilityRegistry.create`` 的唯一替代入口）。

    用在「要一个能跑的能力实例」的所有场景：spawn 装配、注册到 Agent、
    测试里直接构造。唯一权威是定义面——不需要任何 import 期注册。

    Args:
        handler: 能力 handler 名（如 ``execute_command``）。
        **params: 传给实现类构造函数的参数（如 ``permission_checker`` /
            ``command_checker`` / ``hooks``）；不同实现接受的参数不同。

    Returns:
        已构造的 :class:`Ability` 实例。

    Raises:
        ManifestValidationError: handler 未知，或脚本里的实现类不唯一。
        TypeError: ``params`` 与实现类构造函数不匹配。
    """
    return ability_class(handler)(**params)


def load_builtin_manifest(full_name: str) -> AbilityManifest:
    """按全名取**一份** view。

    用在已知道全名的精确查询（单条展示、按名取工具 schema）。

    Args:
        full_name: 成员全名（``ghrah.fs.read_file``）。

    Returns:
        该成员的 :class:`AbilityManifest`。

    Raises:
        FileNotFoundError: 没有这个全名（沿用旧门面语义，便于调用方区分
            "查不到"与"定义非法"）。
    """
    try:
        return load_all_builtin_manifests()[full_name]
    except KeyError:
        raise FileNotFoundError(f"未知的内置 ability 全名：{full_name!r}") from None


# ---------------------------------------------------------------------------
# 旧口径门面（原 manifest/builtins）：内置声明不再来自随包 yaml 清单，
# 而是由上面的目录扫描派生。
# ---------------------------------------------------------------------------
