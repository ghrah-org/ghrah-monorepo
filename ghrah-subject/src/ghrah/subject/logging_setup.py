# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""端侧日志装配（根级别 + 定向级别 + 可选文件输出）。

为什么需要：端侧观察执行轨迹时，把根 logger 开到 DEBUG 会被第三方库洪泛——
``aiosqlite`` 会逐条 SQL 打 DEBUG（实测 20 秒 1.35 MB，并把启动拖过健康检查的
10 秒硬超时），而真正想看的 ``ghrah.*`` 轨迹反而被淹没。本模块支持把级别
**定向**到指定 logger（如 ``ghrah=DEBUG,aiosqlite=WARNING``），根级别不受影响；
并可把同一份日志落盘，便于观察者（人或工具）在进程 stdout 被调试器/IDE 占用时
仍能读到轨迹。

零隐式行为：``levels`` 与 ``log_file`` 为空时行为等价于原先的
``logging.basicConfig(level=..., format=...)``；写法非法立即报错，不静默忽略。

用法::

    configure_logging(level="INFO", levels="ghrah=DEBUG", log_file="~/ghrah.log")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

__all__ = ["DEFAULT_FORMAT", "configure_logging", "parse_log_levels"]


def parse_log_levels(spec: str) -> dict[str, str]:
    """解析 ``logger=LEVEL,logger=LEVEL`` 形式的定向级别串。

    Args:
        spec: 定向级别串；空串或纯空白返回空字典。

    Returns:
        logger 名 → 级别名的映射。

    Raises:
        ValueError: 条目缺少 ``=``、缺 logger 名或级别名，或级别名不是合法
            logging 级别（如 ``ghrah=VERBOSE``）。
    """
    levels: dict[str, str] = {}
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        name, sep, level = item.partition("=")
        name, level = name.strip(), level.strip().upper()
        if not sep or not name or not level:
            raise ValueError(f"非法定向级别条目: {item!r}（应为 logger=LEVEL）")
        if not isinstance(getattr(logging, level, None), int):
            raise ValueError(f"未知日志级别: {level!r}（条目 {item!r}）")
        levels[name] = level
    return levels


def configure_logging(*, level: str = "INFO", levels: str = "", log_file: str = "") -> Path | None:
    """装配端侧日志（终端始终输出；指定文件时终端与文件双写）。

    Args:
        level: 根日志级别。
        levels: 定向级别串，如 ``"ghrah=DEBUG,aiosqlite=WARNING"``；只改指定
            logger 的级别，根级别不受影响。
        log_file: 日志文件路径（``~`` 会展开）；空 = 只写终端。

    Returns:
        实际写入的日志文件路径；未指定时为 None。

    Raises:
        ValueError: ``level`` 或 ``levels`` 写法非法。
        OSError: 日志文件所在目录不可创建或文件不可写。
    """
    root_level = getattr(logging, level.strip().upper(), None)
    if not isinstance(root_level, int):
        raise ValueError(f"未知日志级别: {level!r}")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    resolved: Path | None = None
    if log_file.strip():
        resolved = Path(os.path.expanduser(log_file.strip()))
        resolved.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(resolved, encoding="utf-8"))

    # force=True：端侧入口装配一次即定稿，避免重复调用叠加 handler。
    logging.basicConfig(level=root_level, format=DEFAULT_FORMAT, handlers=handlers, force=True)
    for name, level_name in parse_log_levels(levels).items():
        logging.getLogger(name).setLevel(level_name)
    return resolved
