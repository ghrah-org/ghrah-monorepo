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

from pathlib import PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

try:  # libyaml 可用时用 C loader；不可用则退回纯 Python（慢 ~8 倍，但语义一致）
    from yaml import CSafeLoader as _SafeLoader
except ImportError:  # pragma: no cover - 取决于构建环境
    from yaml import SafeLoader as _SafeLoader

from ghrah.ability.loading.manifest_fields import ManifestValidationError

__all__ = [
    "DEFINITION_FILE_NAME",
    "LOCK_FILE_NAME",
    "SCRIPT_CONTRACT",
    "SCRIPT_PROVIDER_SLOT",
    "SKILL_BODY_DIR",
    "SKILL_BODY_DIRS",
    "SKILL_FILE_NAME",
    "V1_SCHEMA_VERSION",
    "AbilityDefinition",
    "AbilityDefinitionV1",
    "AbilityIdentity",
    "ExecutionSpec",
    "MemberOperation",
    "MemberResource",
    "MemberSkill",
    "MembersDefinition",
    "OperationDefinition",
    "OperationImplementation",
    "ProviderRequirement",
    "SCHEMA_VERSION",
    "SkillBody",
    "SkillDefinition",
    "load_yaml",
    "media_type_for_path",
    "parse_definition",
]

SCHEMA_VERSION = "1"
"""当前定义格式版本（与 :data:`V1_SCHEMA_VERSION` 同值）。

保留这个别名是给只读"格式版本"的外部调用方（``ability_catalog`` 的
``DEFINITION_SCHEMA_VERSION``）；解析不按它分岔——见 :func:`parse_definition`。
"""

DEFINITION_FILE_NAME: str = "ability.yaml"
"""定义面文件名：``<扫描根>/<容器>/ability.yaml``。"""

SKILL_BODY_DIR: str = "skill"
"""技能目录（相对容器根）：``<容器>/skill/SKILL.md``。

一个能力包里的 ``skill/`` 就是**标准 skill 目录**（Anthropic 的 skill 规范）：
``SKILL.md`` 在它根部，``references/`` 与 ``assets/`` 是它的子目录。扫描 / 构建时
按这个位置发现技能；正文若在别处，以定义面声明的 ``body`` 为准。
"""

SKILL_FILE_NAME: str = "SKILL.md"
"""skill 正文文件名。"""

SKILL_BODY_DIRS: tuple[str, ...] = (SKILL_BODY_DIR, "skills")
"""技能目录的发现顺序：``skill/``（现行）→ ``skills/``（历史写法）。"""

LOCK_FILE_NAME: str = "ability.lock.yaml"
"""框架生成的登记文件（相对容器根）。

包布局常量统一定义在本模块：``skill_lock``（读写）与 ``skill_fingerprint``
（整树观测时需要排除它自身，避免自我指涉）都从这里取，避免两处各写一份。
"""

V1_SCHEMA_VERSION = "1"
"""``members`` 分区格式的版本号：作者手写区 + 工具生成区。

与 ``SCHEMA_VERSION``（``0.1-draft``）并存是为了兼容存量包；v1 解析后**归一化**
成同一个 :class:`AbilityDefinition`，因此下游（扫描、快照、授权、投影）只认一种
内部形状，不出现第二套读取规则。
"""

SCRIPT_PROVIDER_SLOT = "script"
"""v1 的成员路由槽：``members.operations.<key>.entry`` 归一成 ``script`` 槽。"""

SCRIPT_CONTRACT = "ghrah.script.v1"
"""``script`` 槽的契约名。未接线通用脚本后端时，这类成员在执行面上是 unavailable。"""

_MEDIA_TYPES: dict[str, str] = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".py": "text/x-python",
    ".svg": "image/svg+xml",
    ".toml": "application/toml",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def media_type_for_path(path: str) -> str:
    """按扩展名给出稳定的媒体类型；未知后缀退化为二进制。

    用在两处：生成区把 ``entry`` / ``references`` / ``assets`` 的路径摊成
    ``{path, media_type}`` 对，以及解析期把 ``entry`` 归一成 ``script`` 资源。
    同一张表两处共用，避免"生成时写 text/x-python、解析时认 application/octet-stream"。

    Args:
        path: 包内相对路径（``/`` 或 ``\\`` 分隔均可）。

    Returns:
        媒体类型字符串；无已知后缀时为 ``application/octet-stream``。
    """
    suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
    return _MEDIA_TYPES.get(suffix, "application/octet-stream")


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


