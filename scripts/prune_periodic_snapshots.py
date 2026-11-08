#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""清理 ghrah v3 上下文库中历史遗留的周期性快照载荷。

背景：旧版本按 ``snapshot_interval`` 周期性写入完整消息快照。机制删除后这些
快照行只剩冗余载荷，消息可以从根节点/压缩节点快照与后续 delta 重建。本脚本
面向离线维护：默认 dry-run（预检 + 等价性门禁 + 打印统计，不写库），加
``--apply`` 才真正执行清理。

执行顺序::

    预检（文件存在 / schema v3 / 无并发写连接）
      → 备份（--backup，仅 --apply）
      → 等价性门禁（不通过则退出码 2，且不写库）
      → 单事务 UPDATE + commit（--apply）
      → PRAGMA wal_checkpoint(TRUNCATE)（--apply）
      → VACUUM（--apply --vacuum）
      → 清理后等价性复检 + 前后库大小/候选行数/快照节点数

退出码：0 = 成功；1 = 预检或执行错误；2 = 等价性门禁失败（未写库）。

用法::

    env -u PYTHONPATH uv run python scripts/prune_periodic_snapshots.py \\
        --db /path/to/context.db [--apply] [--backup /path/to/backup.db] [--vacuum]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from ghrah.context.persistence.prune import (
    SCHEMA_VERSION,
    EquivalenceMismatch,
    ScanStats,
    apply_prune,
    check_equivalence,
    scan_stats,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_GATE_FAILED = 2

_LOCK_TIMEOUT_SECONDS = 2.0


class CliError(RuntimeError):
    """预检或执行阶段的致命错误（由 main 统一打印并映射为退出码 1）。"""


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器（中文帮助文本）。"""
    parser = argparse.ArgumentParser(
        prog="prune_periodic_snapshots",
        description=(
            "清理 ghrah v3 上下文库中历史遗留的周期性快照载荷（根节点与 compact 节点保留）。"
        ),
        epilog=(
            "默认 dry-run：只做预检、等价性门禁与统计，不写库。\n"
            "退出码：0 = 成功；1 = 预检/执行错误；2 = 等价性门禁失败（未写库）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        required=True,
        metavar="PATH",
        help="sqlite 数据库文件路径（必填）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行清理（默认 dry-run，不写库）",
    )
    parser.add_argument(
        "--backup",
        metavar="PATH",
        help="执行清理前把数据库备份到该路径（仅在 --apply 时执行；文件必须不存在）",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="清理成功后执行 VACUUM 收缩数据库文件（仅在 --apply 时生效）",
    )
    return parser


def _connect(db_path: Path, *, read_only: bool) -> sqlite3.Connection:
    """打开数据库连接；dry-run 优先使用只读 URI 连接，失败时回退 query_only。"""
    if read_only:
        try:
            uri = f"{db_path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=_LOCK_TIMEOUT_SECONDS)
        except sqlite3.Error:
            # WAL 库缺少 -shm 等情况不支持只读 URI，退回读写连接但禁止写语句
            connection = sqlite3.connect(db_path, timeout=_LOCK_TIMEOUT_SECONDS)
            connection.execute("PRAGMA query_only = ON")
            return connection
        connection.execute("PRAGMA query_only = ON")
        return connection
    return sqlite3.connect(db_path, timeout=_LOCK_TIMEOUT_SECONDS)


def _require_schema_version(connection: sqlite3.Connection) -> int:
    """读取并校验 ``context_schema.version``，返回版本号。"""
    try:
        row = connection.execute(
            "SELECT version FROM context_schema WHERE singleton = 1"
        ).fetchone()
    except sqlite3.Error as exc:
        raise CliError(f"无法读取 context_schema（{exc}）；这不是 ghrah v3 上下文库") from exc
    if row is None:
        raise CliError("context_schema 缺少 singleton=1 记录；这不是 ghrah v3 上下文库")
    version = int(row[0])
    if version != SCHEMA_VERSION:
        raise CliError(
            f"context_schema.version = {version}，本工具只支持 v{SCHEMA_VERSION}；"
            "请先完成 schema 迁移再清理"
        )
    return version


def _require_exclusive_access(db_path: Path) -> None:
    """用临时连接尝试 ``BEGIN EXCLUSIVE``，确认没有并发写连接。"""
    probe: sqlite3.Connection | None = None
    try:
        probe = sqlite3.connect(db_path, timeout=_LOCK_TIMEOUT_SECONDS)
        probe.execute("BEGIN EXCLUSIVE")
        probe.rollback()
    except sqlite3.OperationalError as exc:
        raise CliError(
            f"无法取得库的独占写锁（{exc}）；请先停止使用该库的 ghrah 服务后重试"
        ) from exc
    except sqlite3.Error as exc:
        raise CliError(f"打开数据库失败（{exc}）") from exc
    finally:
        if probe is not None:
            probe.close()


def _backup_database(source: sqlite3.Connection, backup_path: Path) -> None:
    """用 sqlite3 ``Connection.backup()`` 把当前库完整备份到指定路径。"""
    if backup_path.exists():
        raise CliError(f"备份文件已存在，拒绝覆盖：{backup_path}")
    if not backup_path.parent.is_dir():
        raise CliError(f"备份目录不存在：{backup_path.parent}")
    destination: sqlite3.Connection | None = None
    try:
        destination = sqlite3.connect(backup_path)
        source.backup(destination)
    except sqlite3.Error as exc:
        raise CliError(f"备份失败（{exc}）") from exc
    finally:
        if destination is not None:
            destination.close()


def _format_bytes(size: int) -> str:
    """把字节数格式化为便于阅读的单位。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MiB"
    return f"{size / (1024 * 1024 * 1024):.2f} GiB"


def _print_stats(title: str, stats: ScanStats) -> None:
    """打印一组库统计。"""
    print(f"[{title}]")
    print(f"  数据库文件：{stats.db_path or '（未知）'}")
    print(
        f"  文件大小：{_format_bytes(stats.db_bytes)}"
        f"（页面 {stats.page_count} × {stats.page_size} B，空闲页 {stats.freelist_count}）"
    )
    print(
        f"  节点：总数 {stats.nodes_total}；快照行 {stats.snapshot_nodes}；"
        f"根节点 {stats.root_nodes}；压缩节点 {stats.compact_nodes}"
    )
    print(
        f"  候选周期快照：{stats.candidate_rows} 行；"
        f"可释放载荷 {_format_bytes(stats.candidate_payload_bytes)}"
    )
    print(f"  分支数：{stats.branches}")


def _print_mismatches(mismatches: list[EquivalenceMismatch]) -> None:
    """把门禁不一致清单打印到 stderr。"""
    print("等价性门禁未通过：", file=sys.stderr)
    for mismatch in mismatches:
        print(
            f"  - branch={mismatch.branch_id} head={mismatch.head_node_id} "
            f"node={mismatch.node_id}：{mismatch.detail}",
            file=sys.stderr,
        )


def _checkpoint_wal(connection: sqlite3.Connection) -> None:
    """执行 WAL checkpoint(TRUNCATE) 并提示未完成的截断。"""
    row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if row is not None and int(row[0]) != 0:
        print(
            f"警告：WAL checkpoint 被占用（busy={row[0]}），-wal 文件可能未截断。",
            file=sys.stderr,
        )


def _run(args: argparse.Namespace) -> int:
    """按「预检 → 备份 → 门禁 → 清理 → 复检」顺序执行主流程。"""
    db_path = Path(args.db)
    backup_path = Path(args.backup) if args.backup is not None else None
    if not db_path.is_file():
        print(f"错误：数据库文件不存在：{db_path}", file=sys.stderr)
        return EXIT_ERROR
    if backup_path is not None and backup_path.resolve() == db_path.resolve():
        print("错误：--backup 不能指向数据库文件本身", file=sys.stderr)
        return EXIT_ERROR

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(db_path, read_only=not args.apply)
        _require_schema_version(connection)
        _require_exclusive_access(db_path)

        print("存量周期性快照清理工具（ghrah v3）")
        before_stats = scan_stats(connection)
        _print_stats("清理前" if args.apply else "当前库", before_stats)

        if backup_path is not None:
            if args.apply:
                _backup_database(connection, backup_path)
                print(f"已备份到：{backup_path}")
            else:
                print("提示：dry-run 不写任何文件；--backup 仅在 --apply 时执行。")

        # 门禁与 UPDATE 放在同一事务中：读取一致快照，避免检查后数据被改
        try:
            connection.execute("BEGIN EXCLUSIVE" if args.apply else "BEGIN")
        except sqlite3.OperationalError as exc:
            raise CliError(f"无法开始事务（{exc}）；请先停止服务后重试") from exc
        mismatches = check_equivalence(connection)
        if mismatches:
            connection.rollback()
            _print_mismatches(mismatches)
            print("等价性门禁失败：未写库（退出码 2）。", file=sys.stderr)
            return EXIT_GATE_FAILED
        print("等价性门禁通过：所有 branch head 均可从根/压缩边界快照重建。")

        if not args.apply:
            connection.rollback()
            print(
                "dry-run：库未修改。确认无误后加 --apply 执行；"
                "建议同时用 --backup 备份并用 --vacuum 收缩文件。"
            )
            return EXIT_OK

        updated = apply_prune(connection)
        connection.commit()
        print(
            f"已清理候选行：{updated} 行"
            "（messages_snapshot 置 NULL，is_snapshot 置 0；根/compact 行未动）。"
        )
        _checkpoint_wal(connection)
        if args.vacuum:
            connection.execute("VACUUM")
            print("已执行 VACUUM。")

        after_stats = scan_stats(connection)
        mismatches_after = check_equivalence(connection)
        if mismatches_after:
            _print_mismatches(mismatches_after)
            print("警告：清理后等价性复检失败，请从备份恢复数据库。", file=sys.stderr)
            return EXIT_ERROR

        _print_stats("清理后", after_stats)
        print(
            f"数据库文件：{_format_bytes(before_stats.db_bytes)} → "
            f"{_format_bytes(after_stats.db_bytes)}"
            f"（释放 {_format_bytes(max(before_stats.db_bytes - after_stats.db_bytes, 0))}）"
        )
        print("清理完成，清理后等价性复检通过。")
        return EXIT_OK
    except CliError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_ERROR
    except sqlite3.Error as exc:
        print(f"数据库错误：{exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        if connection is not None:
            connection.close()


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：解析参数并返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
