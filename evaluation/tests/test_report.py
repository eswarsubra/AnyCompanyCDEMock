# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Markdown report renderer (pure formatting)."""
from __future__ import annotations

from evaluation.harness import EvaluationResult, LanguageMetrics
from evaluation.report import render_markdown


def _result() -> EvaluationResult:
    return EvaluationResult(
        total_reviews=100,
        translated_reviews=60,
        total_translations=120,
        quality_threshold=3.0,
        scale_min=1.0,
        scale_max=5.0,
        target_languages=["fr", "de"],
        per_language={
            "fr": LanguageMetrics("fr", 60, 57, 3, 4.6, 0.88),
            "de": LanguageMetrics("de", 60, 60, 0, 4.8, 0.9),
        },
    )


def test_render_includes_headline_metrics():
    md = render_markdown(_result(), generated_at="2026-08-01T00:00:00Z")
    assert "# Translation quality report" in md
    assert "2026-08-01T00:00:00Z" in md
    assert "**Reviews evaluated:** 100" in md
    assert "**Translations scored:** 120" in md


def test_render_per_language_rows_present():
    md = render_markdown(_result(), generated_at="t")
    # Each configured language appears as a table row with its counts.
    assert "| fr | 60 | 4.6 | 0.88 | 57 | 3 |" in md
    assert "| de | 60 | 4.8 | 0.9 | 60 | 0 |" in md


def test_render_overall_kept_percentage():
    md = render_markdown(_result(), generated_at="t")
    # 117 / 120 = 97.5%
    assert "117 / 120" in md
    assert "97.5%" in md


def test_render_handles_missing_metrics_gracefully():
    result = EvaluationResult(
        total_reviews=0, translated_reviews=0, total_translations=0,
        quality_threshold=3.0, scale_min=1.0, scale_max=5.0,
        target_languages=["fr"],
        per_language={"fr": LanguageMetrics("fr", 0, 0, 0, None, None)},
    )
    md = render_markdown(result, generated_at="t")
    # None metrics render as an en dash, not "None" or a crash.
    assert "–" in md
    assert "None" not in md
