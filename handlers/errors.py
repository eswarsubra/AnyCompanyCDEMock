# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared error handling for the pipeline Lambda handlers.

Each handler talks to external AWS services (S3, and — depending on the stage —
Amazon Translate or Amazon Bedrock). Those calls can fail after boto3's built-in
retries (throttling, transient 5xx, missing objects, permission changes). This
module gives handlers one place to turn such a failure into a *logged,
contextual* error instead of an opaque crash.

Two behaviours, matching the two invocation styles in this pipeline:

* :func:`stage_boundary` — for the batch stages, which run under the Step
  Functions state machine. It logs structured context and **re-raises**, so the
  state machine's retry/backoff and failure recording take over (see the retry
  policy in ``infra/stacks/pipeline_stack.py``).
* :func:`client_error_code` — a helper the synchronous API handler uses to log
  the same context before returning a ``500`` response to API Gateway.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional


def client_error_code(exc: BaseException) -> Optional[str]:
    """Return the AWS error code from a botocore ``ClientError``, if present.

    Uses duck typing on the exception's ``response`` mapping rather than
    importing botocore, so this module stays import-side-effect free and unit
    tests never need AWS libraries configured. Walks the ``__cause__`` chain so
    the code is still found when the ``ClientError`` has been wrapped (e.g. by
    :class:`handlers.s3_io.S3IOError`). Returns ``None`` for any exception with
    no AWS error code anywhere in its cause chain.
    """
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        if isinstance(response, dict):
            error = response.get("Error")
            if isinstance(error, dict) and error.get("Code"):
                return error.get("Code")
        current = current.__cause__
    return None


@contextmanager
def stage_boundary(logger: Any, *, stage: str, **context: Any) -> Iterator[None]:
    """Log and re-raise any failure from a batch stage's external-service calls.

    Wrap the read → process → write body of a batch handler in this context
    manager. If any wrapped call raises, the failure is logged once with
    structured context (the stage name, the S3 bucket/keys, the exception type,
    and the AWS error code when available) and then re-raised unchanged.

    Re-raising is intentional: the batch stages are invoked synchronously by the
    Step Functions state machine, which retries transient faults with backoff
    and records terminal failures. Swallowing the error here would hide failed
    runs and defeat that retry/catch.

    Args:
        logger: the handler's structured logger.
        stage: the stage name, used as a log field (e.g. ``"translation"``).
        **context: extra structured log fields (e.g. ``bucket``, ``source_key``,
            ``target_key``).
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 — logged with context, then re-raised
        logger.error(
            "stage handler failed at an external boundary",
            extra={
                "stage": stage,
                "error_type": type(exc).__name__,
                "error_code": client_error_code(exc),
                **context,
            },
        )
        raise
