"""AnyCompany Apparel review pipeline package.

Shared library code for the review translation/summarization/quality pipeline.
The configuration and logging primitives here are imported by every pipeline
stage (ingestion, translation, summarization, quality, api).
"""

__all__ = ["config", "logging_config"]