def parse_definition(raw: str, *, source: str) -> AbilityDefinition:
    """把 ``ability.yaml`` 文本解析并校验成定义模型（**唯一入口、唯一格式**）。

    只有一条解析路径：``format: ghrah.ability-definition`` + ``schema_version: '1'``
    + ``members`` 分区（:class:`AbilityDefinitionV1`）。``schema_version`` 是必填
    字段，这里只做取值校验——不再按它分岔到第二套解析规则，因为每一份定义都会
    带这个字段，"另一条分支"等于永远不会执行。

    解析结果再经 :func:`_normalize_v1` 归一化成 :class:`AbilityDefinition`
    （运行时形状：成员、路由槽、执行约束都摊平），下游因此只有一种内部形状。

    用在扫描期的单个包解析：扫描器只负责定位与收集问题，契约判定全在这里。

    Args:
        raw: ``ability.yaml`` 的文本。
        source: 报错时用的来源标签（通常是容器名，或带根的定位串）。

    Returns:
        已校验并归一化的 :class:`AbilityDefinition`。

    Raises:
        ManifestValidationError: 顶层不是映射、``format`` / ``schema_version``
            不受支持，或成员契约不满足。
    """
    data = load_yaml(raw)
    if not isinstance(data, dict):
        raise ManifestValidationError(f"{source}: {DEFINITION_FILE_NAME} 不是映射")
    return _parse_v1(data, source)


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
    script: ContentResource | None = None
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
    """相对能力包根目录的路径。本版约定为 ``skill/SKILL.md``。

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

    **一个能力包至多一个技能**，键 = 能力名（``container_name``），正文固定在
    ``skill/SKILL.md``——因此这里不引入第二级目录。

    ``title`` / ``description`` 即原 SKILL.md frontmatter 迁出的两个字段。
    """

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    description: str = ""
    body: SkillBody
    tags: list[str] = Field(default_factory=list)
    references: dict[str, ContentResource] | list[str] = Field(default_factory=dict)
    assets: dict[str, ContentResource] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    """作者自定义的附加键（如内容蒸馏来源 ``source_book``）：原样保留，
    框架不解释语义——避免过早固化它们的含义。"""


class MemberOperation(BaseModel):
    """生成区里的一个可执行成员。

    只有索引事实（标题、说明、入口脚本或提供者槽、副作用）；调用契约由实现脚本自己
    持有（``bind_tool()``），因此这里**没有** input/output schema。

    两种成员形状二选一：

    - ``entry``：包内脚本（生成器扫描 ``operations/*.py`` 得到）；
    - ``provider``：由运行期后端提供实现（如 ``fs_backend``），必须在手写区的
      ``provider_requirements`` 里声明过。这类成员扫不出来，属于手写意图，``build``
      重建时原样保留。
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    entry: str | None = None
    """入口脚本路径，相对能力包根（如 ``operations/inspect.py``）。"""
    provider: str | None = None
    """提供者槽名；与 ``entry`` 二选一。"""
    effects: list[str] = Field(default_factory=list)
    expose: bool = True
    """是否**可授予 / 可发布 / 可选择**（缺省可）。

    这是**离线可判定**的授权面事实：内容面读的是已捕获的定义 JSON，拿不到实现类，
    调不了 ``bind_tool()``，所以"这个成员要不要出现在授权视图与工具选择里"必须由
    定义面声明。

    **它不决定模型工具面**——工具面由实现自己的 ``bind_tool()`` 决定（返回 ``None``
    即不进工具面）。两者刻意分开：``conversation`` 不需要声明 expose 就已经没有工具，
    而控制面成员（如 ``send``）可以"有工具绑定但不许授予"。
    """

    @model_validator(mode="after")
    def one_backend(self) -> MemberOperation:
        """成员至少要有一个实现落点：包内脚本或外部提供者（两者都给时槽位为准）。"""
        if self.entry is None and self.provider is None:
            raise ValueError("成员必须声明 entry 或 provider 至少一项")
        return self


class MemberSkill(BaseModel):
    """生成区里的技能正文定位。

    只有位置与媒体类型：名字与说明的作者是正文自身的 frontmatter
    （见 :mod:`ghrah.ability.loading.skill_frontmatter`），索引里不抄第二份。
    ``key`` 缺省取容器名；只有包内历史上就有别的键时才显式写。
    """

    model_config = ConfigDict(extra="forbid")

    key: str | None = None
    body: str = Field(min_length=1)
    media_type: str = Field(min_length=1)


class MemberResource(BaseModel):
    """生成区里的一个附件：路径、媒体类型、可选内容摘要。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    digest: str | None = None


