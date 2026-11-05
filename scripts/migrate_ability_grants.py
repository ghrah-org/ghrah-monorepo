# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""已部署 Agent 的一次性授权迁移 CLI。

读取操作员导出的旧事实快照，把每个 Agent 的初始获授以 ``source=migration``
写入关系库；迁移不读 manifest 模板，也不覆盖已有授权。

用法::

    uv run python scripts/migrate_ability_grants.py \\
        --database /path/to/project.db \\
        --project-id project \\
        --workspace-root /path/to/workspace \\
        --snapshot migration.json

快照形状（操作员显式确认的稳定 Agent ID 与旧事实，不以显示名作键）::

    {
      "agents": {
        "0123456789abcdef0123456789abcdef": [
          {
            "package": "ghrah.fs",
            "members": ["read_file", "write_file"],
            "skills": [],
            "axes": []
          }
        ]
      }
    }

退出码：0 = 全部成功（含已迁移的幂等跳过）；1 = 任一 Agent 迁移失败。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ghrah.ability.access import (
    AbilityAccessStore,
    MigrationGrant,
    migrate_agent,
)
from ghrah.ability.access.identity import validate_agent_id
from ghrah.protocol.ability_tree import MemberAxes, SkillReadScope
from pydantic import TypeAdapter


def _parse_snapshot(path: Path) -> dict[str, list[MigrationGrant]]:
    """解析快照；形状错误直接报错，不猜测或跳过条目。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
        raise SystemExit("snapshot must be an object with an 'agents' object")
    axes_adapter = TypeAdapter(list[MemberAxes])
    skills_adapter = TypeAdapter(list[SkillReadScope])
    result: dict[str, list[MigrationGrant]] = {}
    for agent, entries in data["agents"].items():
        validate_agent_id(agent)
        if not isinstance(entries, list):
            raise SystemExit(f"agents[{agent!r}] must be a list")
        grants: list[MigrationGrant] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("package"), str):
                raise SystemExit(f"agents[{agent!r}] entry must carry a 'package' string")
            grants.append(
                MigrationGrant(
                    package=entry["package"],
                    members=tuple(entry.get("members", ())),
                    skills=tuple(skills_adapter.validate_python(entry.get("skills", []))),
                    axes=tuple(axes_adapter.validate_python(entry.get("axes", []))),
                )
            )
        result[agent] = grants
    return result


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    snapshot = _parse_snapshot(Path(args.snapshot))
    store = await AbilityAccessStore.open(
        Path(args.database),
        args.project_id,
        operators=frozenset({"migration"}),
        runs=lambda agent: None,
    )
    try:
        summary: dict[str, Any] = {}
        for agent, grants in snapshot.items():
            result = await migrate_agent(
                store, agent, grants=grants, workspace_root=args.workspace_root
            )
            summary[agent] = {
                "revision": result.revision,
                "aliases": list(result.aliases),
                "already_migrated": result.already_migrated,
            }
        return summary
    finally:
        await store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-off ability grant migration")
    parser.add_argument("--database", required=True, help="项目数据库文件路径")
    parser.add_argument("--project-id", required=True, help="项目标识")
    parser.add_argument("--workspace-root", required=True, help="Agent 工作区根（文件成员沙盒）")
    parser.add_argument("--snapshot", required=True, help="旧事实快照 JSON 路径")
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(_run(args))
    except Exception as error:  # noqa: BLE001 - CLI 边界打印并返回非零
        print(f"migration failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
