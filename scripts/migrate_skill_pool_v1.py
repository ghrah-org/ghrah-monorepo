# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""把技能池里的旧格式能力包迁移成 v1 两段式（多技能目录结构）。

旧格式（``schema_version: 0.1-draft`` / ``0.2``）把成员写在顶层 ``skills`` 表，
正文固定 ``skills/SKILL.md``；v1 把成员收进 ``members``，每个技能独占
``skills/<name>/SKILL.md``，技能名与目录名一致，名字与说明由正文 frontmatter 声明。

默认只打印计划（dry-run）；加 ``--apply`` 才动盘，动盘前整池备份一份。

    uv run python scripts/migrate_skill_pool_v1.py ~/.ghrah/skill-ability
    uv run python scripts/migrate_skill_pool_v1.py ~/.ghrah/skill-ability --apply
"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from ghrah.ability.loading.ability_schema import parse_definition

LOCK_NAME = "ability.lock.yaml"
DEFINITION_NAME = "ability.yaml"
OBSOLETE_VERSIONS = {"0.1-draft", "0.2"}


@dataclass(frozen=True)
class Plan:
    """一个包的迁移计划（dry-run 与 apply 共用同一份）。"""

    package: Path
    skill: str
    body_from: Path
    body_to: Path
    body_text: str
    definition_text: str
    drop_lock: Path | None


def _frontmatter(metadata: dict[str, Any]) -> str:
    """生成 SKILL.md 的 frontmatter 块（YAML，LF 结尾）。"""
    dumped = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False, width=100)
    return "---\n" + dumped + "---\n\n"


def _plan_package(package: Path) -> Plan | None:
    """读一个旧包，产出迁移计划；已是 v1 或形状不认识时返回 None。"""
    definition_path = package / DEFINITION_NAME
    data = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{package}: ability.yaml 不是映射")
    if str(data.get("schema_version")) not in OBSOLETE_VERSIONS:
        print(f"  跳过（已是 {data.get('schema_version')!r}）")
        return None

    skills = data.get("skills") or {}
    if not isinstance(skills, dict) or len(skills) != 1:
        raise ValueError(f"{package}: 期望恰好一个旧 skill 成员，实际 {len(skills)} 个")
    ((key, skill),) = skills.items()

    body_from = package / str(skill["body"]["path"])
    body_to = package / "skills" / key / "SKILL.md"
    body_text = body_from.read_text(encoding="utf-8")

    description = " ".join(str(skill.get("description") or data["description"]).split())
    title = str(skill.get("title") or data["title"])
    tags = [str(item) for item in (skill.get("tags") or [])]
    frontmatter: dict[str, Any] = {"name": key, "description": description}
    if title or tags:
        frontmatter["metadata"] = {}
        if title:
            frontmatter["metadata"]["title"] = title
        if tags:
            frontmatter["metadata"]["tags"] = tags
    new_body = body_text if body_text.startswith("---\n") else _frontmatter(frontmatter) + body_text

    member: dict[str, Any] = {
        "title": title,
        "description": description,
        "body": {"path": f"skills/{key}/SKILL.md", "media_type": "text/markdown"},
        "assets": {},
        "references": {},
        "scripts": {},
    }
    if tags:
        member["tags"] = tags
    if isinstance(skill.get("annotations"), dict) and skill["annotations"]:
        member["annotations"] = skill["annotations"]

    identity = dict(data.get("identity") or {})
    identity.pop("name", None)
    new_definition = {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": identity,
        "title": str(data["title"]),
        "description": str(data["description"]).strip(),
        "members": {"operations": {}, "skills": {key: member}},
    }
    if data.get("origin", "source") != "source":
        new_definition["origin"] = data["origin"]
    definition_text = yaml.safe_dump(new_definition, allow_unicode=True, sort_keys=False, width=100)
    parse_definition(definition_text, source=str(package))

    lock = package / LOCK_NAME
    return Plan(
        package=package,
        skill=key,
        body_from=body_from,
        body_to=body_to,
        body_text=new_body,
        definition_text=definition_text,
        drop_lock=lock if lock.is_file() else None,
    )


