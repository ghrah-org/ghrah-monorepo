# SPDX-License-Identifier: Apache-2.0
"""目录面调试：扫包、列成员、渲染技能目录。

运行（仓库根）：``uv run python ghrah-core/examples/ability_debug/01_catalog.py``
PyCharm：解释器选仓库根的 ``.venv``，直接右键运行。
"""

from __future__ import annotations

from ghrah.ability import default_pool, load_skill_catalog, scan_abilities, scan_catalog


def main() -> None:
    """打印目录面的三份读数。"""
    print("== 0. 扫描根（default_pool，rank 小者优先）==")
    for root in default_pool():
        state = "存在" if root.locator.is_dir() else "不存在"
        print(f"  [{root.rank:>3}] {root.label:<14} {root.locator}  ({state})")

    print("\n== 1. scan_abilities()：目录池里有哪些能力包 ==")
    containers = scan_abilities()
    synthesized = 0
    for container in containers:
        flag = " · 合成视图" if container.synthesized else ""
        print(f"{container}{flag}")
        synthesized += int(container.synthesized)
        print("   kinds:", container.kinds, "| full_name:", container.full_name)
        for operation in container.operations:
            print("   op    ", operation.key, "|", operation.title, "|", operation.effects)
        for skill in container.skills:
            print("   skill ", skill.catalog_name, "|", skill.title, "|", skill.body_path)
        for issue in container.issues:
            print("   !     ", issue)
    print(f"\n合计 {len(containers)} 个包，其中 {synthesized} 个定义面缺失/不可用（已按磁盘合成）")

    print("\n== 2. scan_catalog()：目录面 + 实现面的合并报告 ==")
    report = scan_catalog()
    print("containers:", [item.name for item in report.containers])
    print("handlers  :", len(report.handlers), report.handlers[:5], "...")
    print("abilities :", len(report.abilities), "（实现面投影，只含内置树）")
    print("issues    :", len(report.issues))

    print("\n== 3. load_skill_catalog()：模型可见的技能清单 ==")
    catalog = load_skill_catalog()
    print("names :", catalog.names)
    print("digest:", catalog.digest)
    print("---- render() ----")
    print(catalog.render())

    if catalog.skills:
        first = catalog.skills[0]
        document = catalog.read(first.catalog_name)
        print("---- read() ----")
        print("name:", document.name, "| media_type:", document.media_type)
        print("path:", document.path)
        print("content[:80]:", document.content[:80].replace("\n", " "))
    else:
        print("（目录池里没有技能包；放一个带 SKILL.md 的包再看）")


if __name__ == "__main__":
    main()