class MembersDefinition(BaseModel):
    """``members`` 生成区：由 ``ghrah ability build`` 扫描目录写入。

    与历史格式的差别有两条：``skill`` 是单数（一个包至多一个技能，键由容器名
    隐含），``references`` / ``assets`` 是**列表**（路径即身份，成员键取文件名
    主干）。两者都是"文件清单"的自然形状，不需要作者另起名字。
    """

    model_config = ConfigDict(extra="forbid")

    operations: dict[str, MemberOperation] = Field(default_factory=dict)
    skill: MemberSkill | None = None
    references: list[MemberResource] = Field(default_factory=list)
    assets: list[MemberResource] = Field(default_factory=list)


class AbilityDefinitionV1(BaseModel):
    """``schema_version: '1'`` 的定义文件：手写区 + 生成区 ``members``。

    手写区（``format`` / ``schema_version`` / ``identity`` / ``title`` /
    ``description``）由作者维护，生成区由工具覆盖；``members`` 可以暂时缺席——
    新包先手写、再 build，两次之间目录面看到的是一个没有成员的空容器。
    """

    model_config = ConfigDict(extra="forbid")

    format: Literal["ghrah.ability-definition"]
    schema_version: str
    identity: AbilityIdentity
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    provider_requirements: dict[str, ProviderRequirement] = Field(default_factory=dict)
    """手写区：``members.operations.<key>.provider`` 引用到的槽必须先在这里声明。"""
    members: MembersDefinition | None = None

    @property
    def container_name(self) -> str:
        """容器名（= 包目录名）：``identity.name``；缺席时取 ``namespace``。"""
        return self.identity.name or self.identity.namespace


def _is_v1(version: Any) -> bool:
    """该 ``schema_version`` 是否走 ``members`` 分区格式。

    接受字符串 ``'1'`` 与 YAML 里未加引号而被解析成整数 ``1`` 的同一版本——
    这两者在盘上是同一份文件，不该因为引号差别读不出来。

    Args:
        version: ``schema_version`` 原值。

    Returns:
        是否按 v1 解析。
    """
    if version is None or isinstance(version, bool):
        return False
    return str(version).strip() == V1_SCHEMA_VERSION


def _parse_v1(data: dict[str, Any], source: str) -> AbilityDefinition:
    """解析 ``members`` 分区格式并归一化成内部定义模型。

    Args:
        data: 已解析的顶层映射。
        source: 报错用来源标签。

    Returns:
        归一化后的 :class:`AbilityDefinition`。

    Raises:
        ManifestValidationError: 契约不满足，或成员清单自相矛盾（附件无技能、
            附件键冲突）。
    """
    try:
        authored = AbilityDefinitionV1.model_validate(data)
    except ValidationError as exc:
        raise ManifestValidationError(f"{source}: {exc}") from exc
    if not _is_v1(authored.schema_version):
        raise ManifestValidationError(
            f"{source}: 不支持的 schema_version {authored.schema_version!r}"
            f"（本实现只认 {V1_SCHEMA_VERSION!r}）"
        )
    return _normalize_v1(authored, source)


