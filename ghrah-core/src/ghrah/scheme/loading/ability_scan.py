# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""能力目录扫描：在目录池上按「能力容器」收集成员，问题不中断整轮。

对外主入口是 :func:`scan_abilities`——**一次调用、一个容器对象列表**，每个容器
对象自带名字、描述、以及 operations / skills / assets 三类成员与它自己的问题，
调用方不必自己按容器名聚合。后续新增成员类型（如 mcp）也落在这个对象上。

底层口径（:func:`scan_ability_definitions`）仍保留扁平的“全部条目 + 全部成员 +
根级问题”视图，用于排查目录池本身的问题（某个根不存在之类），两者共用同一趟扫描。

与 ``ability_catalog`` 的分工（两者不是第二权威，只是两种读法）：

与 ``ability_catalog`` 的分工（两者不是第二权威，只是两种口径的读法）：

- **本模块 = 目录面**：扫整个池（内置树 + 用户目录），一次给出「有哪些能力包、
  各自声明了什么成员、哪些包有问题」。收集式——单个包坏了记一条问题继续扫，
  因为外部目录是**输入**，一份坏文件不该让整面不可读。
- ``ability_catalog`` = 实现面：只读内置树、严格抛错、按点分路径 import 实现
  脚本投影运行时 view。外部包没有实现脚本，落不进这一面。

skill 成员（``ability.yaml`` 的 ``skills`` 表）**在这里只是声明面读数**：给身份、
标题、说明、正文路径与来源，不读正文、不算指纹、不写 lock——正文读取与整树
校验是 ``skill_provider`` 的职责（``SkillAbilityProvider``）。一个文件只被解析一次
（:func:`ghrah.scheme.loading.ability_schema.parse_definition`），两面读同一份结果。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from ghrah.scheme.loading import ability_roots as roots
from ghrah.scheme.loading.ability_roots import AbilityRoot
from ghrah.scheme.loading.ability_schema import (
    DEFINITION_FILE_NAME,
    LOCK_FILE_NAME,
    AbilityDefinition,
    OperationDefinition,
    SkillDefinition,
    parse_definition,
)
from ghrah.scheme.loading.manifest_fields import ManifestValidationError
from ghrah.scheme.loading.skill_errors import SkillLockError, SkillParseError
from ghrah.scheme.loading.skill_fingerprint import (
    build_manifest,
    diff_manifests,
    fingerprint_entry,
)
from ghrah.scheme.loading.skill_lock import (
    build_lock,
    create_lock,
    read_lock_record,
    replace_lock,
    utc_now_rfc3339,
)
from ghrah.scheme.loading.skill_models import (
    BundleVerification,
    LockOutcome,
    LockRecord,
    SkillFingerprint,
)

__all__ = [
    "AbilityAsset",
    "AbilityContainer",
    "AbilityEntry",
    "AbilityOperation",
    "AbilityScanIssue",
    "AbilityScanResult",
    "AbilitySkill",
    "ensure_locks",
    "load_ability_skills",
    "relock_skill",
    "scan_abilities",
    "scan_ability_definitions",
    "verify_skill",
]


@dataclass(frozen=True)
class AbilityEntry:
    """池中命中的一个能力包：定义 + 它来自哪个根。"""

    container: str
    """容器名（= 包目录名 = ``definition.container_name``，两者已校验一致）。"""

    definition: AbilityDefinition
    """解析后的定义面。"""

    root: AbilityRoot
    """来源根（包资源还是某个目录、什么 rank）。"""

    locator: Traversable
    """容器目录句柄：正文、实现脚本都相对它定位。"""


