"""AWS Lambda handlers for the AnyCompany Apparel review pipeline (Phase 6).

Each module here is a thin adapter that wraps one Phase 5 ``review_pipeline``
stage: it reads its input object(s) from S3, calls the (already tested) stage
function, and writes the output object back to S3. The handlers deliberately do
NOT reimplement any pipeline logic -- they only wire the modules to S3 and to
the Lambda ``handler(event, context)`` convention.

See ``docs/infra-contracts.md`` for the S3 object layout, environment-variable
names, and the per-handler read/write contract.
"""

__all__ = [
    "s3_io",
    "keys",
    "ingestion_handler",
    "translation_handler",
    "summarization_handler",
    "quality_handler",
    "api_handler",
]
