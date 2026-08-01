# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Framework-light read API over the pipeline's output (stage 5 of 5).

This module is the framework-agnostic core of the read path. It exposes two
request handlers -- :func:`get_product_reviews` and :func:`get_product_summary`
-- that turn the pipeline's persisted output into stable, JSON-serializable
response dicts. It deliberately knows nothing about HTTP, API Gateway, Lambda,
or boto3: the Phase 6 handler wires those in and simply calls these functions.

Dependency-injection seam
-------------------------
Both handlers take a ``store`` implementing the :class:`ReviewStore` protocol.
This is the read-model seam. Tests and local runs pass an :class:`InMemoryStore`
built from plain lists; Phase 6 will add an S3-backed store (reading the
``ScoredReview`` / ``ProductSummary`` artifacts the earlier stages write to S3)
that satisfies the same protocol and drops in without changing this module.

What gets served
----------------
Only quality-``kept`` translations are exposed (fidelity/fluency below the
configured threshold are filtered upstream in the ``quality`` stage and are
additionally re-checked here so this handler never serves a filtered result):

* An English passthrough review (``source_language == "en"``, empty
  ``translations``) is emitted once with its original title/body and
  ``language == "en"``.
* A non-English review is emitted once per *kept* translation, each entry
  carrying the target ``language`` and that translation's title/body. A review
  whose translations are all filtered contributes no entries.

Unknown products get a consistent not-found shape:
``{"error": "not_found", "product_id": <id>}`` -- returned by both handlers.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

try:  # TypedDict lives in typing on 3.8+, kept import-safe for older stubs.
    from typing import TypedDict
except ImportError:  # pragma: no cover - defensive for very old runtimes
    from typing_extensions import TypedDict  # type: ignore

from review_pipeline.logging_config import get_logger

logger = get_logger(__name__)

# --- Response contract constants (no magic strings) --------------------------
ERROR_KEY = "error"
ERROR_NOT_FOUND = "not_found"
PASSTHROUGH_LANGUAGE = "en"
KEPT_FLAG = "kept"


# --- Data-model typed shapes -------------------------------------------------
# These mirror the dicts produced by the upstream stages (see
# docs/pipeline-contracts.md). They are TypedDicts so callers get editor/type
# hints while the values stay plain, JSON-serializable dicts on the wire.


class TranslationText(TypedDict, total=False):
    """One translated rendering of a review's text, keyed by target language."""

    title: str
    body: str
    engine: str


class QualityVerdict(TypedDict, total=False):
    """Quality verdict for one translation, keyed by target language."""

    score: float
    kept: bool


class ScoredReview(TypedDict, total=False):
    """A review enriched with translations and per-language quality verdicts.

    Produced by the ``quality`` stage (``TranslatedReview + quality``). English
    passthrough reviews carry empty ``translations``/``quality`` maps.
    """

    review_id: str
    product_id: str
    product_name: str
    source_language: str
    rating: int
    title: str
    body: str
    translations: Dict[str, TranslationText]
    quality: Dict[str, QualityVerdict]


class ProductSummary(TypedDict, total=False):
    """Product-level summary produced by the ``summarization`` stage."""

    product_id: str
    product_name: str
    review_count: int
    summary: str


class ReviewEntry(TypedDict):
    """One served review entry in a reviews response."""

    review_id: str
    language: str
    title: str
    body: str
    rating: int


# --- Read-model interface (the injection seam) -------------------------------


@runtime_checkable
class ReviewStore(Protocol):
    """Read model the API handlers depend on.

    Any object providing these two methods can back the API. The in-memory
    implementation below is used by tests and local runs; a future S3-backed
    store (Phase 6) will implement the same protocol so it can be dropped in
    without touching the handlers.
    """

    def get_reviews(self, product_id: str) -> List[ScoredReview]:
        """Return all scored reviews for ``product_id`` (empty list if none)."""
        ...

    def get_summary(self, product_id: str) -> Optional[ProductSummary]:
        """Return the product summary for ``product_id``, or ``None`` if absent."""
        ...