@dataclass(frozen=True)
class AbilitySkill:
    """定义面里声明的一个 skill 成员（声明面读数，不含正文）。"""

    container: str
    """所属容器名。"""

    key: str
    """成员键（``skills`` 表里的键）。"""

    full_name: str
    """成员全名：``<容器点分身份>.<成员键>``。

    例外：成员键与容器同名时收敛为容器身份（``research`` 而不是
    ``research.research``）。多数技能包是「一个包一个技能」，键与包名重复，
    双写既不自然，也和模型/装配侧一直用的包名不是同一个字符串。
    """

    title: str
    description: str
    media_type: str
    """正文媒体类型（``text/markdown`` 等），由定义面声明。"""

    body_path: str
    """定义面声明的正文路径，相对容器目录（如 ``skills/SKILL.md``）。"""

    body_locator: Traversable
    """正文的定位器（已相对容器解析；**不保证存在**，存在性问题在扫描期报）。"""

    tags: tuple[str, ...]
    source: str
    """来源根的标签（``builtin`` / ``skill-ability`` …）。"""

    rank: int
    """来源根的优先级，越小越优先。"""

    skill_dir: Path | None = None
    """容器的真实目录；包资源根（只读、可能不在文件系统上）为 None。"""

    lock_path: Path | None = None
    """登记文件路径；``skill_dir`` 为 None 时同样为 None。"""

    fingerprint: SkillFingerprint | None = None
    """入口哨兵（定义面 + 正文）与登记记录——**扫描期一次读出**，不缓存。

    扫描本身就是「这次盘上有什么状态」的权威读数：登记值来自 lock，当前值来自
    定义面与正文两份文件。正文/定义面在扫描后又被改动，需要重新扫描才能看见。
    """

    lock_issue: str | None = None
    """lock 读不出来、或登记时降级的说明（「无法裁决」不等于「内容变过」）。"""

    @property
    def body(self) -> Path | None:
        """正文的真实路径（按定义面声明的 ``body_path`` 拼出）；包资源根为 None。

        路径的权威是定义面的 ``skills.<key>.body.path``——**不在这里第二次拼**
        ``skills/SKILL.md``：声明与读取必须是同一条路径，否则一个容器声明了别名
        正文时，扫盘校验的是声明路径、读取却拿到另一份文件。

        Returns:
            正文文件的绝对路径；容器来自包资源（不在文件系统上）时为 None。
        """
        if self.skill_dir is None:
            return None
        return self.skill_dir.joinpath(*self.body_path.replace("\\", "/").split("/"))

    @property
    def drifted(self) -> bool:
        """入口哨兵自登记以来是否变过；未登记或读不出来时恒为 False。"""
        return self.fingerprint.drifted if self.fingerprint is not None else False

    @property
    def catalog_name(self) -> str:
        """模型可见的名字：成员键（与 operation 成员同一口径）。

        全名（``<容器点分身份>.<成员键>``）只用于去重与问题定位——目录里显示
        全名会让模型拿一个加载入口不认的名字去调用。
        """
        return self.key


@dataclass(frozen=True)
class AbilityOperation:
    """容器里的一个 operation 成员（旧口径的“工具”）。

    成员键与全名规则与 skill 一致（``<容器点分身份>.<成员键>``），因此同一容器里
    ``fs`` + ``read_file`` 与容器 ``fs.read_file`` 是同一个名字。

    ``definition`` 是定义面的权威载荷（参数 schema、错误表、副作用、执行约束都在
    里面），本类只补上“它属于哪个容器、全名是什么”，**不复制**那些字段。
    """

    container: str
    """所属容器名。"""

    key: str
    """成员键（``operations`` 表里的键）。"""

    full_name: str
    """成员全名：``<容器点分身份>.<成员键>``。"""

    definition: OperationDefinition
    """定义面的成员载荷（权威，原样持有）。"""

    @property
    def title(self) -> str:
        """成员标题（给人看的一行名）。"""
        return self.definition.title

    @property
    def description(self) -> str:
        """成员说明（给模型看的用途描述）。"""
        return self.definition.description

    @property
    def provider_slot(self) -> str:
        """实现所在的提供者槽（``fs`` / ``core`` / ``shell`` …）。"""
        return self.definition.implementation.provider_slot

    @property
    def handler(self) -> str:
        """槽内的实现动作名（运行时据此定位实现）。"""
        return self.definition.implementation.operation

    @property
    def timeout_ms(self) -> int:
        """单次调用的超时上限（毫秒），来自执行约束。"""
        return self.definition.execution.timeout_ms

    @property
    def effects(self) -> tuple[str, ...]:
        """声明的外部副作用（``fs.write`` 之类），空表示只读。"""
        return tuple(self.definition.effects)