def _apply(plan: Plan) -> None:
    """落盘：移动正文、重写定义、删除过期 lock。"""
    plan.body_to.parent.mkdir(parents=True, exist_ok=True)
    plan.body_to.write_text(plan.body_text, encoding="utf-8", newline="\n")
    if plan.body_from != plan.body_to:
        plan.body_from.unlink()
        parent = plan.body_from.parent
        if parent != plan.package / "skills" and not any(parent.iterdir()):
            parent.rmdir()
    (plan.package / DEFINITION_NAME).write_text(
        plan.definition_text, encoding="utf-8", newline="\n"
    )
    if plan.drop_lock is not None:
        plan.drop_lock.unlink()


def _stage(plans: list[Plan], stage: Path) -> None:
    """把迁移结果写进暂存目录，供无写权限的宿主用 shell 落盘。

    每个包在 ``<stage>/<包名>/`` 下生成新的 ``ability.yaml`` 与
    ``skills/<name>/SKILL.md``；同时写 ``plan.tsv``（包名、旧正文、新正文、是否删
    lock），shell 侧按这份清单 cp/rm 即可。
    """
    stage.mkdir(parents=True, exist_ok=True)
    rows = []
    for plan in plans:
        target = stage / plan.package.name
        body_rel = plan.body_to.relative_to(plan.package).as_posix()
        body = target / body_rel
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_text(plan.body_text, encoding="utf-8", newline="\n")
        (target / DEFINITION_NAME).write_text(plan.definition_text, encoding="utf-8", newline="\n")
        old_rel = plan.body_from.relative_to(plan.package).as_posix()
        drop_lock = int(plan.drop_lock is not None)
        rows.append(f"{plan.package.name}\t{old_rel}\t{body_rel}\t{drop_lock}")
    (stage / "plan.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    print(f"\n已暂存 {len(plans)} 个包到 {stage}（plan.tsv 描述落盘动作）")


def main(argv: list[str] | None = None) -> int:
    """扫描技能池，生成并（可选）执行迁移计划。"""
    parser = argparse.ArgumentParser(description="把技能池迁移到 v1 两段式")
    parser.add_argument("root", type=Path, help="技能池根目录（每个子目录一个包）")
    parser.add_argument("--apply", action="store_true", help="真正写盘（默认只打印计划）")
    parser.add_argument(
        "--stage",
        type=Path,
        default=None,
        help="不直接写池，而是把新文件与 plan.tsv 写到该目录（宿主无写权限时用）",
    )
    args = parser.parse_args(argv)
    root: Path = args.root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"不是目录: {root}")

    plans: list[Plan] = []
    for package in sorted(item for item in root.iterdir() if item.is_dir()):
        if not (package / DEFINITION_NAME).is_file():
            continue
        print(f"[{package.name}]")
        plan = _plan_package(package)
        if plan is not None:
            plans.append(plan)
            print(f"  {plan.skill}: {plan.body_from.name} -> {plan.body_to.relative_to(package)}")
    if not plans:
        print("没有需要迁移的包")
        return 0

    if args.stage is not None:
        _stage(plans, args.stage.resolve())
        return 0

    if not args.apply:
        print(f"\n计划 {len(plans)} 个包（dry-run；加 --apply 才写盘）")
        return 0

    backup = root / f".backup-v1-{datetime.now():%Y%m%d-%H%M%S}"
    backup.mkdir()
    for item in root.iterdir():
        if item.name == backup.name:
            continue
        if item.is_dir():
            shutil.copytree(item, backup / item.name)
        else:
            shutil.copy2(item, backup / item.name)
    print(f"\n已备份到 {backup}")
    for plan in plans:
        _apply(plan)
    print(f"已迁移 {len(plans)} 个包")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
