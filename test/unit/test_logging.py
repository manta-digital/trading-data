"""Tests for the structured logging module."""

from __future__ import annotations

import json
import logging

import pytest

from manta_trading.logging import _JsonFormatter, get_logger, setup_logging


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Ensure a clean root logger for every test."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


class _FakeSettings:
    """Minimal settings stub for testing."""

    def __init__(self, log_level: str = "INFO", log_format: str = "text"):
        self.log_level = log_level
        self.log_format = log_format


class TestJsonFormatter:
    """Verify _JsonFormatter output structure."""

    def test_produces_valid_single_line_json(self):
        fmt = _JsonFormatter()
        record = logging.LogRecord(
            name="test.module",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)

        assert "\n" not in output
        assert parsed["level"] == "INFO"
        assert parsed["name"] == "test.module"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed

    def test_includes_exception_when_present(self):
        fmt = _JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test.exc",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="something failed",
            args=(),
            exc_info=exc_info,
        )
        output = fmt.format(record)
        parsed = json.loads(output)

        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
        assert "boom" in parsed["exception"]

    def test_no_exception_field_when_no_exc_info(self):
        fmt = _JsonFormatter()
        record = logging.LogRecord(
            name="test.no_exc",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="clean",
            args=(),
            exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert "exception" not in parsed


class TestSetupLogging:
    """Verify setup_logging configuration behavior."""

    def test_json_format_attaches_json_formatter(self):
        setup_logging(_FakeSettings(log_format="json"))
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, _JsonFormatter)

    def test_text_format_attaches_text_formatter(self):
        setup_logging(_FakeSettings(log_format="text"))
        root = logging.getLogger()
        assert len(root.handlers) == 1
        formatter = root.handlers[0].formatter
        assert not isinstance(formatter, _JsonFormatter)
        assert "%(levelname)" in formatter._fmt

    def test_respects_log_level_debug(self):
        setup_logging(_FakeSettings(log_level="DEBUG"))
        assert logging.getLogger().level == logging.DEBUG

    def test_respects_log_level_warning(self):
        setup_logging(_FakeSettings(log_level="WARNING"))
        assert logging.getLogger().level == logging.WARNING

    def test_idempotent_no_duplicate_handlers(self):
        settings = _FakeSettings()
        setup_logging(settings)
        setup_logging(settings)
        assert len(logging.getLogger().handlers) == 1

    def test_invalid_level_falls_back_to_info(self):
        setup_logging(_FakeSettings(log_level="NONEXISTENT"))
        assert logging.getLogger().level == logging.INFO


class TestGetLogger:
    """Verify get_logger returns a named logger."""

    def test_returns_named_logger(self):
        logger = get_logger("my.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "my.module"

    def test_same_name_returns_same_instance(self):
        a = get_logger("shared.name")
        b = get_logger("shared.name")
        assert a is b