@dataclass(frozen=True)
class AbilityAsset:
    """容器里的一个 asset 成员。

    ``assets`` 在当前 schema 里是自由结构（``dict[str, Any]``），尚无字段级契约：
    本类只给身份与键，内容原样持有、不解释语义。
    """

    container: str
    key: str
    full_name: str
    definition: Any
    """定义面声明的资产内容（自由结构，原样持有）。"""


@dataclass(frozen=True)
class AbilityContainer:
    """一个能力容器（= 一个能力包目录）的全貌。

    这是 :func:`scan_abilities` 的元素类型：名字、描述、三类成员与自身问题都在
    这里，调用方不需要再按容器名做第二次聚合。

    成员表按成员键升序，容器之间按容器名升序——跨文件系统、跨机器都得到同一顺序，
    因此可以安全地用于快照比对与去重。
    """

    name: str
    """容器名（= 包目录名 = ``identity.namespace``，三者已校验一致）。"""

    namespace: str
    """点分身份的命名空间前缀（如 ``ghrah``）。"""

    version: str
    """定义面声明的版本（不在 lock 里重复记录）。"""

    title: str
    """容器标题。"""

    description: str
    """容器说明（给模型/人看的整体用途）。"""

    root: AbilityRoot
    """来源根（包资源还是某个目录、什么 rank）。"""

    locator: Traversable
    """容器目录句柄：实现脚本、正文都相对它定位。"""

    definition: AbilityDefinition
    """解析后的定义面（权威载荷，原样持有）。"""

    operations: tuple[AbilityOperation, ...] = ()
    """operation 成员，按成员键升序。"""

    skills: tuple[AbilitySkill, ...] = ()
    """skill 成员，按成员键升序。"""

    assets: tuple[AbilityAsset, ...] = ()
    """asset 成员，按成员键升序。"""

    issues: tuple[AbilityScanIssue, ...] = ()
    """归属于本容器的问题（同名副本被忽略、正文缺失…）。

    根级问题（某个扫描根不存在）不属于任何容器，见
    :attr:`AbilityScanResult.issues`。
    """

    @property
    def full_name(self) -> str:
        """容器的点分身份全名（``ghrah.fs``）。"""
        return self.definition.identity.full_name

    @property
    def kinds(self) -> tuple[str, ...]:
        """本容器声明了哪几类成员：``operations`` / ``skills`` / ``assets``。

        用于“这个包是纯工具包还是工具+技能混合包”的判定与展示。新增成员类型
        （如 mcp）时在此追加一项。
        """
        kinds: list[str] = []
        if self.operations:
            kinds.append("operations")
        if self.skills:
            kinds.append("skills")
        if self.assets:
            kinds.append("assets")
        return tuple(kinds)

    @property
    def operation_count(self) -> int:
        """operation 成员数量。"""
        return len(self.operations)

    @property
    def skill_count(self) -> int:
        """skill 成员数量。"""
        return len(self.skills)

    @property
    def asset_count(self) -> int:
        """asset 成员数量。"""
        return len(self.assets)

    @property
    def member_count(self) -> int:
        """三类成员的总数（不含问题）。"""
        return self.operation_count + self.skill_count + self.asset_count

    @property
    def is_operation_only(self) -> bool:
        """是否只有 operation 成员（旧口径的纯工具包，如 ``fs`` / ``shell``）。"""
        return bool(self.operations) and not self.skills and not self.assets

    @property
    def is_skill_only(self) -> bool:
        """是否只有 skill 成员（用户目录里的纯技能包）。"""
        return bool(self.skills) and not self.operations and not self.assets

    def __str__(self) -> str:
        """渲染成 ``名字 (路径池标签) operations=N skills=M assets=K`` 一行。"""
        counts = f"operations={self.operation_count} skills={self.skill_count}"
        if self.assets:
            counts += f" assets={self.asset_count}"
        return f"{self.name} ({self.root.label}) {counts}"


@dataclass(frozen=True)
class AbilityScanIssue:
    """扫描期的一条问题（收集式：单条问题不影响其余包）。"""

    path: str
    """出问题的定位串（``<根>/<容器>`` 或更具体的位置）。"""

    reason: str

    container: str | None = None
    """问题归属的容器名；根级问题（根不存在）为 None。"""

    def __str__(self) -> str:
        """渲染成 ``路径: 原因``，便于直接进报告与日志。"""
        return f"{self.path}: {self.reason}"


