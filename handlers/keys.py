"""Named constants for the S3 object layout and handler environment variables.

Centralising these here keeps the S3 keys out of the individual handlers as
magic strings and makes the object layout in ``docs/infra-contracts.md`` the
single source of truth. The layout (single bucket, prefixes) is:

===============================  ==============  ============================
Object key                       Written by      Read by
===============================  ==============  ============================
``raw/reviews.json``             (upload/seed)   ingestion
``staged/ingested.json``         ingestion       translation
``staged/translated.json``       translation     summarization, quality
``staged/summaries.json``        summarization   api
``serving/scored.json``          quality         api
===============================  ==============  ============================
"""
from __future__ import annotations

import os

# --- S3 object keys (see docs/infra-contracts.md "S3 object layout") ---------
RAW_REVIEWS_KEY = "raw/reviews.json"
STAGED_INGESTED_KEY = "staged/ingested.json"
STAGED_TRANSLATED_KEY = "staged/translated.json"
STAGED_SUMMARIES_KEY = "staged/summaries.json"
SERVING_SCORED_KEY = "serving/scored.json"

# --- Environment variables (set by CDK, read by the handlers) ----------------
# The data bucket name is handler-specific (there is no pipeline-config field
# for it); the remaining overrides are consumed by review_pipeline.config.
ENV_BUCKET = "REVIEW_PIPELINE_BUCKET"


def resolve_bucket() -> str:
    """Return the data bucket name from the ``REVIEW_PIPELINE_BUCKET`` env var.

    Returns:
        The configured bucket name.

    Raises:
        RuntimeError: if the environment variable is unset or empty. Failing
            fast here gives a clear message rather than an opaque boto3 error.
    """
    bucket = os.environ.get(ENV_BUCKET)
    if not bucket:
        raise RuntimeError(
            f"{ENV_BUCKET} environment variable is not set; "
            "the data bucket name is required."
        )
    return bucket
