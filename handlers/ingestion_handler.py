# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lambda handler for the ingestion stage.

Reads the raw review dataset from S3, runs :func:`review_pipeline.ingestion.load_reviews`
to validate/drop bad records, and writes the validated records back to S3.

Reads ``raw/reviews.json`` -> writes ``staged/ingested.json`` (see
``docs/infra-contracts.md``).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from review_pipeline import ingestion
from review_pipeline.config import load_config
from review_pipeline.logging_config import configure_logging, get_logger

from handlers import keys, s3_io

logger = get_logger(__name__)

# Top-level key in the dataset object that holds the review array. The raw
# dataset in S3 is the full dataset object (see docs/dataset-spec.md); when a
# bare list is stored instead, it is passed straight through.
_REVIEWS_KEY = "reviews"


def _extract_records(raw: Any) -> Any:
    """Return the review records list from the raw S3 object.

    ``review_pipeline.ingestion.load_reviews`` accepts a path or an in-memory
    list, not the dataset *envelope* dict. Handlers receive the already-parsed
    S3 object, so this unwraps the ``reviews`` array from the dataset object
    while passing a bare list through unchanged. Deeper validation stays in the
    wrapped module.
    """
    if isinstance(raw, dict) and _REVIEWS_KEY in raw:
        return raw[_REVIEWS_KEY]
    return raw


def handler(
    event: Optional[Dict[str, Any]] = None,
    context: Optional[Any] = None,
    *,
    s3_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Ingest the raw dataset and stage the validated records.

    Args:
        event: the Lambda event (unused; the object keys are fixed by the
            contract). Present for the Lambda calling convention.
        context: the Lambda context (unused).
        s3_client: optional S3 client, injected by tests. Defaults to a lazily
            constructed boto3 client via :mod:`handlers.s3_io`.

    Returns:
        A small status dict with the object keys and the validated record count.
    """
    cfg = load_config()
    configure_logging(cfg.log_level)
    bucket = keys.resolve_bucket()

    raw_reviews = s3_io.read_json(bucket, keys.RAW_REVIEWS_KEY, client=s3_client)
    valid_reviews = ingestion.load_reviews(_extract_records(raw_reviews))
    s3_io.write_json(
        bucket, keys.STAGED_INGESTED_KEY, valid_reviews, client=s3_client
    )

    logger.info(
        "ingestion handler complete",
        extra={
            "bucket": bucket,
            "source_key": keys.RAW_REVIEWS_KEY,
            "target_key": keys.STAGED_INGESTED_KEY,
            "valid_count": len(valid_reviews),
        },
    )
    return {
        "source_key": keys.RAW_REVIEWS_KEY,
        "target_key": keys.STAGED_INGESTED_KEY,
        "count": len(valid_reviews),
    }