@dataclass(frozen=True)
class AbilityScanResult:
    """一轮目录池扫描的结果。"""

    roots: tuple[AbilityRoot, ...]
    """本轮的目录池（已按优先级排序），外部可据此说明"扫了哪些路径"。"""

    entries: tuple[AbilityEntry, ...]
    """命中的能力包，按容器名升序（确定性：跨文件系统、跨机器一致）。"""

    skills: tuple[AbilitySkill, ...]
    """全部 skill 成员声明，按全名升序。"""

    issues: tuple[AbilityScanIssue, ...]
    """扫描期问题：根缺失（仅 ``required`` 根）、定义非法、容器名与目录名不符、
    同名容器的低优先级副本被忽略、声明的正文不存在。"""

    lock_issues: tuple[AbilityScanIssue, ...] = ()
    """登记面问题：lock 读不出来、或登记时为降级状态（无法裁决内容是否变过）。

    与 ``issues`` 分开：``issues`` 里的每一条都意味着"这个成员坏了"，而登记面
    的问题只是"漂移判定不可用"，成员本身仍可加载。
    """

    @property
    def definitions(self) -> Mapping[str, AbilityDefinition]:
        """``{容器名: 定义}`` 视图（对外只读，不复制模型）。"""
        return {entry.container: entry.definition for entry in self.entries}

    @property
    def containers(self) -> tuple[AbilityContainer, ...]:
        """把扁平结果按容器聚合（成员与问题都归到各自的容器上）。

        与 :func:`scan_abilities` 同一份数据、同一套摊法；直接持有扁平结果时用这个
        属性，不想关心目录池时用 :func:`scan_abilities`。
        """
        return tuple(
            _build_container(
                entry,
                [issue for issue in self.issues if issue.container == entry.container],
                [skill for skill in self.skills if skill.container == entry.container],
            )
            for entry in self.entries
        )


