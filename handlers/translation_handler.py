# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lambda handler for the translation stage.

Reads the staged, validated reviews from S3, runs
:func:`review_pipeline.translation.translate_reviews` (default Amazon Translate
client), and writes the translated reviews back to S3.

Reads ``staged/ingested.json`` -> writes ``staged/translated.json`` (see
``docs/infra-contracts.md``).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from review_pipeline import translation
from review_pipeline.config import load_config
from review_pipeline.logging_config import configure_logging, get_logger

from handlers import keys, s3_io

logger = get_logger(__name__)


def handler(
    event: Optional[Dict[str, Any]] = None,
    context: Optional[Any] = None,
    *,
    s3_client: Optional[Any] = None,
    translator: Optional[Any] = None,
) -> Dict[str, Any]:
    """Translate the staged reviews into the configured target languages.

    Args:
        event: the Lambda event (unused; object keys are fixed by the contract).
        context: the Lambda context (unused).
        s3_client: optional S3 client, injected by tests. Defaults to a lazily
            constructed boto3 client via :mod:`handlers.s3_io`.
        translator: optional :class:`review_pipeline.translation.Translator`,
            injected by tests. Defaults to the real Amazon Translate client
            constructed lazily inside the wrapped module.

    Returns:
        A status dict with the object keys and the translated record count.
    """
    cfg = load_config()
    configure_logging(cfg.log_level)
    bucket = keys.resolve_bucket()

    reviews = s3_io.read_json(bucket, keys.STAGED_INGESTED_KEY, client=s3_client)
    translated = translation.translate_reviews(reviews, cfg, translator=translator)
    s3_io.write_json(
        bucket, keys.STAGED_TRANSLATED_KEY, translated, client=s3_client
    )

    logger.info(
        "translation handler complete",
        extra={
            "bucket": bucket,
            "source_key": keys.STAGED_INGESTED_KEY,
            "target_key": keys.STAGED_TRANSLATED_KEY,
            "count": len(translated),
        },
    )
    return {
        "source_key": keys.STAGED_INGESTED_KEY,
        "target_key": keys.STAGED_TRANSLATED_KEY,
        "count": len(translated),
    }
