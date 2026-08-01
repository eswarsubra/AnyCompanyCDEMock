"""Behavioural tests for :mod:`review_pipeline.quality` (pipeline stage 4).

These tests exercise the contract from ``docs/pipeline-contracts.md`` (the
"quality" stage) and the module docstring: each target-language translation is
scored on ``[scale_min, scale_max]`` and kept when ``score >= threshold``;
inputs are never mutated; a passthrough (no-translation) review gets an empty
``quality`` dict; ``filter_kept`` drops filtered languages but keeps the record.

All scoring goes through an injected fake ``QualityJudge`` so the tests never
touch the network and never need the ``anthropic`` package installed. The pure
helpers ``build_scoring_prompt`` and ``parse_score`` are tested directly.
"""
from __future__ import annotations

import copy

import pytest

from review_pipeline.config import ModelConfig, PipelineConfig, QualityConfig
from review_pipeline.quality import (
    build_scoring_prompt,
    filter_kept,
    parse_score,
    score_translations,
)


# --------------------------------------------------------------------------- #
# Fakes and fixtures
# --------------------------------------------------------------------------- #
class FakeJudge:
    """Injected judge returning a fixed score and recording every call.

    Implements the same ``score(...)`` signature as the default
    ``BedrockQualityJudge`` but with no dependency on ``anthropic`` and no
    network access -- this is the seam that keeps the unit tests offline.
    """

    def __init__(self, score: float = 4.0):
        self._score = score
        self.calls: list[dict] = []

    def score(
        self,
        source_text,
        translated_text,
        source_lang,
        target_lang,
        model_id,
        max_tokens,
        temperature,
    ) -> float:
        self.calls.append(
            {
                "source_text": source_text,
                "translated_text": translated_text,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "model_id": model_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return self._score


class PerLangJudge:
    """Judge returning a different score per target language."""

    def __init__(self, scores: dict):
        self._scores = scores
        self.calls: list[dict] = []

    def score(self, source_text, translated_text, source_lang, target_lang,
              model_id, max_tokens, temperature) -> float:
        self.calls.append({"target_lang": target_lang})
        return self._scores[target_lang]


class RaisingJudge:
    """Judge whose ``score`` always raises, to exercise boundary handling."""

    def __init__(self):
        self.calls = 0

    def score(self, *args, **kwargs) -> float:
        self.calls += 1
        raise RuntimeError("bedrock exploded")


def _make_cfg(
    *,
    threshold: float = 3.0,
    scale_min: int = 1,
    scale_max: int = 5,
    model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    max_tokens: int = 256,
    temperature: float = 0.0,
) -> PipelineConfig:
    """Build a validated PipelineConfig matching the packaged defaults."""
    cfg = PipelineConfig(
        target_languages=["fr", "de"],
        passthrough_language="en",
        summarization_model=ModelConfig(model_id="us.anthropic.claude-sonnet-5"),
        quality_model=ModelConfig(
            model_id=model_id, max_tokens=max_tokens, temperature=temperature
        ),
        quality=QualityConfig(
            threshold=threshold, scale_min=scale_min, scale_max=scale_max
        ),
    )
    cfg.validate()
    return cfg


def _translated_review(review_id="rev-0001", langs=("fr", "de")):
    """A translation-stage record with translations for the given languages."""
    return {
        "review_id": review_id,
        "product_id": "prod-001",
        "product_name": "Trail Jacket",
        "source_language": "en",
        "rating": 5,
        "title": "Great jacket",
        "body": "Kept me dry on the trail.",
        "translations": {
            lang: {
                "title": f"title-{lang}",
                "body": f"body-{lang}",
                "engine": "amazon-translate",
            }
            for lang in langs
        },
    }


def _passthrough_review(review_id="rev-0002"):
    """An English passthrough record: no translations to score."""
    return {
        "review_id": review_id,
        "product_id": "prod-002",
        "product_name": "Rain Hat",
        "source_language": "en",
        "rating": 4,
        "title": "Nice hat",
        "body": "Comfortable and light.",
        "translations": {},
    }


# --------------------------------------------------------------------------- #
# score_translations: kept vs threshold
# --------------------------------------------------------------------------- #
def test_score_above_threshold_is_kept():
    cfg = _make_cfg()
    scored = score_translations([_translated_review()], cfg, client=FakeJudge(4.0))

    quality = scored[0]["quality"]
    assert set(quality) == {"fr", "de"}
    for lang in ("fr", "de"):
        verdict = quality[lang]
        # Contract shape: {"<lang>": {"score": float, "kept": bool}}
        assert set(verdict) == {"score", "kept"}
        assert isinstance(verdict["score"], float)
        assert isinstance(verdict["kept"], bool)
        assert verdict["score"] == 4.0
        assert verdict["kept"] is True


def test_score_below_threshold_is_filtered():
    cfg = _make_cfg()
    scored = score_translations([_translated_review()], cfg, client=FakeJudge(2.0))

    for lang in ("fr", "de"):
        assert scored[0]["quality"][lang]["score"] == 2.0
        assert scored[0]["quality"][lang]["kept"] is False


def test_score_exactly_at_threshold_is_kept():
    # kept when score >= threshold, so exactly the threshold is kept.
    cfg = _make_cfg(threshold=3.0)
    scored = score_translations([_translated_review()], cfg, client=FakeJudge(3.0))

    for lang in ("fr", "de"):
        assert scored[0]["quality"][lang]["score"] == 3.0
        assert scored[0]["quality"][lang]["kept"] is True


def test_passthrough_review_gets_empty_quality_dict():
    cfg = _make_cfg()
    judge = FakeJudge(4.0)
    scored = score_translations([_passthrough_review()], cfg, client=judge)

    assert scored[0]["quality"] == {}
    # Nothing to score => judge never called.
    assert judge.calls == []


def test_quality_dict_shape_full():
    cfg = _make_cfg()
    scored = score_translations([_translated_review(langs=("fr",))], cfg,
                                client=FakeJudge(4.5))
    assert scored[0]["quality"] == {"fr": {"score": 4.5, "kept": True}}


# --------------------------------------------------------------------------- #
# Input not mutated
# --------------------------------------------------------------------------- #
def test_input_not_mutated():
    cfg = _make_cfg()
    review = _translated_review()
    original = copy.deepcopy(review)

    score_translations([review], cfg, client=FakeJudge(4.0))

    assert review == original
    assert "quality" not in review


def test_filter_kept_does_not_mutate_input():
    cfg = _make_cfg()
    scored = score_translations([_translated_review()], cfg,
                                client=PerLangJudge({"fr": 4.0, "de": 2.0}))
    snapshot = copy.deepcopy(scored)

    filter_kept(scored)

    assert scored == snapshot


# --------------------------------------------------------------------------- #
# filter_kept
# --------------------------------------------------------------------------- #
def test_filter_kept_drops_filtered_language_from_translations_and_quality():
    cfg = _make_cfg()
    scored = score_translations([_translated_review()], cfg,
                                client=PerLangJudge({"fr": 4.0, "de": 2.0}))

    filtered = filter_kept(scored)

    assert set(filtered[0]["quality"]) == {"fr"}
    assert set(filtered[0]["translations"]) == {"fr"}
    assert "de" not in filtered[0]["quality"]
    assert "de" not in filtered[0]["translations"]


def test_filter_kept_keeps_record_even_when_all_translations_filtered():
    cfg = _make_cfg()
    scored = score_translations([_translated_review()], cfg,
                                client=PerLangJudge({"fr": 1.0, "de": 2.0}))

    filtered = filter_kept(scored)

    # Record survives even though every translation was filtered out.
    assert len(filtered) == 1
    assert filtered[0]["review_id"] == "rev-0001"
    assert filtered[0]["quality"] == {}
    assert filtered[0]["translations"] == {}


def test_filter_kept_keeps_passthrough_record():
    cfg = _make_cfg()
    scored = score_translations([_passthrough_review()], cfg, client=FakeJudge(4.0))

    filtered = filter_kept(scored)

    assert len(filtered) == 1
    assert filtered[0]["review_id"] == "rev-0002"
    assert filtered[0]["quality"] == {}
    assert filtered[0]["translations"] == {}


# --------------------------------------------------------------------------- #
# parse_score (pure helper)
# --------------------------------------------------------------------------- #
def test_parse_score_plain_integer():
    assert parse_score("4", 1, 5) == 4.0


def test_parse_score_fraction_notation():
    assert parse_score("4.5/5", 1, 5) == 4.5


def test_parse_score_with_prefix_text():
    assert parse_score("Score: 3.0", 1, 5) == 3.0


def test_parse_score_garbage_returns_scale_min():
    assert parse_score("garbage", 1, 5) == 1.0


def test_parse_score_empty_returns_scale_min():
    assert parse_score("", 1, 5) == 1.0


def test_parse_score_above_max_clamps_to_max():
    assert parse_score("9", 1, 5) == 5.0


def test_parse_score_below_min_clamps_to_min():
    assert parse_score("-3", 1, 5) == 1.0


def test_parse_score_returns_float_type():
    assert isinstance(parse_score("4", 1, 5), float)


# --------------------------------------------------------------------------- #
# build_scoring_prompt (pure helper)
# --------------------------------------------------------------------------- #
def test_build_scoring_prompt_references_texts_and_languages():
    prompt = build_scoring_prompt(
        source_text="Great jacket",
        translated_text="Superbe veste",
        source_lang="en",
        target_lang="fr",
        scale_min=1,
        scale_max=5,
    )
    assert isinstance(prompt, str)
    assert "Great jacket" in prompt
    assert "Superbe veste" in prompt
    assert "en" in prompt
    assert "fr" in prompt
    # scale bounds are surfaced so the judge knows the range.
    assert "1" in prompt
    assert "5" in prompt


# --------------------------------------------------------------------------- #
# Failure behaviour: a raising judge filters that translation, batch survives
# --------------------------------------------------------------------------- #
def test_scoring_error_filters_translation_and_does_not_abort_batch():
    cfg = _make_cfg()
    judge = RaisingJudge()

    scored = score_translations(
        [_translated_review("rev-0001"), _translated_review("rev-0009")],
        cfg,
        client=judge,
    )

    # Batch not aborted: both reviews scored, both translations each.
    assert len(scored) == 2
    assert judge.calls == 4
    for record in scored:
        for lang in ("fr", "de"):
            verdict = record["quality"][lang]
            # Failed call => scored at scale_min => filtered out.
            assert verdict["score"] == float(cfg.quality.scale_min)
            assert verdict["kept"] is False


def test_scoring_error_then_filter_kept_drops_everything():
    cfg = _make_cfg()
    scored = score_translations([_translated_review()], cfg, client=RaisingJudge())

    filtered = filter_kept(scored)

    assert len(filtered) == 1
    assert filtered[0]["translations"] == {}


# --------------------------------------------------------------------------- #
# Judge invoked with the configured model settings
# --------------------------------------------------------------------------- #
def test_judge_called_with_configured_model_settings():
    cfg = _make_cfg(model_id="my-model-id", max_tokens=123, temperature=0.7)
    judge = FakeJudge(4.0)

    score_translations([_translated_review(langs=("fr",))], cfg, client=judge)

    assert len(judge.calls) == 1
    call = judge.calls[0]
    assert call["model_id"] == "my-model-id"
    assert call["max_tokens"] == 123
    assert call["temperature"] == 0.7
    assert call["target_lang"] == "fr"
    assert call["source_lang"] == "en"


def test_judge_called_once_per_translation():
    cfg = _make_cfg()
    judge = FakeJudge(4.0)

    score_translations([_translated_review(langs=("fr", "de"))], cfg, client=judge)

    assert len(judge.calls) == 2
    assert {c["target_lang"] for c in judge.calls} == {"fr", "de"}
