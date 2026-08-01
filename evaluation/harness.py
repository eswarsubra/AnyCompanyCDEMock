# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Evaluation orchestrator: run both quality signals and aggregate the results.

``evaluate`` ties the pieces together:

1. Load reviews (``review_pipeline.ingestion.load_reviews``).
2. Translate them (``review_pipeline.translation.translate_reviews``) — unless
   already-translated records are supplied.
3. Score each translation two ways:
   * forward LLM-as-judge fidelity/fluency
     (``review_pipeline.quality.score_translations`` — the pipeline's own stage),
   * back-translation similarity (``evaluation.back_translation``).
4. Aggregate into per-language and overall metrics an :class:`EvaluationResult`
   the report renderer turns into Markdown.

All AWS access is via injected clients (``translator`` / ``judge``), so the whole
harness runs offline in tests with fakes, or against live AWS in the CLI.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from review_pipeline.config import PipelineConfig
from review_pipeline.ingestion import load_reviews
from review_pipeline.logging_config import get_logger
from review_pipeline.quality import QualityJudge, score_translations
from review_pipeline.translation import Translator, translate_reviews

from evaluation.back_translation import back_translate_all

logger = get_logger(__name__)


@dataclass(frozen=True)
class LanguageMetrics:
    """Aggregated quality metrics for one target language."""

    language: str
    translation_count: int
    kept_count: int
    filtered_count: int
    mean_judge_score: Optional[float]
    mean_similarity: Optional[float]

    @property
    def kept_pct(self) -> float:
        if self.translation_count == 0:
            return 0.0
        return round(100.0 * self.kept_count / self.translation_count, 1)

    @property
    def filtered_pct(self) -> float:
        if self.translation_count == 0:
            return 0.0
        return round(100.0 * self.filtered_count / self.translation_count, 1)


@dataclass(frozen=True)
class EvaluationResult:
    """The full evaluation outcome, ready to render into a report."""

    total_reviews: int
    translated_reviews: int
    total_translations: int
    quality_threshold: float
    scale_min: float
    scale_max: float
    target_languages: List[str]
    per_language: Dict[str, LanguageMetrics]
    # Round-trip / per-translation detail rows, useful for spot-checking.
    details: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def overall_kept(self) -> int:
        return sum(m.kept_count for m in self.per_language.values())

    @property
    def overall_filtered(self) -> int:
        return sum(m.filtered_count for m in self.per_language.values())

    @property
    def overall_kept_pct(self) -> float:
        if self.total_translations == 0:
            return 0.0
        return round(100.0 * self.overall_kept / self.total_translations, 1)


def _mean(values: List[float]) -> Optional[float]:
    """Rounded mean, or None for an empty list."""
    return round(statistics.mean(values), 3) if values else None


def evaluate(
    source: Any,
    cfg: PipelineConfig,
    *,
    translator: Optional[Translator] = None,
    judge: Optional[QualityJudge] = None,
    translated_reviews: Optional[List[Dict[str, Any]]] = None,
) -> EvaluationResult:
    """Run the full evaluation and return aggregated metrics.

    Args:
        source: a dataset path/list accepted by ``load_reviews`` (ignored when
            ``translated_reviews`` is provided).
        cfg: the validated pipeline configuration.
        translator: injected Amazon Translate client (used for translation, if
            not pre-supplied, and always for back-translation). ``None`` -> the
            real client is built lazily by the underlying stages.
        judge: injected quality judge for the forward LLM-as-judge score.
            ``None`` -> the real Bedrock judge is built lazily.
        translated_reviews: optionally skip ingestion+translation and evaluate
            these already-translated records (e.g. the pipeline's own output).

    Returns:
        An :class:`EvaluationResult`.
    """
    if translated_reviews is None:
        reviews = load_reviews(source)
        translated = translate_reviews(reviews, cfg, translator=translator)
    else:
        reviews = translated_reviews
        translated = translated_reviews

    # Forward LLM-as-judge (the pipeline's own quality stage) over every translation.
    scored = score_translations(translated, cfg, client=judge)
    # Back-translation round-trip similarity over the same translations.
    back = back_translate_all(translated, cfg, translator=translator)

    # Index back-translation similarity by (review_id, language) to join with scores.
    sim_by_key = {
        (r["review_id"], r["target_language"]): r["similarity"] for r in back
    }

    target_languages = list(cfg.target_languages)
    # Accumulators per language.
    judge_scores: Dict[str, List[float]] = {lang: [] for lang in target_languages}
    sims: Dict[str, List[float]] = {lang: [] for lang in target_languages}
    counts: Dict[str, int] = {lang: 0 for lang in target_languages}
    kept: Dict[str, int] = {lang: 0 for lang in target_languages}
    details: List[Dict[str, Any]] = []
    total_translations = 0
    translated_count = 0

    for record in scored:
        quality = record.get("quality") or {}
        if quality:
            translated_count += 1
        review_id = record.get("review_id", "<unknown>")
        for lang, verdict in quality.items():
            # Only aggregate configured target languages (defensive).
            if lang not in counts:
                judge_scores.setdefault(lang, [])
                sims.setdefault(lang, [])
                counts.setdefault(lang, 0)
                kept.setdefault(lang, 0)
            total_translations += 1
            counts[lang] += 1
            score = float(verdict.get("score", 0.0))
            judge_scores[lang].append(score)
            if verdict.get("kept"):
                kept[lang] += 1
            sim = sim_by_key.get((review_id, lang))
            if sim is not None:
                sims[lang].append(sim)
            details.append(
                {
                    "review_id": review_id,
                    "product_id": record.get("product_id"),
                    "language": lang,
                    "judge_score": score,
                    "kept": bool(verdict.get("kept")),
                    "similarity": sim,
                }
            )

    per_language: Dict[str, LanguageMetrics] = {}
    for lang in counts:
        per_language[lang] = LanguageMetrics(
            language=lang,
            translation_count=counts[lang],
            kept_count=kept[lang],
            filtered_count=counts[lang] - kept[lang],
            mean_judge_score=_mean(judge_scores[lang]),
            mean_similarity=_mean(sims[lang]),
        )

    result = EvaluationResult(
        total_reviews=len(reviews),
        translated_reviews=translated_count,
        total_translations=total_translations,
        quality_threshold=float(cfg.quality.threshold),
        scale_min=float(cfg.quality.scale_min),
        scale_max=float(cfg.quality.scale_max),
        target_languages=target_languages,
        per_language=per_language,
        details=details,
    )
    logger.info(
        "evaluation complete",
        extra={
            "reviews": result.total_reviews,
            "translations": result.total_translations,
            "kept": result.overall_kept,
            "filtered": result.overall_filtered,
        },
    )
    return result
