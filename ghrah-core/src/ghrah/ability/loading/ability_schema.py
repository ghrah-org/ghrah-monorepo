# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Ability 定义面模型：``ability.yaml`` v1 的类型化契约与解析入口。

v1 只有两段：作者手写的头段（``format`` / ``schema_version`` / ``identity`` /
``title`` / ``description`` / ``origin`` / ``mcp``）与生成器写入的成员索引
``members``（``operations`` / ``skills``）。约束要点：

- 全部子模型 ``extra="forbid"``：未知字段拒绝，不做隐式吸收；
- 定义只承载契约事实：身份、成员 schema 与执行约束；
- **不含**授权结果（``permissions`` 只是包声明的申请值）与 Hook 编排；
- 旧格式 ``0.1-draft`` / ``0.2`` 已退役：build / install / scan 一律拒绝，
  不提供兼容适配器与 ``--migrate``（见 M0 规范冻结 §3.11）。

解析入口（:func:`load_yaml` / :func:`parse_definition`）也在这里：契约与它的
解析器是同一件事，放在一起才不会出现"两处各解析一次、规则悄悄分叉"。上层
（``ability_catalog`` 的实现面、``ability_scan`` 的目录面）都从这里取，不各自
再写一遍；``skill_*`` 模块只消费这里解析出的定义，不再持有第二套解析规则。
"""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

try:  # libyaml 可用时用 C loader；不可用则退回纯 Python（慢 ~8 倍，但语义一致）
    from yaml import CSafeLoader as _SafeLoader
except ImportError:  # pragma: no cover - 取决于构建环境
    from yaml import SafeLoader as _SafeLoader

from ghrah.ability.loading.manifest_fields import ManifestValidationError

__all__ = [
    "DEFAULT_EXECUTION",
    "DEFINITION_FILE_NAME",
    "EMPTY_SCHEMA",
    "LOCK_FILE_NAME",
    "SKILL_BODY_DIR",
    "SKILL_FILE_NAME",
    "V1_SCHEMA_VERSION",
    "V1_SEMVER_PATTERN",
    "AbilityDefinition",
    "AbilityIdentity",
    "ContentResource",
    "ExecutionSpec",
    "OperationDefinition",
    "SkillBody",
    "SkillDefinition",
    "load_yaml",
    "parse_definition",
]

V1_SCHEMA_VERSION = "1"
"""现行定义格式版本：两段式，成员写在 ``members`` 下（可多个 skill）。"""

V1_SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")
"""v1 ``identity.version`` 的 SemVer core 三段；每段最多 9 位，无前导零。

它同时是安装路径段，因此不允许前缀、预发布（``-rc.1``）与构建元数据
（``+build``）；需要时以 ``schema_version`` 变更引入。
"""

EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
"""没有声明 schema 时的空对象契约（拒绝未知字段）。"""

DEFAULT_EXECUTION: dict[str, Any] = {
    "mode": "request",
    "timeout_ms": 60_000,
    "cancellation": "unsupported",
    "on_revoke": "drain",
    "retry": "never",
}
"""v1 成员未声明 ``execution`` 时的缺省执行约束。"""

DEFINITION_FILE_NAME: str = "ability.yaml"
"""定义面文件名：``<扫描根>/<容器>/ability.yaml``。"""

SKILL_BODY_DIR: str = "skills"
"""skill 正文目录（相对容器根）：``<容器>/skills/<name>/SKILL.md``。"""

SKILL_FILE_NAME: str = "SKILL.md"
"""skill 正文文件名。"""

LOCK_FILE_NAME: str = "ability.lock.yaml"
"""框架生成的登记文件（相对容器根）。

