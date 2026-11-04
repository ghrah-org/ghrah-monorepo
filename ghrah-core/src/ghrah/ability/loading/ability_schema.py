# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Ability 定义面模型：``ability.yaml`` 的类型化契约与解析入口。

对应设计草案的 ``ghrah.ability-definition``。约束要点：

- 全部子模型 ``extra="forbid"``：未知字段拒绝，不做隐式吸收；
- 定义只承载契约事实：身份、成员 schema、提供者槽要求、执行约束；
- **不含**授权结果（``permissions``）与 Hook 编排（``hooks``）——前者属授权
  记录，后者随实现。二者暂存于 ``operations/<key>.py`` 的模块级常量。

``schema_version`` 为 ``0.1-draft``；未知版本拒绝。

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
    "SCHEMA_VERSION",
    "SCRIPT_PROVIDER_SLOT",
    "SKILL_BODY_DIR",
    "SKILL_FILE_NAME",
    "V1_SCHEMA_VERSION",
    "V1_SEMVER_PATTERN",
    "AbilityDefinition",
    "AbilityIdentity",
    "ExecutionSpec",
    "OperationDefinition",
    "OperationImplementation",
    "ProviderRequirement",
    "SkillBody",
    "SkillDefinition",
    "load_yaml",
    "parse_definition",
]

SCHEMA_VERSION = "0.1-draft"
"""历史定义格式版本（``operations`` / ``skills`` 顶层表）。"""

V1_SCHEMA_VERSION = "1"
"""现行定义格式版本：两段式，成员写在 ``members`` 下（可多个 skill）。"""

V1_SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")
"""v1 ``identity.version`` 的 SemVer core 三段；每段最多 9 位，无前导零。

它同时是安装路径段，因此不允许前缀、预发布（``-rc.1``）与构建元数据
（``+build``）；需要时以 ``schema_version`` 变更引入。
"""

SCRIPT_PROVIDER_SLOT = "script"
"""包内脚本成员的提供者槽名。"""

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
"""skill 正文目录（相对容器根）：``<容器>/skills/SKILL.md``。"""

SKILL_FILE_NAME: str = "SKILL.md"
"""skill 正文文件名。"""

LOCK_FILE_NAME: str = "ability.lock.yaml"
"""框架生成的登记文件（相对容器根）。

包布局常量统一定义在本模块：``skill_lock``（读写）与 ``skill_fingerprint``
（整树观测时需要排除它自身，避免自我指涉）都从这里取，避免两处各写一份。
"""


class _UniqueLoader(_SafeLoader):
    """定义与 frontmatter 均拒绝重复键，避免后一项覆盖身份。"""