def _normalize_v1(authored: AbilityDefinitionV1, source: str) -> AbilityDefinition:
    """把 v1 的 ``members`` 摊成内部模型的 operations / skills 两块。

    映射规则（单向、可重建，不引入新事实）：

    - ``members.operations.<key>`` → 同键的 OperationDefinition：``entry`` 落到
      ``script`` 资源，路由槽固定为 :data:`SCRIPT_PROVIDER_SLOT`（未接线脚本后端
      时该成员在执行面为 unavailable，而不是被误当成某个内置槽）；I/O schema
      留空——v1 的调用契约由实现脚本持有，定义面只做索引；
    - ``members.skill`` → 以容器名为键的唯一 SkillDefinition；``references`` /
      ``assets`` 挂在该技能下，成员键取文件名主干（``references/style-guide.md``
      → ``style-guide``），与既有示例包的键口径一致；名字与说明**不进定义面**
      ——正文 frontmatter 是它们的作者，由扫描/捕获读取；
    - 顶层 ``assets`` 在 v1 不存在，归一化后恒为空。

    Args:
        authored: 已校验的 v1 定义。
        source: 报错用来源标签。

    Returns:
        内部定义模型。

    Raises:
        ManifestValidationError: 声明了附件却没有技能正文，或附件键主干重名。
    """
    members = authored.members or MembersDefinition()
    if (members.references or members.assets) and members.skill is None:
        raise ManifestValidationError(
            f"{source}: members.references / members.assets 必须伴随 members.skill"
            "（一个能力包只有一份技能正文，附件不能脱离它单独存在）"
        )

    operations: dict[str, OperationDefinition] = {}
    requirements = dict(authored.provider_requirements)
    for key, member in members.operations.items():
        slot = member.provider or SCRIPT_PROVIDER_SLOT
        if slot not in requirements:
            if member.provider is not None:
                raise ManifestValidationError(
                    f"{source}.{key}: provider {slot!r} 未在 provider_requirements 声明"
                )
            requirements[slot] = ProviderRequirement(contract=SCRIPT_CONTRACT)
        script = (
            ContentResource(
                path=member.entry,
                media_type=media_type_for_path(member.entry),
            )
            if member.entry is not None
            else None
        )
        operations[key] = OperationDefinition(
            title=member.title,
            description=member.description,
            input_schema=_empty_schema(),
            output_schema=_empty_schema(),
            implementation=OperationImplementation(
                provider_slot=slot,
                operation=key,
            ),
            effects=list(member.effects),
            execution=_default_execution(),
            script=script,
            expose=member.expose,
        )

    skills: dict[str, SkillDefinition] = {}
    if members.skill is not None:
        skills[members.skill.key or authored.container_name] = SkillDefinition(
            body=SkillBody(
                path=members.skill.body,
                media_type=members.skill.media_type,
            ),
            references=_member_resources(members.references, source, "references"),
            assets=_member_resources(members.assets, source, "assets"),
        )

    return AbilityDefinition(
        format=authored.format,
        schema_version=V1_SCHEMA_VERSION,
        identity=authored.identity,
        title=authored.title,
        description=authored.description,
        provider_requirements=requirements,
        operations=operations,
        skills=skills,
        assets={},
    )


def _empty_schema() -> dict[str, Any]:
    """v1 成员的空参数契约（调用契约在实现脚本里，这里只占位）。"""
    return {"type": "object", "properties": {}, "additionalProperties": False}


def _default_execution() -> ExecutionSpec:
    """v1 成员的执行约束默认值（v1 不声明执行约束）。

    取内置包的主流域值——请求语义、60s 上限、不协作取消、撤销时排空、不重试。
    这里的默认是**声明面填充**，不改变执行器行为（``timeout_ms`` 目前不参与执行）。
    """
    return ExecutionSpec(
        mode="request",
        timeout_ms=60_000,
        cancellation="unsupported",
        on_revoke="drain",
        retry="never",
    )


def _member_resources(
    items: list[MemberResource], source: str, role: str
) -> dict[str, ContentResource]:
    """把生成区的附件列表摊成具名映射（键 = 文件名主干）。

    Args:
        items: ``members.references`` 或 ``members.assets``。
        source: 报错用来源标签。
        role: 附件类别名，仅用于报错定位。

    Returns:
        ``{主干: ContentResource}``。

    Raises:
        ManifestValidationError: 两个附件的主干同名——键会互相覆盖，必须让作者
            把文件改成可区分的名字，而不是悄悄取一个。
    """
    resources: dict[str, ContentResource] = {}
    for item in items:
        key = PurePosixPath(item.path.replace("\\", "/")).stem
        if not key:
            raise ManifestValidationError(f"{source}: members.{role} 路径无文件名：{item.path!r}")
        previous = resources.get(key)
        if previous is not None:
            raise ManifestValidationError(
                f"{source}: members.{role} 成员键冲突 {key!r}"
                f"（{previous.path!r} 与 {item.path!r}），文件名主干需可区分"
            )
        resources[key] = ContentResource(
            path=item.path,
            media_type=item.media_type,
            expected_digest=item.digest,
        )
    return resources


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

    @model_validator(mode="after")
    def validate_members(self) -> AbilityDefinition:
        """所有入口（包括持久恢复）都核对基数和键。"""
        import re

        if len(self.skills) > 1:
            raise ValueError("一个能力包只支持一个 skill 成员；第二个 skill 请独立成包")
        for mapping in (self.operations, self.skills):
            if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) for key in mapping):
                raise ValueError("非法成员键")
        for skill in self.skills.values():
            for mapping in (skill.assets, skill.references):
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