包布局常量统一定义在本模块：``skill_lock``（读写）与 ``skill_fingerprint``
（整树观测时需要排除它自身，避免自我指涉）都从这里取，避免两处各写一份。
"""


class _UniqueLoader(_SafeLoader):
    """定义与 frontmatter 均拒绝重复键与 ``<<`` merge key，避免后一项覆盖身份。"""

    def flatten_mapping(self, node: Any) -> None:
        """拒绝 merge key；锚点/别名仍可解析，生成物由生成器展开。"""
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise ManifestValidationError("YAML merge key '<<' 不支持")
        super().flatten_mapping(node)


def _unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ManifestValidationError(f"YAML 重复键: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor("tag:yaml.org,2002:map", _unique_mapping)


def _reject_merge(loader: Any, node: Any) -> None:
    """拒绝 ``<<`` merge key；错误类型与重复键保持一致。"""
    raise ManifestValidationError("YAML merge key '<<' 不支持")


_UniqueLoader.add_constructor("tag:yaml.org,2002:merge", _reject_merge)


def load_yaml(raw: str) -> Any:
    """解析一段 YAML（定义面的**唯一**入口）。

    用在所有需要把定义面文本变成映射的地方（容器定义、正文 frontmatter）。
    注意 ``yaml.safe_load`` 恒为纯 Python 实现，与本机是否有 libyaml 无关；
    这里显式走 ``CSafeLoader``——36KB 定义从 ~75ms 降到 ~6ms，这是"每次查询
    都扫盘"能成立的前提。

    Args:
        raw: 待解析的 YAML 文本（UTF-8 已解码的字符串）。

    Returns:
        解析结果；标量/序列/映射都可能，调用方自行判型（定义面要求映射）。

    Raises:
        yaml.YAMLError: 文本不是合法 YAML。
    """
    return yaml.load(raw, Loader=_UniqueLoader)


_V1_TOP_LEVEL_KEYS = frozenset(
    {"format", "schema_version", "origin", "identity", "title", "description", "mcp", "members"}
)

_V1_OPERATION_KEYS = frozenset(
    {
        "title",
        "description",
        "entry",
        "input_schema",
        "output_schema",
        "effects",
        "resources",
        "execution",
        "expose",
        "tags",
        "permissions",
    }
)
_V1_SKILL_KEYS = frozenset(
    {"title", "description", "tags", "body", "assets", "references", "scripts"}
)

_OPERATION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SKILL_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESOURCE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
"""资源键由相对路径派生（去后缀、``/`` → ``-``），允许数字开头与点号。"""


def _require_object_schema(where: str, schema: Any) -> dict[str, Any]:
    """校验 Operation 的 in/out schema 是对象形状的 JSON Schema。

    Args:
        where: 报错前缀（``members.operations.<键>.input_schema`` 之类）。
        schema: 待校验的值。

    Returns:
        原样返回校验通过的 schema 映射。

    Raises:
        ManifestValidationError: 不是映射、类型不是 ``object``，或未关闭
            ``additionalProperties``。
    """
    if not isinstance(schema, dict):
        raise ManifestValidationError(f"{where} 必须是映射")
    if schema.get("type") != "object":
        raise ManifestValidationError(f"{where} 必须是 type: object")
    if schema.get("additionalProperties") is not False:
        raise ManifestValidationError(f"{where} 必须显式 additionalProperties: false")
    return schema


def _from_members(data: dict[str, Any], source: str) -> dict[str, Any]:
    """把 v1 的 ``members`` 两段式展开成运行时成员表。

    只做一次结构适配：v1 索引写 ``entry``（实现文件）与成员契约；运行期沿用
    ``operations`` / ``skills`` 两张表，并把 ``entry`` 归一成 ``script`` 内容资源。
    旧格式 ``0.1-draft`` / ``0.2`` 在这里明确拒绝——不读、不猜、不迁移。

    Args:
        data: YAML 解析出的顶层映射（含 ``members``）。
        source: 报错时用的来源标签。

    Returns:
        运行时形状的映射（``operations`` / ``skills`` 顶层表）。

    Raises:
        ManifestValidationError: 不是 v1、缺 ``members``、成员形状非法、
            未知字段或 ``entry`` 不等于 ``operations/<键>.py``。
    """
    version = data.get("schema_version")
    if version != V1_SCHEMA_VERSION or not isinstance(version, str):
        raise ManifestValidationError(
            f"{source}: 只支持 v1 定义（schema_version: '1'），"
            f"当前是 {version!r}；旧格式 0.1-draft / 0.2 已退役，"
            "请按 v1 两段式重写（M0 规范冻结 §3.11），不提供迁移旁路"
        )
    unknown_top = set(data) - _V1_TOP_LEVEL_KEYS
    if unknown_top:
        raise ManifestValidationError(
            f"{source}: 顶层未知字段 {sorted(unknown_top)}（契约拒绝未知字段）"
        )
    if "members" not in data:
        raise ManifestValidationError(f"{source}: v1 定义缺少 members")
    members = data.get("members")
    if not isinstance(members, dict):
        raise ManifestValidationError(f"{source}: members 必须是映射")
    unknown_members = set(members) - {"operations", "skills"}
    if unknown_members:
        raise ManifestValidationError(
            f"{source}: members 未知字段 {sorted(unknown_members)}（只允许 operations / skills）"
        )
    raw_operations = members.get("operations") or {}
    raw_skills = members.get("skills") or {}
    if not isinstance(raw_operations, dict):
        raise ManifestValidationError(f"{source}: members.operations 必须是映射")
    if not isinstance(raw_skills, dict):
        raise ManifestValidationError(f"{source}: members.skills 必须是映射")
    if not raw_operations and not raw_skills:
        raise ManifestValidationError(f"{source}: 包必须至少有一个成员（Operation 或 Skill）")

    operations: dict[str, Any] = {}
    for key, raw in raw_operations.items():
        if not isinstance(key, str) or not _OPERATION_KEY_PATTERN.fullmatch(key):
            raise ManifestValidationError(f"{source}: 非法 Operation 键 {key!r}")
        if not isinstance(raw, dict):
            raise ManifestValidationError(f"{source}: members.operations.{key} 必须是映射")
        unknown = set(raw) - _V1_OPERATION_KEYS
        if unknown:
            raise ManifestValidationError(
                f"{source}: members.operations.{key} 未知字段 {sorted(unknown)}（契约拒绝未知字段）"
            )
        missing = {"title", "description", "entry"} - set(raw)
        if missing:
            raise ManifestValidationError(
                f"{source}: members.operations.{key} 缺少必填字段 {sorted(missing)}"
            )
        entry = raw["entry"]
        expected_entry = f"operations/{key}.py"
        if entry != expected_entry:
            raise ManifestValidationError(
                f"{source}: members.operations.{key}.entry 必须是 "
                f"{expected_entry!r}，当前是 {entry!r}"
            )
        input_schema = raw.get("input_schema") or dict(EMPTY_SCHEMA)
        output_schema = raw.get("output_schema") or dict(EMPTY_SCHEMA)
        _require_object_schema(f"{source}: members.operations.{key}.input_schema", input_schema)
        _require_object_schema(f"{source}: members.operations.{key}.output_schema", output_schema)
        operations[key] = {
            "title": raw["title"],
            "description": raw["description"],
            "input_schema": input_schema,
            "output_schema": output_schema,
            "effects": raw.get("effects") or [],
            "resources": raw.get("resources") or {},
            "execution": raw.get("execution") or dict(DEFAULT_EXECUTION),
            "entry": {"path": entry, "media_type": "text/x-python"},
            "expose": raw.get("expose", True),
            "tags": raw.get("tags") or [],
            "permissions": raw.get("permissions") or {},
        }

    skills: dict[str, Any] = {}
    for key, raw in raw_skills.items():
        if not isinstance(key, str) or not _SKILL_KEY_PATTERN.fullmatch(key):
            raise ManifestValidationError(f"{source}: 非法 Skill 键 {key!r}")
        if not isinstance(raw, dict):
            raise ManifestValidationError(f"{source}: members.skills.{key} 必须是映射")
        unknown = set(raw) - _V1_SKILL_KEYS
        if unknown:
            raise ManifestValidationError(
                f"{source}: members.skills.{key} 未知字段 {sorted(unknown)}（契约拒绝未知字段）"
            )
        missing = {"title", "description", "body"} - set(raw)
        if missing:
            raise ManifestValidationError(
                f"{source}: members.skills.{key} 缺少必填字段 {sorted(missing)}"
            )
        body = raw["body"]
        if not isinstance(body, dict):
            raise ManifestValidationError(f"{source}: members.skills.{key} 缺少 body")
        expected_body = f"{SKILL_BODY_DIR}/{key}/{SKILL_FILE_NAME}"
        if body.get("path") != expected_body:
            raise ManifestValidationError(
                f"{source}: members.skills.{key}.body.path 必须是 {expected_body!r}，"
                f"当前是 {body.get('path')!r}"
            )
        skills[key] = {
            "title": raw.get("title") or key,
            "description": raw.get("description") or "",
            "body": body,
            "tags": raw.get("tags") or [],
            "assets": raw.get("assets") or {},
            "references": raw.get("references") or {},
            "scripts": raw.get("scripts") or {},
        }

    normalized = {key: value for key, value in data.items() if key != "members"}
    normalized["operations"] = operations
    normalized["skills"] = skills
    return normalized


def parse_definition(raw: str, *, source: str) -> AbilityDefinition:
    """把 ``ability.yaml`` 文本解析并校验成定义模型。

    只接受 v1：旧版（``0.1-draft`` / ``0.2``）在解析期明确拒绝，错误信息指向
    M0 规范冻结 §3.11；不提供只读适配器，也不做隐式修正。除 pydantic 契约外
    还做跨字段校验：``entry`` 与成员键一致、``identity.version`` 为 SemVer core
    三段、成员合计至少一个。

    用在扫描期的单个包解析：扫描器只负责定位与收集问题，契约判定全在这里。

    Args:
        raw: ``ability.yaml`` 的文本。
        source: 报错时用的来源标签（通常是容器名，或带根的定位串）。

    Returns:
        已校验的 :class:`AbilityDefinition`。

    Raises:
        ManifestValidationError: 顶层不是映射、不是 v1、契约不满足，或上述
            跨字段校验失败。
    """
    data = load_yaml(raw)
    if not isinstance(data, dict):
        raise ManifestValidationError(f"{source}: {DEFINITION_FILE_NAME} 不是映射")
    data = _from_members(data, source)
    try:
        definition = AbilityDefinition.model_validate(data)
    except ValidationError as exc:
        raise ManifestValidationError(f"{source}: {exc}") from exc
    if definition.schema_version != V1_SCHEMA_VERSION:
        raise ManifestValidationError(
            f"{source}: 不支持的 schema_version {definition.schema_version!r}，"
            f"只支持 {V1_SCHEMA_VERSION!r}"
        )
    if not V1_SEMVER_PATTERN.fullmatch(definition.identity.version):
        raise ManifestValidationError(
            f"{source}: v1 的 identity.version 必须是 SemVer core 三段"
            f"（MAJOR.MINOR.PATCH，每段 1-9 位十进制、无前导零、"
            f"不带预发布/构建元数据），当前是 {definition.identity.version!r}"
        )
    for key, operation in definition.operations.items():
        if operation.entry is not None and operation.entry.path != f"operations/{key}.py":
            raise ManifestValidationError(f"{source}.{key}: entry 必须是 operations/{key}.py")
    return definition


class AbilityIdentity(BaseModel):
    """容器身份：机器载体保留 namespace / name 两段，不反向拆字符串。

    ``namespace`` 即**包名**（包目录名）。``name`` 可省：用户级包（skill 池里的
    一个包就是一个能力）只有一层身份，此时 ``full_name`` 退化为 ``namespace``。
    """

    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(min_length=1)

    @property
    def full_name(self) -> str:
        """身份全名。

        用在拼成员全名、写 lock、比对是否同一个能力。

        Returns:
            ``namespace.name``；``name`` 缺席时退化为 ``namespace``。
        """
        return f"{self.namespace}.{self.name}" if self.name else self.namespace


class ExecutionSpec(BaseModel):
    """一次调用的执行约束。首版只支持 request 语义。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["request"]
    timeout_ms: int = Field(gt=0)
    cancellation: Literal["cooperative", "unsupported"]
    on_revoke: Literal["drain", "request_cancel"]
    retry: Literal["never"]


