"""Quality stage: score translations for fidelity and fluency (ADR-0002).

This is stage 4 of the pipeline (``ingestion -> translation -> summarization ->
quality -> api``). Each target-language translation produced by the translation
stage is scored by a cheap Bedrock judge — Claude Haiku via the Anthropic Bedrock
client (see ``docs/adr/0002-model-and-service-selection.md``) — on the integer/
float scale ``[cfg.quality.scale_min, cfg.quality.scale_max]``. A translation is
kept when its score is at least ``cfg.quality.threshold``.

Design notes (per the contract in ``docs/pipeline-contracts.md``):

* Inputs/outputs are plain, JSON-serializable dicts. Records are never mutated in
  place — every function returns new dicts.
* All AWS access is behind an injected ``client`` seam that defaults to ``None``.
  The default Bedrock judge is constructed lazily *inside* the function (never at
  import), and the ``anthropic`` import lives inside that construction so offline
  unit tests using a fake client need no dependency installed.
* Boundary errors (a failed scoring call) are handled conservatively: the
  translation is scored at ``scale_min`` so it is filtered out rather than
  crashing the batch (see :func:`score_translations`).
* Logs carry ``review_id`` / language / score / counts only — never review text.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Protocol

from review_pipeline.config import PipelineConfig
from review_pipeline.logging_config import get_logger

logger = get_logger(__name__)

# A review enriched by the translation stage. Kept loose (plain dicts) so the
# same shapes can flow through S3 between Lambdas in Phase 6.
TranslatedReview = Dict[str, Any]
# A review further enriched with per-language quality verdicts.
ScoredReview = Dict[str, Any]


class QualityJudge(Protocol):
    """Structural type for the injectable scoring client.

    Tests pass a fake exposing this same ``score`` signature; the default
    implementation is :class:`BedrockQualityJudge`.
    """

    def score(
        self,
        source_text: str,
        translated_text: str,
        source_lang: str,
        target_lang: str,
        model_id: str,
        max_tokens: int,
        temperature: float,
    ) -> float:
        """Return a fidelity+fluency score for one translation."""
        ...


def build_scoring_prompt(
    source_text: str,
    translated_text: str,
    source_lang: str,
    target_lang: str,
    scale_min: float,
    scale_max: float,
) -> str:
    """Build the judge prompt for scoring one translation. Pure and testable.

    The prompt asks the model to rate fidelity (meaning preserved) and fluency
    (reads naturally) on the configured scale and to reply with the number only,
    which :func:`parse_score` then extracts.

    Args:
        source_text: The original review text (title + body concatenated).
        translated_text: The candidate translation to judge.
        source_lang: Source language code (e.g. ``"fr"``).
        target_lang: Target language code the text was translated into.
        scale_min: Lower bound of the scoring scale.
        scale_max: Upper bound of the scoring scale.

    Returns:
        A prompt string for the judge model.
    """
    return (
        "You are a bilingual translation quality judge. Rate how well the "
        "translation preserves the meaning of the source (fidelity) and how "
        "naturally it reads in the target language (fluency).\n\n"
        f"Source language: {source_lang}\n"
        f"Target language: {target_lang}\n"
        f"Source text:\n{source_text}\n\n"
        f"Translation:\n{translated_text}\n\n"
        f"Give a single overall score from {scale_min} to {scale_max} "
        f"(where {scale_max} is a flawless translation and {scale_min} is "
        "unusable). Reply with the number only, no explanation."
    )


# Matches the first signed integer or decimal in the model's reply, so answers
# like "4", "4.5/5", or "Score: 3.0" all yield the leading numeric token.
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_score(raw: str, scale_min: float, scale_max: float) -> float:
    """Parse a judge reply into a float clamped to the scoring scale. Pure.

    Extracts the first numeric token from ``raw`` and clamps it into
    ``[scale_min, scale_max]``. Non-numeric or empty replies are treated
    conservatively as ``scale_min`` (so the translation is filtered) rather
    than raising.

    Args:
        raw: The raw text the judge returned.
        scale_min: Lower bound of the scoring scale.
        scale_max: Upper bound of the scoring scale.

    Returns:
        A float within ``[scale_min, scale_max]``.
    """
    match = _NUMBER_RE.search(raw or "")
    if match is None:
        logger.warning(
            "quality judge returned no parseable score; treating as scale_min",
            extra={"scale_min": scale_min, "scale_max": scale_max},
        )
        return float(scale_min)
    value = float(match.group())
    # Clamp into range: out-of-scale replies are pinned to the nearest bound.
    return max(float(scale_min), min(float(scale_max), value))


class BedrockQualityJudge:
    """Default judge: scores translations via the Anthropic Bedrock client.

    Per ADR-0002 this uses the Anthropic Bedrock client (``AnthropicBedrock``)
    rather than raw boto3. The ``anthropic`` import happens in ``__init__`` (not
    at module import) so offline tests injecting a fake never need the dependency
    installed, and no network client is constructed at import time.

    The scoring scale is captured at construction so :meth:`score` can keep the
    same clean, client-agnostic signature the injected fakes implement.
    """

    def __init__(self, aws_region: str, scale_min: float, scale_max: float) -> None:
        # Lazy import: keeps unit tests offline and avoids import-time clients.
        from anthropic import AnthropicBedrock

        self._client = AnthropicBedrock(aws_region=aws_region)
        self._scale_min = scale_min
        self._scale_max = scale_max

    def score(
        self,
        source_text: str,
        translated_text: str,
        source_lang: str,
        target_lang: str,
        model_id: str,
        max_tokens: int,
        temperature: float,
    ) -> float:
        """Score one translation: build the prompt, call Bedrock, parse the reply."""
        prompt = build_scoring_prompt(
            source_text=source_text,
            translated_text=translated_text,
            source_lang=source_lang,
            target_lang=target_lang,
            scale_min=self._scale_min,
            scale_max=self._scale_max,
        )
        message = self._client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return parse_score(_first_text(message), self._scale_min, self._scale_max)


def _first_text(message: Any) -> str:
    """Return the first non-empty text block from an Anthropic response."""
    for block in getattr(message, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            return text
    return ""


def score_translations(
    reviews: List[TranslatedReview],
    cfg: PipelineConfig,
    client: Optional[QualityJudge] = None,
) -> List[ScoredReview]:
    """Score every target-language translation on each review.

    For each review that has translations, each target-language translation is
    scored by ``client`` (or the default :class:`BedrockQualityJudge`, built
    lazily when ``client is None``) on the scale
    ``[cfg.quality.scale_min, cfg.quality.scale_max]``. A translation is kept
    when ``score >= cfg.quality.threshold``.

    Reviews with no translations (e.g. an English passthrough) receive an empty
    ``quality`` dict.

    Args:
        reviews: Translation-stage output records.
        cfg: The validated pipeline configuration.
        client: Optional injected judge. When ``None`` the default Bedrock judge
            is constructed lazily inside this function.

    Returns:
        A new list of :data:`ScoredReview` dicts; inputs are not mutated.
    """
    quality_cfg = cfg.quality
    model_cfg = cfg.quality_model

    # Construct the default judge lazily and once per batch, only when needed.
    judge = client if client is not None else _default_client(cfg)

    scored: List[ScoredReview] = []
    total_translations = 0
    total_kept = 0
    total_errors = 0

    for review in reviews:
        review_id = review.get("review_id", "<unknown>")
        translations: Dict[str, Any] = review.get("translations") or {}

        # Original review text (source language) that each translation is judged
        # against; title + body concatenated once per review.
        source_text = f"{review.get('title', '')}\n\n{review.get('body', '')}".strip()
        source_lang = review.get("source_language", "")

        quality: Dict[str, Dict[str, Any]] = {}
        for lang, translation in translations.items():
            total_translations += 1
            translated_text = (
                f"{translation.get('title', '')}\n\n{translation.get('body', '')}"
            ).strip()

            try:
                score = judge.score(
                    source_text=source_text,
                    translated_text=translated_text,
                    source_lang=source_lang,
                    target_lang=lang,
                    model_id=model_cfg.model_id,
                    max_tokens=model_cfg.max_tokens,
                    temperature=model_cfg.temperature,
                )
            except Exception:  # noqa: BLE001 - boundary: never crash the batch
                total_errors += 1
                # Conservative choice: a failed scoring call is scored at
                # scale_min so the translation is filtered out, rather than
                # skipped (which would silently serve an unscored translation).
                score = float(quality_cfg.scale_min)
                logger.exception(
                    "quality scoring failed; filtering translation",
                    extra={"review_id": review_id, "lang": lang},
                )

            kept = score >= quality_cfg.threshold
            if kept:
                total_kept += 1
            quality[lang] = {"score": score, "kept": kept}
            logger.info(
                "scored translation",
                extra={
                    "review_id": review_id,
                    "lang": lang,
                    "score": score,
                    "threshold": quality_cfg.threshold,
                    "kept": kept,
                },
            )

        # New dict (don't mutate input); quality is {} for passthrough reviews.
        record = dict(review)
        record["quality"] = quality
        scored.append(record)

    logger.info(
        "quality stage complete",
        extra={
            "reviews": len(reviews),
            "translations": total_translations,
            "kept": total_kept,
            "filtered": total_translations - total_kept,
            "errors": total_errors,
        },
    )
    return scored


def _default_client(cfg: PipelineConfig) -> BedrockQualityJudge:
    """Lazily construct the default Bedrock judge (never at import time)."""
    return BedrockQualityJudge(
        aws_region=cfg.aws_region,
        scale_min=cfg.quality.scale_min,
        scale_max=cfg.quality.scale_max,
    )


def filter_kept(scored: List[ScoredReview]) -> List[ScoredReview]:
    """Drop filtered translations from each record, keeping the record itself.

    Returns new records in which ``translations`` and ``quality`` retain only the
    languages whose quality verdict is ``kept``. Records with no kept
    translations (including passthrough reviews) are still returned, with empty
    ``translations`` / ``quality`` dicts.

    Args:
        scored: Output of :func:`score_translations`.

    Returns:
        A new list of :data:`ScoredReview` dicts; inputs are not mutated.
    """
    result: List[ScoredReview] = []
    dropped = 0

    for review in scored:
        quality: Dict[str, Any] = review.get("quality") or {}
        translations: Dict[str, Any] = review.get("translations") or {}

        kept_langs = [lang for lang, verdict in quality.items() if verdict.get("kept")]
        dropped += len(quality) - len(kept_langs)

        record = dict(review)
        record["quality"] = {lang: quality[lang] for lang in kept_langs}
        record["translations"] = {
            lang: translations[lang] for lang in kept_langs if lang in translations
        }
        result.append(record)

    logger.info(
        "filtered to kept translations",
        extra={"reviews": len(scored), "dropped": dropped},
    )
    return result
