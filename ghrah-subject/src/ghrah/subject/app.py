"""ghrah-subject 应用入口（Ouroboros 装配形态）。

``async with Context()`` + ``assemble_subject(profile="full")`` + 常驻等待；
退出（含取消）由 Context ``__aexit__`` dispose 全部 fiber。
"""

from __future__ import annotations

import asyncio
import logging

from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import SubjectConfig
from ghrah.subject.logging_setup import configure_logging
from ghrah.subject.runtime.assembly import assemble_subject


async def run_forever() -> None:
    """异步入口：装配 Subject 运行时并常驻（断线/生命周期由各 unit 自理）。"""
    config = SubjectConfig.from_env()
    async with Context() as ctx:
        await assemble_subject(ctx, config, profile="full")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            logger = logging.getLogger(__name__)
            logger.info("run_forever cancelled, disposing via Context exit")
            raise


def main() -> None:
    """Subject 服务入口函数。"""
    config = SubjectConfig.from_env()

    log_path = configure_logging(
        level=config.log_level, levels=config.log_levels, log_file=config.log_file
    )

    logger = logging.getLogger(__name__)
    logger.info("ghrah-subject starting (ouroboros, profile=full)...")
    logger.info("  workspace_root: %s", config.workspace_root)
    logger.info("  db_path: %s", config.db_path)
    if log_path is not None:
        logger.info("  log_file: %s", log_path)

    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down...")


if __name__ == "__main__":
    main()