def _skill_body_locator(container_locator: Traversable, body_path: str) -> Traversable | None:
    """把定义面声明的正文路径解析成定位器。

    按 ``/`` 逐段 ``joinpath``（而不是直接拼一整串）：包资源的 ``joinpath`` 对
    多段路径的实现不一致，逐段走最稳。``..`` 一律拒绝——正文必须落在容器目录内，
    否则一份外部 ``ability.yaml`` 就能把正文指到包外任意文件。

    Args:
        container_locator: 容器目录句柄。
        body_path: 定义面声明的相对路径。

    Returns:
        正文定位器；路径越界或为空时返回 None。
    """
    parts = [part for part in body_path.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return None
    locator = container_locator
    for part in parts:
        locator = locator.joinpath(part)
    return locator


def _skill_body_problem(
    container_locator: Traversable,
    key: str,
    skill: SkillDefinition,
) -> str | None:
    """检查一个 skill 声明的正文能不能用；不可用时给出一句可读原因。

    扫描期（记问题）与成员构造（决定要不要把这个成员摊出来）共用同一判断：
    正文不可用的技能**不进清单**——它既读不出来，也就不该出现在模型可见的目录里，
    否则模型会拿到一个永远加载失败的入口。声明本身仍留在定义面上，不被丢弃。

    Args:
        container_locator: 容器目录句柄。
        key: 成员键（用于问题定位）。
        skill: 定义面里的 skill 载荷。

    Returns:
        原因串；正文可用时返回 None。
    """
    body = _skill_body_locator(container_locator, skill.body.path)
    if body is None:
        return f"skills.{key}: 正文路径非法（不能越出容器目录）：{skill.body.path!r}"
    if not body.is_file():
        return f"skills.{key}: 声明的正文不存在：{skill.body.path}"
    return None


def _scan_root(root: AbilityRoot) -> tuple[list[AbilityEntry], list[AbilityScanIssue]]:
    """扫单个根：枚举第一层包目录、解析定义、校验容器名与正文。

    Args:
        root: 单个扫描根。

    Returns:
        ``(命中的包, 问题清单)``；同根内的问题不影响继续扫。

    Raises:
        OSError: 根目录不可列举（权限等）；由调用方转成问题记录。
    """
    entries: list[AbilityEntry] = []
    issues: list[AbilityScanIssue] = []
    for entry in sorted(root.locator.iterdir(), key=lambda item: item.name):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        definition_path = entry.joinpath(DEFINITION_FILE_NAME)
        if not definition_path.is_file():
            # 普通子目录（operations/ 之类）：不是能力包，跳过。
            continue
        label = f"{root.label}/{entry.name}"
        try:
            raw = definition_path.read_text(encoding="utf-8")
            definition = parse_definition(raw, source=label)
        except (ManifestValidationError, OSError, UnicodeDecodeError) as exc:
            issues.append(AbilityScanIssue(path=label, reason=str(exc), container=entry.name))
            continue
        if definition.container_name != entry.name:
            issues.append(
                AbilityScanIssue(
                    path=label,
                    reason=(
                        f"容器名 {definition.container_name!r} 与目录名 {entry.name!r} 不一致"
                        "（目录名只作定位，身份以定义面为准，两者必须相等）"
                    ),
                    container=entry.name,
                )
            )
            continue
        for key, skill in definition.skills.items():
            problem = _skill_body_problem(entry, key, skill)
            if problem is not None:
                issues.append(AbilityScanIssue(path=label, reason=problem, container=entry.name))
        entries.append(
            AbilityEntry(
                container=definition.container_name,
                definition=definition,
                root=root,
                locator=entry,
            )
        )
    return entries, issues


def scan_ability_definitions(
    pool: Sequence[AbilityRoot] | None = None,
) -> AbilityScanResult:
    """扫描目录池，返回全部能力定义与 skill 成员（每次调用都重新扫盘，无缓存）。

    用在「现在这一台机器上有哪些能力包」的所有场景：外部展示、装配前体检、
    调试脚本、给外层框架提供目录。**不投影运行时 view**（那需要可 import 的
    实现脚本，见 ``ability_catalog``）。

    优先级：池按 ``(rank, 根标签)`` 升序扫，同一个容器名由先出现者胜出，后来者
    记一条问题（可见，不静默覆盖）。

    Args:
        pool: 目录池；None 取 ``ability_roots.default_pool()``（内置树 + 用户目录）。

    Returns:
        :class:`AbilityScanResult`：命中的包、全部 skill 成员、扫描期问题。

    Raises:
        OSError: 某个根存在但不可列举——这是环境问题，不是单个包的问题，故不吞。
    """
    active = tuple(pool) if pool is not None else roots.default_pool()
    ordered = tuple(sorted(active, key=lambda item: (item.rank, item.label)))

    by_container: dict[str, AbilityEntry] = {}
    issues: list[AbilityScanIssue] = []

    for root in ordered:
        if not root.locator.is_dir():
            if root.required:
                issues.append(AbilityScanIssue(path=root.label, reason="扫描根不存在"))
            continue
        entries, root_issues = _scan_root(root)
        issues.extend(root_issues)
        for entry in entries:
            existing = by_container.get(entry.container)
            if existing is not None:
                issues.append(
                    AbilityScanIssue(
                        path=f"{root.label}/{entry.container}",
                        reason=(
                            f"重复的容器名 {entry.container!r} 已由 "
                            f"{existing.root.label}（rank={existing.root.rank}）提供，本条被忽略"
                        ),
                        container=entry.container,
                    )
                )
                continue
            by_container[entry.container] = entry

    skills = [
        _skill_member(entry, key, skill)
        for entry in by_container.values()
        for key, skill in entry.definition.skills.items()
        if _skill_body_problem(entry.locator, key, skill) is None
    ]
    ordered_skills = tuple(sorted(skills, key=lambda item: item.full_name))

    return AbilityScanResult(
        roots=ordered,
        entries=tuple(by_container[key] for key in sorted(by_container)),
        skills=ordered_skills,
        issues=tuple(issues),
        lock_issues=tuple(
            AbilityScanIssue(
                path=str(member.lock_path or member.skill_dir or member.container),
                reason=member.lock_issue,
                container=member.container,
            )
            for member in ordered_skills
            if member.lock_issue is not None
        ),
    )


def _skill_member(entry: AbilityEntry, key: str, skill: SkillDefinition) -> AbilitySkill:
    """把一个 skill 声明摊成成员对象（扁平视图与容器视图共用同一个摊法）。

    除定义面事实外，同步读出「这个成员现在在盘上是什么状态」：容器目录、登记
    文件路径、以及一次算出的指纹（登记值 + 当前值 + 漂移判定）。这些读数不缓存，
    每次调用都是当下的真相。

    Args:
        entry: 所属容器条目。
        key: 成员键。
        skill: 定义面里的 skill 载荷。

    Returns:
        :class:`AbilitySkill`。
    """
    definition = entry.definition
    body_path = skill.body.path
    # 成员键与容器同名时，全名收敛为容器名：多数技能包是「一个包一个技能」，
    # 叫 ``research.research`` 既不自然，也和加载入口的常见写法（包名）不一致。
    full_name = entry.container if key == entry.container else definition.full_name_of(key)
    skill_dir = entry.locator if isinstance(entry.locator, Path) else None
    fingerprint = (
        _member_fingerprint(skill_dir, full_name, body_path) if skill_dir is not None else None
    )
    return AbilitySkill(
        container=entry.container,
        key=key,
        full_name=full_name,
        title=skill.title,
        description=skill.description,
        media_type=skill.body.media_type,
        body_path=body_path,
        body_locator=(_skill_body_locator(entry.locator, body_path) or entry.locator),
        tags=tuple(skill.tags),
        source=entry.root.source,
        rank=entry.root.rank,
        skill_dir=skill_dir,
        lock_path=(skill_dir / LOCK_FILE_NAME) if skill_dir is not None else None,
        fingerprint=fingerprint,
        lock_issue=(fingerprint.lock_issue if fingerprint is not None else None),
    )


def _member_fingerprint(skill_dir: Path, full_name: str, body_relpath: str) -> SkillFingerprint:
    """读出单个技能的指纹状态：登记值来自 lock，当前值来自定义面与正文。

    只在扫描期调用。**任何「读不出来」都记成 ``lock_issue`` 而不是异常**：登记文件
    坏掉不该让整轮扫描失败，但也不能悄悄当成"没变过"。

    Args:
        skill_dir: 容器目录。
        full_name: 成员全名（写进指纹对象，便于问题定位）。
        body_relpath: 定义面声明的正文路径（``/`` 分隔）；哨兵与 lock 裁决都用它，
            使「算指纹的那份文件」与「读正文的那份文件」是同一个。

    Returns:
        :class:`SkillFingerprint`，其中 ``current`` 为 None 表示入口文件读不出来。
    """
    lock_path = skill_dir / LOCK_FILE_NAME
    current: str | None = None
    lock_issue: str | None = None
    record: LockRecord | None = None

    try:
        current = fingerprint_entry(skill_dir, body_relpath=body_relpath)
    except (SkillParseError, OSError) as exc:
        lock_issue = f"入口文件不可读: {exc}"

    if lock_path.is_file():
        try:
            record = read_lock_record(lock_path, body_relpath=body_relpath)
        except SkillLockError as exc:
            lock_issue = str(exc)
        else:
            degrade = record.manifest.degraded
            if degrade is not None:
                lock_issue = f"登记为降级状态（{degrade.reason}），整树摘要缺失，无法裁决漂移"

    return SkillFingerprint(
        full_name=full_name,
        skill_dir=skill_dir,
        lock_path=lock_path,
        current=current,
        recorded=record.entry if record is not None else None,
        record=record,
        lock_issue=lock_issue,
    )


def _build_container(
    entry: AbilityEntry,
    issues: Sequence[AbilityScanIssue],
    skills: Sequence[AbilitySkill] | None = None,
) -> AbilityContainer:
    """把一个命中的条目摊成容器对象（成员按成员键升序）。

    Args:
        entry: 命中的容器条目。
        issues: 已筛出的、归属该容器的问题。
        skills: 已算好的该容器 skill 成员；None 时现场摊（会重算一次指纹，
            持有 :class:`AbilityScanResult` 时请传进来避免重复读盘）。

    Returns:
        :class:`AbilityContainer`。
    """
    definition = entry.definition
    members = (
        tuple(skills)
        if skills is not None
        else tuple(
            _skill_member(entry, key, skill)
            for key, skill in sorted(definition.skills.items())
            if _skill_body_problem(entry.locator, key, skill) is None
        )
    )
    return AbilityContainer(
        name=entry.container,
        namespace=definition.identity.namespace,
        version=definition.identity.version,
        title=definition.title,
        description=definition.description,
        root=entry.root,
        locator=entry.locator,
        definition=definition,
        operations=tuple(
            AbilityOperation(
                container=entry.container,
                key=key,
                full_name=definition.full_name_of(key),
                definition=operation,
            )
            for key, operation in sorted(definition.operations.items())
        ),
        skills=members,
        assets=tuple(
            AbilityAsset(
                container=entry.container,
                key=key,
                full_name=definition.full_name_of(key),
                definition=asset,
            )
            for key, asset in sorted(definition.assets.items())
        ),
        issues=tuple(issues),
    )


def scan_abilities(pool: Sequence[AbilityRoot] | None = None) -> tuple[AbilityContainer, ...]:
    """扫描目录池，按「能力容器」返回全貌（对外主入口，每次调用都重新扫盘）。

    一个元素 = 一个能力包：名字、描述、``operations`` / ``skills`` / ``assets``
    三类成员、以及归属于它的问题。想只要某一类成员或只要数量时直接读属性
    （``container.operation_count`` / ``container.kinds`` …）。

    用在「现在这台机器上有哪些能力包、每个包里有什么」的所有场景：外部展示、
    工具面装配前的体检、调试脚本、给外层框架提供目录。

    同名容器的优先级：池按 ``(rank, 根标签)`` 升序扫，rank 小的胜出；被忽略的副本
    会在胜出者的 :attr:`AbilityContainer.issues` 里留一条记录（可见，不静默覆盖）。

    Args:
        pool: 目录池；None 取 ``ability_roots.default_pool()``
            （内置包树 rank 0 + ``~/.ghrah/skill-ability`` rank 100）。

    Returns:
        容器元组，按容器名升序。**根级问题**（某个 ``required`` 根不存在）不属于
        任何容器，需要连目录池一起看时改用 :func:`scan_ability_definitions`。

    Raises:
        OSError: 某个根存在但不可列举——这是环境问题，不是单个包的问题，故不吞。
    """
    return scan_ability_definitions(pool).containers


def load_ability_skills(pool: Sequence[AbilityRoot] | None = None) -> tuple[AbilitySkill, ...]:
    """只要 skill 成员声明（扫描的薄封装）。

    用在「把能力面声明的技能并进工具/目录」的场景：不想关心容器与问题时用这个。

    Args:
        pool: 目录池；None 取默认池。

    Returns:
        按全名升序的 skill 成员。
    """
    return scan_ability_definitions(pool).skills


def ensure_locks(
    scan: AbilityScanResult,
    *,
    recorded_at: str | None = None,
) -> tuple[tuple[LockOutcome, ...], tuple[AbilityScanIssue, ...]]:
    """补齐缺失的登记文件（**唯一**的登记写入入口，扫描本身从不写盘）。

    扫描已经把"哪些成员有 lock、哪些没有"读出来了，这里只对缺的那部分动手；
    已有的 lock 一律不覆盖——覆盖等于悄悄吞掉别人的登记意图。要重登用
    :func:`relock_skill`。

    用在首次装配用户目录、或分发一个能力包之后：``ghrah skill init`` 走的就是
    这条路径。

    Args:
        scan: 刚扫完的结果（:func:`scan_ability_definitions`）。
        recorded_at: 登记时间戳；None 取当前 UTC 时间（RFC 3339）。

    Returns:
        ``(登记结果, 问题)``。每个成员一条 :class:`LockOutcome`（``created`` 表示
        这次是否新写了文件）；写失败、或该成员的 lock 本来就坏，记进问题里。
    """
    stamp = recorded_at or utc_now_rfc3339()
    outcomes: list[LockOutcome] = []
    issues: list[AbilityScanIssue] = []

    for member in scan.skills:
        if member.skill_dir is None or member.lock_path is None:
            continue
        if member.lock_path.is_file():
            outcomes.append(
                LockOutcome(
                    full_name=member.full_name,
                    path=member.lock_path,
                    created=False,
                    reason="已存在，未覆盖",
                )
            )
        else:
            try:
                created = create_lock(member.lock_path, _lock_data(member, stamp))
            except (SkillLockError, SkillParseError, OSError) as exc:
                issues.append(
                    AbilityScanIssue(
                        path=str(member.lock_path),
                        reason=f"登记失败: {exc}",
                        container=member.container,
                    )
                )
                continue
            outcomes.append(
                LockOutcome(
                    full_name=member.full_name,
                    path=member.lock_path,
                    created=created,
                    reason="" if created else "已存在，未覆盖",
                )
            )

        if member.lock_issue is not None:
            issues.append(
                AbilityScanIssue(
                    path=str(member.lock_path),
                    reason=member.lock_issue,
                    container=member.container,
                )
            )

    return tuple(outcomes), tuple(issues)


def relock_skill(skill: AbilitySkill, *, recorded_at: str | None = None) -> LockRecord:
    """强制重登一个技能的 lock（会覆盖既有登记）。

    用在「内容确实变了、我要承认新状态」的场景：先看 :attr:`AbilitySkill.drifted`，
    确认过变更内容再重登。扫描不会因为重登而改变结论——下次扫描读到的就是新登记。

    Args:
        skill: 目标成员（需来自文件系统上的根，``skill_dir`` 不为 None）。
        recorded_at: 登记时间戳；None 取当前 UTC 时间。

    Returns:
        新写入的 :class:`LockRecord`（重新从盘上读回，而非回显内存里的那份）。

    Raises:
        SkillLockError: 成员不在文件系统上，或登记文件写不进去。
    """
    if skill.skill_dir is None or skill.lock_path is None:
        raise SkillLockError(f"成员 {skill.full_name!r} 不在文件系统上，无法登记")
    data = _lock_data(skill, recorded_at or utc_now_rfc3339())
    replace_lock(skill.lock_path, data)
    return read_lock_record(skill.lock_path, body_relpath=skill.body_path)


def verify_skill(skill: AbilitySkill) -> BundleVerification:
    """整树校验一个技能（昂贵路径：会遍历容器目录的每一个文件）。

    扫描期的 :attr:`AbilitySkill.fingerprint` 只读入口两份文件，够回答"成员是否
    变过"；回答"改了什么"才需要整树比对。用在审计、或怀疑入口之外的文件被动过时。

    Args:
        skill: 目标成员。

    Returns:
        :class:`BundleVerification`。内容维度与快照维度分开裁决；未登记或登记时
        降级时两个维度都是 None（无法裁决）。

    Raises:
        SkillLockError: 成员不在文件系统上。
    """
    if skill.skill_dir is None or skill.lock_path is None:
        raise SkillLockError(f"成员 {skill.full_name!r} 不在文件系统上，无法校验")

    record: LockRecord | None = None
    lock_issue: str | None = None
    if skill.lock_path.is_file():
        try:
            record = read_lock_record(skill.lock_path, body_relpath=skill.body_path)
        except SkillLockError as exc:
            lock_issue = str(exc)
    else:
        lock_issue = "尚无登记文件"

    current = build_manifest(skill.skill_dir, full_name=skill.full_name)
    changes = diff_manifests(record.manifest, current) if record is not None else ()
    return BundleVerification(
        full_name=skill.full_name,
        skill_dir=skill.skill_dir,
        lock_path=skill.lock_path,
        record=record,
        current=current,
        changes=changes,
        lock_issue=lock_issue,
    )


def _lock_data(member: AbilitySkill, recorded_at: str) -> dict[str, Any]:
    """为一个成员算出待写入的 lock 载荷（整树 manifest + 摘要块）。

    登记是写操作，需要整树摘要，因此这里**再走一遍目录**——扫描只读入口，
    写入才建 manifest。这个代价只付在显式登记的调用上。

    Args:
        member: 目标成员（``skill_dir`` 必须存在）。
        recorded_at: 登记时间戳（RFC 3339）。

    Returns:
        ``yaml.safe_dump`` 用的载荷字典。
    """
    assert member.skill_dir is not None  # 调用方已保证
    manifest = build_manifest(member.skill_dir, full_name=member.full_name)
    return build_lock(
        full_name=member.full_name,
        source=member.source,
        manifest=manifest,
        frontmatter={},
        recorded_at=recorded_at,
    )
