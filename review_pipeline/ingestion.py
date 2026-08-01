# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ingestion stage: load and validate review records.

The first pipeline stage (see ``docs/pipeline-contracts.md``). It accepts either
a filesystem path to a dataset JSON file or an already-parsed in-memory list of
review dicts, validates each record against ``data/schema/review.schema.json``,
and returns the valid records as new dicts.

Invalid records are dropped and reported via structured logging rather than
aborting the whole batch, so a single malformed review never blocks the rest of
the dataset. This stage is local-only: no boto3 / AWS access, no global state,
and no client construction at import time.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Union

from jsonschema import Draft202012Validator

from review_pipeline.logging_config import get_logger

logger = get_logger(__name__)

# A validated review record. Shapes flow between stages as plain JSON-serializable
# dicts, so we keep the alias loose (str keys, arbitrary JSON values).
Review = Dict[str, Any]

# Accepted inputs: a path to a dataset JSON file, or an in-memory list of records.
ReviewSource = Union[str, Path, List[Review]]

# Location of the dataset schema, resolved relative to the repository root
# (this module lives at ``<repo>/review_pipeline/ingestion.py``).
_REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = _REPO_ROOT / "data" / "schema" / "review.schema.json"

# Top-level key in the dataset object that holds the array of review records.
_REVIEWS_KEY = "reviews"


def _load_review_validator() -> Draft202012Validator:
    """Build a JSON-Schema validator for a single review record.

    The dataset schema describes the whole dataset object; individual review
    records are defined under ``$defs/review``. We validate records against that
    sub-schema while keeping ``$defs`` resolvable for any internal references.

    Raises:
        ValueError: if the schema file cannot be read or parsed.
    """
    try:
        schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not read review schema at {SCHEMA_PATH}: {exc}") from exc

    try:
        dataset_schema = json.loads(schema_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"review schema at {SCHEMA_PATH} is not valid JSON: {exc}"
        ) from exc

    review_schema = {
        "$schema": dataset_schema.get("$schema"),
        "$defs": dataset_schema.get("$defs", {}),
        "$ref": "#/$defs/review",
    }
    Draft202012Validator.check_schema(review_schema)
    return Draft202012Validator(review_schema)


def _read_records_from_path(source: Union[str, Path]) -> List[Any]:
    """Read a dataset JSON file and return its ``reviews`` array.

    Args:
        source: filesystem path to a dataset JSON file.

    Returns:
        The raw (unvalidated) list of review records from the dataset.

    Raises:
        ValueError: if the file is missing, unreadable, not valid JSON, or does
            not contain a top-level ``reviews`` array.
    """
    path = Path(source)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"dataset file not found: {path}") from exc
    except OSError as exc:
        raise ValueError(f"could not read dataset file {path}: {exc}") from exc

    try:
        dataset = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"dataset file {path} is not valid JSON: {exc}") from exc

    if not isinstance(dataset, dict):
        raise ValueError(
            f"dataset file {path} must contain a JSON object with a "
            f"{_REVIEWS_KEY!r} array"
        )
    records = dataset.get(_REVIEWS_KEY)
    if not isinstance(records, list):
        raise ValueError(
            f"dataset file {path} must contain a {_REVIEWS_KEY!r} array"
        )
    return records


def _summarize_error(record: Any, error: Any) -> str:
    """Build a concise, body-free description of a schema validation failure."""
    location = "/".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{location}: {error.message}"


def load_reviews(source: ReviewSource) -> List[Review]:
    """Load review records and return only those valid against the review schema.

    Args:
        source: either a filesystem path (``str`` or ``Path``) to a dataset JSON
            file whose top-level ``reviews`` key holds the records, or an
            in-memory list of review dicts.

    Returns:
        A list of valid review dicts. Each returned dict is a fresh copy; the
        input is never mutated.

    Raises:
        ValueError: if ``source`` is an unsupported type, or (for a path) if the
            file cannot be read, is not valid JSON, or lacks a ``reviews`` array.
            Individual invalid records do NOT raise; they are dropped and logged.
    """
    if isinstance(source, (str, Path)):
        raw_records = _read_records_from_path(source)
    elif isinstance(source, list):
        raw_records = source
    else:
        raise ValueError(
            "source must be a path (str/Path) or a list of review dicts, "
            f"got {type(source).__name__}"
        )

    validator = _load_review_validator()

    valid_reviews: List[Review] = []
    dropped_count = 0
    for record in raw_records:
        errors = sorted(validator.iter_errors(record), key=lambda err: err.absolute_path)
        if errors:
            dropped_count += 1
            review_id = record.get("review_id") if isinstance(record, dict) else None
            logger.warning(
                "dropping invalid review record",
                extra={
                    "review_id": review_id,
                    "validation_error": _summarize_error(record, errors[0]),
                    "validation_error_count": len(errors),
                },
            )
            continue
        # Copy so downstream stages cannot mutate the caller's input.
        valid_reviews.append(copy.deepcopy(record))

    logger.info(
        "ingestion complete",
        extra={
            "loaded_count": len(raw_records),
            "valid_count": len(valid_reviews),
            "dropped_count": dropped_count,
        },
    )
    return valid_reviews
