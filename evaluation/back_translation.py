# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Back-translation similarity signal for the evaluation harness.

A translation preserves meaning if translating it *back* to the source language
yields text close to the original. This module round-trips each target-language
translation through Amazon Translate (source -> target was done by the pipeline;
here we do target -> source) and scores the round-trip against the original with
a lightweight, dependency-free lexical similarity metric.

Why lexical (not embeddings): the prototype avoids extra heavyweight ML deps in
the deliverable. Token-set similarity (a Jaccard/overlap blend) is a transparent,
deterministic, explainable proxy that is more than adequate to flag *broken*
round-trips (the failure mode that matters for filtering). The LLM-as-judge
signal in the harness provides the semantic-quality view; the two are reported
side by side. The similarity function is pure and unit-tested; the AWS call is
behind the same injected ``Translator`` seam the pipeline uses.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from review_pipeline.logging_config import get_logger
from review_pipeline.translation import AmazonTranslateClient, Translator

logger = get_logger(__name__)

# Split on any run of non-word characters; keep it Unicode-aware so accented
# French/German tokens (é, ü, ß, ...) are preserved as single tokens.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> List[str]:
    """Lowercase, Unicode-aware word tokenization. Pure."""
    return _TOKEN_RE.findall((text or "").lower())


def similarity(original: str, round_trip: str) -> float:
    """Lexical similarity in ``[0.0, 1.0]`` between two strings. Pure.

    A blend of token-set Jaccard (order-independent vocabulary overlap) and a
    length-ratio penalty, so a round-trip that keeps the same words *and* a
    similar length scores highest. Two empty strings are defined as identical
    (1.0); one empty and one non-empty is 0.0.

    Args:
        original: the source-language original text.
        round_trip: the text produced by translating the translation back.

    Returns:
        A similarity score in ``[0.0, 1.0]`` (higher = more similar).
    """
    a, b = _tokenize(original), _tokenize(round_trip)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    set_a, set_b = set(a), set(b)
    jaccard = len(set_a & set_b) / len(set_a | set_b)
    # Length ratio in [0,1]: penalizes round-trips that balloon or collapse.
    length_ratio = min(len(a), len(b)) / max(len(a), len(b))
    # Weight vocabulary overlap more than length; both must be decent to score high.
    return round(0.75 * jaccard + 0.25 * length_ratio, 4)


def back_translate_review(
    review: Dict[str, Any],
    target_language: str,
    translator: Translator,
) -> Dict[str, Any]:
    """Round-trip one review's target-language translation back to source.

    Reads the translation the pipeline produced under
    ``review["translations"][target_language]``, translates its title+body back
    to ``review["source_language"]``, and scores similarity against the original
    title+body. A failed back-translation call is caught and scored 0.0 (so it
    surfaces as a bad round-trip rather than crashing the run).

    Returns a result dict:
    ``{"review_id", "product_id", "target_language", "source_language",
       "similarity", "back_translated_title", "back_translated_body", "error"}``.
    """
    review_id = review.get("review_id", "<unknown>")
    source_language = review.get("source_language", "")
    translation = (review.get("translations") or {}).get(target_language) or {}

    original_text = f"{review.get('title', '')}\n\n{review.get('body', '')}".strip()

    bt_title: Optional[str] = None
    bt_body: Optional[str] = None
    error: Optional[str] = None
    try:
        bt_title = translator.translate_text(
            text=translation.get("title", ""),
            source_language=target_language,
            target_language=source_language,
        )
        bt_body = translator.translate_text(
            text=translation.get("body", ""),
            source_language=target_language,
            target_language=source_language,
        )
        round_trip_text = f"{bt_title}\n\n{bt_body}".strip()
        sim = similarity(original_text, round_trip_text)
    except Exception as exc:  # noqa: BLE001 - boundary: never crash the eval run
        # A failed round-trip is evidence of a problem, not a reason to abort;
        # score it 0.0 and record the error for the report.
        error = type(exc).__name__
        sim = 0.0
        logger.exception(
            "back-translation failed; scoring similarity 0.0",
            extra={"review_id": review_id, "target_language": target_language},
        )

    return {
        "review_id": review_id,
        "product_id": review.get("product_id"),
        "target_language": target_language,
        "source_language": source_language,
        "similarity": sim,
        "back_translated_title": bt_title,
        "back_translated_body": bt_body,
        "error": error,
    }


def back_translate_all(
    translated_reviews: List[Dict[str, Any]],
    cfg: Any,
    translator: Optional[Translator] = None,
) -> List[Dict[str, Any]]:
    """Back-translate every target-language translation across all reviews.

    Only reviews that actually have translations are round-tripped (passthrough
    English reviews have none). The AWS client is injected via ``translator``;
    when ``None`` an :class:`AmazonTranslateClient` is built lazily for
    ``cfg.aws_region`` (so offline tests pass a fake and never hit the network).

    Returns one result dict (see :func:`back_translate_review`) per
    (review, target_language) pair.
    """
    active = translator
    results: List[Dict[str, Any]] = []

    for review in translated_reviews:
        translations = review.get("translations") or {}
        for target_language in translations:
            if active is None:
                active = AmazonTranslateClient(region_name=cfg.aws_region)
            results.append(back_translate_review(review, target_language, active))

    logger.info(
        "back-translation complete",
        extra={"round_trips": len(results)},
    )
    return results
