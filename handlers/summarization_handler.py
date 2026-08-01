"""Lambda handler for the summarization stage.

Reads the translated reviews from S3, runs
:func:`review_pipeline.summarization.summarize_products` (default Bedrock
client), and writes the per-product summaries back to S3.

Reads ``staged/translated.json`` -> writes ``staged/summaries.json`` (see
``docs/infra-contracts.md``).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from review_pipeline import summarization
from review_pipeline.config import load_config
from review_pipeline.logging_config import configure_logging, get_logger

from handlers import keys, s3_io

logger = get_logger(__name__)


def handler(
    event: Optional[Dict[str, Any]] = None,
    context: Optional[Any] = None,
    *,
    s3_client: Optional[Any] = None,
    summarizer: Optional[Any] = None,
) -> Dict[str, Any]:
    """Summarize the translated reviews per product.

    Args:
        event: the Lambda event (unused; object keys are fixed by the contract).
        context: the Lambda context (unused).
        s3_client: optional S3 client, injected by tests. Defaults to a lazily
            constructed boto3 client via :mod:`handlers.s3_io`.
        summarizer: optional
            :class:`review_pipeline.summarization.SummarizerClient`, injected by
            tests. Defaults to the real Bedrock client constructed lazily inside
            the wrapped module.

    Returns:
        A status dict with the object keys and the product-summary count.
    """
    cfg = load_config()
    configure_logging(cfg.log_level)
    bucket = keys.resolve_bucket()

    reviews = s3_io.read_json(bucket, keys.STAGED_TRANSLATED_KEY, client=s3_client)
    summaries = summarization.summarize_products(reviews, cfg, client=summarizer)
    s3_io.write_json(
        bucket, keys.STAGED_SUMMARIES_KEY, summaries, client=s3_client
    )

    logger.info(
        "summarization handler complete",
        extra={
            "bucket": bucket,
            "source_key": keys.STAGED_TRANSLATED_KEY,
            "target_key": keys.STAGED_SUMMARIES_KEY,
            "product_count": len(summaries),
        },
    )
    return {
        "source_key": keys.STAGED_TRANSLATED_KEY,
        "target_key": keys.STAGED_SUMMARIES_KEY,
        "count": len(summaries),
    }
