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

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

try:  # libyaml 可用时用 C loader；不可用则退回纯 Python（慢 ~8 倍，但语义一致）
    from yaml import CSafeLoader as _SafeLoader
except ImportError:  # pragma: no cover - 取决于构建环境
    from yaml import SafeLoader as _SafeLoader

from ghrah.scheme_ability.loading.manifest_fields import ManifestValidationError

__all__ = [
    "DEFINITION_FILE_NAME",
    "LOCK_FILE_NAME",
    "SKILL_BODY_DIR",
    "SKILL_FILE_NAME",
    "AbilityDefinition",
    "AbilityIdentity",
    "ExecutionSpec",
    "OperationDefinition",
    "OperationImplementation",
    "ProviderRequirement",
    "SCHEMA_VERSION",
    "SkillBody",
    "SkillDefinition",
    "load_yaml",
    "parse_definition",
]

SCHEMA_VERSION = "0.1-draft"
"""本实现认的定义格式版本。未知版本拒绝。"""

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
    return yaml.load(raw, Loader=_SafeLoader)


def parse_definition(raw: str, *, source: str) -> AbilityDefinition:
    """把 ``ability.yaml`` 文本解析并校验成定义模型。

    除契约（pydantic）外还做四条跨字段校验：``schema_version`` 受支持、
    ``implementation.operation`` 必须等于成员键、``provider_slot`` 必须在
    ``provider_requirements`` 里声明过、``skills`` 至多一个成员。

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
    try:
        definition = AbilityDefinition.model_validate(data)
    except ValidationError as exc:
        raise ManifestValidationError(f"{source}: {exc}") from exc
    if definition.schema_version != SCHEMA_VERSION:
        raise ManifestValidationError(
            f"{source}: 不支持的 schema_version {definition.schema_version!r}，"
            f"期望 {SCHEMA_VERSION!r}"
        )
    for key, operation in definition.operations.items():
        if operation.implementation.operation != key:
            raise ManifestValidationError(
                f"{source}.{key}: implementation.operation 必须等于成员键"
            )
        if operation.implementation.provider_slot not in definition.provider_requirements:
            raise ManifestValidationError(
                f"{source}.{key}: provider_slot "
                f"{operation.implementation.provider_slot!r} 未在 provider_requirements 声明"
            )
    if len(definition.skills) > 1:
        keys = "、".join(sorted(definition.skills))
        raise ManifestValidationError(
            f"{source}: 一个能力包只支持一个 skill 成员，这里声明了 "
            f"{len(definition.skills)} 个（{keys}）。技能是容器的插件包，"
            "容器与技能一一对应；要装第二个技能请独立成包"
        )
    return definition


class AbilityIdentity(BaseModel):
    """容器身份：机器载体保留 namespace / name 两段，不反向拆字符串。

    ``namespace`` 即**包名**（包目录名）。``name`` 可省：用户级包（skill 池里的
    一个包就是一个能力）只有一层身份，此时 ``full_name`` 退化为 ``namespace``。
    """

    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
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

    读取一律以本字段为准（见 :func:`ghrah.scheme_ability.loading.ability_scan.AbilitySkill.body`），
    因此框架侧不再第二次拼路径。**越界判定不在这里**：路径是否落在容器内由扫描期
    的唯一权威 :func:`ghrah.scheme_ability.loading.ability_scan._skill_body_locator` 裁决
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
        parts = [part for part in value.replace("\\", "/").split("/") if part not in ("", ".")]
        return "/".join(parts)


class SkillDefinition(BaseModel):
    """一个技能成员：给模型看的说明 + 正文位置。

    **一个能力包至多一个技能**，键 = 能力名（``container_name``），正文固定在
    ``skills/SKILL.md``——因此这里不引入第二级目录。

    ``title`` / ``description`` 即原 SKILL.md frontmatter 迁出的两个字段。
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    body: SkillBody
    tags: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
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
    provider_requirements: dict[str, ProviderRequirement] = Field(default_factory=dict)
    operations: dict[str, OperationDefinition] = Field(default_factory=dict)
    skills: dict[str, SkillDefinition] = Field(default_factory=dict)
    assets: dict[str, Any] = Field(default_factory=dict)

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
