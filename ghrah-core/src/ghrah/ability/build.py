# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""扫描能力包目录，把生成区 ``members`` 写回 ``ability.yaml``。

这一层只做两件事，都不涉及执行：

1. **扫盘**（:func:`scan_members`）：按目录约定枚举 ``operations/*.py``、``SKILL.md``，
   再按技能目录枚举可选的 ``references/**`` 与 ``assets/**``，产出 ````members`` 区``
   的字典；
2. **写回**（:func:`write_members`）：把生成区拼在手写区之后，原子替换文件。

手写区（``format`` / ``schema_version`` / ``identity`` / ``title`` /
``description``）**逐字节保留**——本模块从不重新序列化它，因此注释、引号风格、
字段顺序都不受工具影响。生成区从 ``# ── 以下由 ... 生成 ──`` 标记行（或裸
``members:`` 行）起到文件末尾整体替换。

扫描侧不 import、不执行包里的代码：实现脚本的模块级字面量（``TITLE`` /
``SUMMARY`` / ``EFFECTS``）用 AST 读取。因此同一份包在任何机器、任何时刻都得到
同一份生成区——重跑一次 ````git diff`` 为空``，这是"生成区可重建"的前提。

用在打包/发布前，或新增成员之后的重新生成::

    python -m ghrah.ability.build path/to/ability-package
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ghrah.ability.loading.ability_schema import (
    DEFINITION_FILE_NAME,
    SCRIPT_PROVIDER_SLOT,
    V1_SCHEMA_VERSION,
    load_yaml,
    parse_definition,
)
from ghrah.ability.loading.manifest_fields import ManifestValidationError
from ghrah.ability.loading.member_index import scan_members as _scan_members
from ghrah.ability.loading.skill_frontmatter import read_skill_frontmatter

__all__ = [
    "GENERATED_MARKER",
    "MemberBuildResult",
    "ensure_members",
    "main",
    "scan_members",
    "write_members",
]

GENERATED_MARKER = "# ── 以下由 `ghrah ability build` 生成，勿手改 ──"
"""生成区起始标记。重写时连同标记在内的整段被替换。"""


@dataclass(frozen=True)
class MemberBuildResult:
    """一次生成的结果：写到了哪、是否变化、生成区内容。"""

    path: Path
    """``ability.yaml`` 的位置。"""

    changed: bool
    """磁盘上的生成区是否与本次扫描结果不同。"""

    members: dict[str, Any]
    """本次扫描出的生成区（可直接对照 YAML 阅读）。"""


def scan_members(package: str | Path, *, body_path: str | None = None) -> dict[str, Any]:
    """扫描一个能力包目录，产出 ````members`` 区`` 的字典（实现见 ``member_index``）。

    这里保留同名入口，便于调用方继续 ``from ghrah.ability.build import scan_members``；
    真正的扫描原语在 :mod:`ghrah.ability.loading.member_index`。

    Args:
        package: 能力包根目录。
        body_path: 定义面已声明的正文路径（相对包根）。

    Returns:
        ``members`` 区的字典；全空时为 ``{}``。
    """
    return _scan_members(package, body_path=body_path)


def write_members(
    package: str | Path,
    *,
    dry_run: bool = False,
    bootstrap: bool = False,
    migrate: bool = False,
) -> MemberBuildResult:
    """扫描并按需写回 ``ability.yaml`` 的生成区。

    写盘前先做自校验：把"手写区 + 新生成区"整段重新走一遍
    :func:`~ghrah.ability.loading.ability_schema.parse_definition`。写不出去的
    文件不会被写出去——不会出现"工具写了一份读不回来的定义"。

    Args:
        package: 能力包根目录。
        dry_run: 只算差异、不写盘（用于 ``--check`` 与预览）。
        bootstrap: 目录里还没有 ``ability.yaml`` 时，按目录与 ``SKILL.md`` 补一份最小
            手写区（容器名取目录名，标题/说明取正文 frontmatter 或旧格式残留）。
        migrate: 定义还是旧格式（``0.1-draft`` / ``0.2``）时，按可推导字段重写成 v1
            手写区再生成（会覆盖旧文件；标题/说明/身份从旧文件读）。

    Returns:
        :class:`MemberBuildResult`。

    Raises:
        ManifestValidationError: 不是 v1 定义、手写区非法、或生成结果读不回来。
        FileNotFoundError: 目录下没有 ``ability.yaml``，且未要求 ``bootstrap``。
    """
    root = Path(package)
    definition_path = root / DEFINITION_FILE_NAME
    if not definition_path.is_file():
        if not bootstrap:
            raise FileNotFoundError(f"缺少定义文件：{definition_path}")
        return _bootstrap_members(root, dry_run=dry_run)

    text = definition_path.read_text(encoding="utf-8")
    authored = _authored_part(text)
    if not authored.strip():
        raise ManifestValidationError(f"{definition_path}: 手写区为空，无法确定身份")

    data = load_yaml(authored)
    if not isinstance(data, dict):
        raise ManifestValidationError(f"{definition_path}: 手写区不是映射")
    version = data.get("schema_version")
    if str(version).strip() != V1_SCHEMA_VERSION:
        if migrate:
            return _migrate_members(root, data, dry_run=dry_run)
        raise ManifestValidationError(
            f"{definition_path}: 只有 schema_version {V1_SCHEMA_VERSION!r} 的能力包"
            f"由 build 生成 members（当前 {version!r}；历史格式加 --migrate 重写，"
            "或继续手写完整定义）"
        )

    members = _keep_declared(scan_members(root, body_path=_declared_body(text)), text)
    new_text = _compose(authored, members)
    rebuilt = parse_definition(new_text, source=str(definition_path))
    if rebuilt.container_name != root.name or set(rebuilt.operations) != set(
        members.get("operations", {})
    ):
        raise ManifestValidationError(
            f"{definition_path}: 生成区读回来与扫描结果不一致"
            f"（容器 {rebuilt.container_name!r} / "
            f"成员 {sorted(rebuilt.operations)}）"
        )

    changed = new_text != text
    if changed and not dry_run:
        _atomic_write(definition_path, new_text)
    return MemberBuildResult(path=definition_path, changed=changed, members=members)


def _migrate_members(
    root: Path,
    legacy: dict[str, Any],
    *,
    dry_run: bool,
) -> MemberBuildResult:
    """把旧格式定义重写成 v1（手写区取可推导字段，生成区按磁盘扫）。

    迁移只搬运能确认的事实：身份（namespace / name / version）、标题、说明。
    旧格式里的 ``input_schema`` 等实现细节属于实现面，不再抄进定义。

    Args:
        root: 包目录。
        legacy: 旧定义文件解析出的映射。
        dry_run: 只算结果、不写盘。

    Returns:
        :class:`MemberBuildResult`。

    Raises:
        ManifestValidationError: 旧定义里没有可用的身份，或迁移结果读不回来。
    """
    identity = legacy.get("identity")
    if not isinstance(identity, dict):
        raise ManifestValidationError(f"{root}: 旧定义缺少 identity，无法迁移")
    namespace = identity.get("namespace") or root.name
    name = identity.get("name")
    version = identity.get("version")
    title, description = _display_identity(root, scan_members(root))
    authored_data: dict[str, Any] = {
        "format": "ghrah.ability-definition",
        "schema_version": V1_SCHEMA_VERSION,
        "identity": {"namespace": namespace, "version": str(version or "0")},
        "title": legacy.get("title") if isinstance(legacy.get("title"), str) else title,
        "description": (
            legacy.get("description") if isinstance(legacy.get("description"), str) else description
        ),
    }
    if isinstance(name, str):
        authored_data["identity"]["name"] = name
    authored = yaml.safe_dump(authored_data, sort_keys=False, allow_unicode=True, width=100).rstrip(
        "\n"
    )
    members = _keep_declared(scan_members(root), "")
    new_text = _compose(authored, members)
    definition_path = root / DEFINITION_FILE_NAME
    rebuilt = parse_definition(new_text, source=str(definition_path))
    if rebuilt.container_name != root.name:
        raise ManifestValidationError(
            f"{definition_path}: 迁移后的容器名 {rebuilt.container_name!r}"
            f" 与目录名 {root.name!r} 不一致"
        )
    if not dry_run:
        _atomic_write(definition_path, new_text)
    return MemberBuildResult(path=definition_path, changed=True, members=members)


def _bootstrap_members(root: Path, *, dry_run: bool) -> MemberBuildResult:
    """给"只有目录和 SKILL.md、还没有 ability.yaml"的包补一份最小定义。

    只写可推导的字段：容器名取目录名、标题与说明取正文 frontmatter（没有则退回
    旧格式残留、再退回目录名）。身份版本留 ``'0'``，等作者填真实版本。

    Args:
        root: 包目录。
        dry_run: 只算结果、不写盘。

    Returns:
        :class:`MemberBuildResult`（``path`` 指向将创建的 ``ability.yaml``）。

    Raises:
        ManifestValidationError: 目录里没有任何可展示成员，或合成结果读不回来。
    """
    members = scan_members(root)
    if not members:
        raise ManifestValidationError(f"{root}: 目录里没有 operations/*.py 或 SKILL.md，无法补定义")
    title, description = _display_identity(root, members)
    authored = yaml.safe_dump(
        {
            "format": "ghrah.ability-definition",
            "schema_version": V1_SCHEMA_VERSION,
            "identity": {"namespace": root.name, "version": "0"},
            "title": title,
            "description": description,
        },
        sort_keys=False,
        allow_unicode=True,
        width=100,
    ).rstrip("\n")
    new_text = _compose(authored, members)
    definition_path = root / DEFINITION_FILE_NAME
    rebuilt = parse_definition(new_text, source=str(definition_path))
    if rebuilt.container_name != root.name:
        raise ManifestValidationError(
            f"{definition_path}: 合成的容器名 {rebuilt.container_name!r}"
            f" 与目录名 {root.name!r} 不一致"
        )
    if not dry_run:
        _atomic_write(definition_path, new_text)
    return MemberBuildResult(path=definition_path, changed=True, members=members)


def _display_identity(root: Path, members: dict[str, Any]) -> tuple[str, str]:
    """取展示用标题与说明：正文 frontmatter → 旧格式残留 → 目录名。

    Args:
        root: 包目录。
        members: 扫描出的生成区（用于定位正文）。

    Returns:
        ``(title, description)``，两者都非空。
    """
    title = root.name
    description = title
    skill = members.get("skill")
    if isinstance(skill, dict):
        body = root.joinpath(*PurePosixPath(str(skill["body"])).parts)
        frontmatter = read_skill_frontmatter(body)
        if frontmatter is not None:
            title = frontmatter.name
            description = frontmatter.description or title
    if title == root.name and description == title:
        legacy_title, legacy_description = _legacy_display(root)
        title = legacy_title or title
        description = legacy_description or title
    return (title, description)


def _legacy_display(root: Path) -> tuple[str | None, str | None]:
    """从旧格式定义里读标题与说明（只取字符串字段，不做校验）。

    Args:
        root: 包目录。

    Returns:
        ``(title, description)``；读不出来时为 ``(None, None)``。
    """
    try:
        data = load_yaml((root / DEFINITION_FILE_NAME).read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, ManifestValidationError:
        return (None, None)
    if not isinstance(data, dict):
        return (None, None)
    title = data.get("title") if isinstance(data.get("title"), str) else None
    description = data.get("description") if isinstance(data.get("description"), str) else None
    return (title, description)


def ensure_members(
    scan: Any,
) -> tuple[tuple[Path, ...], tuple[Any, ...]]:
    """为文件系统上的包补齐生成区（``members``）——与 lock 写在同一个生命周期点。

    ``ability.yaml`` 的两段作者不同：手写区由人维护，生成区由工具在安装 / 初次扫描
    时补齐。这个函数挂在 ``SkillAbilityProvider.initialize`` 里，落点与 ``ensure_locks``
    一致（包目录内的 ``ability.yaml`` 与 ``ability.lock.yaml``）；包资源根不可写，跳过。

    生成是幂等的：已经同步的包不会被重写，也不覆盖手写区。目录里还没有
    ``ability.yaml`` 的包（如从别处收进来的标准 skill 目录）会被补一份最小定义，
    再由同一次调用填上生成区。

    它住在构建层而不是扫描层：扫描只读，写入需要一个明确的调用点。

    Args:
        scan: ``scan_ability_definitions`` 的结果（鸭子类型：需要 ``entries``）。

    Returns:
        ``(被更新的定义文件, 问题)``。写失败只记问题，不影响其余包。
    """
    from ghrah.ability.loading.ability_scan import AbilityScanIssue

    written: list[Path] = []
    issues: list[Any] = []
    for entry in scan.entries:
        if not isinstance(entry.locator, Path):
            continue
        try:
            result = write_members(entry.locator, bootstrap=True)
        except (ManifestValidationError, FileNotFoundError, OSError) as exc:
            issues.append(
                AbilityScanIssue(
                    path=str(entry.locator),
                    reason=f"生成 members 失败: {exc}",
                    container=entry.container,
                )
            )
            continue
        if result.changed:
            written.append(result.path)
    return tuple(written), tuple(issues)


def _declared_body(previous_text: str) -> str | None:
    """读旧定义里声明的正文路径（解析不出来就当没声明）。

    扫描只能靠约定位置找正文；作者把正文放在别处时，靠这一项把位置带回来。

    Args:
        previous_text: 重建前的定义文件全文。

    Returns:
        正文相对路径；没有技能或解析失败时为 None。
    """
    try:
        previous = parse_definition(previous_text, source="previous")
    except ManifestValidationError:
        return None
    for skill in previous.skills.values():
        return skill.body.path
    return None


def _keep_declared(members: dict[str, Any], previous_text: str) -> dict[str, Any]:
    """把扫不出来的作者意图带回生成区。

    四类事实扫描看不到，必须保留：

    - ``provider`` 型 operation（没有包内脚本，实现由运行期后端提供）；
    - ``entry`` 不在 ``operations/`` 下的 operation（如 ``scripts/render.py``）；
    - ``expose: false``（"这个成员不可授予"是意图，不是可推导事实）；
    - 技能键不等于容器名时的原键（成员地址取决于它）。

    扫到的成员一律**以磁盘为准**：作者删了文件就不该再出现在生成区。

    Args:
        members: 本次扫描出的生成区（原地修改）。
        previous_text: 重建前的定义文件全文；解析不出来时原样返回入参。

    Returns:
        合并后的生成区（按 operations → skill → references → assets 固定顺序）。
    """
    try:
        previous = parse_definition(previous_text, source="previous")
    except ManifestValidationError:
        return members

    operations: dict[str, dict[str, Any]] = dict(members.get("operations", {}))
    for key, operation in previous.operations.items():
        if key in operations:
            if not operation.expose:
                operations[key]["expose"] = False
            continue
        slot = operation.implementation.provider_slot
        item: dict[str, Any] = {
            "title": operation.title,
            "description": operation.description,
        }
        if operation.script is None:
            item["provider"] = slot
        else:
            item["entry"] = operation.script.path
            if slot != SCRIPT_PROVIDER_SLOT:
                item["provider"] = slot
        item["effects"] = list(operation.effects)
        if not operation.expose:
            item["expose"] = False
        operations[key] = item

    ordered: dict[str, Any] = {}
    if operations:
        ordered["operations"] = operations
    for name in ("skill", "references", "assets"):
        if name in members:
            ordered[name] = members[name]

    skill = ordered.get("skill")
    ((skill_key, _),) = tuple(previous.skills.items()) or ((None, None),)
    if skill is not None and skill_key is not None and skill_key != previous.container_name:
        skill["key"] = skill_key
    members.clear()
    members.update(ordered)
    return members


def _authored_part(text: str) -> str:
    """切出手写区：标记行（或裸 ``members:`` 行）之前的内容。"""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(GENERATED_MARKER) or line.startswith("members:"):
            return "".join(lines[:index])
    return text


def _compose(authored: str, members: dict[str, Any]) -> str:
    """手写区（原样）+ 标记 + 生成区。"""
    head = authored.rstrip("\n")
    prefix = f"{head}\n\n" if head else ""
    return prefix + GENERATED_MARKER + "\n" + _render(members)


def _render(members: dict[str, Any]) -> str:
    """把生成区渲染成稳定文本（键序即写入顺序，不排序）。"""
    return yaml.safe_dump(
        {"members": members},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=None,
        width=100,
    )


def _atomic_write(path: Path, text: str) -> None:
    """原子替换定义文件（同目录临时文件 + ``os.replace``）。"""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：``python -m ghrah.ability.build <包目录>...``。

    Args:
        argv: 参数列表；None 时取进程参数。

    Returns:
        进程退出码：0 表示成功（``--check`` 下还要求生成区已同步），1 表示有包失败
        或有包待更新。
    """
    parser = argparse.ArgumentParser(
        prog="ghrah ability build",
        description="扫描能力包目录，把生成区 members 写回 ability.yaml",
    )
    parser.add_argument("packages", nargs="+", metavar="PATH", help="能力包根目录")
    parser.add_argument("--dry-run", action="store_true", help="只报告差异，不写盘")
    parser.add_argument("--check", action="store_true", help="生成区未同步时返回 1")
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="把旧格式定义（0.1-draft / 0.2）重写成 v1 再生成",
    )
    args = parser.parse_args(argv)

    status = 0
    writing = not (args.dry_run or args.check)
    for package in args.packages:
        try:
            result = write_members(package, dry_run=not writing, migrate=args.migrate)
        except (ManifestValidationError, FileNotFoundError) as exc:
            print(f"{package}: {exc}", file=sys.stderr)
            status = 1
            continue
        if result.changed and writing:
            verb = "已更新"
        elif result.changed:
            verb = "待更新"
            if args.check:
                status = 1
        else:
            verb = "无变化"
        print(f"{result.path}: {verb}")
    return status


if __name__ == "__main__":  # pragma: no cover - 命令行入口
    raise SystemExit(main())