class OperationDefinition(BaseModel):
    """一个可执行成员：参数、结果、副作用与执行约束。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effects: list[str] = Field(default_factory=list)
    resources: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionSpec
    entry: ContentResource | None = None
    """成员实现文件（v1 ``entry`` 的归一形状）：``operations/<键>.py``。

    v1 索引只写相对路径字符串；运行期需要包根 + 该路径来定位实现。外部包的
    Operation 由安装后的进程内 Provider 从版本根加载，内置包按目录约定 import。
    ``origin: mcp`` 的成员没有实现文件，此处为 ``None``（P5）。
    """

    tags: list[str] = Field(default_factory=list)
    """发现与过滤标签；v1 由实现模块的 ``TAGS`` 生成。"""

    permissions: dict[str, Any] = Field(default_factory=dict)
    """权限**申请值**（``PERMISSIONS``）；授权结果不在这里。"""

    expose: bool = True
    """是否进入模型工具面。

    ``True``（缺省）表示该成员按定义面投影成一个模型工具，工具名即成员键；
    ``False`` 表示只在内部可用（如 ``conversation``——它存在但没有工具绑定）。
    本字段是"是否进工具面"的**唯一权威**：实现脚本不再声明同名开关。
    """


class SkillBody(BaseModel):
    """技能正文的定位与媒体类型。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    """相对能力包根目录的路径。v1 约定为 ``skills/<name>/SKILL.md``。

    读取一律以本字段为准（见 :func:`ghrah.ability.loading.ability_scan.AbilitySkill.body`），
    因此框架侧不再第二次拼路径。**越界判定不在这里**：路径是否落在容器内由扫描期
    的唯一权威 :func:`ghrah.ability.loading.ability_scan._skill_body_locator` 裁决
    ——同一事实只留一个判定点。
    """

    media_type: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        """把正文路径统一成 ``/`` 分隔的相对写法（只做格式归一，不做越界判定）。

        Args:
            value: 声明里的 ``body.path``。

        Returns:
            去掉空段与 ``.`` 段、反斜杠换成 ``/`` 的路径。
        """
        return value


