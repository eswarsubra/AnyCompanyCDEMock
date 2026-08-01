# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Behavioural tests for :mod:`review_pipeline.logging_config`.

Verifies that JsonFormatter emits parseable JSON with the documented fields,
that ``extra=`` fields are merged, that exceptions are captured, and that
``configure_logging`` is idempotent. The root logger is saved and restored
around each test so logging state does not leak between tests.
"""
from __future__ import annotations

import io
import json
import logging

import pytest

from review_pipeline.logging_config import (
    JsonFormatter,
    configure_logging,
    get_logger,
)


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """Snapshot and restore the root logger around each test."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def _make_record(**kwargs):
    """Create a LogRecord, optionally carrying extra structured fields."""
    extra = kwargs.pop("extra", {})
    record = logging.LogRecord(
        name=kwargs.get("name", "test.logger"),
        level=kwargs.get("level", logging.INFO),
        pathname=__file__,
        lineno=10,
        msg=kwargs.get("msg", "hello %s"),
        args=kwargs.get("args", ("world",)),
        exc_info=kwargs.get("exc_info"),
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ---------------------------------------------------------------------------
# JsonFormatter: base structure.
# ---------------------------------------------------------------------------
def test_formatter_emits_parseable_json_with_core_fields():
    formatted = JsonFormatter().format(_make_record())
    payload = json.loads(formatted)  # must be valid JSON

    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    # message must be the fully-rendered string, not the template.
    assert payload["message"] == "hello world"
    assert "timestamp" in payload and payload["timestamp"]


def test_formatter_output_is_single_line():
    formatted = JsonFormatter().format(_make_record())
    assert "\n" not in formatted


def test_formatter_level_reflects_record_level():
    rec = _make_record(level=logging.ERROR, msg="boom", args=())
    payload = json.loads(JsonFormatter().format(rec))
    assert payload["level"] == "ERROR"
    assert payload["message"] == "boom"


# ---------------------------------------------------------------------------
# JsonFormatter: extra fields merged.
# ---------------------------------------------------------------------------
def test_formatter_merges_extra_fields():
    rec = _make_record(
        msg="processed",
        args=(),
        extra={"review_id": "r-123", "count": 7},
    )
    payload = json.loads(JsonFormatter().format(rec))

    assert payload["review_id"] == "r-123"
    assert payload["count"] == 7


def test_formatter_does_not_leak_reserved_attrs():
    """Standard LogRecord attributes should not pollute the JSON payload."""
    payload = json.loads(JsonFormatter().format(_make_record()))
    for reserved in ("pathname", "lineno", "args", "levelno", "msg"):
        assert reserved not in payload


# ---------------------------------------------------------------------------
# JsonFormatter: exceptions captured.
# ---------------------------------------------------------------------------
def test_formatter_captures_exception():
    try:
        raise ValueError("kaboom")
    except ValueError:
        import sys

        rec = _make_record(msg="failed", args=(), exc_info=sys.exc_info())

    payload = json.loads(JsonFormatter().format(rec))
    assert "exception" in payload
    assert "ValueError" in payload["exception"]
    assert "kaboom" in payload["exception"]


def test_formatter_no_exception_key_when_none():
    payload = json.loads(JsonFormatter().format(_make_record()))
    assert "exception" not in payload


# ---------------------------------------------------------------------------
# configure_logging behaviour.
# ---------------------------------------------------------------------------
def test_configure_logging_sets_level():
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_unknown_level_falls_back_to_info():
    configure_logging("NOTALEVEL")
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_is_idempotent():
    """Repeated calls must not stack handlers on the root logger."""
    configure_logging("INFO")
    after_first = len(logging.getLogger().handlers)
    configure_logging("INFO")
    configure_logging("INFO")
    after_third = len(logging.getLogger().handlers)

    assert after_first == 1
    assert after_third == 1


def test_configure_logging_installs_json_formatter():
    configure_logging("INFO")
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)


def test_end_to_end_logging_produces_json(monkeypatch):
    """A logger configured via configure_logging emits parseable JSON lines."""
    configure_logging("INFO")
    # Redirect the installed handler to a buffer we control.
    buffer = io.StringIO()
    logging.getLogger().handlers[0].stream = buffer

    logger = get_logger("pipeline.stage")
    logger.info("translated review", extra={"review_id": "r-9", "target": "fr"})

    line = buffer.getvalue().strip()
    payload = json.loads(line)
    assert payload["message"] == "translated review"
    assert payload["logger"] == "pipeline.stage"
    assert payload["review_id"] == "r-9"
    assert payload["target"] == "fr"


def test_get_logger_returns_named_logger():
    logger = get_logger("some.module")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "some.module"
