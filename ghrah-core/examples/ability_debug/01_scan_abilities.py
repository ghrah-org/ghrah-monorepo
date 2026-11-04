# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""调试 ``scan_abilities()``：按包分层打印目录面读数。

运行（仓库根）：

    uv run python ghrah-core/examples/ability_debug/01_scan_abilities.py
    uv run python ghrah-core/examples/ability_debug/01_scan_abilities.py <额外扫描根>

PyCharm 里解释器选仓库根的 ``.venv``，对文件右键 Run 即可；工作目录不影响结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

from ghrah.ability import scan_abilities
from ghrah.ability.loading import ability_roots as roots


def _pool(extra: str | None) -> tuple:
    """默认池（内置 + 用户目录），可再追加一个临时根做对照。"""
    pool = roots.default_pool()
    if extra is None:
        return pool
    return (*pool, roots.path_root(Path(extra), rank=50, source="extra"))


def main(argv: list[str]) -> int:
    """打印目录池、每个包的成员分层与汇总。"""
    extra = argv[1] if len(argv) > 1 else None
    pool = _pool(extra)

    print("== 扫描根（rank 小者优先）==")
    for root in pool:
        state = "存在" if root.locator.is_dir() else "不存在"
        print(f"  [{root.rank:>3}] {root.source:<14} {root.label}  ({state})")

    containers = scan_abilities(pool)

    print(f"\n== 能力包：{len(containers)} 个 ==")
    for container in containers:
        print(
            f"\n{container.name}  ({container.full_name} v{container.version})  "
            f"operations={container.operation_count} skills={container.skill_count} "
            f"kinds={container.kinds}"
        )
        print(f"  title      : {container.title}")
        print(f"  description: {container.description}")
        print(f"  origin     : {container.definition.origin}  根: {container.root.label}")

        for operation in container.operations:
            definition = operation.definition
            print(
                f"  - op   {operation.key:<20} expose={definition.expose!s:<5} "
                f"tags={list(definition.tags)} effects={list(definition.effects)}"
            )
            print(
                f"         permissions={definition.permissions or '{}'}  "
                f"input_schema={'有' if definition.input_schema else '无'}  "
                f"timeout={definition.execution.timeout_ms}ms"
            )

        for skill in container.skills:
            print(f"  - skill {skill.key:<20} catalog={skill.catalog_name} tags={list(skill.tags)}")
            print(f"         body={skill.body_path} ({skill.media_type})")
            for label, items in (
                ("assets", skill.assets),
                ("references", skill.references),
                ("scripts", skill.scripts),
            ):
                if items:
                    print(f"         {label}: {[item.path for item in items]}")

        for issue in container.issues:
            print(f"  ! issue: {issue}")

    total_ops = sum(container.operation_count for container in containers)
    total_skills = sum(container.skill_count for container in containers)
    total_issues = sum(len(container.issues) for container in containers)
    print(
        f"\n== 汇总：{len(containers)} 个包 / {total_ops} 个 operation / "
        f"{total_skills} 个 skill / {total_issues} 条包内问题 =="
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
