# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Structured (JSON) logging for the pipeline.

Emits one JSON object per log record so logs are queryable in CloudWatch Logs
Insights. Kept dependency-free (standard library only) so it works unchanged in
a Lambda runtime.

Convention: log identifiers and counts, not review text. Review bodies may
originate from customers, so stages should log ``review_id`` / ``product_id``
rather than ``body`` to avoid leaking content into logs.
"""
from __future__ import annotations

import json
import logging
from typing import Any

# Standard LogRecord attributes we do NOT want duplicated into the JSON "extra"
# payload — everything else passed via ``extra=`` is treated as structured data.
_RESERVED = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Merge any structured fields passed through ``extra=``.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger to emit JSON to stdout.

    Idempotent: replaces existing handlers so repeated calls (e.g. across Lambda
    invocations) don't stack duplicate handlers.

    Args:
        level: a logging level name (e.g. "INFO", "DEBUG"). Unknown names fall
            back to INFO.
    """
    root = logging.getLogger()
    resolved = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(resolved)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger. Use structured fields via ``logger.info(msg, extra={...})``."""
    return logging.getLogger(name)
