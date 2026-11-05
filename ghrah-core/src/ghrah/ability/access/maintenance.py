# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""停机维护 Ability 域：完整备份项目数据库，只清理本域的关系与历史。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

ABILITY_TABLES = (
    "ability_tree_schema",
    "ability_versions",
    "ability_content",
    "ability_instances",
    "ability_agents",
    "ability_grants",
    "ability_tools",
    "ability_changes",
    "ability_admissions",
    "ability_registrations",
)


def inspect_ability_domain(database: Path) -> tuple[str, ...]:
    """以只读连接列出维护操作将删除的本域表。"""
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return tuple(name for name in ABILITY_TABLES if name in names)


def reset_ability_domain(database: Path, backup: Path) -> tuple[str, ...]:
    """在调用方已停止相关实例后备份整库，并事务性清理 Ability 自有表。

    备份目标必须不存在；不按旧名称推断 UUID，不删除 Context、消息或其他领域表。
    """
    tables = inspect_ability_domain(database)
    if database.resolve() == backup.resolve():
        raise ValueError("备份路径不能与项目数据库相同")
    if backup.exists():
        raise FileExistsError(f"备份文件已存在：{backup}")
    if not tables:
        raise ValueError("没有 Ability 域可重建")
    if not backup.parent.is_dir():
        raise ValueError("备份目录不存在")
    # 排他创建，拒绝覆盖既有备份。
    with backup.open("xb"):
        pass
    with closing(sqlite3.connect(database)) as db, closing(sqlite3.connect(backup)) as saved:
        db.backup(saved)
        db.execute("BEGIN IMMEDIATE")
        try:
            for table in tables:
                db.execute(f'DROP TABLE "{table}"')
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return tables
