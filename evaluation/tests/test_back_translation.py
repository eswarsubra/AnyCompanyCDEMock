# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the back-translation similarity signal.

The similarity metric is pure; back_translate_review/all use an injected fake
Translator so nothing hits the network.
"""
from __future__ import annotations

from typing import Dict, List

import pytest

from evaluation.back_translation import (
    back_translate_all,
    back_translate_review,
    similarity,
)
from review_pipeline.config import (
    ModelConfig,
    PipelineConfig,
    QualityConfig,
)


# --------------------------------------------------------------------------- #
# similarity() — pure
# --------------------------------------------------------------------------- #
def test_similarity_identical_text_is_one():
    assert similarity("the fabric is soft", "the fabric is soft") == 1.0


def test_similarity_both_empty_is_one():
    assert similarity("", "") == 1.0


def test_similarity_one_empty_is_zero():
    assert similarity("something", "") == 0.0
    assert similarity("", "something") == 0.0


def test_similarity_disjoint_text_is_low():
    # No shared vocabulary -> jaccard 0; length ratio 1 -> 0.25 weight only.
    assert similarity("alpha beta gamma", "delta epsilon zeta") == pytest.approx(0.25)


def test_similarity_is_case_and_accent_aware():
    # Case-insensitive; accented tokens preserved (not stripped to ascii).
    assert similarity("Été Chaud", "été chaud") == 1.0


def test_similarity_partial_overlap_between_zero_and_one():
    score = similarity("soft warm fabric", "soft cold fabric")
    assert 0.0 < score < 1.0


# --------------------------------------------------------------------------- #
# Fakes / helpers
# --------------------------------------------------------------------------- #
class _ReverseTranslator:
    """Fake Translator that returns text unchanged (perfect round-trip)."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, str]] = []

    def translate_text(self, text: str, source_language: str, target_language: str) -> str:
        self.calls.append(
            {"text": text, "src": source_language, "tgt": target_language}
        )
        return text


class _BoomTranslator:
    """Fake Translator that always raises, to exercise the error boundary."""

    def translate_text(self, text: str, source_language: str, target_language: str) -> str:
        raise RuntimeError("translate unavailable")


def _cfg() -> PipelineConfig:
    cfg = PipelineConfig(
        target_languages=["fr", "de"],
        summarization_model=ModelConfig(model_id="sonnet", max_tokens=512),
        quality_model=ModelConfig(model_id="haiku", max_tokens=256, temperature=0.0),
        quality=QualityConfig(threshold=3.0, scale_min=1, scale_max=5),
    )
    cfg.validate()
    return cfg


def _translated_review() -> Dict:
    return {
        "review_id": "rev-1",
        "product_id": "prod-1",
        "source_language": "fr",
        "title": "Très confortable",
        "body": "La robe est douce et bien coupée.",
        "translations": {
            "fr": {"title": "Très confortable", "body": "La robe est douce et bien coupée."},
        },
    }


# --------------------------------------------------------------------------- #
# back_translate_review
# --------------------------------------------------------------------------- #
def test_back_translate_review_perfect_round_trip_scores_high():
    review = _translated_review()
    # Source is fr, translation stored under 'fr' -> reverse translator returns
    # the same text, so round-trip == original -> similarity 1.0.
    result = back_translate_review(review, "fr", _ReverseTranslator())

    assert result["review_id"] == "rev-1"
    assert result["target_language"] == "fr"
    assert result["source_language"] == "fr"
    assert result["similarity"] == 1.0
    assert result["error"] is None


def test_back_translate_review_translates_back_to_source_language():
    review = _translated_review()
    tr = _ReverseTranslator()
    back_translate_review(review, "fr", tr)
    # Both calls translate FROM the target language TO the source language.
    assert all(c["src"] == "fr" and c["tgt"] == "fr" for c in tr.calls)
    assert len(tr.calls) == 2  # title + body


def test_back_translate_review_failure_scores_zero_and_records_error():
    review = _translated_review()
    result = back_translate_review(review, "fr", _BoomTranslator())
    assert result["similarity"] == 0.0
    assert result["error"] == "RuntimeError"


# --------------------------------------------------------------------------- #
# back_translate_all
# --------------------------------------------------------------------------- #
def test_back_translate_all_one_result_per_translation():
    reviews = [
        {
            "review_id": "rev-1",
            "product_id": "prod-1",
            "source_language": "de",
            "title": "Gut",
            "body": "Schön.",
            "translations": {
                "fr": {"title": "Bien", "body": "Joli."},
                "de": {"title": "Gut", "body": "Schön."},
            },
        },
        {  # passthrough English review: no translations -> no round-trips
            "review_id": "rev-2",
            "product_id": "prod-2",
            "source_language": "en",
            "title": "Great",
            "body": "Nice.",
            "translations": {},
        },
    ]
    results = back_translate_all(reviews, _cfg(), translator=_ReverseTranslator())
    assert len(results) == 2  # rev-1 fr + rev-1 de only
    assert {r["target_language"] for r in results} == {"fr", "de"}
    assert all(r["review_id"] == "rev-1" for r in results)