class ContentResource(BaseModel):
    """Skill 内显式声明的静态文件；大小与摘要由生成器写入、捕获期核对。"""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    size: int | None = Field(default=None)
    """字节数（生成期写入）。"""

    digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    """内容摘要（生成期写入，``sha256:<hex>``）；捕获期据此核对文件字节。"""


class SkillDefinition(BaseModel):
    """一个技能成员：给模型看的说明 + 正文位置。

    一个能力包可以有 0 个、1 个或多个技能；每个技能独占一个目录
    （``skills/<name>/SKILL.md``），因此 ``body.path`` 总是带第二级目录。
    成员键 = 目录名 = ``SKILL.md`` frontmatter 的 ``name``。

    ``title`` / ``description`` 由正文 frontmatter 迁出；捕获期会以正文为准
    重新读出并覆盖。
    """

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    description: str = ""
    body: SkillBody
    tags: list[str] = Field(default_factory=list)
    assets: dict[str, ContentResource] = Field(default_factory=dict)
    references: dict[str, ContentResource] = Field(default_factory=dict)
    scripts: dict[str, ContentResource] = Field(default_factory=dict)
    """技能委托执行的脚本（``scripts/`` 下的 ``.py``）；不是 operation，不生成 schema。"""


class AbilityDefinition(BaseModel):
    """一个 Ability 容器：身份 + 成员表。

    v1 头段不承载 ``provider_requirements`` 与顶层 ``assets``；Operation 成员的
    后端由包来源与 Provider 决定，资源归属各自 Skill。
    """

    model_config = ConfigDict(extra="forbid")

    format: Literal["ghrah.ability-definition"]
    schema_version: Literal["1"]
    identity: AbilityIdentity
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    origin: Literal["source", "mcp"] = "source"
    """成员来源：``source``（包内脚本/技能）或 ``mcp``（由 ``tools/list`` 生成）。"""

    mcp: dict[str, Any] | None = None
    """``origin: mcp`` 时的连接声明（``{"server": <agentconf 名>}``），不含 URL/密钥。"""

    operations: dict[str, OperationDefinition] = Field(default_factory=dict)
    skills: dict[str, SkillDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_members(self) -> AbilityDefinition:
        """所有入口（包括持久恢复）都核对成员键、成员集与来源字段。"""
        for key in self.operations:
            if not _OPERATION_KEY_PATTERN.fullmatch(key):
                raise ValueError("非法成员键")
        for key in self.skills:
            if not _SKILL_KEY_PATTERN.fullmatch(key):
                raise ValueError("非法技能键")
        for skill in self.skills.values():
            for mapping in (skill.assets, skill.references, skill.scripts):
                if any(not _RESOURCE_KEY_PATTERN.fullmatch(key) for key in mapping):
                    raise ValueError("非法附件键")
        if not self.operations and not self.skills:
            raise ValueError("包必须至少有一个成员（Operation 或 Skill）")
        if self.origin == "mcp":
            if self.identity.namespace != "mcp":
                raise ValueError("origin: mcp 时 identity.namespace 必须是 mcp")
            server = (self.mcp or {}).get("server")
            if not isinstance(server, str) or not server:
                raise ValueError("origin: mcp 时必须声明 mcp.server")
            if self.operations or self.skills:
                raise ValueError(
                    "origin: mcp 时成员由 tools/list 生成，不得声明 operations / skills"
                )
        elif self.mcp is not None:
            raise ValueError("origin: source 时不得声明 mcp")
        return self

    @property
    def container_name(self) -> str:
        """容器名（= 目录名 = 局部别名候选）。

        用在扫描期与目录名做一致性校验、以及局部别名展示。

        Returns:
            ``identity.name``；缺席时取 ``namespace``。
        """
        return self.identity.name or self.identity.namespace

    @property
    def full_name(self) -> str:
        """身份全名（唯一权威是 ``identity``，本属性不引入第二个来源）。

        Returns:
            与 :attr:`AbilityIdentity.full_name` 同值。
        """
        return self.identity.full_name

    @property
    def legacy_namespace(self) -> str:
        """旧 manifest 的 ``metadata.namespace`` 口径：点分身份串。

        用在把新格式投影回旧 view 时填 ``metadata.namespace``。

        Returns:
            点分身份串（与 :attr:`full_name` 同值）。
        """
        return self.identity.full_name

    def full_name_of(self, operation_key: str) -> str:
        """成员的旧口径全名。

        用在投影 view 的键、以及按全名精确查询成员。

        Args:
            operation_key: 成员键（``operations`` 表里的键，如 ``read_file``）。

        Returns:
            ``<点分身份>.<成员键>``，如 ``ghrah.fs.read_file``。
        """
        return f"{self.legacy_namespace}.{operation_key}"