def _unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ManifestValidationError(f"YAML 重复键: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor("tag:yaml.org,2002:map", _unique_mapping)


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


_V1_OPERATION_KEYS = frozenset(
    {
        "title",
        "description",
        "entry",
        "entry_media_type",
        "provider",
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
    {"title", "description", "tags", "body", "assets", "references", "scripts", "annotations"}
)


def _from_members(data: dict[str, Any]) -> dict[str, Any]:
    """把 v1 的 ``members`` 两段式展开成运行时成员表。

    v1 只写「作者与生成器共同维护的成员事实」：operations 的 schema/标签/权限/执行
    约束，skills 的正文与三类附件。历史格式（``0.1-draft`` / ``0.2``）把成员直接放在
    顶层，这里仅对 v1 做一次结构适配，其余字段原样保留。

    Args:
        data: YAML 解析出的顶层映射（含 ``members``）。

    Returns:
        运行时形状的映射（``operations`` / ``skills`` 顶层表）。

    Raises:
        ManifestValidationError: ``members`` 或某个成员不是映射、技能缺 ``body``、
            ``members.skills`` 不是映射（不接受列表简写）、v1 operation 声明
            ``errors``（冻结裁决：错误表不进 v1）。
    """
    members = data.get("members") or {}
    if not isinstance(members, dict):
        raise ManifestValidationError("members 必须是映射")

    operations: dict[str, Any] = {}
    raw_operations = members.get("operations") or {}
    if not isinstance(raw_operations, dict):
        raise ManifestValidationError("members.operations 必须是映射")
    for key, raw in raw_operations.items():
        if not isinstance(raw, dict):
            raise ManifestValidationError(f"members.operations.{key} 必须是映射")
        unknown = set(raw) - _V1_OPERATION_KEYS
        if unknown:
            raise ManifestValidationError(
                f"members.operations.{key}: 未知字段 {sorted(unknown)}（契约拒绝未知字段）"
            )
        entry = raw.get("entry")
        slot = raw.get("provider") or SCRIPT_PROVIDER_SLOT
        operations[key] = {
            "title": raw.get("title") or key,
            "description": raw.get("description") or raw.get("title") or key,
            "input_schema": raw.get("input_schema") or dict(EMPTY_SCHEMA),
            "output_schema": raw.get("output_schema") or dict(EMPTY_SCHEMA),
            "implementation": {"provider_slot": slot, "operation": key},
            "effects": raw.get("effects") or [],
            "resources": raw.get("resources") or {},
            "execution": raw.get("execution") or dict(DEFAULT_EXECUTION),
            "script": (
                {"path": entry, "media_type": raw.get("entry_media_type") or "text/x-python"}
                if isinstance(entry, str) and entry
                else None
            ),
            "expose": raw.get("expose", True),
            "tags": raw.get("tags") or [],
            "permissions": raw.get("permissions") or {},
        }

    skills: dict[str, Any] = {}
    raw_skills = members.get("skills") or {}
    if not isinstance(raw_skills, dict):
        raise ManifestValidationError("members.skills 必须是映射")
    for key, raw in raw_skills.items():
        if not isinstance(raw, dict) or not isinstance(key, str):
            raise ManifestValidationError("members.skills 的每项必须是有键的映射")
        unknown = set(raw) - _V1_SKILL_KEYS
        if unknown:
            raise ManifestValidationError(
                f"members.skills.{key}: 未知字段 {sorted(unknown)}（契约拒绝未知字段）"
            )
        body = raw.get("body")
        if not isinstance(body, dict):
            raise ManifestValidationError(f"members.skills.{key} 缺少 body")
        skills[key] = {
            "title": raw.get("title") or "",
            "description": raw.get("description") or "",
            "body": body,
            "tags": raw.get("tags") or [],
            "assets": raw.get("assets") or {},
            "references": raw.get("references") or {},
            "scripts": raw.get("scripts") or {},
            "annotations": raw.get("annotations") or {},
        }

    normalized = {key: value for key, value in data.items() if key != "members"}
    normalized["operations"] = operations
    normalized["skills"] = skills
    return normalized


def parse_definition(raw: str, *, source: str) -> AbilityDefinition:
    """把 ``ability.yaml`` 文本解析并校验成定义模型。

    除契约（pydantic）外还做跨字段校验：``schema_version`` 受支持、
    ``implementation.operation`` 必须等于成员键、``provider_slot`` 必须在
    ``provider_requirements`` 里声明过；v1 额外校验 ``identity.version``
    为 SemVer core 三段。

    用在扫描期的单个包解析：扫描器只负责定位与收集问题，契约判定全在这里。

    Args:
        raw: ``ability.yaml`` 的文本。
        source: 报错时用的来源标签（通常是容器名，或带根的定位串）。

    Returns:
        已校验的 :class:`AbilityDefinition`。

    Raises:
        ManifestValidationError: 顶层不是映射、契约不满足、版本不受支持，
            或上述跨字段校验失败。
    """
    data = load_yaml(raw)
    if not isinstance(data, dict):
        raise ManifestValidationError(f"{source}: {DEFINITION_FILE_NAME} 不是映射")
    if str(data.get("schema_version")) == V1_SCHEMA_VERSION or "members" in data:
        data = _from_members(data)
    try:
        definition = AbilityDefinition.model_validate(data)
    except ValidationError as exc:
        raise ManifestValidationError(f"{source}: {exc}") from exc
    if definition.schema_version not in {SCHEMA_VERSION, "0.2", V1_SCHEMA_VERSION}:
        raise ManifestValidationError(
            f"{source}: 不支持的 schema_version {definition.schema_version!r}，"
            f"期望 {SCHEMA_VERSION!r} / '0.2' / {V1_SCHEMA_VERSION!r}"
        )
    if definition.schema_version == V1_SCHEMA_VERSION:
        if not V1_SEMVER_PATTERN.fullmatch(definition.identity.version):
            raise ManifestValidationError(
                f"{source}: v1 的 identity.version 必须是 SemVer core 三段"
                f"（MAJOR.MINOR.PATCH，每段 1-9 位十进制、无前导零、"
                f"不带预发布/构建元数据），当前是 {definition.identity.version!r}"
            )
    for key, operation in definition.operations.items():
        if operation.implementation.operation != key:
            raise ManifestValidationError(
                f"{source}.{key}: implementation.operation 必须等于成员键"
            )
        if definition.schema_version == V1_SCHEMA_VERSION:
            # v1 不写 provider_requirements：脚本槽由包内脚本实现，其余槽由运行期后端
            # 按名字解析。缺后端是运行期问题，不在解析期冒充契约错误。
            continue
        if operation.implementation.provider_slot not in definition.provider_requirements:
            raise ManifestValidationError(
                f"{source}.{key}: provider_slot "
                f"{operation.implementation.provider_slot!r} 未在 provider_requirements 声明"
            )
    if definition.schema_version == "0.2" and definition.assets:
        raise ManifestValidationError(f"{source}: assets 必须归属 skill，不支持顶层资源")
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


class ProviderRequirement(BaseModel):
    """提供者槽要求：该槽必须履行的接口身份与执行能力。"""

    model_config = ConfigDict(extra="forbid")

    contract: str = Field(min_length=1)
    contract_digest: str | None = None
    """由发布路径从唯一规范计算填入；作者不自签。"""
    required_capabilities: list[str] = Field(default_factory=list)


class ExecutionSpec(BaseModel):
    """一次调用的执行约束。首版只支持 request 语义。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["request"]
    timeout_ms: int = Field(gt=0)
    cancellation: Literal["cooperative", "unsupported"]
    on_revoke: Literal["drain", "request_cancel"]
    retry: Literal["never"]


class OperationImplementation(BaseModel):
    """成员到提供者的路由：槽 + 该槽接口内的动作名。"""

    model_config = ConfigDict(extra="forbid")

    provider_slot: str = Field(min_length=1)
    operation: str = Field(min_length=1)


class OperationDefinition(BaseModel):
    """一个可执行成员：参数、结果、错误、副作用与执行约束。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    errors: dict[str, Any] = Field(default_factory=dict)
    implementation: OperationImplementation
    effects: list[str] = Field(default_factory=list)
    resources: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionSpec
    script: ContentResource | None = None
    tags: list[str] = Field(default_factory=list)
    """发现与过滤标签；v1 由实现模块的 ``TAGS`` 生成，旧格式可缺省。"""

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
    """相对能力包根目录的路径。本版约定为 ``skills/SKILL.md``。

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
    """Skill 内显式声明的静态文件；摘要和大小由捕获方计算。"""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    expected_digest: str | None = None


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
    references: dict[str, ContentResource] | list[str] = Field(default_factory=dict)
    assets: dict[str, ContentResource] = Field(default_factory=dict)
    scripts: dict[str, ContentResource] = Field(default_factory=dict)
    """技能委托执行的脚本（``scripts/`` 下的 ``.py``）；不是 operation，不生成 schema。"""

    annotations: dict[str, Any] = Field(default_factory=dict)
    """作者自定义的附加键（如内容蒸馏来源 ``source_book``）：原样保留，
    框架不解释语义——避免过早固化它们的含义。"""


class AbilityDefinition(BaseModel):
    """一个 Ability 容器：身份 + 成员表 + 提供者槽要求。"""

    model_config = ConfigDict(extra="forbid")

    format: Literal["ghrah.ability-definition"]
    schema_version: str
    identity: AbilityIdentity
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    origin: Literal["source", "mcp"] = "source"
    """成员来源：``source``（包内脚本/技能）或 ``mcp``（由 ``tools/list`` 生成）。"""

    mcp: dict[str, Any] | None = None
    """``origin: mcp`` 时的连接声明（``{"server": <agentconf 名>}``），不含 URL/密钥。"""

    provider_requirements: dict[str, ProviderRequirement] = Field(default_factory=dict)
    operations: dict[str, OperationDefinition] = Field(default_factory=dict)
    skills: dict[str, SkillDefinition] = Field(default_factory=dict)
    assets: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_members(self) -> AbilityDefinition:
        """所有入口（包括持久恢复）都核对成员键与附件键。"""
        import re

        for mapping in (self.operations, self.skills):
            if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) for key in mapping):
                raise ValueError("非法成员键")
        for skill in self.skills.values():
            for mapping in (skill.assets, skill.references, skill.scripts):
                if isinstance(mapping, dict) and any(
                    not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) for key in mapping
                ):
                    raise ValueError("非法附件键")
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
