"""logging_setup 的测试：定向级别解析与日志装配。

不启动端侧进程：只验证「级别怎么解析」「装配后根级别/定向级别/文件输出对不对」。
装配会改进程级 logging 状态，故用 autouse fixture 快照并恢复 root handlers 与级别。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ghrah.subject.logging_setup import configure_logging, parse_log_levels

_TOUCHED_LOGGERS = ("ghrah", "ghrah.test.logging_setup", "aiosqlite")


@pytest.fixture(autouse=True)
def _restore_logging() -> object:
    """保存并恢复 root handler/级别与几个被定向设置的 logger 级别。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_root_level = root.level
    saved_logger_levels = {name: logging.getLogger(name).level for name in _TOUCHED_LOGGERS}
    try:
        yield None
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            if handler not in saved_handlers:
                handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_root_level)
        for name, level in saved_logger_levels.items():
            logging.getLogger(name).setLevel(level)


class TestParseLogLevels:
    """``logger=LEVEL`` 串的解析与非法写法。"""

    def test_empty_spec_yields_no_overrides(self) -> None:
        assert parse_log_levels("") == {}
        assert parse_log_levels("   ") == {}
        assert parse_log_levels(" , ") == {}

    def test_parses_multiple_entries_and_normalizes_case(self) -> None:
        assert parse_log_levels("ghrah=DEBUG, aiosqlite=warning") == {
            "ghrah": "DEBUG",
            "aiosqlite": "WARNING",
        }

    @pytest.mark.parametrize(
        "spec",
        ["ghrah", "ghrah=", "=DEBUG", "ghrah=VERBOSE", "ghrah=DEBUG,aiosqlite="],
    )
    def test_invalid_entries_fail_loudly(self, spec: str) -> None:
        with pytest.raises(ValueError):
            parse_log_levels(spec)


class TestConfigureLogging:
    """装配行为：根级别、定向级别、文件输出。"""

    def test_without_file_returns_none_and_replaces_handlers(self) -> None:
        assert configure_logging(level="WARNING") is None
        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert len(root.handlers) == 1

    def test_writes_records_to_file_while_keeping_terminal(self, tmp_path: Path) -> None:
        log_file = tmp_path / "nested" / "subject.log"

        resolved = configure_logging(level="INFO", log_file=str(log_file))

        assert resolved == log_file
        assert len(logging.getLogger().handlers) == 2
        logging.getLogger("ghrah.test.logging_setup").warning("轨迹落盘")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "轨迹落盘" in log_file.read_text(encoding="utf-8")

    def test_targeted_levels_leave_root_level_alone(self) -> None:
        configure_logging(level="INFO", levels="ghrah=DEBUG,aiosqlite=WARNING")

        root = logging.getLogger()
        assert root.level == logging.INFO
        assert logging.getLogger("ghrah").level == logging.DEBUG
        assert logging.getLogger("aiosqlite").level == logging.WARNING

    def test_invalid_level_fails_loudly(self) -> None:
        with pytest.raises(ValueError):
            configure_logging(level="LOUD")
        with pytest.raises(ValueError):
            configure_logging(level="INFO", levels="ghrah=LOUD")