class InMemoryStore:
    """In-memory :class:`ReviewStore` built from plain lists.

    Indexes the supplied scored reviews and product summaries by ``product_id``
    once at construction. Intended for unit tests and local runs; the same
    interface is implemented by the S3-backed store deferred to Phase 6.
    """

    def __init__(
        self,
        scored_reviews: Optional[List[ScoredReview]] = None,
        product_summaries: Optional[List[ProductSummary]] = None,
    ) -> None:
        self._reviews_by_product: Dict[str, List[ScoredReview]] = {}
        for review in scored_reviews or []:
            product_id = review["product_id"]
            self._reviews_by_product.setdefault(product_id, []).append(review)

        self._summary_by_product: Dict[str, ProductSummary] = {
            summary["product_id"]: summary for summary in product_summaries or []
        }

    def get_reviews(self, product_id: str) -> List[ScoredReview]:
        return list(self._reviews_by_product.get(product_id, []))

    def get_summary(self, product_id: str) -> Optional[ProductSummary]:
        return self._summary_by_product.get(product_id)


# --- Handlers ----------------------------------------------------------------


def _not_found(product_id: str) -> Dict[str, str]:
    """Build the consistent not-found response used by both handlers."""
    return {ERROR_KEY: ERROR_NOT_FOUND, "product_id": product_id}


def _served_entries(review: ScoredReview) -> List[ReviewEntry]:
    """Expand one scored review into the entries the API serves.

    An English passthrough review yields one original-text entry. A non-English
    review yields one entry per quality-kept translation (in the target
    language). Filtered translations are skipped so a filtered result is never
    served even if an upstream ``filter_kept`` pass was not applied.
    """
    source_language = review.get("source_language", "")
    translations = review.get("translations") or {}

    # English passthrough: no translations, serve the original text as-is.
    if source_language == PASSTHROUGH_LANGUAGE and not translations:
        return [
            ReviewEntry(
                review_id=review["review_id"],
                language=PASSTHROUGH_LANGUAGE,
                title=review.get("title", ""),
                body=review.get("body", ""),
                rating=review["rating"],
            )
        ]

    quality = review.get("quality") or {}
    entries: List[ReviewEntry] = []
    for language, translated in translations.items():
        verdict = quality.get(language) or {}
        if not verdict.get(KEPT_FLAG, False):
            continue  # filtered by the quality stage -> never served
        entries.append(
            ReviewEntry(
                review_id=review["review_id"],
                language=language,
                title=translated.get("title", ""),
                body=translated.get("body", ""),
                rating=review["rating"],
            )
        )
    return entries


def get_product_reviews(product_id: str, store: ReviewStore) -> Dict[str, Any]:
    """Return the served reviews for a product.

    Args:
        product_id: the product to fetch (e.g. ``"prod-001"``).
        store: read model satisfying :class:`ReviewStore`.

    Returns:
        On success, a dict of shape::

            {"product_id": str, "summary": str,
             "reviews": [{"review_id", "language", "title", "body", "rating"}]}

        Only quality-kept translations (and English passthrough originals) are
        included. If the product is unknown -- no reviews *and* no summary --
        the not-found shape ``{"error": "not_found", "product_id": ...}`` is
        returned instead.
    """
    reviews = store.get_reviews(product_id)
    summary = store.get_summary(product_id)

    if not reviews and summary is None:
        logger.info(
            "product reviews requested for unknown product",
            extra={"product_id": product_id, "result": ERROR_NOT_FOUND},
        )
        return _not_found(product_id)

    entries: List[ReviewEntry] = []
    for review in reviews:
        entries.extend(_served_entries(review))

    logger.info(
        "served product reviews",
        extra={
            "product_id": product_id,
            "review_count": len(reviews),
            "served_entry_count": len(entries),
        },
    )
    return {
        "product_id": product_id,
        "summary": summary["summary"] if summary else "",
        "reviews": entries,
    }


def get_product_summary(product_id: str, store: ReviewStore) -> Dict[str, Any]:
    """Return the product-level summary for a product.

    Args:
        product_id: the product to fetch (e.g. ``"prod-001"``).
        store: read model satisfying :class:`ReviewStore`.

    Returns:
        The :class:`ProductSummary` dict for the product, or the not-found
        shape ``{"error": "not_found", "product_id": ...}`` when the product is
        unknown to the store.
    """
    summary = store.get_summary(product_id)
    if summary is None:
        logger.info(
            "product summary requested for unknown product",
            extra={"product_id": product_id, "result": ERROR_NOT_FOUND},
        )
        return _not_found(product_id)

    logger.info(
        "served product summary",
        extra={
            "product_id": product_id,
            "review_count": summary.get("review_count", 0),
        },
    )
    return dict(summary)
