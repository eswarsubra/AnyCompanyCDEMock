"""Lambda handler for the quality stage.

Reads the translated reviews from S3, scores each translation with
:func:`review_pipeline.quality.score_translations` (default Bedrock judge),
drops the filtered translations with :func:`review_pipeline.quality.filter_kept`,
and writes the kept, scored reviews to the serving prefix in S3.

Reads ``staged/translated.json`` -> writes ``serving/scored.json`` (see
``docs/infra-contracts.md``).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from review_pipeline import quality
from review_pipeline.config import load_config
from review_pipeline.logging_config import configure_logging, get_logger

from handlers import keys, s3_io

logger = get_logger(__name__)


def handler(
    event: Optional[Dict[str, Any]] = None,
    context: Optional[Any] = None,
    *,
    s3_client: Optional[Any] = None,
    judge: Optional[Any] = None,
) -> Dict[str, Any]:
    """Score translations and stage only the kept ones for serving.

    Args:
        event: the Lambda event (unused; object keys are fixed by the contract).
        context: the Lambda context (unused).
        s3_client: optional S3 client, injected by tests. Defaults to a lazily
            constructed boto3 client via :mod:`handlers.s3_io`.
        judge: optional :class:`review_pipeline.quality.QualityJudge`, injected
            by tests. Defaults to the real Bedrock judge constructed lazily
            inside the wrapped module.

    Returns:
        A status dict with the object keys and the served record count.
    """
    cfg = load_config()
    configure_logging(cfg.log_level)
    bucket = keys.resolve_bucket()

    reviews = s3_io.read_json(bucket, keys.STAGED_TRANSLATED_KEY, client=s3_client)
    scored = quality.score_translations(reviews, cfg, client=judge)
    kept = quality.filter_kept(scored)
    s3_io.write_json(bucket, keys.SERVING_SCORED_KEY, kept, client=s3_client)

    logger.info(
        "quality handler complete",
        extra={
            "bucket": bucket,
            "source_key": keys.STAGED_TRANSLATED_KEY,
            "target_key": keys.SERVING_SCORED_KEY,
            "count": len(kept),
        },
    )
    return {
        "source_key": keys.STAGED_TRANSLATED_KEY,
        "target_key": keys.SERVING_SCORED_KEY,
        "count": len(kept),
    }
