# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the evaluation orchestrator (harness.evaluate).

Uses pre-translated records + injected fake judge/translator so the harness runs
fully offline and the aggregation math is asserted deterministically.
"""
from __future__ import annotations

from typing import Any, Dict, List

from evaluation.harness import evaluate
from review_pipeline.config import ModelConfig, PipelineConfig, QualityConfig


def _cfg(threshold: float = 3.0) -> PipelineConfig:
    cfg = PipelineConfig(
        target_languages=["fr", "de"],
        summarization_model=ModelConfig(model_id="sonnet", max_tokens=512),
        quality_model=ModelConfig(model_id="haiku", max_tokens=256, temperature=0.0),
        quality=QualityConfig(threshold=threshold, scale_min=1, scale_max=5),
    )
    cfg.validate()
    return cfg


def _translated() -> List[Dict[str, Any]]:
    """Two translated reviews + one passthrough (no translations)."""
    return [
        {
            "review_id": "rev-1", "product_id": "prod-1", "source_language": "fr",
            "title": "Bon", "body": "Confortable.",
            "translations": {
                "fr": {"title": "Bon", "body": "Confortable."},
                "de": {"title": "Gut", "body": "Bequem."},
            },
        },
        {
            "review_id": "rev-2", "product_id": "prod-2", "source_language": "de",
            "title": "Schlecht", "body": "Dünn.",
            "translations": {
                "fr": {"title": "Mauvais", "body": "Mince."},
                "de": {"title": "Schlecht", "body": "Dünn."},
            },
        },
        {
            "review_id": "rev-3", "product_id": "prod-3", "source_language": "en",
            "title": "Fine", "body": "Okay.", "translations": {},
        },
    ]


class _ScriptedJudge:
    """Judge returning a per-(review,lang) score from a lookup, else a default."""

    def __init__(self, scores: Dict, default: float = 5.0) -> None:
        self._scores = scores
        self._default = default

    def score(self, *, source_text, translated_text, source_lang, target_lang,
              model_id, max_tokens, temperature) -> float:
        # Key on the translated text so we can target specific rows.
        return self._scores.get(translated_text, self._default)


class _EchoTranslator:
    def translate_text(self, text, source_language, target_language) -> str:
        return text  # perfect round-trip -> similarity 1.0


def test_evaluate_aggregates_counts_and_kept_filtered():
    # Make rev-2's French translation fail the threshold; everything else passes.
    judge = _ScriptedJudge(scores={"Mauvais\n\nMince.": 1.0}, default=5.0)
    result = evaluate(
        source=None, cfg=_cfg(threshold=3.0),
        translator=_EchoTranslator(), judge=judge,
        translated_reviews=_translated(),
    )

    assert result.total_reviews == 3
    assert result.translated_reviews == 2  # rev-3 is passthrough
    assert result.total_translations == 4  # 2 reviews x 2 langs

    fr = result.per_language["fr"]
    de = result.per_language["de"]
    assert fr.translation_count == 2
    assert fr.kept_count == 1 and fr.filtered_count == 1  # rev-2 fr filtered
    assert de.kept_count == 2 and de.filtered_count == 0

    assert result.overall_kept == 3
    assert result.overall_filtered == 1
    assert result.overall_kept_pct == 75.0


def test_evaluate_similarity_joined_per_language():
    # For a clean assertion, use records whose stored translation text equals the
    # original text: an echo translator then reproduces the original on the
    # round-trip, so similarity is exactly 1.0 for every translation.
    mirror = [
        {
            "review_id": "rev-1", "product_id": "prod-1", "source_language": "fr",
            "title": "Bon", "body": "Confortable.",
            "translations": {
                "fr": {"title": "Bon", "body": "Confortable."},
                "de": {"title": "Bon", "body": "Confortable."},
            },
        },
    ]
    result = evaluate(
        source=None, cfg=_cfg(),
        translator=_EchoTranslator(), judge=_ScriptedJudge(scores={}),
        translated_reviews=mirror,
    )
    # Round-trip reproduces the original text -> mean similarity 1.0 per language.
    assert result.per_language["fr"].mean_similarity == 1.0
    assert result.per_language["de"].mean_similarity == 1.0


def test_evaluate_similarity_below_one_when_round_trip_differs():
    # With genuinely different translation text, the echo round-trip does NOT
    # match the original, so similarity is a real value in [0, 1) — confirming
    # the metric is computed from actual round-trip content, not hard-coded.
    result = evaluate(
        source=None, cfg=_cfg(),
        translator=_EchoTranslator(), judge=_ScriptedJudge(scores={}),
        translated_reviews=_translated(),
    )
    fr = result.per_language["fr"]
    assert fr.mean_similarity is not None
    assert 0.0 <= fr.mean_similarity < 1.0


def test_evaluate_language_metrics_percentages():
    result = evaluate(
        source=None, cfg=_cfg(),
        translator=_EchoTranslator(), judge=_ScriptedJudge(scores={}, default=5.0),
        translated_reviews=_translated(),
    )
    fr = result.per_language["fr"]
    assert fr.kept_pct == 100.0
    assert fr.filtered_pct == 0.0
    assert fr.mean_judge_score == 5.0


def test_evaluate_empty_input_is_safe():
    result = evaluate(
        source=None, cfg=_cfg(),
        translator=_EchoTranslator(), judge=_ScriptedJudge(scores={}),
        translated_reviews=[],
    )
    assert result.total_translations == 0
    assert result.overall_kept_pct == 0.0
    assert result.per_language["fr"].translation_count == 0
